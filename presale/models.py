"""Модели данных предварительного расчёта.

Все денежные значения и площади — Decimal; округление ROUND_HALF_UP
до 2 знаков выполняется только на границе отчёта, без float в расчётах.
"""
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
from typing import Optional

from pydantic import BaseModel

CENT = Decimal("0.01")


class Category(str, Enum):
    """Категории строк расчёта (термины Игоря)."""

    WORKS = "works"
    MATERIALS = "materials"
    EQUIPMENT = "equipment"


class PriceStatus(str, Enum):
    """Статус цены строки."""

    OK = "ok"
    ROUNDED = "rounded"
    MISSING = "missing"
    MANUAL = "manual"
    OVERRIDDEN = "overridden"


class EstimateItem(BaseModel):
    """Строка расчёта: рыночная позиция с подобранной внутренней ценой."""

    row_id: str
    category: Category
    name: str
    unit: str
    qty: Decimal
    market_price: Optional[Decimal] = None
    material: Optional[str] = None
    thickness: Optional[Decimal] = None
    product_kind: Optional[str] = None
    length_class: Optional[str] = None
    internal_price: Optional[Decimal] = None
    status: PriceStatus
    status_note: str = ""
    price_source: str = ""

    def computed_total(self) -> Optional[Decimal]:
        """Сумма строки = qty * internal_price; None, если цены нет."""
        if self.internal_price is None:
            return None
        return (self.qty * self.internal_price).quantize(CENT, rounding=ROUND_HALF_UP)


class Summary(BaseModel):
    """Итоги расчёта по рынку и по внутренним ценам."""

    by_category: dict[Category, dict[str, Decimal]]
    market_total: Decimal
    internal_total: Decimal
    delta: Decimal
    margin_pct: Optional[Decimal]


def _money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def build_summary(items: list[EstimateItem]) -> Summary:
    """Собирает Summary из строк расчёта.

    Маржа = (internal − market) / internal * 100; None, если internal_total == 0.
    """
    by_category: dict[Category, dict[str, Decimal]] = {
        category: {"market": Decimal("0.00"), "internal": Decimal("0.00")}
        for category in Category
    }
    for item in items:
        totals = by_category[item.category]
        if item.market_price is not None:
            totals["market"] += item.qty * item.market_price
        total = item.computed_total()
        if total is not None:
            totals["internal"] += total

    market_total = _money(sum(
        (totals["market"] for totals in by_category.values()), Decimal("0")
    ))
    internal_total = _money(sum(
        (totals["internal"] for totals in by_category.values()), Decimal("0")
    ))
    delta = _money(internal_total - market_total)
    margin_pct = (
        _money((internal_total - market_total) / internal_total * Decimal("100"))
        if internal_total != 0
        else None
    )
    return Summary(
        by_category=by_category,
        market_total=market_total,
        internal_total=internal_total,
        delta=delta,
        margin_pct=margin_pct,
    )
