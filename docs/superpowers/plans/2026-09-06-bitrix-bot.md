# Bitrix24 Task Bot — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Чат-бот в задачах Битрикс24 (портал svok-kavkaz.bitrix24.ru), который по @упоминанию с reply на сообщение с PDF забирает файл, прогоняет существующий пайплайн проекта и возвращает в чат задачи номер заказа 1С со сводкой и детальными списками.

**Architecture:** Отдельный пакет `bitrix_bot/` — FastAPI-сервис (`server.py`) принимает webhook-события Битрикса, кладёт задания в SQLite-очередь (`queue.py`), один фоновый воркер последовательно обрабатывает: скачивание PDF через REST (`bitrix_client.py`) → пайплайн `api.load_tables_from_pdf` → `process_rows` → `json_positions.build_positions` → `tools/as_order_loader/build_execute_payload` → MCP `POST /api/execute_code` → формирование отчёта (`report.py`) → ответные сообщения в чат задачи. Спека: `docs/superpowers/specs/2026-09-06-bitrix-bot-design.md`.

**Tech Stack:** Python 3.12, FastAPI + uvicorn, httpx, SQLite (stdlib sqlite3), pytest, существующий пайплайн проекта.

## Global Constraints

- Новый код — только в пакете `bitrix_bot/` + его тесты; существующие модули меняем минимально: `config.yaml` (две секции), `requirements.txt` (три зависимости), `tools/as_order_loader/build_execute_payload.py` (опциональный параметр `order_comment`, значение по умолчанию сохраняет текущее поведение).
- Вебхук/секреты Битрикса — ТОЛЬКО в `bitrix.local.yaml` (gitignored). В git не коммитить.
- Ответ webhook — всегда быстрый 200; вся обработка — в фоновом воркере.
- Воркер — один, последовательный (1С не любит параллельные записи). Ретраи — до 3 попыток с паузой 5 с → 20 с → 60 с.
- Лимит одного сообщения im — 3500 символов (константа `REPORT_LIMIT`). Длинная детализация отправляется НЕСКОЛЬКИМИ последовательными сообщениями (осознанное упрощение спеки: вместо прикрепления файла — нарезка на сообщения; REST-загрузка файлов в чат ботом нестабильна, фоллоу-ап при необходимости).
- Код и комментарии — на русском там, где так принято в проекте (json_positions.py, build_execute_payload.py); идентификаторы — английские.
- Тесты запускаются из корня: `.venv/bin/python -m pytest tests/test_bitrix_*.py -v` (venv проекта).
- Пайплайн в 1С идёт через `POST http://127.0.0.1:6005/api/execute_code` (MCP Toolkit); URL из `config.yaml` — заменить хост на `execute_code` URL.
- Ответ 1С — строка вида `ЗАКАЗ 000000860 | строк=286 | ошибок=0 | предупр=2 ## <детали строк> ## ...`; парсим дефенсивно (см. Task 4).

---

### Task 1: Скелет пакета, конфигурация, зависимости

**Files:**
- Create: `bitrix_bot/__init__.py`
- Create: `bitrix_bot/config.py`
- Modify: `config.yaml` (добавить секции `bitrix:` и `middleware:`)
- Modify: `requirements.txt` (добавить fastapi, uvicorn, httpx)
- Test: `tests/test_bitrix_config.py`

**Interfaces:**
- Produces: `bitrix_bot.config.BotConfig` (frozen dataclass) и `load_bot_config() -> BotConfig` — используются всеми остальными модулями и тестами.

`BotConfig` поля (все `str`, кроме отмеченных): `portal`, `incoming_webhook`, `client_id`, `client_secret`, `verify_token`, `execute_code_url`, `tmp_dir`, `report_limit: int`, `request_timeout: float`, `task_comment_prefix: str` (по умолчанию `"Задача Битрикс24"`).

- [ ] **Step 1: Добавить зависимости**

В `requirements.txt` добавить три строки в конец:

```text
fastapi>=0.110.0
uvicorn>=0.29.0
httpx>=0.27.0
```

Установить: `.venv/bin/pip install "fastapi>=0.110.0" "uvicorn>=0.29.0" "httpx>=0.27.0"`

- [ ] **Step 2: Добавить секции в config.yaml**

В конец `config.yaml`:

```yaml
# Bitrix24 bot integration (секреты — в bitrix.local.yaml, gitignored)
bitrix:
  portal: "https://svok-kavkaz.bitrix24.ru"

middleware:
  tmp_dir: "tmp/bitrix_bot"
  report_limit: 3500
  request_timeout: 280
  task_comment_prefix: "Задача Битрикс24"
```

- [ ] **Step 3: Написать failing test**

Создать `tests/test_bitrix_config.py`:

```python
"""Тесты конфигурации битрикс-бота."""
import bitrix_bot.config as bc


def test_load_bot_config_merges_files(tmp_path):
    base = tmp_path / "config.yaml"
    base.write_text(
        """
bitrix:
  portal: "https://example.bitrix24.ru"
middleware:
  tmp_dir: "tmp/test"
  report_limit: 100
  request_timeout: 10
  task_comment_prefix: "Префикс"
""",
        encoding="utf-8",
    )
    local = tmp_path / "bitrix.local.yaml"
    local.write_text(
        """
bitrix:
  portal: "https://example.bitrix24.ru"
  incoming_webhook: "https://example.bitrix24.ru/rest/1/KEY/"
  app_client_id: "cid"
  app_client_secret: "sec"
middleware:
  webhook_verify_token: "tok"
""",
        encoding="utf-8",
    )
    cfg = bc.load_bot_config(base, local)
    assert cfg.portal == "https://example.bitrix24.ru"
    assert cfg.incoming_webhook == "https://example.bitrix24.ru/rest/1/KEY/"
    assert cfg.client_id == "cid"
    assert cfg.client_secret == "sec"
    assert cfg.verify_token == "tok"
    assert cfg.report_limit == 100
    assert cfg.request_timeout == 10
    assert cfg.task_comment_prefix == "Префикс"
    assert cfg.execute_code_url == "http://127.0.0.1:6005/api/execute_code"
    assert cfg.tmp_dir == "tmp/test"


def test_load_bot_config_missing_local_ok(tmp_path):
    base = tmp_path / "config.yaml"
    base.write_text("bitrix:\n  portal: \"https://x.ru\"\nmiddleware: {}\n", encoding="utf-8")
    cfg = bc.load_bot_config(base, tmp_path / "nope.yaml")
    assert cfg.verify_token == ""
    assert cfg.incoming_webhook == ""
    assert cfg.report_limit == 3500  # дефолт
    assert cfg.task_comment_prefix == "Задача Битрикс24"  # дефолт
```

- [ ] **Step 4: Запустить, убедиться что падает**

Run: `.venv/bin/python -m pytest tests/test_bitrix_config.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'bitrix_bot'`).

- [ ] **Step 5: Реализовать**

Создать `bitrix_bot/__init__.py` (пустой) и `bitrix_bot/config.py`:

```python
"""Конфигурация битрикс-бота: config.yaml + секреты из bitrix.local.yaml.

Секреты (вебхук, client_secret, токен верификации) живут ТОЛЬКО в
bitrix.local.yaml — файл в .gitignore, в git не коммитить.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict

import yaml

import config as app_config

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LOCAL_CONFIG = ROOT / "bitrix.local.yaml"

DEFAULT_EXECUTE_CODE_URL = "http://127.0.0.1:6005/api/execute_code"


@dataclass(frozen=True)
class BotConfig:
    portal: str = ""
    incoming_webhook: str = ""
    client_id: str = ""
    client_secret: str = ""
    verify_token: str = ""
    execute_code_url: str = DEFAULT_EXECUTE_CODE_URL
    tmp_dir: str = "tmp/bitrix_bot"
    report_limit: int = 3500
    request_timeout: float = 280.0
    task_comment_prefix: str = "Задача Битрикс24"


def _read_yaml(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_bot_config(
    base_path: str | Path | None = None,
    local_path: str | Path | None = None,
) -> BotConfig:
    """Собрать BotConfig из config.yaml (base) и bitrix.local.yaml (секреты)."""
    base = _read_yaml(Path(base_path)) if base_path else app_config.get_config()
    local = _read_yaml(Path(local_path)) if local_path else _read_yaml(DEFAULT_LOCAL_CONFIG)

    bx: Dict[str, Any] = {**base.get("bitrix", {}) or {}, **local.get("bitrix", {}) or {}}
    mw: Dict[str, Any] = {**base.get("middleware", {}) or {}, **local.get("middleware", {}) or {}}

    mcp_url = (base.get("mcp", {}) or {}).get("url", "")
    execute_url = DEFAULT_EXECUTE_CODE_URL
    if mcp_url:
        # http://127.0.0.1:6005/mcp -> http://127.0.0.1:6005/api/execute_code
        execute_url = mcp_url.rstrip("/").rsplit("/", 1)[0] + "/api/execute_code"

    return BotConfig(
        portal=bx.get("portal", ""),
        incoming_webhook=bx.get("incoming_webhook", ""),
        client_id=bx.get("app_client_id", ""),
        client_secret=bx.get("app_client_secret", ""),
        verify_token=mw.get("webhook_verify_token", ""),
        execute_code_url=mw.get("execute_code_url", execute_url),
        tmp_dir=mw.get("tmp_dir", "tmp/bitrix_bot"),
        report_limit=int(mw.get("report_limit", 3500)),
        request_timeout=float(mw.get("request_timeout", 280)),
        task_comment_prefix=mw.get("task_comment_prefix", "Задача Битрикс24"),
    )
```

- [ ] **Step 6: Запустить, убедиться что проходит**

Run: `.venv/bin/python -m pytest tests/test_bitrix_config.py -v`
Expected: PASS (2 теста).

- [ ] **Step 7: Commit**

```bash
git add bitrix_bot/__init__.py bitrix_bot/config.py tests/test_bitrix_config.py config.yaml requirements.txt
git commit -m "feat(bitrix_bot): package skeleton, config loader, deps"
```

---

### Task 2: Формирование отчёта (report.py)

**Files:**
- Create: `bitrix_bot/report.py`
- Create: `bitrix_bot/pipeline.py` (только dataclass `PipelineResult`, без логики — логика в Task 4)
- Test: `tests/test_bitrix_report.py`

**Interfaces:**
- Consumes: ничего (первый модуль домена).
- Produces: `PipelineResult` (dataclass) — контракт между pipeline и report; `build_report(res: PipelineResult, limit: int = 3500) -> list[str]` — список готовых сообщений (каждое ≤ limit символов). Используется в Task 5 (воркер) и Task 6 (server).

`PipelineResult` поля: `file_name: str`, `order_number: str | None`, `loaded: list[dict]`, `skipped: list[dict]`, `errors_1c: list[str]`, `warnings_1c: list[str]`, `raw_text: str = ""`. Загруженная позиция — parsed-строка пайплайна: ключи `article`, `quantity`, `comment`, `params` (dict). Пропущенная — ключи `name`, `size`, `quantity`, `reason`.

- [ ] **Step 1: Написать failing test**

Создать `tests/test_bitrix_report.py`:

```python
"""Тесты формирования итогового отчёта бота."""
from bitrix_bot.pipeline import PipelineResult
from bitrix_bot.report import build_report


def _res(**kw):
    base = dict(
        file_name="spec.pdf",
        order_number="000000860",
        loaded=[
            {"article": "1-2-1", "quantity": 6, "comment": "", "params": {"A0": 400, "B0": 200}},
            {"article": "4-2-3", "quantity": 2, "comment": "отвод", "params": {"D0": 315}},
        ],
        skipped=[
            {"name": "Клапан КПУ", "size": "200", "quantity": "1", "reason": "нет маппинга"},
        ],
        errors_1c=[],
        warnings_1c=["Строка 1 (1-2-1): цена 0 — проверьте прайс"],
    )
    base.update(kw)
    return PipelineResult(**base)


def test_report_has_header_summary_and_lists():
    msgs = build_report(_res())
    assert len(msgs) == 1
    text = msgs[0]
    assert "Заказ 1С: №000000860" in text
    assert "Загружено: 2" in text and "Пропущено: 1" in text
    assert "Ошибок: 0" in text
    assert "1-2-1" in text and "4-2-3" in text
    assert "Клапан КПУ" in text and "нет маппинга" in text
    assert "цена 0" in text  # предупреждение 1С видно


def test_report_no_order():
    text = build_report(_res(order_number=None, errors_1c=["не найден продукт \"9-9-9\""]))[0]
    assert "Заказ НЕ создан" in text
    assert "Ошибок: 1" in text
    assert "9-9-9" in text


def test_report_splits_long_details():
    loaded = [
        {"article": f"1-2-1", "quantity": i, "comment": "x" * 100, "params": {}}
        for i in range(80)
    ]
    msgs = build_report(_res(loaded=loaded), limit=3500)
    assert len(msgs) > 1
    assert all(len(m) <= 3500 for m in msgs)
    assert msgs[0].startswith("Заказ 1С")
    total = "".join(msgs)
    assert total.count("1-2-1") == 80
```

- [ ] **Step 2: Запустить, убедиться что падает**

Run: `.venv/bin/python -m pytest tests/test_bitrix_report.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'bitrix_bot.pipeline'`).

- [ ] **Step 3: Реализовать**

Создать `bitrix_bot/pipeline.py` (в этой задаче — только dataclass):

```python
"""Оркестрация пайплайна PDF → заказ 1С (реализация — ниже в этом файле, Task 4)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class PipelineResult:
    """Результат обработки одного PDF для отчёта бота."""

    file_name: str
    order_number: Optional[str] = None
    loaded: List[Dict[str, Any]] = field(default_factory=list)
    skipped: List[Dict[str, Any]] = field(default_factory=list)
    errors_1c: List[str] = field(default_factory=list)
    warnings_1c: List[str] = field(default_factory=list)
    raw_text: str = ""
```

Создать `bitrix_bot/report.py`:

```python
"""Формирование итогового сообщения бота: сводка + детальные списки.

Длинная детализация нарезается на несколько сообщений (лимит im ~4000
символов; берём 3500 с запасом).
"""

from __future__ import annotations

from typing import List

from bitrix_bot.pipeline import PipelineResult

DEFAULT_LIMIT = 3500


def _position_line(p: dict) -> str:
    dims = p.get("params") or {}
    dim_txt = "x".join(str(int(v)) for v in dims.values() if isinstance(v, (int, float)))
    suffix = f" ({dim_txt})" if dim_txt else ""
    comment = f" — {p['comment']}" if p.get("comment") else ""
    return f"· {p['article']}{suffix}, {p['quantity']} шт{comment}"


def _skipped_line(s: dict) -> str:
    name = s.get("name") or "?"
    size = f" {s.get('size')}" if s.get("size") else ""
    qty = f", {s.get('quantity')}" if s.get("quantity") else ""
    return f"· {name}{size}{qty} — {s.get('reason', 'без причины')}"


def _join_chunks(lines: List[str], limit: int) -> List[str]:
    """Склеить строки в сообщения не длиннее limit, не рвя строки."""
    out: List[str] = []
    buf: List[str] = []
    for line in lines:
        cur = "\n".join(buf + [line])
        if buf and len(cur) > limit:
            out.append("\n".join(buf))
            buf = [line]
        else:
            buf.append(line)
    if buf:
        out.append("\n".join(buf))
    return out


def build_report(res: PipelineResult, limit: int = DEFAULT_LIMIT) -> List[str]:
    """Вернуть список сообщений для чата задачи."""
    header = (
        f"Заказ 1С: №{res.order_number}" if res.order_number else "Заказ НЕ создан"
    )
    summary = (
        f"Файл: {res.file_name}\n"
        f"Загружено: {len(res.loaded)} · Пропущено: {len(res.skipped)}"
        f" · Ошибок: {len(res.errors_1c)}"
    )
    lines: List[str] = [header, "", summary]

    if res.loaded:
        lines += ["", "✅ Загружены:"] + [_position_line(p) for p in res.loaded]
    if res.skipped:
        lines += ["", "⏭ Пропущены:"] + [_skipped_line(s) for s in res.skipped]
    if res.errors_1c:
        lines += ["", "❌ Ошибки 1С:"] + [f"· {e}" for e in res.errors_1c]
    if res.warnings_1c:
        lines += ["", "⚠ Предупреждения 1С:"] + [f"· {w}" for w in res.warnings_1c]

    chunks = _join_chunks(lines, limit)
    # Первый кусок начинается с шапки; продолжения помечаем
    if len(chunks) > 1:
        chunks = [chunks[0]] + [f"(продолжение {i + 2}/{len(chunks)})\n{c}" for i, c in enumerate(chunks[1:])]
    return chunks
```

