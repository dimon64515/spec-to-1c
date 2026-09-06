"""Тесты REST-клиента Битрикс24 (httpx замокан)."""
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
