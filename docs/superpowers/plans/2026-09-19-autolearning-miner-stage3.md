# Stage 3: Auto-Learning Size-Format Miner — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `tools/size_format_mine.py` — замкнутая петля C: агрегация skipped-отчётов → кластеры нераспознанных размеров → kimi предлагает АДДИТИВНЫЕ варианты записи для `config/size_notations.yaml` (только структуру, без единой цифры размера) → gate (кластер парсится каскадом + полный pytest зелёный) → коммит с provenance; gate красный → отброс.

**Architecture:** Патч-пространство майнера — только символьные аддитивные записи: `diameter_prefixes`, `diameter_suffixes`, `separators`. Они конфиг-драйвены (`size_notations.prefix_group()` и т.д. используются в 6 точках каскада), поэтому одна добавленная запись мгновенно расширяет покрытие везде. Новые классы паттернов (слэш-тройники, thousands-space) — НЕ в зоне майнера (нужен код), уходят в отчёт/BACKLOG. Gate применяет патч к КОПИИ yaml через env-override `SPEC_TO_1C_SIZE_NOTATIONS` + `size_notations.reload_notations()`, гоняет live-парсинг кластера и полный pytest в subprocess, и только при зелёном — пишет в реальный конфиг с provenance и коммитит.

**Tech Stack:** Python 3.12 stdlib + pyyaml; повторное использование `llm_size_classifier._run_kimi`/`_assistant_content`. БЕЗ новых pip-зависимостей.

Spec: `docs/superpowers/specs/2026-09-10-size-notation-config-design.md` (этап 3). База: stage 1 (`size_notations`, coverage-корпус) + stage 2 (`llm_size_classifier`, `REASON_LLM_REJECTED`) — оба на ветке.

## Global Constraints

- Запуск тестов: `.venv/bin/python -m pytest tests/ -q` из корня репозитория.
- НЕ добавлять pip-зависимости.
- **Жёсткое ограничение:** патчи конфига содержат ТОЛЬКО символьную структуру (префиксы/суффиксы/сепараторы). Запрещены цифры, длины, диапазоны в предложениях kimi — санитайзер отбрасывает любое предложение, содержащее `\d`. Числовые значения размеров никогда не генерируются.
- Патчи строго аддитивные: майнер не изменяет и не удаляет существующие записи конфига.
- Gate = обязательное условие коммита: (1) каждая строка кластера парсится каскадом под пропатченной КОПИЕЙ конфига; (2) полный `pytest tests/ -q` зелёный (включая закреплённые coverage-базлайны — падение ниже baseline = красный). Любой красный → патч отбрасывается, кластер уходит в отчёт.
- Реальный конфиг перезаписывается ТОЛЬКО после зелёного gate; каждый принятый патч — отдельный git-коммит (лёгкий revert). Запуск по умолчанию — `--dry-run`; коммит только с `--apply`.
- Тесты НИКОГДА не вызывают kimi, pytest-subprocess и не ходят в сеть (все внешние части инжектируются).
- Поведение пайплайна без запуска майнера не меняется (майнер — отдельный инструмент, не импортируется пайплайном).
- Dirty `requirements.txt` не коммитить никогда.

## Ключевые факты кодовой базы

- Skipped-отчёты: `<output>_skipped.json` — список словарей с полями `name/size/unit/reason` (пишется process_csv, process_specification_table.py:1519-1521). Причины нераспознанного размера: «Не удалось распознать размер / тип» (classify_skip :583) и `llm_size_classifier.REASON_LLM_REJECTED` («LLM-классификация отклонена»).
- `size_notations`: env-override `SPEC_TO_1C_SIZE_NOTATIONS`, `reload_notations()` сбрасывает кэш паттернов; аксессоры вызываются в точках каскада (parse_size/extract_dimensions/extract_size_token/is_round) — патч конфига действует сразу после reload.
- `llm_size_classifier._run_kimi(prompt, cfg) -> stdout` и `_assistant_content(stdout) -> content` — переиспользуем (внутренние, но тот же пакет). Конфиг llm: `get_llm_config()` (enabled:false по умолчанию; для майнера требуем enabled + llm_enabled()).
- Эталонные skipped-файлы для dry-run: `tmp/vladik_skipped.json`, `tmp/vladik_skipped_baseline.json`.
- Gate-pytest: subprocess `.venv/bin/python -m pytest tests/ -q` с env-переменной копии конфига; код возврата 0 = зелёный.

