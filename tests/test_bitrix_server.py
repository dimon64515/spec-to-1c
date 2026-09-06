"""Тесты FastAPI-сервера бота (клиент Битрикса замокан duck-typing)."""
import pytest
from fastapi.testclient import TestClient

import bitrix_bot.server as srv
from bitrix_bot.bitrix_client import BitrixError
from bitrix_bot.config import BotConfig
from bitrix_bot.events import parse_event
from bitrix_bot.pipeline import PipelineResult
from bitrix_bot.queue import Job, JobQueue


class FakeClient:
    """Замена BitrixClient: записывает сообщения, отдаёт файл/историю диалога."""

    def __init__(self):
        self.messages = []
        self.dialog_messages = []
        self.task_titles = {}

    def send_message(self, dialog_id, text):
        self.messages.append((dialog_id, text))

    def get_dialog_messages(self, dialog_id, limit=30):
        return self.dialog_messages

    def get_task_title(self, task_id):
        return self.task_titles.get(task_id, "")

    def download_file(self, url):
        return b"%PDF-fake-bytes"


def _payload(**params):
    return {
        "event": "ONIMBOTMESSAGEADD",
        "data": {"PARAMS": {"DIALOG_ID": "task|42", "MESSAGE_ID": "7", "FROM_USER_ID": 5,
                            "MESSAGE": "@Спец2Заказ загрузи", **params}},
    }


@pytest.fixture()
def env(tmp_path):
    cfg = BotConfig(
        portal="https://b24",
        incoming_webhook="https://b24/rest/1/KEY/",
        verify_token="tok",
        tmp_dir=str(tmp_path / "tmp"),
        report_limit=3500,
        execute_code_url="http://x/api/execute_code",
        request_timeout=10,
        task_comment_prefix="Задача Битрикс24",
    )
    client = FakeClient()
    queue = JobQueue(tmp_path / "jobs.db", tmp_path / "tmp")
    return cfg, client, queue


def test_parse_event_task_dialog():
    ev = parse_event(_payload())
    assert ev.dialog_id == "task|42"
    assert ev.task_id == 42
    assert ev.user_id == 5
    assert ev.file_url is None


def test_parse_event_ignores_other():
    assert parse_event({"event": "ONCRMLEADADD", "data": {}}) is None
    assert parse_event({"event": "ONIMBOTMESSAGEADD", "data": {"PARAMS": {}}}) is None


def test_webhook_rejects_bad_token(env):
    cfg, client, queue = env
    app = srv.create_app(cfg, client=client, queue=queue, start_worker=False)
    resp = TestClient(app).post("/webhook/bot", json=_payload(),
                                headers={"X-Webhook-Token": "wrong"})
    assert resp.status_code == 401


def test_webhook_enqueues_and_acks(env):
    cfg, client, queue = env
    client.dialog_messages = [
        {"id": 1, "params": {"FILE_URL": "https://b24/disk/download/1&auth=x",
                             "FILE_NAME": "ОВ2.pdf"}},
    ]
    client.task_titles[42] = "Шипиловский — ОВ2"
    app = srv.create_app(cfg, client=client, queue=queue, start_worker=False)
    resp = TestClient(app).post("/webhook/bot", json=_payload(),
                                headers={"X-Webhook-Token": "tok"})
    assert resp.status_code == 200 and resp.json()["ok"] is True
    assert queue.stats().get("pending") == 1
    job = queue.next_pending()
    assert job.task_id == 42
    assert job.file_name == "ОВ2.pdf"
    assert job.order_comment == "Задача Битрикс24 №42: Шипиловский — ОВ2"
    assert open(job.pdf_path, "rb").read() == b"%PDF-fake-bytes"
    # ack отправлен
    assert any("Принял" in m[1] and "ОВ2.pdf" in m[1] for m in client.messages)


def test_webhook_network_error_still_200(env, monkeypatch):
    cfg, client, queue = env

    def _boom(url):
        raise BitrixError("disk unavailable")

    monkeypatch.setattr(client, "download_file", _boom)
    app = srv.create_app(cfg, client=client, queue=queue, start_worker=False)
    resp = TestClient(app).post(
        "/webhook/bot",
        json=_payload(FILE_URL="https://b24/disk/download/1&auth=x"),
        headers={"X-Webhook-Token": "tok"},
    )
    assert resp.status_code == 200 and resp.json()["ok"] is True
    assert queue.stats().get("pending", 0) == 0


def test_webhook_no_pdf_asks_to_attach(env):
    cfg, client, queue = env
    app = srv.create_app(cfg, client=client, queue=queue, start_worker=False)
    resp = TestClient(app).post("/webhook/bot", json=_payload(),
                                headers={"X-Webhook-Token": "tok"})
    assert resp.status_code == 200
    assert queue.stats().get("pending", 0) == 0
    assert any("PDF" in m[1] for m in client.messages)


def test_handler_sends_report(env, monkeypatch):
    cfg, client, queue = env
    job = queue.enqueue(
        Job(dialog_id="task|42", task_id=42, pdf_path="", file_name="spec.pdf",
            order_comment="c"),
        pdf_bytes=b"%PDF",
    )
    fake = PipelineResult(
        file_name="spec.pdf", order_number="000000860",
        loaded=[{"article": "1-2-1", "quantity": 6, "comment": "", "params": {}}],
        skipped=[{"name": "Клапан", "size": "200", "quantity": "1", "reason": "нет маппинга"}],
        errors_1c=[], warnings_1c=[],
    )
    monkeypatch.setattr(srv, "run_pipeline", lambda *a, **kw: fake)
    handler = srv.make_handler(cfg, client)
    handler(job)
    texts = [m[1] for m in client.messages if m[0] == "task|42"]
    joined = "\n".join(texts)
    assert "Заказ 1С: №000000860" in joined
    assert "Загружено: 1" in joined and "Пропущено: 1" in joined
    assert "Клапан" in joined and "нет маппинга" in joined


def test_handler_delivery_error_does_not_reschedule(env, monkeypatch):
    cfg, client, queue = env
    job = queue.enqueue(
        Job(dialog_id="task|42", task_id=42, pdf_path="", file_name="spec.pdf",
            order_comment="c"),
        pdf_bytes=b"%PDF",
    )
    fake = PipelineResult(file_name="spec.pdf", order_number="000000860",
                          loaded=[{"article": "1-2-1", "quantity": 1}], skipped=[],
                          errors_1c=[], warnings_1c=[])
    monkeypatch.setattr(srv, "run_pipeline", lambda *a, **kw: fake)
    orig = client.send_message
    calls = {"n": 0}

    def flaky(dialog_id, text):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("network down")
        orig(dialog_id, text)

    client.send_message = flaky
    handler = srv.make_handler(cfg, client)
    # сбой доставки отчёта не должен уходить наружу (иначе воркер сделает
    # reschedule и создаст дубликат заказа в 1С)
    handler(job)
    assert calls["n"] >= 1


def test_health(env):
    cfg, client, queue = env
    app = srv.create_app(cfg, client=client, queue=queue, start_worker=False)
    resp = TestClient(app).get("/health")
    assert resp.status_code == 200 and resp.json()["ok"] is True
