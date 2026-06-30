#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pdf_spec_extractor.py

Модуль извлечения таблиц спецификации из PDF.

Использует PyMuPDF (fitz) для поиска таблиц и pandas для их представления.
Предоставляет fallback на построчный текст, если таблицы не найдены.
"""

import re
from glob import glob
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd


def extract_tables_from_pdf(
    pdf_path: str,
    pages: Optional[List[int]] = None,
) -> Dict[int, List[pd.DataFrame]]:
    """Извлекает таблицы из PDF с указанных страниц.

    Args:
        pdf_path: путь к PDF-файлу.
        pages: список номеров страниц (нумерация с 1). Если None — все страницы.

    Returns:
        Словарь {номер_страницы: [DataFrame1, DataFrame2, ...]}.
    """
    result: Dict[int, List[pd.DataFrame]] = {}

    try:
        import fitz  # PyMuPDF
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "PyMuPDF (fitz) не установлен. Установите: pip install pymupdf"
        ) from exc

    with fitz.open(pdf_path) as doc:
        total_pages = doc.page_count
        page_indices = (
            [p - 1 for p in pages if 1 <= p <= total_pages]
            if pages is not None
            else range(total_pages)
        )

        for idx in page_indices:
            page = doc[idx]
            tables = page.find_tables()
            page_num = idx + 1
            result[page_num] = []
            for table in tables:
                df = table.to_pandas()
                result[page_num].append(df)

    return result


def extract_text_lines_from_pdf(
    pdf_path: str,
    pages: Optional[List[int]] = None,
) -> Dict[int, List[str]]:
    """Извлекает текст страниц PDF построчно.

    Args:
        pdf_path: путь к PDF-файлу.
        pages: список номеров страниц (нумерация с 1). Если None — все страницы.

    Returns:
        Словарь {номер_страницы: [строка1, строка2, ...]}.
    """
    result: Dict[int, List[str]] = {}

    try:
        import fitz  # PyMuPDF
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "PyMuPDF (fitz) не установлен. Установите: pip install pymupdf"
        ) from exc

    with fitz.open(pdf_path) as doc:
        total_pages = doc.page_count
        page_indices = (
            [p - 1 for p in pages if 1 <= p <= total_pages]
            if pages is not None
            else range(total_pages)
        )

        for idx in page_indices:
            page = doc[idx]
            text = page.get_text()
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            result[idx + 1] = lines

    return result


# --- Нормализация столбцов ---

COLUMN_KEYWORDS = {
    "name": ["наименование", "изделие", "описание", "позиция", "продукт"],
    "size": ["размер", "сечение", "диаметр", "габарит", "типоразмер"],
    "unit": ["ед.изм", "единица", "ед", "unit", "изм"],
    "quantity": ["количество", "кол-во", "qty", "кол"],
    "material": ["материал", "сталь", "мат"],
    "thickness": ["толщина", "толщ", "мм"],
}

DEFAULT_MATERIAL = "оцинкованная"
DEFAULT_THICKNESS = 0.8


def _normalize_header(header: str) -> str:
    """Приводит заголовок к нижнему регистру, убирает лишние пробелы и символы."""
    text = str(header).lower().strip()
    text = text.replace("ё", "е")
    text = re.sub(r"[._\\/|\\-]+", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text


def _detect_unit_by_size(size: str) -> str:
    """Определяет единицу измерения по размеру, если не задана явно."""
    size_lower = str(size).lower()
    # Если размер содержит D/DN/Ф или число — скорее всего воздуховод/фасонка в метрах
    if re.search(r"(?:^|\s)(?:d|dn|ф)?\s*\d+", size_lower, re.IGNORECASE):
        return "м"
    return "шт"


def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Переименовывает столбцы PDF-таблицы в стандартные name/size/unit/quantity/material/thickness.

    Удаляет полностью пустые строки и столбцы. Если столбец не найден — он будет
    добавлен со значениями NaN.
    """
    if df is None or df.empty:
        return pd.DataFrame(columns=list(COLUMN_KEYWORDS.keys()))

    # Работаем с копией, чтобы не менять оригинал
    df = df.copy()

    # Удаляем полностью пустые столбцы и строки
    df = df.dropna(axis=1, how="all").dropna(axis=0, how="all")

    if df.empty:
        return pd.DataFrame(columns=list(COLUMN_KEYWORDS.keys()))

    mapping: Dict[str, str] = {}
    used_targets = set()

    for col in df.columns:
        normalized = _normalize_header(col)
        target = None
        for tcol, keywords in COLUMN_KEYWORDS.items():
            if tcol in used_targets:
                continue
            if any(kw in normalized for kw in keywords):
                target = tcol
                break

        if target:
            mapping[str(col)] = target
            used_targets.add(target)

    df = df.rename(columns=mapping)

    # Добавляем отсутствующие стандартные столбцы
    for col in COLUMN_KEYWORDS.keys():
        if col not in df.columns:
            df[col] = pd.NA

    return df[list(COLUMN_KEYWORDS.keys())]


def _to_float(value, default: float) -> float:
    """Безопасное преобразование значения в float."""
    if value is None:
        return default
    try:
        if pd.isna(value):
            return default
    except (TypeError, ValueError):
        pass
    text = str(value).strip().replace(" ", "").replace(",", ".")
    if not text:
        return default
    try:
        return float(text)
    except ValueError:
        return default