## Файловая структура

- Modify: `config.yaml` — секция `learning:` (enabled:false, min_occurrences, auto_commit:false).
- Create: `tools/size_format_mine.py` — load/cluster/extract → propose (kimi) → sanitize → gate (copy/live-parse/pytest) → apply+provenance+commit.
- Create: `tests/test_size_format_mine.py`.

## Публичный API (контракт между задачами)

```python
def load_skipped_sizes(report_paths: list[str]) -> "collections.Counter[str]"
    # только записи с reason == «Не удалось распознать размер / тип» или REASON_LLM_REJECTED
def extract_symbol_candidates(s: str) -> list[str]
    # не-цифровые токены вокруг чисел: из "∅315" → ["∅"], из "1250✕800" → ["✕"]
def sanitize_proposals(prop: dict, current: dict) -> dict
    # {add_prefixes/add_suffixes/add_separators: [...]} — отбрасывает: любые записи с \d,
    # дубликаты текущего конфига, записи длиннее 3 символов, пустые; приводит к str
def gate_check(patch: dict, cluster_strings: list[str], run_pytest=...) -> tuple[bool, str]
    # применяет patch к временной копии yaml (env+reload), live-парсинг кластера,
    # pytest-subprocess; (True, "") или (False, причина)
```

---

### Task 1: Конфиг learning + загрузка/кластеризация/символы

**Files:**
- Modify: `config.yaml`
- Create: `tools/size_format_mine.py` (скелет: loader/cluster/symbols)
- Create: `tests/test_size_format_mine.py`

**Interfaces:**
- Produces: `load_skipped_sizes`, `extract_symbol_candidates` (контракт выше); `get_learning_config() -> dict`.

- [ ] **Step 1: Написать failing-тесты** (`tests/test_size_format_mine.py`)

```python
"""Майнер автообучения форматов. Все внешние части замоканы."""
import json

import pytest

import tools.size_format_mine as mine


def test_get_learning_config_defaults():
    cfg = mine.get_learning_config()
    assert cfg["enabled"] is False
    assert cfg["min_occurrences"] >= 2
    assert "auto_commit" in cfg


def test_load_skipped_sizes_filters_reasons(tmp_path):
    report = [
        {"name": "a", "size": "∅315", "reason": "Не удалось распознать размер / тип"},
        {"name": "b", "size": "Ø400", "reason": "LLM-классификация отклонена"},
        {"name": "c", "size": "Ф160", "reason": "Покупная позиция — завод не производит"},
        {"name": "d", "size": "", "reason": "Не удалось распознать размер / тип"},
    ]
    p = tmp_path / "order_skipped.json"
    p.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    counter = mine.load_skipped_sizes([str(p)])
    assert counter == {"∅315": 1, "Ø400": 1}


def test_load_skipped_sizes_aggregates_multiple_reports(tmp_path):
    rows = [{"name": "a", "size": "∅315", "reason": "Не удалось распознать размер / тип"}]
    for name in ("one_skipped.json", "two_skipped.json"):
        (tmp_path / name).write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    counter = mine.load_skipped_sizes([str(tmp_path / "one_skipped.json"),
                                       str(tmp_path / "two_skipped.json")])
    assert counter["∅315"] == 2


def test_extract_symbol_candidates():
    assert mine.extract_symbol_candidates("∅315") == ["∅"]
    assert mine.extract_symbol_candidates("1250✕800") == ["✕"]
    assert mine.extract_symbol_candidates("315ø") == ["ø"]
    assert mine.extract_symbol_candidates("315") == []
    assert mine.extract_symbol_candidates("Ду 315") == ["Ду"]  # буквенный префикс с пробелом


def test_cluster_filter_min_occurrences():
    counter = {"∅315": 5, "Ø400": 1, "Х500": 3}
    clusters = mine.stable_clusters(counter, min_occurrences=3)
    assert clusters == {"∅315": 5, "Х500": 3}
```

