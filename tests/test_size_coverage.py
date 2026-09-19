"""Трёхуровневый корпус покрытия размеров (spec: docs/superpowers/specs/2026-09-10-size-notation-config-design.md)."""
import json
from pathlib import Path

import pytest
import yaml

from process_specification_table import extract_dimensions, parse_size

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


BASELINE_MATRIX_RECALL: dict = {
    "round_prefix": 0.6667,
    "round_suffix": 1.0,
    "rect": 1.0,
    "tee_round": 0.5,
    "transition": 1.0,
    "transition_mixed": 1.0,
    "litened": 1.0,
    "knk": 1.0,
    "length": 1.0,
    "ocr_space": 0.0,
}  # PINNED


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
    assert set(measured) == set(BASELINE_MATRIX_RECALL), "matrix classes changed — re-pin baselines"
    for fmt, rate in measured.items():
        assert rate >= BASELINE_MATRIX_RECALL.get(fmt, 0.0), f"{fmt}: {rate} below baseline"


BASELINE_MULTI_COVERAGE = 1.0  # PINNED


def test_multi_project_coverage():
    corpus = json.loads((FIXTURES / "multi_project_sizes.json").read_text(encoding="utf-8"))
    covered = sum(
        1
        for r in corpus
        if parse_size(r["raw"])[0] is not None or extract_dimensions(r["raw"])
    )
    rate = covered / len(corpus)
    print(f"\nBASELINE_MULTI_COVERAGE = {rate:.4f} ({covered}/{len(corpus)})")
    assert rate >= BASELINE_MULTI_COVERAGE


def test_vladik_baseline_coverage():
    rows = _etalon_rows()
    covered = sum(1 for r in rows if _primary_dims_match(r.get("comment", ""), r["params"]))
    rate = covered / len(rows)
    print(f"\nBASELINE_VLADIK_COVERAGE = {rate:.4f} ({covered}/{len(rows)})")
    assert rate >= BASELINE_VLADIK_COVERAGE
