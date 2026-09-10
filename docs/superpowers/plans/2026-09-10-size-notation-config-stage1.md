# Stage 1: Size Notation Config + Coverage Corpus — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Вынести словари вариантов записи размеров из `process_specification_table.py` в `config/size_notations.yaml`, зафиксировать трёхуровневый эталонный корпус покрытия (baseline / матрица форматов / мультикорпус) как regression-net.

**Architecture:** Новый модуль `size_notations.py` — единая точка загрузки YAML-конфига и сборки regex-паттернов (сейчас префикс-набор `dn|d|дн|ду|д|ф|ø|⌀` продублирован в 6 местах god-файла). `process_specification_table.py` заменяет литералы на вызовы аксессоров `size_notations`. Coverage-тесты пинуются числом и не дают покрытию упасть.

**Tech Stack:** Python 3.12, pyyaml (уже в requirements), pytest. БЕЗ новых pip-зависимостей. LLM на этом этапе НЕ используется.

Spec: `docs/superpowers/specs/2026-09-10-size-notation-config-design.md` (этапы 2 и 3 — отдельными планами после этого).

## Global Constraints

- Запуск тестов: `.venv/bin/python -m pytest tests/ -q` из корня репозитория.
- НЕ добавлять pip-зависимости. НЕ трогать логику мэтчинга/артикулов — изменения локальны вокруг `parse_size`/`extract_dimensions`/regex-констант размеров.
- Существующие OCR-эвристики (потерянный ноль, «7 00», «125ø») НЕ удалять — только переносить словари в конфиг.
- Поведение на существующих тестах не меняется; покрытие baseline не падает ниже закреплённого числа.
- Baseline-числа в тестах фиксируются ПОСЛЕ первого прогона (шаг «записать и запинить»), коммитом отдельно от исходного теста.
- Каждый таск заканчивается зелёным `pytest tests/ -q` и коммитом.

## Ключевые факты кодовой базы (для исполнителя с нулевым контекстом)

- `process_specification_table.py` (~1557 строк) — god-файл пайплайна. Импорт конфига: `from config import get_config, mapping_file` (стр. 33). Снапшот `_DEFAULTS = get_config()` на стр. 44 (на этом этапе не трогаем — меняем только константы размеров на аксессоры; lazy-defaults остаётся на этап, когда это понадобится LLM-фолбэку).
- Точки с литералами префиксов диаметра `dn|d|дн|ду|д|ф|ø|⌀`:
  - стр. 220 (`is_round`), стр. 233 (`normalize_dimension_prefix`), стр. 267 и 271 (`extract_size_token`),
  - стр. 431 и 436 (`parse_size`), стр. 470 (`extract_dimensions`), стр. 612 (`try_parse_ksd` — НЕ трогаем, там только замена х→x).
- `LITENED_SILENCER_SIZES` — стр. 81-91 (9 кодов); использование стр. 540-549 (L0: 1100 NKD / 510 NKK — хардкод на стр. 549).
- `_OCR_MIN_RECT_SIDE = 100` — стр. 356; используется стр. 389, 391.
- Суффиксный класс `[øØ⌀]` — стр. 223, 267, 470. Сепараторы `х→x, ×→x, *→x` — стр. 239, 413, 459, 599.
- Эталон: `tmp/vladik_success.json` — 234 позиции `{article, params{...}, quantity, ..., comment}`. `comment` — исходное наименование со встроенным размером. У воздуховодов `L0` в params — это ДЕФОЛТНАЯ длина (3000/1250), которую добавляет `parse_row`, а не извлечение из имени → в baseline-сравнении L0 не проверяем.
- Skipped-отчёт пишется в `process_csv` (стр. 1490-1492, 1519-1521) — на этом этапе не меняется.
- `config.py`: `load_config/get_config/reload_config`, env-override `SPEC_TO_1C_CONFIG`.

---

### Task 1: Baseline-корпус Владикавказ + coverage-тест

**Files:**
- Create: `tests/fixtures/vladik_success.json` (копия `tmp/vladik_success.json`)
- Test: `tests/test_size_coverage.py`

**Interfaces:**
- Consumes: `process_specification_table.extract_dimensions(text: str) -> Dict[str, float]` (ключи A0/B0/D0/A1/B1/D1/D2/L0/U0/R0).
- Produces: константа `BASELINE_VLADIK_COVERAGE: float` в `tests/test_size_coverage.py` — её в Task 2-6 не переименовывать; функция `_primary_dims_match(comment, params) -> bool`, переиспользуемая позже.

