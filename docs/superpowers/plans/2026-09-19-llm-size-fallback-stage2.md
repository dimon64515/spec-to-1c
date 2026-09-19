# Stage 2: LLM Fallback Size Classification — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** LLM-фолбэк классификации форматов записи размеров для строк, которые regex-каскад не взял: модуль `llm_size_classifier.py` (бэкенд — Kimi Code CLI через subprocess, batched), интеграция в `parse_row`/`process_rows`, skipped-отчёт с причиной «LLM-классификация отклонена».

**Architecture:** `process_rows` делает предпроход: собирает уникальные size-строки, которые `parse_size` не взял, и одним/несколькими batch-вызовами классифицирует их через `kimi -p --output-format stream-json`. Результаты (кэш size→classification) передаются в `parse_row`, который (а) усыновляет dims, только если класс применим к воздуховоду (`round_diameter`/`rect_axb`) и dims прошли валидацию, (б) в финальном skip ставит причину «LLM-классификация отклонена» + детали. Цифры извлекаются ТОЛЬКО из исходной строки по spans.

**Tech Stack:** Python 3.12 stdlib (subprocess/json/re), pyyaml, pytest. БЕЗ новых pip-зависимостей. БЕЗ openai/httpx.

Spec: `docs/superpowers/specs/2026-09-10-size-notation-config-design.md` (этап 2). Stage 1 merged basis: `size_notations.validate_dimensions`, конфиг-инфраструктура, coverage-корпус.

## Global Constraints

- Запуск тестов: `.venv/bin/python -m pytest tests/ -q` из корня репозитория.
- НЕ добавлять pip-зависимости.
- **ЖЁСТКОЕ ОГРАНИЧЕНИЕ:** LLM никогда не генерирует/переписывает цифры размеров. Цифры — только `int(source[start:end])` из ИСХОДНОЙ строки, подстрока обязана состоять из цифр. LLM выдаёт только format_class + spans.
- Поведение без LLM-конфига (`llm.enabled: false` или секция отсутствует) идентично текущему: ни одного вызова subprocess, те же skipped-причины. Существующие тесты не меняются.
- Тесты НИКОГДА не вызывают subprocess/kimi и не ходят в сеть (runner инжектируется/монкейпатчится).
- Пайплайн не падает при недоступности LLM: любой сбой → status=rejected, строка уходит в skipped.
- Логирование: исходная строка, сырой ответ LLM, результат валидации → logger (WARNING для reject) + поля в skipped-словаре.
- Каждый таск заканчивается зелёным `pytest tests/ -q` и коммитом (только свои файлы; dirty `requirements.txt` не трогать).

## Ключевые факты кодовой базы

- `parse_row(row, defaults)` (process_specification_table.py:1105): порядок веток — покупные пропуски (1161-1189) → quantity (1191-1207) → explicit article (1211-1234) → КСД (1245-1249) → **`try_parse_fitting` (1252-1255)** → aggregate/м² (1258) → equipment ptype (1267) → non-duct ptype None (1276) → **duct-путь `parse_size` (1283)** → финальный fallthrough skip (1349-1352) с `classify_skip` → причина «Не удалось распознать размер / тип».
- Fittings-путь ИДЁТ ДО duct-пути → LLM-усыновление в duct-пути не перехватывает фасонку. LLM НЕ усыновливает результаты для fittings/equipment.
- `process_rows` (1355): цикл `parse_row(row, defaults)` в 1386-1407; XML-валидация success_rows (1412-1436).
- `size_notations.validate_dimensions(dims, section)` — guard-валидация (min 100 / max 3000 сторон, L0 ≤ 12000, U0 ≤ 180). Параметр `section` сейчас не используется — в этом плане НЕ чиним (defer этапа 3).
- `config.py`: `get_config()` кэширован; `config.yaml` — плоский YAML, добавляем секцию `llm:`.
- Kimi CLI (проверено 2026-09-10): `kimi -p "<prompt>" --output-format stream-json` → stdout построчный JSON; ответ — строка с `"role":"assistant"`, поле `content`. Модель опционально: `-m <alias>`.
- Skip-словари: поля name/size/unit/quantity/material/thickness/reason (+ocr_warning). Дополнительные поля добавляем аддитивно.

## Файловая структура

- Create: `llm_size_classifier.py` — конфиг-доступ, prompt-builder, subprocess-runner (retries/timeout), парсер strict-JSON, spans→dims, валидация, batch-API.
- Modify: `config.yaml` — секция `llm:` (enabled: false по умолчанию).
- Modify: `process_specification_table.py` — `parse_row(..., llm_cache=None)`, предпроход в `process_rows`, причина «LLM-классификация отклонена».
- Test: `tests/test_llm_size_classifier.py`, `tests/test_llm_fallback_integration.py`.

## Публичный API (контракт между задачами)

