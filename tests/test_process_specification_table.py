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
    # 400 м при стандартной длине 3000 мм → 133 шт
    assert parsed["quantity"] == 133


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
