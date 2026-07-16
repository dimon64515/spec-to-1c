#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Парсер распознанного проектного Excel-файла спецификации ОВ.

Файл получается после распознавания PDF (например, через FineReader/ABBYY):
- заголовки таблиц повторяются на каждой странице,
- данные разбиты на несколько секций,
- строки содержат позицию, наименование, тип/марку, единицу измерения и количество.
"""

import re
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd


def _find_header_rows(df: pd.DataFrame) -> List[int]:
    """Возвращает индексы строк, в которых есть заголовок таблицы."""
    headers: List[int] = []
    for idx, row in df.iterrows():
        vals = [str(v).lower() if pd.notna(v) else "" for v in row.values]
        if any("наименование" in v for v in vals):
            headers.append(int(idx))
    return headers


def _map_header(row: pd.Series) -> Dict[str, int]:
    """По строке заголовка определяет номера столбцов."""
    mapping: Dict[str, int] = {}
    for col, val in row.items():
        if pd.isna(val):
            continue
        text = str(val).lower()
        if "наименование" in text:
            mapping["name"] = int(col)
        elif re.search(r"\bтип\b|марка|обозначение", text):
            mapping["model"] = int(col)
        elif "ед" in text and "изм" in text:
            mapping["unit"] = int(col)
        elif "кол" in text and ("во" in text or "-во" in text):
            mapping["quantity"] = int(col)
        elif "поз" in text:
            mapping["pos"] = int(col)
    return mapping


def _clean_model(model: str) -> Optional[str]:
    """Убирает из модели бесполезные значения ('0', 'ГОСТ ...')."""
    m = model.strip()
    if not m:
        return None
    first = m.split()[0]
    if first in ("0", "-") or first.startswith("ГОСТ"):
        return None
    return m


def _normalize_unit(unit: str) -> str:
    """Приводит единицу измерения к стандартному виду."""
    u = unit.strip().lower().replace(".", "").replace(" ", "")
    if u in ("м2", "м²", "кв.м", "квм", "m2"):
        return "м²"
    if u in ("шт", "штк", "pcs", "pc", "шт"):
        return "шт"
    if u in ("м", "m", "метр", "mtr"):
        return "м"
    return unit.strip()


def _to_float(value) -> float:
    if value is None:
        return 0.0
    text = str(value).strip().replace(" ", "").replace(",", ".")
    if not text:
        return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0


def parse_project_spec_xlsx(path: str | Path, sheet_name=0) -> List[dict]:
    """Извлекает строки спецификации из распознанного Excel-файла проекта."""
    df = pd.read_excel(path, sheet_name=sheet_name, dtype=str, header=None)
    headers = _find_header_rows(df)

    rows: List[dict] = []
    seen: set = set()

    for h_idx in headers:
        mapping = _map_header(df.iloc[h_idx])
        if "name" not in mapping or "unit" not in mapping or "quantity" not in mapping:
            continue

        name_col = mapping["name"]
        model_col = mapping.get("model")
        unit_col = mapping["unit"]
        qty_col = mapping["quantity"]

        for idx in range(h_idx + 1, len(df)):
            if idx in headers:
                break

            r = df.iloc[idx]
            name = str(r[name_col]).strip() if pd.notna(r[name_col]) else ""
            model = _clean_model(str(r[model_col])) if model_col is not None else None
            unit = _normalize_unit(str(r[unit_col])) if pd.notna(r[unit_col]) else ""
            qty = _to_float(r[qty_col])

            if not name:
                continue
            if not unit or qty == 0:
                continue

            full_name = name
            if model and model.upper() not in name.upper():
                full_name = f"{name} {model}".strip()

            # Дедупликация по нормализованному имени + единица измерения.
            key = (re.sub(r"\s+", " ", full_name).lower(), unit.lower())
            if key in seen:
                continue
            seen.add(key)

            rows.append(
                {
                    "name": full_name,
                    "size": "",
                    "unit": unit,
                    "quantity": qty,
                    "material": "",
                    "thickness": "",
                }
            )

    return rows


def is_project_spec_xlsx(df: pd.DataFrame) -> bool:
    """Эвристика: распознанный проектный файл, а не обычная CSV/Excel-спецификация."""
    for col in df.columns:
        vals = df[col].astype(str).str.lower()
        if vals.str.contains("наименование и техническая характеристика").any():
            return True
        if vals.str.contains("тип, марка, обозначение документа").any():
            return True
    return False