- [ ] **Step 1: Скопировать фикстуру**

```bash
mkdir -p tests/fixtures
cp tmp/vladik_success.json tests/fixtures/vladik_success.json
```

- [ ] **Step 2: Написать failing-тест** (`tests/test_size_coverage.py`)

```python
"""Трёхуровневый корпус покрытия размеров (spec: docs/superpowers/specs/2026-09-10-size-notation-config-design.md)."""
import json
from pathlib import Path

import pytest

from process_specification_table import extract_dimensions

FIXTURES = Path(__file__).parent / "fixtures"

# Записывается при первом прогоне (Step 3) и далее только ПОВЫШАЕТСЯ.
BASELINE_VLADIK_COVERAGE = 0.0  # PINNED


def _etalon_rows() -> list:
    return json.loads((FIXTURES / "vladik_success.json").read_text(encoding="utf-8"))


def _primary_dims_match(comment: str, expected: dict) -> bool:
    """Сравнивает только геометрию сечения (D0/A0/B0), без L0/U0/R0 —
    L0 у воздуховодов — дефолтный, добавленный parse_row, а не извлечённый."""
    dims = extract_dimensions(comment or "")
    if "A0" in expected:
        keys = ("A0", "B0")
    elif "D0" in expected:
        keys = ("D0",)
    else:
        return False
    return all(dims.get(k) == pytest.approx(expected[k]) for k in keys if k in expected)


def test_vladik_baseline_coverage():
    rows = _etalon_rows()
    covered = sum(1 for r in rows if _primary_dims_match(r.get("comment", ""), r["params"]))
    rate = covered / len(rows)
    print(f"\nBASELINE_VLADIK_COVERAGE = {rate:.4f} ({covered}/{len(rows)})")
    assert rate >= BASELINE_VLADIK_COVERAGE
```

- [ ] **Step 3: Прогнать, записать baseline, запинить**

Run: `.venv/bin/python -m pytest tests/test_size_coverage.py -q -s`
Expected: PASS, в выводе строка `BASELINE_VLADIK_COVERAGE = X.XXXX (NNN/234)`. Подставить X в константу `BASELINE_VLADIK_COVERAGE` (округлить до 4 знаков вниз, например 0.9316 → 0.9315). Если rate < 0.5 — стоп, не пинить: проверить, что `comment` читается (`_etalon_rows()[0]["comment"]`) и доложить аномалию.

- [ ] **Step 4: Перепрогнать с запиненным числом**

Run: `.venv/bin/python -m pytest tests/test_size_coverage.py -q`
Expected: PASS

- [ ] **Step 5: Полный прогон + коммит**

```bash
.venv/bin/python -m pytest tests/ -q
git add tests/test_size_coverage.py tests/fixtures/vladik_success.json
git commit -m "test: pin Vladikavkaz baseline size coverage"
```

---

### Task 2: Матрица форматов записи + per-class recall

**Files:**
- Create: `tests/fixtures/size_format_matrix.yaml`
- Modify: `tests/test_size_coverage.py`

**Interfaces:**
- Consumes: `parse_size(size: str) -> Tuple[Optional[str], Dict[str, float]]`, `extract_dimensions`, `_primary_dims_match` из Task 1.
- Produces: константа `BASELINE_MATRIX_RECALL: dict` в `tests/test_size_coverage.py`.

- [ ] **Step 1: Создать фикстуру матрицы** (`tests/fixtures/size_format_matrix.yaml`)