```python
@dataclass
class SizeClassification:
    status: str            # "ok" | "rejected"
    format_class: str      # из enum или "unknown"
    dims: dict             # извлечённые из исходной строки размеры ({} если нет)
    section: str | None    # "round" | "rectangular" | None
    adoptable: bool        # True только для round_diameter/rect_axb со status ok
    raw_response: str      # сырой ответ LLM (для лога/отчёта)
    detail: str            # причина rejection / примечание

REASON_LLM_REJECTED = "LLM-классификация отклонена"

def get_llm_config() -> dict
def llm_enabled(cfg: dict | None = None) -> bool   # enabled + backend kimi-cli + shutil.which(command)
def classify_sizes_batch(strings: list[str], cfg: dict | None = None, runner=None) -> dict[str, SizeClassification]
```

---

### Task 1: Конфиг LLM + предусловия доступности

**Files:**
- Modify: `config.yaml`
- Create: `tests/test_llm_size_classifier.py` (только конфиг-тесты в этой задаче)

**Interfaces:**
- Produces: `get_llm_config()`, `llm_enabled(cfg)` — контракт выше. Константа `REASON_LLM_REJECTED`.

- [ ] **Step 1: Написать failing-тесты** (в новом `tests/test_llm_size_classifier.py`)

```python
"""LLM-фолбэк классификации размеров. Все тесты без сети/subprocess."""
import pytest

import llm_size_classifier as lsc


def test_get_llm_config_defaults(monkeypatch):
    cfg = lsc.get_llm_config()
    assert cfg["enabled"] is False
    assert cfg["backend"] == "kimi-cli"
    assert cfg["batch_size"] >= 1
    assert cfg["timeout"] > 0
    assert cfg["max_retries"] >= 0


def test_llm_enabled_requires_flag():
    assert lsc.llm_enabled({"enabled": False, "backend": "kimi-cli", "command": "kimi"}) is False
    assert lsc.llm_enabled({"enabled": True, "backend": "other", "command": "kimi"}) is False
    assert lsc.llm_enabled({"enabled": True, "backend": "kimi-cli", "command": ""}) is False


def test_llm_enabled_requires_command_on_path(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda cmd: None)
    assert lsc.llm_enabled({"enabled": True, "backend": "kimi-cli", "command": "kimi"}) is False
    monkeypatch.setattr("shutil.which", lambda cmd: "/usr/bin/kimi")
    assert lsc.llm_enabled({"enabled": True, "backend": "kimi-cli", "command": "kimi"}) is True


def test_reason_constant():
    assert lsc.REASON_LLM_REJECTED == "LLM-классификация отклонена"
```

- [ ] **Step 2: Прогнать — упадёт импортом**

Run: `.venv/bin/python -m pytest tests/test_llm_size_classifier.py -q`
Expected: FAIL `ModuleNotFoundError`

- [ ] **Step 3: Секция в `config.yaml`** (добавить в конец файла)

```yaml
# LLM-фолбэк классификации форматов записи размеров (только для строк,
# которые regex-каскад не распознал). Цифры размеров LLM не генерирует —
# извлекаются детерминированно по spans из исходной строки.
# Бэкенд kimi-cli использует локальную аутентификацию Kimi Code CLI.
llm:
  enabled: false
  backend: "kimi-cli"
  command: "kimi"
  model: ""           # опциональный -m alias, "" = модель по умолчанию
  batch_size: 20
  timeout: 120
  max_retries: 2
```

- [ ] **Step 4: Реализовать модуль-скелет** (`llm_size_classifier.py`)

```python
"""LLM-фолбэк классификации форматов записи размеров.

Жёсткое ограничение: LLM никогда не генерирует и не переписывает цифры
размеров. Модель возвращает только format_class и spans (смещения подстрок
в ИСХОДНОЙ строке); цифры извлекаются детерминированно int(source[start:end]).
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from config import get_config

REASON_LLM_REJECTED = "LLM-классификация отклонена"

FORMAT_CLASSES = [
    "round_diameter",  # один диаметр: «Ø315», «315ø», «Ду315»
    "rect_axb",        # прямоугольное сечение: «1250x800»
    "tee_axbxc",       # тройник: «315/315/160»
    "reducer_pair",    # переход: «250/160»
    "silencer_code",   # кодовая форма: «LITENED 50-25»
    "unknown",
]

# Классы, применимые к воздуховодам (duct-путь parse_row). Остальные
# парсятся для лога/отчёта, но не усыновливаются как воздуховод.
ADOPTABLE_CLASSES = ("round_diameter", "rect_axb")

logger = __import__("logging").getLogger(__name__)


@dataclass
class SizeClassification:
    status: str                      # "ok" | "rejected"
    format_class: str = "unknown"
    dims: Dict[str, float] = field(default_factory=dict)
    section: Optional[str] = None    # "round" | "rectangular" | None
    adoptable: bool = False
    raw_response: str = ""
    detail: str = ""


def get_llm_config() -> Dict:
    return dict(get_config().get("llm") or {})


def llm_enabled(cfg: Optional[Dict] = None) -> bool:
    cfg = get_llm_config() if cfg is None else cfg
    if not cfg.get("enabled"):
        return False
    if cfg.get("backend", "kimi-cli") != "kimi-cli":
        return False
    command = str(cfg.get("command") or "").strip()
    return bool(command) and shutil.which(command) is not None


def classify_sizes_batch(strings, cfg=None, runner=None):
    raise NotImplementedError  # Task 2
```

