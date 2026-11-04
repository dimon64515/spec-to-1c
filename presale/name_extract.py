"""Извлечение материала/толщины/типа изделия из наименования позиции.

Детерминированные правила для наименований вида «Воздуховоды из
оцинкованной стали, жесткие, спирально-навивные, прямой участок, толщина
0,55 мм, диаметр 160 мм». Правила намеренно консервативны: сомнительное
не угадывается — строка уходит в 🔴 к менеджеру.
"""
from __future__ import annotations

import re
from decimal import Decimal
from typing import Optional

from presale.models import Category, EstimateItem

# «толщина 0,7 мм» / «толщиной 0.7» — единственный надёжный источник толщины:
# «диаметр/периметр/размер N мм» ловить нельзя.
_THICKNESS_RE = re.compile(
    r"толщин[а-я]*\s*(?:металла\s*)?(\d+(?:[.,]\d+)?)", re.IGNORECASE
)

_FITTING_WORDS = re.compile(
    r"(фасонн|отвод|переход|тройник|заглушк|наконечник|зонт|дефлектор|"
    r"дроссель|клапан|ниппель|муфт)",
    re.IGNORECASE,
)
_NONSTANDARD_WORDS = re.compile(r"нестандартн", re.IGNORECASE)
# Круглое сечение: диаметр или явные маркеры; «спирально-навивн» — всегда круг.
_ROUND_WORDS = re.compile(
    r"(кругл|диаметр|спирально-навивн|спиральнонавивн)", re.IGNORECASE
)
_RECT_WORDS = re.compile(r"(прямоугольн|периметр|шин[аой]\s*и\s*уголк)", re.IGNORECASE)


def extract_from_name(name: str) -> dict:
    """Наименование → {"material", "thickness", "shape", "product_kind"}.

    Нераспознанные поля — None; материал берём по приоритету: AISI → нерж,
    затем «черн», затем «оцинк» («тонколистовой оцинкованной и сортовой» —
    оцинк, сортовой прокат без «оцинк» — чёрная сталь).
    """
    text = " ".join(name.casefold().replace("ё", "е").split())
    out: dict[str, Optional[object]] = {
        "material": None, "thickness": None,
        "shape": None, "product_kind": None,
    }

    if re.search(r"aisi\s*304", text):
        out["material"] = "aisi304"
    elif re.search(r"aisi\s*430", text) or "нержав" in text:
        out["material"] = "aisi430"
    elif "черн" in text or "сортовой прокат" in text:
        out["material"] = "black"
    elif "оцинк" in text:
        out["material"] = "ocsink"

    thick = _THICKNESS_RE.search(text)
    if thick:
        out["thickness"] = Decimal(thick.group(1).replace(",", "."))

    if _ROUND_WORDS.search(text):
        out["shape"] = "round"
    elif _RECT_WORDS.search(text):
        out["shape"] = "rect"

    if _NONSTANDARD_WORDS.search(text):
        out["product_kind"] = "nonstandard"
    elif _FITTING_WORDS.search(text):
        out["product_kind"] = "fitting"
    elif re.search(r"спирально-навивн|спиральнонавивн", text):
        out["product_kind"] = "spiral"
    elif "прямой участок" in text:
        out["product_kind"] = "standard"

    return out


def enrich_items(items: list[EstimateItem]) -> list[EstimateItem]:
    """Дозаполняет material/thickness/shape/product_kind, если не заданы.

    Применяется к материалам до ценообразования: без material+thickness
    прайс Бориса не срабатывает (backlog N-2).
    """
    enriched = []
    for item in items:
        if item.category != Category.MATERIALS:
            enriched.append(item)
            continue
        if item.material and item.thickness:
            enriched.append(item)
            continue
        extracted = extract_from_name(item.name)
        enriched.append(item.model_copy(update={
            k: v for k, v in extracted.items()
            if k != "shape" and v is not None and getattr(item, k) is None
        }))
    return enriched
