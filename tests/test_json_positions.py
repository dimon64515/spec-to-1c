"""Преобразование позиций пайплайна в JSON для as_order_loader (1С).

Закрывает расхождения XML-импорта на стороне прямого пути:
- материал передаётся точной ссылкой (рулон оц. / лист нерж. AISI 430 /
  лист черн. Ст-3) — 1С не подменяет оцинковку на AISI;
- шина на соединение выбирается по правилам техотдела от наибольшего
  размера сечения: ≤350 → УГФ 65, 400–950 → УГФ 95, ≥1000 → УГФ 105
  (в XML-порте 1С порог УГФ-65 ошибочно стоит на 250 мм);
- соединения «0» не передаются — 1С оставляет дефолт изделия.
"""
import pytest

from json_positions import build_position, material_1c_name, shina_by_max_dim


# --- Имена материалов в справочнике асМатериалы (сверены с базой 05.09.2026) ---

def test_material_name_oц_055():
    assert material_1c_name("1", 0.55) == "Рулон оц.(08ПС)0.55"


def test_material_name_oц_07_two_decimals():
    assert material_1c_name("1", 0.7) == "Рулон оц.(08ПС)0.70"


def test_material_name_nерж_430():
    assert material_1c_name("2", 0.8) == "Лист.нерж. (AISI 430) 0.80"


def test_material_name_chern():
    assert material_1c_name("3", 1.2) == "Лист черн.(Ст-3)1.20"


# --- Шина по правилам техотдела (от наибольшего размера сечения) ---

def test_shina_65_up_to_350():
    assert shina_by_max_dim(100) == "65"
    assert shina_by_max_dim(350) == "65"


def test_shina_95_for_400_950():
    assert shina_by_max_dim(400) == "95"
    assert shina_by_max_dim(950) == "95"


def test_shina_105_from_1000():
    assert shina_by_max_dim(1000) == "105"
    assert shina_by_max_dim(2000) == "105"


# --- Сборка позиции из распознанной строки пайплайна ---

def _parsed(article="1-2-1", params=None, quantity=10, thickness=0.55,
            material_code="1", conn0="6", conn1="6"):
    return {
        "article": article,
        "params": params or {"A0": 300, "B0": 200, "L0": 1250},
        "quantity": quantity,
        "thickness": thickness,
        "material_code": material_code,
        "connection_0": conn0,
        "connection_1": conn1,
        "connection_2": "0",
        "connection_3": "0",
        "system": "",
        "comment": "Воздуховод 300x200",
    }


def test_position_rect_flange_has_shina_65():
    pos = build_position(_parsed())
    assert pos["shina"] == "65"
    assert pos["conn0"] == "6" and pos["conn1"] == "6"
    assert pos["material"] == "Рулон оц.(08ПС)0.55"
    assert pos["params"] == {"A0": 300, "B0": 200, "L0": 1250}


def test_position_shina_by_max_side_not_min():
    # 1000x200: по минимальной стороне было бы 65, по максимальной — 105
    pos = build_position(_parsed(params={"A0": 1000, "B0": 200, "L0": 1250},
                               thickness=0.8))
    assert pos["shina"] == "105"


def test_position_round_no_shina():
    pos = build_position(_parsed(article="1-1-2", params={"D0": 160, "L0": 3000},
                                 conn0="2", conn1="2"))
    assert pos["shina"] == ""
    assert pos["conn0"] == "2"


def test_position_zero_connections_no_shina():
    # КСД-адаптер: соединений «шина/уголок» нет — шина не передаётся
    pos = build_position(_parsed(article="10-2-3", params={"A0": 600, "B0": 600},
                                 conn0="0", conn1="0"))
    assert pos["conn0"] == ""
    assert pos["conn1"] == ""
    assert pos["shina"] == ""


# --- Имена параметров под реквизиты ТЧ Товары (метаданные базы 08.09.2026) ---

def test_params_u_R_p_renamed():
    pos = build_position({
        "article": "2-1-1", "quantity": 2, "thickness": 0.55,
        "material_code": "1", "connection_0": "2", "connection_1": "2",
        "params": {"D0": 200, "U0": 90, "R0": 200}, "comment": "c",
    })
    assert pos["params"] == {"D0": 200, "u": 90, "R": 200}


def test_params_other_keys_kept():
    pos = build_position({
        "article": "3-2-5", "quantity": 1, "thickness": 0.7,
        "material_code": "1", "connection_0": "6", "connection_1": "6",
        "params": {"A0": 400, "B0": 300, "A1": 300, "B1": 350, "L0": 200},
        "comment": "c",
    })
    assert pos["params"] == {"A0": 400, "B0": 300, "A1": 300, "B1": 350, "L0": 200}


def test_params_zaglushka_p():
    pos = build_position({
        "article": "6-2-1", "quantity": 1, "thickness": 0.7,
        "material_code": "1", "connection_0": "0", "connection_1": "0",
        "params": {"A0": 400, "B0": 400, "P0": 25}, "comment": "c",
    })
    assert pos["params"] == {"A0": 400, "B0": 400, "p": 25}
