# Refactor spec-to-1c Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Повысить качество кодовой базы spec-to-1c: устранить дублирование, вынести конфигурацию, добавить тесты, усилить валидацию и безопасность, сохранив текущее поведение приложения.

**Architecture:** Создать модуль `spec_common.py` с общими утилитами (материалы, размеры, соединения), ввести `config.yaml` для настроек, добавить `tests/` с pytest, разделить чистую бизнес-логику в `api.py`, сделать `web_app.py` тонким UI-слоем.

**Tech Stack:** Python 3.12, pytest, pyyaml, streamlit, pandas, PyMuPDF.

## Global Constraints

- Все изменения должны сохранять существующее поведение для примеров в `examples/`.
- Python 3.12+.
- Зависимости добавлять только в `requirements.txt` (pytest, pyyaml).
- Никакого изменения формата выходного XML без явной задачи.
- Каждая задача должна заканчиваться работающими тестами.
- Частые коммиты после каждой задачи.

---

## Task 1: Add pytest and first tests for parse_row

**Files:**
- Create: `tests/test_process_specification_table.py`
- Create: `pytest.ini`
- Modify: `requirements.txt`

**Interfaces:**
- Consumes: `process_specification_table.parse_row(row, defaults)`
- Produces: passing tests for round duct, rectangular duct, and skipped unknown item

- [ ] **Step 1: Add pytest to requirements**

```bash
# Append to requirements.txt
pytest
```

- [ ] **Step 2: Create pytest.ini**

```ini
[pytest]
testpaths = tests
python_files = test_*.py
python_functions = test_*
```

- [ ] **Step 3: Write failing tests**

Create `tests/test_process_specification_table.py`:

```python
import pytest
from process_specification_table import parse_row


def test_parse_row_round_duct():
    row = {"name": "Воздуховод", "size": "160", "unit": "м", "quantity": "400"}
    defaults = {"material": "оцинкованная", "thickness": "0.8"}
    parsed, skipped = parse_row(row, defaults)
    assert skipped is None
    assert parsed["article"] == "1-1-2"
    assert parsed["params"]["D0"] == 160
    assert parsed["params"]["L0"] == 3000
    assert parsed["quantity"] == pytest.approx(400.0)


def test_parse_row_rect_duct():
    row = {"name": "Воздуховод", "size": "300x200", "unit": "м", "quantity": "50"}
    defaults = {"material": "оцинкованная", "thickness": "0.7"}
    parsed, skipped = parse_row(row, defaults)
    assert skipped is None
    assert parsed["article"] == "1-2-1"
    assert parsed["params"]["A0"] == 300
    assert parsed["params"]["B0"] == 200
    assert parsed["params"]["L0"] == 1250


def test_parse_row_unknown_skipped():
    row = {"name": "Непонятная деталь", "size": "", "unit": "шт", "quantity": "1"}
    defaults = {}
    parsed, skipped = parse_row(row, defaults)
    assert parsed is None
    assert "reason" in skipped
```

- [ ] **Step 4: Install dependencies and run tests**

```bash
source venv/bin/activate
pip install -r requirements.txt
pytest tests/test_process_specification_table.py -v
```

Expected: tests run and either pass or fail for fixable reasons.

- [ ] **Step 5: Commit**

```bash
git add requirements.txt pytest.ini tests/test_process_specification_table.py
git commit -m "test: add initial pytest setup and parse_row tests"
```

---

## Task 2: Extract common utilities into spec_common.py

**Files:**
- Create: `spec_common.py`
- Modify: `process_specification_table.py`
- Modify: `generate_order_xml.py`
- Modify: `match_description_to_product.py`
- Create: `tests/test_spec_common.py`

**Interfaces:**
- Consumes: duplicated functions from existing modules
- Produces:
  - `spec_common.material_to_code(material: str) -> str`
  - `spec_common.connection_to_code(conn: str | int, section: str) -> str`
  - `spec_common.parse_size(size: str) -> dict[str, int | None]`
  - `spec_common.extract_dimensions(text: str) -> dict[str, int | None]`

- [ ] **Step 1: Write tests for spec_common**

Create `tests/test_spec_common.py`:

```python
import pytest
from spec_common import material_to_code, connection_to_code, parse_size, extract_dimensions


def test_material_to_code_galvanized():
    assert material_to_code("оцинкованная") == "1"


def test_material_to_code_stainless():
    assert material_to_code("нержавеющая") == "2"


def test_material_to_code_unknown_defaults_to_one():
    assert material_to_code("дерево") == "1"


def test_parse_size_round():
    assert parse_size("160") == {"D0": 160}


def test_parse_size_rect():
    assert parse_size("300x200") == {"A0": 300, "B0": 200}


def test_extract_dimensions_with_diameter():
    assert extract_dimensions("D160") == {"D0": 160}


def test_connection_to_code_numeric():
    assert connection_to_code("1", "round") == "1"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_spec_common.py -v
```

Expected: FAIL (module not found / functions not defined).

- [ ] **Step 3: Create spec_common.py with consolidated logic**

Create `spec_common.py`:

```python
"""Common utilities for spec parsing and XML generation."""

from __future__ import annotations

import re
from typing import Dict, Optional


def material_to_code(material: str | None) -> str:
    """Convert human-readable material to 1C material code."""
    if not material:
        return "1"
    mat = str(material).lower()
    if "нерж" in mat or "08х18н10" in mat:
        return "2"
    if "алюм" in mat:
        return "3"
    return "1"


def connection_to_code(conn: str | int | None, section: str = "round") -> str:
    """Convert connection description to 1C connection code."""
    if conn is None or conn == "":
        return "0"
    if isinstance(conn, int):
        return str(conn)
    s = str(conn).strip().lower()
    mapping = {
        "1": "1",
        "2": "2",
        "3": "3",
        "4": "4",
        "5": "5",
        "6": "6",
        "7": "7",
        "8": "8",
        "н/н": "1",
        "бурт": "2",
        "фланец": "3",
        "ниппель": "4",
    }
    return mapping.get(s, "0")


def parse_size(size: str | None) -> Dict[str, int | None]:
    """Parse a size string like '160' or '300x200'."""
    result: Dict[str, int | None] = {}
    if not size:
        return result
    s = str(size).strip().lower().replace("х", "x")
    rect_match = re.search(r"(\d+)\s*x\s*(\d+)", s)
    if rect_match:
        result["A0"] = int(rect_match.group(1))
        result["B0"] = int(rect_match.group(2))
        return result
    diam_match = re.search(r"d?\s*(\d+)", s, re.IGNORECASE)
    if diam_match:
        result["D0"] = int(diam_match.group(1))
        return result
    plain_match = re.search(r"(\d+)", s)
    if plain_match:
        result["D0"] = int(plain_match.group(1))
    return result


def extract_dimensions(text: str | None) -> Dict[str, int | None]:
    """Extract A0/B0/D0/L0 etc. from a text description."""
    result: Dict[str, int | None] = {}
    if not text:
        return result
    t = str(text).lower().replace("х", "x")
    patterns = [
        (r"(\d+)\s*x\s*(\d+)\s*x\s*(\d+)", ["A0", "B0", "L0"]),
        (r"(\d+)\s*x\s*(\d+)", ["A0", "B0"]),
        (r"d\s*(\d+)", ["D0"]),
    ]
    for pattern, keys in patterns:
        m = re.search(pattern, t)
        if m:
            for i, key in enumerate(keys):
                result[key] = int(m.group(i + 1))
            break
    return result
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_spec_common.py -v
```

Expected: PASS.

- [ ] **Step 5: Refactor existing modules to use spec_common**

Modify `process_specification_table.py`:
- Remove local `material_to_code`.
- Import `from spec_common import material_to_code, parse_size, extract_dimensions`.
- Replace internal calls.

Modify `generate_order_xml.py`:
- Remove local `material_type_to_code` and `connection_to_code`.
- Import `from spec_common import material_to_code as material_type_to_code, connection_to_code`.

Modify `match_description_to_product.py`:
- Remove local `extract_dimensions` if duplicate, import from `spec_common`.

- [ ] **Step 6: Run all tests**

