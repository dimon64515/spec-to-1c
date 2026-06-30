#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
map_customer_equipment.py

Превращает строки оборудования из PDF-ведомостей заказчика в строки спецификации,
понятные конвейеру process_specification_table → XML для 1С.
"""

import json
import logging
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from config import mapping_file


logger = logging.getLogger(__name__)


def load_mapping(path: str = None) -> dict:
    if path is None:
        path = mapping_file("equipment")
    """Загружает JSON с правилами сопоставления категорий."""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _normalize_regex(pattern: str) -> str:
    """Преобразует named groups из .NET-стиля (?<name>...) в Python (?P<name>...)."""
    return re.sub(r"\(\?<([A-Za-z_][A-Za-z0-9_]*)>", r"(?P<\1>", pattern)


def _alias_pattern(alias: str) -> str:
    """Возвращает регистронезависимое регулярное выражение для поиска псевдонима."""
    return re.escape(alias)


def detect_category(raw_name: str, mapping: dict) -> Optional[dict]:
    """Определяет категорию оборудования по ключевым словам и псевдонимам."""
    text = raw_name.lower()
    for cat in mapping.get("categories", []):
        # 1. Ключевые слова в наименовании
        for kw in cat.get("keywords", []):
            if kw.lower() in text:
                return cat
        # 2. Псевдонимы (модельные ряды) — ищем как отдельное слово
        for alias in cat.get("aliases", []):
            if re.search(r"\b" + _alias_pattern(alias.lower()) + r"\b", text):
                return cat
    return None


def parse_dimensions_from_text(text: str, patterns: List[str]) -> Dict[str, float]:
    """Извлекает размеры из текста по списку regex-паттернов.

    Поддерживаемые именованные группы:
        A, B — прямоугольное сечение (A0, B0)
        D — диаметр (D0)
        L — длина (L0)
        U — угол (U0)
    """
    dims: Dict[str, float] = {}
    for pattern in patterns:
        try:
            regex = re.compile(_normalize_regex(pattern), re.IGNORECASE)
        except re.error:
            continue
        match = regex.search(text)
        if not match:
            continue

        groups = match.groupdict()
        if "A" in groups and groups["A"] is not None and "B" in groups and groups["B"] is not None:
            dims["A0"] = float(groups["A"])
            dims["B0"] = float(groups["B"])
        if "D" in groups and groups["D"] is not None:
            dims["D0"] = float(groups["D"])
        if "L" in groups and groups["L"] is not None:
            dims["L0"] = float(groups["L"])
        if "U" in groups and groups["U"] is not None:
            dims["U0"] = float(groups["U"])

        if dims:
            break

    return dims


def determine_section(dims: Dict[str, float], category: dict) -> str:
    """Определяет сечение по извлечённым размерам."""
    if "D0" in dims and "A0" not in dims and "B0" not in dims:
        return "round"
    if "A0" in dims and "B0" in dims:
        return "rectangular"
    # Если ничего не распознано — по умолчанию прямоугольное для большинства категорий
    return "rectangular"


def select_article(category: dict, section: str) -> Optional[str]:
    """Выбирает артикул из категории в зависимости от сечения."""
    if section == "round":
        return category.get("article_round") or category.get("article_rect")
    return category.get("article_rect") or category.get("article_round")


def _apply_round_derived_params(dims: Dict[str, float], category: dict) -> None:
    """Для круглых изделий добавляет производные размеры (например, A0=D0, B0=D0)."""
    if "D0" not in dims:
        return
    for target, source in category.get("round_derived_params", {}).items():
        if target not in dims and source in dims:
            dims[target] = dims[source]


def _apply_default_params(dims: Dict[str, float], category: dict) -> None:
    """Дополняет недостающие параметры значениями по умолчанию из категории."""
    for param, value in category.get("default_params", {}).items():
        if param not in dims:
            dims[param] = value


def build_spec_name(category: dict, section: str) -> str:
    """Формирует наименование, понятное конвейеру process_specification_table."""
    base = category.get("name", "")
    if section == "round":
        return f"{base} круглого сечения"
    return f"{base} прямоугольного сечения"


def build_spec_size(dims: Dict[str, float]) -> str:
    """Формирует строку размера, понятную parse_size / extract_dimensions."""
    if "D0" in dims:
        return f"D{int(dims['D0'])}"
    if "A0" in dims and "B0" in dims:
        return f"{int(dims['A0'])}x{int(dims['B0'])}"
    return ""


def map_equipment_rows(
    rows: List[dict],
    mapping: Optional[dict] = None,
) -> Tuple[List[dict], List[dict]]:
    """Преобразует строки оборудования из PDF в строки спецификации.

    Args:
        rows: список dict с ключами raw_name, model, manufacturer, unit, quantity.
        mapping: правила сопоставления (если None — загружается из файла).

    Returns:
        (spec_rows, skipped_rows)
    """
    if mapping is None:
        mapping = load_mapping()

    defaults = mapping.get("defaults", {"material": "оцинкованная", "thickness": 0.7, "unit": "шт"})
    skip_keywords = [kw.lower() for kw in mapping.get("skip_keywords", [])]

    spec_rows: List[dict] = []
    skipped: List[dict] = []

    for row in rows:
        raw_name = str(row.get("raw_name", "")).strip()
        model = str(row.get("model", "")).strip()
        full_text = f"{raw_name} {model}".strip()

        category = detect_category(full_text, mapping)
        if category is None:
            # Проверяем явный список исключений
            text_lower = full_text.lower()
            skipped_kw = next((kw for kw in skip_keywords if kw in text_lower), None)
            if skipped_kw:
                skipped.append({
                    "raw_name": raw_name,
                    "model": model,
                    "reason": f"Оборудование не производится ({skipped_kw})",
                })
            else:
                skipped.append({
                    "raw_name": raw_name,
                    "model": model,
                    "reason": "Не удалось распознать категорию",
                })
            continue

        article = select_article(category, "round")  # временно, уточнится после размеров
        if article is None:
            skipped.append({
                "raw_name": raw_name,
                "model": model,
                "reason": f"Для категории {category.get('name')} не назначен артикул в 1С",
            })
            continue

        dims = parse_dimensions_from_text(full_text, category.get("dimension_patterns", []))
        if not dims:
            skipped.append({
                "raw_name": raw_name,
                "model": model,
                "reason": f"Не удалось извлечь размеры для категории {category.get('name')}",
            })
            continue

        section = determine_section(dims, category)
        article = select_article(category, section)
        if article is None:
            skipped.append({
                "raw_name": raw_name,
                "model": model,
                "reason": f"Нет артикула для категории {category.get('name')} ({section})",
            })
            continue

        _apply_round_derived_params(dims, category)
        _apply_default_params(dims, category)

        name = build_spec_name(category, section)
        size = build_spec_size(dims)

        spec_rows.append({
            "name": name,
            "size": size,
            "unit": row.get("unit") or defaults.get("unit", "шт"),
            "quantity": row.get("quantity", "1"),
            "material": defaults.get("material", "оцинкованная"),
            "thickness": defaults.get("thickness", 0.7),
            "article": article,
            "params": dims,
            "source_raw_name": raw_name,
            "source_model": model,
            "detected_category": category.get("id"),
            "detected_article": article,
        })

    return spec_rows, skipped


def main():
    from equipment_pdf_extractor import extract_equipment_from_pdf
    pdf_path = input("Введите путь к PDF: ").strip()
    rows = extract_equipment_from_pdf(pdf_path)
    spec_rows, skipped = map_equipment_rows(rows)
    logger.info(
        "Извлечено: %d, в спецификацию: %d, пропущено: %d",
        len(rows),
        len(spec_rows),
        len(skipped),
    )
    logger.info("Примеры спецификации:")
    for r in spec_rows[:20]:
        logger.info("%s", r)


if __name__ == "__main__":
    main()
