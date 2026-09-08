"""Исправления по сверке парсера с КП №1090 (Владикавказ, 08.09).

Баги найдены на PDF «Проект_Владикавказ_Московский_ВиТ_ОВ.pdf»,
эталон — tmp/kp_raw.txt (поз. 1–224 производимые, 226+ покупные).
"""
from pdf_spec_extractor import parse_spec_text_blocks
from process_specification_table import parse_row, extract_dimensions, normalize_thickness
from json_positions import material_1c_name

DEFAULTS = {"material": "оцинкованная", "thickness": "0.8"}


# --- Баг 1/8: блок-парсер текстового слоя (бренды, единицы, штамп) ---

def test_block_parse_vendor_season_breaks_name():
    lines = ["Дроссель-клапан Ø160", "КР-160", "Сезон", "шт.", "2"]
    rows = parse_spec_text_blocks(lines)
    assert len(rows) == 1
    assert rows[0]["name"] == "Дроссель-клапан Ø160 КР-160"
    assert rows[0]["unit"] == "шт"
    assert rows[0]["quantity"] == 2.0


def test_block_parse_split_vendor_klimatventmash():
    lines = ["Вентилятор дымоудаления", "ВЕРФ-69-7,1ДУ400-4-04-", "Лев0-У2",
             "Климатве", "нтмаш", "шт", "1"]
    rows = parse_spec_text_blocks(lines)
    assert len(rows) == 1
    assert "Климатве" not in rows[0]["name"]
    assert "нтмаш" not in rows[0]["name"]
    assert rows[0]["quantity"] == 1.0


def test_block_parse_stamp_lines_not_glued():
    lines = ["Изм № уч Лист №док Подпись Дата", "Взам. Инв. №", "Подп. и дата",
             "Инв. № подп.", "Воздушный клапан с ручным", "приводом",
             "АВК 800х500", "Арктос", "шт.", "3"]
    rows = parse_spec_text_blocks(lines)
    assert len(rows) == 1
    assert rows[0]["name"] == "Воздушный клапан с ручным приводом АВК 800х500"
    assert rows[0]["quantity"] == 3.0


def test_block_parse_resell_note_kept_as_marker():
    lines = ["Воздуховод из черной стали", "толщиной 2 мм со сварными швами",
             "293x187", "Занести в", "перекупные", "м", "1.6"]
    rows = parse_spec_text_blocks(lines)
    assert len(rows) == 1
    assert rows[0]["size"] == "293x187"
    assert rows[0]["unit"] == "м"
    assert rows[0]["quantity"] == 1.6
    assert "Занести в перекупные" in rows[0]["name"]


def test_block_parse_meters_decimal_qty():
    lines = ["Воздуховод из оцинк. стали", "толщиной 0,5 мм Ø160", "м", "6.5"]
    rows = parse_spec_text_blocks(lines)
    assert len(rows) == 1
    assert rows[0]["unit"] == "м"
    assert rows[0]["quantity"] == 6.5


def test_block_parse_junk_position_numbers_skipped():
    lines = ["1", "2", "3", "Шифр объекта: 013/07-2026-ОВ.С", "Лист.", "4",
             "Воздуховод из оцинк. стали", "толщиной 0,5 мм Ø200", "м", "8.0"]
    rows = parse_spec_text_blocks(lines)
    assert len(rows) == 1
    assert rows[0]["quantity"] == 8.0


# --- Баг 2: круглые воздуховоды в метрах ---

def test_round_duct_meters_spiral_l3000():
    parsed, skipped = parse_row(
        {"name": "Воздуховод из оцинк. стали по ГОСТ 14918-80 S=0,5мм Ø200",
         "size": "", "unit": "м", "quantity": "30"}, DEFAULTS)
    assert skipped is None
    assert parsed["article"] == "1-1-2"
    assert parsed["params"] == {"D0": 200.0, "L0": 3000.0}
    assert parsed["quantity"] == 10  # ceil(30/3)


def test_round_duct_large_diameter_straight_seam():
    """Ø630 S=0,9 (ГОСТ 16523) — прямошовной, правило как у прямоугольных
    (КП поз.198: Ф630-1250)."""
    parsed, skipped = parse_row(
        {"name": "Воздуховод из оцинк. стали по ГОСТ 16523-97 S=0,9мм Ø630",
         "size": "", "unit": "м", "quantity": "2"}, DEFAULTS)
    assert skipped is None
    assert parsed["article"] == "1-1-1"
    assert parsed["params"]["L0"] == 1250.0
    assert parsed["thickness"] == 1.0


# --- Баг 3: прямоугольные воздуховоды в метрах ---

