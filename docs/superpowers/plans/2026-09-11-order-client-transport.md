# order_client.py — транспорт загрузки заказов в 1С: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Новый модуль `order_client.py` — транспортная замена MCP `execute_code` для загрузки заказов в 1С (HTTP-сервис как основной путь, execute_code как фолбэк), плюс корпус позиций полного покрытия артикулов для приёмки.

**Architecture:** Один корневой модуль с двумя транспортами (`HttpServiceTransport`, `ExecuteCodeTransport`) за общим интерфейсом `send(...)`, диспетчером `transport_for(url)` (auto-detect по форме URL) и единой точкой входа `load_order(...)`. Оба транспорта возвращают ровно ту же форму результата, что и `bitrix_bot.pipeline.parse_1c_result` (`{"order_number", "errors", "warnings", "raw"}`), чтобы будущая интеграция заменила тело `load_order_to_1c` одной строкой. Ответ HTTP-сервиса `{Успех, НомерЗаказа, Ошибки, Предупреждения}` (ТЗ) маппится в ту же форму через общий парсер. Скоуп — только новые файлы; интеграция в бота/web_app отдельной задачей.

**Tech Stack:** Python 3.12, httpx (уже в проекте), pytest. Спека: `docs/superpowers/specs/2026-09-11-order-client-transport-design.md`.

## Global Constraints

- Выполняется на текущей ветке `feature/size-notation-config`.
- **Только новые файлы.** Ни один существующий файл не редактируется (решение спеки: скоуп — только транспорт).
- Зависимости: `httpx` + стандартная библиотека. Новые пакеты не добавлять.
- Тесты и python: `.venv/bin/python -m pytest <путь> -v` (venv в корне репо).
- Комментарии и docstrings — на русском, стиль как в `json_positions.py` / `bitrix_bot/pipeline.py`.
- Контракт результата (все задачи): `dict` с ключами `order_number: str|None`, `errors: list[str]`, `warnings: list[str]`, `raw: str` — без ключа `success`.
- Подпись `send` (одинаковая у обоих транспортов):
  `send(positions: list[dict], order_comment: str, request_id: str | None = None, timeout: float = 280.0) -> dict`
- Commit message: префиксы `feat:` / `test:` как в `git log`.

---

### Task 1: Парсер результата 1С и заглушка ответа для тестов

**Files:**
- Create: `order_client.py`
- Test: `tests/test_order_client.py`

**Interfaces:**
- Consumes: ничего (первый модуль проекта).
- Produces: `order_client.parse_1c_result(text: str) -> dict` — копия логики `bitrix_bot.pipeline.parse_1c_result` (тот же алгоритм, тот же формат строки `ЗАКАЗ № | строк=N | ошибок=N | предупр=N | ... ## ...`); класс `_Resp` в тестовом файле — заглушка `httpx.Response` для monkeypatch-стиля тестов (образец — `tests/test_bitrix_pipeline.py:25-34`).

- [ ] **Step 1: Написать падающий тест**

Создать `tests/test_order_client.py`:

```python
"""Тесты транспорта загрузки заказов в 1С (order_client)."""
import json

import httpx
import pytest

import order_client as oc

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
    """Заглушка httpx.Response для monkeypatched httpx.post (стиль test_bitrix_pipeline)."""

    def __init__(self, data, status_code: int = 200):
        self.status_code = status_code
        self._data = data
        if isinstance(data, str):
            self.text = data
        elif isinstance(data, Exception):
            self.text = str(data)
        else:
            self.text = json.dumps(data, ensure_ascii=False)

    def json(self):
        if isinstance(self._data, Exception):
            raise self._data
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code} for mock url",
                request=None,
                response=None,
            )


def test_parse_result_ok():
    out = oc.parse_1c_result(SAMPLE_1C_OK)
    assert out["order_number"] == "000000860"
    assert out["errors"] == []
    assert out["warnings"] == ["Строка 1 (1-2-1): цена 0 — проверьте прайс"]
    assert out["raw"] == SAMPLE_1C_OK


def test_parse_result_errors_joined_single_segment():
    # BSL склеивает ошибки/предупреждения через СтрСоединить(..., "; ") —
    # всё сообщение приходит ОДНИМ сегментом после "ошибок=N"
    text = (
        'ЗАКАЗ 000001 | строк=2 | ошибок=2'
        ' | не найден продукт "9-9-9"; не найден материал "X"'
        ' | предупр=1 | Строка 1: цена 0 — проверьте прайс'
    )
    out = oc.parse_1c_result(text)
    assert out["order_number"] == "000001"
    assert out["errors"] == ['не найден продукт "9-9-9"', 'не найден материал "X"']
    assert out["warnings"] == ["Строка 1: цена 0 — проверьте прайс"]
```

