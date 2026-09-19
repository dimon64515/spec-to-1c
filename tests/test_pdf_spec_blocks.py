"""Блок-парсер текстового слоя ГОСТ-бланков спецификации (fallback).

Бланки вида «Позиция / Наименование / ... / Ед.изм / Количество» с
многострочными ячейками не извлекаются find_tables (строки съезжаются
по колонкам). Текстовый слой даёт блоки: наименование (1+ строк) ->
размер -> «Россия»/бренд -> ед. -> количество.
"""
from pdf_spec_extractor import parse_spec_text_blocks

LINES = [
    "ВЕНТИЛЯЦИЯ",
    "Воздуховоды и фасонные изделия из оцинкованной стали",
    "Воздуховод из оцинкованной стали толщиной 0,9 мм",
    "150x150",
    "Россия",
    "м",
    "75",
    "Фасонные изделия из черной стали толщиной 1 мм",
    "Россия",
    "м2",
    "250",
]


def test_block_parse_duct_row():
    rows = parse_spec_text_blocks(LINES)
    ducts = [r for r in rows if "Воздуховод" in r["name"]]
    assert len(ducts) == 1
    assert ducts[0]["name"] == "Воздуховод из оцинкованной стали толщиной 0,9 мм"
    assert ducts[0]["size"] == "150x150"
    assert ducts[0]["unit"] == "м"
    assert ducts[0]["quantity"] == 75.0


def test_block_parse_aggregate_row():
    rows = parse_spec_text_blocks(LINES)
    agg = [r for r in rows if "Фасонные" in r["name"]]
    assert len(agg) == 1
    assert agg[0]["size"] == ""
    assert agg[0]["unit"] == "м2"
    assert agg[0]["quantity"] == 250.0


def test_block_parse_strips_section_headers_and_bom():
    lines = LINES + ["Лист", "Изм.", "Подпись", "Дата", "2024-00061-00-0-ОВ2"]
    rows = parse_spec_text_blocks(lines)
    names = [r["name"] for r in rows]
    # заголовки разделов не клеятся к первой строке группы
    assert "Воздуховод из оцинкованной стали толщиной 0,9 мм" in names
    # элементы штампа/колонтитулы не становятся строками
    assert not any(n in ("Лист", "Изм.", "Подпись") for n in names)


def test_block_parse_round_duct_with_multiword_name():
    lines = [
        "Воздуховод из нержавеющей стали толщиной 0,8 мм класс воздуховодов \"П\"",
        "200",
        "Россия",
        "м",
        "50",
    ]
    rows = parse_spec_text_blocks(lines)
    assert len(rows) == 1
    assert rows[0]["size"] == "200"
    assert rows[0]["quantity"] == 50.0
    assert "нержавеющей" in rows[0]["name"]


def test_block_parse_pogonny_metraj_glued_and_split():
    """«п.м.» приходит то склеенной, то парой «п.»+«м.» — обе формы → «м».

    ГОСТ-ведомость Заявки №1274 (ОВ1): у прямоугольных воздуховодов единица
    разорвана, у круглых склеена; встречается и обрезанное «мм»→«м» толщины
    в конце наименования («…толщиной 0.5 м»).
    """
    lines = [
        "Воздуховод прямоугольный из оцинкованной стали толщиной",
        "0.7мм",
        "300x150",
        "п.",
        "м.",
        "1",
        "Воздуховод прямоугольный из оцинкованной стали толщиной",
        "0.7мм",
        "400x200",
        "п.м.",
        "3.5",
        "Воздуховод  круглый из оцинкованной стали толщиной 0.5 м",
        "100",
        "п.м.",
        "65",
    ]
    rows = [r for r in parse_spec_text_blocks(lines) if "Воздуховод" in r["name"]]
    assert len(rows) == 3
    assert rows[0]["size"] == "300x150"
    assert rows[0]["unit"] == "м"
    assert rows[0]["quantity"] == 1.0
    assert rows[1]["size"] == "400x200"
    assert rows[1]["unit"] == "м"
    assert rows[1]["quantity"] == 3.5
    # круглый: диаметр не улетел в quantity, «м» не обрезалась из «мм»
    assert rows[2]["size"] == "100"
    assert rows[2]["unit"] == "м"
    assert rows[2]["quantity"] == 65.0
    assert "0.5 м" in rows[2]["name"]


def test_block_parse_orphan_pogonny_row_merges_into_previous():
    """Одинокий «п.» с количеством вливается в предыдущую строку без единицы."""
    lines = [
        "Воздуховод прямоугольный из оцинкованной стали толщиной 0.7мм",
        "600x200",
        "п.",
        "м",
        "2",
    ]
    rows = [r for r in parse_spec_text_blocks(lines) if "Воздуховод" in r["name"]]
    assert len(rows) == 1
    assert rows[0]["size"] == "600x200"
    assert rows[0]["unit"] == "м"
    assert rows[0]["quantity"] == 2.0