- [ ] **Step 5: Прогнать**

Run: `.venv/bin/python -m pytest tests/test_llm_size_classifier.py -q`
Expected: PASS (4 тестов; NotImplementedError тестами не трогается)

- [ ] **Step 6: Полный прогон + коммит**

```bash
.venv/bin/python -m pytest tests/ -q
git add config.yaml llm_size_classifier.py tests/test_llm_size_classifier.py
git commit -m "feat: llm config section + classifier skeleton"
```

---

### Task 2: Классификатор — runner, парсер, spans→dims, валидация

**Files:**
- Modify: `llm_size_classifier.py`
- Modify: `tests/test_llm_size_classifier.py`

**Interfaces:**
- Consumes: `size_notations.validate_dimensions`.
- Produces: рабочий `classify_sizes_batch(strings, cfg, runner)`; внутренние `_build_prompt`, `_run_kimi`, `_parse_batch_response` (тесты опираются на них).

- [ ] **Step 1: Дописать failing-тесты** (добавить в `tests/test_llm_size_classifier.py`)

```python
import json

import size_notations as szn


def _ok_runner(payload_map=None, fail_times=0):
    """Фейк-runner: принимает prompt, возвращает «stdout stream-json».
    payload_map: {подстрока_промпта: ответный контент}. fail_times: сколько
    первых вызовов бросить TimeoutError (проверка retries)."""
    state = {"calls": 0, "fails_left": fail_times}

    def runner(prompt, cfg):
        state["calls"] += 1
        if state["fails_left"] > 0:
            state["fails_left"] -= 1
            raise TimeoutError("kimi timeout")
        for needle, content in (payload_map or {}).items():
            if needle in prompt:
                return json.dumps(
                    {"role": "meta", "type": "system.version", "version": "0.41.0"}
                ) + "\n" + json.dumps({"role": "assistant", "content": content}, ensure_ascii=False)
        raise AssertionError("prompt не распознан: " + prompt[:200])

    return runner, state


CFG = {"enabled": True, "backend": "kimi-cli", "command": "kimi",
       "model": "", "batch_size": 2, "timeout": 30, "max_retries": 1}


def test_round_diameter_extracted_from_source_spans():
    content = json.dumps({"results": [
        {"format_class": "round_diameter",
         "spans": [{"role": "diameter", "start": 1, "end": 4}]}
    ]}, ensure_ascii=False)
    runner, state = _ok_runner({"Ø315": content})
    out = lsc.classify_sizes_batch(["Ø315"], cfg=CFG, runner=runner)
    r = out["Ø315"]
    assert r.status == "ok" and r.format_class == "round_diameter"
    assert r.dims == {"D0": 315.0} and r.section == "round" and r.adoptable


def test_rect_axb_with_length():
    content = json.dumps({"results": [
        {"format_class": "rect_axb",
         "spans": [{"role": "a", "start": 0, "end": 4},
                   {"role": "b", "start": 5, "end": 8},
                   {"role": "length", "start": 9, "end": 13}]}
    ]})
    runner, _ = _ok_runner({"1250x800-3000": content})
    r = lsc.classify_sizes_batch(["1250x800-3000"], cfg=CFG, runner=runner)["1250x800-3000"]
    assert r.dims == {"A0": 1250.0, "B0": 800.0, "L0": 3000.0}
    assert r.section == "rectangular" and r.adoptable


def test_non_digit_span_rejected():
    content = json.dumps({"results": [
        {"format_class": "round_diameter",
         "spans": [{"role": "diameter", "start": 0, "end": 2}]}
    ]})
    runner, _ = _ok_runner({"Ø315": content})
    r = lsc.classify_sizes_batch(["Ø315"], cfg=CFG, runner=runner)["Ø315"]
    assert r.status == "rejected" and not r.dims


def test_out_of_range_rejected_by_domain_validation():
    content = json.dumps({"results": [
        {"format_class": "round_diameter",
         "spans": [{"role": "diameter", "start": 1, "end": 3}]}
    ]})
    runner, _ = _ok_runner({"Ø25": content})
    r = lsc.classify_sizes_batch(["Ø25"], cfg=CFG, runner=runner)["Ø25"]
    assert r.status == "rejected"
    assert "валидац" in r.detail.lower() or "диапазон" in r.detail.lower()


def test_tee_ok_but_not_adoptable():
    # "315/315-160": 0-2 "315", 4-6 "315", 8-10 "160" — три числа тройника.
    content = json.dumps({"results": [
        {"format_class": "tee_axbxc",
         "spans": [{"role": "a", "start": 0, "end": 3},
                   {"role": "b", "start": 4, "end": 7},
                   {"role": "c", "start": 8, "end": 11}]}
    ]})
    runner, _ = _ok_runner({"315/315-160": content})
    r = lsc.classify_sizes_batch(["315/315-160"], cfg=CFG, runner=runner)["315/315-160"]
    assert r.status == "ok"                      # распознан и провалидирован
    assert r.dims == {"D0": 315.0, "D1": 315.0, "D2": 160.0}
    assert r.adoptable is False                  # в воздуховоды не усыновляется
    assert "не усыновляется" in r.detail


def test_batching_respects_batch_size():
    import json as _json
    import re as _re

    def runner(prompt, cfg):
        # Строки в промпте нумерованы: «N: "строка"». Для каждой свой результат
        # со span на её ведущие цифры — так dims зависят от реального входа.
        strings = []
        for line in prompt.splitlines():
            m = _re.match(r"^\d+: (\".*\")$", line)
            if m:
                strings.append(_json.loads(m.group(1)))
        results = []
        for s in strings:
            m = _re.match(r"\d+", s)
            results.append({"format_class": "round_diameter",
                            "spans": [{"role": "diameter", "start": 0, "end": m.end()}]})
        return _json.dumps({"role": "assistant", "content": _json.dumps({"results": results})})

    out = lsc.classify_sizes_batch(["315a", "400a", "500a"], cfg=CFG, runner=runner)
    assert len(out) == 3
    assert all(r.status == "ok" and r.adoptable for r in out.values())
    assert {r.dims["D0"] for r in out.values()} == {315.0, 400.0, 500.0}
```

