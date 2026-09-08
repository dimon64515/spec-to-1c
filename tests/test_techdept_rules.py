"""Правила техотдела (ВОК-Регион):
- воздуховоды: сечение от большего размера к меньшему (100x150 -> 150x100);
- отводы: как написано, если не указано иного;
- толщина по ГОСТ: до 350 -> 0.55; 400-950 -> 0.7; 1000 -> 0.8; свыше 1000 -> 1.0;
- металл 0.5 и 0.6 не считается, считать 0.55;
- дымоудаление: толщина минимум 0.8;
- дефлектор = зонт крышный;
- заслонки ручные = ДК (дроссель-клапан 16-x-x).
"""
from process_specification_table import parse_row

DEFAULTS = {"material": "оцинкованная", "thickness": "0.5"}


# --- Сечение воздуховодов: от большего к меньшему; отводы — как написано ---

def test_rect_duct_size_sorted_desc():
    parsed, skipped = parse_row(
        {"name": "Воздуховод", "size": "100x150", "unit": "шт", "quantity": "1"}, DEFAULTS)
    assert skipped is None
    assert parsed["article"] == "1-2-1"
    assert parsed["params"]["A0"] == 150
    assert parsed["params"]["B0"] == 100


def test_rect_duct_size_already_sorted_unchanged():
    parsed, skipped = parse_row(
        {"name": "Воздуховод", "size": "150x100", "unit": "шт", "quantity": "1"}, DEFAULTS)
    assert skipped is None
    assert parsed["params"]["A0"] == 150
    assert parsed["params"]["B0"] == 100


def test_elbow_size_kept_as_written():
    parsed, skipped = parse_row(
        {"name": "Отвод прямоугольный 100x150", "size": "", "unit": "шт", "quantity": "1"}, DEFAULTS)
    assert skipped is None
    assert parsed["article"] == "2-2-2"
    assert parsed["params"]["A0"] == 100
    assert parsed["params"]["B0"] == 150


# --- Толщина по ГОСТ (как минимум) + нормализация 0.5/0.6 -> 0.55 ---

def test_thickness_05_becomes_055():
    parsed, _ = parse_row(
        {"name": "Воздуховод", "size": "300x200", "unit": "шт", "quantity": "1"}, DEFAULTS)
    assert parsed["thickness"] == 0.55


def test_thickness_06_becomes_07():
    # Сверка с КП №1090 (поз.14–15): проектная S=0,6 → заводской рулон 0.70
    parsed, _ = parse_row(
        {"name": "Воздуховод толщиной 0,6 мм", "size": "300x200", "unit": "шт", "quantity": "1"},
        DEFAULTS)
    assert parsed["thickness"] == 0.7


def test_thickness_gost_400_950_is_07():
    parsed, _ = parse_row(
        {"name": "Воздуховод", "size": "500x300", "unit": "шт", "quantity": "1"}, DEFAULTS)
    assert parsed["thickness"] == 0.7


def test_thickness_gost_1000_is_08():
    parsed, _ = parse_row(
        {"name": "Воздуховод", "size": "1000x500", "unit": "шт", "quantity": "1"}, DEFAULTS)
    assert parsed["thickness"] == 0.8


def test_thickness_gost_over_1000_is_10():
    parsed, _ = parse_row(
        {"name": "Воздуховод", "size": "1200x500", "unit": "шт", "quantity": "1"}, DEFAULTS)
    assert parsed["thickness"] == 1.0


def test_thickness_round_duct_gost():
    parsed, _ = parse_row(
        {"name": "Воздуховод", "size": "Ф160", "unit": "шт", "quantity": "1"}, DEFAULTS)
    assert parsed["thickness"] == 0.55


def test_thickness_project_value_above_gost_kept():
    parsed, _ = parse_row(
        {"name": "Воздуховод", "size": "300x200", "unit": "шт", "quantity": "1",
         "thickness": "1.0"}, DEFAULTS)
    assert parsed["thickness"] == 1.0


def test_thickness_smoke_system_min_08():
    parsed, _ = parse_row(
        {"name": "Воздуховод", "size": "Ф160", "unit": "шт", "quantity": "1",
         "system": "Система дымоудаления"}, DEFAULTS)
    assert parsed["thickness"] == 0.8


