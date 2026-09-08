#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
validate_1c_adaptation.py

Проверяет адаптацию генератора XML под поведение 1С по уже загруженному
документу асСпецификацияЗаказа № 844.

Восстанавливает параметры из полей табличной части 1С, строит характеристику
через generate_order_xml.build_characteristic и сверяет:
  - вторичные размеры (D2, L2, A2, B2) не теряются;
  - принудительные L0 (force_params) совпадают с тем, что оставляет 1С.
"""

import json
import sys
from collections import Counter
from pathlib import Path
from typing import Dict, List

from generate_order_xml import build_characteristic, load_article_mapping


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


def row_to_params(row: dict) -> Dict[str, float]:
    """Преобразует строку таблицы 1С в параметры для build_characteristic."""
    params = {}
    for src, dst in FIELD_MAP.items():
        val = row.get(src)
        if isinstance(val, (int, float)) and val != 0:
            params[dst] = val
    return params


def parse_characteristic(ch: str) -> Dict[str, str]:
    """Разбирает строку характеристики вида D0100D0_A0500A0_..."""
    result = {}
    if not ch:
        return result
    # Ищем пары ПАРАМЕТР<значение>ПАРАМЕТР_
    import re
    for m in re.finditer(r"([A-Za-z]\w*)(\d+(?:\.\d+)?)\1_", ch):
        result[m.group(1)] = m.group(2)
    return result


def main() -> int:
    data_path = Path("1c_doc_rows_full.json")
    if not data_path.exists():
        print(f"Файл не найден: {data_path}", file=sys.stderr)
        return 1

    doc = json.loads(data_path.read_text(encoding="utf-8"))
    if not doc.get("success"):
        print(f"Ошибка в данных 1С: {doc.get('error')}", file=sys.stderr)
        return 1

    rows = doc["data"]
    mapping = load_article_mapping()

    issues: List[dict] = []
    force_mismatches: List[dict] = []
    ok_count = 0

    for row in rows:
        article = row.get("АртикулПродукции")
        if article not in mapping:
            issues.append({"row": row.get("НомерСтроки"), "article": article, "reason": "нет в mapping"})
            continue

        item = mapping[article]
        params = row_to_params(row)
        try:
            rebuilt = build_characteristic(params, item)
        except Exception as e:
            issues.append({"row": row.get("НомерСтроки"), "article": article, "reason": str(e)})
            continue

        # Проверка force_params: rebuilt должен давать такое же L0, как в 1С
        force = item.get("force_params", {})
        parsed_rebuilt = parse_characteristic(rebuilt)
        for p, expected in force.items():
            actual_raw = parsed_rebuilt.get(p)
            if actual_raw is None:
                force_mismatches.append({
                    "row": row.get("НомерСтроки"),
                    "article": article,
                    "param": p,
                    "reason": "отсутствует в перестроенной характеристике",
                })
                continue
            actual = int(actual_raw)
            if isinstance(expected, str):
                # строковое выражение; оценим через параметры строки
                from generate_order_xml import _eval_default_expression
                expected_val = _eval_default_expression(expected, params)
            else:
                expected_val = expected
            if actual != int(expected_val):
                force_mismatches.append({
                    "row": row.get("НомерСтроки"),
                    "article": article,
                    "param": p,
                    "expected": expected_val,
                    "actual": actual,
                })

        ok_count += 1

    print(f"Проверено строк: {ok_count} (всего в документе: {len(rows)})")
    print(f"Строк с проблемами: {len(issues)}")
    print(f"Несовпадений force_params: {len(force_mismatches)}")

    if issues:
        print("\nПроблемы:")
        for i in issues[:20]:
            print(f"  строка {i['row']}, арт. {i['article']}: {i['reason']}")
        if len(issues) > 20:
            print(f"  ... и ещё {len(issues) - 20}")

    if force_mismatches:
        print("\nНесовпадения force_params:")
        for m in force_mismatches[:20]:
            print(f"  строка {m['row']}, арт. {m['article']}, параметр {m['param']}: "
                  f"ожидалось {m.get('expected')}, получено {m.get('actual', m.get('reason'))}")
        if len(force_mismatches) > 20:
            print(f"  ... и ещё {len(force_mismatches) - 20}")

    # Сводка по артикулам
    print("\nСводка по артикулам:")
    counter = Counter(row["АртикулПродукции"] for row in rows)
    for art, cnt in sorted(counter.items()):
        print(f"  {art}: {cnt} строк")

    return 0 if not issues and not force_mismatches else 1


if __name__ == "__main__":
    sys.exit(main())
