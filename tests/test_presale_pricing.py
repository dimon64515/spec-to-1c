"""Тесты ценообразования предварительного расчёта (presale/pricing.py)."""
from decimal import Decimal
from pathlib import Path
from typing import Optional

import pytest

from presale.boris_prices import BorisPriceTable
from presale.db import PresaleDB
from presale.models import Category, EstimateItem, PriceStatus
from presale.pricing import price_items

FIXTURES = Path(__file__).parent / "fixtures" / "boris"


class StubBackend:
    """Stub PriceBackend: отвечает ценами из словаря, запоминает вызовы."""

    def __init__(self, prices: Optional[dict] = None):
        self._prices = prices or {}
        self.called_with: list[list[str]] = []

    def search_1c(self, names: list[str]) -> dict[str, Decimal]:
        self.called_with.append(list(names))
        return {n: p for n, p in self._prices.items() if n in names}


class StubMarket:
    """Stub MarketPricer: одна цена на все позиции или None."""

    def __init__(self, price: Optional[Decimal] = None):
        self.price = price
        self.calls: list[str] = []

    def market_price(self, name: str) -> Optional[Decimal]:
        self.calls.append(name)
        return self.price


@pytest.fixture()
def boris():
    return BorisPriceTable.from_xlsx(FIXTURES / "rect.xlsx", FIXTURES / "round.xlsx")


@pytest.fixture()
def db(tmp_path):
    return PresaleDB(tmp_path / "t.db")


@pytest.fixture()
def stub_backend():
    return StubBackend({"Решетка LITENED 70-40": Decimal("3500")})


@pytest.fixture()
def stub_backend_empty():
    return StubBackend()


@pytest.fixture()
def stub_market():
    return StubMarket()


@pytest.fixture()
def stub_market_empty():
    return StubMarket(None)


def _item(**kwargs):
    kwargs.setdefault("row_id", "1")
    kwargs.setdefault("unit", "шт")
    kwargs.setdefault("qty", Decimal("1"))
    kwargs.setdefault("status", PriceStatus.MISSING)
    return EstimateItem(**kwargs)


def test_material_priced_by_boris_rounded(stub_backend, stub_market, db, boris):
    items = [EstimateItem(row_id="1", category=Category.MATERIALS, name="Воздуховод оцинк. 0.65",
                          unit="м²", qty=Decimal("41"), material="оцинкованная сталь",
                          thickness=Decimal("0.65"), status=PriceStatus.MISSING)]
    out = price_items(items, boris=boris, backend=stub_backend, market=stub_market, db=db)
    assert out[0].status == PriceStatus.ROUNDED and out[0].price_source == "boris"
    assert out[0].computed_total() == out[0].internal_price * Decimal("41")


def test_equipment_uses_batched_1c(stub_backend, stub_market, db, boris):
    items = [EstimateItem(row_id="1", category=Category.EQUIPMENT, name="Решетка LITENED 70-40",
                          unit="шт", qty=Decimal("4"), status=PriceStatus.MISSING)]
    out = price_items(items, boris=boris, backend=stub_backend, market=stub_market, db=db)
    assert stub_backend.called_with == [["Решетка LITENED 70-40"]]  # один батч-вызов
    assert out[0].price_source == "1c"
    assert out[0].internal_price == Decimal("3500")


def test_works_market_times_1_5(stub_backend, stub_market, db, boris):
    stub_market.price = Decimal("2000")
    items = [EstimateItem(row_id="1", category=Category.WORKS, name="Монтаж вентиляции",
                          unit="м²", qty=Decimal("10"), status=PriceStatus.MISSING)]
    out = price_items(items, boris=boris, backend=stub_backend, market=stub_market, db=db)
    assert out[0].internal_price == Decimal("3000") and out[0].price_source == "market_works"