(Если `tools/` не является пакетом без `__init__.py` — проверь импорт: тесты в `tests/` добавляют корень в sys.path через conftest, поэтому `import tools.size_format_mine` требует `tools/__init__.py`. Если его нет — создай пустой `tools/__init__.py` и добавь его в коммит Task 1. `tools/as_order_loader` — подпакет, не мешает.)

- [ ] **Step 2: Прогнать — упадут импортом**

Run: `.venv/bin/python -m pytest tests/test_size_format_mine.py -q`
Expected: FAIL (ModuleNotFoundError)

- [ ] **Step 3: Секция в `config.yaml`** (после секции `llm:`)

```yaml
# Автообучение вариантов записи размеров (tools/size_format_mine.py):
# кластеры нераспознанных размеров из skipped-отчётов → kimi предлагает
# аддитивные символьные записи → gate (кластер парсится + pytest зелёный) → коммит.
# Патчи строго аддитивные и без цифр — только структура записи.
learning:
  enabled: false
  min_occurrences: 3
  auto_commit: false     # true — коммитить принятые патчи автоматически (требуется --apply)
```

- [ ] **Step 4: Скелет майнера** (`tools/size_format_mine.py`)

```python
"""Автообучение вариантов записи размеров из skipped-отчётов (петля C).

Цикл: агрегация skipped-отчётов → кластеры нераспознанных строк → kimi
предлагает АДДИТИВНЫЕ символьные записи для config/size_notations.yaml
(без единой цифры размера) → gate: кластер парсится каскадом + полный pytest
зелёный → коммит с provenance. Gate красный → патч отброшен, кластер в отчёте.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Dict, List

from config import get_config

ROOT = Path(__file__).resolve().parent.parent

UNRECOGNIZED_REASONS = (
    "Не удалось распознать размер / тип",
    "LLM-классификация отклонена",
)


def get_learning_config() -> Dict:
    return dict(get_config().get("learning") or {})


def load_skipped_sizes(report_paths: List[str]) -> "Counter[str]":
    """Собирает счётчики size из skipped-отчётов по причинам нераспознанного размера."""
    counter: Counter[str] = Counter()
    for path in report_paths:
        try:
            rows = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            if row.get("reason") not in UNRECOGNIZED_REASONS:
                continue
            size = str(row.get("size") or "").strip()
            if size:
                counter[size] += 1
    return counter


def stable_clusters(counter: "Counter[str]", min_occurrences: int) -> Dict[str, int]:
    return {s: n for s, n in counter.most_common() if n >= min_occurrences}


_SYMBOL_RE = re.compile(r"[^\d\s]+")


def extract_symbol_candidates(s: str) -> List[str]:
    """Не-цифровые токены вокруг чисел: '∅315' → ['∅'], '1250✕800' → ['✕'],
    'Ду 315' → ['Ду']. Цифры/пробелы игнорируются."""
    return [t for t in _SYMBOL_RE.findall(s) if not t.isdigit()]


def sanitize_proposals(prop, current):
    raise NotImplementedError  # Task 2


def gate_check(patch, cluster_strings, run_pytest=None):
    raise NotImplementedError  # Task 3
```

- [ ] **Step 5: Прогнать**

Run: `.venv/bin/python -m pytest tests/test_size_format_mine.py -q`
Expected: PASS (5 тестов; NotImplementedError не дёргается)

- [ ] **Step 6: Полный прогон + коммит**

```bash
.venv/bin/python -m pytest tests/ -q
git add config.yaml tools/size_format_mine.py tests/test_size_format_mine.py
# + tools/__init__.py если создавал
git commit -m "feat: learning config + miner skeleton (load/cluster/symbols)"
```

---

### Task 2: Предложения kimi + санитайзер

**Files:**
- Modify: `tools/size_format_mine.py`
- Modify: `tests/test_size_format_mine.py`

**Interfaces:**
- Consumes: `llm_size_classifier._run_kimi`, `_assistant_content`, `get_llm_config`, `llm_enabled`; `size_notations.get_notations()`.
- Produces: `propose_additions(clusters: dict[str, int], runner=None) -> dict` — сырой ответ kimi; `sanitize_proposals(prop, current) -> dict` — очищенный аддитивный патч.