def test_rect_duct_meters_pieces_ceil():
    parsed, skipped = parse_row(
        {"name": "Воздуховод из оцинк. стали по ГОСТ 14918-80 S=0,7мм 400x400",
         "size": "", "unit": "м", "quantity": "15,5"}, DEFAULTS)
    assert skipped is None
    assert parsed["article"] == "1-2-1"
    assert parsed["quantity"] == 13  # КП поз.125
    assert parsed["params"]["L0"] == 1250.0


def test_rect_duct_black_steel_uses_standard_rule():
    """Чёрная сталь: единое правило техотдела — отрезки 1250 мм с
    округлением вверх (КП поз.210: 293x187 м1.6 → 2×1250)."""
    parsed, skipped = parse_row(
        {"name": "Воздуховод из черной стали толщиной 2 мм со сварными швами",
         "size": "293x187", "unit": "м", "quantity": "1.6"}, DEFAULTS)
    assert skipped is None
    assert parsed["article"] == "1-2-1"
    assert parsed["quantity"] == 2
    assert parsed["params"]["L0"] == 1250.0
    assert parsed["material_code"] == "3"


def test_rect_duct_black_steel_two_pieces():
    """Чёрная сталь: метраж 2.0 → 2 отрезка по 1250 (КП поз.212)."""
    parsed, skipped = parse_row(
        {"name": "Воздуховод из черной стали толщиной 2 мм со сварными швами",
         "size": "400x400", "unit": "м", "quantity": "2.0"}, DEFAULTS)
    assert skipped is None
    assert parsed["quantity"] == 2
    assert parsed["params"]["L0"] == 1250.0


def test_rect_duct_black_steel_rounded_pieces():
    """Чёрная сталь: метраж 7.0 → 6 отрезков по 1250 (КП поз.214);
    округление вверх: 7.0/1.25 = 5.6 → 6."""
    parsed, skipped = parse_row(
        {"name": "Воздуховод из черной стали толщиной 2 мм со сварными швами",
         "size": "800x400", "unit": "м", "quantity": "7.0"}, DEFAULTS)
    assert skipped is None
    assert parsed["quantity"] == 6
    assert parsed["params"]["L0"] == 1250.0


# --- Баг 4: «ГОСТ 14918-80» не даёт фантомный D0=80 ---

def test_gost_suffix_not_parsed_as_diameter():
    dims = extract_dimensions("Переход из оцинк. стали по ГОСТ 14918-80 Ø250/ Ø200")
    assert dims.get("D0") == 250.0
    assert dims.get("D1") == 200.0


def test_round_transition_gost_full_row():
    parsed, skipped = parse_row(
        {"name": "Переход из оцинк. стали по ГОСТ 14918-80 S=0,6мм  Ø200/ Ø160",
         "size": "", "unit": "шт", "quantity": "1"}, DEFAULTS)
    assert skipped is None
    assert parsed["article"] == "3-1-1"
    assert parsed["params"]["D0"] == 200.0
    assert parsed["params"]["D1"] == 160.0


# --- Баг 5: врезки круглые → 8-1-1 ---

def test_saddle_round_simple():
    parsed, skipped = parse_row(
        {"name": "Врезка Ø 200", "size": "", "unit": "шт", "quantity": "2"},
        DEFAULTS)
    assert skipped is None
    assert parsed["article"] == "8-1-1"
    assert parsed["params"]["D0"] == 200.0


def test_saddle_round_two_diameters():
    """«Врезка Ø315/Ø125»: D0 = патрубок (меньший), D2 = основной (КП поз.59)."""
    parsed, skipped = parse_row(
        {"name": "Врезка Ø 315/Ø 125", "size": "", "unit": "шт", "quantity": "2"},
        DEFAULTS)
    assert skipped is None
    assert parsed["article"] == "8-1-1"
    assert parsed["params"]["D0"] == 125.0
    assert parsed["params"]["D2"] == 315.0


# --- Баг 6: тройники/переходы без Ø ---

def test_tee_round_slashed_without_prefix():
    parsed, skipped = parse_row(
        {"name": "Тройник-90° из оцинк. стали толщиной 0,6 мм 315/315/200",
         "size": "", "unit": "шт", "quantity": "1"}, DEFAULTS)
    assert skipped is None
    assert parsed["article"] == "4-1-1"
    assert parsed["params"]["D0"] == 315.0
    assert parsed["params"]["D2"] == 200.0


def test_transition_round_slashed_without_prefix():
    parsed, skipped = parse_row(
        {"name": "Переход из оцинк. стали по ГОСТ 14918-80 200/125",
         "size": "", "unit": "шт", "quantity": "1"}, DEFAULTS)
    assert skipped is None
    assert parsed["article"] == "3-1-1"
    assert parsed["params"]["D0"] == 200.0
    assert parsed["params"]["D1"] == 125.0


