"""Транспорты загрузки заказа в 1С: HTTP-сервис (замена MCP) и execute_code.

Скоуп — только этот модуль; интеграция в bitrix_bot/web_app отдельной
задачей после приёмки HTTP-сервиса
(см. docs/superpowers/specs/2026-09-11-order-client-transport-design.md).

Контракт результата — ровно те же ключи, что у
bitrix_bot.pipeline.parse_1c_result: {"order_number", "errors", "warnings",
"raw"} — чтобы будущая интеграция заменила тело load_order_to_1c одной строкой.
"""
from __future__ import annotations

import json
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


def transport_for(url: str, api_key: str = "") -> "ExecuteCodeTransport | HttpServiceTransport":
    if "/hs/" in url:
        return HttpServiceTransport(url, api_key=api_key)
    if "/api/execute_code" in url:
        return ExecuteCodeTransport(url)
    raise ValueError(f"неизвестный URL транспорта 1С: {url}")


class HttpServiceTransport:
    """HTTP-сервис расширения ВОКЗагрузкаЗаказаHTTP (POST /hs/vok/order).

    Контракт запроса/ответа: docs/ТЗ_HTTP_СЕРВИС_ЗАГРУЗКА_ЗАКАЗА_1С.md.
    Ответ {Успех, НомерЗаказа, Ошибки, Предупреждения} маппится в форму
    parse_1c_result — результат неотличим от execute_code-пути.
    """

    def __init__(self, base_url: str, api_key: str = "") -> None:
        self.base_url = base_url
        self.api_key = api_key

    def send(
        self,
        positions: List[dict],
        order_comment: str,
        request_id: str | None = None,
        timeout: float = 280.0,
    ) -> Dict[str, Any]:
        body = {
            "order_comment": order_comment,
            "request_id": request_id,
            "positions": positions,
        }
        headers = {"Content-Type": "application/json; charset=utf-8"}
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        # content=, а не json=: httpx json= не ставит charset=utf-8 (ТЗ п. 3)
        resp = httpx.post(
            self.base_url,
            content=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            timeout=timeout,
        )
        text = resp.text or ""
        data: Dict[str, Any] | None = None
        try:
            parsed = resp.json()
            if isinstance(parsed, dict):
                data = parsed
        except ValueError:
            data = None
        # 5xx со структурой ТЗ — бизнес-ответ (Успех=false), не исключение
        if resp.status_code >= 500 and data is not None and "Успех" in data:
            return self._to_result(data, text)
        if resp.status_code >= 500:
            resp.raise_for_status()  # httpx.HTTPStatusError — ретрай
        if resp.status_code >= 400:
            # детерминированная ошибка конфигурации (ключ/метод) — ретрай бессмысленен
            raise RuntimeError(
                f"HTTP-сервис 1С: HTTP {resp.status_code}: {text[:500]}"
            )
        if data is None:
            raise RuntimeError(f"HTTP-сервис 1С: битый JSON в ответе: {text[:500]}")
        return self._to_result(data, text)

    @staticmethod
    def _to_result(data: Dict[str, Any], raw: str) -> Dict[str, Any]:
        """{Успех, НомерЗаказа, Ошибки, Предупреждения} → форма parse_1c_result."""
        errors = [str(e) for e in (data.get("Ошибки") or [])]
        warnings = [str(w) for w in (data.get("Предупреждения") or [])]
        number = data.get("НомерЗаказа")
        segments = []
        if number:
            segments.append(f"ЗАКАЗ {number}")
        segments.append(f"ошибок={len(errors)}")
        if errors:
            segments.append("; ".join(errors))
        segments.append(f"предупр={len(warnings)}")
        if warnings:
            segments.append("; ".join(warnings))
        out = parse_1c_result(" | ".join(segments))
        out["raw"] = raw
        return out


def load_order(
    positions: List[dict],
    url: str,
    order_comment: str,
    request_id: str | None = None,
    api_key: str = "",
    timeout: float = 280.0,
) -> Dict[str, Any]:
    """Единая точка входа: auto-detect транспорта по форме URL."""
    return transport_for(url, api_key=api_key).send(
        positions, order_comment, request_id=request_id, timeout=timeout
    )