```yaml
# Матрица вариантов записи размеров. via: какой вход используем.
# expect: первичная геометрия, которую каскад ОБЯЗАН извлечь.
round_prefix:
  - {input: "Ф315", expect: {D0: 315}, via: parse_size}
  - {input: "Ø315", expect: {D0: 315}, via: parse_size}
  - {input: "ø315", expect: {D0: 315}, via: parse_size}
  - {input: "⌀315", expect: {D0: 315}, via: parse_size}
  - {input: "Ду315", expect: {D0: 315}, via: parse_size}
  - {input: "ДН315", expect: {D0: 315}, via: parse_size}
  - {input: "DN315", expect: {D0: 315}, via: parse_size}
  - {input: "d315", expect: {D0: 315}, via: parse_size}
  - {input: "Ф315-3000", expect: {D0: 315}, via: parse_size}
round_suffix:
  - {input: "315ø", expect: {D0: 315}, via: extract_dimensions}
  - {input: "315 Ø", expect: {D0: 315}, via: extract_dimensions}
rect:
  - {input: "1250x800", expect: {A0: 1250, B0: 800}, via: parse_size}
  - {input: "1250х800", expect: {A0: 1250, B0: 800}, via: parse_size}
  - {input: "1250×800", expect: {A0: 1250, B0: 800}, via: parse_size}
  - {input: "1250*800", expect: {A0: 1250, B0: 800}, via: parse_size}
  - {input: "1250 x 800", expect: {A0: 1250, B0: 800}, via: parse_size}
  - {input: "600x400-1250", expect: {A0: 600, B0: 400}, via: parse_size}
  - {input: "600x400_1250", expect: {A0: 600, B0: 400}, via: parse_size}
  - {input: "600x400x1250", expect: {A0: 600, B0: 400}, via: parse_size}
  - {input: "Отвод 90 град 600x400", expect: {A0: 600, B0: 400}, via: extract_dimensions}
tee_round:
  - {input: "Ф315-Ф315-Ф160", expect: {D0: 315}, via: extract_dimensions}
  - {input: "Тройник 315/315/160", expect: {D0: 315}, via: extract_dimensions}
transition:
  - {input: "Переход Ф250-Ф160", expect: {D0: 250}, via: extract_dimensions}
  - {input: "Переход с Ф200 на Ф160", expect: {D0: 200}, via: extract_dimensions}
  - {input: "Переход 900x900-800x800", expect: {A0: 900, B0: 900}, via: extract_dimensions}
transition_mixed:
  - {input: "Переход 300x200-Ф200", expect: {A0: 300, B0: 200}, via: extract_dimensions}
litened:
  - {input: "LITENED 50-25 NKD", expect: {A0: 500, B0: 250}, via: extract_dimensions}
  - {input: "LITENED 40-20 NKK", expect: {A0: 400, B0: 200}, via: extract_dimensions}
knk:
  - {input: "KNK 250/6", expect: {D0: 250}, via: extract_dimensions}
length:
  - {input: "Воздуховод Ф160 L=1000", expect: {D0: 160, L0: 1000}, via: extract_dimensions}
  - {input: "Воздуховод Ф160 1000 мм", expect: {D0: 160, L0: 1000}, via: extract_dimensions}
ocr_space:
  - {input: "1 250x800", expect: {A0: 1250, B0: 800}, via: extract_dimensions}
```

(Форматы, которые каскад сейчас НЕ берёт — `315/315/160`, `315ø` через parse_size и т.п. — останутся в per-class recall ниже 1.0; это нормально, baseline честный.)

- [ ] **Step 2: Добавить тест в `tests/test_size_coverage.py`**

```python
import yaml  # добавить в импорты сверху файла

BASELINE_MATRIX_RECALL: dict = {}  # PINNED


def _check_matrix_entry(entry: dict) -> bool:
    from process_specification_table import parse_size

    if entry["via"] == "parse_size":
        _, dims = parse_size(entry["input"])
    else:
        dims = extract_dimensions(entry["input"])
    return all(dims.get(k) == pytest.approx(v) for k, v in entry["expect"].items())


def test_size_format_matrix_recall():
    matrix = yaml.safe_load((FIXTURES / "size_format_matrix.yaml").read_text(encoding="utf-8"))
    measured = {}
    for fmt, entries in matrix.items():
        ok = sum(1 for e in entries if _check_matrix_entry(e))
        measured[fmt] = round(ok / len(entries), 4)
    print(f"\nBASELINE_MATRIX_RECALL = {measured}")
    for fmt, rate in measured.items():
        assert rate >= BASELINE_MATRIX_RECALL.get(fmt, 0.0), f"{fmt}: {rate} below baseline"
```

- [ ] **Step 3: Прогнать, запинить per-class recall**

Run: `.venv/bin/python -m pytest tests/test_size_coverage.py::test_size_format_matrix_recall -q -s`
Expected: PASS, в `-s`-выводе словарь `BASELINE_MATRIX_RECALL`. Перенести его в константу как есть (не завышать). Форматы с recall < 1.0 перечислить — они пойдут в итоговый отчёт.

- [ ] **Step 4: Полный прогон + коммит**

```bash
.venv/bin/python -m pytest tests/ -q
git add tests/fixtures/size_format_matrix.yaml tests/test_size_coverage.py
git commit -m "test: add size format matrix with per-class recall baseline"
```

---

### Task 3: Мультикорпус проектов из examples/

**Files:**
- Create: `tools/collect_multi_project_sizes.py`
- Create: `tests/fixtures/multi_project_sizes.json` (генерируется скриптом)
- Modify: `tests/test_size_coverage.py`

