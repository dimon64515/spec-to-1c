"""Тесты транспорта загрузки заказов в 1С (order_client)."""
import json

import httpx
import pytest

import order_client as oc

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
    """Заглушка httpx.Response для monkeypatched httpx.post (стиль test_bitrix_pipeline)."""

    def __init__(self, data, status_code: int = 200):
        self.status_code = status_code
        self._data = data
        if isinstance(data, str):
            self.text = data
        elif isinstance(data, Exception):
            self.text = str(data)
        else:
            self.text = json.dumps(data, ensure_ascii=False)

    def json(self):
        if isinstance(self._data, Exception):
            raise self._data
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code} for mock url",
                request=None,
                response=None,
            )


def test_parse_result_ok():
    out = oc.parse_1c_result(SAMPLE_1C_OK)
    assert out["order_number"] == "000000860"
    assert out["errors"] == []
    assert out["warnings"] == ["Строка 1 (1-2-1): цена 0 — проверьте прайс"]
    assert out["raw"] == SAMPLE_1C_OK


def test_parse_result_errors_joined_single_segment():
    # BSL склеивает ошибки/предупреждения через СтрСоединить(..., "; ") —
    # всё сообщение приходит ОДНИМ сегментом после "ошибок=N"
    text = (
        'ЗАКАЗ 000001 | строк=2 | ошибок=2'
        ' | не найден продукт "9-9-9"; не найден материал "X"'
        ' | предупр=1 | Строка 1: цена 0 — проверьте прайс'
    )
    out = oc.parse_1c_result(text)
    assert out["order_number"] == "000001"
    assert out["errors"] == ['не найден продукт "9-9-9"', 'не найден материал "X"']
    assert out["warnings"] == ["Строка 1: цена 0 — проверьте прайс"]


def test_execute_code_transport_tunnel_envelope(monkeypatch):
    calls = {}

    def fake_post(url, json=None, timeout=None):
        calls["url"] = url
        calls["json"] = json
        calls["timeout"] = timeout
        return _Resp({"result": SAMPLE_1C_OK})

    monkeypatch.setattr(httpx, "post", fake_post)
    tr = oc.transport_for("http://127.0.0.1:6005/api/execute_code")
    assert isinstance(tr, oc.ExecuteCodeTransport)
    out = tr.send([{"article": "1-2-1"}], "Задача №42", timeout=12.0)
    assert out["order_number"] == "000000860"
    assert out["errors"] == []
    assert calls["timeout"] == 12.0
    # тело запроса — execute_code-конверт {"code": "<BSL>"}
    assert set(calls["json"].keys()) == {"code"}
    assert "Задача №42" in calls["json"]["code"]


def test_execute_code_transport_native_envelope(monkeypatch):
    # нативный HTTP-API MCPToolkit: {"success": true, "data": "..."}
    monkeypatch.setattr(
        httpx, "post",
        lambda url, json=None, timeout=None: _Resp(
            {"success": True, "data": SAMPLE_1C_OK}
        ),
    )
    tr = oc.ExecuteCodeTransport("http://127.0.0.1:6005/api/execute_code")
    out = tr.send([{"article": "1-2-1"}], "c")
    assert out["order_number"] == "000000860"
    assert out["warnings"] == ["Строка 1 (1-2-1): цена 0 — проверьте прайс"]


def test_execute_code_transport_failure_envelope(monkeypatch):
    # {"success": false, "error": "..."} — исключение с текстом 1С
    monkeypatch.setattr(
        httpx, "post",
        lambda url, json=None, timeout=None: _Resp(
            {"success": False, "error": "Ошибка компиляции"}
        ),
    )
    tr = oc.ExecuteCodeTransport("http://127.0.0.1:6005/api/execute_code")
    with pytest.raises(RuntimeError, match="Ошибка компиляции"):
        tr.send([{"article": "1-2-1"}], "c")


def test_transport_for_unknown_url_raises():
    with pytest.raises(ValueError, match="неизвестный URL"):
        oc.transport_for("https://example.com/anything")
