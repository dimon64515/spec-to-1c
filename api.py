"""Service layer for spec-to-1c file processing.

This module contains the core business logic that does not depend on
Streamlit. It can be imported by scripts, tests, and alternative UIs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Dict, List, Optional

import fitz
import pandas as pd

from pdf_spec_extractor import (
    df_to_spec_rows,
    extract_drawing_spec_rows,
    extract_ocr_spec_rows,
    extract_tables_from_pdf,
    extract_text_lines_from_pdf,
    normalize_columns,
    parse_spec_text_blocks,
    parse_text_fallback,
)
from equipment_pdf_extractor import extract_equipment_from_pdf
from map_customer_equipment import map_equipment_rows
from process_specification_table import process_rows
from project_spec_xlsx import is_project_spec_xlsx, parse_project_spec_xlsx
from zayavka_xlsx import is_zayavka_xlsx, parse_zayavka_xlsx


@dataclass
class ProcessResult:
    """Result of processing a specification file."""

    xml: str = ""
    skipped: List[Dict[str, Any]] = field(default_factory=list)
    tables: List[pd.DataFrame] = field(default_factory=list)
    text_fallback: Optional[Dict[int, List[str]]] = None
    equipment_skipped: List[Dict[str, Any]] = field(default_factory=list)


def load_tables_from_pdf(
    file_bytes: bytes,
    selected_pages: Optional[List[int]] = None,
    ocr_cache_path: Optional[str] = None,
):
    """Save PDF bytes to a temporary file and extract tables or text fallback.

    Если таблицы найдены, но строки съезжаются (меньше 25% строк имеют и имя,
    и размер — типично для ГОСТ-бланков с многострочными ячейками), падаем
    в разбор текстового слоя: сначала позиционный разбор встроенной в чертёж
    таблицы «Спецификация изделий и материалов» (extract_drawing_spec_rows),
    при её отсутствии — блоками по текстовому слою (parse_spec_text_blocks).
    Страницы без текстового слоя вообще (текст преобразован в кривые), но с
    сеткой ведомости ГОСТ 21.602 разбираются через OCR (extract_ocr_spec_rows,
    tesseract); результаты кэшируются в ocr_cache_path, чтобы повторные
    прогоны не перезапускали распознавание.
    Возвращаем {"text_fallback": ..., "block_rows": [...], "ocr_pages": [...]}.
    """
    with NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(file_bytes)
        tmp_path = Path(tmp.name)
    try:
        tables_by_page = extract_tables_from_pdf(str(tmp_path), pages=selected_pages)
        if any(tables_by_page.values()):
            total_rows = 0
            valid_rows = 0
            for tables in tables_by_page.values():
                for df in tables:
                    total_rows += len(df)
                    for row in df_to_spec_rows(df):
                        if str(row.get("name", "")).strip() and str(row.get("size", "")).strip():
                            valid_rows += 1
            if total_rows and valid_rows / total_rows >= 0.25:
                return {"tables": tables_by_page}
        text_by_page = extract_text_lines_from_pdf(str(tmp_path), pages=selected_pages)
        all_lines = [line for page in sorted(text_by_page) for line in text_by_page[page]]
        # Чертёж с встроенной таблицей «Спецификация изделий и материалов»:
        # строки восстанавливаются по координатам колонок точнее, чем блоками
        # по текстовому слою; если таблица найдена — блокный разбор не нужен
        # (иначе те же позиции продублируются).
        drawing_rows = extract_drawing_spec_rows(str(tmp_path), pages=selected_pages)
        if drawing_rows:
            block_rows = [
                row for page in sorted(drawing_rows) for row in drawing_rows[page]
            ]
        else:
            block_rows = parse_spec_text_blocks(all_lines)
        # OCR-фолбэк: страницы-ведомости без текстового слоя (векторные
        # кривые вместо текста). Страницы с текстовым слоем и страницы без
        # сетки ведомости (чертежи, планы) внутри функции пропускаются.
        ocr_rows = extract_ocr_spec_rows(
            str(tmp_path), pages=selected_pages, cache_path=ocr_cache_path)
        if ocr_rows:
            block_rows = block_rows + [
                row for page in sorted(ocr_rows) for row in ocr_rows[page]
            ]
        return {
            "text_fallback": text_by_page,
            "block_rows": block_rows,
            "ocr_pages": sorted(ocr_rows),
        }
    finally:
        tmp_path.unlink(missing_ok=True)


def extract_equipment_from_bytes(
    file_bytes: bytes, selected_pages: Optional[List[int]] = None
) -> tuple[List[dict], List[dict]]:
    """Extract equipment rows from a PDF and map them to 1C articles."""
    with NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(file_bytes)
        tmp_path = Path(tmp.name)
    try:
        raw_rows = extract_equipment_from_pdf(str(tmp_path), pages=selected_pages)
        return map_equipment_rows(raw_rows)
    finally:
        tmp_path.unlink(missing_ok=True)


def read_uploaded_csv_or_excel(uploaded_file) -> pd.DataFrame:
    """Read an uploaded CSV/Excel file into a DataFrame."""
    name = uploaded_file.name.lower()
    if name.endswith((".xlsx", ".xls", ".xlsm")):
        return pd.read_excel(uploaded_file, dtype=str)
    return pd.read_csv(uploaded_file, delimiter=";", dtype=str, encoding="utf-8-sig")


def read_csv_or_excel_bytes(file_bytes: bytes, file_name: str) -> pd.DataFrame:
    """Read CSV/Excel from bytes."""
    name = file_name.lower()
    buffer = pd.io.common.BytesIO(file_bytes)
    if name.endswith((".xlsx", ".xls", ".xlsm")):
        return pd.read_excel(buffer, dtype=str)
    return pd.read_csv(buffer, delimiter=";", dtype=str, encoding="utf-8-sig")


def read_project_spec_bytes(file_bytes: bytes, file_name: str) -> list[dict]:
    """Read a recognised project-specification Excel file into spec rows."""
    suffix = Path(file_name).suffix or ".xlsx"
    with NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(file_bytes)
        tmp_path = Path(tmp.name)
    try:
        return parse_project_spec_xlsx(tmp_path)
    finally:
        tmp_path.unlink(missing_ok=True)


def read_zayavka_bytes(file_bytes: bytes, file_name: str) -> list[dict]:
    """Read a manager «заявка» Excel (loose per-system position list)."""
    suffix = Path(file_name).suffix or ".xlsx"
    with NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(file_bytes)
        tmp_path = Path(tmp.name)
    try:
        return parse_zayavka_xlsx(tmp_path)
    finally:
        tmp_path.unlink(missing_ok=True)


def count_pdf_pages(file_bytes: bytes) -> int:
    """Return the number of pages in a PDF byte stream."""
    with NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(file_bytes)
        tmp_path = Path(tmp.name)
    try:
        with fitz.open(str(tmp_path)) as doc:
            return doc.page_count
    finally:
        tmp_path.unlink(missing_ok=True)


def process_specification_file(
    file_bytes: bytes,
    file_name: str,
    header: Optional[Dict[str, str]] = None,
    options: Optional[Dict[str, Any]] = None,
) -> ProcessResult:
    """Process an uploaded specification file and return XML + skipped rows.

    This is a high-level entry point for CSV/Excel files. PDF processing
    usually requires user interaction (page/table selection) and is better
    handled through ``load_tables_from_pdf`` and ``process_rows`` directly.
    """
    options = options or {}
    header = header or {}
    df = read_csv_or_excel_bytes(file_bytes, file_name)
    is_excel = file_name.lower().endswith((".xlsx", ".xls", ".xlsm"))
    if is_excel and is_project_spec_xlsx(df):
        # Распознанный проектный Excel (FineReader) с ГОСТ-разметкой: колонки
        # не совпадают с name/size/unit/quantity, разбираем постранично.
        rows = read_project_spec_bytes(file_bytes, file_name)
    elif is_excel:
        # Заявка менеджера: без шапки колонок, секции-системы, единицы
        # «шт»/«п.м.»/«м2»/«пара». Детект требует df с header=None.
        df_raw = pd.read_excel(
            pd.io.common.BytesIO(file_bytes), header=None, dtype=object)
        if is_zayavka_xlsx(df_raw):
            rows = read_zayavka_bytes(file_bytes, file_name)
        else:
            df = normalize_columns(df)
            rows = df_to_spec_rows(df)
    else:
        df = normalize_columns(df)
        rows = df_to_spec_rows(df)
    xml_text, skipped, _ = process_rows(rows, header=header)
    return ProcessResult(xml=xml_text, skipped=skipped)
