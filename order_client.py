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

import httpx


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


class ExecuteCodeTransport:
    """Текущий путь через MCP Toolkit execute_code (фолбэк на переходный период).

    request_id не поддерживается execute_code-конвертом — параметр принимается
    ради единой подписи send() и игнорируется.
    """

    def __init__(self, execute_url: str) -> None:
        self.execute_url = execute_url

    def send(
        self,
        positions: List[dict],
        order_comment: str,
        request_id: str | None = None,
        timeout: float = 280.0,
    ) -> Dict[str, Any]:
        from tools.as_order_loader.build_execute_payload import build_payload

        payload = build_payload(positions, order_comment=order_comment)
        resp = httpx.post(self.execute_url, json=payload, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        # Два envelope ответа:
        # - нативный HTTP-API MCPToolkit: {"success": bool, "data"|"error": str}
        # - MCP-прокси (tunnel): {"result": ...}
        if isinstance(data, dict) and data.get("success") is False:
            raise RuntimeError(f"1С execute_code: {data.get('error', data)}")
        if isinstance(data, dict):
            text = str(data.get("data") or data.get("result") or data)
        else:
            text = str(data)
        return parse_1c_result(text)


def transport_for(url: str, api_key: str = "") -> ExecuteCodeTransport:
    if "/api/execute_code" in url:
        return ExecuteCodeTransport(url)
    raise ValueError(f"неизвестный URL транспорта 1С: {url}")
