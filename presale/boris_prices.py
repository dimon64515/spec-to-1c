"""Прайс Бориса: внутренние цены завода из xlsx (СофтВент).

Сетка читается из двух прайс-листов: прямоугольные и круглые воздуховоды.
Строки — материал + толщина, колонки — вид изделия (product_kind).
Толщина запроса округляется ВВЕРХ до ближайшей доступной в сетке материальной
группы; выше максимума сетки lookup возвращает None.

Все цены — Decimal (в ячейках xlsx лежат целые числа, float не используется).
"""
import re
from decimal import Decimal
from pathlib import Path
from typing import Optional

from openpyxl import load_workbook
from pydantic import BaseModel

# Группы материалов прайса.
_MATERIAL_RE = re.compile(r"(aisi\s*430|aisi\s*304|08пс|оц|черн)", re.IGNORECASE)
_THICKNESS_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*$")
_FORM_WORDS_RE = re.compile(r"(рулон|лист|штрипс)\.?", re.IGNORECASE)

# Нормализованный запрос → группа материала прайса.
MATERIAL_ALIASES: dict[str, str] = {
    "оцинкованная сталь": "ocsink",
    "оцинк.": "ocsink",
    "оцинк": "ocsink",
    "08пс": "ocsink",
    "нерж": "aisi430",
    "aisi 430": "aisi430",
    "aisi 304": "aisi304",
    "черная сталь": "black",
    "ст3": "black",
}

_KIND_HEADERS = {
    "standard": ("l=1250",),
    "short": ("l<",),
    "fitting": ("фасонн",),
    "nonstandard": ("нестандартн",),
}


class BorisPrice(BaseModel):
    """Найденная цена: значение, толщина сетки и факт округления."""

    price: Decimal
    thickness_used: Decimal
    rounded: bool


def _normalize_material(material: str) -> str:
    """Casefold + убрать «рулон/лист/штрипс», схлопнуть пробелы."""
    s = _FORM_WORDS_RE.sub("", material.casefold()).strip()
    return re.sub(r"\s+", " ", s)


def _parse_row(name: str) -> Optional[tuple[str, Decimal]]:
    """«Лист.нерж. (AISI 430) 0.50» → («aisi430», Decimal("0.50"))."""
    match = _MATERIAL_RE.search(name)
    if match is None:
        return None
    token = match.group(1).casefold().replace(" ", "")
    group = "aisi430" if token == "aisi430" else "aisi304" if token == "aisi304" else "black" if token == "черн" else "ocsink"
    thick = _THICKNESS_RE.search(name[: match.start()] + name[match.end():])
    if thick is None:
        return None
    return group, Decimal(thick.group(1).replace(",", "."))


def _kind_of_header(header: str) -> Optional[str]:
    """Определить product_kind по тексту заголовка колонки."""
    s = header.casefold().replace(" ", "")
    for kind, needles in _KIND_HEADERS.items():
        if any(n.replace(" ", "") in s for n in needles):
            return kind
    return None


class BorisPriceTable:
    """Таблица цен Бориса: сетка из xlsx + переопределения поверх неё."""

    def __init__(self, overrides: Optional[dict] = None):
        self.overrides = overrides or {}
        # {(group, thickness): {kind: Decimal}} для каждой формы.
        self._grids: dict[str, dict[tuple[str, Decimal], dict[str, Decimal]]] = {
            "rect": {},
            "round": {},
        }

    @classmethod
    def from_xlsx(
        cls,
        rect_path: Path,
        round_path: Path,
        overrides: Optional[dict] = None,
    ) -> "BorisPriceTable":
        """Загрузить сетку из двух прайс-листов (data_only=True)."""
        table = cls(overrides=overrides)
        for shape, path in (("rect", rect_path), ("round", round_path)):
            table._load_sheet(shape, path)
        return table

    def _load_sheet(self, shape: str, path: Path) -> None:
        ws = load_workbook(path, data_only=True).worksheets[0]
        header_row = None
        material_col = None
        for r in range(1, min(ws.max_row, 10) + 1):
            for c in ws[r]:
                if isinstance(c.value, str) and c.value.strip().casefold() == "материал":
                    header_row = r
                    material_col = c.column
                    break
            if header_row:
                break
        if header_row is None:
            return
        kind_cols: dict[str, int] = {}
        for r in range(header_row, min(header_row + 3, ws.max_row) + 1):
            for c in ws[r]:
                if isinstance(c.value, str) and c.column != material_col:
                    kind = _kind_of_header(c.value)
                    if kind is not None and kind not in kind_cols:
                        kind_cols[kind] = c.column
        for r in range(header_row + 1, ws.max_row + 1):
            name = ws.cell(row=r, column=material_col).value
            if not isinstance(name, str):
                continue
            parsed = _parse_row(name.strip())
            if parsed is None:
                continue
            group, thickness = parsed
            cells = self._grids[shape].setdefault((group, thickness), {})
            for kind, col in kind_cols.items():
                value = ws.cell(row=r, column=col).value
                if isinstance(value, (int, float)):
                    cells[kind] = Decimal(str(value))

    def lookup(
        self,
        *,
        material: str,
        thickness: Decimal,
        shape: str,
        product_kind: str = "standard",
    ) -> Optional[BorisPrice]:
        """Цена для материала/толщины/формы/вида; None, если не найдена.

        Толщина округляется вверх до ближайшей в сетке группы; выше максимума
        сетки или для неизвестного материала/вида — None.
        """
        group = MATERIAL_ALIASES.get(_normalize_material(material))
        if group is None or shape not in self._grids:
            return None
        grid = self._grids[shape]
        available = sorted(t for (g, t) in grid if g == group)
        if not available:
            return None
        thickness_used = next((t for t in available if t >= thickness), None)
        if thickness_used is None:
            return None
        rounded = thickness_used != thickness
        key = (group, thickness_used, shape, product_kind)
        if key in self.overrides:
            return BorisPrice(price=self.overrides[key], thickness_used=thickness_used, rounded=rounded)
        price = grid.get((group, thickness_used), {}).get(product_kind)
        if price is None:
            return None
        return BorisPrice(price=price, thickness_used=thickness_used, rounded=rounded)