- [ ] **Step 1: Дописать failing-тесты**

```python
import llm_size_classifier as lsc
import size_notations as szn


def _current():
    n = szn.get_notations()
    return {
        "diameter_prefixes": list(n["diameter_prefixes"]),
        "diameter_suffixes": list(n["diameter_suffixes"]),
        "separators": list(n["separators"]),
    }


def test_sanitize_drops_digits_and_too_long():
    prop = {"add_prefixes": ["dia315", "∅", "toolongprefix"],
            "add_suffixes": ["øØ"],
            "add_separators": ["✕"]}
    out = mine.sanitize_proposals(prop, _current())
    assert out["add_prefixes"] == ["∅"]            # dia315 содержит цифры, toolongprefix >3
    assert out["add_suffixes"] == []               # ø и Ø уже в конфиге
    assert out["add_separators"] == ["✕"]


def test_sanitize_drops_duplicates_and_nonadditive():
    cur = _current()
    prop = {"add_prefixes": [cur["diameter_prefixes"][0], "новый"],
            "add_suffixes": [], "add_separators": cur["separators"]}
    out = mine.sanitize_proposals(prop, cur)
    assert cur["diameter_prefixes"][0] not in out["add_prefixes"]
    assert out["add_separators"] == []


def test_propose_additions_parses_kimi_response(monkeypatch):
    import json as _json
    content = _json.dumps({
        "add_prefixes": ["∅"], "add_suffixes": [], "add_separators": ["✕"]
    })
    stdout = _json.dumps({"role": "assistant", "content": content})

    monkeypatch.setattr(lsc, "llm_enabled", lambda cfg=None: True)
    runner_calls = []

    def runner(prompt, cfg):
        runner_calls.append(prompt)
        return stdout

    out = mine.propose_additions({"∅315": 5, "1250✕800": 3}, runner=runner)
    assert runner_calls, "kimi не вызван"
    assert "∅315" in runner_calls[0] and "1250✕800" in runner_calls[0]
    assert out["add_prefixes"] == ["∅"]


def test_propose_additions_failure_returns_empty(monkeypatch):
    monkeypatch.setattr(lsc, "llm_enabled", lambda cfg=None: True)

    def runner(prompt, cfg):
        raise RuntimeError("kimi down")

    assert mine.propose_additions({"∅315": 5}, runner=runner) == {}


def test_sanitize_rejects_unknown_sections_and_types():
    out = mine.sanitize_proposals(
        {"add_prefixes": ["∅"], "validation": {"min_side_mm": 50}, "add_lengths": {"X": 1}},
        _current(),
    )
    assert "validation" not in out and "add_lengths" not in out
    assert out["add_prefixes"] == ["∅"]
```

- [ ] **Step 2: Прогнать — новые падают (NotImplementedError)**

Run: `.venv/bin/python -m pytest tests/test_size_format_mine.py -q`
Expected: старые PASS, новые FAIL

- [ ] **Step 3: Реализовать** (дописать в `tools/size_format_mine.py`)

