"""Тесты прайса Бориса (presale/boris_prices.py)."""
from decimal import Decimal
from pathlib import Path

import pytest

from presale.boris_prices import BorisPriceTable, MATERIAL_ALIASES

FIXTURES = Path(__file__).parent / "fixtures" / "boris"
RECT = FIXTURES / "rect.xlsx"
ROUND = FIXTURES / "round.xlsx"


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


def test_empty_cell_for_kind_falls_to_other_row():
    """В rect на (ocsink, 0.70) две строки: рулон и штрипс.

    Пустые ячейки штрипса не затирают цены рулона (standard=1310),
    а заполненные J/K штрипса перекрывают значения рулона (последняя строка
    с непустой ячейкой побеждает).
    """
    t = BorisPriceTable.from_xlsx(RECT, ROUND)
    r = t.lookup(material="08пс", thickness=Decimal("0.7"), shape="rect", product_kind="standard")
    assert r is not None and r.price == Decimal("1310")
    r = t.lookup(material="08пс", thickness=Decimal("0.7"), shape="rect", product_kind="fitting")
    assert r is not None and r.price == Decimal("2931")


def test_spiral_kind_not_mapped():
    """Спирально-навивной L=3000 в v1 не мапится: product_kind='spiral' → None."""
    t = BorisPriceTable.from_xlsx(RECT, ROUND)
    assert t.lookup(material="AISI 304", thickness=Decimal("0.8"), shape="round", product_kind="spiral") is None


def test_unknown_kind_returns_none():
    t = BorisPriceTable.from_xlsx(RECT, ROUND)
    assert t.lookup(material="AISI 304", thickness=Decimal("0.8"), shape="round", product_kind="bogus") is None


def test_black_steel_alias():
    assert "black" in MATERIAL_ALIASES.values()
