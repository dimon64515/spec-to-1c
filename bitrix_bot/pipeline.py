"""Оркестрация пайплайна PDF → заказ 1С."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import httpx


@dataclass
class PipelineResult:
    """Результат обработки одного PDF для отчёта бота."""

    file_name: str
    order_number: Optional[str] = None
    loaded: List[Dict[str, Any]] = field(default_factory=list)
    skipped: List[Dict[str, Any]] = field(default_factory=list)
    errors_1c: List[str] = field(default_factory=list)
    warnings_1c: List[str] = field(default_factory=list)
    raw_text: str = ""


def process_pdf_to_positions(pdf_bytes: bytes) -> Tuple[List[dict], List[dict]]:
    """PDF → (успешные строки пайплайна, пропущенные с причинами).

    Берёт таблицы из PDF; если их нет — текстовый слой, разобранный блоками
    (ГОСТ-бланки, см. api.load_tables_from_pdf).
    """
    from api import load_tables_from_pdf
    from pdf_spec_extractor import df_to_spec_rows
    from process_specification_table import process_rows

    data = load_tables_from_pdf(pdf_bytes)
    if "tables" in data:
        rows: List[dict] = []
        for tables in data["tables"].values():
            for df in tables:
                rows.extend(df_to_spec_rows(df))
    else:
        rows = data.get("block_rows") or []
    _, skipped, success = process_rows(rows)
    return success, skipped


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
                errors = segments[i + 1 : i + 1 + n_errors]
        m = re.match(r"предупр=(\d+)", seg)
        if m:
            n_warnings = int(m.group(1))
            if n_warnings:
                warnings = segments[i + 1 : i + 1 + n_warnings]
    return {
        "order_number": order_number,
        "errors": errors,
        "warnings": warnings,
        "raw": text,
    }


def load_order_to_1c(
    positions: List[dict],
    execute_url: str,
    order_comment: str,
    timeout: float = 280.0,
) -> Dict[str, Any]:
    """Отправить позиции в 1С через MCP execute_code; вернуть разобранный результат.

    Ошибки 1С (Успех=false и т.п.) — в поле errors результата, не исключение.
    Исключение — только сетевой сбой (httpx.HTTPError).
    """
    from tools.as_order_loader.build_execute_payload import build_payload

    payload = build_payload(positions, order_comment=order_comment)
    resp = httpx.post(execute_url, json=payload, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    text = str(data.get("result", data))
    return parse_1c_result(text)


def run_pipeline(
    pdf_bytes: bytes,
    file_name: str,
    order_comment: str,
    execute_url: str,
    timeout: float = 280.0,
) -> PipelineResult:
    """Полный цикл: PDF → позиции → заказ 1С → PipelineResult."""
    from json_positions import build_positions

    success, skipped = process_pdf_to_positions(pdf_bytes)
    if not success:
        return PipelineResult(
            file_name=file_name,
            skipped=skipped,
            raw_text="Позиции не распознаны: бот не распознал в PDF ни одной валидной строки спецификации.",
        )
    positions = build_positions(success)
    out = load_order_to_1c(positions, execute_url, order_comment, timeout=timeout)
    return PipelineResult(
        file_name=file_name,
        order_number=out["order_number"],
        loaded=success,
        skipped=skipped,
        errors_1c=out["errors"],
        warnings_1c=out["warnings"],
        raw_text=out["raw"],
    )