def _clean_str(value) -> str:
    """Безопасное преобразование значения в строку."""
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    return str(value).strip()


def df_to_spec_rows(df: pd.DataFrame) -> List[dict]:
    """Преобразует DataFrame в список словарей для process_specification_table.

    Ключи: name, size, unit, quantity, material, thickness.
    Пустые material/thickness заменяются на значения по умолчанию.
    """
    df = normalize_columns(df)
    rows: List[dict] = []

    for record in df.to_dict("records"):
        name = _clean_str(record.get("name"))
        size = _clean_str(record.get("size"))
        unit = _clean_str(record.get("unit"))
        quantity = _to_float(record.get("quantity"), 0.0)
        material = _clean_str(record.get("material")) or DEFAULT_MATERIAL
        thickness = _to_float(record.get("thickness"), DEFAULT_THICKNESS)

        if not unit:
            unit = _detect_unit_by_size(size)

        rows.append({
            "name": name,
            "size": size,
            "unit": unit,
            "quantity": quantity,
            "material": material,
            "thickness": thickness,
        })

    return rows


# --- Fallback: разбор текста ---

# Регулярные выражения для извлечения позиций из строки текста.
# Поддерживаются строки вида:
#   "Воздуховод оцинкованный 100 мм 400 м"
#   "Отвод D160 10 шт"
#   "3 Отвод круглый 90 градусов D160 шт 10"
FALLBACK_PATTERNS = [
    # С явным разделителем-табуляцией/несколькими пробелами и числом в конце
    re.compile(
        r"(?P<name>.+?)\s{2,}(?P<size>(?:\d{2,5}(?:\s*[xх×*]\s*\d{2,5})?|\b[dDдД][Nn]?\s*\d{2,5}|\bФ\s*\d{2,5}))\s+(?P<unit>м|шт|м2|м²|шт\.|шток|pcs|pc|m)\s+(?P<quantity>\d+(?:[.,]\d+)?)",
        re.IGNORECASE,
    ),
    # Общий случай: текст, затем размер, затем единица и количество
    re.compile(
        r"(?P<name>.+?)\s+(?P<size>\d{2,5}(?:\s*[xх×*]\s*\d{2,5})?|\b[dDдД][Nn]?\s*\d{2,5}|\bФ\s*\d{2,5})\s+(?P<unit>м|шт|м2|м²|шт\.|шток|pcs|pc|m)\s+(?P<quantity>\d+(?:[.,]\d+)?)",
        re.IGNORECASE,
    ),
]


def parse_text_fallback(lines: List[str]) -> List[dict]:
    """Пытается разобрать строки текста в формат спецификации.

    Распознаёт простые строки вида: '<наименование> <размер> <единица> <количество>'.
    Возвращает список словарей с ключами name, size, unit, quantity, material, thickness.
    """
    rows: List[dict] = []

    for line in lines:
        line = line.strip()
        if not line or len(line) < 5:
            continue

        for pattern in FALLBACK_PATTERNS:
            match = pattern.search(line)
            if match:
                name = match.group("name").strip(" -;:")
                size = match.group("size").strip()
                unit = match.group("unit").strip().lower()
                quantity = _to_float(match.group("quantity"), 0.0)

                # Пытаемся найти материал и толщину в наименовании
                name_lower = name.lower()
                material = DEFAULT_MATERIAL
                if "нерж" in name_lower:
                    material = "нержавеющая"
                elif "черн" in name_lower:
                    material = "черная"
                elif "оц" in name_lower:
                    material = "оцинкованная"

                thickness = DEFAULT_THICKNESS
                t_match = re.search(r"(\d+(?:[.,]\d+)?)\s*мм", line, re.IGNORECASE)
                if t_match:
                    thickness = _to_float(t_match.group(1), DEFAULT_THICKNESS)

                rows.append({
                    "name": name,
                    "size": size,
                    "unit": unit,
                    "quantity": quantity,
                    "material": material,
                    "thickness": thickness,
                })
                break

    return rows


def main():
    """Простой тест: ищет PDF в рабочей директории и выводит статистику по таблицам."""
    pdf_files = glob("*.pdf")
    if not pdf_files:
        print("PDF-файлы в рабочей директории не найдены. Тест пропущен.")
        return

    pdf_path = pdf_files[0]
    print(f"Тестовый файл: {pdf_path}")

    try:
        tables_by_page = extract_tables_from_pdf(pdf_path)
        total_tables = sum(len(tables) for tables in tables_by_page.values())
        total_rows = 0

        for page_num, tables in tables_by_page.items():
            print(f"Страница {page_num}: найдено таблиц — {len(tables)}")
            for i, df in enumerate(tables, start=1):
                rows = df_to_spec_rows(df)
                total_rows += len(rows)
                print(f"  Таблица {i}: {len(df)} строк, {len(rows)} распознано")

        print(f"Всего таблиц: {total_tables}, распознано строк: {total_rows}")

    except Exception as exc:  # pragma: no cover
        print(f"Ошибка при извлечении таблиц: {exc}")


if __name__ == "__main__":
    main()