- [ ] **Step 2: Запустить тест, убедиться что падает**

Run: `.venv/bin/python -m pytest tests/test_order_client.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'order_client'`

- [ ] **Step 3: Минимальная реализация**

Создать `order_client.py`:

```python
"""Транспорты загрузки заказа в 1С: HTTP-сервис (замена MCP) и execute_code.

Скоуп — только этот модуль; интеграция в bitrix_bot/web_app отдельной
задачей после приёмки HTTP-сервиса
(см. docs/superpowers/specs/2026-09-11-order-client-transport-design.md).

Контракт результата — ровно те же ключи, что у
bitrix_bot.pipeline.parse_1c_result: {"order_number", "errors", "warnings",
"raw"} — чтобы будущая интеграция заменила тело load_order_to_1c одной строкой.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List


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
                # BSL склеивает ошибки СтрСоединить(Ошибки, "; ") — один сегмент
                errors = [e.strip() for e in segments[i + 1].split("; ")]
        m = re.match(r"предупр=(\d+)", seg)
        if m:
            n_warnings = int(m.group(1))
            if n_warnings:
                warnings = [w.strip() for w in segments[i + 1].split("; ")]
    return {
        "order_number": order_number,
        "errors": errors,
        "warnings": warnings,
        "raw": text,
    }
```

- [ ] **Step 4: Запустить тест, убедиться что проходит**

Run: `.venv/bin/python -m pytest tests/test_order_client.py -v`
Expected: PASS (2 теста)

- [ ] **Step 5: Commit**

```bash
git add order_client.py tests/test_order_client.py
git commit -m "feat(order_client): parser for 1C result string"
```

---

### Task 2: ExecuteCodeTransport (паритет с текущим MCP-путём)

**Files:**
- Modify: `order_client.py`
- Test: `tests/test_order_client.py`

**Interfaces:**
- Consumes: `parse_1c_result` (Task 1); `tools.as_order_loader.build_execute_payload.build_payload(positions, mark_delete=False, order_comment="") -> {"code": str}` — существующий код, импорт как в `bitrix_bot/pipeline.py:120`.
- Produces: `class ExecuteCodeTransport` с методом `send(...)` (подпись из Global Constraints); `order_client.transport_for(url: str, api_key: str = "") -> ExecuteCodeTransport` — пока распознаёт только `/api/execute_code`, для остальных URL `ValueError` (Task 3 добавит ветку `/hs/`).

- [ ] **Step 1: Написать падающие тесты**

Дописать в `tests/test_order_client.py`:

```python
def test_execute_code_transport_tunnel_envelope(monkeypatch):
    calls = {}

    def fake_post(url, json=None, timeout=None):
        calls["url"] = url
        calls["json"] = json
        calls["timeout"] = timeout
        return _Resp({"result": SAMPLE_1C_OK})

    monkeypatch.setattr(httpx, "post", fake_post)
    tr = oc.transport_for("http://127.0.0.1:6005/api/execute_code")
    assert isinstance(tr, oc.ExecuteCodeTransport)
    out = tr.send([{"article": "1-2-1"}], "Задача №42", timeout=12.0)
    assert out["order_number"] == "000000860"
    assert out["errors"] == []
    assert calls["timeout"] == 12.0
    # тело запроса — execute_code-конверт {"code": "<BSL>"}
    assert set(calls["json"].keys()) == {"code"}
    assert "Задача №42" in calls["json"]["code"]


def test_execute_code_transport_native_envelope(monkeypatch):
    # нативный HTTP-API MCPToolkit: {"success": true, "data": "..."}
    monkeypatch.setattr(
        httpx, "post",
        lambda url, json=None, timeout=None: _Resp(
            {"success": True, "data": SAMPLE_1C_OK}
        ),
    )
    tr = oc.ExecuteCodeTransport("http://127.0.0.1:6005/api/execute_code")
    out = tr.send([{"article": "1-2-1"}], "c")
    assert out["order_number"] == "000000860"
    assert out["warnings"] == ["Строка 1 (1-2-1): цена 0 — проверьте прайс"]


def test_execute_code_transport_failure_envelope(monkeypatch):
    # {"success": false, "error": "..."} — исключение с текстом 1С
    monkeypatch.setattr(
        httpx, "post",
        lambda url, json=None, timeout=None: _Resp(
            {"success": False, "error": "Ошибка компиляции"}
        ),
    )
    tr = oc.ExecuteCodeTransport("http://127.0.0.1:6005/api/execute_code")
    with pytest.raises(RuntimeError, match="Ошибка компиляции"):
        tr.send([{"article": "1-2-1"}], "c")


def test_transport_for_unknown_url_raises():
    with pytest.raises(ValueError, match="неизвестный URL"):
        oc.transport_for("https://example.com/anything")
```

- [ ] **Step 2: Запустить тесты, убедиться что падают**

Run: `.venv/bin/python -m pytest tests/test_order_client.py -v`
Expected: FAIL — `AttributeError: module 'order_client' has no attribute 'transport_for'`

- [ ] **Step 3: Реализация**

В `order_client.py` добавить импорт `httpx` (в шапку, к остальным) и код:

```python
import httpx


class ExecuteCodeTransport:
    """Текущий путь через MCP Toolkit execute_code (фолбэк на переходный период).

    request_id не поддерживается execute_code-конвертом — параметр принимается
    ради единой подписи send() и игнорируется.
    """

    def __init__(self, execute_url: str) -> None:
        self.execute_url = execute_url

    def send(
        self,
        positions: List[dict],
        order_comment: str,
        request_id: str | None = None,
        timeout: float = 280.0,
    ) -> Dict[str, Any]:
        from tools.as_order_loader.build_execute_payload import build_payload

        payload = build_payload(positions, order_comment=order_comment)
        resp = httpx.post(self.execute_url, json=payload, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        # Два envelope ответа:
        # - нативный HTTP-API MCPToolkit: {"success": bool, "data"|"error": str}
        # - MCP-прокси (tunnel): {"result": ...}
        if isinstance(data, dict) and data.get("success") is False:
            raise RuntimeError(f"1С execute_code: {data.get('error', data)}")
        if isinstance(data, dict):
            text = str(data.get("data") or data.get("result") or data)
        else:
            text = str(data)
        return parse_1c_result(text)


def transport_for(url: str, api_key: str = "") -> ExecuteCodeTransport:
    if "/api/execute_code" in url:
        return ExecuteCodeTransport(url)
    raise ValueError(f"неизвестный URL транспорта 1С: {url}")
```

- [ ] **Step 4: Запустить тесты, убедиться что проходят**

Run: `.venv/bin/python -m pytest tests/test_order_client.py -v`
Expected: PASS (6 тестов)

- [ ] **Step 5: Commit**

```bash
git add order_client.py tests/test_order_client.py
git commit -m "feat(order_client): ExecuteCodeTransport parity with MCP path"
```

---

### Task 3: HttpServiceTransport и единая точка входа load_order

**Files:**
- Modify: `order_client.py`
- Test: `tests/test_order_client.py`

**Interfaces:**
- Consumes: `parse_1c_result` (Task 1), `transport_for` (Task 2), `_Resp` (Task 1).
- Produces: `class HttpServiceTransport(base_url: str, api_key: str = "")` с методом `send(...)` (подпись из Global Constraints); `transport_for` теперь распознаёт `/hs/` → `HttpServiceTransport` и `/api/execute_code` → `ExecuteCodeTransport`; `order_client.load_order(positions, url, order_comment, request_id=None, api_key="", timeout=280.0) -> dict` — диспетчер через `transport_for`.