**Interfaces:**
- Consumes: `parse_size`, `extract_dimensions`; `project_spec_xlsx.parse_project_spec_xlsx` (опционально).
- Produces: константа `BASELINE_MULTI_COVERAGE: float`; JSON схема `[{source, raw, parse_size_ok, extract_ok}]`.

- [ ] **Step 1: Написать скрипт сборки** (`tools/collect_multi_project_sizes.py`)

```python
"""Собирает сырые строки размеров из проектов examples/ в фикстуру корпуса.

Usage: .venv/bin/python tools/collect_multi_project_sizes.py
Перезаписывает tests/fixtures/multi_project_sizes.json. Тест использует
снапшот — сеть и PDF-парсеры при обычном прогоне pytest не нужны.
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from process_specification_table import extract_dimensions, parse_size  # noqa: E402

EXAMPLES = ROOT / "examples"
OUT = ROOT / "tests" / "fixtures" / "multi_project_sizes.json"

# Пермиссивный сборщик кандидатов: круг с префиксом/суффиксом, AxB, с дефисной длиной.
CANDIDATE = re.compile(
    r"(?:\d{2,5}\s*[xх×*]\s*\d{2,5}(?:\s*[-_]\s*\d{2,5})?"
    r"|(?:dn|дн|ду|d|д|ф|ø|Ø|⌀)\s*\d{2,5}(?:\s*[-_]\s*\d{2,5})?"
    r"|\d{2,5}\s*[øØ⌀](?!\s*\d))"
)


def collect_from_text(source: str, text: str, found: dict) -> None:
    for m in CANDIDATE.finditer(text):
        raw = m.group(0).strip()
        if raw and raw not in found:
            section, _ = parse_size(raw)
            found[raw] = {
                "source": source,
                "raw": raw,
                "parse_size_ok": section is not None,
                "extract_ok": bool(extract_dimensions(raw)),
            }


def main() -> None:
    found: dict = {}
    # Excel-файлы: сканируем все строковые ячейки
    try:
        import pandas as pd

        for path in sorted(EXAMPLES.rglob("*.xls*")):
            try:
                df = pd.read_excel(path, dtype=str)
            except Exception as exc:  # noqa: BLE001
                print(f"skip {path.name}: {exc}")
                continue
            for row in df.fillna("").astype(str).values:
                collect_from_text(path.name, " ".join(row), found)
    except ImportError:
        print("pandas недоступен — xls/xlsx пропущены")
    # PDF: только если pdfplumber установлен
    try:
        import pdfplumber

        for path in sorted(EXAMPLES.rglob("*.pdf")):
            try:
                with pdfplumber.open(path) as pdf:
                    text = "\n".join((p.extract_text() or "") for p in pdf.pages)
            except Exception as exc:  # noqa: BLE001
                print(f"skip {path.name}: {exc}")
                continue
            collect_from_text(path.name, text, found)
    except ImportError:
        print("pdfplumber недоступен — pdf пропущены")
    corpus = sorted(found.values(), key=lambda r: (r["source"], r["raw"]))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(corpus, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"собрано {len(corpus)} уникальных строк размеров из {EXAMPLES}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Сгенерировать фикстуру**

Run: `.venv/bin/python tools/collect_multi_project_sizes.py`
Expected: печать `собрано N уникальных строк…` (N порядка сотен; меньше 50 — проверить пути). Файл `tests/fixtures/multi_project_sizes.json` создан.

- [ ] **Step 3: Добавить тест** (в `tests/test_size_coverage.py`)

```python
BASELINE_MULTI_COVERAGE = 0.0  # PINNED


def test_multi_project_coverage():
    corpus = json.loads((FIXTURES / "multi_project_sizes.json").read_text(encoding="utf-8"))
    covered = sum(1 for r in corpus if r["parse_size_ok"] or r["extract_ok"])
    rate = covered / len(corpus)
    print(f"\nBASELINE_MULTI_COVERAGE = {rate:.4f} ({covered}/{len(corpus)})")
    assert rate >= BASELINE_MULTI_COVERAGE
