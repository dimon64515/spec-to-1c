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


def recreate_order_from_report(
    content: bytes,
    execute_url: str,
    base_comment: str = "",
    timeout: float = 280.0,
) -> PipelineResult:
    """Пересоздать заказ из отредактированного Excel-отчёта (round-trip).

    Лист «Загружено» → позиции напрямую; позиции с «Включить в заказ» = да
    из «Пропущено»/«Перекупного» проходят повторный разбор process_rows
    (подбор аналога). Создаётся НОВЫЙ заказ; старый (replaced_order) удаляет
    менеджер вручную — в 1С ничего не обновляем.
    """
    from process_specification_table import process_rows
    from json_positions import build_positions
    from report_xlsx import EditedReportError, parse_edited_report

    edited = parse_edited_report(content)

    extra_success: List[Dict[str, Any]] = []
    extra_skipped: List[Dict[str, Any]] = []
    if edited.include_rows:
        _, extra_skipped, extra_success = process_rows(edited.include_rows)

    success = edited.loaded_rows + extra_success
    if not success:
        raise EditedReportError(
            "Нечего загружать: лист «Загружено» пуст, а включённые позиции "
            "не дали ни одного аналога."
        )

    comment = base_comment or "Заказ из Excel-отчёта"
    if edited.replaced_order:
        comment += f" | Заменяет заказ №{edited.replaced_order} (исправлено из отчёта)"

    positions = build_positions(success)
    out = load_order_to_1c(positions, execute_url, comment, timeout=timeout)
    # Включённые позиции из skipped_rows (маркер "_include") исключаем:
    # они либо уже в extra_success, либо дублируются в extra_skipped с причиной.
    skipped = [s for s in edited.skipped_rows if not s.get("_include")]
    return PipelineResult(
        file_name=f"edited_report_{edited.replaced_order or 'new'}",
        order_number=out["order_number"],
        loaded=success,
        skipped=skipped + extra_skipped,
        errors_1c=out["errors"],
        warnings_1c=out["warnings"],
        raw_text=out["raw"],
    )