(Проверка батчинга косвенная: runner возвращает ровно столько результатов, сколько строк в промпте; если бы `classify_sizes_batch` не разбил 3 строки на 2+1 батча при batch_size=2, zip(batch, results) дал бы неверные dims и финальный assert не сошёлся бы. Для прямой проверки числа вызовов runner может вести счётчик наружу — опционально.)

```python
def test_runner_failure_rejects_all_without_raising():
    def runner(prompt, cfg):
        raise FileNotFoundError("kimi")

    out = lsc.classify_sizes_batch(["Ø315", "Ø400"], cfg=CFG, runner=runner)
    assert all(r.status == "rejected" for r in out.values())
    assert any("недоступн" in r.detail.lower() or "kimi" in r.detail.lower() for r in out.values())


def test_retries_then_success():
    payload = json.dumps({"results": [
        {"format_class": "round_diameter", "spans": [{"role": "diameter", "start": 1, "end": 4}]}
    ]})
    runner, state = _ok_runner({"Ø315": payload}, fail_times=1)
    r = lsc.classify_sizes_batch(["Ø315"], cfg=CFG, runner=runner)["Ø315"]
    assert r.status == "ok" and state["calls"] == 2


def test_malformed_json_rejected():
    runner, _ = _ok_runner({"Ø315": "не json вообще"})
    r = lsc.classify_sizes_batch(["Ø315"], cfg=CFG, runner=runner)["Ø315"]
    assert r.status == "rejected"
```

- [ ] **Step 2: Прогнать — новые падают (NotImplementedError)**

Run: `.venv/bin/python -m pytest tests/test_llm_size_classifier.py -q`
Expected: старые 4 PASS, новые FAIL

- [ ] **Step 3: Реализовать `classify_sizes_batch` и внутренности**

Заменить заглушку в `llm_size_classifier.py` (оставить импорты сверху; добавить `import json, logging, re, subprocess`):