Кодирование запроса сервиса (ТЗ п. 3): тело `{"order_comment": ..., "request_id": ..., "positions": [...]}` — ключ `request_id` всегда присутствует (`None` → JSON `null`); отправка через `content=<utf-8 bytes>` + явный заголовок `Content-Type: application/json; charset=utf-8` (httpx `json=` не ставит charset); заголовок `X-API-Key` — только если ключ не пустой.

- [ ] **Step 1: Написать падающие тесты**

Дописать в `tests/test_order_client.py`:

```python
SERVICE_URL = "https://srv1c/base/hs/vok/order"

SERVICE_OK = {
    "Успех": True,
    "НомерЗаказа": "000000860",
    "Ошибки": [],
    "Предупреждения": ["Строка 1 (1-2-1): цена 0 — проверьте прайс"],
}

SERVICE_BUSINESS_ERRORS = {
    "Успех": False,
    "НомерЗаказа": "000000861",
    "Ошибки": ['Строка 1: не найден продукт "9-9-9"'],
    "Предупреждения": [],
}

SERVICE_500 = {
    "Успех": False,
    "НомерЗаказа": None,
    "Ошибки": ["Ошибка при вызове метода контекста (Записать)"],
    "Предупреждения": [],
}


def _capture_post(monkeypatch, resp):
    calls = {}

    def fake_post(url, content=None, json=None, headers=None, timeout=None):
        calls["url"] = url
        calls["content"] = content
        calls["json"] = json
        calls["headers"] = headers or {}
        calls["timeout"] = timeout
        return resp

    monkeypatch.setattr(httpx, "post", fake_post)
    return calls


def test_service_success_200(monkeypatch):
    calls = _capture_post(monkeypatch, _Resp(SERVICE_OK))
    tr = oc.transport_for(SERVICE_URL, api_key="secret-1")
    assert isinstance(tr, oc.HttpServiceTransport)
    out = tr.send(
        [{"article": "1-2-1"}], "Задача №42", request_id="bx1-job7", timeout=30.0
    )
    assert out["order_number"] == "000000860"
    assert out["errors"] == []
    assert out["warnings"] == ["Строка 1 (1-2-1): цена 0 — проверьте прайс"]
    # контракт запроса (ТЗ п. 3)
    assert calls["url"] == SERVICE_URL
    body = json.loads(calls["content"].decode("utf-8"))
    assert body == {
        "order_comment": "Задача №42",
        "request_id": "bx1-job7",
        "positions": [{"article": "1-2-1"}],
    }
    assert calls["json"] is None  # тело шлём через content, не через json=
    assert calls["headers"]["Content-Type"] == "application/json; charset=utf-8"
    assert calls["headers"]["X-API-Key"] == "secret-1"
    assert calls["timeout"] == 30.0


def test_service_business_errors_200_no_exception(monkeypatch):
    _capture_post(monkeypatch, _Resp(SERVICE_BUSINESS_ERRORS))
    tr = oc.HttpServiceTransport(SERVICE_URL)
    out = tr.send([{"article": "9-9-9"}], "c")
    # бизнес-ошибки — значением, не исключением (как в текущем пути)
    assert out["order_number"] == "000000861"
    assert out["errors"] == ['Строка 1: не найден продукт "9-9-9"']


def test_service_structured_500_is_result_not_exception(monkeypatch):
    # ТЗ п. 4: 500 с валидной структурой — не исключение
    _capture_post(monkeypatch, _Resp(SERVICE_500, status_code=500))
    tr = oc.HttpServiceTransport(SERVICE_URL)
    out = tr.send([{"article": "1-2-1"}], "c")
    assert out["order_number"] is None
    assert out["errors"] == ["Ошибка при вызове метода контекста (Записать)"]


def test_service_unstructured_500_raises_http_error(monkeypatch):
    _capture_post(monkeypatch, _Resp("<html>Gateway Timeout</html>", status_code=500))
    tr = oc.HttpServiceTransport(SERVICE_URL)
    with pytest.raises(httpx.HTTPError):
        tr.send([{"article": "1-2-1"}], "c")


def test_service_4xx_raises_runtime_error(monkeypatch):
    _capture_post(monkeypatch, _Resp("Unauthorized", status_code=401))
    tr = oc.HttpServiceTransport(SERVICE_URL)
    with pytest.raises(RuntimeError, match="401"):
        tr.send([{"article": "1-2-1"}], "c")


def test_service_bad_json_raises_runtime_error(monkeypatch):
    _capture_post(monkeypatch, _Resp(ValueError("no json"), status_code=200))
    tr = oc.HttpServiceTransport(SERVICE_URL)
    with pytest.raises(RuntimeError, match="битый JSON"):
        tr.send([{"article": "1-2-1"}], "c")


def test_service_timeout_raises_http_error(monkeypatch):
    def boom(url, content=None, json=None, headers=None, timeout=None):
        raise httpx.TimeoutException("read timeout")

    monkeypatch.setattr(httpx, "post", boom)
    tr = oc.HttpServiceTransport(SERVICE_URL)
    with pytest.raises(httpx.HTTPError):
        tr.send([{"article": "1-2-1"}], "c")


def test_load_order_dispatches_by_url(monkeypatch):
    # load_order — единая точка входа: auto-detect транспорта по форме URL
    calls = _capture_post(monkeypatch, _Resp(SERVICE_OK))
    out = oc.load_order(
        [{"article": "1-2-1"}], SERVICE_URL, "c",
        request_id="r-1", api_key="k",
    )
    assert out["order_number"] == "000000860"
    assert calls["headers"]["X-API-Key"] == "k"

    calls2 = _capture_post(monkeypatch, _Resp({"result": SAMPLE_1C_OK}))
    out2 = oc.load_order(
        [{"article": "1-2-1"}], "http://127.0.0.1:6005/api/execute_code", "c"
    )
    assert out2["order_number"] == "000000860"
    assert set(calls2["json"].keys()) == {"code"}
```

