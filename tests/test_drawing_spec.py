"""Спецификация изделий и материалов, встроенная в чертёж аспирационной
системы (г. Ростов-на-Дону, КП 1191).

Покрывает:
- позиционный разбор таблицы из текстового слоя чертежа
  (extract_drawing_spec_rows): колонки «Поз. / Наименование / Кол.» размазаны
  по слою вперемешку с выносками схемы, построчный разбор их склеивает;
- разбор нотаций таких таблиц в process_specification_table:
  «Отвод ∠ 60°; R=630 мм; D=315 мм», «Прямик D=280 мм; L=1250 мм»,
  «Тройник D355/D225/D280», «Тройник 500/315/500», «Переходник 672х396/D500»,
  «Местный отсос 1000х350х250(h)», «Зонт вентиляционный круглый D500»;
- починку единицы «м» → «шт» для фасонки в блок-парсере текстового слоя.
"""
from pathlib import Path

import pytest

from pdf_spec_extractor import extract_drawing_spec_rows, parse_spec_text_blocks
from process_specification_table import parse_row

EXAMPLES = Path(__file__).resolve().parent.parent / "examples" / "projects"
PDF_LIST3 = EXAMPLES / "Проект_лист3_КП1191" / "3.pdf"
PDF_LIST4 = EXAMPLES / "Проект_лист4_без_КП" / "4.pdf"

DEFAULTS = {"material": "оцинкованная", "thickness": "0.7"}


def _parse(name, unit="шт", quantity=1):
    parsed, skipped = parse_row(
        {"name": name, "size": "", "unit": unit, "quantity": quantity},
        DEFAULTS,
    )
    assert skipped is None, skipped
    return parsed


# ---------- extract_drawing_spec_rows ----------

@pytest.mark.skipif(not PDF_LIST3.exists(), reason="3.pdf не найден в examples")
def test_drawing_spec_list3_positions_and_quantities():
    rows = extract_drawing_spec_rows(str(PDF_LIST3))[1]
    assert len(rows) == 30
    # Прямик D=500 ×22 и Прямик 500х500 ×18 — многозначные количества
    # из колонки «Кол.» не теряются.
    by_name = {}
    for r in rows:
        assert r["unit"] == "шт"
        by_name.setdefault(r["name"], 0)
        by_name[r["name"]] += r["quantity"]
    assert by_name["Прямик D=500 мм; L=1250 мм"] == 22
    assert by_name["Прямик 500х500 мм; L=1250 мм"] == 18
    assert by_name["Отвод ∠ 90°; R=1000 мм; D=500 мм"] == 7
    # примечание «(3 метра)» из соседней колонки приклеено к наименованию
    pur = [r for r in rows if "полиуретановый" in r["name"].lower()]
    assert len(pur) == 2
    assert all("3 метра" in r["name"] for r in pur)
    # выноски схемы (люки, расходы, градусы) в строки не попали
    assert not any("Люк" in r["name"] for r in rows)
    assert not any("м3/ч" in r["name"] for r in rows)


@pytest.mark.skipif(not PDF_LIST4.exists(), reason="4.pdf не найден в examples")
def test_drawing_spec_list4_positions_and_quantities():
    rows = extract_drawing_spec_rows(str(PDF_LIST4))[1]
    assert len(rows) == 23
    by_qty = {(r["name"]): r["quantity"] for r in rows}
    assert by_qty["Прямик D=400 мм; L=1250 мм"] == 32
    assert by_qty["Прямик D=355 мм; L=1250 мм"] == 8
    assert by_qty["Отвод ∠ 90°; R=710 мм; D=355 мм"] == 3
    # таблица в лист4 слева, схема справа — выноски не должны просочиться
    assert not any("Люк" in r["name"] for r in rows)
    assert not any("ЦН" in r["name"] for r in rows)


# ---------- нотации в process_specification_table ----------

def test_pryamik_round_duct_with_eq_diameter_and_length():
    """«Прямик D=280 мм; L=1250 мм» — прямошовный круглый воздуховод 1-1-1."""
    parsed = _parse("Прямик D=280 мм; L=1250 мм", quantity=4)
    assert parsed["article"] == "1-1-1"
    assert parsed["params"] == {"D0": 280.0, "L0": 1250.0}
    assert parsed["quantity"] == 4


def test_pryamik_rect_duct():
    parsed = _parse("Прямик 500х500 мм; L=1250 мм", quantity=18)
    assert parsed["article"] == "1-2-1"
    assert parsed["params"] == {"A0": 500.0, "B0": 500.0, "L0": 1250.0}
    assert parsed["quantity"] == 18


