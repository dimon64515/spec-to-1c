"""Парсер книги ChatGPT: листы Работы/Материалы/Оборудование → EstimateItem.

Книга — рыночный расчёт Игоря: каждая категория на своём листе, заголовки
колонок в одной из первых строк, дальше строки позиций. Ячейки количества и
цены могут содержать формулы — читаются только вычисленные значения кэша
(`data_only=True`); если кэш пуст, число из строки формулы не извлекается:
qty/price становятся None, строка получает статус MISSING с пометкой 🔴.

Парсер толерантен: неизвестные листы пропускаются, неизвестные колонки —
игнорируются, строка валидна, если у неё есть имя и (количество или цена).
Все числа — Decimal (float не используется).
"""
import io
import logging
import re
from decimal import Decimal
from pathlib import Path
from typing import Optional, Union

from openpyxl import load_workbook
from openpyxl.worksheet.worksheet import Worksheet

from presale.models import Category, EstimateItem, PriceStatus

log = logging.getLogger(__name__)

# Синонимы имён листов (сравнение после нормализации, casefold).
SHEET_ALIASES: dict[Category, tuple[str, ...]] = {
    Category.WORKS: ("работы", "работа", "трудозатраты", "works"),
    Category.MATERIALS: ("материалы", "материал", "materials"),
    Category.EQUIPMENT: ("оборудование", "оборуд", "equipment"),
}

# Ключ колонки EstimateItem → синонимы заголовка (после нормализации).
COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "row_id": ("id",),
    "name": ("наименование", "наименование позиции", "название"),
    "unit": ("ед", "ед изм", "единица", "единица измерения", "unit"),
    "qty": ("количество", "кол-во", "колво", "объем", "объём", "qty"),
    "market_price": ("цена за ед", "цена за единицу", "цена", "price"),
}

# Максимальный номер строки, в которой ищется строка заголовков.
_HEADER_SCAN_ROWS = 15

_SPACE_RE = re.compile(r"\s+")


def _normalize(text: object) -> str:
    """casefold, пробелы схлопнуты, точки/запятые/знаки ₽ убраны."""
    if text is None:
        return ""
    folded = str(text).casefold().replace("\xa0", " ")
    folded = re.sub(r"[.,;:₽]", "", folded)
    return _SPACE_RE.sub(" ", folded).strip()


def _to_decimal(value: object) -> Optional[Decimal]:
    """int/float/str → Decimal; иначе None. Float берётся через str."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    text = str(value).strip().replace("\xa0", " ").replace(" ", "")
    if not text:
        return None
    try:
        return Decimal(text.replace(",", "."))
    except ValueError:
        return None


def _match_category(sheet_name: str) -> Optional[Category]:
    normalized = _normalize(sheet_name)
    for category, aliases in SHEET_ALIASES.items():
        if any(normalized == alias for alias in aliases):
            return category
    return None


def _match_column(header: str) -> Optional[str]:
    normalized = _normalize(header)
    for key, aliases in COLUMN_ALIASES.items():
        if any(normalized == alias for alias in aliases):
            return key
    return None


def _find_header_map(ws: Worksheet) -> Optional[tuple[int, dict[int, str]]]:
    """Ищет строку заголовков → (номер строки, номер колонки → ключ поля)."""
    for row in ws.iter_rows(min_row=1, max_row=_HEADER_SCAN_ROWS):
        column_map: dict[int, str] = {}
        for cell in row:
            key = _match_column(cell.value)
            if key is not None and key not in column_map.values():
                column_map[cell.column] = key
        if "name" in column_map.values() and (
            "qty" in column_map.values() or "market_price" in column_map.values()
        ):
            return row[0].row, column_map
    return None


def _cell_value(row: tuple, column_map: dict[int, str], key: str) -> object:
    for column, field in column_map.items():
        if field == key:
            return row[column - 1].value if column - 1 < len(row) else None
    return None


def _parse_sheet(ws: Worksheet, category: Category) -> list[EstimateItem]:
    header = _find_header_map(ws)
    if header is None:
        log.warning("Лист %r: строка заголовков не найдена, пропуск", ws.title)
        return []
    header_row, column_map = header

    items: list[EstimateItem] = []
    for row in ws.iter_rows(min_row=header_row + 1):
        name = _cell_value(row, column_map, "name")
        if name is None:
            continue
        name = str(name).strip()
        qty = _to_decimal(_cell_value(row, column_map, "qty"))
        market_price = _to_decimal(_cell_value(row, column_map, "market_price"))
        # Строка валидна, если есть имя и (количество или цена).
        if not name or (qty is None and market_price is None):
            continue
        if qty is None:
            # qty обязателен в EstimateItem; без него строку не представить.
            log.warning(
                "Лист %r, строка %s: нет количества, строка пропущена",
                ws.title, row[0].row,
            )
            continue

        raw_id = _cell_value(row, column_map, "row_id")
        row_id = str(raw_id).strip() if raw_id is not None else ""
        if not row_id:
            row_id = f"{ws.title}:{row[0].row}"

        unit_raw = _cell_value(row, column_map, "unit")
        unit = str(unit_raw).strip() if unit_raw is not None else ""

        status_note = "" if market_price is not None else "🔴 нет цены"
        items.append(
            EstimateItem(
                row_id=row_id,
                category=category,
                name=name,
                unit=unit,
                qty=qty,
                market_price=market_price,
                status=PriceStatus.MISSING,
                status_note=status_note,
            )
        )
    return items


def parse_chatgpt_workbook(path: Union[str, Path, bytes]) -> list[EstimateItem]:
    """Читает книгу ChatGPT и возвращает позиции трёх категорий.

    Принимает путь (str/Path) или сырые байты xlsx. Неизвестные листы
    пропускаются; при пустом кэше формул qty/price — None → MISSING с 🔴.
    """
    if isinstance(path, bytes):
        workbook = load_workbook(io.BytesIO(path), data_only=True)
    else:
        workbook = load_workbook(Path(path), data_only=True)

    items: list[EstimateItem] = []
    for ws in workbook.worksheets:
        category = _match_category(ws.title)
        if category is None:
            log.info("Лист %r не из каталога категорий, пропуск", ws.title)
            continue
        items.extend(_parse_sheet(ws, category))
    return items