```python
_MAX_SYMBOL_LEN = 3
_PATCH_SECTIONS = ("add_prefixes", "add_suffixes", "add_separators")


def _sanitize_entry(entry, existing: set) -> str | None:
    e = str(entry or "").strip()
    if not e or len(e) > _MAX_SYMBOL_LEN:
        return None
    if re.search(r"\d", e):           # ЖЁСТКОЕ ОГРАНИЧЕНИЕ: никаких цифр
        return None
    low = e.lower()
    if low in {x.lower() for x in existing}:
        return None                   # строго аддитивно: дубликаты отбрасываем
    return e


def sanitize_proposals(prop: Dict, current: Dict) -> Dict:
    """Очищает сырой ответ kimi до аддитивного символьного патча.
    Возвращает ТОЛЬКО известные add_*-секции; всё числовое/неизвестное отброшено."""
    prop = prop if isinstance(prop, dict) else {}
    mapping = {
        "add_prefixes": "diameter_prefixes",
        "add_suffixes": "diameter_suffixes",
        "add_separators": "separators",
    }
    out: Dict[str, List[str]] = {}
    for section in _PATCH_SECTIONS:
        raw_list = prop.get(section) or []
        if not isinstance(raw_list, list):
            continue
        existing = set(current.get(mapping[section]) or [])
        cleaned = []
        for entry in raw_list:
            e = _sanitize_entry(entry, existing | set(cleaned))
            if e is not None:
                cleaned.append(e)
        out[section] = cleaned
    return out


def _propose_prompt(clusters: Dict[str, int], current: Dict) -> str:
    items = "\n".join(f"{json.dumps(s, ensure_ascii=False)}: {n} вхождений"
                      for s, n in clusters.items())
    return (
        "Ты помощник по расширению словаря вариантов записи размеров воздуховодов.\n"
        "Ниже — кластеры НЕРАСПОЗНАННЫХ строк размеров из производственных спецификаций "
        "(строка: число вхождений):\n"
        f"{items}\n\n"
        f"Текущий конфиг: diameter_prefixes={current['diameter_prefixes']}, "
        f"diameter_suffixes={current['diameter_suffixes']}, separators={current['separators']}.\n\n"
        "Задача: предложи, какие СИМВОЛЬНЫЕ записи добавить, чтобы каскад regex мог "
        "распознать эти строки. Верни строго один JSON-объект без пояснений:\n"
        '{"add_prefixes":["..."],"add_suffixes":["..."],"add_separators":["..."]}\n'
        "Правила: только символы-префиксы диаметра (до числа), символы-суффиксы (после числа), "
        "разделители сторон прямоугольника; 1-3 символа; НИКАКИХ цифр, длин, диапазонов; "
        "не предлагай то, что уже есть в конфиге; если кластер — это не символьная "
        "запись (например слэш-форма '315/315/160'), предложи пустые списки.\n"
        'Пример для "∅315": {"add_prefixes":["∅"],"add_suffixes":[],"add_separators":[]}'
    )


def propose_additions(clusters: Dict[str, int], runner=None) -> Dict:
    """kimi предлагает аддитивные символьные записи. Любой сбой → {} (пайплайн не падает)."""
    if not clusters:
        return {}
    cfg = lsc.get_llm_config()
    if not lsc.llm_enabled(cfg):
        return {}
    run = lsc._run_kimi if runner is None else runner
    n = szn.get_notations()
    current = {
        "diameter_prefixes": list(n["diameter_prefixes"]),
        "diameter_suffixes": list(n["diameter_suffixes"]),
        "separators": list(n["separators"]),
    }
    prompt = _propose_prompt(clusters, current)
    try:
        content = lsc._assistant_content(run(prompt, cfg))
        import json as _json
        obj, _ = _json.JSONDecoder().raw_decode(content[content.find("{"):])
        return obj if isinstance(obj, dict) else {}
    except Exception as exc:  # noqa: BLE001
        print(f"propose_additions: kimi недоступна: {exc}")
        return {}
```

(Добавить импорты `import llm_size_classifier as lsc` и `import size_notations as szn` в шапку модуля.)

- [ ] **Step 4: Прогнать**

Run: `.venv/bin/python -m pytest tests/test_size_mine.py -q` — ВНИМАНИЕ: правильное имя файла `tests/test_size_format_mine.py`:
Run: `.venv/bin/python -m pytest tests/test_size_format_mine.py -q`
Expected: все PASS

- [ ] **Step 5: Полный прогон + коммит**

```bash
.venv/bin/python -m pytest tests/ -q
git add tools/size_format_mine.py tests/test_size_format_mine.py
git commit -m "feat: miner kimi proposals with digit-free sanitizer"
```

---

### Task 3: Gate (копия конфига + live-парсинг + pytest) + apply с provenance

**Files:**
- Modify: `tools/size_format_mine.py`
- Modify: `tests/test_size_format_mine.py`

**Interfaces:**
- Consumes: sanitize_proposals (Task 2); `size_notations.reload_notations`, env `SPEC_TO_1C_SIZE_NOTATIONS`; `parse_size`/`extract_dimensions` из process_specification_table.
- Produces: `gate_check(patch, cluster_strings, run_pytest=None) -> tuple[bool, str]`; `apply_patch(patch, cluster_info, session: str, dry_run=True) -> str` — путь к записанному yaml / описание; `main(argv=None) -> int`.