def test_elbow_angle_radius_eq_diameter_notation():
    """«Отвод ∠ 60°; R=630 мм; D=315 мм» → 2-1-1 с U0/R0 из текста."""
    parsed = _parse("Отвод ∠ 60°; R=630 мм; D=315 мм")
    assert parsed["article"] == "2-1-1"
    assert parsed["params"] == {"D0": 315.0, "U0": 60.0, "R0": 630.0}


def test_elbow_rectangular_angle_notation():
    parsed = _parse("Отвод ∠ 90°; 500х500 мм")
    assert parsed["article"] == "2-2-2"
    assert parsed["params"]["A0"] == 500.0
    assert parsed["params"]["B0"] == 500.0
    assert parsed["params"]["U0"] == 90.0


def test_tee_aspirational_notation_branch_last():
    """«Тройник D355/D225/D280» — ветвь последняя (магистраль/ветвь-угол/Dглавн)."""
    parsed = _parse("Тройник D355/D225/D280")
    assert parsed["article"] == "4-1-1"
    assert parsed["params"]["D0"] == 355.0
    assert parsed["params"]["D2"] == 280.0


def test_tee_main_branch_main_notation():
    """«Тройник 500/315/500» — первая и последняя одинаковы, ветвь посередине."""
    parsed = _parse("Тройник 500/315/500")
    assert parsed["article"] == "4-1-1"
    assert parsed["params"]["D0"] == 500.0
    assert parsed["params"]["D2"] == 315.0


def test_transition_eq_diameters():
    parsed = _parse("Переходник D500/D355")
    assert parsed["article"] == "3-1-1"
    assert parsed["params"]["D0"] == 500.0
    assert parsed["params"]["D1"] == 355.0


def test_transition_rect_to_round():
    parsed = _parse("Переходник 672х396/D500")
    assert parsed["article"] == "3-3-1"
    assert parsed["params"] == {"A0": 672.0, "B0": 396.0, "D0": 500.0, "L0": 200}


def test_transition_rect_to_rect_not_a_round_stub():
    """Кириллическое «х» не должно давать ложный круглый патрубок D0."""
    parsed = _parse("Переходник 378х378/400х400")
    assert parsed["article"] == "3-2-5"
    assert parsed["params"] == {"A0": 378.0, "B0": 378.0,
                                "A1": 400.0, "B1": 400.0, "L0": 200}


def test_local_hood_is_exhaust_hood():
    """«Местный отсос 1000х350х250(h)» — вытяжной зонт 19-2-1: первое число —
    длина, далее сечение AxB."""
    parsed = _parse("Местный отсос 1000х350х250(h) мм")
    assert parsed["article"] == "19-2-1"
    assert parsed["params"] == {"A0": 350.0, "B0": 250.0, "L0": 1000.0}


def test_round_roof_cap_from_drawing():
    parsed = _parse("Зонт вентиляционный круглый D500")
    assert parsed["article"] == "9-1-2"
    assert parsed["params"] == {"D0": 500.0}


def test_rect_roof_cap_from_drawing():
    parsed = _parse("Зонт вентиляционный прямоугольный 400х400 мм")
    assert parsed["article"] == "9-2-1"
    assert parsed["params"]["A0"] == 400.0
    assert parsed["params"]["B0"] == 400.0


def test_polyurethane_duct_is_buyout():
    parsed, skipped = parse_row(
        {"name": 'Воздуховод полиуретановый PUR FR1.0, диам. 315 мм [(3 метра)]',
         "size": "", "unit": "шт", "quantity": 1},
        DEFAULTS,
    )
    assert parsed is None
    assert "покупн" in skipped["reason"].lower()


# ---------- починка единицы м → шт в блок-парсере ----------

def test_block_parser_piece_unit_not_meter():
    """Обрезанное «мм»/«шт» в слое даёт единицу «м» у фасонки — чиним на «шт»."""
    rows = parse_spec_text_blocks([
        "Отвод ∠ 60°; R=630 мм; D=315 м",
        "м",
        "1",
        "Прямик D=280 мм; L=1250 м",
        "м",
        "2",
    ])
    assert [r["unit"] for r in rows] == ["шт", "шт"]
    assert [r["quantity"] for r in rows] == [1.0, 2.0]


def test_block_parser_real_meter_duct_untouched():
    """У воздуховода в метрах единица «м» остаётся."""
    rows = parse_spec_text_blocks([
        "Воздуховод из оцинкованной стали",
        "500x200",
        "м",
        "75",
    ])
    assert rows[0]["unit"] == "м"
    assert rows[0]["quantity"] == 75.0
