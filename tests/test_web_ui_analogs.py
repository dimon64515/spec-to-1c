"""Тесты подбора аналогов web_ui.analogs для пропущенных строк."""

import pytest

from json_positions import build_position
from map_customer_equipment import load_mapping
from web_ui.analogs import AnalogError, accept_analog, suggest_analog

MAPPING = load_mapping()  # реальный json проекта


def test_suggest_analog_known_category():
    row = {
        "name": "Клапан обратный АОС 100",
        "size": "100x100",
        "unit": "шт",
        "quantity": 2,
    }
    suggestion = suggest_analog(row, MAPPING)
    assert suggestion is not None
    assert suggestion["article"] == "18-2-1"
    assert suggestion["spec_name"] == "Клапан обратный прямоугольного сечения"
    assert suggestion["spec_size"] == "100x100"
    assert suggestion["category"] == "Клапан обратный"


def test_suggest_analog_unknown_returns_none():
    row = {
        "name": "Вентилятор ВЦ 4-75",
        "size": "№4",
        "unit": "шт",
        "quantity": 1,
    }
    assert suggest_analog(row, MAPPING) is None


def test_accept_analog_builds_loaded_row():
    row = {
        "name": "Клапан обратный АОС 100",
        "size": "100x100",
        "unit": "шт",
        "quantity": 2,
    }
    suggestion = suggest_analog(row, MAPPING)
    loaded = accept_analog(row, suggestion, MAPPING)

    for key in (
        "article", "name", "size", "unit", "quantity", "params",
        "material", "material_code", "thickness",
        "connection_0", "connection_1", "connection_2", "connection_3",
        "system", "comment",
    ):
        assert key in loaded, f"нет ключа {key}"

    assert loaded["article"] == "18-2-1"
    assert loaded["name"] == suggestion["spec_name"]
    assert loaded["size"] == "100x100"
    assert loaded["unit"] == "шт"
    assert loaded["quantity"] == 2  # количество сохранено
    assert loaded["params"] == {"A0": 100.0, "B0": 100.0}
    assert loaded["material_code"] == "1"
    assert loaded["thickness"] == 0.8  # дефолт как в run_pipeline
    assert (
        loaded["connection_0"],
        loaded["connection_1"],
        loaded["connection_2"],
        loaded["connection_3"],
    ) == ("6", "6", "0", "0")  # default_connections("rectangular", "18-2-1")
    assert loaded["system"] == ""
    assert loaded["comment"] == ""

    # собранная позиция проходит через реальный конвейер json_positions
    pos = build_position(loaded)
    assert pos["article"] == "18-2-1"
    assert pos["qty"] == 2


def test_accept_analog_bad_size_raises():
    row = {
        "name": "Клапан обратный АОС",
        "size": "б/у без размеров",
        "unit": "шт",
        "quantity": 1,
    }
    suggestion = suggest_analog(row, MAPPING)
    assert suggestion is not None  # категория по ключевым словам найдётся
    with pytest.raises(AnalogError, match="размер не распознан"):
        accept_analog(row, suggestion, MAPPING)