Тест `test_transport_for_unknown_url_raises` из Task 2 остаётся без изменений.

- [ ] **Step 2: Запустить тесты, убедиться что падают**

Run: `.venv/bin/python -m pytest tests/test_order_client.py -v`
Expected: FAIL — `AttributeError: module 'order_client' has no attribute 'HttpServiceTransport'`

- [ ] **Step 3: Реализация**

В `order_client.py` добавить импорт `json` (в шапку) и код:

```python
class HttpServiceTransport:
    """HTTP-сервис расширения ВОКЗагрузкаЗаказаHTTP (POST /hs/vok/order).

    Контракт запроса/ответа: docs/ТЗ_HTTP_СЕРВИС_ЗАГРУЗКА_ЗАКАЗА_1С.md.
    Ответ {Успех, НомерЗаказа, Ошибки, Предупреждения} маппится в форму
    parse_1c_result — результат неотличим от execute_code-пути.
    """

    def __init__(self, base_url: str, api_key: str = "") -> None:
        self.base_url = base_url
        self.api_key = api_key

    def send(
        self,
        positions: List[dict],
        order_comment: str,
        request_id: str | None = None,
        timeout: float = 280.0,
    ) -> Dict[str, Any]:
        body = {
            "order_comment": order_comment,
            "request_id": request_id,
            "positions": positions,
        }
        headers = {"Content-Type": "application/json; charset=utf-8"}
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        # content=, а не json=: httpx json= не ставит charset=utf-8 (ТЗ п. 3)
        resp = httpx.post(
            self.base_url,
            content=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            timeout=timeout,
        )
        text = resp.text or ""
        data: Dict[str, Any] | None = None
        try:
            parsed = resp.json()
            if isinstance(parsed, dict):
                data = parsed
        except ValueError:
            data = None
        # 5xx со структурой ТЗ — бизнес-ответ (Успех=false), не исключение
        if resp.status_code >= 500 and data is not None and "Успех" in data:
            return self._to_result(data, text)
        if resp.status_code >= 500:
            resp.raise_for_status()  # httpx.HTTPStatusError — ретрай
        if resp.status_code >= 400:
            # детерминированная ошибка конфигурации (ключ/метод) — ретрай бессмысленен
            raise RuntimeError(
                f"HTTP-сервис 1С: HTTP {resp.status_code}: {text[:500]}"
            )
        if data is None:
            raise RuntimeError(f"HTTP-сервис 1С: битый JSON в ответе: {text[:500]}")
        return self._to_result(data, text)

    @staticmethod
    def _to_result(data: Dict[str, Any], raw: str) -> Dict[str, Any]:
        """{Успех, НомерЗаказа, Ошибки, Предупреждения} → форма parse_1c_result."""
        errors = [str(e) for e in (data.get("Ошибки") or [])]
        warnings = [str(w) for w in (data.get("Предупреждения") or [])]
        number = data.get("НомерЗаказа")
        segments = []
        if number:
            segments.append(f"ЗАКАЗ {number}")
        segments.append(f"ошибок={len(errors)}")
        if errors:
            segments.append("; ".join(errors))
        segments.append(f"предупр={len(warnings)}")
        if warnings:
            segments.append("; ".join(warnings))
        out = parse_1c_result(" | ".join(segments))
        out["raw"] = raw
        return out


def load_order(
    positions: List[dict],
    url: str,
    order_comment: str,
    request_id: str | None = None,
    api_key: str = "",
    timeout: float = 280.0,
) -> Dict[str, Any]:
    """Единая точка входа: auto-detect транспорта по форме URL."""
    return transport_for(url, api_key=api_key).send(
        positions, order_comment, request_id=request_id, timeout=timeout
    )
```