def test_missing_everywhere_is_red(stub_backend_empty, stub_market_empty, db, boris):
    items = [EstimateItem(row_id="1", category=Category.EQUIPMENT, name="Неведомая хрень",
                          unit="шт", qty=Decimal("1"), status=PriceStatus.MISSING)]
    out = price_items(items, boris=boris, backend=stub_backend_empty, market=stub_market_empty, db=db)
    assert out[0].status == PriceStatus.MISSING and out[0].internal_price is None
    assert out[0].status_note == "нет цены: борис/1с/рынок"


def test_manual_price_beats_1c(stub_backend, stub_market, db, boris):
    db.set_manual_price("Пластина крепежная", Decimal("700"), "шт", 1)
    items = [_item(category=Category.MATERIALS, name="Пластина крепежная",
                   material="титан", thickness=Decimal("0.5"))]
    out = price_items(items, boris=boris, backend=stub_backend, market=stub_market, db=db)
    assert out[0].status == PriceStatus.MANUAL and out[0].price_source == "manual"
    assert out[0].internal_price == Decimal("700")


def test_material_without_thickness_is_red(stub_backend, stub_market, db, boris):
    items = [_item(category=Category.MATERIALS, name="Воздуховод оцинк.",
                   material="оцинкованная сталь")]
    out = price_items(items, boris=boris, backend=stub_backend, market=stub_market, db=db)
    assert out[0].status == PriceStatus.MISSING and out[0].internal_price is None
    assert out[0].status_note == "не указана толщина"


def test_spiral_without_price_is_red(stub_backend, stub_market, db, boris):
    items = [_item(category=Category.MATERIALS, name="Воздуховод спиральный",
                   material="оцинкованная сталь", thickness=Decimal("0.7"),
                   product_kind="spiral")]
    out = price_items(items, boris=boris, backend=stub_backend, market=stub_market, db=db)
    assert out[0].status == PriceStatus.MISSING
    assert out[0].status_note == "спирально-навивной: нет цены в прайсе"


def test_small_stuff_via_market(stub_backend_empty, stub_market, db, boris):
    stub_market.price = Decimal("12")
    items = [_item(category=Category.MATERIALS, name="Болт анкерный М10")]
    out = price_items(items, boris=boris, backend=stub_backend_empty, market=stub_market, db=db)
    assert out[0].status == PriceStatus.OK and out[0].price_source == "market"
    assert out[0].internal_price == Decimal("12")


def test_work_rate_hit_uses_row_coefficient(stub_backend, stub_market, db, boris):
    db.upsert_work_rate("Монтаж воздуховодов", "м²", Decimal("800"), Decimal("1.5"))
    items = [_item(category=Category.WORKS, name="Монтаж воздуховодов", unit="м²")]
    out = price_items(items, boris=boris, backend=stub_backend, market=stub_market, db=db)
    assert out[0].status == PriceStatus.OK and out[0].price_source == "work_rates"
    assert out[0].internal_price == Decimal("1200")
    assert not stub_market.calls  # рынок не дёргаем при совпадении расценки


def test_round_shape_derived_from_name(stub_backend, stub_market, db, boris):
    items = [_item(category=Category.MATERIALS, name="Воздуховод Ø200",
                   material="оцинкованная сталь", thickness=Decimal("0.65"))]
    out = price_items(items, boris=boris, backend=stub_backend, market=stub_market, db=db)
    assert out[0].price_source == "boris" and out[0].internal_price == Decimal("1520")


def test_1c_batch_collects_all_unpriced(stub_backend, stub_market, db, boris):
    stub_backend._prices["Клапан КВР"] = Decimal("5000")
    stub_backend._prices["Лист оцинк."] = Decimal("800")
    items = [
        _item(row_id="1", category=Category.EQUIPMENT, name="Решетка LITENED 70-40"),
        _item(row_id="2", category=Category.EQUIPMENT, name="Клапан КВР"),
        _item(row_id="3", category=Category.MATERIALS, name="Лист оцинк.",
              material="титан", thickness=Decimal("0.5")),
    ]
    out = price_items(items, boris=boris, backend=stub_backend, market=stub_market, db=db)
    assert stub_backend.called_with == [["Решетка LITENED 70-40", "Клапан КВР", "Лист оцинк."]]
    assert [it.price_source for it in out] == ["1c", "1c", "1c"]