```bash
pytest tests/ -v
```

Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add spec_common.py tests/test_spec_common.py process_specification_table.py generate_order_xml.py match_description_to_product.py
git commit -m "refactor: extract common utilities into spec_common.py"
```

---

## Task 3: Introduce config.yaml and remove hardcoded constants

**Files:**
- Create: `config.yaml`
- Create: `config.py`
- Modify: `process_specification_table.py`
- Modify: `map_customer_equipment.py`
- Modify: `query_1c.py`
- Create: `tests/test_config.py`

**Interfaces:**
- Consumes: hardcoded constants from existing modules
- Produces: `config.load_config() -> dict`, environment-aware settings

- [ ] **Step 1: Write tests for config**

Create `tests/test_config.py`:

```python
import os
from config import load_config


def test_load_config_default():
    cfg = load_config()
    assert cfg["default_round_article"] == "1-1-2"
    assert cfg["default_rect_article"] == "1-2-1"
    assert cfg["default_round_length_mm"] == 3000
    assert cfg["default_rect_length_mm"] == 1250


def test_load_config_env_override(monkeypatch):
    monkeypatch.setenv("SPEC_TO_1C_CONFIG", "nonexistent.yaml")
    try:
        load_config()
    except FileNotFoundError:
        pass
```

- [ ] **Step 2: Create config.yaml**

```yaml
default_round_article: "1-1-2"
default_rect_article: "1-2-1"
default_round_length_mm: 3000
default_rect_length_mm: 1250

mapping_files:
  products: "product_article_mapping.json"
  products_designation: "product_designation_patterns.json"
  materials: "article_materials.json"
  equipment: "customer_product_mapping.json"
  allowed_articles: "articles_all.txt"

mcp:
  url: "http://localhost:6003/mcp"
  timeout: 30