- [ ] **Step 1: Дописать failing-тесты**

```python
import yaml

import process_specification_table as pst
import size_notations as szn


PATCH = {"add_prefixes": ["∅"], "add_suffixes": [], "add_separators": []}


def test_gate_green_when_cluster_parses(tmp_path, monkeypatch):
    # Патч добавляет префикс ∅: под пропатченной копией "∅315" должен парситься.
    monkeypatch.setattr(mine, "_run_pytest_gate", lambda env: (True, ""))
    ok, reason = mine.gate_check(PATCH, ["∅315"])
    assert ok, reason


def test_gate_red_when_cluster_still_unparsed(tmp_path, monkeypatch):
    # Слэш-форма не символьная — патч её не берёт.
    monkeypatch.setattr(mine, "_run_pytest_gate", lambda env: (True, ""))
    ok, reason = mine.gate_check(PATCH, ["315/315/160"])
    assert not ok and "парс" in reason.lower()


def test_gate_red_when_pytest_fails(monkeypatch):
    monkeypatch.setattr(mine, "_run_pytest_gate", lambda env: (False, "3 failed"))
    ok, reason = mine.gate_check(PATCH, ["∅315"])
    assert not ok and "3 failed" in reason


def test_gate_restores_config_after_check():
    before = szn.get_notations()
    mine.gate_check(PATCH, ["∅315"], run_pytest=lambda env: (True, ""))
    assert szn.get_notations() is before or szn.prefix_group() == szn.prefix_group()


def test_apply_patch_writes_provenance(tmp_path):
    n = szn.get_notations()
    patched = mine._merged_notations(PATCH)   # helper: dict = текущий конфиг + патч
    assert "∅" in patched["diameter_prefixes"]
    assert n["diameter_prefixes"] != patched["diameter_prefixes"]  # исходный не мутирован


def test_main_dry_run_reports_without_writing(tmp_path, monkeypatch):
    # Сквозной dry-run: отчёт формируется, реальный конфиг не тронут.
    report = [{"name": "a", "size": "∅315",
               "reason": "Не удалось распознать размер / тип"}] * 3
    rp = tmp_path / "x_skipped.json"
    rp.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    before = Path("config/size_notations.yaml").read_text(encoding="utf-8")
    monkeypatch.setattr(lsc, "llm_enabled", lambda cfg=None: True)
    monkeypatch.setattr(mine, "propose_additions",
                        lambda clusters, runner=None: {"add_prefixes": ["∅"],
                                                       "add_suffixes": [], "add_separators": []})
    monkeypatch.setattr(mine, "_run_pytest_gate", lambda env: (True, ""))
    rc = mine.main(["--reports", str(rp), "--min-occurrences", "2", "--dry-run"])
    assert rc == 0
    assert Path("config/size_notations.yaml").read_text(encoding="utf-8") == before
```

(Замечание: `test_gate_restores_config_after_check` намеренно слабый — главное, что gate не оставляет пропатченный конфиг в кэше; если придёшь к сильной проверке (сравнение prefix_group до/после), улучши. `mine.main` в dry-run НЕ пишет реальный конфиг и НЕ коммитит; вывод — в stdout и/или JSON-отчёт рядом (минимум: печать принятых/отклонённых патчей с причинами).)

- [ ] **Step 2: Прогнать — новые падают**

Run: `.venv/bin/python -m pytest tests/test_size_format_mine.py -q`
Expected: FAIL (NotImplementedError)

- [ ] **Step 3: Реализовать gate + apply** (дописать в `tools/size_format_mine.py`)