def test_thickness_smoke_damper_min_08():
    parsed, skipped = parse_row(
        {"name": "Клапан противопожарный дымовой 400x200", "size": "", "unit": "шт", "quantity": "1"},
        DEFAULTS)
    assert skipped is None
    assert parsed["article"] == "20-2"
    assert parsed["thickness"] == 0.8


# --- Дефлектор = Зонт крышный ---

def test_deflector_round_is_roof_cap():
    parsed, skipped = parse_row(
        {"name": "Дефлектор крышный Д315", "size": "", "unit": "шт", "quantity": "1"}, DEFAULTS)
    assert skipped is None
    assert parsed["article"] == "9-1-2"
    assert parsed["params"]["D0"] == 315


def test_roof_cap_rect_is_9_2_1():
    parsed, skipped = parse_row(
        {"name": "Зонт крышный 500x400", "size": "", "unit": "шт", "quantity": "1"}, DEFAULTS)
    assert skipped is None
    assert parsed["article"] == "9-2-1"
    assert parsed["params"]["A0"] == 500
    assert parsed["params"]["B0"] == 400


# --- ЗАСЛОНКИ РУЧНЫЕ = ДК (дроссель-клапан) ---

def test_manual_damper_rect_is_dk():
    parsed, skipped = parse_row(
        {"name": "Заслонка ручная ДК-400х200", "size": "", "unit": "шт", "quantity": "2"}, DEFAULTS)
    assert skipped is None
    assert parsed["article"] == "16-2-1"
    assert parsed["params"]["A0"] == 400
    assert parsed["params"]["B0"] == 200
    # ГОСТ для 400 мм: 0.7
    assert parsed["thickness"] == 0.7


def test_dk_round_is_damper():
    parsed, skipped = parse_row(
        {"name": "ДК-315", "size": "", "unit": "шт", "quantity": "1"}, DEFAULTS)
    assert skipped is None
    assert parsed["article"] == "16-1-1"
    assert parsed["params"]["D0"] == 315


# --- Соединения прямоугольной фасонки: шина/уголок (код 6) ---

def test_rect_elbow_connections_are_flange_strip():
    """Прямоугольный отвод собирается на шине/уголке, иначе 1С берёт
    дефолт изделия (УГФ-95/105 без УГФ-65 для сечений до 350 мм)."""
    parsed, skipped = parse_row(
        {"name": "Отвод прямоугольный 300x200", "size": "", "unit": "шт", "quantity": "1"}, DEFAULTS)
    assert skipped is None
    assert parsed["article"] == "2-2-2"
    assert parsed["connection_0"] == "6"
    assert parsed["connection_1"] == "6"


def test_rect_tee_connections_are_flange_strip():
    parsed, skipped = parse_row(
        {"name": "Тройник прямоугольный 600x300-600x300-300x300", "size": "",
         "unit": "шт", "quantity": "1"}, DEFAULTS)
    assert skipped is None
    assert parsed["article"] == "4-2-3"
    assert parsed["connection_0"] == "6"
    assert parsed["connection_1"] == "6"


def test_round_elbow_connections_unchanged():
    """Круглая фасонка не меняется — соединения остаются «0»."""
    parsed, skipped = parse_row(
        {"name": "Отвод круглый D200", "size": "", "unit": "шт", "quantity": "1"}, DEFAULTS)
    assert skipped is None
    assert parsed["article"] == "2-1-1"
    assert parsed["connection_0"] == "0"
    assert parsed["connection_1"] == "0"


def test_rect_cap_single_side_connection():
    """Заглушка — одностороннее изделие: шина только на стороне 0,
    иначе в 1С появляется шина на несуществующем соединении 1."""
    parsed, skipped = parse_row(
        {"name": "Заглушка прямоугольная 600x300", "size": "", "unit": "шт", "quantity": "1"}, DEFAULTS)
    assert skipped is None
    assert parsed["article"] == "6-2-1"
    assert parsed["connection_0"] == "6"
    assert parsed["connection_1"] == "0"


# --- Явная толщина из проекта нормируется под заводские рулоны ---
# Решение от 05.09.2026 (сверка с КП №599): толщина, указанная в проекте,
# приоритетнее таблицы «от 1100 -> 1.0»; ГОСТ-таблица — только когда
# толщина в проекте не указана («согласно СП 60»).
# Сверка с КП №1090 (08.09.2026): заводские рулоны 0.55/0.70/0.80/1.00 —
# проектная S=0,6 нормируется в 0.70, S=0,9 — в 1.00 (поз.198–201).

