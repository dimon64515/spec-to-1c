#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pdf_spec_extractor.py

Модуль извлечения таблиц спецификации из PDF.

Использует PyMuPDF (fitz) для поиска таблиц и pandas для их представления.
Предоставляет fallback на построчный текст, если таблицы не найдены.
"""

import json
import logging
import re
import shutil
import subprocess
import tempfile
from glob import glob
from pathlib import Path
from typing import Dict, List, Optional, Tuple

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


# --- OCR-фолбэк: ведомости, переведённые в векторные кривые ---

# PDF, где текст преобразован в кривые (нет текстового слоя и растровых
# картинок), не отдаёт ни таблицы, ни текст. Единственный способ извлечь
# позиции — отрендерить страницу и прогнать tesseract. Шрифт ГОСТ-бланков
# (курсив) tesseract rus распознаёт с типовыми путаницами в размерах:
# «х» → «0»/«М»/«%», «ø» → «#»/«9», «s=» → «5=»/«$=». Структура строк при
# этом (psm 4) сохраняется, поэтому размеры подтверждаются точечным
# повторным OCR области токена на 600 dpi с rus+eng.

OCR_TEXT_LAYER_MAX_CHARS = 100
OCR_GRID_MIN_VLINES = 6
OCR_GRID_MIN_HLINES = 4

_UNIT_TOKENS = {
    "м": "м", "m": "м", "ом": "м", "0м": "м", "гм": "м", "шм": "м",
    "шт": "шт", "штук": "шт", "шток": "шт", "ш": "шт",
    "компл": "компл",
    "м2": "м2", "м²": "м²", "м?": "м²", "м3": "м²",
}


def _tesseract_available() -> bool:
    return shutil.which("tesseract") is not None


def _render_page_png(page, png_path: str, dpi: int, clip=None) -> None:
    import fitz  # PyMuPDF

    matrix = fitz.Matrix(dpi / 72.0, dpi / 72.0)
    pix = page.get_pixmap(matrix=matrix, colorspace=fitz.csGRAY, clip=clip)
    pix.save(png_path)


def _run_tesseract(png_path: str, lang: str, psm: int, tsv: bool = False) -> str:
    cmd = ["tesseract", png_path, "stdout", "-l", lang, "--psm", str(psm)]
    if tsv:
        cmd.append("tsv")
    # OMP_THREAD_LIMIT=1: многопоточный tesseract недетерминирован
    # (результат зависит от загрузки потоков), что ломает воспроизводимость.
    env = dict(**__import__("os").environ, OMP_THREAD_LIMIT="1")
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300, env=env)
    if proc.returncode != 0:
        logger.warning("tesseract вернул код %s: %s",
                       proc.returncode, proc.stderr.strip()[:200])
    return proc.stdout


def ocr_page_text(page, dpi: int = 300, psm: int = 4, lang: str = "rus") -> str:
    """Распознаёт текст страницы через tesseract (рендер → временный PNG).

    Для страниц-ведомостей ГОСТ 21.602 оптимален psm 4 (однострочный
    вывод по строкам таблицы); psm 6 «схлопывает» колонки в мусор.
    """
    if not _tesseract_available():  # pragma: no cover
        raise RuntimeError("tesseract не установлен")
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        png_path = tmp.name
    try:
        _render_page_png(page, png_path, dpi)
        return _run_tesseract(png_path, lang, psm)
    finally:
        Path(png_path).unlink(missing_ok=True)


def _ocr_word_boxes(page, dpi: int = 300) -> List[dict]:
    """OCR страницы + координаты слов (в точках PDF)."""
    import csv
    import io

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        png_path = tmp.name
    try:
        _render_page_png(page, png_path, dpi)
        tsv = _run_tesseract(png_path, "rus", 4, tsv=True)
    finally:
        Path(png_path).unlink(missing_ok=True)

    scale = 72.0 / dpi
    words = []
    for row in csv.DictReader(io.StringIO(tsv), delimiter="\t"):
        if row.get("level") != "5" or not (row.get("text") or "").strip():
            continue
        words.append({
            "text": row["text"].strip(),
            "x0": float(row["left"]) * scale,
            "y0": float(row["top"]) * scale,
            "x1": (float(row["left"]) + float(row["width"])) * scale,
            "y1": (float(row["top"]) + float(row["height"])) * scale,
        })
    return words


def _table_grid(page) -> Tuple[List[float], List[float]]:
    """Вертикальные/горизонтальные линии сетки ведомости (координаты в pt)."""
    vlines, hlines = [], []
    for drawing in page.get_drawings():
        if drawing["type"] != "s":  # только обводка — заливки это штриховка
            continue
        for item in drawing["items"]:
            if item[0] != "l":
                continue
            p1, p2 = item[1], item[2]
            if abs(p1.x - p2.x) < 0.5 and abs(p1.y - p2.y) > page.rect.height * 0.45:
                vlines.append(p1.x)
            elif abs(p1.y - p2.y) < 0.5 and abs(p1.x - p2.x) > page.rect.width * 0.5:
                hlines.append(p1.y)
    return sorted(set(round(x, 1) for x in vlines)), sorted(set(round(y, 1) for y in hlines))


def _column_intervals(lines: List[float], min_width: float = 8.0) -> List[Tuple[float, float]]:
    return [(a, b) for a, b in zip(lines, lines[1:]) if b - a >= min_width]


# Токен, похожий на размер: чистый прямоугольный «100x100мм» (доверяем сразу),
# круглый «Ф160мм»/«ø160мм» и искажённые варианты («100М00мм», «200150мм»,
# «#160мм», «9100мм») — их дочитываем точечным OCR.
_SIZE_TOKEN_RE = re.compile(
    r"^(?:[#ФфØø⌀]?\d{2,5}(?:[xXх×*Мм%/]?\d{2,5})?)\s*мм\.?$"
)
_CLEAN_RECT_RE = re.compile(r"^\d{2,5}\s*[xXх×*]\s*\d{2,5}\s*мм\.?$")
_THICK_TOKEN_RE = re.compile(r"^[$5ЅsS]\s*=\s*(\d{1,2}(?:[.,]\d{1,3})?)\s*мм\.?$")
_TO_ZHE_RE = re.compile(r"^\W*[ТГTтгt][оаеo]?\s*же\b[,:]?\s*", re.IGNORECASE)
_STAMP_WORDS = {
    "изм", "кол", "уч", "лист", "№", "док", "подпись", "дата", "подп",
    "инв", "взам", "листов", "разраб", "пров", "тк", "лит", "листа",
    "подписьидата", "n", "лист№докподписьдата", "nдок", "9/2021", "лист№",
}


def _norm_unit_token(word: str) -> Optional[str]:
    t = word.strip().strip(".,;:|»”\"'").lower()
    t = t.replace("0м", "ом")
    return _UNIT_TOKENS.get(t)


def _qty_from_word(word: str) -> Optional[float]:
    m = re.match(r"(\d+(?:[.,]\d+)?)", word.strip().lstrip("*"))
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", "."))
    except ValueError:
        return None


def _split_name_size(text: str) -> Tuple[str, str]:
    """Отделяет последний размерный токен («100x100мм», «Ф160мм») от наименования.

    Возвращает (наименование без размера, размерный токен или "").
    """
    # Токен и «мм» могут разойтись в слова («10000 мм»): склеиваем обратно.
    text = re.sub(
        r"\s((?:[#ФфØø⌀D9]?\d{2,5}(?:[xXх×*%Кк]?\d{2,5})?)) (мм)\b",
        r" \1\2", text)
    tokens = text.split()
    for k in range(len(tokens) - 1, -1, -1):
        if _SIZE_TOKEN_RE.match(tokens[k]):
            name = " ".join(tokens[:k] + tokens[k + 1:]).strip(" ,;:")
            return name, tokens[k]
    return text.strip(), ""


def _reocr_region_text(page, bbox: Tuple[float, float, float, float],
                       pad: float = 3.0, lang: str = "rus+eng", psm: int = 7,
                       dpi: int = 600) -> str:
    """Точечный повторный OCR области страницы на повышенном разрешении.

    Курсив ГОСТ-шрифта на страничном OCR (rus, 300 dpi) систематически
    путает «х»/«ø»/«s»; rus+eng/eng на клипе, увеличенном в 1.5 раза,
    читают их корректно (тонкие засечки курсива критичны)."""
    import fitz  # PyMuPDF

    clip = fitz.Rect(bbox[0] - pad, bbox[1] - pad, bbox[2] + pad, bbox[3] + pad)
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        png_path = tmp.name
    try:
        _render_page_png(page, png_path, dpi, clip=clip)
        try:
            from PIL import Image

            with Image.open(png_path) as im:
                im = im.resize((int(im.width * 1.5), int(im.height * 1.5)),
                               Image.LANCZOS)
                im.save(png_path)
        except ImportError:  # pragma: no cover
            pass
        return _run_tesseract(png_path, lang, psm)
    finally:
        Path(png_path).unlink(missing_ok=True)


_SIZE_SEPARATORS = "xXхХ%Kk"  # «x» и типовые его OCR-замены в курсиве ГОСТ


def _size_candidate_score(size: str) -> int:
    """Правдоподобие размера: стандартные сечения кратны 50 мм."""
    if "x" in size:
        a, b = (int(v) for v in size.split("x"))
        return ((a % 50 == 0) * 2 + (b % 50 == 0) * 2
                + (50 <= a <= 1600) + (50 <= b <= 1600) + (a >= b))
    d = int(size.lstrip("Ф"))
    return (d % 50 == 0) + (50 <= d <= 630) * 2 + (d % 10 == 0)


def _extract_size_candidates(text: str) -> Dict[str, int]:
    """Все размерные кандидаты из текста OCR с оценкой правдоподобия."""
    t = re.sub(r"(?i)mm", "мм", text)
    t = re.sub(r"(?<=\d)\s*m(?=\s|$|[,.|])", "мм", t)  # усечённое «mm» → «m»
    t = t.replace("*", "x").replace("×", "x")
    cands: Dict[str, int] = {}
    sep = re.escape(_SIZE_SEPARATORS)
    # Прямоугольное сечение с явным разделителем: «100x100мм», «400K300мм»
    for a, b in re.findall(rf"(\d{{2,5}})\s*[{sep}]\s*(\d{{2,5}})\s*мм", t):
        ia, ib = int(a), int(b)
        if 20 <= ia <= 2000 and 20 <= ib <= 2000:
            cand = f"{ia}x{ib}"
            cands[cand] = max(cands.get(cand, 0), _size_candidate_score(cand))
    # Круглое сечение: «Ф160мм», «#160мм», «Ø250мм» (ø → «0»/«#»/«D»),
    # либо диаметр без префикса из 3+ цифр
    for prefix, digits in re.findall(r"(?:^|[\s,;(])([#ФфØø⌀D]|0)?\s*(\d{2,5})\s*мм", t):
        if digits.startswith("0") and len(digits) > 3:
            continue
        d = int(digits.lstrip("0") or "0")
        if (prefix or len(digits) >= 3) and 50 <= d <= 2000:
            cand = "Ф" + str(d)
            cands[cand] = max(cands.get(cand, 0), _size_candidate_score(cand))
    # Разделитель «x» потерян целиком: «900500мм» → «900x500»
    for digits in re.findall(r"(\d{5,7})\s*мм", t):
        merged = _merged_digits_size(digits)
        if merged:
            cands[merged] = max(cands.get(merged, 0), _size_candidate_score(merged))
    return cands


def _reocr_size_token(page, bbox: Tuple[float, float, float, float],
                      token: str = "") -> str:
    """Размер из повторного OCR строки ячейки наименования.

    Курсивный «х»/«ø» ГОСТ-шрифта каждый движок читает с разными ошибками,
    поэтому гоняем набор вариантов (eng/rus+eng × psm 6/7/8/13) и голосуем
    по правдоподобию: стандартные сечения кратны 50 мм, ширина ≥ высоты.
    Кандидаты, чьи стороны есть в цифрах исходного (первого прохода)
    токена, получают бонус — он сдвигает равные оценки к согласованным
    прочтениям («190x150» против «150x150» у токена «120150мм»)."""
    totals: Dict[str, int] = {}
    votes: Dict[str, int] = {}
    # eng для этого курсива надёжнее rus+eng (rus путает «х»/«ø» с «0»/«М»),
    # поэтому голоса eng взвешены выше — они перевешивают согласованные,
    # но ошибочные прочтения rus+eng.
    variants = (("eng", 8), ("eng", 7), ("eng", 13), ("eng", 6),
                ("rus+eng", 7), ("rus+eng", 6), ("rus+eng", 13))
    for lang, psm in variants:
        weight = 10 if lang == "eng" else 7
        text = _reocr_region_text(page, bbox, lang=lang, psm=psm)
        for cand, score in _extract_size_candidates(text).items():
            totals[cand] = totals.get(cand, 0) + score * weight
            votes[cand] = votes.get(cand, 0) + 1
    if not totals:
        return ""
    # Согласованность с цифрами исходного (первого прохода) токена — главный
    # сигнал: ошибочные прочтения редко сохраняют состав цифр («200150мм» →
    # «200x50» теряет цифру, «190x150» подставляет чужую). Веса перекрывают
    # сумму оценок голосования, поэтому перевешивают случайные сбои отдельных
    # вариантов движка.
    token_digits = re.sub(r"\D", "", token)
    if len(token_digits) >= 3:
        # «Чистый» прямоугольный токен первого прохода («900х200мм») сам по
        # себе подозрителен — ради таких случаев всё и дочитывается, — поэтому
        # согласие с его цифрами взвешено слабо. Склеенные/округлые токены
        # («120150мм», «#100мм») наоборот — сильный априор.
        clean_rect = re.match(r"^\d{2,5}\s*[xXх×*]\s*\d{2,5}\s*мм\.?$", token)
        w_all, w_count, w_miss = (10, 20, 30) if clean_rect else (40, 80, 60)
        for cand in totals:
            parts = [p for p in re.split(r"[xX]", cand.lstrip("Ф")) if len(p) >= 3]
            if not parts:
                continue
            hits = sum(1 for p in parts if p in token_digits)
            if hits == len(parts):
                totals[cand] += w_all
            else:
                totals[cand] -= w_miss * (len(parts) - hits)
            if sum(len(p) for p in parts) == len(token_digits):
                totals[cand] += w_count
    return max(totals, key=lambda c: (totals[c], votes[c]))


def _merged_digits_size(token: str) -> str:
    """Эвристика склеенных цифр без разделителя: «1200600мм» → «1200x600»."""
    digits = re.sub(r"\D", "", token)
    if not (5 <= len(digits) <= 7):
        return ""
    best, best_score = "", -1
    for cut in range(2, len(digits) - 1):
        a, b = digits[:cut], digits[cut:]
        if not (2 <= len(a) <= 4 and 2 <= len(b) <= 4):
            continue
        ia, ib = int(a), int(b)
        if not (50 <= ia <= 2000 and 50 <= ib <= 2000):
            continue
        score = (ia % 50 == 0) + (ib % 50 == 0) + (ia >= ib)
        if score > best_score:
            best, best_score = f"{ia}x{ib}", score
    return best


def _merge_size_tail(name: str, size: str, token: str, page, bbox) -> Tuple[str, str]:
    """Приводит размерный токен к чистому виду, при необходимости дочитывая
    его точечным OCR. Возвращает (наименование, чистый размер)."""
    if not token:
        return name, ""
    # Pass-1 нельзя доверять даже «чистым» прямоугольникам: «900х200мм» при
    # фактическом «900x500» форматно чист, но неверен — дочитываем всегда.
    reocr = _reocr_size_token(page, bbox, token)
    if reocr:
        return name, reocr
    m = re.match(r"(\d{2,5})\s*([xXх×*])\s*(\d{2,5})\s*мм\.?$", token)
    if m:
        return name, f"{int(m.group(1))}x{int(m.group(3))}"
    # Дочитать не вышло — эвристики по исходному токену: круглый «#160мм»
    # (ø → «#»/«9»/«D») и склеенные цифры («1200600мм»)
    bare = re.match(r"^[#ФфØø⌀D9]\s*(\d{2,5})\s*мм\.?$", token)
    if bare and 100 <= int(bare.group(1)) <= 1250:
        return name, "Ф" + bare.group(1)
    merged = _merged_digits_size(token)
    if merged:
        return name, merged
    return name, size or ""


class _OcrRow:
    __slots__ = ("name", "size", "unit", "quantity")

    def __init__(self, name: str, size: str, unit: str, quantity: float):
        self.name = name
        self.size = size
        self.unit = unit
        self.quantity = quantity

    def as_dict(self) -> dict:
        return {"name": self.name, "size": self.size,
                "unit": self.unit, "quantity": self.quantity}


def _is_stamp_or_header_cell(text: str) -> bool:
    t = " ".join(text.lower().replace(".", " ").split())
    if not t:
        return True
    if "наименование и техническ" in t or t.startswith("позиция"):
        return True
    words = [w for w in re.split(r"[\s|]+", t) if w]
    return bool(words) and all(
        w in _STAMP_WORDS or re.fullmatch(r"\d+", w) for w in words
    )


def _ocr_spec_rows_from_page(page, dpi: int = 300) -> List[dict]:
    """Разбирает страницу-ведомость без текстового слоя через OCR.

    Строки и колонки восстанавливаются по векторной сетке бланка,
    наименование/единица/количество берутся из соответствующих колонок,
    искажённые размеры дочитываются точечным OCR. «То же, …» разворачивается
    в наименование предыдущей позиции. Возвращает строки формата
    parse_spec_text_blocks ({name, size, unit, quantity}).
    """
    vlines, hlines = _table_grid(page)
    if len(vlines) < OCR_GRID_MIN_VLINES or len(hlines) < OCR_GRID_MIN_HLINES:
        return []

    words = _ocr_word_boxes(page, dpi=dpi)
    if not words:
        return []

    col_intervals = _column_intervals(vlines)
    name_col = max(col_intervals, key=lambda iv: iv[1] - iv[0])
    right_intervals = [iv for iv in col_intervals if iv[0] >= name_col[1] - 1]

    def col_of(word) -> Optional[Tuple[float, float]]:
        cx = (word["x0"] + word["x1"]) / 2
        for iv in right_intervals:
            if iv[0] <= cx <= iv[1]:
                return iv
        return None

    # Голосование за колонки единиц и количества по содержимому
    unit_scores: Dict[Tuple[float, float], int] = {iv: 0 for iv in right_intervals}
    qty_scores: Dict[Tuple[float, float], int] = {iv: 0 for iv in right_intervals}
    for w in words:
        iv = col_of(w)
        if iv is None:
            continue
        if _norm_unit_token(w["text"]):
            unit_scores[iv] += 1
        if re.match(r"^\d[\d.,/+*]*$", w["text"].strip().rstrip(".")):
            qty_scores[iv] += 1
    unit_col = max(right_intervals, key=lambda iv: unit_scores[iv]) \
        if unit_scores and max(unit_scores.values()) >= 2 else None
    qty_candidates = [iv for iv in right_intervals if unit_col is None or iv[0] > unit_col[0]]
    qty_col = max(qty_candidates, key=lambda iv: qty_scores[iv]) \
        if qty_candidates and max(qty_scores[iv] for iv in qty_candidates) >= 2 else None

    row_bounds = [(a, b) for a, b in zip(hlines, hlines[1:]) if b - a > 4]

    def words_in(xiv, yiv) -> List[dict]:
        cell = [
            w for w in words
            if xiv[0] <= (w["x0"] + w["x1"]) / 2 <= xiv[1]
            and yiv[0] < (w["y0"] + w["y1"]) / 2 < yiv[1]
        ]
        # Курсив ГОСТ-шрифта «пляшет» по baseline (до ±3 pt), поэтому сортировка
        # по (y0, x0) перемешивает слова внутри строки — группируем визуальные
        # строки по y-центру и только внутри них сортируем по x.
        cell.sort(key=lambda w: (w["y0"] + w["y1"]) / 2)
        lines: List[List[dict]] = []
        for w in cell:
            cy = (w["y0"] + w["y1"]) / 2
            if lines and abs(sum((q["y0"] + q["y1"]) / 2 for q in lines[-1]) / len(lines[-1]) - cy) < 8:
                lines[-1].append(w)
            else:
                lines.append([w])
        ordered: List[dict] = []
        for line in lines:
            ordered.extend(sorted(line, key=lambda w: w["x0"]))
        return ordered

    rows: List[_OcrRow] = []
    last_base: dict = {"name": "", "thick": ""}

    def _find_token_bbox(row_words: List[dict], token: str):
        """Bbox слов, покрывающих токен в склейке текстов слов."""
        joined = ""
        spans = []
        for w in row_words:
            start = len(joined)
            joined = f"{joined} {w['text']}" if joined else w["text"]
            spans.append((start, len(joined), w))
        pos = joined.find(token)
        if pos < 0:
            pos = len(joined) - len(token)
        hit = [w for a, b, w in spans if b > pos and a < pos + len(token)]
        if not hit:
            return None
        return (min(w["x0"] for w in hit), min(w["y0"] for w in hit),
                max(w["x1"] for w in hit), max(w["y1"] for w in hit))

    def _resolve_size(row_name: str, row_words, size_token: str, yiv):
        # Точечный OCR всей строки ячейки (контекст слов исправляет
        # одиночные токены: «#2250мм» читается как «#250мм»)
        if row_words:
            bbox = (min(w["x0"] for w in row_words), min(w["y0"] for w in row_words),
                    max(w["x1"] for w in row_words), max(w["y1"] for w in row_words))
        else:
            bbox = (name_col[0], yiv[0], name_col[1], yiv[1])
        return _merge_size_tail(row_name, "", size_token, page, bbox)

    def amend_previous(text: str, frag_words) -> None:
        """Многострочная ячейка: текст относится к позиции выше."""
        if not rows or not text:
            return
        combined = f"{rows[-1].name} {text}".strip()
        name, token = _split_name_size(combined)
        if token and not rows[-1].size:
            if _CLEAN_RECT_RE.match(token):
                m = re.match(r"(\d{2,5}\s*[xXх×*]\s*\d{2,5})", token)
                rows[-1].size = re.sub(r"\s*[xXх×*]\s*", "x", m.group(1))
                rows[-1].name = name
                return
            bbox = _find_token_bbox(frag_words, token)
            if bbox:
                _n, size = _merge_size_tail(name, "", token, page, bbox)
                if size:
                    rows[-1].size = size
                    rows[-1].name = _n
                    return
        rows[-1].name = combined

    for yiv in row_bounds:
        name_words = words_in(name_col, yiv)
        name_text = " ".join(w["text"] for w in name_words).strip()
        name_text = re.sub(r"^[‚„“”'\-\.\|»\s]+", "", name_text)

        unit = ""
        quantity: Optional[float] = None
        if unit_col:
            unit_words = [w for w in words_in(unit_col, yiv) if _norm_unit_token(w["text"])]
            if unit_words:
                unit = _norm_unit_token(unit_words[-1]["text"])
        if qty_col:
            cell_words = words_in(qty_col, yiv)
            qty_words = [w for w in cell_words if _qty_from_word(w["text"]) is not None]
            if qty_words:
                quantity = _qty_from_word(qty_words[-1]["text"])
            elif cell_words and name_text:
                # Ячейка количества прочиталась с мусором («э» вместо «5»):
                # дочитываем клип колонки; psm 7/6 пусты на разреженной
                # ячейке — пробуем и односимвольный psm 10.
                bbox = (qty_col[0], yiv[0], qty_col[1], yiv[1])
                for psm in (7, 10):
                    text = _reocr_region_text(page, bbox, pad=2.0, lang="eng", psm=psm)
                    m = re.search(r"(\d+(?:[.,]\d+)?)", text.strip().lstrip("*"))
                    if m:
                        quantity = float(m.group(1).replace(",", "."))
                        break

        if quantity is None:
            right_words = words_in((name_col[1], page.rect.width), yiv)
            if any(re.search(r"\d", w["text"]) for w in right_words):
                # Количество справа от наименования есть, единица не ясна:
                # «м» у воздуховодов/труб, иначе штучная позиция.
                side = " ".join(w["text"] for w in right_words)
                m = None
                for m in re.finditer(r"(\d+(?:[.,]\d+)?)", side):
                    pass  # последнее число справа — количество
                if m:
                    quantity = float(m.group(1).replace(",", "."))
                    unit = unit or ("м" if re.search(
                        r"воздуховод|трубопровод|кабель", name_text.lower()) else "шт")

        if quantity is None or not name_text or _is_stamp_or_header_cell(name_text):
            # Фрагмент многострочной ячейки, штамп или строка без количества
            if name_text and not _is_stamp_or_header_cell(name_text):
                if rows:
                    amend_previous(name_text, name_words)
                elif last_base["name"]:
                    last_base["name"] = f"{last_base['name']} {name_text}".strip()
            continue

        # «То же, …» — продолжение предыдущей позиции
        m_zhe = _TO_ZHE_RE.match(name_text)
        if m_zhe and last_base["name"]:
            rest = name_text[m_zhe.end():].strip()
            t_m = re.search(r"[$5ЅsS]\s*=\s*(\d{1,2}(?:[.,]\d{1,3})?)\s*мм", rest)
            thick = ""
            if t_m:
                thick = f"s={t_m.group(1).replace(',', '.')}мм"
                rest = (rest[:t_m.start()] + rest[t_m.end():]).strip()
            name = last_base["name"]
            if thick:
                name = re.sub(r"[$5ЅsS]\s*=\s*\d{1,2}(?:[.,]\d{1,3})?\s*мм", thick, name)
            _rest_name, size_token = _split_name_size(rest) if rest else ("", "")
            name = " ".join(name.split())
            size = ""
            if size_token:
                name, size = _resolve_size(name, name_words, size_token, yiv)
        else:
            name, size_token = _split_name_size(name_text)
            # явная толщина в наименовании («s=0.5мм», «5=0.6мм»)
            t_m = re.search(r"[$5ЅsS]\s*=\s*(\d{1,2}(?:[.,]\d{1,3})?)\s*мм", name)
            if t_m:
                thick = f"s={t_m.group(1).replace(',', '.')}мм"
                name = (name[:t_m.start()] + thick + name[t_m.end():]).strip()
            size = ""
            if size_token:
                name, size = _resolve_size(name, name_words, size_token, yiv)
            # контекст для следующих «То же, …»
            n_part, _t = _split_name_size(name_text)
            t_m = re.search(r"[$5ЅsS]\s*=\s*(\d{1,2}(?:[.,]\d{1,3})?)\s*мм", n_part)
            thick_save = f"s={t_m.group(1).replace(',', '.')}мм" if t_m else ""
            last_base = {
                "name": (n_part[:t_m.start()] + thick_save + n_part[t_m.end():]).strip()
                         if thick_save else n_part,
                "thick": thick_save,
            }

        if not unit:
            unit = "м" if re.search(r"воздуховод|трубопровод", name.lower()) else "шт"

        rows.append(_OcrRow(" ".join(name.split()), size, unit, quantity))

    return [r.as_dict() for r in rows]


def extract_ocr_spec_rows(
    pdf_path: str,
    pages: Optional[List[int]] = None,
    cache_path: Optional[str] = None,
    dpi: int = 300,
) -> Dict[int, List[dict]]:
    """OCR-фолбэк для страниц без текстового слоя, но с сеткой ведомости.

    Возвращает {номер_страницы: [row, ...]} в формате parse_spec_text_blocks.
    Результаты кэшируются в cache_path (JSON), чтобы повторные прогоны
    проектов не перезапускали tesseract.
    """
    result: Dict[int, List[dict]] = {}
    if not _tesseract_available():  # pragma: no cover
        logger.warning("tesseract не установлен — OCR-фолбэк пропущен")
        return result

    wanted = None
    if cache_path and Path(cache_path).exists():
        try:
            cached = json.loads(Path(cache_path).read_text(encoding="utf-8"))
            result = {int(k): v for k, v in cached.get("pages", {}).items()}
        except (ValueError, KeyError, OSError):
            result = {}
        if pages is not None:
            wanted = [p for p in pages if p not in result]
            if not wanted:
                return {p: result[p] for p in pages if p in result}
        else:
            return result

    import fitz  # PyMuPDF

    try:
        doc = fitz.open(pdf_path)
    except Exception as exc:  # повреждённый/не-PDF файл — не роняем вызов
        logger.warning("extract_ocr_spec_rows: не удалось открыть %s: %s",
                       pdf_path, exc)
        return result
    with doc:
        page_indices = (
            [p - 1 for p in (wanted or pages) if 1 <= p <= doc.page_count]
            if (wanted or pages) is not None
            else range(doc.page_count)
        )
        for idx in page_indices:
            page = doc[idx]
            text = page.get_text().strip()
            if len(text) > OCR_TEXT_LAYER_MAX_CHARS:
                continue  # текстовый слой есть — OCR не нужен
            try:
                rows = _ocr_spec_rows_from_page(page, dpi=dpi)
            except Exception as exc:  # pragma: no cover
                logger.warning("OCR страницы %s не удался: %s", idx + 1, exc)
                continue
            if rows:
                result[idx + 1] = rows

    if cache_path:
        try:
            Path(cache_path).parent.mkdir(parents=True, exist_ok=True)
            Path(cache_path).write_text(
                json.dumps({"pages": {str(k): v for k, v in result.items()}},
                           ensure_ascii=False, indent=1),
                encoding="utf-8")
        except OSError as exc:  # pragma: no cover
            logger.warning("Не удалось сохранить OCR-кэш %s: %s", cache_path, exc)

    return result if pages is None else {p: result[p] for p in pages if p in result}


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
        unit = _fix_piece_unit(name, unit)
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


# --- Позиционный разбор «Спецификации изделий и материалов» в чертеже ---

_DRAWING_SPEC_TITLE = "спецификация изделий и материалов"


def _norm_span_text(text: str) -> str:
    return " ".join(str(text).lower().replace("ё", "е").split()).rstrip(".")


def extract_drawing_spec_rows(
    pdf_path: str,
    pages: Optional[List[int]] = None,
) -> Dict[int, List[dict]]:
    """Позиционно разбирает таблицу «Спецификация изделий и материалов»,
    встроенную в чертёж аспирационной/вентиляционной системы.

    Такая таблица — не ГОСТ-бланк: колонки «Поз. / обозначение / Наименование /
    Кол. / Примечание» размазаны по текстовому слою вперемешку с выносками
    самой схемы (расходы «L2160м3/ч», «∅280», люки, градусы), поэтому
    построчный parse_spec_text_blocks склеивает всё в одну строку. Здесь строки
    восстанавливаются по координатам спанов: якоря колонок берутся из шапки
    («Поз.», «Наименование», «Кол.»), затем каждому номеру позиции в колонке
    «Поз.» подбираются наименование и количество по близости координат y.

    Все количества такой таблицы — штучные (фасонка и прямики идут поштучно,
    «м» в текстовом слое — обрезанное «мм»/«шт»).

    Returns:
        Словарь {номер_страницы: [row, ...]}; пустой, если таблица не найдена.
    """
    try:
        import fitz  # PyMuPDF
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "PyMuPDF (fitz) не установлен. Установите: pip install pymupdf"
        ) from exc

    result: Dict[int, List[dict]] = {}
    try:
        doc = fitz.open(pdf_path)
    except Exception as exc:  # повреждённый/не-PDF файл — не роняем fallback
        logger.warning("extract_drawing_spec_rows: не удалось открыть %s: %s",
                       pdf_path, exc)
        return result
    with doc:
        page_indices = (
            [p - 1 for p in pages if 1 <= p <= doc.page_count]
            if pages is not None
            else range(doc.page_count)
        )
        for idx in page_indices:
            try:
                rows = _extract_drawing_spec_rows_from_page(doc[idx])
            except Exception as exc:  # pragma: no cover
                logger.warning("extract_drawing_spec_rows: страница %s: %s",
                               idx + 1, exc)
                continue
            if rows:
                result[idx + 1] = rows
    return result


def _extract_drawing_spec_rows_from_page(page) -> List[dict]:
    """Разбор одной страницы; [] если встроенной спецификации нет."""
    spans = []
    for block in page.get_text("dict")["blocks"]:
        if block.get("type") != 0:
            continue
        for line in block["lines"]:
            for span in line["spans"]:
                text = str(span["text"]).strip()
                if text:
                    spans.append((fitz_rect(span["bbox"]), text))
    if not spans:
        return []

    def norm(t: str) -> str:
        return _norm_span_text(t)

    # Заголовок таблицы
    title = next(
        ((r, t) for r, t in spans if _DRAWING_SPEC_TITLE in norm(t)),
        None,
    )
    if title is None:
        return []
    title_y = title[0].y1

    # Шапка колонок — в окне ниже заголовка (штамп «Кол . уч.» дальше игнорим)
    header_zone = [s for s in spans if title_y <= s[0].y0 <= title_y + 120]
    poz_h = next((s for s in header_zone if norm(s[1]) in ("поз", "позиция")), None)
    name_h = next((s for s in header_zone if "наименование" in norm(s[1])), None)
    qty_h = next(
        (s for s in header_zone
         if norm(s[1]).startswith("кол") and s[0].y0 < title_y + 120),
        None,
    )
    if poz_h is None or name_h is None or qty_h is None:
        return []
    remark_h = next(
        (s for s in header_zone if norm(s[1]).startswith("примечан")), None)
    poz_x, name_x, qty_x = poz_h[0].x0, name_h[0].x0, qty_h[0].x0
    if not (poz_x < name_x < qty_x):
        return []

    header_bottom = max(poz_h[0].y1, name_h[0].y1, qty_h[0].y1)
    right_x = max(qty_x + 260, remark_h[0].x1 + 60) if remark_h else qty_x + 260

    # Данные: спаны правее колонки «Поз.» и ниже шапки
    data = [
        s for s in spans
        if s[0].y0 >= header_bottom - 2 and s[0].x0 >= poz_x - 20
        and s[0].x0 < right_x
    ]
    # Левая граница колонки наименований — по реальным спанам с буквами
    lettered = [
        s[0].x0 for s in data
        if poz_x + 5 <= s[0].x0 < qty_x - 20 and re.search(r"[A-Za-zА-Яа-я]", s[1])
    ]
    if not lettered:
        return []
    name_x0 = min(lettered) - 8

    # Номера позиций — целые числа в колонке «Поз.»
    poz_spans = [
        s for s in data
        if re.fullmatch(r"\d+", s[1]) and s[0].x0 < name_x0
    ]
    if not poz_spans:
        return []
    poz_spans.sort(key=lambda s: (s[0].y0, s[0].x0))
    ys = sorted({round(s[0].y0) for s in poz_spans})
    diffs = [b - a for a, b in zip(ys, ys[1:]) if 0 < b - a < 60]
    pitch = sorted(diffs)[len(diffs) // 2] if diffs else 20
    tol = max(8.0, pitch * 0.45)

    rows: List[dict] = []
    for rect, pos_text in poz_spans:
        ymid = (rect.y0 + rect.y1) / 2
        near = [
            s for s in data
            if s[0] is not rect and abs((s[0].y0 + s[0].y1) / 2 - ymid) < tol
        ]
        name_spans = sorted(
            (s for s in near if name_x0 <= s[0].x0 < qty_x - 8),
            key=lambda s: (s[0].y0, s[0].x0),
        )
        name = " ".join(t for _, t in name_spans).strip()
        if not name:
            continue
        qty = 0.0
        remark_parts = []
        for s in sorted(
            (s for s in near if s[0].x0 >= qty_x - 12),
            key=lambda s: s[0].x0,
        ):
            if qty == 0.0 and re.fullmatch(r"\d+(?:[.,]\d+)?", s[1]):
                qty = _to_float(s[1], 0.0)
            else:
                remark_parts.append(s[1])
        if remark_parts:
            name = f"{name} [{' '.join(remark_parts)}]"
        rows.append({
            "name": name,
            "size": "",
            "unit": "шт",
            "quantity": qty,
            "material": DEFAULT_MATERIAL,
            "thickness": DEFAULT_THICKNESS,
        })
    return rows


def fitz_rect(bbox) -> "object":
    """Минимальный accessor к bbox спана (x0, y0, x1, y1) без зависимости от fitz."""
    class _R:
        __slots__ = ("x0", "y0", "x1", "y1")

        def __init__(self, b):
            self.x0, self.y0, self.x1, self.y1 = b[0], b[1], b[2], b[3]

    return _R(bbox)


_POGONNY_METRAJ_RE = re.compile(r"^(?:п\.\s*м|м\.\s*п)\.?$", re.IGNORECASE)

# Фасонка/изделия, которые идут поштучно: «м» у них в текстовом слое —
# обрезанное «мм»/«шт», а не погонные метры (чертёжные спецификации
# аспирационных систем: «Отвод ∠ 60°; R=630 мм; D=315 м»).
_PIECE_UNIT_NAME_RE = re.compile(
    r"отвод|тройник|переход|прямик|люк|отсос|зонт|заглушк|врезк"
    r"|крестовин|фланец|ниппель|утка|клапан|дефлектор",
    re.IGNORECASE,
)


def _fix_piece_unit(name: str, unit: str) -> str:
    """«м» → «шт» для фасонки/изделий, если единица явно не погонная.

    Не трогает воздуховоды (у них «м» — настоящие метры): наименование не
    должно начинаться со слова «воздуховод».
    """
    if unit != "м" or not name:
        return unit
    if re.match(r"(?i)\s*воздуховод", name):
        return unit
    if _PIECE_UNIT_NAME_RE.search(name):
        return "шт"
    return unit


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
