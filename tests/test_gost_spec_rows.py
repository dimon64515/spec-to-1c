"""Позиционный разбор спецификации ГОСТ 21.602 в многостраничном проектном
документе (Архитектурное бюро А2, tmp/bitrix_bot/170.pdf).

Бланк ГОСТ без линий сетки: find_tables распадает таблицу на ячейки, построчный
разбор теряет количества. extract_gost_spec_rows восстанавливает строки по
координатам колонок («Позиция | Наименование | … | Ед. изм. | Количество»):
многострочные ячейки склеиваются, количество берётся строго из своей колонки.
"""
from pathlib import Path

import pytest

from pdf_spec_extractor import extract_gost_spec_rows

PDF_170 = Path(__file__).resolve().parent.parent / "tmp" / "bitrix_bot" / "170.pdf"

pytestmark = pytest.mark.skipif(not PDF_170.exists(), reason="170.pdf не найден")


def test_gost_spec_rows_cover_spec_pages():
    """Спецификация на страницах ~13–36: все строки с непустым именем,
    ненулевым количеством и адекватной единицей."""
    by_page = extract_gost_spec_rows(str(PDF_170))
    assert by_page, "ГОСТ-строки не найдены"
    pages = sorted(by_page)
    assert min(pages) <= 13
    assert max(pages) >= 30
    total = 0
    allowed_units = {"шт", "м", "м2", "м²", "м3", "компл", "кг", "рул"}
    for page in pages:
        rows = by_page[page]
        assert rows, page
        for row in rows:
            total += 1
            assert row["name"].strip(), (page, row)
            assert row["quantity"] > 0, (page, row)
            assert row["unit"] in allowed_units, (page, row)
            # служебный мусор штампа в строки не просочился
            for word in ("Изм", "Подпись", "Стадия", "Формат", "Лист"):
                assert word not in row["name"], (page, row)
    assert total >= 150


def test_gost_spec_rows_multiline_cells_and_quantities():
    """Многострочные ячейки собраны, количества не потеряны."""
    by_page = extract_gost_spec_rows(str(PDF_170), pages=[13, 20, 30])
    p13 = by_page[13]
    # Вентиляторы первых двух систем страницы 13, по 1 шт
    fans = [r for r in p13 if "Вентилятор канальный" in r["name"]]
    assert len(fans) == 2
    assert all(r["unit"] == "шт" and r["quantity"] == 1.0 for r in fans)
    # круглый воздуховод Ø250 — 83 м (длинное количество из своей колонки)
    duct83 = [r for r in p13 if r["unit"] == "м" and r["quantity"] == 83.0]
    assert len(duct83) == 1
    assert "Ø250" in duct83[0]["name"] or duct83[0]["size"] == "Ø250"
    # изоляция — площади, не штуки
    iso = [r for r in p13 if r["name"].startswith("Изоляция")]
    assert iso and all(r["unit"] in ("м2", "м²") for r in iso)
    # страница 20: переточная решётка 3 шт, воздуховод 87 м
    p20 = by_page[20]
    grille = [r for r in p20 if "Решетка переточная" in r["name"]]
    assert grille and grille[0]["quantity"] == 3.0 and grille[0]["unit"] == "шт"
    assert any(r["unit"] == "м" and r["quantity"] == 87.0 for r in p20)
    # страница 30: канальный вентилятор 69 шт (многозначное количество)
    p30 = by_page[30]
    fan = [r for r in p30 if "Канальный вентилятор" in r["name"]]
    assert fan and fan[0]["quantity"] == 69.0 and fan[0]["unit"] == "шт"


def test_gost_spec_rows_duct_section_in_size():
    """Сечение воздуховода из колонки «Тип, марка» вынесено в size."""
    by_page = extract_gost_spec_rows(str(PDF_170), pages=[13])
    duct = [r for r in by_page[13]
            if r["unit"] == "м" and r["quantity"] == 83.0][0]
    assert duct["size"] == "Ø250"
    assert "Ø250" not in duct["name"]


def test_load_tables_from_pdf_gost_beats_blocks():
    """Wiring: ГОСТ-строки идут в block_rows вместо блокного разбора,
    process_rows даёт success существенно больше 24."""
    from api import load_tables_from_pdf
    from process_specification_table import process_rows

    result = load_tables_from_pdf(PDF_170.read_bytes())
    assert "block_rows" in result
    rows = result["block_rows"]
    assert len(rows) >= 150
    assert all(r["name"].strip() and r["quantity"] > 0 for r in rows)
    _, _, success = process_rows(rows, run_llm_size_classify=False)
    assert len(success) > 24
