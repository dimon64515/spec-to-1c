"""Тесты фолбэка HTTP-сервис → MCP execute_code в load_order_to_1c."""
import json

import httpx
import pytest

import bitrix_bot.pipeline as pl

SAMPLE_1C_OK = (
    "ЗАКАЗ 000000860 | строк=2 | ошибок=0 | предупр=0"
    " ## 1|1-2-1|цена=150|S=1.2|n=6"
)

EXECUTE_URL = "http://127.0.0.1:6005/api/execute_code"
SERVICE_URL = "https://srv1c/base/hs/Zakaz/NewZakaz"

ORDER_SERVICE = {
    "url": SERVICE_URL,
    "key": "k",
    "user": "HTTP_Service1",
    "password": "",
}


class _Resp:
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
                request=None, response=None,
            )


def _router(monkeypatch, handler):
    """httpx.post -> handler(url, **kwargs) -> _Resp | Exception(raise)."""
    calls = []

    def fake_post(url, **kwargs):
        calls.append(url)
        result = handler(url, **kwargs)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(httpx, "post", fake_post)
    return calls


def test_http_ok_no_fallback(monkeypatch):
    calls = _router(
        monkeypatch,
        lambda url, **kw: _Resp({
            "Успех": True, "НомерЗаказа": "000000860",
            "Ошибки": [], "Предупреждения": [],
        }),
    )
    out = pl.load_order_to_1c(
        [{"article": "1-2-1"}], EXECUTE_URL, "c", order_service=ORDER_SERVICE,
    )
    assert out["order_number"] == "000000860"
    assert calls == [SERVICE_URL]  # execute_code не дёргался


def test_http_business_error_no_fallback(monkeypatch):
    # Успех=false — документ в 1С УЖЕ создан; повтор через MCP = дубликат.
    calls = _router(
        monkeypatch,
        lambda url, **kw: _Resp({
            "Успех": False, "НомерЗаказа": "000000861",
            "Ошибки": ['не найден продукт "9-9-9"'], "Предупреждения": [],
        }),
    )
    out = pl.load_order_to_1c(
        [{"article": "9-9-9"}], EXECUTE_URL, "c", order_service=ORDER_SERVICE,
    )
    assert out["order_number"] == "000000861"
    assert out["errors"] == ['не найден продукт "9-9-9"']
    assert calls == [SERVICE_URL]


def test_http_down_falls_back_to_execute_code(monkeypatch):
    def handler(url, **kw):
        if "/hs/" in url:
            raise httpx.ConnectError("сервис недоступен")
        return _Resp({"result": SAMPLE_1C_OK})

    calls = _router(monkeypatch, handler)
    out = pl.load_order_to_1c(
        [{"article": "1-2-1"}], EXECUTE_URL, "c", order_service=ORDER_SERVICE,
    )
    assert out["order_number"] == "000000860"
    assert calls == [SERVICE_URL, EXECUTE_URL]


def test_http_4xx_falls_back_to_execute_code(monkeypatch):
    # детерминированный 4xx (например, платформа ещё не обновлена) —
    # фолбэк тоже пробуем: локальный MCP может быть жив
    def handler(url, **kw):
        if "/hs/" in url:
            return _Resp("Недостаточно прав", status_code=403)
        return _Resp({"result": SAMPLE_1C_OK})

    calls = _router(monkeypatch, handler)
    out = pl.load_order_to_1c(
        [{"article": "1-2-1"}], EXECUTE_URL, "c", order_service=ORDER_SERVICE,
    )
    assert out["order_number"] == "000000860"
    assert calls == [SERVICE_URL, EXECUTE_URL]


def test_both_transports_down_raises_with_context(monkeypatch):
    def handler(url, **kw):
        if "/hs/" in url:
            raise httpx.ConnectError("http down")
        raise httpx.ConnectError("mcp down")

    _router(monkeypatch, handler)
    with pytest.raises(RuntimeError, match="Оба транспорта"):
        pl.load_order_to_1c(
            [{"article": "1-2-1"}], EXECUTE_URL, "c",
            order_service=ORDER_SERVICE,
        )


def test_no_order_service_execute_code_only(monkeypatch):
    # прежнее поведение: только execute_code, без фолбэка
    calls = _router(monkeypatch, lambda url, **kw: _Resp({"result": SAMPLE_1C_OK}))
    out = pl.load_order_to_1c([{"article": "1-2-1"}], EXECUTE_URL, "c")
    assert out["order_number"] == "000000860"
    assert calls == [EXECUTE_URL]


def test_request_id_passed_to_service(monkeypatch):
    bodies = []

    def fake_post(url, content=None, json=None, headers=None, timeout=None,
                  auth=None):
        if "/hs/" in url:
            bodies.append(json_module_loads(content))
            return _Resp({
                "Успех": True, "НомерЗаказа": "1",
                "Ошибки": [], "Предупреждения": [],
            })
        raise AssertionError("execute_code не должен вызываться")

    monkeypatch.setattr(httpx, "post", fake_post)
    pl.load_order_to_1c(
        [{"article": "1-2-1"}], EXECUTE_URL, "c",
        order_service=ORDER_SERVICE, request_id="bx42-job7",
    )
    assert bodies[0]["request_id"] == "bx42-job7"


def json_module_loads(content: bytes):
    return json.loads(content.decode("utf-8"))