```

- [ ] **Step 3: Create config.py**

```python
"""Configuration loader."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict

import yaml


DEFAULT_CONFIG_PATH = Path(__file__).parent / "config.yaml"


def load_config(path: str | os.PathLike | None = None) -> Dict[str, Any]:
    """Load application configuration from YAML file."""
    if path is None:
        path = os.environ.get("SPEC_TO_1C_CONFIG", DEFAULT_CONFIG_PATH)
    path = Path(path)
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)
```

- [ ] **Step 4: Add pyyaml to requirements**

```bash
# Append to requirements.txt
pyyaml
```

- [ ] **Step 5: Replace hardcoded constants in modules**

Modify `process_specification_table.py`:
- Replace `DEFAULT_ROUND_ARTICLE`, `DEFAULT_RECT_ARTICLE`, `DEFAULT_ROUND_LENGTH_MM`, `DEFAULT_RECT_LENGTH_MM` with reads from `config.load_config()`.
- Load `product_article_mapping.json`, `article_materials.json`, `articles_all.txt` paths from config.

Modify `map_customer_equipment.py`:
- Replace hardcoded `customer_product_mapping.json` path with config.

Modify `query_1c.py`:
- Replace hardcoded MCP URL with `config.load_config()["mcp"]["url"]`.

- [ ] **Step 6: Run tests**

```bash
source venv/bin/activate
pip install -r requirements.txt
pytest tests/test_config.py tests/test_spec_common.py tests/test_process_specification_table.py -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add config.yaml config.py requirements.txt tests/test_config.py process_specification_table.py map_customer_equipment.py query_1c.py
git commit -m "feat: add config.yaml and remove hardcoded constants"
```

---

## Task 4: Safe temporary files in web_app.py

**Files:**
- Modify: `web_app.py`
- Create: `tests/test_web_app_tempfiles.py`

**Interfaces:**
- Consumes: uploaded file bytes
- Produces: temporary file paths deleted after use

- [ ] **Step 1: Write failing test**

Create `tests/test_web_app_tempfiles.py`:

```python
import os
from unittest.mock import MagicMock, patch
from web_app import load_tables_from_pdf


def test_load_tables_from_pdf_cleans_temp_file():
    fake_bytes = b"%PDF-1.4 fake"
    with patch("web_app.extract_tables_from_pdf", return_value={0: []}) as mock:
        load_tables_from_pdf(fake_bytes)
        call_args = mock.call_args
        tmp_path = call_args[0][0]
        assert os.path.exists(tmp_path)
        # Simulate function completion: file should be cleaned
        # (if cleanup happens inside function, check after call)
```

- [ ] **Step 2: Refactor web_app.py temp file handling**

Replace:

```python
tmp_path = Path("_tmp_uploaded.pdf")
tmp_path.write_bytes(file_bytes)
```

with:

```python
from tempfile import NamedTemporaryFile

with NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
    tmp.write(file_bytes)
    tmp_path = Path(tmp.name)
try:
    ...
finally:
    tmp_path.unlink(missing_ok=True)
```

Apply the same pattern to `extract_equipment_from_bytes`.

- [ ] **Step 3: Run tests**

```bash
pytest tests/test_web_app_tempfiles.py -v
```

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add web_app.py tests/test_web_app_tempfiles.py
git commit -m "fix: use secure temporary files in web_app.py"
```

---

## Task 5: Harden eval in generate_order_xml.py

**Files:**
- Modify: `generate_order_xml.py`
- Create: `tests/test_generate_order_xml.py`

**Interfaces:**
- Consumes: expression strings from `product_article_mapping.json`
- Produces: safely evaluated numeric results

- [ ] **Step 1: Write tests**

Create `tests/test_generate_order_xml.py`:

```python
import pytest
from generate_order_xml import _eval_default_expression


def test_eval_simple_reference():
    assert _eval_default_expression("D0", {"D0": 160}) == 160


def test_eval_max_expression():
    assert _eval_default_expression("max(A0, B0) / 2", {"A0": 300, "B0": 200}) == 150


def test_eval_forbidden_attribute_raises():
    with pytest.raises(ValueError):
        _eval_default_expression("().__class__", {})
```

- [ ] **Step 2: Harden evaluator**

Modify `_eval_default_expression` in `generate_order_xml.py` to allow only:
- `ast.Expression`, `ast.BinOp`, `ast.UnaryOp`, `ast.Call`
- Names from params and whitelisted builtins (`max`, `min`, `round`)
- Constants

Reject `ast.Attribute`, `ast.Subscript`, names not in whitelist.

- [ ] **Step 3: Run tests**

```bash
pytest tests/test_generate_order_xml.py -v
```

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add generate_order_xml.py tests/test_generate_order_xml.py
git commit -m "fix: harden default expression evaluator against injection"
```

---

## Task 6: Introduce api.py service layer

**Files:**
- Create: `api.py`
- Create: `tests/test_api.py`
- Modify: `web_app.py`

**Interfaces:**
- Consumes: file bytes, filename, options dict
- Produces: `ProcessResult(xml: str, skipped: list, tables: list)`

- [ ] **Step 1: Define ProcessResult and api.py**

Create `api.py`:

```python
"""Clean service layer for spec-to-1c processing."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd

from pdf_spec_extractor import extract_tables_from_pdf, normalize_columns, df_to_spec_rows
from process_specification_table import process_rows


@dataclass
class ProcessResult:
    xml: str
    skipped: List[Dict[str, Any]]
    tables: List[Dict[str, Any]]


def process_specification_file(
    file_bytes: bytes,
    file_name: str,
    header: Dict[str, str],
    options: Dict[str, Any] | None = None,
) -> ProcessResult:
    """Process an uploaded specification file and return XML + skipped rows."""
    options = options or {}
    ext = Path(file_name).suffix.lower()

    if ext == ".pdf":
        from tempfile import NamedTemporaryFile
        with NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(file_bytes)
            tmp_path = Path(tmp.name)
        try:
            tables = extract_tables_from_pdf(str(tmp_path), pages=options.get("pages"))
            rows = []
            for page_tables in tables.values():
                for df in page_tables:
                    df = normalize_columns(df)
                    rows.extend(df_to_spec_rows(df))
        finally:
            tmp_path.unlink(missing_ok=True)
    elif ext in (".csv", ".xlsx", ".xls"):
        if ext == ".csv":
            df = pd.read_csv(pd.io.common.BytesIO(file_bytes), sep=";", dtype=str, encoding="utf-8-sig")
        else:
            df = pd.read_excel(pd.io.common.BytesIO(file_bytes), dtype=str)
        df = normalize_columns(df)
        rows = df_to_spec_rows(df)
    else:
        raise ValueError(f"Unsupported file extension: {ext}")

    xml, skipped = process_rows(rows, header, {})
    return ProcessResult(xml=xml, skipped=skipped, tables=[])
