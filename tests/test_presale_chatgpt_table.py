"""Тесты парсера книги ChatGPT (presale/chatgpt_table.py).

Фикстура — реальная книга Игоря «ИИ Рыночный_расчёт_ЛСР_26-09-07.xlsx»:
6 листов, строки позиций на листах Работы/Материалы/Оборудование
начинаются с 7-й, колонки маппятся по заголовку 6-й строки.
"""
from decimal import Decimal
from pathlib import Path

import openpyxl

from presale.chatgpt_table import (
    COLUMN_ALIASES,
    SHEET_ALIASES,
    parse_chatgpt_workbook,
)
from presale.models import Category, PriceStatus

FIXTURE = (
    Path(__file__).parent / "fixtures" / "igor" / "ИИ Рыночный_расчёт_ЛСР_26-09-07.xlsx"
)


def test_parse_real_igor_workbook():
    items = parse_chatgpt_workbook(FIXTURE)
    assert {i.category for i in items} == {
        Category.WORKS,
        Category.MATERIALS,
        Category.EQUIPMENT,
    }
    works = [i for i in items if i.category == Category.WORKS]
    assert works[0].name  # «Установка воздухонагревателей…»
    assert works[0].qty > 0 and works[0].unit == "шт"
    assert works[0].market_price is not None
    assert works[0].status == PriceStatus.MISSING
    # row_id берётся из колонки «ID» (Л1, Л2, …), а не из номера строки.
    assert works[0].row_id == "Л1"


def test_quantity_and_price_are_decimal():
    items = parse_chatgpt_workbook(FIXTURE)
    item = next(i for i in items if i.category == Category.MATERIALS)
    assert isinstance(item.qty, Decimal)
    assert isinstance(item.market_price, Decimal)


def test_unknown_sheets_are_skipped_without_crash():
    items = parse_chatgpt_workbook(FIXTURE)
    # «Итог», «Исходная ЛСР», «Цены и источники» не должны порождать позиций.
    assert all(
        i.category in {Category.WORKS, Category.MATERIALS, Category.EQUIPMENT}
        for i in items
    )


def test_renamed_columns_still_parse(tmp_path):
    # Лист «работы» в нижнем регистре, колонка «Кол-во» вместо «Количество».
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "работы"
    ws.append(["Наименование", "Ед. изм.", "Кол-во", "Цена"])
    ws.append(["Монтаж воздуховода", "м²", 10.5, 750])
    path = tmp_path / "renamed.xlsx"
    wb.save(path)

    items = parse_chatgpt_workbook(path)
    assert len(items) == 1
    assert items[0].category == Category.WORKS
    assert items[0].name == "Монтаж воздуховода"
    assert items[0].qty == Decimal("10.5")
    assert items[0].market_price == Decimal("750")


def test_unknown_columns_do_not_crash(tmp_path):
    # Неизвестные колонки пропускаются; строка с именем всё равно парсится.
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Материалы"
    ws.append(["ID", "Наименование", "Фасонка", "Количество", "Цена за ед., ₽"])
    ws.append(["Ц1", "Отвод 90°", "да", 3, 1200])
    path = tmp_path / "unknown_cols.xlsx"
    wb.save(path)

    items = parse_chatgpt_workbook(path)
    assert len(items) == 1
    assert items[0].row_id == "Ц1"
    assert items[0].qty == Decimal("3")


def test_uncached_formula_price_becomes_missing(tmp_path):
    # Книга без вычисленного кэша: формулу читать нельзя → цена None + 🔴.
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Оборудование"
    ws.append(["Наименование", "Ед.", "Количество", "Цена за ед., ₽"])
    ws.append(["Вентилятор", "шт", 2, "=B2*100"])
    path = tmp_path / "formula.xlsx"
    wb.save(path)

    items = parse_chatgpt_workbook(path)
    assert len(items) == 1
    assert items[0].market_price is None
    assert items[0].status == PriceStatus.MISSING
    assert "🔴" in items[0].status_note


def test_rows_without_name_or_qty_and_price_are_skipped(tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Работы"
    ws.append(["Наименование", "Ед.", "Количество", "Цена за ед., ₽"])
    ws.append(["Работы", None, None, None])  # строка-заголовок
    ws.append([None, None, None, None])  # пустая строка
    ws.append(["Без количества и цены", "шт", None, None])  # не валидна
    ws.append(["Валидная позиция", "шт", 1, 500])
    path = tmp_path / "rows.xlsx"
    wb.save(path)

    items = parse_chatgpt_workbook(path)
    assert [i.name for i in items] == ["Валидная позиция"]


def test_row_id_falls_back_to_sheet_and_row(tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Работы"
    ws.append(["Наименование", "Количество", "Цена"])
    ws.append(["Позиция без ID", 1, 100])
    path = tmp_path / "noid.xlsx"
    wb.save(path)

    items = parse_chatgpt_workbook(path)
    assert items[0].row_id == "Работы:2"


def test_accepts_bytes():
    items = parse_chatgpt_workbook(FIXTURE.read_bytes())
    assert items


def test_aliases_are_exported():
    assert set(SHEET_ALIASES) == set(Category)
    for key in ("name", "unit", "qty", "market_price"):
        assert key in COLUMN_ALIASES
