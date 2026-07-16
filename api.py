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
    extract_tables_from_pdf,
    extract_text_lines_from_pdf,
    normalize_columns,
    parse_text_fallback,
)
from equipment_pdf_extractor import extract_equipment_from_pdf
from map_customer_equipment import map_equipment_rows
from process_specification_table import process_rows
from project_spec_xlsx import is_project_spec_xlsx, parse_project_spec_xlsx


@dataclass
class ProcessResult:
    """Result of processing a specification file."""

    xml: str = ""
    skipped: List[Dict[str, Any]] = field(default_factory=list)
    tables: List[pd.DataFrame] = field(default_factory=list)
    text_fallback: Optional[Dict[int, List[str]]] = None
    equipment_skipped: List[Dict[str, Any]] = field(default_factory=list)


def load_tables_from_pdf(file_bytes: bytes, selected_pages: Optional[List[int]] = None):
    """Save PDF bytes to a temporary file and extract tables or text fallback."""
    with NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(file_bytes)
        tmp_path = Path(tmp.name)
    try:
        tables_by_page = extract_tables_from_pdf(str(tmp_path), pages=selected_pages)
        if not any(tables_by_page.values()):
            text_by_page = extract_text_lines_from_pdf(str(tmp_path), pages=selected_pages)
            return {"text_fallback": text_by_page}
        return {"tables": tables_by_page}
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
    df = normalize_columns(df)
    rows = df_to_spec_rows(df)
    xml_text, skipped, _ = process_rows(rows, header=header)
    return ProcessResult(xml=xml_text, skipped=skipped)