```

- [ ] **Step 4: Прогнать, запинить**

Run: `.venv/bin/python -m pytest tests/test_size_coverage.py::test_multi_project_coverage -q -s`
Expected: PASS; перенести число (вниз до 4 знаков) в `BASELINE_MULTI_COVERAGE`.

- [ ] **Step 5: Полный прогон + коммит**

```bash
.venv/bin/python -m pytest tests/ -q
git add tools/collect_multi_project_sizes.py tests/fixtures/multi_project_sizes.json tests/test_size_coverage.py
git commit -m "test: add multi-project size corpus with coverage baseline"
```

---

### Task 4: config/size_notations.yaml + модуль size_notations

**Files:**
- Create: `config/size_notations.yaml`
- Create: `size_notations.py`
- Test: `tests/test_size_notations.py`

**Interfaces:**
- Produces (всё — публичный API, используется в Task 5 и позже этапом 2):
  - `get_notations() -> dict` — кэшированный YAML
  - `reload_notations() -> dict` — сброс кэша (вызывается из `config.reload_config`)
  - `prefix_group() -> str` — alternation префиксов, len-desc: `dn|дн|ду|d|д|ф|ø|⌀`
  - `word_prefix_group() -> str` — только буквенные префиксы (для `\b`-паттернов): `dn|дн|ду|d|д|ф`
  - `suffix_char_class() -> str` — `[øØ⌀]`
  - `round_diameter_pattern() -> re.Pattern` (IGNORECASE) — эквивалент `(?:\d{2,5}\s*[øØ⌀](?!\s*\d)|(?:dn|d|дн|ду|д|ф|ø|⌀)\s*\d{2,5})`
  - `separators() -> list` — `["х", "×", "*"]`
  - `litened_sizes() -> dict[str, tuple[int, int]]`
  - `litened_lengths() -> dict[str, int]`
  - `min_rect_side_mm() -> int`
  - `validate_dimensions(dims: dict, section: str | None) -> bool`

- [ ] **Step 1: Написать failing-тесты** (`tests/test_size_notations.py`)

```python
import re

import pytest

import size_notations as szn


def test_prefix_group_matches_all_variants():
    g = szn.prefix_group()
    for sample, num in [("dn315", "315"), ("дн315", "315"), ("ду315", "315"),
                        ("d315", "315"), ("д315", "315"), ("ф315", "315"),
                        ("ø315", "315"), ("⌀315", "315")]:
        m = re.match(rf"^(?:{g})\s*(\d+)$", sample, re.IGNORECASE)
        assert m and m.group(1) == num, sample


def test_round_diameter_pattern_equivalent():
    p = szn.round_diameter_pattern()
    # префиксные и суффиксные формы
    assert p.findall("Ф160")
    assert p.findall("Ø160")
    assert p.findall("160ø")
    # «80 Ø250» из ГОСТ 14918-80: 80 — НЕ суффиксный диаметр
    found = p.findall("ГОСТ 14918-80 Ø250")
    assert any("250" in f for f in found)
    assert not any(f.strip().startswith("80") for f in found)


def test_litened_tables_from_config():
    assert szn.litened_sizes()["50-25"] == (500, 250)
    assert szn.litened_lengths() == {"NKD": 1100, "NKK": 510}


def test_validate_dimensions():
    assert szn.validate_dimensions({"D0": 315.0}, "round")
    assert szn.validate_dimensions({"A0": 1250.0, "B0": 800.0}, "rectangular")
    assert not szn.validate_dimensions({"D0": 25.0}, "round")      # ниже min_side
    assert not szn.validate_dimensions({"D0": 9999.0}, "round")    # выше max_side
    assert szn.validate_dimensions({"A0": 500.0, "B0": 250.0, "L0": 1100.0}, "rectangular")
    assert not szn.validate_dimensions({"A0": 500.0, "L0": 99999.0}, "rectangular")


