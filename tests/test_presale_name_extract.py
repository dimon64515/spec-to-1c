"""Тесты извлечения материала/толщины/типа из наименования позиции."""
from decimal import Decimal

from presale.models import Category, EstimateItem, PriceStatus
from presale.name_extract import enrich_items, extract_from_name


def test_extract_ocsink_spiral_round():
    r = extract_from_name(
        "Воздуховоды из оцинкованной стали, жесткие, спирально-навивные, "
        "прямой участок, толщина 0,55 мм, диаметр 160 мм")
    assert r["material"] == "ocsink"
    assert r["thickness"] == Decimal("0.55")
    assert r["shape"] == "round"
    assert r["product_kind"] == "spiral"


def test_extract_fitting_rect():
    r = extract_from_name(
        "Изделия фасонные для воздуховодов из оцинкованной стали с шиной "
        "и уголками, толщина 0,7 мм, периметр 1200 мм")
    assert r["material"] == "ocsink"
    assert r["thickness"] == Decimal("0.7")
    assert r["shape"] == "rect"
    assert r["product_kind"] == "fitting"


def test_extract_aisi304_beats_generic_nerzh():
    r = extract_from_name("Воздуховод круглый из нержавеющей стали AISI 304, "
                          "толщина 0.8 мм")
    assert r["material"] == "aisi304"


def test_no_thickness_word_no_guess():
    # «диаметр 200 мм» — НЕ толщина
    r = extract_from_name("Дефлектор статический из оцинкованной стали, "
                          "диаметр 200 мм")
    assert r["material"] == "ocsink"
    assert r["thickness"] is None
    assert r["product_kind"] == "fitting"


def test_enrich_does_not_override_explicit():
    item = EstimateItem(row_id="1", category=Category.MATERIALS,
                        name="Воздуховод из оцинкованной стали, толщина 0.7 мм",
                        unit="м²", qty=Decimal("10"),
                        material="aisi430", thickness=Decimal("0.8"),
                        status=PriceStatus.MISSING)
    out = enrich_items([item])
    assert out[0].material == "aisi430" and out[0].thickness == Decimal("0.8")


def test_enrich_skips_works_and_equipment():
    works = EstimateItem(row_id="1", category=Category.WORKS, name="Монтаж",
                         unit="м²", qty=Decimal("1"), status=PriceStatus.MISSING)
    out = enrich_items([works])
    assert out[0] is works


def test_boris_spiral_column_priced():
    from pathlib import Path
    from presale.boris_prices import BorisPriceTable
    root = Path(__file__).parent / "fixtures" / "boris"
    table = BorisPriceTable.from_xlsx(root / "rect.xlsx", root / "round.xlsx")
    # кругляк, рулон оц. 0.70, спирально-навивной L=3000 → 1100 из колонки F
    r = table.lookup(material="оцинкованная сталь", thickness=Decimal("0.70"),
                     shape="round", product_kind="spiral")
    assert r is not None and r.price == Decimal("1100")
    # 0.55: у рулона спиральная ячейка пустая — берётся цена штрипса 860
    r = table.lookup(material="оцинкованная сталь", thickness=Decimal("0.55"),
                     shape="round", product_kind="spiral")
    assert r is not None and r.price == Decimal("860")
    # прямоугольный прайс спиральной колонки не имеет → None
    assert table.lookup(material="оцинкованная сталь", thickness=Decimal("0.70"),
                        shape="rect", product_kind="spiral") is None
