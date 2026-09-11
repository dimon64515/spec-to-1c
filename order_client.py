"""Транспорты загрузки заказа в 1С: HTTP-сервис (замена MCP) и execute_code.

Скоуп — только этот модуль; интеграция в bitrix_bot/web_app отдельной
задачей после приёмки HTTP-сервиса
(см. docs/superpowers/specs/2026-09-11-order-client-transport-design.md).

Контракт результата — ровно те же ключи, что у
bitrix_bot.pipeline.parse_1c_result: {"order_number", "errors", "warnings",
"raw"} — чтобы будущая интеграция заменила тело load_order_to_1c одной строкой.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List


def parse_1c_result(text: str) -> Dict[str, Any]:
    """Разобрать строку-результат 1С: 'ЗАКАЗ № | строк=N | ошибок=N | предупр=N | ... ## ...'."""
    head = text.split(" ## ")[0]
    segments = [s.strip() for s in head.split(" | ")]
    order_number = None
    errors: List[str] = []
    warnings: List[str] = []
    n_errors = 0
    n_warnings = 0
    for i, seg in enumerate(segments):
        m = re.match(r"ЗАКАЗ\s+(\S+)", seg)
        if m:
            order_number = m.group(1)
        m = re.match(r"ошибок=(\d+)", seg)
        if m:
            n_errors = int(m.group(1))
            if n_errors:
                # BSL склеивает ошибки СтрСоединить(Ошибки, "; ") — один сегмент
                errors = [e.strip() for e in segments[i + 1].split("; ")]
        m = re.match(r"предупр=(\d+)", seg)
        if m:
            n_warnings = int(m.group(1))
            if n_warnings:
                warnings = [w.strip() for w in segments[i + 1].split("; ")]
    return {
        "order_number": order_number,
        "errors": errors,
        "warnings": warnings,
        "raw": text,
    }
