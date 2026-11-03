"""Тесты Excel-выгрузок предварительного расчёта presale/report.py."""
import io
from decimal import Decimal

import openpyxl

from presale.models import Category, EstimateItem, PriceStatus, build_summary
from presale.report import build_customer_xlsx, build_internal_xlsx

VAT_RATE = Decimal("0.22")

ITEMS = [
    EstimateItem(
        row_id="1", category=Category.WORKS, name="Монтаж воздуховодов",
        unit="м²", qty=Decimal("15.5"), market_price=Decimal("450"),
        internal_price=Decimal("380"), status=PriceStatus.OK,
        price_source="прайс",
    ),
    EstimateItem(
        row_id="2", category=Category.MATERIALS, name="Воздуховод прямоугольный",
        unit="м²", qty=Decimal("10"), market_price=Decimal("0.1"),
        internal_price=Decimal("0.1"), status=PriceStatus.ROUNDED,
        status_note="0.7 → 0.8", price_source="прайс",
    ),
    EstimateItem(
        row_id="3", category=Category.MATERIALS, name="Отвод 90°",
        unit="шт", qty=Decimal("4"), market_price=None,
        internal_price=None, status=PriceStatus.MISSING,
        status_note="нет в прайсе", price_source="",
    ),
    EstimateItem(
        row_id="4", category=Category.EQUIPMENT, name="Клапан КСД",
        unit="шт", qty=Decimal("2"), market_price=Decimal("12000"),
        internal_price=Decimal("10500"), status=PriceStatus.MANUAL,
        status_note="ручная цена", price_source="ручной ввод",
    ),
]

META = {"order": "Т-100", "file": "spec.pdf"}


def _wb(data: bytes):
    return openpyxl.load_workbook(io.BytesIO(data))


def test_internal_has_5_plus_5_columns():
    data = build_internal_xlsx(ITEMS, META)
    ws = _wb(data)["Материалы"]
    headers = [c.value for c in ws[1]]
    assert headers == ["Наименование", "Ед.", "Количество", "Рыночная цена", "Рыночная сумма",
                       "Наша цена", "Наша сумма", "Источник цены", "Статус", "Примечание"]


def test_customer_export_has_no_internal_data():
    data = build_customer_xlsx(ITEMS, META)
    wb = _wb(data)
    text = " ".join(str(c.value) for ws in wb for row in ws.iter_rows() for c in row)
    assert "Рыночн" not in text and "маржа" not in text.lower() and "Источник" not in text
    assert [c.value for c in wb["Материалы"][1]] == ["Наименование", "Ед.", "Количество", "Цена", "Сумма"]


def test_totals_use_decimal_not_float():
    data = build_internal_xlsx(ITEMS, META)
    ws = _wb(data)["Итог"]
    summary = build_summary(ITEMS)
    # ищем строку «Итого» и проверяем, что значение квантизовано как Decimal,
    # а не «сырой» float (0.1*10 в float даёт 1.0000000000000002)
    itogo = None
    for row in ws.iter_rows(min_row=2, values_only=True):
        if row and row[0] == "Итого":
            itogo = row
            break
    assert itogo is not None
    internal_cell = itogo[2]
    assert internal_cell == float(summary.internal_total)
    assert internal_cell == round(float(summary.internal_total), 2)
    # значение кратно копейке и совпадает с Decimal-расчётом точно
    assert Decimal(str(internal_cell)) == summary.internal_total


def test_internal_sheets_only_nonempty_categories_plus_itog():
    wb = _wb(build_internal_xlsx(
        [i for i in ITEMS if i.category == Category.MATERIALS], META))
    assert wb.sheetnames == ["Итог", "Материалы"]


def test_vat_22_in_itog():
    wb = _wb(build_internal_xlsx(ITEMS, META))
    ws = wb["Итог"]
    itogo = None
    for row in ws.iter_rows(min_row=2, values_only=True):
        if row and row[0] == "Итого":
            itogo = row
            break
    assert itogo is not None
    base = float(build_summary(ITEMS).internal_total)
    expected_vat = float((build_summary(ITEMS).internal_total * VAT_RATE).quantize(Decimal("0.01")))
    assert itogo[4] == expected_vat
    assert itogo[5] == round(base + expected_vat, 2)


def test_customer_price_uses_internal_and_statuses_rendered():
    wb = _wb(build_internal_xlsx(ITEMS, META))
    ws = wb["Материалы"]
    rows = {r[0]: r for r in ws.iter_rows(min_row=2, values_only=True)}
    rounded = rows["Воздуховод прямоугольный"]
    assert rounded[8] == "🟡 округление толщины"
    missing = rows["Отвод 90°"]
    assert missing[8] == "🔴 нет цены"
    assert missing[6] is None  # наша сумма без цены — пусто

    works = {r[0]: r for r in wb["Работы"].iter_rows(min_row=2, values_only=True)}
    assert works["Монтаж воздуховодов"][8] == "🟢 ок"
    equip = {r[0]: r for r in wb["Оборудование"].iter_rows(min_row=2, values_only=True)}
    assert equip["Клапан КСД"][8] == "🟢 ручная"


def test_customer_rows_have_internal_price_and_total():
    wb = _wb(build_customer_xlsx(ITEMS, META))
    ws = wb["Работы"]
    rows = list(ws.iter_rows(min_row=2, values_only=True))
    assert rows[0] == ("Монтаж воздуховодов", "м²", 15.5, 380.0, 5890.0)