def test_thickness_explicit_09_normalized_to_roll_for_large_rect():
    parsed, _ = parse_row(
        {"name": "Воздуховод из оцинкованной стали толщиной 0,9 мм",
         "size": "1200x500", "unit": "м", "quantity": "3"}, DEFAULTS)
    assert parsed["thickness"] == 1.0


def test_thickness_explicit_row_value_normalized_to_roll():
    parsed, _ = parse_row(
        {"name": "Воздуховод", "size": "1200x500", "unit": "шт", "quantity": "1",
         "thickness": "0.9"}, DEFAULTS)
    assert parsed["thickness"] == 1.0


def test_thickness_sp60_400_950_is_07_not_default_08():
    """«согласно СП 60» без явной толщины: таблица техотдела как точное
    значение (400–950 -> 0.7), а не default 0.8."""
    parsed, _ = parse_row(
        {"name": 'Воздуховод из оцинкованной стали толщиной согласно СП 60 класс воздуховодов "П"',
         "size": "400x250", "unit": "м", "quantity": "123", "thickness": ""},
        {"material": "оцинкованная", "thickness": "0.8"})
    assert parsed["thickness"] == 0.7


def test_thickness_sp60_up_to_350_is_055():
    parsed, _ = parse_row(
        {"name": 'Воздуховод из оцинкованной стали толщиной согласно СП 60 класс воздуховодов "П"',
         "size": "300x200", "unit": "м", "quantity": "27", "thickness": ""},
        {"material": "оцинкованная", "thickness": "0.8"})
    assert parsed["thickness"] == 0.55


def test_thickness_sp60_1000_is_08():
    parsed, _ = parse_row(
        {"name": 'Воздуховод из оцинкованной стали толщиной согласно СП 60 класс воздуховодов "П"',
         "size": "1000x500", "unit": "м", "quantity": "11", "thickness": ""},
        {"material": "оцинкованная", "thickness": "0.8"})
    assert parsed["thickness"] == 0.8


# --- КСД: толщина по таблице от максимального размера сечения ---

def test_ksd_thickness_gost_from_rect_size():
    parsed, skipped = parse_row(
        {"name": "Квадратный диффузор с адаптером 4АПН 600х600 + КСД 200",
         "size": "600x600", "unit": "шт", "quantity": "48"}, DEFAULTS)
    assert skipped is None
    assert parsed["article"] == "10-2-3"
    assert parsed["thickness"] == 0.7


# --- Оборудование: «м3/ч» и номер системы «В2.2» не должны давать толщину ---

def test_thickness_m3h_not_parsed_as_thickness():
    """«м3/ч» (кубометры в час) в описании оборудования не толщина стенки."""
    parsed, _ = parse_row(
        {"name": "В3.1 (L=480 м3/ч, Pc=200 Па) Оборудование Шумоглушитель KNK 200/6",
         "size": "Ф200", "unit": "шт", "quantity": "2"}, DEFAULTS)
    assert parsed["thickness"] != 3.0
    assert parsed["thickness"] == 0.55  # ГОСТ по макс. размеру сечения


def test_thickness_system_number_not_parsed_as_thickness():
    """Номер системы «В2.2»/«В2.3» в начале строки не толщина стенки."""
    parsed, _ = parse_row(
        {"name": "В2.2 (L=375 м3/ч, Pc=200 Па) Оборудование Шумоглушитель KNK 160/6",
         "size": "Ф160", "unit": "шт", "quantity": "2"}, DEFAULTS)
    assert parsed["thickness"] != 2.2
    parsed2, _ = parse_row(
        {"name": "В2.3 (L=40 м3/ч, Pc=150 Па) Оборудование Шумоглушитель KNK 100/6",
         "size": "Ф100", "unit": "шт", "quantity": "2"}, DEFAULTS)
    assert parsed2["thickness"] != 2.3


def test_thickness_explicit_still_works():
    """Явная толщина в наименовании по-прежнему извлекается
    и нормируется под заводской рулон (0.9 -> 1.00 по КП №1090)."""
    parsed, _ = parse_row(
        {"name": "Воздуховод толщиной 0,9 мм",
         "size": "800x500", "unit": "м", "quantity": "5", "thickness": ""},
        DEFAULTS)
    assert parsed["thickness"] == 1.0
