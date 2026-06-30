#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
match_description_to_product.py

Сопоставляет текстовое описание позиции (из PDF/сайта/Excel) с артикулом продукта 1С.

Пример:
    python match_description_to_product.py "Воздуховод круглый прямошовный D160 L1000"
"""

import json
import logging
import re
import sys
from difflib import SequenceMatcher
from pathlib import Path
from typing import List, Dict, Tuple


logger = logging.getLogger(__name__)


def load_products(path: str = "product_article_mapping.json") -> List[dict]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def normalize(text: str) -> str:
    text = text.lower()
    text = text.replace("ё", "е")
    text = re.sub(r"[^a-zа-я0-9x*×х/\-. ]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def keyword_score(text: str, product: dict) -> float:
    """Баллы за ключевые слова, характеризующие группу/тип продукта."""
    score = 0.0
    name = normalize(product.get("name", ""))
    section = product.get("section")

    # Круглое / прямоугольное сечение
    if section == "round":
        if any(w in text for w in ("кругл", "кр", "ф", "d", "dn")):
            score += 2.0
    elif section == "rectangular":
        if any(w in text for w in ("прямоуг", "прям", "п")):
            score += 2.0

    # Тип воздуховода
    if "прямошовн" in text and "прямошовн" in name:
        score += 3.0
    if "спираль" in text and "спираль" in name:
        score += 3.0
    if "навивн" in text and "навивн" in name:
        score += 3.0

    # Фасонные изделия
    if "фланец" in text and "фланец" in name:
        score += 3.0
    if "отвод" in text and "отвод" in name:
        score += 3.0
    if "переход" in text and "переход" in name:
        score += 3.0
    if "тройник" in text and "тройник" in name:
        score += 3.0
    if "заглушка" in text and "заглушка" in name:
        score += 3.0
    if "врезка" in text and "врезка" in name:
        score += 3.0
    if "насадок" in text and "насадок" in name:
        score += 3.0
    if "крестовина" in text and "крестовина" in name:
        score += 3.0
    if "пленум" in text and "пленум" in name:
        score += 3.0
    if "шумоглуш" in text and "шумоглуш" in name:
        score += 3.0
    if "диффузор" in text and "диффузор" in name:
        score += 3.0
    if "решетк" in text and "решетк" in name:
        score += 3.0

    return score


def extract_dimensions(text: str) -> Dict[str, float]:
    """Извлекает типовые размеры из текстового описания."""
    dims = {}
    text = text.replace("х", "x").replace("×", "x").replace("*", "x")

    # Прямоугольное сечение: 300x200, 300*200 и т.п.
    rect_match = re.search(r"(\d{2,4})\s*x\s*(\d{2,4})", text, re.IGNORECASE)
    if rect_match:
        dims["A0"] = float(rect_match.group(1))
        dims["B0"] = float(rect_match.group(2))

    # Диаметр: D160, DN160, Ф160, ф 160
    diam_match = re.search(r"(?:d|dn|ф)\s*(\d+)", text, re.IGNORECASE)
    if diam_match:
        dims["D0"] = float(diam_match.group(1))

    # Если диаметр не найден, но есть одно число 50..1600 в конце описания — скорее всего диаметр.
    if "D0" not in dims and "A0" not in dims:
        bare_match = re.search(r"\b(\d{2,4})\s*$", text.strip())
        if bare_match:
            val = float(bare_match.group(1))
            if 50 <= val <= 1600:
                dims["D0"] = val

    # Длина: L1000, L=1000, 1000 мм
    len_match = re.search(r"(?:l\s*=?\s*|длина\s*)(\d{3,5})", text, re.IGNORECASE)
    if len_match:
        dims["L0"] = float(len_match.group(1))

    # Количество отверстий / сторон для фланцев/заглушек
    hole_match = re.search(r"(?:отверст|\sр\s*|штуцер)(?:\s*[:=])?\s*(\d+)", text, re.IGNORECASE)
    if not hole_match:
        hole_match = re.search(r"\b(\d)\s*(?:отверст|р\b|штуц)", text, re.IGNORECASE)
    if hole_match:
        dims["P0"] = float(hole_match.group(1))

    return dims


def match_description(
    text: str,
    products: List[dict],
    top_n: int = 5,
) -> List[Tuple[dict, float]]:
    """Возвращает top-N наиболее подходящих продуктов."""
    norm_text = normalize(text)
    dims = extract_dimensions(text)
    inferred_section = None
    if "A0" in dims and "B0" in dims:
        inferred_section = "rectangular"
    elif "D0" in dims:
        inferred_section = "round"

    scored = []

    for p in products:
        name = normalize(p.get("name", ""))
        similarity = SequenceMatcher(None, norm_text, name).ratio()
        kw = keyword_score(norm_text, p)
        section_bonus = 0.0
        if inferred_section and p.get("section") == inferred_section:
            section_bonus = 2.5
        total = similarity * 10 + kw + section_bonus
        scored.append((p, total))

    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:top_n]


def main():
    if len(sys.argv) < 2:
        logger.error("Использование: python match_description_to_product.py \"<описание>\"")
        logger.error("Пример: python match_description_to_product.py \"Воздуховод круглый прямошовный D160 L1000\"")
        sys.exit(1)

    text = " ".join(sys.argv[1:])
    products = load_products()
    dims = extract_dimensions(text)
    matches = match_description(text, products, top_n=5)

    logger.info("=" * 60)
    logger.info("Описание: %s", text)
    logger.info("Извлечённые размеры: %s", dims)
    logger.info("=" * 60)
    logger.info("Топ совпадений:")
    for i, (p, score) in enumerate(matches, 1):
        logger.info(
            "%d. [%s] %s  (сечение: %s, параметры: %s, балл: %.2f)",
            i,
            p["article"],
            p.get("name", ""),
            p.get("section"),
            ", ".join(p.get("xml_parameters", [])),
            score,
        )


if __name__ == "__main__":
    main()