# --- Баг 7: огнезадерживающие/брендовые клапаны — покупные ---

def test_fire_damper_belimo_is_purchased():
    parsed, skipped = parse_row(
        {"name": "Клапан огнезадерживающий с электромеханическим приводом "
                 "Belimo MB, огнестойкостью EI60, Ø125 ПДВ-1 (60)- НО-MB (220)- Ø125-К",
         "size": "", "unit": "шт", "quantity": "2"}, DEFAULTS)
    assert parsed is None
    assert "покупн" in skipped["reason"].lower()


def test_klоп_damper_is_purchased():
    parsed, skipped = parse_row(
        {"name": "Клапан огнезадерживающий КЛОП-3(120)-НЗ 400х300",
         "size": "", "unit": "шт", "quantity": "1"}, DEFAULTS)
    assert parsed is None
    assert "покупн" in skipped["reason"].lower()


def test_vk_zs_damper_is_purchased():
    parsed, skipped = parse_row(
        {"name": "Клапан с осью под привод ВК-ЗС 800х500 Сезон",
         "size": "", "unit": "шт", "quantity": "1"}, DEFAULTS)
    assert parsed is None
    assert "покупн" in skipped["reason"].lower()


def test_season_throttle_still_manufactured():
    """Дроссели КР-xxx «Сезон» — производимые, не трогаем."""
    parsed, skipped = parse_row(
        {"name": "Дроссель-клапан Ø160 КР-160 Сезон", "size": "",
         "unit": "шт", "quantity": "3"}, DEFAULTS)
    assert skipped is None
    assert parsed["article"] == "16-1-1"
    assert parsed["quantity"] == 3


def test_avk_manual_damper_is_throttle():
    """«Воздушный клапан АВК» — по КП поз.4 это дроссель 16-2-1."""
    parsed, skipped = parse_row(
        {"name": "Воздушный клапан с ручным приводом АВК 800х500",
         "size": "", "unit": "шт", "quantity": "3"}, DEFAULTS)
    assert skipped is None
    assert parsed["article"] == "16-2-1"
    assert parsed["params"]["A0"] == 800.0
    assert parsed["params"]["B0"] == 500.0


# --- Баг 9: нормировка толщин под заводские рулоны ---

def test_thickness_norm_06_to_07():
    assert normalize_thickness(0.6, {"D0": 250.0}, explicit=True) == 0.7
    assert normalize_thickness(0.9, {"D0": 630.0}, explicit=True) == 1.0
    assert normalize_thickness(0.5, {"D0": 200.0}, explicit=True) == 0.55
    assert normalize_thickness(0.8, {"D0": 800.0}, explicit=True) == 0.8


def test_transition_s06_thickness_070():
    parsed, skipped = parse_row(
        {"name": "Переход из оцинк. стали толщиной 0,6 мм Ø200/Ø160",
         "size": "", "unit": "шт", "quantity": "2"}, DEFAULTS)
    assert skipped is None
    assert parsed["thickness"] == 0.7  # КП поз.14/15: Рулон оц. 0.70


# --- Баг 10: зонт — толщина не из габаритов «1,35х0,86» ---

def test_roof_cap_thickness_not_from_dimensions():
    parsed, skipped = parse_row(
        {"name": "Зонт вытяжной В3-4.1 Г- 1,35х0,86х0,3(1350х860х300)",
         "size": "", "unit": "шт", "quantity": "2"}, DEFAULTS)
    assert skipped is None
    assert parsed["article"] == "9-2-1"
    assert parsed["params"]["A0"] == 1350.0
    assert parsed["params"]["B0"] == 860.0
    assert parsed["thickness"] == 1.0  # ГОСТ по макс. размеру 1350


# --- Баг 9 (json_positions): штрипс для спирально-навивных 1-1-2 ---

def test_material_strip_for_spiral_duct():
    assert material_1c_name("1", 0.55, article="1-1-2") == "Штрипс оц.(08ПС)0.55"
    assert material_1c_name("1", 1.0, article="1-1-1") == "Рулон оц.(08ПС)1.00"
    assert material_1c_name("1", 0.7, article="1-2-1") == "Рулон оц.(08ПС)0.70"


# --- Регрессия: «ГОСТ 14918-80» не склеивается с размером ---

def test_gost_designation_not_merged_with_size():
    parsed, skipped = parse_row(
        {"name": "Тройник-90° из оцинк. стали по ГОСТ 14918-80 400x300/400x300/200",
         "size": "", "unit": "шт", "quantity": "1"}, DEFAULTS)
    assert skipped is None
    assert parsed["params"]["A0"] == 400.0
    assert parsed["params"]["B0"] == 300.0
    assert all(v < 10000 for v in parsed["params"].values())
