#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Тесты ридера заявки менеджера (zayavka_xlsx) и жаргона заявок
в process_specification_table («с/н», «н.ж.», «(90)», «односторонний»,
«Прямая врезка», «Опуск», покупные позиции)."""

import warnings
from pathlib import Path

import pandas as pd
import pytest

warnings.filterwarnings("ignore")

from process_specification_table import parse_row, process_rows
from zayavka_xlsx import is_zayavka_xlsx, parse_zayavka_xlsx

BASE = Path(__file__).resolve().parent.parent
PROJ = BASE / "examples" / "projects" / "Столовая_МРЭО_Магас_КП1003"
ZAYAVKA = PROJ / "Заявка Столовая_ Молельная (1).xlsx"
KP = PROJ / "КП 1003 Столовая МРЭО г.Магас ул. 65 лет Победы, 15Б,.xls"
PROJECT_SPEC = BASE / "examples" / "для обучения 03.09.26" / "КР-0324-ОВ_страницы.xlsx"

DEFAULTS = {"material": "оцинкованная", "thickness": "0.8"}


# --- детект формата ---

def test_detect_zayavka_format():
    df = pd.read_excel(ZAYAVKA, header=None, dtype=object)
    assert is_zayavka_xlsx(df) is True


def test_detect_not_zayavka_kp():
    df = pd.read_excel(KP, header=None, dtype=object)
    assert is_zayavka_xlsx(df) is False


def test_detect_not_zayavka_project_spec():
    df = pd.read_excel(PROJECT_SPEC, header=None, dtype=object)
    assert is_zayavka_xlsx(df) is False


def test_detect_not_zayavka_plain_table():
    # обычная CSV-таблица со штатными колонками — не заявка
    df = pd.DataFrame({
        "наименование": ["Воздуховод", "Отвод"],
        "размер": ["160", "Ф200"],
        "ед": ["м", "шт"],
        "количество": ["10", "2"],
    })
    assert is_zayavka_xlsx(df) is False


# --- разбор заявки ---

def test_parse_zayavka_rows_and_systems():
    rows = parse_zayavka_xlsx(ZAYAVKA)
    assert len(rows) == 79
    systems = {r["system"] for r in rows}
    assert "Молельная/Приток (1000 м3/ч; 250 Па)" in systems
    assert "Молельная/Вытяжка (1000 м3/ч; 250 Па)" in systems
    assert "Столовая/Вытяжка кухня (5250 м3/ч; 550 Па)" in systems
    assert "Столовая/Приток столовая (3500 м3/ч; 450 Па)" in systems
    assert all(r["source"] == "zayavka" for r in rows)
    # количество и единицы
    by_name = {(r["system"], r["name"]): r for r in rows}
    assert by_name[("Молельная/Приток (1000 м3/ч; 250 Па)",
                    "Воздуховод с/н 250")]["quantity"] == 2
    assert by_name[("Столовая/Приток столовая (3500 м3/ч; 450 Па)",
                    "Воздуховод 600х200-1250")]["quantity"] == 4
    # позиция без количества сохраняется (не теряется молча)
    opusk = by_name[("Столовая/Вытяжка кухня (5250 м3/ч; 550 Па)",
                     "Опуск н.ж. (воздуховод 315-500) 315-500")]
    assert opusk["quantity"] is None
    # примечание проброшено
    vrezka = by_name[("Столовая/Приток столовая (3500 м3/ч; 450 Па)",
                      "Врезка 600х600-100")]
    assert "высота" in vrezka["note"]


# --- жаргон заявок → артикулы/параметры ---

JARGON_CASES = [
    # (наименование, артикул, ожидаемые параметры, код материала)
    ("Воздуховод с/н 315", "1-1-2", {"D0": 315, "L0": 3000}, "1"),
    ("Воздуховод с/н 315-3000", "1-1-2", {"D0": 315, "L0": 3000}, "1"),
    ("Воздуховод н.ж. 400-1250", "1-1-1", {"D0": 400, "L0": 1250}, "2"),
    ("Воздуховод 600х300-1250", "1-2-1", {"A0": 600, "B0": 300, "L0": 1250}, "1"),
    ("Отвод (90) 400", "2-1-1", {"D0": 400, "U0": 90, "R0": 400}, "1"),
    ("Отвод н.ж. (90) 400", "2-1-1", {"D0": 400, "U0": 90, "R0": 400}, "2"),
    ("Отвод (90) 600х250", "2-2-2", {"A0": 600, "B0": 250, "U0": 90}, "1"),
    ("Переход односторонний 315/250", "3-1-1", {"D0": 315, "D1": 250}, "1"),
    ("Переход  односторонний 600х350/600х300", "3-2-5",
     {"A0": 600, "B0": 350, "A1": 600, "B1": 300}, "1"),
    # «Врезка 315/200» = врезка D200 в магистраль D315 → D0=200, D2=315
    ("Врезка 315/200", "8-1-1", {"D0": 200, "D2": 315}, "1"),
    ("Прямая врезка 160", "8-1-1", {"D0": 160, "D2": 160}, "1"),
    ("Врезка н.ж. 400/315", "8-1-1", {"D0": 315, "D2": 400}, "2"),
    ("Врезка 600х600-100", "8-2-1", {"A0": 600, "B0": 600}, "1"),
    ("Заглушка 200", "6-1-1", {"D0": 200}, "1"),
    ("Заглушка н.ж. 400", "6-1-1", {"D0": 400}, "2"),
    ("Заглушка 600х200", "6-2-1", {"A0": 600, "B0": 200}, "1"),
    ("Дроссель-клапан н.ж. 315", "16-1-1", {"D0": 315}, "2"),
    ("Зонт пристенный н.ж. с лабиринтными жироуловителями 1500х1500х400 "
     "со встроенными врезками 315", "19-2-1", {"A0": 1500, "B0": 400}, "2"),
]


@pytest.mark.parametrize("name,article,params,material_code", JARGON_CASES)
def test_jargon_names_to_articles(name, article, params, material_code):
    row = {"name": name, "size": "", "unit": "шт", "quantity": "1",
           "source": "zayavka", "system": "Молельная/Приток"}
    parsed, skipped = parse_row(row, DEFAULTS)
    assert skipped is None, f"{name}: {skipped}"
    assert parsed["article"] == article
    for key, val in params.items():
        assert parsed["params"][key] == val, f"{name}: {key}"
    assert parsed["material_code"] == material_code
    assert parsed["system"] == "Молельная/Приток"


BUYOUT_CASES = [
    "Вентилятор 315 (пластик)",
    "Вентилятор ВР 280-46 №4,0 (3,0 кВт; 960 об/мин)",
    "Обратный клапан 315",
    "Фильтр в корпусе 315",
    "Шумоглушитель 315/600",
    "Шумоглушитель н.ж. 400/900",
    "Диффузор 200",
    "Диффузор потолочный 600х600 с КРВ",
    "Сетка (решётка ALAV) 315",
    "Решётка наружная нерегулируемая 600х350",
    "Пенофол 5С",
    "Гибкая вставка 400 AISI 304",
    "Гибкий теплоизолированный воздуховод 160",
    "Косой отвод с сеткой 280х280",
    "Кронштейн усиленный для ВР 280-46 №4,0",
    "Виброизоляторы для ВР 280-46 №4,0",
    "Симистор 2,5 А",
    "Шкаф 6 модулей",
    "Автомат 3П 10А",
    "Кабель ПВС 3х1,5",
    "Частотный преобразователь 4 кВт; 380 В",
    "Электропривод воздушной заслонки с возвратной пружиной 5 Н*м",
    "Воздушный алюминиевый клапан 600х350 с площадкой под привод",
]


@pytest.mark.parametrize("name", BUYOUT_CASES)
def test_zayavka_buyout_skipped(name):
    row = {"name": name, "size": "", "unit": "шт", "quantity": "1",
           "source": "zayavka"}
    parsed, skipped = parse_row(row, DEFAULTS)
    assert parsed is None
    assert "Покупная" in skipped["reason"] or "reason" in skipped


def test_zayavka_buyout_not_applied_to_other_sources():
    # шумоглушитель без source=zayavka — обычное поведение (LITENED → 15-2-1)
    row = {"name": "Шумоглушитель LITENED 100-50 NKD", "size": "",
           "unit": "шт", "quantity": "2"}
    parsed, skipped = parse_row(row, DEFAULTS)
    assert skipped is None
    assert parsed["article"] == "15-2-1"


def test_zayavka_opusk_without_qty_skipped():
    # «Опуск» распознаётся как отвод, но без количества в XML не попадает
    row = {"name": "Опуск н.ж. (воздуховод 315-500) 315-500", "size": "",
           "unit": "", "quantity": None, "source": "zayavka"}
    parsed, skipped = parse_row(row, DEFAULTS)
    assert parsed is None
    assert "количеств" in skipped["reason"]


# --- сквозной прогон заявки ---

def test_zayavka_end_to_end_counts():
    rows = parse_zayavka_xlsx(ZAYAVKA)
    xml, skipped, success = process_rows(rows)
    assert len(rows) == 79
    assert len(success) == 36
    assert len(skipped) == 43
    # покупных позиций в XML нет
    assert not any(
        w in str(r.get("comment", "")).lower()
        for r in success
        for w in ("вентилятор", "шумоглушител", "диффузор", "пенофол",
                  "кронштейн", "виброизолятор", "преобразовател")
    )
    # системы прописаны в строках XML
    systems = {r["system"] for r in success}
    assert "Столовая/Приток столовая (3500 м3/ч; 450 Па)" in systems
