"""Тесты REST-клиента Битрикс24 (httpx замокан)."""
import base64

import httpx
import pytest

from bitrix_bot.bitrix_client import BitrixClient, BitrixError


class _Resp:
    def __init__(self, status_code=200, data=None):
        self.status_code = status_code
        self._data = data or {}

    def json(self):
        return self._data


def test_call_posts_to_webhook(monkeypatch):
    calls = []

    def fake_post(url, json=None, timeout=None):
        calls.append((url, json))
        return _Resp(data={"result": {"id": 1}})

    monkeypatch.setattr(httpx, "post", fake_post)
    client = BitrixClient("https://b24/rest/1/KEY/", timeout=5)
    result = client.call("im.message.add", DIALOG_ID="task|1", MESSAGE="hi")
    assert result == {"id": 1}
    assert calls[0][0] == "https://b24/rest/1/KEY/im.message.add"
    assert calls[0][1]["MESSAGE"] == "hi"


def test_call_raises_on_rest_error(monkeypatch):
    def fake_post(url, json=None, timeout=None):
        return _Resp(data={"error": "INVALID_TOKEN", "error_description": "bad"})

    monkeypatch.setattr(httpx, "post", fake_post)
    client = BitrixClient("https://b24/rest/1/KEY/")
    with pytest.raises(BitrixError, match="INVALID_TOKEN"):
        client.call("tasks.task.get")


def test_call_raises_on_http_error(monkeypatch):
    def fake_post(url, json=None, timeout=None):
        return _Resp(status_code=500, data={})

    monkeypatch.setattr(httpx, "post", fake_post)
    client = BitrixClient("https://b24/rest/1/KEY/")
    with pytest.raises(BitrixError, match="HTTP 500"):
        client.call("im.message.add")


def test_get_task_title(monkeypatch):
    def fake_post(url, json=None, timeout=None):
        return _Resp(data={"result": {"task": {"title": "Шипиловский — ОВ2"}}})

    monkeypatch.setattr(httpx, "post", fake_post)
    assert BitrixClient("https://b24/rest/1/KEY/").get_task_title(42) == "Шипиловский — ОВ2"


def test_download_file(monkeypatch):
    class _GetResp:
        status_code = 200
        content = b"%PDF-fake"

    monkeypatch.setattr(httpx, "get", lambda url, timeout=None: _GetResp())
    data = BitrixClient("https://b24/rest/1/KEY/").download_file("https://b24/disk/download/x&auth=1")
    assert data == b"%PDF-fake"


class _StubTransport:
    """Записывает вызовы call(); download/get — заглушки."""

    def __init__(self):
        self.calls = []

    def call(self, method, **params):
        self.calls.append((method, params))
        if method == "disk.storage.getlist":
            return [{"ID": "7"}]
        if method == "disk.storage.get":
            return {"ROOT_OBJECT_ID": "99"}
        if method == "disk.folder.uploadfile":
            return {"ID": "555", "DETAIL_URL": "https://portal/disk/555"}
        if method in ("im.message.add", "imbot.message.add"):
            return {"message_id": 1}
        return {}


def test_send_file_uploads_and_attaches():
    client = BitrixClient.__new__(BitrixClient)
    client._webhook = "http://hook/"
    client._timeout = 30
    client._client_id = ""
    stub = _StubTransport()
    client.call = stub.call

    client.send_file("task|42", "report_order_839.xlsx", b"PK\x03\x04", "Заказ №839",
                     bot_id=5)

    methods = [m for m, _ in stub.calls]
    assert methods == ["disk.storage.getlist", "disk.storage.get",
                       "disk.folder.uploadfile", "imbot.message.add"]
    up = stub.calls[2][1]
    assert up["id"] == "99"
    assert up["data"] == {"NAME": "report_order_839.xlsx"}
    assert base64.b64decode(up["fileContent"]) == b"PK\x03\x04"
    msg = stub.calls[3][1]
    assert msg["MESSAGE"] == "Заказ №839"
    assert msg["ATTACH"] == [["DISK", "555"]]
    assert msg["BOT_ID"] == 5


def test_send_file_falls_back_to_link_on_attach_error():
    client = BitrixClient.__new__(BitrixClient)
    client._webhook = "http://hook/"
    client._timeout = 30
    client._client_id = ""
    stub = _StubTransport()
    client.call = stub.call

    def failing(method, **params):
        if method == "imbot.message.add":
            raise BitrixError("attach not supported")
        return stub.call(method, **params)

    client.call = failing
    sent = []
    client.send_message = lambda dialog_id, text, bot_id=None: sent.append(text)

    client.send_file("task|42", "r.xlsx", b"x", "cap", bot_id=5)

    assert sent and "https://portal/disk/555" in sent[0]
    assert sent[0].startswith("cap")
