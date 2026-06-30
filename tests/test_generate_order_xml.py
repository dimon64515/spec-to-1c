import pytest

from generate_order_xml import _eval_default_expression


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
