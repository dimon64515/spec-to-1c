"""Тесты пайплайна PDF → 1С (MCP execute_code замокан)."""
import httpx
import pytest

import bitrix_bot.pipeline as pl
from tools.as_order_loader.build_execute_payload import build_payload

SAMPLE_1C_OK = (
    "ЗАКАЗ 000000860 | строк=2 | ошибок=0 | предупр=1"
    " | Строка 1 (1-2-1): цена 0 — проверьте прайс"
    " ## 1|1-2-1|t=0.8|мат=Рулон оц.(08ПС)0.80|цена=0|S=1.2|n=6"
    " ## 2|4-2-3|t=0.8|мат=Рулон оц.(08ПС)0.80|цена=150|S=0.4|n=2"
)

SAMPLE_1C_ERRORS = (
    "ЗАКАЗ 000000861 | строк=2 | ошибок=1"
    " | Строка 1: не найден продукт \"9-9-9\""
    " | предупр=0"
)


class _Resp:
    def __init__(self, data):
        self.status_code = 200
        self._data = data

    def json(self):
        return self._data

    def raise_for_status(self):
        return None


def test_build_payload_order_comment_substituted():
    payload = build_payload([{"article": "1-2-1"}], order_comment="Задача №42: Шипиловский")
    assert "Задача №42: Шипиловский" in payload["code"]
    # по умолчанию — прежний комментарий
    payload_def = build_payload([{"article": "1-2-1"}])
    assert "Загрузка из JSON (execute_code, build_execute_payload)" in payload_def["code"]


def test_load_order_parses_ok(monkeypatch):
    monkeypatch.setattr(
        httpx, "post",
        lambda url, json=None, timeout=None: _Resp({"result": SAMPLE_1C_OK}),
    )
    out = pl.load_order_to_1c([{"article": "1-2-1"}], "http://x/api/execute_code", "c")
    assert out["order_number"] == "000000860"
    assert out["errors"] == []
    assert out["warnings"] == ["Строка 1 (1-2-1): цена 0 — проверьте прайс"]


def test_load_order_parses_errors(monkeypatch):
    monkeypatch.setattr(
        httpx, "post",
        lambda url, json=None, timeout=None: _Resp({"result": SAMPLE_1C_ERRORS}),
    )
    out = pl.load_order_to_1c([{"article": "9-9-9"}], "http://x/api/execute_code", "c")
    assert out["order_number"] == "000000861"
    assert out["errors"] == ['Строка 1: не найден продукт "9-9-9"']
    assert out["warnings"] == []


def test_run_pipeline_no_positions(monkeypatch):
    monkeypatch.setattr(
        pl, "process_pdf_to_positions",
        lambda b: ([], [{"name": "x", "reason": "bad"}]),
    )
    res = pl.run_pipeline(b"pdf", "f.pdf", "c", "http://x")
    assert res.order_number is None
    assert res.loaded == []
    assert len(res.skipped) == 1
    assert "не распознал" in res.raw_text


def test_run_pipeline_end_to_end_mocked(monkeypatch):
    monkeypatch.setattr(
        pl, "process_pdf_to_positions",
        lambda b: (
            [{"article": "1-2-1", "quantity": 6, "comment": "", "thickness": 0.8, "params": {}}],
            [{"name": "Клапан", "size": "200", "quantity": "1", "reason": "нет маппинга"}],
        ),
    )
    monkeypatch.setattr(
        httpx, "post",
        lambda url, json=None, timeout=None: _Resp({"result": SAMPLE_1C_OK}),
    )
    res = pl.run_pipeline(b"pdf", "spec.pdf", "Задача №42", "http://x")
    assert res.order_number == "000000860"
    assert len(res.loaded) == 1
    assert res.skipped[0]["name"] == "Клапан"
    assert res.warnings_1c == ["Строка 1 (1-2-1): цена 0 — проверьте прайс"]
