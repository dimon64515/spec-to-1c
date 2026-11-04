"""Excel-выгрузки предварительного расчёта.

build_internal_xlsx — внутренняя выгрузка (рыночные/внутренние цены, маржа,
источник, статусы). build_customer_xlsx — КП для заказчика: только наши цены,
без рыночных колонок, маржи и служебных статусов.

НДС в сводке «Итог» — 22% (VAT_RATE, по образцу КП Игоря).
Decimal в ячейки уходит только после квантизации 0.01 ROUND_HALF_UP → float.
"""
from decimal import Decimal, ROUND_HALF_UP
from io import BytesIO
from typing import Any, List, Optional

from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

from presale.models import CENT, Category, EstimateItem, Summary, build_summary

VAT_RATE = Decimal("0.22")

SHEET_TOTAL = "Итог"
CATEGORY_SHEETS = {
    Category.WORKS: "Работы",
    Category.MATERIALS: "Материалы",
    Category.EQUIPMENT: "Оборудование",
}

INTERNAL_HEADERS = ["Наименование", "Ед.", "Количество", "Рыночная цена",
                    "Рыночная сумма", "Наша цена", "Наша сумма",
                    "Источник цены", "Статус", "Примечание"]
CUSTOMER_HEADERS = ["Наименование", "Ед.", "Количество", "Цена", "Сумма"]

STATUS_LABELS = {
    "ok": "🟢 ок",
    "rounded": "🟡 округление толщины",
    "missing": "🔴 нет цены",
    "manual": "🟢 ручная",
    "overridden": "🟢 переопределена",
}


def _cell(value: Optional[Decimal]) -> Optional[float]:
    """Decimal → float для ячейки; квантизация 0.01 ROUND_HALF_UP."""
    if value is None:
        return None
    return float(value.quantize(CENT, rounding=ROUND_HALF_UP))


def _status_text(item: EstimateItem) -> str:
    return STATUS_LABELS.get(item.status.value, item.status.value)


def _market_total(item: EstimateItem) -> Optional[Decimal]:
    if item.market_price is None:
        return None
    return (item.qty * item.market_price).quantize(CENT, rounding=ROUND_HALF_UP)


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


def _internal_row(item: EstimateItem) -> List[Any]:
    return [
        item.name, item.unit, float(item.qty),
        _cell(item.market_price), _cell(_market_total(item)),
        _cell(item.internal_price), _cell(item.computed_total()),
        item.price_source, _status_text(item), item.status_note,
    ]


def _customer_row(item: EstimateItem) -> List[Any]:
    return [
        item.name, item.unit, float(item.qty),
        _cell(item.internal_price), _cell(item.computed_total()),
    ]


def _itog_rows(summary: Summary, *, internal_only: bool) -> List[List[Any]]:
    """Строки сводки «Итог»: по категориям + «Итого», с колонками НДС 22%."""
    headers = ["Категория", "Сумма без НДС", "НДС 22%", "С НДС"]
    if not internal_only:
        headers = ["Категория", "Рыночная сумма", "Внутренняя сумма",
                   "Разница", "НДС 22%", "С НДС"]

    rows: List[List[Any]] = []

    def add(label: str, market: Decimal, internal: Decimal) -> None:
        vat = (internal * VAT_RATE).quantize(CENT, rounding=ROUND_HALF_UP)
        with_vat = internal + vat
        if internal_only:
            rows.append([label, _cell(internal), _cell(vat), _cell(with_vat)])
        else:
            rows.append([label, _cell(market), _cell(internal),
                         _cell(internal - market), _cell(vat), _cell(with_vat)])

    for category in Category:
        totals = summary.by_category[category]
        market = totals["market"].quantize(CENT, rounding=ROUND_HALF_UP)
        internal = totals["internal"].quantize(CENT, rounding=ROUND_HALF_UP)
        if internal_only and internal == 0 and market == 0:
            continue
        add(CATEGORY_SHEETS[category], market, internal)
    add("Итого", summary.market_total, summary.internal_total)
    return [headers] + rows


def _write_itog(wb, summary: Summary, meta: dict, *, internal_only: bool) -> None:
    ws = wb.active
    ws.title = SHEET_TOTAL
    for label, value in meta.items():
        ws.append([str(label), str(value)])
    if meta:
        ws.append([])
    table = _itog_rows(summary, internal_only=internal_only)
    ws.append(table[0])
    for cell in ws[ws.max_row]:
        cell.font = Font(bold=True)
    for row in table[1:]:
        ws.append(row)
    if not internal_only:
        ws.append([])
        ws.append(["Маржа, %", _cell(summary.margin_pct) if summary.margin_pct is not None else None])
    ws.column_dimensions["A"].width = 22
    for i in range(2, len(table[0]) + 1):
        ws.column_dimensions[get_column_letter(i)].width = 16


def _build(items: List[EstimateItem], meta: dict, *, internal_only: bool) -> bytes:
    summary = build_summary(items)
    wb = Workbook()
    _write_itog(wb, summary, meta, internal_only=internal_only)
    for category, sheet_name in CATEGORY_SHEETS.items():
        category_items = [i for i in items if i.category == category]
        if not category_items:
            continue
        if internal_only:
            rows = [_customer_row(i) for i in category_items]
            headers = CUSTOMER_HEADERS
        else:
            rows = [_internal_row(i) for i in category_items]
            headers = INTERNAL_HEADERS
        _write_sheet(wb.create_sheet(sheet_name), headers, rows)
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def build_internal_xlsx(items: List[EstimateItem], meta: dict) -> bytes:
    """Внутренняя выгрузка: «Итог» + листы категорий, 10 колонок (5+5)."""
    return _build(items, meta, internal_only=False)


def build_customer_xlsx(items: List[EstimateItem], meta: dict) -> bytes:
    """КП для заказчика: только наши цены, без market/маржи/источника/статусов."""
    return _build(items, meta, internal_only=True)
