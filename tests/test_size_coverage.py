"""Трёхуровневый корпус покрытия размеров (spec: docs/superpowers/specs/2026-09-10-size-notation-config-design.md)."""
import json
from pathlib import Path

import pytest

from process_specification_table import extract_dimensions

FIXTURES = Path(__file__).parent / "fixtures"

# Записывается при первом прогоне (Step 3) и далее только ПОВЫШАЕТСЯ.
BASELINE_VLADIK_COVERAGE = 0.9273  # PINNED


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