И заменить в `order_client.py` функцию `transport_for` (из Task 2) на:

```python
def transport_for(url: str, api_key: str = "") -> "ExecuteCodeTransport | HttpServiceTransport":
    if "/hs/" in url:
        return HttpServiceTransport(url, api_key=api_key)
    if "/api/execute_code" in url:
        return ExecuteCodeTransport(url)
    raise ValueError(f"неизвестный URL транспорта 1С: {url}")
```

- [ ] **Step 4: Запустить тесты, убедиться что проходят**

Run: `.venv/bin/python -m pytest tests/test_order_client.py -v`
Expected: PASS (14 тестов)

- [ ] **Step 5: Прогнать весь тестовый набор (регрессия — модуль никого не трогает, но проверить дёшево)**

Run: `.venv/bin/python -m pytest tests/ -x -q`
Expected: PASS (все зелёные; новый модуль ни от кого не зависит)

- [ ] **Step 6: Commit**

```bash
git add order_client.py tests/test_order_client.py
git commit -m "feat(order_client): HttpServiceTransport and load_order entry"
```

---

### Task 4: Корпус позиций полного покрытия (генератор + фикстура + контрактный тест)

**Files:**
- Create: `tools/build_order_positions_fixture.py`
- Create: `tests/fixtures/order_positions_full.json` (генерируется скриптом, коммитится)
- Test: `tests/test_order_positions_fixture.py`

**Interfaces:**
- Consumes: `bitrix_bot.pipeline.BLOCKED_1C_ARTICLES` (dict blocked-артибутов), `bitrix_bot.pipeline.process_pdf_to_positions(pdf_bytes) -> (success, skipped)`, `json_positions.build_positions(parsed_rows) -> list[dict]`; `reference/1c_products_all.json` (снимок справочника `асПродукция`: `{"success": true, "data": [{"Артикул": ..., "ЭтоГруппа": ...}, ...]}`).
- Produces: `tests/fixtures/order_positions_full.json` со структурой `{"real_count": int, "synthetic_count": int, "blocked": [str], "positions": [position, ...]}`; position — ровно 9 ключей `article, qty, thickness, material, params, comment, shina, conn0, conn1` (контракт `json_positions.build_position`).

Ожидаемые числа (на момент плана): валидных артикулов в каталоге 199 (209 записей с Артикулом минус 10 мусорных `----`), минус заблокированный `20-2` → `MIN_ARTICLES = 198` в тесте.

