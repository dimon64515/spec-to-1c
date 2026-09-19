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