```python
logger = logging.getLogger(__name__)

_ROLE_KEYS = {"diameter": "D0", "a": "A0", "b": "B0", "length": "L0"}


def _build_prompt(strings: List[str]) -> str:
    items = "\n".join(f'{i}: {json.dumps(s, ensure_ascii=False)}' for i, s in enumerate(strings))
    classes = ", ".join(FORMAT_CLASSES)
    return (
        "Ты классификатор форматов записи размеров воздуховодов.\n"
        f"Даны строки (номер: JSON-строка):\n{items}\n\n"
        "Для КАЖДОЙ строки верни строго один JSON-объект без пояснений, markdown и кода:\n"
        '{"results":[{"format_class":"<класс>","spans":[{"role":"...","start":N,"end":N}]}...]}\n'
        f"format_class — одно из: {classes}.\n"
        "spans — смещения подстрок В ИСХОДНОЙ строке (символы, start включительно, end исключительно), "
        "которые являются ЧИСЛАМИ размеров. role — одно из: diameter, a, b, c, length.\n"
        "Правила: round_diameter — role diameter (обязателен), опционально length; "
        "rect_axb — a и b (обязательны), опционально length; "
        "tee_axbxc — a, b, c (три числа, тройник); reducer_pair — два числа (role diameter или a/b); "
        "silencer_code — span на код (role a); unknown — spans пустой.\n"
        "НИКОГДА не выводи сами числа и не переписывай строку — только класс и смещения. "
        "Несколько диаметров round_diameter: первый — diameter, остальные тоже diameter.\n"
        'Пример для строки "Ø315": {"results":[{"format_class":"round_diameter",'
        '"spans":[{"role":"diameter","start":1,"end":4}]}]}'
    )


def _run_kimi(prompt: str, cfg: Dict) -> str:
    """Один вызов kimi -p; возвращает сырой stdout. Бросает исключения при сбое."""
    cmd = [str(cfg.get("command") or "kimi"), "-p", prompt, "--output-format", "stream-json"]
    model = str(cfg.get("model") or "").strip()
    if model:
        cmd += ["-m", model]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=int(cfg.get("timeout", 120)))
    if proc.returncode != 0:
        raise RuntimeError(f"kimi exit {proc.returncode}: {proc.stderr[:300]}")
    return proc.stdout


def _assistant_content(stdout: str) -> str:
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if rec.get("role") == "assistant" and isinstance(rec.get("content"), str):
            return rec["content"]
    raise ValueError("assistant-ответ не найден в stream-json")


def _extract_json_object(text: str) -> Dict:
    start = text.find("{")
    if start < 0:
        raise ValueError("JSON не найден в ответе")
    obj, _ = json.JSONDecoder().raw_decode(text[start:])
    if not isinstance(obj, dict):
        raise ValueError("ответ — не объект")
    return obj


def _spans_to_dims(format_class: str, spans: List[Dict], source: str) -> Optional[Dict[str, float]]:
    """Детерминированное извлечение цифр ИСХОДНОЙ строки по spans. None = отказ."""
    values: Dict[str, float] = {}
    ordered: List[float] = []
    for sp in spans:
        role = str(sp.get("role", ""))
        try:
            start, end = int(sp["start"]), int(sp["end"])
        except (KeyError, TypeError, ValueError):
            return None
        if not (0 <= start < end <= len(source)):
            return None
        token = source[start:end].strip()
        if not re.fullmatch(r"\d+", token):
            return None  # LLM прислала не цифры — отказ
        value = float(int(token))
        ordered.append(value)
        if role in _ROLE_KEYS and _ROLE_KEYS[role] not in values:
            values[_ROLE_KEYS[role]] = value
        elif role == "c":
            values.setdefault("D2", value)
    if format_class == "round_diameter":
        if "D0" not in values:
            return None
    elif format_class == "rect_axb":
        if "A0" not in values or "B0" not in values:
            return None
    elif format_class == "tee_axbxc":
        if len(ordered) < 3:
            return None
        return {"D0": ordered[0], "D1": ordered[1], "D2": ordered[2]}
    elif format_class == "reducer_pair":
        if len(ordered) < 2:
            return None
        return {"D0": ordered[0], "D1": ordered[1]}
    elif format_class == "silencer_code":
        return {}  # кодовые таблицы — забота каскада/этапа 3
    return values or None


def _classify_one(source: str, result: Dict, raw_response: str) -> "SizeClassification":
    """status='ok' — spans дали валидные числа И валидация диапазонов пройдена.
    adoptable — дополнительно: класс применим к воздуховодам (duct-путь)."""
    fmt = str(result.get("format_class") or "unknown")
    if fmt not in FORMAT_CLASSES:
        fmt = "unknown"
    spans = result.get("spans") or []
    if not isinstance(spans, list):
        spans = []
    dims = _spans_to_dims(fmt, spans, source)
    if dims is None:
        return SizeClassification(
            status="rejected", format_class=fmt, raw_response=raw_response,
            detail="spans не дали валидных чисел из исходной строки",
        )
    section = None
    if fmt == "round_diameter":
        section = "round"
    elif fmt == "rect_axb":
        section = "rectangular"
    if not size_notations.validate_dimensions(dims, section):
        return SizeClassification(
            status="rejected", format_class=fmt, dims=dims, section=section,
            raw_response=raw_response, detail="валидация диапазонов не пройдена",
        )
    adoptable = fmt in ADOPTABLE_CLASSES
    detail = "" if adoptable else f"класс {fmt} не усыновляется в воздуховоды"
    return SizeClassification(
        status="ok", format_class=fmt, dims=dims, section=section,
        adoptable=adoptable, raw_response=raw_response, detail=detail,
    )


def classify_sizes_batch(strings, cfg=None, runner=None):
    """Классифицирует строки батчами. runner(prompt, cfg)->stdout инжектируется в тестах.
    Любой сбой → status=rejected (пайплайн не падает)."""
    cfg = get_llm_config() if cfg is None else cfg
    run = _run_kimi if runner is None else runner
    batch_size = max(1, int(cfg.get("batch_size", 20)))
    out: Dict[str, SizeClassification] = {}

    def reject_batch(batch, detail):
        for s in batch:
            logger.warning("LLM-классификация отклонена: %r — %s", s, detail)
            out[s] = SizeClassification(status="rejected", detail=detail)

    for i in range(0, len(strings), batch_size):
        batch = [s for s in strings[i:i + batch_size] if s and s not in out]
        if not batch:
            continue
        prompt = _build_prompt(batch)
        raw = ""
        last_exc: Optional[Exception] = None
        for attempt in range(int(cfg.get("max_retries", 2)) + 1):
            try:
                raw = run(prompt, cfg)
                break
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                logger.warning("kimi вызов неудачен (попытка %d): %s", attempt + 1, exc)
        else:
            reject_batch(batch, f"LLM недоступна: {last_exc}")
            continue
        try:
            content = _assistant_content(raw)
            obj = _extract_json_object(content)
            results = obj.get("results")
            if not isinstance(results, list) or len(results) < len(batch):
                raise ValueError(f"ожидалось {len(batch)} результатов, получено {len(results) if isinstance(results, list) else 0}")
        except ValueError as exc:
            reject_batch(batch, f"ответ LLM не распознан: {exc}")
            continue
        for source, result in zip(batch, results):
            if not isinstance(result, dict):
                result = {}
            cls = _classify_one(source, result, raw)
            logger.info(
                "LLM-классификация: %r → %s (%s) %s",
                source, cls.format_class, cls.status, cls.detail,
            )
            out[source] = cls
    return out
```