Примечание: генератор гоняет реальный PDF-пайплайн по 10 PDF в `examples/` — нужно то же окружение, что для локального прогона пайплайна (зависимости `.venv` уже покрывают импорты `api`/`pdf_spec_extractor`/`process_specification_table`). Выполняется один раз, при коммитте фикстуры.

- [ ] **Step 1: Написать падающий тест**

Создать `tests/test_order_positions_fixture.py`:

```python
"""Контракт корпуса позиций полного покрытия (фикстура для приёмки HTTP-сервиса 1С).

Фикстура собирается один раз:
    .venv/bin/python tools/build_order_positions_fixture.py
"""
import json
from pathlib import Path

FIXTURE = Path(__file__).parent / "fixtures" / "order_positions_full.json"

CONTRACT_KEYS = {
    "article", "qty", "thickness", "material", "params", "comment",
    "shina", "conn0", "conn1",
}
MIN_ARTICLES = 198  # 199 валидных артикулов каталога минус заблокированный 20-2


def test_fixture_exists_and_covers_catalog():
    assert FIXTURE.exists(), (
        "фикстура не собрана: "
        ".venv/bin/python tools/build_order_positions_fixture.py"
    )
    corpus = json.loads(FIXTURE.read_text(encoding="utf-8"))
    positions = corpus["positions"]
    articles = {p["article"] for p in positions}
    assert len(articles) >= MIN_ARTICLES, f"покрыто артикулов: {len(articles)}"
    assert "20-2" not in articles  # дефект ПередЗаписью в 1С, см. BACKLOG
    assert "----" not in articles
    assert corpus["synthetic_count"] > 0
    assert corpus["real_count"] > 0


def test_fixture_positions_match_contract():
    corpus = json.loads(FIXTURE.read_text(encoding="utf-8"))
    for pos in corpus["positions"]:
        assert set(pos.keys()) == CONTRACT_KEYS, pos["article"]
        assert isinstance(pos["params"], dict) and pos["params"], pos["article"]
        for conn_key in ("shina", "conn0", "conn1"):
            assert conn_key in pos, pos["article"]
    # сериализация в тело запроса сервиса — без потерь (utf-8 round-trip)
    body = {
        "order_comment": "c",
        "request_id": None,
        "positions": corpus["positions"],
    }
    assert json.loads(json.dumps(body, ensure_ascii=False))["positions"] == corpus["positions"]
```

- [ ] **Step 2: Запустить тест, убедиться что падает**

Run: `.venv/bin/python -m pytest tests/test_order_positions_fixture.py -v`
Expected: FAIL — фикстура не существует

- [ ] **Step 3: Генератор + сборка фикстуры**

Создать `tools/build_order_positions_fixture.py`:

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Сборка корпуса позиций полного покрытия для приёмки HTTP-сервиса 1С.

Один раз: прогоняет PDF-корпуса пайплайна (examples/) и дополняет
синтетическими позициями артикулы, которых в корпусах нет. Результат —
tests/fixtures/order_positions_full.json (коммитится; тесты только читают).

Источник артикулов: reference/1c_products_all.json (снимок справочника
асПродукция). Мусорные артикулы «----» и заблокированные продукты
(bitrix_bot.pipeline.BLOCKED_1C_ARTICLES, сейчас 20-2) исключаются.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bitrix_bot.pipeline import BLOCKED_1C_ARTICLES, process_pdf_to_positions
from json_positions import build_positions

FIXTURE = ROOT / "tests" / "fixtures" / "order_positions_full.json"
PRODUCTS_REF = ROOT / "reference" / "1c_products_all.json"
EXAMPLES = ROOT / "examples"

SYNTH_MATERIAL = "Рулон оц.(08ПС)0.80"


def real_positions() -> list[dict]:
    """Позиции из всех PDF корпусов, прогнанных через боевой пайплайн."""
    positions = []
    for pdf in sorted(EXAMPLES.rglob("*.pdf")):
        print(f"pipeline: {pdf.relative_to(ROOT)}")
        success, _skipped = process_pdf_to_positions(pdf.read_bytes())
        positions.extend(build_positions(success))
    return positions


