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


SERVICE_URL = "https://srv1c/base/hs/vok/order"

SERVICE_OK = {
    "Успех": True,
    "НомерЗаказа": "000000860",
    "Ошибки": [],
    "Предупреждения": ["Строка 1 (1-2-1): цена 0 — проверьте прайс"],
}

SERVICE_BUSINESS_ERRORS = {
    "Успех": False,
    "НомерЗаказа": "000000861",
    "Ошибки": ['Строка 1: не найден продукт "9-9-9"'],
    "Предупреждения": [],
}

SERVICE_500 = {
    "Успех": False,
    "НомерЗаказа": None,
    "Ошибки": ["Ошибка при вызове метода контекста (Записать)"],
    "Предупреждения": [],
}


def _capture_post(monkeypatch, resp):
    calls = {}

    def fake_post(url, content=None, json=None, headers=None, timeout=None):
        calls["url"] = url
        calls["content"] = content
        calls["json"] = json
        calls["headers"] = headers or {}
        calls["timeout"] = timeout
        return resp

    monkeypatch.setattr(httpx, "post", fake_post)
    return calls


def test_service_success_200(monkeypatch):
    calls = _capture_post(monkeypatch, _Resp(SERVICE_OK))
    tr = oc.transport_for(SERVICE_URL, api_key="secret-1")
    assert isinstance(tr, oc.HttpServiceTransport)
    out = tr.send(
        [{"article": "1-2-1"}], "Задача №42", request_id="bx1-job7", timeout=30.0
    )
    assert out["order_number"] == "000000860"
    assert out["errors"] == []
    assert out["warnings"] == ["Строка 1 (1-2-1): цена 0 — проверьте прайс"]
    # контракт запроса (ТЗ п. 3)
    assert calls["url"] == SERVICE_URL
    body = json.loads(calls["content"].decode("utf-8"))
    assert body == {
        "order_comment": "Задача №42",
        "request_id": "bx1-job7",
        "positions": [{"article": "1-2-1"}],
    }
    assert calls["json"] is None  # тело шлём через content, не через json=
    assert calls["headers"]["Content-Type"] == "application/json; charset=utf-8"
    assert calls["headers"]["X-API-Key"] == "secret-1"
    assert calls["timeout"] == 30.0


def test_service_business_errors_200_no_exception(monkeypatch):
    _capture_post(monkeypatch, _Resp(SERVICE_BUSINESS_ERRORS))
    tr = oc.HttpServiceTransport(SERVICE_URL)
    out = tr.send([{"article": "9-9-9"}], "c")
    # бизнес-ошибки — значением, не исключением (как в текущем пути)
    assert out["order_number"] == "000000861"
    assert out["errors"] == ['Строка 1: не найден продукт "9-9-9"']


def test_service_structured_500_is_result_not_exception(monkeypatch):
    # ТЗ п. 4: 500 с валидной структурой — не исключение
    _capture_post(monkeypatch, _Resp(SERVICE_500, status_code=500))
    tr = oc.HttpServiceTransport(SERVICE_URL)
    out = tr.send([{"article": "1-2-1"}], "c")
    assert out["order_number"] is None
    assert out["errors"] == ["Ошибка при вызове метода контекста (Записать)"]


def test_service_unstructured_500_raises_http_error(monkeypatch):
    _capture_post(monkeypatch, _Resp("<html>Gateway Timeout</html>", status_code=500))
    tr = oc.HttpServiceTransport(SERVICE_URL)
    with pytest.raises(httpx.HTTPError):
        tr.send([{"article": "1-2-1"}], "c")


def test_service_4xx_raises_runtime_error(monkeypatch):
    _capture_post(monkeypatch, _Resp("Unauthorized", status_code=401))
    tr = oc.HttpServiceTransport(SERVICE_URL)
    with pytest.raises(RuntimeError, match="401"):
        tr.send([{"article": "1-2-1"}], "c")


def test_service_bad_json_raises_runtime_error(monkeypatch):
    _capture_post(monkeypatch, _Resp(ValueError("no json"), status_code=200))
    tr = oc.HttpServiceTransport(SERVICE_URL)
    with pytest.raises(RuntimeError, match="битый JSON"):
        tr.send([{"article": "1-2-1"}], "c")


def test_service_timeout_raises_http_error(monkeypatch):
    def boom(url, content=None, json=None, headers=None, timeout=None):
        raise httpx.TimeoutException("read timeout")

    monkeypatch.setattr(httpx, "post", boom)
    tr = oc.HttpServiceTransport(SERVICE_URL)
    with pytest.raises(httpx.HTTPError):
        tr.send([{"article": "1-2-1"}], "c")


def test_load_order_dispatches_by_url(monkeypatch):
    # load_order — единая точка входа: auto-detect транспорта по форме URL
    calls = _capture_post(monkeypatch, _Resp(SERVICE_OK))
    out = oc.load_order(
        [{"article": "1-2-1"}], SERVICE_URL, "c",
        request_id="r-1", api_key="k",
    )
    assert out["order_number"] == "000000860"
    assert calls["headers"]["X-API-Key"] == "k"

    calls2 = _capture_post(monkeypatch, _Resp({"result": SAMPLE_1C_OK}))
    out2 = oc.load_order(
        [{"article": "1-2-1"}], "http://127.0.0.1:6005/api/execute_code", "c"
    )
    assert out2["order_number"] == "000000860"
    assert set(calls2["json"].keys()) == {"code"}
