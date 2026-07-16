#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
generate_order_xml.py

Генератор XML-заказа для загрузки в 1С обработку "Ввод нового заказа".
Источник: структурированные данные (сайт, PDF, Excel).

Пример использования см. в __main__.
"""

import ast
import json
import logging
import xml.etree.ElementTree as ET
from xml.dom import minidom
from pathlib import Path
from typing import List, Dict, Optional

from spec_common import material_to_code as material_type_to_code, connection_to_code


logger = logging.getLogger(__name__)


def load_article_mapping(path: str = "product_article_mapping.json") -> Dict[str, dict]:
    """Загружает сопоставление артикула → параметры и сечение."""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return {item["article"]: item for item in data}


def format_parameter_value(name: str, value) -> str:
    """Форматирует значение параметра в строку фиксированной длины.

    В 1С парсер ищет подстроку между ПАРАМЕТР и ПАРАМЕТР_.
    Для совместимости используем 4 цифры с ведущими нулями для целых размеров.
    Для дробных параметров (U, R, z) подбираем формат по значению.
    """
    name_upper = name.upper()
    if name_upper in (
        "D0", "D1", "D2", "D3",
        "A0", "A1", "A2", "A3",
        "B0", "B1", "B2", "B3",
        "L0", "L1", "L2", "L3",
        "P0", "P1", "P2", "P3",
    ):
        return f"{int(round(float(value))):04d}"
    if name_upper in ("U0", "R0", "Z"):
        # углы/радиусы обычно целые числа
        return f"{int(round(float(value)))}"
    return str(value)


# AST node types allowed in default expressions.
_ALLOWED_EXPR_NODES = (
    ast.Expression,
    ast.BinOp,
    ast.UnaryOp,
    ast.Call,
    ast.Name,
    ast.Constant,
    ast.Load,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.FloorDiv,
    ast.Mod,
    ast.Pow,
    ast.USub,
    ast.UAdd,
    ast.keyword,
)


def _eval_default_expression(expr: str, params: dict) -> float:
    """Безопасно вычисляет строковое выражение умолчания на основе уже известных параметров."""
    allowed_names = set(params) | {"max", "min", "round"}
    tree = ast.parse(expr, mode="eval")

    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED_EXPR_NODES):
            raise ValueError(f"Запрещённая конструкция в выражении умолчания: {type(node).__name__}")

        if isinstance(node, ast.Name) and node.id not in allowed_names:
            raise ValueError(f"Запрещённое имя в выражении умолчания: {node.id}")

        if isinstance(node, ast.Call):
            func = node.func
            if not isinstance(func, ast.Name) or func.id not in allowed_names:
                raise ValueError("В выражении умолчания разрешены только вызовы max/min/round")

    env = {"__builtins__": {}, "max": max, "min": min, "round": round, **params}
    return eval(compile(tree, "<string>", "eval"), env)


def build_characteristic(params: Dict[str, float], mapping: dict) -> str:
    """Собирает строку характеристики из параметров по паттерну продукта.

    Пропущенные параметры подставляются из default_params, заданных
    в product_article_mapping.json. Строковые значения вычисляются
    по уже известным параметрам (например, R0 = D0).

    force_params применяются последними и позволяют задать значения,
    которые 1С использует принудительно независимо от исходных данных
    (например, стандартная длина врезки L0 = 100 мм).
    """
    required = mapping.get("xml_parameters", [])
    defaults = mapping.get("default_params", {})
    force = mapping.get("force_params", {})
    merged = dict(params)
    parts = []
    for p in required:
        if p not in merged:
            if p in defaults:
                dv = defaults[p]
                if isinstance(dv, str):
                    merged[p] = _eval_default_expression(dv, merged)
                else:
                    merged[p] = dv
            else:
                raise ValueError(f"Для артикула {mapping['article']} не хватает параметра {p}")
        # 1С-специфичные принудительные значения имеют наивысший приоритет
        if p in force:
            fv = force[p]
            if isinstance(fv, str):
                merged[p] = _eval_default_expression(fv, merged)
            else:
                merged[p] = fv
        val = format_parameter_value(p, merged[p])
        parts.append(f"{p}{val}{p}_")
    return "".join(parts)


def generate_order_xml(
    header: dict,
    rows: List[dict],
    mapping_path: str = "product_article_mapping.json",
) -> str:
    """Генерирует XML-строку заказа."""
    article_map = load_article_mapping(mapping_path)

    order = ET.Element("Order")
    for key in ["numberDate", "numberOrder", "INN", "customer", "email", "phone", "contact"]:
        child = ET.SubElement(order, key)
        child.text = str(header.get(key, ""))

    tabl = ET.SubElement(order, "Tabl")

    for row in rows:
        article = row["article"]
        if article not in article_map:
            raise ValueError(f"Артикул {article} не найден в сопоставлении")
        mapping = article_map[article]
        section = mapping.get("section", "round")

        prow = ET.SubElement(tabl, "productRow")

        ET.SubElement(prow, "article").text = article
        ET.SubElement(prow, "name").text = row.get("name", mapping.get("name", mapping.get("comment", "")))
        ET.SubElement(prow, "characteristic").text = build_characteristic(row["params"], mapping)
        ET.SubElement(prow, "quantity").text = str(row.get("quantity", 1))

        material_code = row.get("material_code")
        if material_code is None:
            material_code = material_type_to_code(row.get("material", "оцинкованная"))
        ET.SubElement(prow, "material").text = material_code
        ET.SubElement(prow, "thickness").text = str(row.get("thickness", 0.7))

        for i in range(4):
            conn = row.get(f"connection_{i}")
            ET.SubElement(prow, f"connection_{i}").text = connection_to_code(conn, section)

        ET.SubElement(prow, "system").text = row.get("system", "")
        ET.SubElement(prow, "comment").text = row.get("comment", "")

    # Красивый вывод
    rough_string = ET.tostring(order, encoding="unicode")
    reparsed = minidom.parseString(rough_string)
    pretty = reparsed.toprettyxml(indent="  ", encoding="UTF-8")
    if isinstance(pretty, bytes):
        pretty = pretty.decode("utf-8")
    # Удаляем пустые строки, которые minidom вставляет между узлами
    lines = [line for line in pretty.splitlines() if line.strip()]
    return "\n".join(lines) + "\n"


def main():
    """Пример генерации XML для 4 тестовых позиций."""
    header = {
        "numberDate": "18.06.2026",
        "numberOrder": "0000001",
        "INN": "7721234567",
        "customer": "ООО СтройВент",
        "email": "zakaz@stroyvent.example",
        "phone": "+7 (495) 123-45-67",
        "contact": "Петров А.В.",
    }

    rows = [
        {
            "article": "1-1-1",
            "params": {"D0": 160, "L0": 100},
            "quantity": 10,
            "material": "оцинкованная",
            "thickness": 0.7,
            "connection_0": "без соединения",
            "connection_1": "без соединения",
            "connection_2": "0",
            "connection_3": "0",
            "system": "Система приточная",
            "comment": "Цвет RAL 9016",
        },
        {
            "article": "1-1-2",
            "params": {"D0": 200, "L0": 100},
            "quantity": 5,
            "material": "оцинкованная",
            "thickness": 0.7,
            "connection_0": "без соединения",
            "connection_1": "без соединения",
            "connection_2": "0",
            "connection_3": "0",
            "system": "Система приточная",
            "comment": "",
        },
        {
            "article": "1-2-1",
            "params": {"A0": 300, "B0": 200, "L0": 100},
            "quantity": 6,
            "material": "оцинкованная",
            "thickness": 0.7,
            "connection_0": "шина / уголок",
            "connection_1": "шина / уголок",
            "connection_2": "0",
            "connection_3": "0",
            "system": "Система вытяжная",
            "comment": "Усиление рейкой",
        },
        {
            "article": "12-1-3",
            "params": {"D0": 160, "P0": 4},
            "quantity": 4,
            "material": "оцинкованная",
            "thickness": 0.7,
            "connection_0": "0",
            "connection_1": "0",
            "connection_2": "0",
            "connection_3": "0",
            "system": "Система приточная",
            "comment": "",
        },
    ]

    xml_text = generate_order_xml(header, rows)
    out_path = Path("generated_order_example.xml")
    out_path.write_text(xml_text, encoding="utf-8")
    logger.info("Сохранён файл: %s", out_path.resolve())


if __name__ == "__main__":
    main()
