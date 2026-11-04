"""Тесты моделей предварительного расчёта presale."""
from decimal import Decimal

from presale.models import Category, EstimateItem, PriceStatus, build_summary


def _item(**kw):
    base = dict(
        row_id="1",
        category=Category.MATERIALS,
        name="Воздуховод",
        unit="м²",
        qty=Decimal("10"),
        status=PriceStatus.OK,
    )
    base.update(kw)
    return EstimateItem(**base)


def test_computed_total_none_without_internal_price():
    assert _item(market_price=Decimal("1000")).computed_total() is None


def test_computed_total_qty_times_internal_price():
    total = _item(qty=Decimal("3.5"), internal_price=Decimal("1200")).computed_total()
    assert total == Decimal("4200.00")


def test_summary_math_and_margin():
    items = [
        EstimateItem(row_id="1", category=Category.MATERIALS, name="Воздуховод", unit="м²",
                     qty=Decimal("10"), market_price=Decimal("1000"), internal_price=Decimal("800"),
                     status=PriceStatus.OK),
        EstimateItem(row_id="2", category=Category.WORKS, name="Монтаж", unit="шт",
                     qty=Decimal("2"), market_price=None, internal_price=Decimal("5000"),
                     status=PriceStatus.MANUAL),
    ]
    s = build_summary(items)
    assert s.market_total == Decimal("10000")
    assert s.internal_total == Decimal("18000")
    assert s.delta == Decimal("8000")
    assert s.by_category[Category.WORKS]["internal"] == Decimal("10000")


def test_summary_margin_none_when_no_internal():
    assert build_summary([]).margin_pct is None


def test_summary_margin_value():
    s = build_summary([
        _item(market_price=Decimal("1000"), internal_price=Decimal("800")),
    ])
    # (800 - 1000) / 800 * 100 = -25
    assert s.margin_pct == Decimal("-25.00")