def catalog_articles() -> list[str]:
    data = json.loads(PRODUCTS_REF.read_text(encoding="utf-8"))["data"]
    arts = []
    for row in data:
        art = (row.get("Артикул") or "").strip()
        if not art or art == "----" or row.get("ЭтоГруппа"):
            continue
        arts.append(art)
    return sorted(set(arts))


def synthetic_position(article: str) -> dict:
    """Минимально валидная позиция для артикула, не встретившегося в корпусах.

    Сечение по средней цифре артикула: «1» — круглое (D0), иначе прямоугольное
    (A0×B0); длина L0=1000. Шина — по правилам техотдела от макс. размера
    сечения (json_positions.shina_by_max_dim): 200 мм → 65, 400 мм → 95.
    """
    parts = article.split("-")
    is_round = len(parts) > 1 and parts[1] == "1"
    if is_round:
        params = {"D0": 200, "L0": 1000}
        shina = "65"
    else:
        params = {"A0": 400, "B0": 200, "L0": 1000}
        shina = "95"
    return {
        "article": article,
        "qty": 1,
        "thickness": 0.8,
        "material": SYNTH_MATERIAL,
        "params": params,
        "comment": "synthetic coverage",
        "shina": shina,
        "conn0": "6",
        "conn1": "6",
    }


def build_fixture() -> dict:
    positions = real_positions()
    covered = {p["article"] for p in positions}
    blocked = set(BLOCKED_1C_ARTICLES)
    synth = [
        synthetic_position(a)
        for a in catalog_articles()
        if a not in covered and a not in blocked
    ]
    return {
        "real_count": len(positions),
        "synthetic_count": len(synth),
        "blocked": sorted(blocked),
        "positions": sorted(positions + synth, key=lambda p: p["article"]),
    }


def main() -> None:
    corpus = build_fixture()
    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE.write_text(
        json.dumps(corpus, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    articles = {p["article"] for p in corpus["positions"]}
    print(
        f"real={corpus['real_count']} synth={corpus['synthetic_count']} "
        f"total={len(corpus['positions'])} unique_articles={len(articles)} "
        f"blocked={corpus['blocked']}"
    )


if __name__ == "__main__":
    main()
```

Запустить сборку:

Run: `.venv/bin/python tools/build_order_positions_fixture.py`
Expected: завершение без traceback; в выводе `unique_articles` ≥ 198 и строка `blocked=['20-2']`; файл `tests/fixtures/order_positions_full.json` создан.

- [ ] **Step 4: Запустить тест, убедиться что проходит**

Run: `.venv/bin/python -m pytest tests/test_order_positions_fixture.py -v`
Expected: PASS (2 теста)

- [ ] **Step 5: Полный прогон + commit**

Run: `.venv/bin/python -m pytest tests/ -x -q`
Expected: PASS

```bash
git add tools/build_order_positions_fixture.py tests/fixtures/order_positions_full.json tests/test_order_positions_fixture.py
git commit -m "test(order_client): full article coverage fixture (corpus + synthetic)"
```

---

## Самопроверка плана (против спеки)

- **Контракт модуля** (спека «Контракт order_client.py»): Tasks 1–3 — `load_order`, `transport_for`, оба транспорта, тело запроса, заголовки, charset=utf-8, request_id всегда в теле. Покрыто.
- **Обработка ошибок** (таблица спеки): Task 3 — бизнес-ошибки значением; 5xx без структуры → `httpx.HTTPError`; 5xx со структурой ТЗ → значение; 4xx → `RuntimeError`; битый JSON → `RuntimeError`. Покрыто тестами.
- **Тестирование** (спека): все перечисленные кейсы присутствуют (Tasks 1–3), паритет транспортов — косвенно через одинаковые ассерты `order_number/errors/warnings` на обоих.
- **Полное покрытие артикулов** (спека): Task 4 — корпуса + синтетика на каждый отсутствующий артикул, фикстура коммитится, тесты только читают. Покрыто.
- **Не входит (по спеке):** живая приёмка на копии базы (ручной чек-лист спеки, после 1С-разработчика); интеграция в бота/web_app; конфиг-переключатель — в план не включены осознанно.
