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
from typing import Dict, List, Optional, Tuple

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
    # «п.м.»/«пм»/«м.п.» и варианты записи погонных метров
    if u in ("м", "m", "метр", "mtr", "пм", "мп"):
        return "м"
    # «к-т.», «компл.» и прочие единицы оставляем как есть
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


def _split_pages(df: pd.DataFrame) -> List[Tuple[int, int]]:
    """Разбивает df на «страницы» по маркерам начала листа.

    Каждый лист проектной спецификации начинается со штампа «Инв. № подл.»
    и заканчивается «Формат А3А», поэтому оба маркера считаем границами.
    """
    starts = [0]
    for idx, row in df.iterrows():
        v = row.iloc[0]
        if pd.isna(v):
            continue
        s = str(v).strip()
        if "Инв. № подл." in s or s == "Формат А3А":
            starts.append(int(idx))
    starts = sorted(set(s for s in starts if s < len(df)))
    return [
        (start, starts[i + 1] if i + 1 < len(starts) else len(df))
        for i, start in enumerate(starts)
    ]


# Слова-типы изделий и признаки размера для эвристики «stacked»-страниц.
_STACKED_TYPE_RE = re.compile(
    r"врезка|отвод|переход|тройник|крестовина|заглушка|ниппель|утка|фланец",
    re.IGNORECASE,
)
_STACKED_SIZE_RE = re.compile(
    r"\d{2,5}\s*[øØ⌀]|[øØ⌀]\s*\d{2,5}|ф\s*\d{2,5}|\d{2,5}\s*[xх×]\s*\d{2,5}",
    re.IGNORECASE,
)


def _is_stacked_name(value: str) -> bool:
    """Наименование изделия на «stacked»-странице (тип + размер)."""
    return bool(_STACKED_TYPE_RE.search(value)) and bool(_STACKED_SIZE_RE.search(value))


def _extract_stacked_rows(df: pd.DataFrame, start: int, end: int) -> List[dict]:
    """Эвристика для «stacked»-страниц: после поворота таблицы FineReader
    колонки стали вертикальными сериями в колонке 0 df.

    Два паттерна:
    - серии отделены блоками (units == qtys > names): парь по индексу name[i]↔qty[i];
    - серии перемежаются: парь по мини-блокам (серии «шт»/чисел после группы имён),
      последняя пара — последнему имени блока, предыдущие — идущим перед ним именам.
    Имена без найденного количества возвращаются с quantity=0 (менеджер допишет
    в редакторе web_app) — раньше такие строки молча терялись.
    """
    names: List[str] = []
    tokens: List[Tuple[str, str]] = []  # (kind, value), kind: name|unit|qty
    for idx in range(start, end):
        v = df.iloc[idx, 0]
        if pd.isna(v):
            continue
        s = str(v).strip()
        if not s:
            continue
        if _is_stacked_name(s):
            tokens.append(("name", s))
            names.append(s)
        elif re.fullmatch(r"шт\.?", s, re.IGNORECASE):
            tokens.append(("unit", s))
        elif re.fullmatch(r"\d+", s):
            tokens.append(("qty", s))

    # Защита от ложных срабатываний на обычных страницах
    if len(names) < 2:
        return []

    units = [v for k, v in tokens if k == "unit"]
    qtys = [v for k, v in tokens if k == "qty"]

    def make_row(name: str, qty: float) -> dict:
        return {
            "name": name,
            "size": "",
            "unit": "шт",
            "quantity": qty,
            "material": "",
            "thickness": "",
        }

    if len(units) == len(qtys) and len(units) > len(names):
        # Паттерн «серии отделены блоками» (например, ОВ.С-6):
        # пары (шт, число) идут отдельными сериями, парь по индексу.
        q = [int(v) for v in qtys]
        if len(q) == len(names) + 1 and q[0] == q[1]:
            # Артефакт дублирования FineReader: лишний первый элемент серии.
            q = q[:1] + q[2:]
        return [make_row(name, float(qty)) for name, qty in zip(names, q)] + [
            make_row(name, 0.0) for name in names[len(q):]
        ]

    # Паттерн «серии перемежаются» (например, ОВ.С-5):
    # поток токенов делится на мини-блоки — группы подряд идущих имён и
    # следующие за ними серии «шт»/чисел. Количества выравниваются по концу
    # блока: последняя пара — последнему имени блока, предыдущие — идущим
    # перед ним именам; лишние ведущие пары отбрасываются.
    assigned: List[Optional[int]] = [None] * len(names)
    run: List[int] = []  # индексы имён текущего мини-блока
    seg_qtys: List[int] = []
    name_pos = -1  # индекс последнего встреченного имени

    def flush() -> None:
        if run and seg_qtys:
            offset = len(run) - len(seg_qtys)
            for k, q in enumerate(seg_qtys):
                j = offset + k
                if j >= 0:
                    assigned[run[j]] = q

    for kind, value in tokens:
        if kind == "name":
            name_pos += 1
            # имя после серии чисел начинает новый мини-блок
            if seg_qtys and run:
                flush()
                seg_qtys = []
                run = []
            run.append(name_pos)
        elif kind == "qty" and name_pos >= 0:
            seg_qtys.append(int(value))
    flush()

    return [
        make_row(name, float(q) if q is not None else 0.0)
        for name, q in zip(names, assigned)
    ]