def test_input_items_not_mutated(stub_backend, stub_market, db, boris):
    items = [EstimateItem(row_id="1", category=Category.MATERIALS, name="Воздуховод оцинк. 0.65",
                          unit="м²", qty=Decimal("41"), material="оцинкованная сталь",
                          thickness=Decimal("0.65"), status=PriceStatus.MISSING)]
    out = price_items(items, boris=boris, backend=stub_backend, market=stub_market, db=db)
    assert items[0].internal_price is None and items[0].status == PriceStatus.MISSING
    assert out[0] is not items[0]


def test_transition_without_type_searched_as_type1(db, boris, stub_market_empty):
    """Переход без явного типа ищется в 1С как «… тип 1»; имя строки не меняется."""
    backend = StubBackend(
        {"Переход 500x300/Ф400-300 тип 1": Decimal("1955.18")}
    )
    items = [_item(category=Category.EQUIPMENT, name="Переход 500x300/Ф400-300")]
    out = price_items(items, boris=boris, backend=backend,
                      market=stub_market_empty, db=db)
    assert backend.called_with[0] == ["Переход 500x300/Ф400-300 тип 1"]
    assert out[0].internal_price == Decimal("1955.18")
    assert out[0].price_source == "1c"
    assert out[0].name == "Переход 500x300/Ф400-300"


def test_transition_with_explicit_type_untouched(db, boris, stub_market_empty):
    """Явный «тип N» в наименовании перехода не перезаписывается дефолтом."""
    backend = StubBackend({"Переход 500x300/Ф400-300 тип 2": Decimal("2500")})
    items = [_item(category=Category.EQUIPMENT,
                   name="Переход 500x300/Ф400-300 тип 2")]
    out = price_items(items, boris=boris, backend=backend,
                      market=stub_market_empty, db=db)
    assert backend.called_with[0] == ["Переход 500x300/Ф400-300 тип 2"]
    assert out[0].internal_price == Decimal("2500")


def test_round_and_rect_transitions_default_type1(db, boris, stub_market_empty):
    """Дефолт «тип 1» действует на переходы обеих форм (круглые и прямоугольные)."""
    backend = StubBackend()
    items = [
        _item(row_id="1", category=Category.EQUIPMENT,
              name="Переход с прямоугольного на круглое сечения 672x396/Ф500-300"),
        _item(row_id="2", category=Category.EQUIPMENT,
              name="Переход прямоугольного сечения 378x378/500x500-500"),
    ]
    price_items(items, boris=boris, backend=backend,
                market=stub_market_empty, db=db)
    assert backend.called_with[0] == [
        "Переход с прямоугольного на круглое сечения 672x396/Ф500-300 тип 1",
        "Переход прямоугольного сечения 378x378/500x500-500 тип 1",
    ]


def test_non_transition_names_not_modified(db, boris, stub_market_empty):
    """Обычные позиции идут в 1С с исходным именем (дефолт только для переходов)."""
    backend = StubBackend()
    items = [
        _item(row_id="1", category=Category.EQUIPMENT, name="Решетка LITENED 70-40"),
        _item(row_id="2", category=Category.EQUIPMENT,
              name="Зонт вытяжной островной (тип1) 250/350x1000/100"),
    ]
    price_items(items, boris=boris, backend=backend,
                market=stub_market_empty, db=db)
    assert backend.called_with[0] == [
        "Решетка LITENED 70-40",
        "Зонт вытяжной островной (тип1) 250/350x1000/100",
    ]
