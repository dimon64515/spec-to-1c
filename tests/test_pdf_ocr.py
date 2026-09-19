"""Тесты OCR-фолбэка для PDF без текстового слоя (проект Прачечная, ОВ7).

Страницы 3–18 документа 09_2021-ОВ7.pdf — векторные кривые вместо текста,
поэтому обычные extract_tables_from_pdf/parse_spec_text_blocks не работают.
Здесь проверяются: точечный OCR страницы (ocr_page_text), эвристики
размеров без tesseract и построчный разбор ведомости (_ocr_spec_rows_from_page).
"""
import shutil
from pathlib import Path

import pytest

import pdf_spec_extractor as pse

PROJECT_ROOT = Path(__file__).parent.parent
PRACH_PDF = PROJECT_ROOT / "examples" / "projects" / "Прачечная_Заявка1075" / "09_2021-ОВ7.pdf"

needs_tesseract = pytest.mark.skipif(
    shutil.which("tesseract") is None, reason="tesseract не установлен"
)
needs_pdf = pytest.mark.skipif(not PRACH_PDF.exists(), reason="PDF Прачечной не найден")


# --- чистые функции (без tesseract) ---

def test_split_name_size_merges_split_mm_token():
    """«10000 мм» (токен и «мм» разошлись в слова) склеивается в размер."""
    name, token = pse._split_name_size(
        "Дроссель- клапан из оцинкованной стали 10000 мм ГОСТ 14918-2020")
    assert token == "10000мм"
    assert "10000" not in name and "ГОСТ" in name


def test_split_name_size_takes_last_size_token():
    name, token = pse._split_name_size("Воздуховод из оцинкованной стали 100x100мм")
    assert token == "100x100мм"
    assert name == "Воздуховод из оцинкованной стали"


def test_merged_digits_size_heuristic():
    assert pse._merged_digits_size("1200600мм") == "1200x600"
    assert pse._merged_digits_size("200150") == "200x150"
    assert pse._merged_digits_size("160") == ""


def test_extract_size_candidates_plausibility():
    cands = pse._extract_size_candidates("To xe 150x150mm ГОСТ 14918-2020")
    assert cands.get("150x150", 0) > cands.get("190x150", 0)
    # круглое сечение с префиксом-заменой ø
    assert "Ф250" in pse._extract_size_candidates("To xe 0250Mm")
    # разделитель «x» потерян целиком
    assert "900x500" in pse._extract_size_candidates("To xe 900500mm")


def test_norm_unit_token_ocr_noise():
    assert pse._norm_unit_token("ом") == "м"
    assert pse._norm_unit_token("шт.") == "шт"
    assert pse._norm_unit_token("м?") == "м²"
    assert pse._norm_unit_token("ГОСТ") is None


# --- OCR через tesseract ---

@needs_tesseract
@needs_pdf
def test_ocr_page_text_produces_cyrillic():
    """Страница 3 (общие указания) без текстового слоя: OCR отдаёт кириллицу."""
    import fitz

    with fitz.open(str(PRACH_PDF)) as doc:
        text = pse.ocr_page_text(doc[2], dpi=150)
    assert "Воздуховод" in text or "вентиляц" in text.lower()
    cyrillic = [ch for ch in text if "А" <= ch <= "я"]
    assert len(cyrillic) > 100


@needs_tesseract
@needs_pdf
def test_ocr_spec_rows_page18_ducts():
    """Ведомость воздуховодов (стр. 18): круглые и прямоугольные позиции."""
    import fitz

    with fitz.open(str(PRACH_PDF)) as doc:
        rows = pse._ocr_spec_rows_from_page(doc[17])
    by_size = {r["size"]: r for r in rows if r["size"]}
    assert by_size["100x100"]["unit"] == "м"
    assert by_size["100x100"]["quantity"] == 9.0
    assert by_size["Ф160"]["quantity"] == 10.0
    assert by_size["1200x600"]["quantity"] == 28.0
    # «То же, …» развёрнуто в полное наименование с толщиной
    assert "Воздуховод" in by_size["Ф160"]["name"]


@needs_tesseract
@needs_pdf
def test_ocr_spec_rows_page17_dampers_and_hoods():
    """Ведомость арматуры (стр. 17): зонты и дроссель-клапаны поштучно."""
    import fitz

    with fitz.open(str(PRACH_PDF)) as doc:
        rows = pse._ocr_spec_rows_from_page(doc[16])
    by_size = {r["size"]: r for r in rows if r["size"]}
    assert by_size["Ф100"]["unit"] == "шт" and by_size["Ф100"]["quantity"] == 1.0
    assert by_size["1000x500"]["quantity"] == 1.0
    assert "Дроссель" in by_size["100x100"]["name"]
