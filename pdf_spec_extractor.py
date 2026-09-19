#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pdf_spec_extractor.py

Модуль извлечения таблиц спецификации из PDF.

Использует PyMuPDF (fitz) для поиска таблиц и pandas для их представления.
Предоставляет fallback на построчный текст, если таблицы не найдены.
"""

import logging
import re
from glob import glob
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd


logger = logging.getLogger(__name__)


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


def parse_spec_text_blocks(lines: List[str]) -> List[dict]:
    """Разбирает текстовый слой ГОСТ-бланка спецификации на позиции.

    Fallback для PDF, где find_tables съезжается по колонкам (многострочные
    ячейки бланка «Позиция / Наименование / ... / Количество»). Текстовый слой
    такого бланка — последовательность блоков: наименование (1+ строк) ->
    размер -> бренд -> «Занести в перекупные»? -> единица -> количество.
    Строки бренда/единицы/штампа могут быть разбиты переносами
    («Климатве»/«нтмаш», «Изм № уч Лист №док Подпись Дата»).

    Возвращает список dict с ключами name, size, unit, quantity (float).
    """
    size_re = re.compile(r"^\d+(?:[xх]\d+)?$")
    qty_re = re.compile(r"^\d+(?:[.,]\d+)?$")
    unit_re = re.compile(r"^(м|м2|м²|шт|компл)\.?$", re.IGNORECASE)

    lines = _normalize_pogonny_metraj(lines)

    rows: List[dict] = []
    i, n = 0, len(lines)
    while i < n:
        token = lines[i].strip()
        if not token or _is_block_junk(token):
            i += 1
            continue
        # Наименование: всё до размера, единицы, поставщика или элемента штампа
        name_parts = []
        while i < n:
            token = lines[i].strip()
            nxt = lines[i + 1].strip() if i + 1 < n else ""
            if (not token or _is_block_junk(token) or unit_re.match(token)
                    or _is_block_vendor(token, nxt)
                    or size_re.match(token) or token.startswith("Занести в")):
                break
            name_parts.append(token)
            i += 1
        name = " ".join(name_parts).strip()
        # Отклеиваем заголовки разделов, склеившиеся с первой строкой группы
        changed = True
        while changed:
            changed = False
            for header in _BLOCK_SECTION_HEADERS:
                if name.startswith(header + " "):
                    name = name[len(header) + 1:].strip()
                    changed = True
        if not name:
            i += 1
            continue
        size = ""
        if i < n and size_re.match(lines[i].strip()):
            size = lines[i].strip()
            i += 1
        # Поставщик (1+ строк, в т.ч. разорванные переносом: «Климатве»/«нтмаш»)
        # и примечание «Занести в перекупные» — в любом порядке. Примечание НЕ
        # означает пропуск: техотдел включает такие позиции в КП как
        # производимые, пометку сохраняем в наименовании (уйдёт в комментарий).
        resell_note = ""
        while i < n:
            token = lines[i].strip()
            nxt = lines[i + 1].strip() if i + 1 < n else ""
            if _norm_block_token(f"{token} {nxt}") in _BLOCK_VENDOR_TOKENS:
                i += 2
                continue
            t = _norm_block_token(token)
            if t in _BLOCK_VENDOR_TOKENS or (
                    len(t) >= 4 and any(v.startswith(t) or v.endswith(t)
                                        for v in _BLOCK_VENDOR_TOKENS)):
                i += 1
                continue
            if token.startswith("Занести в"):
                resell_note = "Занести в перекупные"
                i += 1
                if i < n and lines[i].strip().rstrip(".") == "перекупные":
                    i += 1
                continue
            break
        unit = ""
        quantity = 0.0
        if i < n and unit_re.match(lines[i].strip()):
            unit = lines[i].strip().rstrip(".")
            i += 1
            if i < n and qty_re.match(lines[i].strip()):
                try:
                    quantity = float(lines[i].strip().replace(",", "."))
                except ValueError:
                    quantity = 0.0
                i += 1
        # Единица приклеена к концу наименования, а «размер» — на самом деле
        # количество: «…Ровен шт.» + «1» → unit=шт, quantity=1, size="".
        # «м» в конце наименования при этом НЕ единица измерения, если это
        # обрезанное при извлечении «мм» толщины («…толщиной 0.5 м»).
        if not unit and size and qty_re.match(size):
            m = re.search(r"(м|м2|м²|шт|компл)\.?\s*$", name)
            thickness_mm_truncated = (
                m and m.group(1).lower() == "м"
                and "толщин" in name.lower()
                and re.search(r"\d\s*м\.?\s*$", name)
            )
            if m and not thickness_mm_truncated:
                unit = m.group(1).rstrip(".")
                name = name[:m.start()].strip()
                try:
                    quantity = float(size.replace(",", "."))
                except ValueError:
                    quantity = 0.0
                size = ""
        if resell_note:
            name = f"{name} [{resell_note}]" if name else resell_note
        # Одинокий «п.»/«п.м.» с количеством — оторванная единица «п.м.»
        # предыдущей позиции (строка вида {name, size, unit="", qty=0}):
        # вливаем в предыдущую строку вместо отдельной позиции.
        if (name.replace(" ", "").lower().rstrip(".") in ("п", "пм", "мп")
                and unit == "м" and quantity > 0
                and rows and not rows[-1]["unit"] and not rows[-1]["quantity"]):
            rows[-1]["unit"] = "м"
            rows[-1]["quantity"] = quantity
            continue
        rows.append({"name": name, "size": size, "unit": unit, "quantity": quantity})
    return rows


_POGONNY_METRAJ_RE = re.compile(r"^(?:п\.\s*м|м\.\s*п)\.?$", re.IGNORECASE)


def _normalize_pogonny_metraj(lines: List[str]) -> List[str]:
    """Канонизирует «п.м.»/«м.п.» текстового слоя в единицу «м».

    Текстовый слой ГОСТ-ведомостей даёт единицу то склеенной («п.м.»), то
    разорванной на два токена («п.» + «м.»). Обе формы приводим к «м» до
    основного цикла разбора, чтобы unit_re увидел единицу.
    """
    result: List[str] = []
    for line in lines:
        token = line.strip()
        if _POGONNY_METRAJ_RE.match(token):
            result.append("м")
            continue
        if (token.rstrip(".").lower() == "м" and result
                and result[-1].strip().rstrip(".").lower() == "п"):
            # разорванная пара «п.» + «м.» → «м»
            result[-1] = "м"
            continue
        result.append(token)
    return result


def _norm_block_token(token: str) -> str:
    return " ".join(token.lower().replace("ё", "е").split())


# Заводы-поставщики из колонки «Завод-изготовитель». Встречаются целиком,
# разбитые переносом («Климатве»/«нтмаш») и в двух словах («Polar Bear»).
_BLOCK_VENDOR_TOKENS = {
    "россия", "ned", "арктос", "сезон", "korf", "imbat", "valtec",
    "русич", "вентинфо", "рм", "изовент", "rockwool", "пенофол",
    "lennox", "mdv", "ровен", "aluduct", "belimo",
    "климатвентмаш", "metu system", "polar bear",
}


def _is_block_vendor(token: str, nxt: str = "", pair_ok: bool = False) -> bool:
    """Определяет, является ли строка (возможно, с соседней) брендом-поставщиком."""
    t = _norm_block_token(token)
    if t in _BLOCK_VENDOR_TOKENS:
        return True
    if pair_ok and _norm_block_token(f"{token} {nxt}") in _BLOCK_VENDOR_TOKENS:
        return True
    # Разорванный переносом бренд: «Климатве» (префикс) / «нтмаш» (суффикс)
    if len(t) >= 4 and any(v.startswith(t) or v.endswith(t) for v in _BLOCK_VENDOR_TOKENS):
        return True
    return False


def _is_block_junk(token: str) -> bool:
    """Строки штампа, колонтитулов и служебные — не части позиций."""
    t = _norm_block_token(token).rstrip(".")
    if t in _BLOCK_BOM_TOKENS or _is_stamp_line(token):
        return True
    # номера позиций / страниц / служебные числа («194», «01.2», «6»)
    if re.fullmatch(r"\d+(?:[.,]\d+)?", t):
        return True
    if t.startswith(("шифр объекта", "шифр проекта")):
        return True
    if t in ("стадия", "листов", "гип", "инженер", "березин", "кузьмичева", "р"):
        return True
    return False


def _is_stamp_line(token: str) -> bool:
    """Фрагменты основного штампа ГОСТ 21.602 (в т.ч. на одной строке)."""
    t = _norm_block_token(token)
    if t in ("изм", "№уч", "№ уч", "дата", "подпись", "взам инв №",
             "подп и дата", "инв № подп", "лист №док подпись"):
        return True
    if t.startswith(("изм №", "взам.", "подп.", "инв.", "лист №док")):
        return True
    if "подпись" in t and "дата" in t:
        return True
    if "взам" in t and "инв" in t:
        return True
    if "инв" in t and "подп" in t:
        return True
    return False


# Элементы штампа/шапки бланка ГОСТ 21.602 — не позиции спецификации
_BLOCK_BOM_TOKENS = {
    "лист", "изм", "кол уч", "№док", "подпись", "дата",
    "наименование и техническая характеристика",
    "тип, марка, обозначение документа, опросного",
    "код оборудования, изделия, материала",
    "завод - изготовитель", "завод-изготовитель", "единица", "измерения",
    "количество", "масса единицы, кг", "примечание", "оборудование,",
    "изделия,", "материала", "измерен ия", "количест во", "масса",
    "единицы,", "кг", "примечание",
}

# Заголовки разделов, которые склеиваются с первой строкой группы
_BLOCK_SECTION_HEADERS = (
    "ПРОТИВОДЫМНАЯ ЗАЩИТА",
    "Воздуховоды и фасонные изделия из оцинкованной стали",
    "Воздуховоды и фасонные изделия",
    "Воздухораспределительные устройства",
    "ВЕНТИЛЯЦИЯ",
    "Оборудование",
    "КИПиА",
)


def main():
    """Простой тест: ищет PDF в рабочей директории и выводит статистику по таблицам."""
    pdf_files = glob("*.pdf")
    if not pdf_files:
        logger.warning("PDF-файлы в рабочей директории не найдены. Тест пропущен.")
        return

    pdf_path = pdf_files[0]
    logger.info("Тестовый файл: %s", pdf_path)

    try:
        tables_by_page = extract_tables_from_pdf(pdf_path)
        total_tables = sum(len(tables) for tables in tables_by_page.values())
        total_rows = 0

        for page_num, tables in tables_by_page.items():
            logger.info("Страница %s: найдено таблиц — %d", page_num, len(tables))
            for i, df in enumerate(tables, start=1):
                rows = df_to_spec_rows(df)
                total_rows += len(rows)
                logger.info("  Таблица %d: %d строк, %d распознано", i, len(df), len(rows))

        logger.info("Всего таблиц: %d, распознано строк: %d", total_tables, total_rows)

    except Exception as exc:  # pragma: no cover
        logger.exception("Ошибка при извлечении таблиц: %s", exc)


if __name__ == "__main__":
    main()