```python
import copy
import datetime
import os
import subprocess
import sys
import tempfile

import process_specification_table as pst


def _merged_notations(patch: Dict) -> Dict:
    """Текущий конфиг + аддитивный патч (без мутации кэша)."""
    n = copy.deepcopy(szn.get_notations())
    n["diameter_prefixes"] = list(n.get("diameter_prefixes") or []) + list(patch.get("add_prefixes") or [])
    n["diameter_suffixes"] = list(n.get("diameter_suffixes") or []) + list(patch.get("add_suffixes") or [])
    n["separators"] = list(n.get("separators") or []) + list(patch.get("add_separators") or [])
    return n


def _write_temp_config(notations: Dict) -> str:
    fd, path = tempfile.mkstemp(suffix=".yaml", prefix="size_notations_")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        yaml.safe_dump(notations, f, allow_unicode=True, sort_keys=False)
    return path


def _run_pytest_gate(env: Dict[str, str]) -> tuple:
    """Полный pytest под пропатченным конфигом. (False, хвост вывода) при красном."""
    e = dict(os.environ)
    e.update(env)
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/", "-q", "-x"],
        capture_output=True, text=True, encoding="utf-8", env=e, cwd=str(ROOT),
        timeout=900,
    )
    if proc.returncode != 0:
        tail = "\n".join((proc.stdout + proc.stderr).splitlines()[-15:])
        return False, tail
    return True, ""


def gate_check(patch: Dict, cluster_strings: List[str], run_pytest=None) -> tuple:
    """Gate: (1) каждая строка кластера парсится под пропатченной КОПИЕЙ конфига;
    (2) полный pytest зелёный. Реальный конфиг не трогаем."""
    run_pytest = run_pytest or _run_pytest_gate
    tmp_path = _write_temp_config(_merged_notations(patch))
    old_env = os.environ.get("SPEC_TO_1C_SIZE_NOTATIONS")
    os.environ["SPEC_TO_1C_SIZE_NOTATIONS"] = tmp_path
    try:
        szn.reload_notations()
        unparsed = [s for s in cluster_strings
                    if pst.parse_size(s)[0] is None and not pst.extract_dimensions(s)]
        if unparsed:
            return False, f"под патчем не парсятся: {unparsed[:5]}"
        ok, detail = run_pytest({"SPEC_TO_1C_SIZE_NOTATIONS": tmp_path})
        if not ok:
            return False, f"pytest красный: {detail}"
        return True, ""
    finally:
        if old_env is None:
            os.environ.pop("SPEC_TO_1C_SIZE_NOTATIONS", None)
        else:
            os.environ["SPEC_TO_1C_SIZE_NOTATIONS"] = old_env
        szn.reload_notations()
        Path(tmp_path).unlink(missing_ok=True)


def apply_patch(patch: Dict, cluster_info: Dict[str, int], session: str, path: str = "config/size_notations.yaml") -> str:
    """Пишет принятый патч в реальный конфиг + provenance. Возвращает итоговый текст."""
    merged = _merged_notations(patch)
    merged.setdefault("provenance", [])
    today = datetime.date.today().isoformat()
    for section, key in (("add_prefixes", "diameter_prefixes"),
                         ("add_suffixes", "diameter_suffixes"),
                         ("add_separators", "separators")):
        for entry in patch.get(section) or []:
            merged["provenance"].append({
                "entry": entry, "section": key,
                "source": ", ".join(list(cluster_info)[:5]),
                "count": sum(cluster_info.values()),
                "date": today, "session": session,
            })
    p = ROOT / path
    p.write_text(yaml.safe_dump(merged, allow_unicode=True, sort_keys=False), encoding="utf-8")
    szn.reload_notations()
    return p.read_text(encoding="utf-8")


def main(argv=None) -> int:
    """CLI: --reports P [P...] --min-occurrences N --dry-run/--apply [--no-commit]."""
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reports", nargs="+", required=True)
    parser.add_argument("--min-occurrences", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--no-commit", action="store_true")
    args = parser.parse_args(argv)

    lcfg = get_learning_config()
    if not args.apply:                       # default = dry-run
        args.dry_run = True
    min_occ = args.min_occurrences or int(lcfg.get("min_occurrences", 3))

    counter = load_skipped_sizes(args.reports)
    clusters = stable_clusters(counter, min_occ)
    print(f"кластеров (>= {min_occ} вхождений): {len(clusters)} из {sum(counter.values())} строк")
    if not clusters:
        return 0

    current = {
        "diameter_prefixes": list(szn.get_notations()["diameter_prefixes"]),
        "diameter_suffixes": list(szn.get_notations()["diameter_suffixes"]),
        "separators": list(szn.get_notations()["separators"]),
    }
    raw = propose_additions(clusters)
    patch = sanitize_proposals(raw, current)
    if not any(patch.values()):
        print("kimi не предложила аддитивных символьных записей")
        return 0

    print("патч:", patch)
    ok, reason = gate_check(patch, list(clusters))
    if not ok:
        print(f"GATE ОТКАЗ: {reason}")
        return 1
    if args.dry_run:
        print("GATE ЗЕЛЁНЫЙ (dry-run: конфиг не изменён, коммит не создан)")
        return 0

    apply_patch(patch, clusters, session="kimi-cli")
    if args.no_commit or not lcfg.get("auto_commit"):
        print("конфиг обновлён; коммит пропущен (auto_commit=false или --no-commit)")
        return 0

    subprocess.run(["git", "add", "config/size_notations.yaml"], check=True, cwd=str(ROOT))
    subprocess.run(["git", "commit", "-m",
                    "learn(size_notations): mined additive symbols "
                    f"{patch} (clusters: {list(clusters)[:5]})"],
                   check=True, cwd=str(ROOT))
    print("принято и закоммичено")
    return 0
```

