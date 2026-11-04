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


def test_resolve_download_url_prefers_im_for_chat_files():
    """Файлы чужих пользователей через disk.file.get недоступны (403);
    бот берёт их через im.disk.file.get."""
    client = BitrixClient.__new__(BitrixClient)
    calls = []

    def fake_call(method, **params):
        calls.append(method)
        if method == "im.disk.file.get":
            return {"DOWNLOAD_URL": "https://b24/signed/1"}
        raise AssertionError(f"unexpected {method}")

    client.call = fake_call
    assert client.resolve_download_url(123) == "https://b24/signed/1"
    assert calls == ["im.disk.file.get"]


def test_resolve_download_url_falls_back_to_disk(monkeypatch):
    """im.disk.file.get отказал (нет scope im/старое ядро) — disk.file.get."""
    def fake_post(url, json=None, timeout=None):
        if "im.disk.file.get" in url:
            return _Resp(data={"error": "METHOD_NOT_FOUND",
                               "error_description": "no such method"})
        return _Resp(data={"result": {"DOWNLOAD_URL": "https://b24/signed/2"}})

    monkeypatch.setattr(httpx, "post", fake_post)
    client = BitrixClient("https://b24/rest/1/KEY/")
    assert client.resolve_download_url(123) == "https://b24/signed/2"


def test_resolve_download_url_raises_if_both_fail(monkeypatch):
    def fake_post(url, json=None, timeout=None):
        if "im.disk.file.get" in url:
            return _Resp(data={"error": "METHOD_NOT_FOUND"})
        return _Resp(status_code=403, data={})

    monkeypatch.setattr(httpx, "post", fake_post)
    client = BitrixClient("https://b24/rest/1/KEY/")
    with pytest.raises(BitrixError, match="HTTP 403"):
        client.resolve_download_url(123)

def test_resolve_download_url_uses_bot_token_for_chat_file():
    """Файлы чужих чатов недоступны вебхуку — URL берём от имени бота
    через imbot.v2.File.download (botToken = client_id приложения)."""
    calls = []

    def fake_call(method, **params):
        calls.append((method, params))
        if method == "imbot.v2.File.download":
            return {"downloadUrl": "https://b24/signed-chat"}
        raise AssertionError(method)

    client = BitrixClient.__new__(BitrixClient)
    client._client_id = "cid"
    client.call = fake_call
    url = client.resolve_download_url(604899, bot_id=357)
    assert url == "https://b24/signed-chat"
    assert calls[0][0] == "imbot.v2.File.download"
    assert calls[0][1] == {"botId": 357, "botToken": "cid", "fileId": 604899}


def test_resolve_download_url_bot_method_failure_falls_back():
    """imbot.v2.File.download недоступен — фолбэк на disk.file.get."""
    def fake_call(method, **params):
        if method == "imbot.v2.File.download":
            raise BitrixError("BOT_OWNERSHIP_ERROR")
        if method == "im.disk.file.get":
            raise BitrixError("METHOD_NOT_FOUND")
        return {"DOWNLOAD_URL": "https://b24/signed-disk"}

    client = BitrixClient.__new__(BitrixClient)
    client._client_id = "cid"
    client.call = fake_call
    assert client.resolve_download_url(1, bot_id=9) == "https://b24/signed-disk"
