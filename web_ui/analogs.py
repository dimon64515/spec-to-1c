"""Подбор производственных аналогов для пропущенных строк заявки.

Тонкая обёртка над map_customer_equipment: определение категории,
извлечение размеров, выбор артикула и сборка loaded-позиции с дефолтами,
совпадающими с run_pipeline (bitrix_bot/pipeline.py).
"""

from __future__ import annotations

from typing import Optional

from map_customer_equipment import (
    build_spec_name,
    build_spec_size,
    detect_category,
    determine_section,
    parse_dimensions_from_text,
    select_article,
)
from process_specification_table import default_connections
from spec_common import material_to_code

DEFAULT_MATERIAL = "оцинкованная"
DEFAULT_THICKNESS = 0.8
DEFAULT_UNIT = "шт"


class AnalogError(ValueError):
    """Строку нельзя превратить в позицию спецификации (нет категории/размеров)."""


def _row_text(row: dict) -> str:
    """Текст для поиска категории и размеров: наименование + размер строки."""
    return f"{row.get('name', '')} {row.get('size', '')}".strip()


def _detect(row: dict, mapping: dict) -> Optional[dict]:
    category = detect_category(_row_text(row), mapping)
    if category is None:
        return None
    return category


def _parse_dims(row: dict, category: dict) -> dict:
    return parse_dimensions_from_text(
        _row_text(row), category.get("dimension_patterns") or []
    )


def suggest_analog(row: dict, mapping: dict) -> Optional[dict]:
    """Предложить аналог из номенклатуры завода по пропущенной строке.

    Args:
        row: skipped-строка пайплайна с ключами name, size, unit, quantity.
        mapping: правила сопоставления из load_mapping().

    Returns:
        {"article", "spec_name", "spec_size", "category"} или None,
        если категорию определить не удалось. spec_size может быть пустым,
        если размеры в строке не распознались — accept_analog их дозапросит.
    """
    category = _detect(row, mapping)
    if category is None:
        return None
    dims = _parse_dims(row, category)
    section = determine_section(dims, category)
    article = select_article(category, section)
    if article is None:
        return None
    return {
        "article": article,
        "spec_name": build_spec_name(category, section),
        "spec_size": build_spec_size(dims),
        "category": category.get("name") or category.get("id") or "",
    }


def accept_analog(row: dict, suggestion: dict, mapping: dict) -> dict:
    """Превратить предложение аналога в loaded-позицию конвейера заказа.

    Собирает полный набор ключей, который потребляют
    json_positions.build_position и generate_order_xml: дефолты совпадают
    с run_pipeline (params={} не нужен — заполняется размерами, thickness
    0.8, connection_* через default_connections(section, article)).

    Raises:
        AnalogError: категория не определена или размеры не распознаны.
    """
    if not suggestion:
        raise AnalogError("категория не определена")
    category = _detect(row, mapping)
    if category is None:
        raise AnalogError("категория не определена")
    dims = _parse_dims(row, category)
    if not dims:
        raise AnalogError("размер не распознан")

    section = determine_section(dims, category)
    connections = default_connections(section, suggestion["article"])
    material = row.get("material") or DEFAULT_MATERIAL

    return {
        "article": suggestion["article"],
        "name": suggestion["spec_name"],
        "size": build_spec_size(dims),
        "unit": row.get("unit") or DEFAULT_UNIT,
        "quantity": row.get("quantity", 1),
        "params": dims,
        "material": material,
        "material_code": material_to_code(material),
        "thickness": row.get("thickness") or DEFAULT_THICKNESS,
        "connection_0": connections[0],
        "connection_1": connections[1],
        "connection_2": connections[2],
        "connection_3": connections[3],
        "system": row.get("system", ""),
        "comment": row.get("comment", ""),
    }