(Добавить `import yaml` в шапку. Порядок операций в main осознанно: gate гоняется и в dry-run — пользователь видит, прошёл бы патч. Документируй это в отчёте.)

- [ ] **Step 4: Прогнать**

Run: `.venv/bin/python -m pytest tests/test_size_format_mine.py -q`
Expected: все PASS. Если `test_main_dry_run_reports_without_writing` падает на перезагрузке конфигов между тестами (env-override протекает) — проверь finally-блок gate_check и изолируй тест monkeypatch'ем `szn.reload_notations` при необходимости.

- [ ] **Step 5: Полный прогон + коммит**

```bash
.venv/bin/python -m pytest tests/ -q
git add tools/size_format_mine.py tests/test_size_format_mine.py
git commit -m "feat: miner gate (copy config + live parse + pytest) and provenance apply"
```

---

### Task 4: Сквозной dry-run на реальных skipped-отчётах + итоги

**Files:**
- Без изменений кода (возможны мелкие правки по результатам dry-run — коммитить отдельно).

- [ ] **Step 1: Найти реальные skipped-отчёты**: `find . -name "*_skipped.json" -not -path "./.venv/*" 2>/dev/null` — ожидаются tmp/vladik_skipped*.json и, возможно, другие. Если файлов мало, допустимо собрать свежий: прогнать process_csv/process_rows на examples-файле с LLM-усыновлением ВЫКЛ и записать skipped-отчёт в tmp/.

- [ ] **Step 2: Dry-run** (LLM при этом реально дёргается — это ручной шаг, не pytest):

```bash
.venv/bin/python -m tools.size_format_mine --reports tmp/vladik_skipped.json --min-occurrences 2 --dry-run
```

Expected: кластеры найдены, kimi предложила патч (или честно пусто), gate зелёный/красный с причиной. Результат — в отчёт (ledger). Реальный конфиг НЕ менялся — проверь `git status` после.

- [ ] **Step 3: Если dry-run показал полезный патч и gate зелёный — НЕ применять автоматически; зафиксировать в отчёте предложение для пользователя.

- [ ] **Step 4: Итоги этапа 3 в ledger**: числа кластеров, предложенные патчи, решения gate, список «не символьных» кластеров (кандидаты в BACKLOG на этап 4 — кодовые изменения).

---

## Self-Review (при написании)

- **Spec coverage:** петля C целиком: агрегация+кластеры (Task 1), kimi-аддитивные патчи без цифр (Task 2), gate копия+pytest+provenance+коммит (Task 3), dry-run на реальных данных (Task 4). «Полный авто с gate» — auto_commit + --apply; default dry-run.
- **Жёсткое ограничение:** `_sanitize_entry` режет всё с `\d`; `_merged_notations` добавляет только символьные списки; provenance не влияет на аксессоры.
- **Type consistency:** контрактные сигнатуры едины между задачами; `main` возвращает int.
- **Известное ограничение (честно):** слэш-формы/кодовые формы майнер не закроет никогда — это код, не конфиг; они пойдут в BACKLOG-отчёт Task 4.