(Добавить `import size_notations` в импорты; убрать старый `logger = __import__(...)`-хак.)

- [ ] **Step 4: Прогнать**

Run: `.venv/bin/python -m pytest tests/test_llm_size_classifier.py -q`
Expected: все PASS. Если `test_batching_respects_batch_size` падает на «prompt не распознан» — фейк-runner там не использует payload_map (возвращает payload всегда) — проверь, что пробник из шага 1 действительно игнорирует распознавание.

- [ ] **Step 5: Полный прогон + коммит**

```bash
.venv/bin/python -m pytest tests/ -q
git add llm_size_classifier.py tests/test_llm_size_classifier.py
git commit -m "feat: llm size classifier with span-based deterministic extraction"
```

---

### Task 3: Интеграция в parse_row/process_rows

**Files:**
- Modify: `process_specification_table.py`
- Create: `tests/test_llm_fallback_integration.py`

**Interfaces:**
- Consumes: `llm_size_classifier` API из Task 1-2; `parse_row` контракт.
- Produces: `parse_row(row, defaults, llm_cache=None)`; `process_rows` предпроход; skip-причина `REASON_LLM_REJECTED` + поля `llm_format_class`/`llm_detail` в skipped-словаре.

- [ ] **Step 1: Написать failing-тесты** (`tests/test_llm_fallback_integration.py`)