- [ ] **Step 4: Запустить, убедиться что проходит**

Run: `.venv/bin/python -m pytest tests/test_bitrix_report.py -v`
Expected: PASS (3 теста).

- [ ] **Step 5: Commit**

```bash
git add bitrix_bot/pipeline.py bitrix_bot/report.py tests/test_bitrix_report.py
git commit -m "feat(bitrix_bot): PipelineResult contract and report builder"
```


---

### Task 3: REST-клиент Битрикс24 (bitrix_client.py)

**Files:**
- Create: `bitrix_bot/bitrix_client.py`
- Test: `tests/test_bitrix_client.py`

**Interfaces:**
- Produces: `BitrixError(RuntimeError)`; `BitrixClient(webhook: str, timeout: float = 30.0)` с методами:
  - `call(method: str, **params) -> dict` — обёртка над `POST {webhook}{method}`; кладёт result в `["result"]`, ошибки → `BitrixError`.
  - `send_message(dialog_id: str, text: str) -> None`
  - `get_task_title(task_id: int) -> str` — `tasks.task.get`, вернуть `title` или "".
  - `download_file(download_url: str) -> bytes` — GET по абсолютной ссылке (в событиях/файлах Битрикс отдаёт URL с уже вшитой авторизацией).
  - `get_dialog_messages(dialog_id: str, limit: int = 30) -> list[dict]` — `im.message.get`, вернуть список сообщений.
- Используется в Task 5 (воркер) и Task 6 (events).

- [ ] **Step 1: Написать failing test**

Создать `tests/test_bitrix_client.py`:

```python
"""Тесты REST-клиента Битрикс24 (httpx замокан)."""
import httpx
import pytest

from bitrix_bot.bitrix_client import BitrixClient, BitrixError


class _Resp:
    def __init__(self, status_code=200, data=None):
        self.status_code = status_code
        self._data = data or {}

    def json(self):
        return self._data


def test_call_posts_to_webhook(monkeypatch):
    calls = []

    def fake_post(url, json=None, timeout=None):
        calls.append((url, json))
        return _Resp(data={"result": {"id": 1}})

    monkeypatch.setattr(httpx, "post", fake_post)
    client = BitrixClient("https://b24/rest/1/KEY/", timeout=5)
    result = client.call("im.message.add", DIALOG_ID="task|1", MESSAGE="hi")
    assert result == {"id": 1}
    assert calls[0][0] == "https://b24/rest/1/KEY/im.message.add"
    assert calls[0][1]["MESSAGE"] == "hi"


def test_call_raises_on_rest_error(monkeypatch):
    def fake_post(url, json=None, timeout=None):
        return _Resp(data={"error": "INVALID_TOKEN", "error_description": "bad"})

    monkeypatch.setattr(httpx, "post", fake_post)
    client = BitrixClient("https://b24/rest/1/KEY/")
    with pytest.raises(BitrixError, match="INVALID_TOKEN"):
        client.call("tasks.task.get")


def test_call_raises_on_http_error(monkeypatch):
    def fake_post(url, json=None, timeout=None):
        return _Resp(status_code=500, data={})

    monkeypatch.setattr(httpx, "post", fake_post)
    client = BitrixClient("https://b24/rest/1/KEY/")
    with pytest.raises(BitrixError, match="HTTP 500"):
        client.call("im.message.add")


def test_get_task_title(monkeypatch):
    def fake_post(url, json=None, timeout=None):
        return _Resp(data={"result": {"task": {"title": "Шипиловский — ОВ2"}}})

    monkeypatch.setattr(httpx, "post", fake_post)
    assert BitrixClient("https://b24/rest/1/KEY/").get_task_title(42) == "Шипиловский — ОВ2"


def test_download_file(monkeypatch):
    class _GetResp:
        status_code = 200
        content = b"%PDF-fake"

    monkeypatch.setattr(httpx, "get", lambda url, timeout=None: _GetResp())
    data = BitrixClient("https://b24/rest/1/KEY/").download_file("https://b24/disk/download/x&auth=1")
    assert data == b"%PDF-fake"
```

- [ ] **Step 2: Запустить, убедиться что падает**

Run: `.venv/bin/python -m pytest tests/test_bitrix_client.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'bitrix_bot.bitrix_client'`).

- [ ] **Step 3: Реализовать**

Создать `bitrix_bot/bitrix_client.py`:

```python
"""Минимальный REST-клиент Битрикс24 поверх входящего вебхука.

Вебхук вида https://portal.bitrix24.ru/rest/{user}/{key}/ — каждый метод
вызывается POST-ом на {webhook}{method} с телом params.
"""

from __future__ import annotations

import httpx


class BitrixError(RuntimeError):
    """Ошибка REST API Битрикс24 (HTTP или error в ответе)."""


class BitrixClient:
    def __init__(self, webhook: str, timeout: float = 30.0):
        self._webhook = webhook.rstrip("/") + "/"
        self._timeout = timeout

    def call(self, method: str, **params):
        resp = httpx.post(self._webhook + method, json=params, timeout=self._timeout)
        if resp.status_code != 200:
            raise BitrixError(f"{method}: HTTP {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        if "error" in data:
            raise BitrixError(
                f"{method}: {data['error']}: {data.get('error_description', '')}"
            )
        return data["result"]

    def send_message(self, dialog_id: str, text: str) -> None:
        self.call("im.message.add", DIALOG_ID=dialog_id, MESSAGE=text)

    def get_task_title(self, task_id: int) -> str:
        result = self.call("tasks.task.get", taskId=task_id)
        task = result.get("task") or {}
        return task.get("title") or ""

    def get_dialog_messages(self, dialog_id: str, limit: int = 30) -> list[dict]:
        result = self.call("im.message.get", CHAT_ID=dialog_id, LIMIT=limit)
        if isinstance(result, list):
            return result
        return (result or {}).get("messages") or []

    def download_file(self, download_url: str) -> bytes:
        resp = httpx.get(download_url, timeout=self._timeout)
        if resp.status_code != 200:
            raise BitrixError(f"download: HTTP {resp.status_code}")
        return resp.content
```

- [ ] **Step 4: Запустить, убедиться что проходит**

Run: `.venv/bin/python -m pytest tests/test_bitrix_client.py -v`
Expected: PASS (5 тестов).

- [ ] **Step 5: Commit**

```bash
git add bitrix_bot/bitrix_client.py tests/test_bitrix_client.py
git commit -m "feat(bitrix_bot): Bitrix24 REST client over incoming webhook"
```

---

### Task 4: Пайплайн PDF → 1С (pipeline.py) + параметр комментария в загрузчике

**Files:**
- Modify: `bitrix_bot/pipeline.py` (добавить функции)
- Modify: `tools/as_order_loader/build_execute_payload.py` (опциональный `order_comment`)
- Test: `tests/test_bitrix_pipeline.py`