def test_reload_notations_clears_cache(monkeypatch, tmp_path):
    yaml_path = tmp_path / "size_notations.yaml"
    yaml_path.write_text(
        "diameter_prefixes: [\"zz\"]\ndiameter_suffixes: [\"ø\"]\n"
        "separators: [\"х\"]\ncode_tables:\n  litened:\n    sizes: {}\n    lengths: {}\n"
        "ocr:\n  min_rect_side_mm: 100\nvalidation:\n  min_side_mm: 100\n  max_side_mm: 3000\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("SPEC_TO_1C_SIZE_NOTATIONS", str(yaml_path))
    szn.reload_notations()
    try:
        assert "zz" in szn.prefix_group()
    finally:
        monkeypatch.undo()
        szn.reload_notations()
    assert "zz" not in szn.prefix_group()
```

- [ ] **Step 2: Прогнать — упадёт импортом**

Run: `.venv/bin/python -m pytest tests/test_size_notations.py -q`
Expected: FAIL (`ModuleNotFoundError: No module named 'size_notations'`)

- [ ] **Step 3: Создать конфиг** (`config/size_notations.yaml`)

```yaml
# Варианты записи размеров в проектных спецификациях.
# Канонизация: префиксные диаметры → «Ф…» (normalize_dimension_prefix),
# суффиксный «125ø» → «Ф125». Только СТРУКТУРА записи — числовые значения
# размеров сюда не добавляются (жёсткое ограничение: цифры — только из
# исходной строки детерминированным парсером).

diameter_prefixes: ["dn", "дн", "ду", "d", "д", "ф", "ø", "⌀"]
diameter_suffixes: ["ø", "Ø", "⌀"]

# Разделители сторон прямоугольного сечения, нормализуются к «x»
separators: ["х", "×", "*"]

# OCR-эвристики
ocr:
  # Сторона прямоугольника < 100 мм почти всегда = потерянный ноль
  min_rect_side_mm: 100

# Кодовые таблицы оборудования
code_tables:
  litened:
    # Код X-Y → сечение (X*100)×(Y*100) мм. Источник: air-ned.com/tovar-271.html
    sizes:
      "40-20": [400, 200]
      "50-25": [500, 250]
      "50-30": [500, 300]
      "60-30": [600, 300]
      "60-35": [600, 350]
      "70-40": [700, 400]
      "80-50": [800, 500]
      "90-50": [900, 500]
      "100-50": [1000, 500]
    lengths:
      NKD: 1100
      NKK: 510

# Диапазоны для guard-валидации извлечённых размеров (новые пути: LLM-фолбэк)
validation:
  min_side_mm: 100
  max_side_mm: 3000
```

- [ ] **Step 4: Реализовать модуль** (`size_notations.py`)

```python
"""Загрузка и сборка паттернов вариантов записи размеров из config/size_notations.yaml.

Единая точка знания о том, какие префиксы/суффиксы/разделители размеров
существуют в проектных спецификациях. Заменяет литералы, ранее
размазанные по process_specification_table.py.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Dict, List, Tuple

import yaml

DEFAULT_PATH = Path(__file__).parent / "config" / "size_notations.yaml"

_CACHE: Dict | None = None
_PATTERN_CACHE: Dict[str, re.Pattern] = {}


def _load() -> Dict:
    global _CACHE
    if _CACHE is None:
        path = Path(os.environ.get("SPEC_TO_1C_SIZE_NOTATIONS", DEFAULT_PATH))
        with open(path, "r", encoding="utf-8") as f:
            _CACHE = yaml.safe_load(f)
    return _CACHE


def get_notations() -> Dict:
    return _load()


def reload_notations() -> Dict:
    global _CACHE
    _CACHE = None
    _PATTERN_CACHE.clear()
    return _load()


def _prefixes() -> List[str]:
    return list(_load()["diameter_prefixes"])


def prefix_group() -> str:
    """Alternation префиксов диаметра, длинные первыми (regex priority)."""
    return "|".join(re.escape(p) for p in sorted(_prefixes(), key=len, reverse=True))


def word_prefix_group() -> str:
    """Буквенные префиксы — для паттернов с границей слова (\b)."""
    words = [p for p in _prefixes() if p.isalnum()]
    return "|".join(re.escape(p) for p in sorted(words, key=len, reverse=True))


def suffix_char_class() -> str:
    return "[" + "".join(re.escape(s) for s in _load()["diameter_suffixes"]) + "]"


def round_diameter_pattern() -> re.Pattern:
    """Префиксный ИЛИ суффиксный диаметр. Эквивалент исторического
    (?:\\d{2,5}\\s*[øØ⌀](?!\\s*\\d)|(?:dn|d|дн|ду|д|ф|ø|⌀)\\s*\\d{2,5})."""
    key = "round_diameter"
    if key not in _PATTERN_CACHE:
        _PATTERN_CACHE[key] = re.compile(
            rf"(?:\d{{2,5}}\s*{suffix_char_class()}(?!\s*\d)"
            rf"|(?:{prefix_group()})\s*\d{{2,5}})",
            re.IGNORECASE,
        )
    return _PATTERN_CACHE[key]


def separators() -> List[str]:
    return list(_load()["separators"])


def litened_sizes() -> Dict[str, Tuple[int, int]]:
    return {k: tuple(v) for k, v in _load()["code_tables"]["litened"]["sizes"].items()}


def litened_lengths() -> Dict[str, int]:
    return dict(_load()["code_tables"]["litened"]["lengths"])


def min_rect_side_mm() -> int:
    return int(_load()["ocr"]["min_rect_side_mm"])


def validate_dimensions(dims: Dict[str, float], section: str | None) -> bool:
    """Guard-валидация извлечённых размеров (используется LLM-фолбэком,
    в каскад не встроена — поведение каскада не меняем)."""
    cfg = _load()["validation"]
    lo, hi = int(cfg["min_side_mm"]), int(cfg["max_side_mm"])
    for key, value in (dims or {}).items():
        if not isinstance(value, (int, float)):
            return False
        if key == "L0":
            if not (10 <= value <= hi * 4):
                return False
        elif key == "U0":
            if not (0 < value <= 180):
                return False
        elif key.startswith(("A", "B", "D", "R")):
            if not (lo <= value <= hi):
                return False
    return True
```

- [ ] **Step 5: Прогнать тесты**

Run: `.venv/bin/python -m pytest tests/test_size_notations.py -q`
Expected: PASS (5 тестов)

- [ ] **Step 6: Полный прогон + коммит**

```bash
.venv/bin/python -m pytest tests/ -q
git add config/size_notations.yaml size_notations.py tests/test_size_notations.py
git commit -m "feat: size_notations module + config for size notation variants"
```

---

### Task 5: Рефакторинг process_specification_table.py на size_notations

**Files:**
- Modify: `process_specification_table.py` (стр. 33, 78-91, 220-224, 230-234, 237-243, 407-445, 448-564, 356, 389-391)
- Modify: `config.py` (`reload_config`)

**Interfaces:**
- Consumes: весь API из Task 4.
- Produces: без новых API; поведение — идентично (контроль: все тесты + три baseline-теста зелёные).

- [ ] **Step 1: Импорт и удаление LITENED-хардкода**

В `process_specification_table.py`:
1. После стр. 33 добавить: `import size_notations as szn`
2. Удалить блок стр. 78-91 (`LITENED_SILENCER_SIZES = {...}`) целиком, вместо него комментарий:
   ```python
   # Справочник кодов LITENED и длины — в config/size_notations.yaml (size_notations).
   ```
3. `_OCR_MIN_RECT_SIDE = 100` (стр. 356) удалить; комментарий оставить, добавив: «порог — size_notations.min_rect_side_mm()».

- [ ] **Step 2: Заменить точки с литералами** (каждую — точным Edit):

a) `is_round`, стр. 220 и 223:
```python
    if re.search(rf"(?:{szn.prefix_group()})\s*\d+", text, re.IGNORECASE):
        return True
    # Суффиксный диаметр «125ø» (U+00F8)
    if re.search(rf"\d{{2,5}}\s*{szn.suffix_char_class()}", text):
        return True
```

b) `normalize_dimension_prefix`, стр. 232-233 (замена символов Ø/ø/⌀ — структурная, остаётся; буквенные префиксы — из конфига):
```python
    token = token.replace("Ø", "Ф").replace("ø", "Ф").replace("⌀", "Ф")
    token = re.sub(rf"^(?:{szn.word_prefix_group()})\b", "ф", token, flags=re.IGNORECASE)
```

c) `_collapse_size_spaces`, стр. 239:
```python
    for sep in szn.separators():
        text = text.replace(sep, "x")
```
(заменяет три литеральных `.replace`; остальной код функции без изменений)

d) `extract_size_token`, стр. 267:
```python
    m = re.search(rf"\b(\d{{2,5}})\s*{szn.suffix_char_class()}(?!\s*\d)", text)
```
и стр. 271:
```python
    m = re.search(rf"(?<![\w.])(?:(?:{szn.prefix_group()})\s*\d{{2,5}}(?:\s*[-_]\s*\d{{2,5}})?)", text, re.IGNORECASE)
```
(эквивалент прежнего литерала `(?:DN|D|ДН|ДУ|Д|Ф|Ø|⌀)` — IGNORECASE + len-desc порядок в prefix_group)

e) `parse_size`, стр. 413 (сепараторы):
```python
    for sep in szn.separators():
        size = size.replace(sep, "x")
```
и стр. 431/436 (префиксы — теперь ПОЛНЫЙ набор из конфига, включая ø/⌀; это надмножество прежнего `dn|d|дн|ду|д|ф`, покрытие растёт, существующие тесты не затрагивает):
```python
    m = re.match(rf"^(?:{szn.prefix_group()})\s*(\d{{2,5}})\s*[-_]\s*(\d{{2,5}})$", size)
    ...
    m = re.match(rf"^(?:{szn.prefix_group()})\s*(\d{{2,5}})$", size)
```

f) `extract_dimensions`, стр. 459 (сепараторы — как в (e)); стр. 469-477:
```python
    round_matches = szn.round_diameter_pattern().findall(text)
    round_tokens = [
        float(re.search(r"\d{2,5}", m.replace("ø", "").replace("Ø", "").replace("⌀", "")).group(0))
        for m in round_matches
    ]
```
(обработка суффиксных токенов — структурная, остаётся); стр. 545-549:
```python
        sizes = szn.litened_sizes()
        if code in sizes:
            a0, b0 = sizes[code]
            dims["A0"] = a0
            dims["B0"] = b0
            dims["L0"] = float(szn.litened_lengths().get(variant, 1100))
```
(дефолт 1100 сохраняет прежнее поведение для неизвестного варианта)

g) `correct_ocr_size`, стр. 389 и 391: `_OCR_MIN_RECT_SIDE` → `szn.min_rect_side_mm()` (два вхождения).

- [ ] **Step 3: Проверить, что литералов префиксов не осталось**

Run: `.venv/bin/python -c "
import re
src = open('process_specification_table.py', encoding='utf-8').read()
hits = [l for l in src.splitlines() if re.search(r'dn\|d\|дн|øØ⌀', l)]
print('\n'.join(hits) if hits else 'OK: литералов префиксов не осталось')"`
Expected: `OK: литералов префиксов не осталось` (строки с `"ø", "Ø", "⌀"` в `.replace(...)` структурной нормализации допустимы — если grep их покажет, убедиться, что это только replace-символы и suffix-токены).

- [ ] **Step 4: Hook в reload_config** (`config.py`)

```python
def reload_config(path: str | os.PathLike | None = None) -> Dict[str, Any]:
    """Reload configuration and update the cached copy."""
    global _CONFIG
    _CONFIG = load_config(path)
    try:  # сбросить кэш паттернов размеров, если модуль загружен
        import size_notations

        size_notations.reload_notations()
    except ImportError:
        pass
    return _CONFIG
```

- [ ] **Step 5: Полный прогон — всё зелёное, baselines не упали**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: весь сьют зелёный, включая `test_size_coverage.py` (3 теста) и `test_vladikavkaz_kp1090.py`, `test_process_specification_table.py`, `test_techdept_rules.py`. Если baseline-тест упал — регрессия рефакторинга: откатить правку точки (f) или (e), сравнить паттерн со старым литералом через `szn.round_diameter_pattern().pattern`.

- [ ] **Step 6: Коммит**

```bash
git add process_specification_table.py config.py
git commit -m "refactor: read size notation patterns from size_notations config"
```

---

### Task 6: Итоговая верификация и отчёт

**Files:**
- Modify: `docs/superpowers/specs/2026-09-10-size-notation-config-design.md` не трогать; отчёт — комментарием в конец `tests/test_size_coverage.py`? Нет — отдельным выводом исполнителю (родительскому агенту), не файлом.

- [ ] **Step 1: Собрать цифры для отчёта**

```bash
.venv/bin/python -m pytest tests/ -q
.venv/bin/python -m pytest tests/test_size_coverage.py -q -s  # напечатают baseline recall по классам
```

- [ ] **Step 2: Прогнать матрицу «вручную» для списка непокрытых**

Мини-скриптом (не коммитить) прогнать все форматы из матрицы и выписать классы с recall < 1.0 и конкретные падающие входы — это список «не покрыто каскадом» для отчёта.

- [ ] **Step 3: Финальный коммит (если Step 2 ничего не изменил — пропустить)**

---

## Self-Review (выполнено при написании)

- **Spec coverage:** п.1 (конфиг+рефакторинг) → Tasks 4-5; п.3 (корпус) → Tasks 1-3; уровень C-процесс (логирование отклонений) → этап 2, другой план; validate_dimensions → Task 4 (guard для этапа 2, в каскад не встроен — соответствует spec).
- **Placeholders:** нет TBD/TODO; все код-блоки полные.
- **Type consistency:** `szn.*` сигнатуры в Task 4 совпадают с использованием в Task 5; константы `BASELINE_*` единообразны; `_primary_dims_match` определена в Task 1 и не переименовывается.
- **Известное намеренное изменение поведения:** `parse_size` теперь принимает префиксы ø/⌀ (надмножество) — существующие тесты не затрагивает, покрытие растёт; зафиксировано в Task 5 (e).
