"""Генерация и разбор Excel-отчёта по результатам пайплайна.

build_excel_report: PipelineResult -> xlsx (4 листа).
Листы и заголовки — константы: parse_edited_report (ниже в этом же файле,
Task 7) читает их обратно для пересоздания заказа.
"""

from __future__ import annotations

import io
from typing import TYPE_CHECKING, Any, List

from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

from process_specification_table import detect_product_type

if TYPE_CHECKING:
    from bitrix_bot.pipeline import PipelineResult

SHEET_LOADED = "Загружено"
SHEET_SKIPPED = "Пропущено"
SHEET_TRADING = "Перекупное"
SHEET_ERRORS = "Ошибки 1С"

TRADING_REASON_PREFIXES = ("Покупная позиция", "Покупная арматура")
ORDER_NUMBER_LABEL = "Номер заказа"

# Product types treated as resold equipment (покупное оборудование).
# Источник истины — здесь (web_app.py не импортируется: это Streamlit-приложение).
EQUIPMENT_PTYPES = {
    "diffuser",
    "ksd",
    "grille",
    "silencer",
    "damper",
    "shutter",
    "filter",
    "throttle",
    "roof_cap",
    "fan",
}

LOADED_HEADERS = ["Артикул", "A", "B", "D", "Кол-во", "Материал", "Толщина",
                  "Соед. 0", "Соед. 1", "Соед. 2", "Соед. 3",
                  "Система", "Комментарий"]
SKIPPED_HEADERS = ["Наименование", "Размер", "Кол-во", "Ед.", "Материал",
                   "Толщина", "Причина", "Включить в заказ"]
TRADING_HEADERS = ["Наименование", "Категория", "Размер", "Материал", "Толщина",
                   "Кол-во", "Ед.", "Производитель", "Модель", "Включить в заказ"]


def is_trading_skip(skip: dict) -> bool:
    """Перекупное (покупное) оборудование: скипы по причине «Покупная …»,
    строки автомаппинга оборудования (raw_name) и позиции, чей тип
    распознаётся как покупное оборудование (EQUIPMENT_PTYPES)."""
    if skip.get("raw_name"):
        return True
    reason = str(skip.get("reason", ""))
    if reason.startswith(TRADING_REASON_PREFIXES):
        return True
    name = skip.get("name") or skip.get("raw_name") or ""
    if name:
        ptype = detect_product_type(name)
        if ptype in EQUIPMENT_PTYPES:
            return True
    return False


def _write_sheet(ws, headers: List[str], rows: List[List[Any]]) -> None:
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for row in rows:
        ws.append(row)
    ws.freeze_panes = "A2"
    for i, h in enumerate(headers, 1):
        width = len(str(h))
        for row in rows:
            width = max(width, len(str(row[i - 1])) if row[i - 1] is not None else 0)
        ws.column_dimensions[get_column_letter(i)].width = min(max(width + 2, 8), 60)


def _loaded_row(p: dict) -> List[Any]:
    dims = p.get("params") or {}
    return [
        p.get("article", ""), dims.get("A0"), dims.get("B0"), dims.get("D0"),
        p.get("quantity"), p.get("material_code", ""), p.get("thickness"),
        p.get("connection_0", ""), p.get("connection_1", ""),
        p.get("connection_2", ""), p.get("connection_3", ""),
        p.get("system", ""), p.get("comment", ""),
    ]


def _skipped_row(s: dict) -> List[Any]:
    return [s.get("name", ""), s.get("size", ""), s.get("quantity"),
            s.get("unit", ""), s.get("material", ""), s.get("thickness"),
            s.get("reason", ""), ""]


def _trading_row(s: dict) -> List[Any]:
    name = s.get("name") or s.get("raw_name") or ""
    return [name, detect_product_type(name) or "",
            s.get("size") or s.get("model") or "",
            s.get("material", ""), s.get("thickness"),
            s.get("quantity"), s.get("unit", "шт"),
            s.get("manufacturer", ""), s.get("model", ""), ""]


def build_excel_report(res: "PipelineResult") -> bytes:
    """Собрать xlsx-отчёт: Загружено / Пропущено / Перекупное / Ошибки 1С."""
    wb = Workbook()
    ws = wb.active
    ws.title = SHEET_LOADED
    _write_sheet(ws, LOADED_HEADERS, [_loaded_row(p) for p in res.loaded])

    trading = [s for s in res.skipped if is_trading_skip(s)]
    skipped = [s for s in res.skipped if not is_trading_skip(s)]

    _write_sheet(wb.create_sheet(SHEET_SKIPPED), SKIPPED_HEADERS,
                 [_skipped_row(s) for s in skipped])
    _write_sheet(wb.create_sheet(SHEET_TRADING), TRADING_HEADERS,
                 [_trading_row(s) for s in trading])

    ws = wb.create_sheet(SHEET_ERRORS)
    summary = [
        (ORDER_NUMBER_LABEL, res.order_number or ""),
        ("Файл", res.file_name),
        ("Загружено", len(res.loaded)),
        ("Пропущено", len(skipped)),
        ("Перекупное", len(trading)),
        ("Ошибок 1С", len(res.errors_1c)),
        ("Предупреждений 1С", len(res.warnings_1c)),
    ]
    ws.append(["Показатель", "Значение"])
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for label, value in summary:
        ws.append([label, value])
    ws.append([])
    ws.append(["Тип", "Текст"])
    for cell in ws[ws.max_row]:
        cell.font = Font(bold=True)
    for e in res.errors_1c:
        ws.append(["Ошибка", e])
    for w in res.warnings_1c:
        ws.append(["Предупреждение", w])
    ws.freeze_panes = "A2"
    ws.column_dimensions["A"].width = 22
    ws.column_dimensions["B"].width = 60

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