**Interfaces:**
- Consumes: `api.load_tables_from_pdf` (api.py:42), `pdf_spec_extractor.df_to_spec_rows`, `process_specification_table.process_rows` (process_specification_table.py:1256, возвращает `(xml, skipped, success_rows)`), `json_positions.build_positions` (json_positions.py:67), `build_execute_payload.build_payload`.
- Produces:
  - `build_payload(positions, mark_delete=False, order_comment="")` — изменённая сигнатура (по умолчанию поведение прежнее).
  - `process_pdf_to_positions(pdf_bytes: bytes) -> tuple[list[dict], list[dict]]` — `(success_rows, skipped)`.
  - `load_order_to_1c(positions: list[dict], execute_url: str, order_comment: str, timeout: float = 280.0) -> dict` — `{"order_number": str|None, "errors": [...], "warnings": [...], "raw": str}`. Ошибки 1С НЕ бросают исключение; исключения бросают только сетевые сбои (`httpx.HTTPError`).
  - `run_pipeline(pdf_bytes: bytes, file_name: str, order_comment: str, execute_url: str, timeout: float = 280.0) -> PipelineResult` — полный вход для воркера: 0 распознанных позиций → `PipelineResult` без заказа и с текстом в `raw_text` (не исключение).

Формат ответа 1С (BSL `Результат = Итог`): `ЗАКАЗ 000000860 | строк=286 | ошибок=0 | предупр=2 ## <детали> ## ...`; если ошибки есть, их список идёт сегментами ` | ` между `ошибок=N` и `предупр`.

- [ ] **Step 1: Написать failing test**

Создать `tests/test_bitrix_pipeline.py`:

```python
"""Тесты пайплайна PDF → 1С (MCP execute_code замокан)."""
import httpx
import pytest

import bitrix_bot.pipeline as pl
from tools.as_order_loader.build_execute_payload import build_payload

SAMPLE_1C_OK = (
    "ЗАКАЗ 000000860 | строк=2 | ошибок=0 | предупр=1"
    " | Строка 1 (1-2-1): цена 0 — проверьте прайс"
    " ## 1|1-2-1|t=0.8|мат=Рулон оц.(08ПС)0.80|цена=0|S=1.2|n=6"
    " ## 2|4-2-3|t=0.8|мат=Рулон оц.(08ПС)0.80|цена=150|S=0.4|n=2"
)

SAMPLE_1C_ERRORS = (
    "ЗАКАЗ 000000861 | строк=2 | ошибок=1"
    " | Строка 1: не найден продукт \"9-9-9\""
    " | предупр=0"
)


class _Resp:
    def __init__(self, data):
        self.status_code = 200
        self._data = data

    def json(self):
        return self._data

    def raise_for_status(self):
        return None


def test_build_payload_order_comment_substituted():
    payload = build_payload([{"article": "1-2-1"}], order_comment="Задача №42: Шипиловский")
    assert "Задача №42: Шипиловский" in payload["code"]
    # по умолчанию — прежний комментарий
    payload_def = build_payload([{"article": "1-2-1"}])
    assert "Загрузка из JSON (execute_code, build_execute_payload)" in payload_def["code"]


def test_load_order_parses_ok(monkeypatch):
    monkeypatch.setattr(
        httpx, "post",
        lambda url, json=None, timeout=None: _Resp({"result": SAMPLE_1C_OK}),
    )
    out = pl.load_order_to_1c([{"article": "1-2-1"}], "http://x/api/execute_code", "c")
    assert out["order_number"] == "000000860"
    assert out["errors"] == []
    assert out["warnings"] == ["Строка 1 (1-2-1): цена 0 — проверьте прайс"]


def test_load_order_parses_errors(monkeypatch):
    monkeypatch.setattr(
        httpx, "post",
        lambda url, json=None, timeout=None: _Resp({"result": SAMPLE_1C_ERRORS}),
    )
    out = pl.load_order_to_1c([{"article": "9-9-9"}], "http://x/api/execute_code", "c")
    assert out["order_number"] == "000000861"
    assert out["errors"] == ['Строка 1: не найден продукт "9-9-9"']
    assert out["warnings"] == []


def test_run_pipeline_no_positions(monkeypatch):
    monkeypatch.setattr(
        pl, "process_pdf_to_positions",
        lambda b: ([], [{"name": "x", "reason": "bad"}]),
    )
    res = pl.run_pipeline(b"pdf", "f.pdf", "c", "http://x")
    assert res.order_number is None
    assert res.loaded == []
    assert len(res.skipped) == 1
    assert "не распознал" in res.raw_text


def test_run_pipeline_end_to_end_mocked(monkeypatch):
    monkeypatch.setattr(
        pl, "process_pdf_to_positions",
        lambda b: (
            [{"article": "1-2-1", "quantity": 6, "comment": "", "params": {}}],
            [{"name": "Клапан", "size": "200", "quantity": "1", "reason": "нет маппинга"}],
        ),
    )
    monkeypatch.setattr(
        httpx, "post",
        lambda url, json=None, timeout=None: _Resp({"result": SAMPLE_1C_OK}),
    )
    res = pl.run_pipeline(b"pdf", "spec.pdf", "Задача №42", "http://x")
    assert res.order_number == "000000860"
    assert len(res.loaded) == 1
    assert res.skipped[0]["name"] == "Клапан"
    assert res.warnings_1c == ["Строка 1 (1-2-1): цена 0 — проверьте прайс"]
```

- [ ] **Step 2: Запустить, убедиться что падает**

Run: `.venv/bin/python -m pytest tests/test_bitrix_pipeline.py -v`
Expected: FAIL (`TypeError: build_payload() got an unexpected keyword argument 'order_comment'` и `AttributeError: module 'bitrix_bot.pipeline' has no attribute 'load_order_to_1c'`).

- [ ] **Step 3: Реализовать**

Изменить `tools/as_order_loader/build_execute_payload.py`:

1. В шаблон `BSL_TEMPLATE` заменить строку 39:

```bsl
	ДокОбъект.Комментарий = "Загрузка из JSON (execute_code, build_execute_payload)";
```

на:

```bsl
	ДокОбъект.Комментарий = "{order_comment}";
```

2. Заменить функцию `build_payload` (build_execute_payload.py:198):

```python
def build_payload(positions, mark_delete=False, order_comment=""):
    b64 = base64.b64encode(
        json.dumps(positions, ensure_ascii=False).encode()
    ).decode()
    if not order_comment:
        order_comment = "Загрузка из JSON (execute_code, build_execute_payload)"
    code = BSL_TEMPLATE.format(
        b64=b64,
        mark_delete="" if mark_delete else "// ",
        order_comment=order_comment,
    )
    return {"code": code}
```

⚠️ `BSL_TEMPLATE.format` — в шаблоне уже есть плейсхолдеры `{b64}` и `{mark_delete}`; `order_comment` подставляется тем же механизмом. Проверить, что в шаблоне не осталось других одиночных `{`/`}` (BSL-код не содержит фигурных скобок — в текущем шаблоне их нет).

Проверить, что существующие тесты не сломались: `.venv/bin/python -m pytest tests/test_json_positions.py -v` → PASS.

Дописать в `bitrix_bot/pipeline.py`:

```python
"""Оркестрация пайплайна PDF → заказ 1С."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import httpx


@dataclass
class PipelineResult:
    """Результат обработки одного PDF для отчёта бота."""

    file_name: str
    order_number: Optional[str] = None
    loaded: List[Dict[str, Any]] = field(default_factory=list)
    skipped: List[Dict[str, Any]] = field(default_factory=list)
    errors_1c: List[str] = field(default_factory=list)
    warnings_1c: List[str] = field(default_factory=list)
    raw_text: str = ""


def process_pdf_to_positions(pdf_bytes: bytes) -> Tuple[List[dict], List[dict]]:
    """PDF → (успешные строки пайплайна, пропущенные с причинами).

    Берёт таблицы из PDF; если их нет — текстовый слой, разобранный блоками
    (ГОСТ-бланки, см. api.load_tables_from_pdf).
    """
    from api import load_tables_from_pdf
    from pdf_spec_extractor import df_to_spec_rows
    from process_specification_table import process_rows

    data = load_tables_from_pdf(pdf_bytes)
    if "tables" in data:
        rows: List[dict] = []
        for tables in data["tables"].values():
            for df in tables:
                rows.extend(df_to_spec_rows(df))
    else:
        rows = data.get("block_rows") or []
    _, skipped, success = process_rows(rows)
    return success, skipped


def parse_1c_result(text: str) -> Dict[str, Any]:
    """Разобрать строку-результат 1С: 'ЗАКАЗ № | строк=N | ошибок=N | предупр=N | ... ## ...'."""
    head = text.split(" ## ")[0]
    segments = [s.strip() for s in head.split(" | ")]
    order_number = None
    errors: List[str] = []
    warnings: List[str] = []
    n_errors = 0
    n_warnings = 0
    for i, seg in enumerate(segments):
        m = re.match(r"ЗАКАЗ\s+(\S+)", seg)
        if m:
            order_number = m.group(1)
        m = re.match(r"ошибок=(\d+)", seg)
        if m:
            n_errors = int(m.group(1))
            if n_errors:
                errors = segments[i + 1 : i + 1 + n_errors]
        m = re.match(r"предупр=(\d+)", seg)
        if m:
            n_warnings = int(m.group(1))
            if n_warnings:
                warnings = segments[i + 1 : i + 1 + n_warnings]
    return {
        "order_number": order_number,
        "errors": errors,
        "warnings": warnings,
        "raw": text,
    }


def load_order_to_1c(
    positions: List[dict],
    execute_url: str,
    order_comment: str,
    timeout: float = 280.0,
) -> Dict[str, Any]:
    """Отправить позиции в 1С через MCP execute_code; вернуть разобранный результат.

    Ошибки 1С (Успех=false и т.п.) — в поле errors результата, не исключение.
    Исключение — только сетевой сбой (httpx.HTTPError).
    """
    from tools.as_order_loader.build_execute_payload import build_payload

    payload = build_payload(positions, order_comment=order_comment)
    resp = httpx.post(execute_url, json=payload, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    text = str(data.get("result", data))
    return parse_1c_result(text)


def run_pipeline(
    pdf_bytes: bytes,
    file_name: str,
    order_comment: str,
    execute_url: str,
    timeout: float = 280.0,
) -> PipelineResult:
    """Полный цикл: PDF → позиции → заказ 1С → PipelineResult."""
    from json_positions import build_positions

    success, skipped = process_pdf_to_positions(pdf_bytes)
    if not success:
        return PipelineResult(
            file_name=file_name,
            skipped=skipped,
            raw_text="Позиции не распознаны: в PDF не найдено ни одной валидной строки спецификации.",
        )
    positions = build_positions(success)
    out = load_order_to_1c(positions, execute_url, order_comment, timeout=timeout)
    return PipelineResult(
        file_name=file_name,
        order_number=out["order_number"],
        loaded=success,
        skipped=skipped,
        errors_1c=out["errors"],
        warnings_1c=out["warnings"],
        raw_text=out["raw"],
    )
```

- [ ] **Step 4: Запустить, убедиться что проходит**

Run: `.venv/bin/python -m pytest tests/test_bitrix_pipeline.py tests/test_json_positions.py -v`
Expected: PASS все.

- [ ] **Step 5: Commit**

```bash
git add bitrix_bot/pipeline.py tools/as_order_loader/build_execute_payload.py tests/test_bitrix_pipeline.py
git commit -m "feat(bitrix_bot): pipeline PDF -> 1C order, order_comment in loader payload"
```

---

### Task 5: Очередь заданий (queue.py)

**Files:**
- Create: `bitrix_bot/queue.py`
- Test: `tests/test_bitrix_queue.py`

**Interfaces:**
- Produces:
  - `@dataclass Job`: `id: int`, `dialog_id: str`, `task_id: int | None`, `pdf_path: str`, `file_name: str`, `order_comment: str`, `attempts: int = 0`.
  - `class JobQueue(db_path: str | Path, tmp_dir: str | Path)`: `enqueue(job: Job) -> Job`, `next_pending() -> Job | None` (переводит в `running`), `complete(job_id)`, `reschedule(job_id, error: str)` — attempts+1, back в `pending` если attempts < 3, иначе `failed`, `get(job_id) -> Job`, `stats() -> dict`.
  - PDF байты хранятся файлом в `tmp_dir/jobs/{id}.pdf` — в БД только путь (BLOB не нужен).
  - `MAX_ATTEMPTS = 3`; паузы ретраев `[5, 20, 60]` секунд — константа `RETRY_DELAYS`.
  - `run_worker(queue: JobQueue, handler, stop_event=None, poll_seconds: float = 2.0)` — блокирующий цикл: берёт задание, вызывает `handler(job) -> None`; исключение → `reschedule`; стоп по `stop_event` (threading.Event).
- Используется в Task 6.

- [ ] **Step 1: Написать failing test**

Создать `tests/test_bitrix_queue.py`:

```python
"""Тесты SQLite-очереди заданий."""
import threading
import time

import pytest

from bitrix_bot.queue import Job, JobQueue, MAX_ATTEMPTS, run_worker


@pytest.fixture()
def queue(tmp_path):
    return JobQueue(tmp_path / "jobs.db", tmp_path / "files")


def _job(**kw):
    base = dict(
        dialog_id="task|1",
        task_id=1,
        pdf_path="",
        file_name="spec.pdf",
        order_comment="c",
    )
    base.update(kw)
    return Job(**base)


def test_enqueue_stores_pdf_and_roundtrip(queue, tmp_path):
    job = queue.enqueue(_job(), pdf_bytes=b"%PDF-abc")
    assert (tmp_path / "files" / f"{job.id}.pdf").read_bytes() == b"%PDF-abc"
    got = queue.get(job.id)
    assert got.dialog_id == "task|1"
    assert queue.stats()["pending"] == 1


def test_next_pending_marks_running_and_complete(queue):
    job = queue.enqueue(_job())
    assert queue.next_pending().id == job.id
    assert queue.next_pending() is None  # уже running
    queue.complete(job.id)
    assert queue.stats()["done"] == 1


def test_reschedule_retries_then_fails(queue):
    job = queue.enqueue(_job())
    queue.next_pending()
    for _ in range(MAX_ATTEMPTS - 1):
        queue.reschedule(job.id, "boom")
        assert queue.next_pending() is not None
        queue.stats()
    queue.reschedule(job.id, "boom")  # последняя попытка исчерпана
    assert queue.next_pending() is None
    stats = queue.stats()
    assert stats["failed"] == 1


def test_persistence_across_instances(tmp_path):
    q1 = JobQueue(tmp_path / "jobs.db", tmp_path / "files")
    job = q1.enqueue(_job())
    q2 = JobQueue(tmp_path / "jobs.db", tmp_path / "files")
    assert q2.stats()["pending"] == 1
    assert q2.get(job.id).file_name == "spec.pdf"


def test_run_worker_processes_job(queue):
    done = []
    stop = threading.Event()

    def handler(job):
        done.append(job.id)
        stop.set()

    queue.enqueue(_job())
    run_worker(queue, handler, stop_event=stop, poll_seconds=0.05)
    assert len(done) == 1
    assert queue.stats()["done"] == 1


def test_run_worker_reschedules_on_error(queue):
    stop = threading.Event()
    calls = []

    def handler(job):
        calls.append(job.attempts)
        if len(calls) >= 2:
            stop.set()
        raise RuntimeError("transient")

    queue.enqueue(_job())
    run_worker(queue, handler, stop_event=stop, poll_seconds=0.05)
    assert len(calls) == 2
    stats = queue.stats()
    assert stats["pending"] + stats["failed"] == 1
```

- [ ] **Step 2: Запустить, убедиться что падает**

Run: `.venv/bin/python -m pytest tests/test_bitrix_queue.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'bitrix_bot.queue'`).

- [ ] **Step 3: Реализовать**

Создать `bitrix_bot/queue.py`:

```python
"""SQLite-очередь заданий бота: персистентность между рестартами, ретраи."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Optional

MAX_ATTEMPTS = 3
RETRY_DELAYS = [5, 20, 60]  # паузы перед повтором, сек


@dataclass
class Job:
    dialog_id: str
    task_id: Optional[int]
    pdf_path: str
    file_name: str
    order_comment: str
    id: int = 0
    attempts: int = 0


class JobQueue:
    def __init__(self, db_path: str | Path, tmp_dir: str | Path):
        self._db_path = str(db_path)
        self._tmp_dir = Path(tmp_dir)
        self._tmp_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    dialog_id TEXT NOT NULL,
                    task_id INTEGER,
                    pdf_path TEXT NOT NULL,
                    file_name TEXT NOT NULL,
                    order_comment TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'pending',
                    error TEXT
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def _row_to_job(row: sqlite3.Row) -> Job:
        return Job(
            id=row["id"],
            dialog_id=row["dialog_id"],
            task_id=row["task_id"],
            pdf_path=row["pdf_path"],
            file_name=row["file_name"],
            order_comment=row["order_comment"],
            attempts=row["attempts"],
        )

    def enqueue(self, job: Job, pdf_bytes: Optional[bytes] = None) -> Job:
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO jobs (dialog_id, task_id, pdf_path, file_name, order_comment)"
                " VALUES (?, ?, ?, ?, ?)",
                (job.dialog_id, job.task_id, job.pdf_path, job.file_name, job.order_comment),
            )
            job_id = cur.lastrowid
            if pdf_bytes is not None:
                pdf_path = self._tmp_dir / f"{job_id}.pdf"
                pdf_path.write_bytes(pdf_bytes)
                conn.execute(
                    "UPDATE jobs SET pdf_path = ? WHERE id = ?", (str(pdf_path), job_id)
                )
            job.id = job_id
            job.pdf_path = str(self._tmp_dir / f"{job_id}.pdf")
            return job

    def get(self, job_id: int) -> Job:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            raise KeyError(f"job {job_id} not found")
        return self._row_to_job(row)

    def next_pending(self) -> Optional[Job]:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM jobs WHERE status = 'pending' ORDER BY id LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            conn.execute(
                "UPDATE jobs SET status = 'running' WHERE id = ?", (row["id"],)
            )
            return self._row_to_job(row)

    def complete(self, job_id: int) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("UPDATE jobs SET status = 'done' WHERE id = ?", (job_id,))

    def reschedule(self, job_id: int, error: str) -> None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT attempts FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
            attempts = row["attempts"] + 1
            status = "pending" if attempts < MAX_ATTEMPTS else "failed"
            conn.execute(
                "UPDATE jobs SET status = ?, attempts = ?, error = ? WHERE id = ?",
                (status, attempts, error[:500], job_id),
            )

    def stats(self) -> Dict[str, int]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT status, COUNT(*) AS n FROM jobs GROUP BY status"
            ).fetchall()
        return {r["status"]: r["n"] for r in rows}


def run_worker(
    queue: JobQueue,
    handler: Callable[[Job], None],
    stop_event: Optional[threading.Event] = None,
    poll_seconds: float = 2.0,
) -> None:
    """Блокирующий цикл воркера: handler(job); исключение -> reschedule."""
    stop_event = stop_event or threading.Event()
    while not stop_event.is_set():
        job = queue.next_pending()
        if job is None:
            time.sleep(poll_seconds)
            continue
        try:
            handler(job)
            queue.complete(job.id)
        except Exception as exc:  # noqa: BLE001 - любой сбой -> ретрай
            queue.reschedule(job.id, f"{type(exc).__name__}: {exc}")
```

- [ ] **Step 4: Запустить, убедиться что проходит**

Run: `.venv/bin/python -m pytest tests/test_bitrix_queue.py -v`
Expected: PASS (6 тестов).

- [ ] **Step 5: Commit**

```bash
git add bitrix_bot/queue.py tests/test_bitrix_queue.py
git commit -m "feat(bitrix_bot): SQLite job queue with retries and worker loop"
```


---

### Task 6: Webhook-endpoint и воркер (server.py, events.py)

**Files:**
- Create: `bitrix_bot/events.py`
- Create: `bitrix_bot/server.py`
- Test: `tests/test_bitrix_server.py`

**Interfaces:**
- Consumes: всё из Tasks 1–5: `load_bot_config`/`BotConfig`, `BitrixClient`, `JobQueue`/`Job`/`run_worker`, `run_pipeline`/`PipelineResult`, `build_report`, `parse_event`/`find_pdf` (эта задача).
- Produces:
  - `events.BotEvent` — dataclass: `dialog_id: str`, `message_id: str`, `user_id: int | None`, `text: str`, `task_id: int | None`, `file_url: str | None`, `file_name: str | None`.
  - `events.PdfNotFound(RuntimeError)`.
  - `events.parse_event(payload: dict) -> BotEvent | None` — `None` если событие не касается бота (не ONIMBOTMESSAGEADD, нет DIALOG_ID).
  - `events.find_pdf(client, event) -> tuple[bytes, str]` — поднимает `PdfNotFound` с понятным сообщением, если PDF не нашёлся.
  - `server.create_app(cfg: BotConfig | None = None, client=None, queue=None, start_worker: bool = True) -> FastAPI`; `server.make_handler(cfg, client) -> Callable[[Job], None]` — обработчик задания (скачанный PDF уже в `job.pdf_path`): `run_pipeline` → `build_report` → отправка сообщений в `dialog_id`.
  - Эндпоинты: `POST /webhook/bot` (верификация заголовка `X-Webhook-Token`, быстрый 200), `GET /health` → `{"ok": True, "stats": queue.stats()}`.

⚠️ Реальная схема событий Битрикса (что именно приходит в reply-контексте) известна не полностью — парсинг написан дефенсивно (проверяет несколько известных ключей), ручная сверка с живым событием — в Task 7.

- [ ] **Step 1: Написать failing test**

Создать `tests/test_bitrix_server.py`:

```python
"""Тесты FastAPI-сервера бота (клиент Битрикса замокан duck-typing)."""
import pytest
from fastapi.testclient import TestClient

import bitrix_bot.server as srv
from bitrix_bot.config import BotConfig
from bitrix_bot.events import parse_event
from bitrix_bot.pipeline import PipelineResult
from bitrix_bot.queue import Job, JobQueue


class FakeClient:
    """Замена BitrixClient: записывает сообщения, отдаёт файл/историю диалога."""

    def __init__(self):
        self.messages = []
        self.dialog_messages = []
        self.task_titles = {}

    def send_message(self, dialog_id, text):
        self.messages.append((dialog_id, text))

    def get_dialog_messages(self, dialog_id, limit=30):
        return self.dialog_messages

    def get_task_title(self, task_id):
        return self.task_titles.get(task_id, "")

    def download_file(self, url):
        return b"%PDF-fake-bytes"


def _payload(**params):
    return {
        "event": "ONIMBOTMESSAGEADD",
        "data": {"PARAMS": {"DIALOG_ID": "task|42", "MESSAGE_ID": "7", "FROM_USER_ID": 5,
                            "MESSAGE": "@Спец2Заказ загрузи", **params}},
    }


@pytest.fixture()
def env(tmp_path):
    cfg = BotConfig(
        portal="https://b24",
        incoming_webhook="https://b24/rest/1/KEY/",
        verify_token="tok",
        tmp_dir=str(tmp_path / "tmp"),
        report_limit=3500,
        execute_code_url="http://x/api/execute_code",
        request_timeout=10,
        task_comment_prefix="Задача Битрикс24",
    )
    client = FakeClient()
    queue = JobQueue(tmp_path / "jobs.db", tmp_path / "tmp")
    return cfg, client, queue


def test_parse_event_task_dialog():
    ev = parse_event(_payload())
    assert ev.dialog_id == "task|42"
    assert ev.task_id == 42
    assert ev.user_id == 5
    assert ev.file_url is None


def test_parse_event_ignores_other():
    assert parse_event({"event": "ONCRMLEADADD", "data": {}}) is None
    assert parse_event({"event": "ONIMBOTMESSAGEADD", "data": {"PARAMS": {}}}) is None


def test_webhook_rejects_bad_token(env):
    cfg, client, queue = env
    app = srv.create_app(cfg, client=client, queue=queue, start_worker=False)
    resp = TestClient(app).post("/webhook/bot", json=_payload(),
                                headers={"X-Webhook-Token": "wrong"})
    assert resp.status_code == 401


def test_webhook_enqueues_and_acks(env):
    cfg, client, queue = env
    client.dialog_messages = [
        {"id": 1, "params": {"FILE_URL": "https://b24/disk/download/1&auth=x",
                             "FILE_NAME": "ОВ2.pdf"}},
    ]
    client.task_titles[42] = "Шипиловский — ОВ2"
    app = srv.create_app(cfg, client=client, queue=queue, start_worker=False)
    resp = TestClient(app).post("/webhook/bot", json=_payload(),
                                headers={"X-Webhook-Token": "tok"})
    assert resp.status_code == 200 and resp.json()["ok"] is True
    assert queue.stats().get("pending") == 1
    job = queue.next_pending()
    assert job.task_id == 42
    assert job.file_name == "ОВ2.pdf"
    assert job.order_comment == "Задача Битрикс24 №42: Шипиловский — ОВ2"
    assert open(job.pdf_path, "rb").read() == b"%PDF-fake-bytes"
    # ack отправлен
    assert any("Принял" in m[1] and "ОВ2.pdf" in m[1] for m in client.messages)


def test_webhook_no_pdf_asks_to_attach(env):
    cfg, client, queue = env
    app = srv.create_app(cfg, client=client, queue=queue, start_worker=False)
    resp = TestClient(app).post("/webhook/bot", json=_payload(),
                                headers={"X-Webhook-Token": "tok"})
    assert resp.status_code == 200
    assert queue.stats().get("pending", 0) == 0
    assert any("PDF" in m[1] for m in client.messages)


def test_handler_sends_report(env, monkeypatch):
    cfg, client, queue = env
    job = queue.enqueue(
        Job(dialog_id="task|42", task_id=42, pdf_path="", file_name="spec.pdf",
            order_comment="c"),
        pdf_bytes=b"%PDF",
    )
    fake = PipelineResult(
        file_name="spec.pdf", order_number="000000860",
        loaded=[{"article": "1-2-1", "quantity": 6, "comment": "", "params": {}}],
        skipped=[{"name": "Клапан", "size": "200", "quantity": "1", "reason": "нет маппинга"}],
        errors_1c=[], warnings_1c=[],
    )
    monkeypatch.setattr(srv, "run_pipeline", lambda *a, **kw: fake)
    handler = srv.make_handler(cfg, client)
    handler(job)
    texts = [m[1] for m in client.messages if m[0] == "task|42"]
    joined = "\n".join(texts)
    assert "Заказ 1С: №000000860" in joined
    assert "Загружено: 1" in joined and "Пропущено: 1" in joined
    assert "Клапан" in joined and "нет маппинга" in joined


def test_health(env):
    cfg, client, queue = env
    app = srv.create_app(cfg, client=client, queue=queue, start_worker=False)
    resp = TestClient(app).get("/health")
    assert resp.status_code == 200 and resp.json()["ok"] is True
```

- [ ] **Step 2: Запустить, убедиться что падает**

Run: `.venv/bin/python -m pytest tests/test_bitrix_server.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'bitrix_bot.events'`).

- [ ] **Step 3: Реализовать**

Создать `bitrix_bot/events.py`:

```python
"""Разбор событий webhook Битрикса и поиск PDF в чате задачи.

Схема событий Битрикс24 не документирована до конца (что приходит в
reply-контексте — см. Task 7, ручная сверка), поэтому парсинг ищет файл
по нескольким известным ключам. Цепочка: reply-контекст сообщения →
последние сообщения диалога → PdfNotFound.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple


class PdfNotFound(RuntimeError):
    """PDF не найден в задаче/диалоге."""


@dataclass
class BotEvent:
    dialog_id: str
    message_id: str
    user_id: Optional[int]
    text: str
    task_id: Optional[int]
    file_url: Optional[str] = None
    file_name: Optional[str] = None


def task_id_from_dialog(dialog_id: str) -> Optional[int]:
    """'task|42' -> 42; остальное -> None."""
    if dialog_id.startswith("task|"):
        try:
            return int(dialog_id.split("|", 1)[1])
        except ValueError:
            return None
    return None


def _first_file(params: dict) -> Tuple[Optional[str], Optional[str]]:
    """Вытащить (url, имя) файла из params сообщения — best effort по известным ключам."""
    files = params.get("FILES") or params.get("files") or []
    if isinstance(files, list) and files:
        f0 = files[0] or {}
        url = f0.get("url") or f0.get("downloadUrl") or f0.get("DOWNLOAD_URL")
        name = f0.get("name") or f0.get("FILE_NAME") or "document.pdf"
        if url:
            return url, name
    for key in ("FILE_URL", "DOWNLOAD_URL", "ATTACH_URL"):
        if params.get(key):
            return params[key], params.get("FILE_NAME") or "document.pdf"
    attach = params.get("ATTACH")
    if isinstance(attach, list):
        for block in attach:
            if isinstance(block, dict) and block.get("LINK"):
                return block["LINK"], block.get("NAME") or "document.pdf"
    return None, None


def parse_event(payload: dict) -> Optional[BotEvent]:
    """Разобрать тело webhook; None — событие не касается бота."""
    if payload.get("event") != "ONIMBOTMESSAGEADD":
        return None
    params = ((payload.get("data") or {}).get("PARAMS")) or {}
    dialog_id = params.get("DIALOG_ID", "")
    if not dialog_id:
        return None
    # reply-контекст: в некоторых версиях приходит целиком цитируемое сообщение
    replied = params.get("MESSAGE_REPLIED") or params.get("message_replied") or {}
    replied_params = replied.get("params") if isinstance(replied, dict) else None
    file_url, file_name = (None, None)
    if isinstance(replied_params, dict):
        file_url, file_name = _first_file(replied_params)
    if not file_url:
        file_url, file_name = _first_file(params)
    user_raw = params.get("FROM_USER_ID") or params.get("from_user_id")
    return BotEvent(
        dialog_id=dialog_id,
        message_id=str(params.get("MESSAGE_ID", "")),
        user_id=int(user_raw) if user_raw else None,
        text=params.get("MESSAGE", ""),
        task_id=task_id_from_dialog(dialog_id),
        file_url=file_url,
        file_name=file_name,
    )


def _file_from_message(msg: dict) -> Tuple[Optional[str], Optional[str]]:
    params = msg.get("params") if isinstance(msg, dict) else None
    if isinstance(params, dict):
        return _first_file(params)
    return None, None


def find_pdf(client, event: BotEvent) -> Tuple[bytes, str]:
    """Скачать PDF: из reply-контекста, иначе из последних сообщений диалога."""
    if event.file_url:
        return client.download_file(event.file_url), event.file_name or "document.pdf"
    for msg in client.get_dialog_messages(event.dialog_id, limit=30):
        url, name = _file_from_message(msg)
        if url:
            return client.download_file(url), name or "document.pdf"
    raise PdfNotFound(
        "Не нашёл PDF: ответьте (reply) на сообщение с файлом и упомяните меня ещё раз."
    )
```

Создать `bitrix_bot/server.py`:

```python
"""FastAPI-сервис бота: webhook от Битрикса + фоновый воркер очереди."""

from __future__ import annotations

import logging
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Callable, Optional

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from bitrix_bot.bitrix_client import BitrixClient
from bitrix_bot.config import BotConfig, load_bot_config
from bitrix_bot.events import PdfNotFound, find_pdf, parse_event
from bitrix_bot.pipeline import run_pipeline
from bitrix_bot.queue import Job, JobQueue, run_worker
from bitrix_bot.report import build_report

logger = logging.getLogger(__name__)


def make_handler(cfg: BotConfig, client) -> Callable[[Job], None]:
    """Обработчик задания очереди: пайплайн -> отчёт в чат задачи."""
    def handle(job: Job) -> None:
        pdf_bytes = Path(job.pdf_path).read_bytes()
        res = run_pipeline(
            pdf_bytes, job.file_name, job.order_comment,
            cfg.execute_code_url, timeout=cfg.request_timeout,
        )
        for msg in build_report(res, cfg.report_limit):
            client.send_message(job.dialog_id, msg)
    return handle


def create_app(
    cfg: Optional[BotConfig] = None,
    client=None,
    queue: Optional[JobQueue] = None,
    start_worker: bool = True,
) -> FastAPI:
    cfg = cfg or load_bot_config()
    client = client or BitrixClient(cfg.incoming_webhook, timeout=30.0)
    queue = queue or JobQueue(
        Path(cfg.tmp_dir) / "jobs.db", cfg.tmp_dir
    )

    worker_stop: Optional[threading.Event] = None

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        nonlocal worker_stop
        worker_stop = threading.Event()
        thread = None
        if start_worker:
            handler = make_handler(cfg, client)
            thread = threading.Thread(
                target=run_worker, args=(queue, handler, worker_stop),
                daemon=True, name="bitrix-bot-worker",
            )
            thread.start()
        yield
        worker_stop.set()
        if thread:
            thread.join(timeout=5)

    app = FastAPI(title="spec-to-1c bitrix bot", lifespan=lifespan)

    @app.post("/webhook/bot")
    async def webhook(request: Request, background: BackgroundTasks):
        if cfg.verify_token:
            token = request.headers.get("X-Webhook-Token", "")
            if token != cfg.verify_token:
                raise HTTPException(status_code=401, detail="bad token")
        payload = await request.json()
        event = parse_event(payload)
        if event is None:
            return JSONResponse({"ok": True})
        try:
            pdf_bytes, file_name = find_pdf(client, event)
        except PdfNotFound as exc:
            background.add_task(client.send_message, event.dialog_id, str(exc))
            return JSONResponse({"ok": True})
        title = client.get_task_title(event.task_id) if event.task_id else ""
        if event.task_id:
            comment = f"{cfg.task_comment_prefix} №{event.task_id}: {title}"
        else:
            comment = cfg.task_comment_prefix
        job = queue.enqueue(
            Job(
                dialog_id=event.dialog_id,
                task_id=event.task_id,
                pdf_path="",
                file_name=file_name,
                order_comment=comment,
            ),
            pdf_bytes=pdf_bytes,
        )
        background.add_task(
            client.send_message, event.dialog_id,
            f"Принял «{file_name}», обрабатываю…",
        )
        return JSONResponse({"ok": True, "job_id": job.id})

    @app.get("/health")
    async def health():
        return {"ok": True, "stats": queue.stats()}

    return app


app = create_app()
```

- [ ] **Step 4: Запустить, убедиться что проходит**

Run: `.venv/bin/python -m pytest tests/test_bitrix_server.py -v`
Expected: PASS (7 тестов).

- [ ] **Step 5: Проверить, что весь новый пакет зелёный**

Run: `.venv/bin/python -m pytest tests/test_bitrix_config.py tests/test_bitrix_report.py tests/test_bitrix_client.py tests/test_bitrix_pipeline.py tests/test_bitrix_queue.py tests/test_bitrix_server.py tests/test_json_positions.py -v`
Expected: PASS все.

- [ ] **Step 6: Commit**

```bash
git add bitrix_bot/events.py bitrix_bot/server.py tests/test_bitrix_server.py
git commit -m "feat(bitrix_bot): FastAPI webhook server, event parsing, worker handler"
```

---

### Task 7: Документация развёртывания и ручная сверка со схемой событий

**Files:**
- Create: `bitrix_bot/README.md`

**Interfaces:**
- Produces: инструкция развёртывания (приложение Битрикса, systemd, nginx) и чек-лист ручной сверки реальных событий. Кода нет — задача завершается документом; автотестов нет, проверка = ревью документа.

- [ ] **Step 1: Написать bitrix_bot/README.md**

```markdown
# Битрикс-бот «Спецификация → Заказ 1С»

Чат-бот в задачах Битрикс24: reply на сообщение с PDF + @упоминание →
заказ в 1С (асСпецификацияЗаказа) + отчёт в чат задачи.
Спека: docs/superpowers/specs/2026-09-06-bitrix-bot-design.md.

## Запуск локально

    .venv/bin/uvicorn bitrix_bot.server:app --host 127.0.0.1 --port 8080

Конфиг: config.yaml (секции bitrix:/middleware:) + секреты в bitrix.local.yaml
(gitignored): incoming_webhook, app_client_id, app_client_secret,
webhook_verify_token (придумать случайную строку, она же — заголовок
X-Webhook-Token при настройке приложения).

## Настройка Битрикс24 (svok-kavkaz.bitrix24.ru, админ)

1. Разработчикам → Другое → Локальное приложение: создать, указать
   URL обработчика событий https://<домен>/webhook/bot, права im, task, disk.
   Скопировать client_id/client_secret в bitrix.local.yaml.
2. Регистрация чат-бота (выполнить один раз из консоли с токеном приложения):
   imbot.register с EVENT_MESSAGE_ADD → https://<домен>/webhook/bot,
   тип открытый, права im/task/disk.
3. Вебхук для REST уже есть (incoming_webhook в bitrix.local.yaml).

## Прод (сервер завода)

systemd — /etc/systemd/system/bitrix-bot.service:

    [Unit]
    Description=spec-to-1c bitrix bot
    After=network.target

    [Service]
    WorkingDirectory=/home/dimon64515/projects/xml-to-1c
    ExecStart=/home/dimon64515/projects/xml-to-1c/.venv/bin/uvicorn bitrix_bot.server:app --host 127.0.0.1 --port 8080
    Restart=always
    RestartSec=5

    [Install]
    WantedBy=multi-user.target

nginx: server 443 ssl для <домена> → proxy_pass http://127.0.0.1:8080
(websockets не нужны; certbot --nginx). Порт 6005 (MCP/1С) наружу НЕ публиковать.

## Ручная сверка событий (один раз после настройки)

1. Создать тестовую задачу, приложить PDF, reply + @бот.
2. Сравнить тело POST /webhook/bot (лог: временно добавить print(payload) или
   смотреть access-лог nginx) с предположениями bitrix_bot/events.py:
   - приходит ли reply-контекст (цитируемое сообщение с файлом) и в каком ключе;
   - как в im.message.get выглядит сообщение с файлом (params.FILES? FILE_URL?).
3. При расхождении поправить _first_file/_file_from_message под реальную схему
   (fallback — последние сообщения диалога — уже реализован).

## Проверка конца в конец

Тестовая задача с реальным PDF из examples/customer_projects/ → в чате
«Принял …, обрабатываю…» → «Заказ 1С: №…» + сводка + списки.
Заказ в 1С проверить вручную (тестовая база).
```

- [ ] **Step 2: Commit**

```bash
git add bitrix_bot/README.md
git commit -m "docs(bitrix_bot): deployment and event-schema verification guide"
```

---

### Task 8: Финальная проверка всего пакета

**Files:**
- Нет изменений кода — только прогон тестов.

- [ ] **Step 1: Прогнать все тесты бота + затронутые существующие**

Run: `.venv/bin/python -m pytest tests/test_bitrix_config.py tests/test_bitrix_report.py tests/test_bitrix_client.py tests/test_bitrix_pipeline.py tests/test_bitrix_queue.py tests/test_bitrix_server.py tests/test_json_positions.py tests/test_config.py -q`
Expected: all green.

- [ ] **Step 2: Прогнать полный сьют проекта**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: all green (тесты price_search могут требовать сеть/camoufox — если падают по окружению, проверить, что падали ДО изменений: `git stash && .venv/bin/python -m pytest tests/ -q`).

- [ ] **Step 3: Итоговый коммит при необходимости**

```bash
git status  # убедиться, что ничего не забыто; секретов в diff быть не должно
```
