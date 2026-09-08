#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
generate_adapted_test_xml.py

Генерирует тестовый XML (sample_adapted_test.xml) на основе строк документа 844,
уже загруженного в 1С. Использует принудительные параметры (force_params) и
сохраняет вторичные размеры (D2, L2, A2, B2), чтобы при повторной загрузке 1С
не меняла значения за спиной генератора.
"""

import json
import sys
from pathlib import Path

from generate_order_xml import generate_order_xml


FIELD_MAP = {
    "A0": "A0",
    "A1": "A1",
    "A2": "A2",
    "B0": "B0",
    "B1": "B1",
    "B2": "B2",
    "D0": "D0",
    "D1": "D1",
    "D2": "D2",
    "D3": "D3",
    "L0": "L0",
    "L1": "L1",
    "L2": "L2",
    "L3": "L3",
    "u": "U0",
    "R": "R0",
}


def row_to_xml_row(row: dict) -> dict:
    """Преобразует строку табличной части 1С в формат generate_order_xml."""
    params = {}
    for src, dst in FIELD_MAP.items():
        val = row.get(src)
        if isinstance(val, (int, float)) and val != 0:
            params[dst] = val

    article = row.get("АртикулПродукции", "")

    # Материал всегда оцинкованный 0.7 в заказе 844; при необходимости расширить
    # парсинг характеристики.
    return {
        "article": article,
        "name": row.get("Обозначение", ""),
        "params": params,
        "quantity": row.get("n", 1),
        "material_code": "1",
        "thickness": 0.7,
        "connection_0": "0",
        "connection_1": "0",
        "connection_2": "0",
        "connection_3": "0",
        "system": "",
        "comment": row.get("Обозначение", ""),
    }


def main() -> int:
    data_path = Path("1c_doc_rows_full.json")
    out_path = Path("sample_adapted_test.xml")

    if not data_path.exists():
        print(f"Файл не найден: {data_path}", file=sys.stderr)
        return 1

    doc = json.loads(data_path.read_text(encoding="utf-8"))
    if not doc.get("success"):
        print(f"Ошибка в данных 1С: {doc.get('error')}", file=sys.stderr)
        return 1

    rows = doc["data"]
    xml_rows = [row_to_xml_row(row) for row in rows]

    header = {
        "numberDate": "30.06.2026",
        "numberOrder": "TEST-844-ADAPTED",
        "INN": "",
        "customer": "Тестовая проверка адаптации",
        "email": "",
        "phone": "",
        "contact": "",
    }

    xml_text = generate_order_xml(header, xml_rows)
    out_path.write_text(xml_text, encoding="utf-8")
    print(f"Сохранён файл: {out_path} ({len(xml_rows)} строк)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