```python
"""Интеграция LLM-фолбэка в parse_row/process_rows. Subprocess/сеть запрещены."""
import pytest

import llm_size_classifier as lsc
import process_specification_table as pst


def _cls(**kw):
    base = dict(status="ok", format_class="round_diameter", dims={"D0": 315.0},
                section="round", adoptable=True, raw_response="{}", detail="")
    base.update(kw)
    return lsc.SizeClassification(**base)


DEFAULTS = {"material": "оцинкованная", "thickness": "0.5"}


def test_parse_row_without_cache_identical_behavior():
    row = {"name": "Воздуховод спирально-навивной", "size": "315/315/160",
           "unit": "шт", "quantity": "2"}
    p1, s1 = pst.parse_row(row, DEFAULTS)
    p2, s2 = pst.parse_row(row, DEFAULTS, llm_cache={})
    assert (p1, s1) == (p2, s2)
    assert p1 is None and s1 is not None


def test_parse_row_adopts_llm_dims():
    # Имя — обычный воздуховод: проходит мимо fittings/equipment-веток,
    # size каскад не берёт (slash-форма), LLM-кэш даёт round_diameter.
    row = {"name": "Воздуховод спирально-навивной", "size": "315/315-160",
           "unit": "шт", "quantity": "2"}
    cache = {"315/315-160": _cls()}
    parsed, skip = pst.parse_row(row, DEFAULTS, llm_cache=cache)
    assert skip is None
    assert parsed["article"] == "1-1-2"
    assert parsed["params"]["D0"] == 315.0


def test_parse_row_llm_rejected_gets_reason():
    row = {"name": "Воздуховод спирально-навивной", "size": "??",
           "unit": "шт", "quantity": "2"}
    cache = {"??": _cls(status="rejected", adoptable=False,
                        format_class="unknown", detail="spans пусты")}
    parsed, skip = pst.parse_row(row, DEFAULTS, llm_cache=cache)
    assert parsed is None
    assert skip["reason"] == lsc.REASON_LLM_REJECTED
    assert skip["llm_format_class"] == "unknown"
    assert skip["llm_detail"]


def test_fittings_path_not_preempted_by_llm():
    # Фасонку fittings-путь берёт САМ (до duct-пути): даже adoptable-запись
    # в кэше не должна превратить отвод в воздуховод.
    row = {"name": "Отвод 90 град Ф160-Ф160", "size": "Ф160-Ф160",
           "unit": "шт", "quantity": "3"}
    cache = {"Ф160-Ф160": _cls(format_class="round_diameter", dims={"D0": 160.0})}
    parsed, skip = pst.parse_row(row, DEFAULTS, llm_cache=cache)
    assert parsed is not None and parsed["article"].startswith("2-"), parsed


def test_process_rows_builds_cache_and_calls_classifier(monkeypatch):
    import re as _re
    calls = []

    def fake_batch(strings, cfg=None, runner=None):
        calls.append(list(strings))
        out = {}
        for s in strings:
            d0 = float(_re.match(r"\d+", s).group())
            out[s] = _cls(dims={"D0": d0})
        return out

    monkeypatch.setattr(lsc, "llm_enabled", lambda cfg=None: True)
    monkeypatch.setattr(lsc, "classify_sizes_batch", fake_batch)
    monkeypatch.setattr(pst, "ALLOWED_ARTICLES", set())

    rows = [
        {"name": "Воздуховод спирально-навивной", "size": "315/315-160",
         "unit": "шт", "quantity": "2"},
        {"name": "Воздуховод спирально-навивной", "size": "400/400-200",
         "unit": "шт", "quantity": "1"},
    ]
    xml, skipped, success = pst.process_rows(rows, defaults=DEFAULTS)
    assert len(success) == 2 and not skipped
    assert calls and len(calls[0]) == 2  # обе нераспознанные строки ушли в батч
    assert {r["params"]["D0"] for r in success} == {315.0, 400.0}


def test_process_rows_llm_disabled_no_classifier_call(monkeypatch):
    def boom(*a, **kw):
        raise AssertionError("classify_sizes_batch не должен вызываться")

    monkeypatch.setattr(lsc, "llm_enabled", lambda cfg=None: False)
    monkeypatch.setattr(lsc, "classify_sizes_batch", boom)
    rows = [{"name": "Воздуховод", "size": "315/315-160",
             "unit": "шт", "quantity": "2"}]
    xml, skipped, success = pst.process_rows(rows, defaults=DEFAULTS)
    assert not success and skipped
    assert skipped[0]["reason"] != lsc.REASON_LLM_REJECTED
```

(Если `test_parse_row_adopts_llm_dims` или `test_fittings_path_not_preempted_by_llm` падают из-за конкретных веток parse_row (например, fittings не берёт «Отвод 90 град Ф160-Ф160», или наоборот перехватывает воздуховод) — подбери строки, реально доходящие до целевой ветки, зафиксировав выбор в отчёте; логика тестов (усыновление / не-премption) не меняется.)

- [ ] **Step 2: Прогнать — падают (parse_row не принимает llm_cache)**

Run: `.venv/bin/python -m pytest tests/test_llm_fallback_integration.py -q`
Expected: FAIL (TypeError: parse_row() got an unexpected keyword argument)

- [ ] **Step 3: Интеграция в `process_specification_table.py`**

a) Импорт (после `import size_notations as szn`):

```python
import llm_size_classifier as lsc
```

b) `parse_row` — сигнатура (стр. 1105):

```python
def parse_row(row: dict, defaults: dict, llm_cache: Optional[Dict[str, "lsc.SizeClassification"]] = None) -> Tuple[Optional[dict], Optional[dict]]:
```

c) Duct-путь (стр. 1283) — после `section, dims = parse_size(size)` добавить усыновление:

```python
    # Воздуховоды
    section, dims = parse_size(size)
    if not section and llm_cache:
        llm_result = llm_cache.get(size)
        if llm_result is not None and llm_result.adoptable:
            logger.info("LLM-фолбэк: размер %r усыновлён (%s)", size, llm_result.format_class)
            section, dims = llm_result.section, dict(llm_result.dims)
```

(Остальной duct-путь без изменений: он сам докинет L0 по умолчанию и выберет артикул.)

d) Финальный fallthrough (стр. 1349-1352) — заменить на:

```python
    # Диффузоры и прочее без возможности автозагрузки
    reason = classify_skip(name, size, unit, ptype)
    skip_dict = {"name": name, "size": size, "unit": unit, "reason": reason}
    if reason == "Не удалось распознать размер / тип" and llm_cache:
        llm_result = llm_cache.get(size)
        if llm_result is not None:
            skip_dict["reason"] = lsc.REASON_LLM_REJECTED
            skip_dict["llm_format_class"] = llm_result.format_class
            skip_dict["llm_detail"] = llm_result.detail
            skip_dict["llm_raw"] = llm_result.raw_response[:2000]
            logger.warning(
                "LLM-классификация отклонена: %r → %s (%s)",
                size, llm_result.format_class, llm_result.detail,
            )
    return None, _apply_ocr_warnings(skip_dict, ocr_warnings)
```

e) `process_rows` — предпроход перед циклом (после стр. 1384 `skipped: List[dict] = []`):

```python
    llm_cache: Optional[Dict[str, lsc.SizeClassification]] = None
    if lsc.llm_enabled():
        candidates = []
        seen = set()
        for row in rows:
            size = str(row.get("size", "")).strip()
            if size and size not in seen and parse_size(size)[0] is None:
                seen.add(size)
                candidates.append(size)
        if candidates:
            logger.info("LLM-фолбэк: классификация %d нераспознанных размеров", len(candidates))
            llm_cache = lsc.classify_sizes_batch(candidates)
```

и в цикле (стр. 1391) заменить вызов:

```python
        parsed, skip = parse_row(row, defaults, llm_cache=llm_cache)
```

- [ ] **Step 4: Прогнать интеграционные тесты**

Run: `.venv/bin/python -m pytest tests/test_llm_fallback_integration.py -q`
Expected: все PASS

- [ ] **Step 5: Полный прогон + коммит**

Run: `.venv/bin/python -m pytest tests/ -q` — Expected: весь сьют зелёный (LLM выключен в конфиге → поведение идентично, coverage-базлайны не тронуты).

```bash
git add process_specification_table.py tests/test_llm_fallback_integration.py
git commit -m "feat: llm fallback wiring in parse_row/process_rows"
```

---

### Task 4: Сквозная проверка и ручной смоук с реальным kimi

**Files:**
- Без изменений кода; отчёт — в ledger.

- [ ] **Step 1: Проверить защиту «без LLM ещё раз»**: `.venv/bin/python -m pytest tests/ -q` зелёный; `git grep -n "classify_sizes_batch" process_specification_table.py` — вызов под `lsc.llm_enabled()`.

- [ ] **Step 2: Ручной смоук (НЕ в pytest)** — временный скрипт `tmp/smoke_llm_size.py`:

```python
import llm_size_classifier as lsc

cfg = dict(lsc.get_llm_config(), enabled=True)
print("enabled:", lsc.llm_enabled(cfg))
if lsc.llm_enabled(cfg):
    out = lsc.classify_sizes_batch(["315/315-160", "1 250x800", "Ду200-1000"], cfg=cfg)
    for s, r in out.items():
        print(repr(s), "→", r.status, r.format_class, r.dims, r.detail)
```

Run: `.venv/bin/python tmp/smoke_llm_size.py`
Expected: реальный вызов kimi (секунды), «315/315-160» → tee_axbxc ok (не adoptable), остальное по факту. Результат — в отчёт. Скрипт НЕ коммитить (tmp/).

- [ ] **Step 3: Записать итоги этапа 2 в ledger** (числа, список rejected-кейсов из смоука, замечания для этапа 3).

---

## Self-Review (при написании)

- **Spec coverage:** этап 2 spec полностью: модуль+JSON-spans (Tasks 1-2), интеграция+skipped-причина+логирование (Task 3), конфиг с graceful-off (Task 1), обязательное логирование (Task 2 warn/info + Task 3 skipped-поля). Batching — batch_size. Retries/timeout — Task 2. Тесты без сети — runner инжектируется.
- **Жёсткое ограничение:** `_spans_to_dims` — единственная точка получения цифр: `int(source[start:end])` с `re.fullmatch(r"\d+")`. Проверено по всем путям.
- **Отклонения от spec, зафиксированные осознанно:** (1) response_format OpenAI-стиля невозможен через kimi CLI — strict JSON достигается промптом + `raw_decode` парсингом; (2) adoption только round_diameter/rect_axb (остальные классы — лог/отчёт, fittings-путь приоритетен); (3) «api key из env» не требуется — kimi CLI использует локальную авторизацию.
- **Type consistency:** `SizeClassification` поля едины в Tasks 1-3; `lsc.SizeClassification` ссылка в сигнатуре parse_row — в кавычках (терпимо для py3.12, Optional/Dict импортированы в модуле).
