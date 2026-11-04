"""Прайс Бориса: внутренние цены завода из xlsx (СофтВент).

Сетка читается из двух прайс-листов: прямоугольные и круглые воздуховоды.
Строки — материал + толщина, колонки — вид изделия (product_kind).
Толщина запроса округляется ВВЕРХ до ближайшей доступной в сетке материальной
группы; выше максимума сетки lookup возвращает None.

Все цены — Decimal (в ячейках xlsx лежат целые числа, float не используется).
"""
import logging
import re
from decimal import Decimal
from pathlib import Path
from typing import Optional

from openpyxl import load_workbook
from pydantic import BaseModel

log = logging.getLogger(__name__)

# Группы материалов прайса.
_MATERIAL_RE = re.compile(r"(aisi\s*430|aisi\s*304|08пс|оц|черн)", re.IGNORECASE)
_THICKNESS_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*$")
_FORM_WORDS_RE = re.compile(r"(рулон|лист|штрипс)\.?", re.IGNORECASE)
_STRIP_RE = re.compile(r"штрипс", re.IGNORECASE)

# При слиянии дублей (group, thickness) в одном листе форма «рулон/лист»
# первична, «штрипс» — вторична: цену рулона штрипс не перекрывает.
_ROW_FORM_PRIORITY = {"primary": 1, "secondary": 0}

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
    # Только круглый прайс имеет эту колонку; прямоугольный — нет (None → 🔴).
    "spiral": ("спирально-навивн",),
}

# Человеческие подписи канонических ключей (селекты админки / отчёты).
MATERIAL_GROUP_LABELS = {
    "ocsink": "Оцинкованная сталь",
    "aisi430": "AISI 430",
    "aisi304": "AISI 304",
    "black": "Чёрная сталь",
}
SHAPE_LABELS = {"rect": "Прямоугольные", "round": "Круглые"}
PRODUCT_KIND_LABELS = {
    "standard": "Стандартный (L=1250)",
    "short": "Короткий (L<1250)",
    "fitting": "Фасонная часть",
    "nonstandard": "Нестандартный",
    "spiral": "Спирально-навивной",
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
    """Таблица цен Бориса: сетка из xlsx + переопределения поверх неё.

    Слияние дублей (group, thickness) внутри одного листа детерминировано:
    первичная форма «рулон/лист» побеждает вторичную «штрипс» независимо от
    порядка строк; пустые ячейки уже записанные значения не затирают;
    при конфликте двух непустых цен берётся значение из строки рулона/листа
    и пишется log.warning.

    overrides копируется и приводится к Decimal на входе; ключ задаётся по
    уже округлённой до сетки толщине (0.7, а не 0.65):
    {(material_group, thickness, shape, product_kind): Decimal}.
    """

    def __init__(self, overrides: Optional[dict] = None):
        self.overrides = {
            key: Decimal(str(value)) for key, value in dict(overrides or {}).items()
        }
        # {(group, thickness): {kind: Decimal}} для каждой формы.
        self._grids: dict[str, dict[tuple[str, Decimal], dict[str, Decimal]]] = {
            "rect": {},
            "round": {},
        }
        # Источник записанной цены: {(shape, group, thickness, kind): form}.
        self._sources: dict[tuple[str, str, Decimal, str], str] = {}

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
            log.warning(
                "Прайс %s: колонка «Материал» не найдена — лист не распознан, цены пропущены",
                path,
            )
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
            row_form = "secondary" if _STRIP_RE.search(name) else "primary"
            cells = self._grids[shape].setdefault((group, thickness), {})
            for kind, col in kind_cols.items():
                value = ws.cell(row=r, column=col).value
                if not isinstance(value, (int, float)) or isinstance(value, bool):
                    continue
                new_price = Decimal(str(value))
                cell_key = (shape, group, thickness, kind)
                old_price = cells.get(kind)
                if old_price is None:
                    cells[kind] = new_price
                    self._sources[cell_key] = row_form
                    continue
                if old_price == new_price:
                    continue
                # Конфликт двух непустых цен: первична форма «рулон/лист».
                old_form = self._sources[cell_key]
                log.warning(
                    "Прайс %s: конфликт цен %s %s %s %s: %s (%s) vs %s (%s); беру %s",
                    path, group, thickness, shape, kind,
                    old_price, old_form, new_price, row_form,
                    new_price if _ROW_FORM_PRIORITY[row_form] > _ROW_FORM_PRIORITY[old_form] else old_price,
                )
                if _ROW_FORM_PRIORITY[row_form] > _ROW_FORM_PRIORITY[old_form]:
                    cells[kind] = new_price
                    self._sources[cell_key] = row_form

    def cells(self) -> list[tuple[str, str, Decimal, str, Decimal]]:
        """Все ячейки сетки: (shape, group, thickness, kind, price), отсортированы.

        Публичный доступ к сетке для админки: селекты формы переопределений
        строятся из реальных ячеек, чтобы ключ нельзя было опечатать.
        """
        out = []
        for shape, grid in self._grids.items():
            for (group, thickness), kinds in grid.items():
                for kind, price in kinds.items():
                    out.append((shape, group, thickness, kind, price))
        return sorted(out, key=lambda c: (c[0], c[1], c[2], c[3]))

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
