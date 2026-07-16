import pytest

from generate_order_xml import _eval_default_expression, build_characteristic


def test_eval_simple_reference():
    assert _eval_default_expression("D0", {"D0": 160}) == 160


def test_eval_max_expression():
    assert _eval_default_expression("max(A0, B0) / 2", {"A0": 300, "B0": 200}) == 150


def test_eval_arithmetic():
    assert _eval_default_expression("A0 + B0 * 2", {"A0": 100, "B0": 50}) == 200


def test_eval_forbidden_name_raises():
    with pytest.raises(ValueError):
        _eval_default_expression("__import__('os')", {})


def test_eval_forbidden_attribute_raises():
    with pytest.raises(ValueError):
        _eval_default_expression("().__class__", {})


def test_eval_forbidden_subscript_raises():
    with pytest.raises(ValueError):
        _eval_default_expression("params['x']", {"params": {"x": 1}})


def test_force_params_override_input():
    mapping = {
        "article": "8-2-1",
        "xml_parameters": ["A0", "B0", "L0"],
        "default_params": {"L0": 100},
        "force_params": {"L0": 100},
    }
    # Даже если исходные данные говорят L0=150, 1С оставит 100
    characteristic = build_characteristic({"A0": 400, "B0": 200, "L0": 150}, mapping)
    assert "L00100L0_" in characteristic
    assert "L00150L0_" not in characteristic


def test_a2_formatted_with_leading_zeros():
    mapping = {
        "article": "15-2-4",
        "xml_parameters": ["B0", "L0", "A2"],
        "default_params": {},
    }
    characteristic = build_characteristic({"B0": 500, "L0": 1100, "A2": 200}, mapping)
    assert "B00500B0_" in characteristic
    assert "L01100L0_" in characteristic
    assert "A20200A2_" in characteristic