def parse_project_spec_xlsx(path: str | Path, sheet_name=0) -> List[dict]:
    """Извлекает строки спецификации из распознанного Excel-файла проекта."""
    df = pd.read_excel(path, sheet_name=sheet_name, dtype=str, header=None)
    headers = _find_header_rows(df)

    rows: List[dict] = []

    # Обычный разбор ведём постранично: для каждой «страницы» (листа) по
    # её собственным заголовкам. Дедупликацию НЕ применяем: повторяющиеся
    # позиции из разных секций (В-1 и П-1) — отдельные строки, как в КП.
    for page_start, page_end in _split_pages(df):
        page_headers = [h for h in headers if page_start <= h < page_end]
        page_rows = 0

        for h_idx in page_headers:
            mapping = _map_header(df.iloc[h_idx])
            if "name" not in mapping or "unit" not in mapping or "quantity" not in mapping:
                continue

            name_col = mapping["name"]
            model_col = mapping.get("model")
            unit_col = mapping["unit"]
            qty_col = mapping["quantity"]

            for idx in range(h_idx + 1, page_end):
                if idx in headers:
                    break

                r = df.iloc[idx]
                name = str(r[name_col]).strip() if pd.notna(r[name_col]) else ""
                # NaN в колонке «Тип, марка» не должен превращаться в «nan»
                model = (
                    _clean_model(str(r[model_col]))
                    if model_col is not None and pd.notna(r[model_col])
                    else None
                )
                unit = _normalize_unit(str(r[unit_col])) if pd.notna(r[unit_col]) else ""
                qty = _to_float(r[qty_col])

                if not name:
                    continue
                # qty == 0 отсекает пустые/нечисловые значения, pd.isna — строки
                # штампа листа («Изм.», «Кол.уч.» ...), у которых _to_float даёт NaN
                if not unit or qty == 0 or pd.isna(qty):
                    continue

                full_name = name
                if model and model.upper() not in name.upper():
                    full_name = f"{name} {model}".strip()

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
                page_rows += 1

        # Страница с повёрнутой FineReader таблицей: обычный разбор по
        # заголовкам не дал строк — применяем «stacked»-эвристику к колонке 0.
        if page_rows == 0:
            rows.extend(_extract_stacked_rows(df, page_start, page_end))

    return rows


def is_project_spec_xlsx(df: pd.DataFrame) -> bool:
    """Эвристика: распознанный проектный файл, а не обычная CSV/Excel-спецификация."""
    needles = (
        "наименование и техническая характеристика",
        "тип, марка, обозначение документа",
    )
    for col in df.columns:
        col_text = str(col).lower()
        if any(needle in col_text for needle in needles):
            return True
        vals = df[col].astype(str).str.lower()
        if any(vals.str.contains(needle).any() for needle in needles):
            return True
    return False
