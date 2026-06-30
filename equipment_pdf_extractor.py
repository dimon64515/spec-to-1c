#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
equipment_pdf_extractor.py

Извлечение ведомостей оборудования из PDF проектов заказчиков.

Основные особенности:
- работает с PDF, где заголовки таблиц числовые или отсутствуют;
- определяет роли столбцов по содержимому (наименование, производитель, ед.изм., количество);
- объединяет многострочные ячейки;
- фильтрует служебные строки ("ОБОРУДОВАНИЕ", "КИПиА", названия систем и т.п.).
"""

import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd


# ---------------------------------------------------------------------------
# Регулярные выражения
# ---------------------------------------------------------------------------

UNIT_RE = re.compile(r"^(шт\.?|м|м2|м²|кв\.м|кв м|pcs|pc|m|m2)$", re.IGNORECASE)
QTY_RE = re.compile(r"^\d{1,6}([\s,\.]*\d{0,3})?$")
MANUFACTURER_RE = re.compile(r"^[A-ZА-Я]{1,10}(\s+[A-ZА-Я]{1,10})?$")
SYSTEM_NAME_RE = re.compile(r"^(П\d+(\.\d+)?\s*\(.*\)|ОБОРУДОВАНИЕ|КИПиА|Лист|Приложение|Спецификация|Тип, марка|Позиция|Наименование)", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Извлечение таблиц
# ---------------------------------------------------------------------------

def extract_tables_from_pdf(
    pdf_path: str,
    pages: Optional[List[int]] = None,
) -> Dict[int, List[pd.DataFrame]]:
    """Извлекает таблицы с указанных страниц PDF через PyMuPDF."""
    import fitz  # PyMuPDF

    result: Dict[int, List[pd.DataFrame]] = {}
    with fitz.open(pdf_path) as doc:
        total_pages = doc.page_count
        page_indices = (
            [p - 1 for p in pages if 1 <= p <= total_pages]
            if pages is not None
            else range(total_pages)
        )
        for idx in page_indices:
            page = doc[idx]
            finder = page.find_tables()
            tables = finder.tables if hasattr(finder, "tables") else list(finder)
            page_num = idx + 1
            result[page_num] = []
            for table in tables:
                df = table.to_pandas()
                if not df.empty:
                    result[page_num].append(df)
    return result


# ---------------------------------------------------------------------------
# Определение ролей столбцов
# ---------------------------------------------------------------------------

def _to_str(value) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    return str(value).strip()


def _is_unit(text: str) -> bool:
    return bool(UNIT_RE.match(text.strip()))


def _is_quantity(text: str) -> bool:
    t = text.strip().replace(" ", "").replace(",", ".")
    if not t:
        return False
    try:
        float(t)
        return True
    except ValueError:
        return False


def _is_manufacturer(text: str) -> bool:
    t = text.strip()
    return len(t) <= 15 and bool(MANUFACTURER_RE.match(t)) and t.isupper()


def _is_name(text: str) -> bool:
    """Столбец с наименованием содержит длинный текст с буквами и цифрами."""
    t = text.strip()
    if len(t) < 5:
        return False
    has_letters = bool(re.search(r"[a-zа-яё]", t, re.IGNORECASE))
    has_digits = bool(re.search(r"\d", t))
    return has_letters and (has_digits or len(t) > 20)


def detect_column_roles(df: pd.DataFrame) -> Dict[str, Optional[int]]:
    """Определяет роли столбцов DataFrame по содержимому.

    Возвращает словарь {role: col_index}:
        - "name" — наименование + модель
        - "manufacturer" — производитель
        - "unit" — единица измерения
        - "quantity" — количество
        - "pos" — порядковый номер (опционально)
    """
    if df is None or df.empty:
        return {}

    scores: Dict[str, Dict[int, float]] = {
        "unit": {},
        "quantity": {},
        "manufacturer": {},
        "name": {},
        "pos": {},
    }

    for col_idx, col in enumerate(df.columns):
        unit_score = 0.0
        qty_score = 0.0
        mfr_score = 0.0
        name_score = 0.0
        pos_score = 0.0

        non_empty = 0
        for value in df[col]:
            text = _to_str(value)
            if not text:
                continue
            non_empty += 1

            if _is_unit(text):
                unit_score += 1.0
            if _is_quantity(text):
                qty_score += 1.0
            if _is_manufacturer(text):
                mfr_score += 1.0
            if _is_name(text):
                name_score += 1.0
            if re.match(r"^\d{1,4}$", text):
                pos_score += 1.0

        # Нормализуем относительно непустых ячеек
        if non_empty:
            scores["unit"][col_idx] = unit_score
            scores["quantity"][col_idx] = qty_score
            scores["manufacturer"][col_idx] = mfr_score
            scores["name"][col_idx] = name_score
            scores["pos"][col_idx] = pos_score

    roles: Dict[str, Optional[int]] = {}

    def best(role: str, exclude: Optional[int] = None) -> Optional[int]:
        items = {k: v for k, v in scores[role].items() if v > 0 and k != exclude}
        if not items:
            return None
        return max(items, key=items.get)

    roles["unit"] = best("unit")
    roles["quantity"] = best("quantity", exclude=roles["unit"])
    roles["manufacturer"] = best("manufacturer")
    roles["name"] = best("name")
    roles["pos"] = best("pos")

    return roles


# ---------------------------------------------------------------------------
# Извлечение строк из одной таблицы
# ---------------------------------------------------------------------------

def _clean_row_cells(row: pd.Series) -> List[str]:
    return [_to_str(v) for v in row.values]


def _merge_sparse_rows(rows: List[List[str]], name_col: int) -> List[List[str]]:
    """Объединяет соседние строки, если вторая содержит только данные,
    а первая — только наименование (пустые ячейки в qty/unit)."""
    if not rows:
        return rows

    merged: List[List[str]] = []
    pending: Optional[List[str]] = None

    for row in rows:
        if not any(row):
            continue

        if pending is not None:
            # Если текущая строка не содержит наименования, но содержит
            # производителя/ед.изм./количество — сливаем с pending.
            has_name = bool(row[name_col].strip())
            has_service = any(
                _is_unit(c) or _is_quantity(c) or _is_manufacturer(c)
                for i, c in enumerate(row)
                if i != name_col
            )
            if not has_name and has_service:
                for i, val in enumerate(row):
                    if val.strip():
                        pending[i] = val.strip()
                continue
            else:
                merged.append(pending)
                pending = None

        if row[name_col].strip():
            pending = list(row)
        else:
            merged.append(row)

    if pending is not None:
        merged.append(pending)

    return merged


def _is_service_row(row_texts: List[str]) -> bool:
    """Пропускает служебные строки: заголовки, названия систем, пустые, числовые."""
    joined = " ".join(t.strip() for t in row_texts if t.strip())
    if not joined:
        return True
    if SYSTEM_NAME_RE.match(joined):
        return True
    if joined.lower() in ("оборудование", "кипиа", "лист", "приложение"):
        return True
    # Чисто числовая строка (с возможными пробелами) — это остаток заголовка
    if re.match(r"^[\d\s,]+$", joined) and re.search(r"\d", joined):
        return True
    return False


def extract_equipment_rows_from_table(df: pd.DataFrame) -> List[Dict[str, str]]:
    """Извлекает строки оборудования из одной таблицы DataFrame."""
    if df is None or df.empty:
        return []

    roles = detect_column_roles(df)
    name_col = roles.get("name")
    qty_col = roles.get("quantity")
    unit_col = roles.get("unit")
    mfr_col = roles.get("manufacturer")

    if name_col is None or qty_col is None:
        return []

    # Превращаем DataFrame в список строк-списков
    raw_rows = [_clean_row_cells(row) for _, row in df.iterrows()]
    raw_rows = [r for r in raw_rows if not _is_service_row(r)]
    raw_rows = _merge_sparse_rows(raw_rows, name_col)

    result: List[Dict[str, str]] = []
    for row in raw_rows:
        if _is_service_row(row):
            continue

        name = row[name_col].strip()
        qty_text = row[qty_col].strip() if qty_col is not None else ""
        unit = row[unit_col].strip() if unit_col is not None else ""
        manufacturer = row[mfr_col].strip() if mfr_col is not None else ""

        if not name or not qty_text:
            continue

        # Имя не должно быть просто числом (остаток заголовка)
        if re.match(r"^\d+([,\s]\d+)*$", name):
            continue

        # Если единица не определена, попробуем найти в соседних колонках
        if not unit:
            for i, val in enumerate(row):
                if i in (name_col, qty_col, mfr_col):
                    continue
                if _is_unit(val):
                    unit = val.strip()
                    break

        result.append({
            "raw_name": name,
            "model": "",  # будет заполнено позже парсером
            "manufacturer": manufacturer,
            "unit": unit or "шт",
            "quantity": qty_text,
        })

    return result


# ---------------------------------------------------------------------------
# Публичный API
# ---------------------------------------------------------------------------

def extract_equipment_from_pdf(
    pdf_path: str,
    pages: Optional[List[int]] = None,
) -> List[Dict[str, str]]:
    """Извлекает все строки оборудования из PDF-файла.

    Args:
        pdf_path: путь к PDF.
        pages: список номеров страниц (с 1). Если None — все страницы.

    Returns:
        Список словарей с ключами raw_name, model, manufacturer, unit, quantity.
    """
    tables_by_page = extract_tables_from_pdf(pdf_path, pages=pages)
    all_rows: List[Dict[str, str]] = []
    for page_num, tables in tables_by_page.items():
        for df in tables:
            rows = extract_equipment_rows_from_table(df)
            all_rows.extend(rows)
    return all_rows


# ---------------------------------------------------------------------------
# Тест
# ---------------------------------------------------------------------------

def main():
    from glob import glob
    pdf_files = glob("*.pdf")
    if not pdf_files:
        print("PDF-файлы в рабочей директории не найдены.")
        return

    pdf_path = pdf_files[0]
    rows = extract_equipment_from_pdf(pdf_path)
    print(f"Извлечено строк оборудования из {pdf_path}: {len(rows)}")
    for row in rows[:20]:
        print(row)


if __name__ == "__main__":
    main()