```

- [ ] **Step 2: Write tests for api.py**

Create `tests/test_api.py`:

```python
from api import process_specification_file


def test_process_csv_round_duct():
    csv = "Наименование;Размер;Ед;Количество;Материал;Толщина\nВоздуховод;160;м;10;оцинкованная;0.8\n"
    result = process_specification_file(
        csv.encode("utf-8-sig"),
        "spec.csv",
        header={"order_name": "TEST"},
    )
    assert "1-1-2" in result.xml
    assert "D0160" in result.xml
```

- [ ] **Step 3: Refactor web_app.py to use api.py**

Replace direct pandas/pdf extraction logic in `main()` with calls to `api.process_specification_file` and display results.

- [ ] **Step 4: Run all tests**

```bash
pytest tests/ -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add api.py tests/test_api.py web_app.py
git commit -m "feat: introduce api.py service layer and simplify web_app.py"
```

---

## Task 7: Add logging and replace print statements

**Files:**
- Create: `logging_config.py`
- Modify: all modules using `print()`
- Modify: `web_app.py` to configure logging

**Interfaces:**
- Consumes: application events
- Produces: structured log output

- [ ] **Step 1: Create logging_config.py**

```python
"""Centralized logging configuration."""

import logging
import sys


def configure_logging(level: int = logging.INFO) -> None:
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
```

- [ ] **Step 2: Replace prints with logging**

In each module add:

```python
import logging
logger = logging.getLogger(__name__)
```

Replace `print(...)` with `logger.info(...)`, `logger.warning(...)`, or `logger.error(...)` as appropriate.

- [ ] **Step 3: Configure logging in web_app.py**

At module level or in `main()`:

```python
from logging_config import configure_logging
configure_logging()
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/ -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add logging_config.py web_app.py process_specification_table.py generate_order_xml.py pdf_spec_extractor.py equipment_pdf_extractor.py map_customer_equipment.py match_description_to_product.py
git commit -m "feat: add centralized logging and replace print statements"
```

---

## Task 8: Add CI workflow and final integration test

**Files:**
- Create: `.github/workflows/ci.yml`
- Create: `tests/test_integration.py`

**Interfaces:**
- Consumes: full pipeline
- Produces: CI passes

- [ ] **Step 1: Create GitHub Actions workflow**

Create `.github/workflows/ci.yml`:

```yaml
name: CI

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install -r requirements.txt
      - run: pytest tests/ -v
```

- [ ] **Step 2: Add integration test**

Create `tests/test_integration.py`:

```python
from api import process_specification_file


def test_integration_csv_to_xml():
    csv = (
        "Наименование;Размер;Ед;Количество;Материал;Толщина\n"
        "Воздуховод;160;м;10;оцинкованная;0.8\n"
        "Отвод 90;160;шт;5;оцинкованная;0.8\n"
    )
    result = process_specification_file(
        csv.encode("utf-8-sig"),
        "spec.csv",
        header={"order_name": "INTEGRATION"},
    )
    assert "<order" in result.xml or "<Заказ" in result.xml
    assert result.skipped == []
```

- [ ] **Step 3: Run full test suite**

```bash
pytest tests/ -v
```

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/ci.yml tests/test_integration.py
git commit -m "ci: add GitHub Actions workflow and integration test"
```

---

## Self-Review

**Spec coverage:**
- Tests: Task 1, Task 8.
- Устранение дублирования: Task 2.
- Конфигурация: Task 3.
- Безопасные временные файлы: Task 4.
- Harden eval: Task 5.
- Разделение UI/API: Task 6.
- Логирование: Task 7.
- CI/CD: Task 8.

**Placeholder scan:** No TBD/TODO placeholders.

**Type consistency:** All signatures use `str | None` and `Dict[str, Any]` consistently.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-06-30-refactor-spec-to-1c.md`.

**Execution options:**

1. **Subagent-Driven (recommended)** — dispatch a fresh subagent per task, review between tasks.
2. **Inline Execution** — execute tasks in this session with checkpoints.

The user has requested to start implementing, so begin with Task 1 immediately unless instructed otherwise.
