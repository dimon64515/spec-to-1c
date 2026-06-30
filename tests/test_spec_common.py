import pytest
from spec_common import material_to_code, connection_to_code


def test_material_to_code_galvanized():
    assert material_to_code("оцинкованная") == "1"


def test_material_to_code_stainless():
    assert material_to_code("нержавеющая") == "2"


def test_material_to_code_black_steel():
    assert material_to_code("чёрная") == "3"


def test_material_to_code_direct_digit():
    assert material_to_code("2") == "2"


def test_material_to_code_unknown_defaults_to_galvanized():
    assert material_to_code("дерево") == "1"


def test_material_to_code_none():
    assert material_to_code(None) == "1"


def test_connection_to_code_numeric():
    assert connection_to_code("1", "round") == "1"


def test_connection_to_code_empty():
    assert connection_to_code("", "round") == "0"


def test_connection_to_code_round_flange():
    assert connection_to_code("фланец", "round") == "4"


def test_connection_to_code_rect_flange():
    assert connection_to_code("фланец", "rectangular") == "5"


def test_connection_to_code_rect_rail():
    assert connection_to_code("рейка", "rectangular") == "7"
