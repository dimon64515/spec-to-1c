"""Тесты прайса Бориса (presale/boris_prices.py)."""
import re
from decimal import Decimal
from pathlib import Path

import pytest

from presale.boris_prices import BorisPriceTable, MATERIAL_ALIASES

FIXTURES = Path(__file__).parent / "fixtures" / "boris"
RECT = FIXTURES / "rect.xlsx"
ROUND = FIXTURES / "round.xlsx"

_ROW_MATERIAL_RE = re.compile(r"(aisi\s*430|aisi\s*304|08пс|оц|черн)", re.IGNORECASE)


def test_lookup_rounds_thickness_up():
    t = BorisPriceTable.from_xlsx(RECT, ROUND)
    r = t.lookup(material="оцинкованная сталь", thickness=Decimal("0.65"), shape="round")
    assert r is not None and r.thickness_used == Decimal("0.7") and r.rounded is True
    assert r.price == Decimal("1520")  # кругляк, рулон оц. 0.70, прямошовный L=1250


def test_lookup_above_grid_returns_none():
    t = BorisPriceTable.from_xlsx(RECT, ROUND)
    assert t.lookup(material="оцинковка", thickness=Decimal("1.2"), shape="round") is None


def test_material_alias_unknown_returns_none():
    t = BorisPriceTable.from_xlsx(RECT, ROUND)
    assert t.lookup(material="титан", thickness=Decimal("0.7"), shape="round") is None


def test_override_beats_xlsx():
    t = BorisPriceTable.from_xlsx(RECT, ROUND, overrides={("aisi304", Decimal("0.8"), "rect", "standard"): Decimal("9000")})
    r = t.lookup(material="AISI 304", thickness=Decimal("0.8"), shape="rect")
    assert r.price == Decimal("9000")


def test_exact_thickness_not_rounded():
    t = BorisPriceTable.from_xlsx(RECT, ROUND)
    r = t.lookup(material="оцинк.", thickness=Decimal("0.7"), shape="round")
    assert r is not None and r.thickness_used == Decimal("0.7") and r.rounded is False


def test_duplicate_rows_roll_wins(caplog):
    """В rect на (ocsink, 0.70) две строки: рулон и штрипс.

    Пустые ячейки штрипса не затирают цены рулона (standard=1310);
    конфликт J/K (рулон 2430/4510 vs штрипс 2931/700) разрешается в пользу
    первичной формы «рулон/лист» независимо от порядка строк, с log.warning.
    """
    with caplog.at_level("WARNING", logger="presale.boris_prices"):
        t = BorisPriceTable.from_xlsx(RECT, ROUND)
    r = t.lookup(material="08пс", thickness=Decimal("0.7"), shape="rect", product_kind="standard")
    assert r is not None and r.price == Decimal("1310")
    r = t.lookup(material="08пс", thickness=Decimal("0.7"), shape="rect", product_kind="fitting")
    assert r is not None and r.price == Decimal("2430")
    r = t.lookup(material="08пс", thickness=Decimal("0.7"), shape="rect", product_kind="nonstandard")
    assert r is not None and r.price == Decimal("4510")
    assert any("конфликт цен" in rec.message and "ocsink" in rec.message for rec in caplog.records)


def _reversed_rows_xlsx(src: Path, dst: Path) -> None:
    """Копия xlsx с перевернутым порядком строк прайса (строки материалов)."""
    import openpyxl

    wb = openpyxl.load_workbook(src, data_only=True)
    ws = wb.worksheets[0]
    for rng in list(ws.merged_cells.ranges):
        ws.unmerge_cells(str(rng))
    header_row = next(
        r for r in range(1, 11)
        if any(isinstance(c.value, str) and c.value.strip().casefold() == "материал" for c in ws[r])
    )
    material_col = next(
        c.column for c in ws[header_row]
        if isinstance(c.value, str) and c.value.strip().casefold() == "материал"
    )
    price_rows = [
        r for r in range(header_row + 1, ws.max_row + 1)
        if _ROW_MATERIAL_RE.search(str(ws.cell(row=r, column=material_col).value or ""))
    ]
    values = [[c.value for c in ws[r]] for r in price_rows]
    for target_r, vals in zip(price_rows, reversed(values)):
        for j, v in enumerate(vals):
            ws.cell(row=target_r, column=1 + j).value = v
    wb.save(dst)


def test_merge_deterministic_under_row_reorder(tmp_path):
    """Перестановка строк прайса не меняет итоговую сетку: рулон бьёт штрипс."""
    reversed_rect = tmp_path / "rect_reversed.xlsx"
    _reversed_rows_xlsx(RECT, reversed_rect)
    t = BorisPriceTable.from_xlsx(reversed_rect, ROUND)
    r = t.lookup(material="08пс", thickness=Decimal("0.7"), shape="rect", product_kind="standard")
    assert r is not None and r.price == Decimal("1310")
    r = t.lookup(material="08пс", thickness=Decimal("0.7"), shape="rect", product_kind="fitting")
    assert r is not None and r.price == Decimal("2430")
    r = t.lookup(material="08пс", thickness=Decimal("0.7"), shape="rect", product_kind="nonstandard")
    assert r is not None and r.price == Decimal("4510")


def test_overrides_copied_and_coerced_to_decimal():
    overrides = {("aisi304", Decimal("0.8"), "rect", "standard"): 9000}
    t = BorisPriceTable.from_xlsx(RECT, ROUND, overrides=overrides)
    overrides[("aisi304", Decimal("0.8"), "rect", "standard")] = 1  # внешняя мутация не влияет
    r = t.lookup(material="AISI 304", thickness=Decimal("0.8"), shape="rect")
    assert r.price == Decimal("9000")


def test_spiral_kind_not_mapped():
    """Спирально-навивной L=3000 в v1 не мапится: product_kind='spiral' → None."""
    t = BorisPriceTable.from_xlsx(RECT, ROUND)
    assert t.lookup(material="AISI 304", thickness=Decimal("0.8"), shape="round", product_kind="spiral") is None


def test_unknown_kind_returns_none():
    t = BorisPriceTable.from_xlsx(RECT, ROUND)
    assert t.lookup(material="AISI 304", thickness=Decimal("0.8"), shape="round", product_kind="bogus") is None


def test_black_steel_alias():
    assert "black" in MATERIAL_ALIASES.values()
