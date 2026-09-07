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

    def send_message(self, dialog_id, text, bot_id=None):
        self.messages.append((dialog_id, text, bot_id))

    def get_dialog_messages(self, dialog_id, limit=30):
        return self.dialog_messages

    def get_task_title(self, task_id):
        return self.task_titles.get(task_id, "")

    def download_file(self, url):
        return b"%PDF-fake-bytes"


def _payload(**params):
    return {
        "event": "ONIMBOTMESSAGEADD",
        "data": {"BOT": [{"BOT_ID": "357", "BOT_CODE": "spec1c_bot"}],
                 "PARAMS": {"DIALOG_ID": "task|42", "MESSAGE_ID": "7", "FROM_USER_ID": 5,
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


def test_webhook_plain_message_goes_to_welcome(env):
    """Простое сообщение без файла и без reply → приветствие;
    старый PDF из истории диалога НЕ подхватывается."""
    cfg, client, queue = env
    client.dialog_messages = [
        {"id": 1, "params": {"FILE_URL": "https://b24/disk/download/9&auth=x",
                             "FILE_NAME": "vo2112_passport.pdf"}},
    ]
    app = srv.create_app(cfg, client=client, queue=queue, start_worker=False)
    resp = TestClient(app).post("/webhook/bot", json=_payload(),
                                headers={"X-Webhook-Token": "tok"})
    assert resp.status_code == 200
    assert queue.stats().get("pending", 0) == 0
    assert any("заказы в 1С" in m[1] for m in client.messages)  # WELCOME_TEXT
    assert not any("Принял" in m[1] for m in client.messages)


def test_webhook_reply_without_file_context_scans_history(env):
    """Reply на сообщение с файлом: если файл не извлёкся из контекста reply —
    допустим исторический fallback (только для reply, не для простых сообщений)."""
    cfg, client, queue = env
    client.dialog_messages = [
        {"id": 1, "params": {"FILE_URL": "https://b24/disk/download/1&auth=x",
                             "FILE_NAME": "ОВ2.pdf"}},
    ]
    app = srv.create_app(cfg, client=client, queue=queue, start_worker=False)
    resp = TestClient(app).post(
        "/webhook/bot",
        json=_payload(MESSAGE_REPLIED={"params": {"MESSAGE": "обработай вот это"}}),
        headers={"X-Webhook-Token": "tok"},
    )
    assert resp.status_code == 200
    assert queue.stats().get("pending", 0) == 1


def test_parse_event_marks_reply():
    ev = parse_event(_payload(MESSAGE_REPLIED={"params": {"MESSAGE": "вот файл"}}))
    assert ev.is_reply is True
    ev2 = parse_event(_payload())
    assert ev2.is_reply is False


def test_webhook_enqueues_and_acks(env):
    cfg, client, queue = env
    client.task_titles[42] = "Шипиловский — ОВ2"
    app = srv.create_app(cfg, client=client, queue=queue, start_worker=False)
    resp = TestClient(app).post(
        "/webhook/bot",
        json=_payload(
            MESSAGE_REPLIED={"params": {
                "FILE_URL": "https://b24/disk/download/1&auth=x",
                "FILE_NAME": "ОВ2.pdf",
            }}
        ),
        headers={"X-Webhook-Token": "tok"},
    )
    assert resp.status_code == 200 and resp.json()["ok"] is True
    assert queue.stats().get("pending") == 1
    job = queue.next_pending()
    assert job.task_id == 42
    assert job.file_name == "ОВ2.pdf"
    assert job.order_comment == "Задача Битрикс24 №42: Шипиловский — ОВ2"
    assert open(job.pdf_path, "rb").read() == b"%PDF-fake-bytes"
    # ack отправлен от имени бота
    assert any("Принял" in m[1] and "ОВ2.pdf" in m[1] and m[2] == 357
               for m in client.messages)


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


def test_webhook_welcome_message(env):
    cfg, client, queue = env
    app = srv.create_app(cfg, client=client, queue=queue, start_worker=False)
    resp = TestClient(app).post(
        "/webhook/bot",
        json={"event": "ONIMBOTJOINCHAT",
              "data": {"PARAMS": {"DIALOG_ID": "chat|99"}}},
        headers={"X-Webhook-Token": "tok"},
    )
    assert resp.status_code == 200 and resp.json()["ok"] is True
    assert queue.stats().get("pending", 0) == 0
    assert any("заказы в 1С" in m[1] for m in client.messages
               if m[0] == "chat|99")


def test_parse_event_direct_chat_file_in_message():
    """PDF, прикреплённый к самому сообщению (личный чат, без reply)."""
    ev = parse_event(_payload(FILE_URL="https://b24/disk/1&auth=x",
                              FILE_NAME="ОВ2.pdf"))
    assert ev is not None
    assert ev.file_url == "https://b24/disk/1&auth=x"
    assert ev.file_name == "ОВ2.pdf"


def test_parse_event_files_entry_url_download():
    """Реальная схема Битрикса: params.FILES[] с urlDownload (camelCase)."""
    ev = parse_event(_payload(FILES=[{"id": "589127", "name": "КР-0324-ОВ.pdf",
                                      "urlDownload": "https://b24/disk/589127"}]))
    assert ev is not None
    assert ev.file_url == "https://b24/disk/589127"
    assert ev.file_name == "КР-0324-ОВ.pdf"


def test_form_payload_parses_bitrix_event():
    from bitrix_bot.events import form_payload
    body = (
        "event=ONIMBOTMESSAGEADD"
        "&data[PARAMS][DIALOG_ID]=task%7C42"
        "&data[PARAMS][MESSAGE_ID]=7"
        "&data[PARAMS][FROM_USER_ID]=5"
        "&data[PARAMS][MESSAGE]=%D0%BF%D1%80%D0%B8%D0%B2%D0%B5%D1%82"
        "&data[PARAMS][FILES][0][url]=https%3A%2F%2Fb24%2Fdisk%2F1"
        "&data[PARAMS][FILES][0][name]=%D0%9E%D0%922.pdf"
        "&auth[access_token]=tok123"
    ).encode()
    payload = form_payload(body)
    assert payload["event"] == "ONIMBOTMESSAGEADD"
    assert payload["auth"]["access_token"] == "tok123"
    files = payload["data"]["PARAMS"]["FILES"]
    assert isinstance(files, list) and files[0]["name"] == "ОВ2.pdf"
    ev = parse_event(payload)
    assert ev.dialog_id == "task|42" and ev.file_name == "ОВ2.pdf"


def test_webhook_accepts_form_encoded(env):
    cfg, client, queue = env
    client.task_titles[42] = "Шипиловский — ОВ2"
    app = srv.create_app(cfg, client=client, queue=queue, start_worker=False)
    resp = TestClient(app).post(
        "/webhook/bot",
        data={
            "event": "ONIMBOTMESSAGEADD",
            "data[PARAMS][DIALOG_ID]": "task|42",
            "data[PARAMS][MESSAGE_ID]": "7",
            "data[PARAMS][FILES][0][url]": "https://b24/disk/1&auth=x",
            "data[PARAMS][FILES][0][name]": "ОВ2.pdf",
        },
        headers={"X-Webhook-Token": "tok"},
    )
    assert resp.status_code == 200 and resp.json()["ok"] is True
    assert queue.stats().get("pending") == 1
    assert any("Принял" in m[1] for m in client.messages)


def test_webhook_welcome_message_form(env):
    cfg, client, queue = env
    app = srv.create_app(cfg, client=client, queue=queue, start_worker=False)
    resp = TestClient(app).post(
        "/webhook/bot",
        data={"event": "ONIMBOTJOINCHAT",
              "data[PARAMS][DIALOG_ID]": "chat|99"},
        headers={"X-Webhook-Token": "tok"},
    )
    assert resp.status_code == 200 and resp.json()["ok"] is True
    assert any("заказы в 1С" in m[1] for m in client.messages
               if m[0] == "chat|99")


def test_webhook_routes_xlsx_to_recreate(monkeypatch):
    """xlsx-вложение доходит до очереди как файл отчёта (file_name сохраняется)."""
    import bitrix_bot.server as server_mod

    captured = {}

    def fake_recreate(content, execute_url, base_comment="", timeout=280.0):
        captured["called"] = True
        return PipelineResult(file_name="edited", order_number="840",
                              loaded=[{"article": "1-1-1", "params": {},
                                       "quantity": 2}],
                              skipped=[], errors_1c=[], warnings_1c=[])

    monkeypatch.setattr(server_mod, "recreate_order_from_report", fake_recreate)

    sent = []

    class _Client:
        def send_message(self, dialog_id, text, bot_id=None):
            sent.append(text)

        def get_task_title(self, task_id):
            return "Т"

        def download_file(self, url):
            return b"xlsx-bytes"

    from fastapi.testclient import TestClient
    cfg = BotConfig(execute_code_url="http://x", task_comment_prefix="Задача Битрикс24")
    app = server_mod.create_app(cfg=cfg, client=_Client(), start_worker=False)
    client = TestClient(app)

    payload = {
        "event": "ONIMBOTMESSAGEADD",
        "data": {
            "PARAMS": {
                "DIALOG_ID": "task|42",
                "MESSAGE_ID": "1",
                "FROM_USER_ID": "7",
                "FILES": [{"url": "http://f/report.xlsx", "name": "report_order_839.xlsx"}],
            },
            "BOT": [{"BOT_ID": "5"}],
        },
    }
    resp = client.post("/webhook/bot", json=payload)
    assert resp.status_code == 200
    # webhook вернул ok; задание ушло в очередь, воркер обработает в фоне —
    # здесь проверяем только маршрутизацию события (file_name xlsx дошёл)


def test_find_pdf_finds_xlsx_when_asked():
    """xlsx теперь легитимное вложение: find_pdf с ext='.xlsx' его находит."""
    from bitrix_bot.events import BotEvent, find_pdf

    class _Client:
        def download_file(self, url):
            return b"data"

        def get_dialog_messages(self, dialog_id, limit=30):
            return []

    ev = BotEvent(dialog_id="task|42", message_id="1", user_id=7, text="",
                  task_id=42, bot_id=5,
                  file_url="http://f/report.xlsx", file_name="report.xlsx")
    data, name = find_pdf(_Client(), ev, ext=".xlsx")
    assert data == b"data" and name == "report.xlsx"

    # тот же event с ext=.pdf не должен подхватить xlsx
    import pytest
    from bitrix_bot.events import PdfNotFound
    ev2 = BotEvent(dialog_id="task|42", message_id="1", user_id=7, text="",
                   task_id=42, bot_id=5, file_url=None, file_name=None)
    with pytest.raises(PdfNotFound):
        find_pdf(_Client(), ev2, ext=".pdf")


def test_first_file_best_effort_without_name():
    """Файл без ключа имени не отфильтровывается (best effort) — регресс-фикс:
    раньше дефолт 'document.pdf' проходил фильтр, 'document' — нет."""
    from bitrix_bot.events import _first_file

    url, name = _first_file({"FILES": [{"url": "http://f/x"}]})
    assert url == "http://f/x"
    assert name is None


def test_find_pdf_best_effort_downloads_nameless_file():
    """find_pdf с file_name=None скачивает файл и отдаёт дефолтное имя document.pdf."""
    from bitrix_bot.events import BotEvent, find_pdf

    class _Client:
        def __init__(self):
            self.urls = []

        def download_file(self, url):
            self.urls.append(url)
            return b"data"

        def get_dialog_messages(self, dialog_id, limit=30):
            return []

    ev = BotEvent(dialog_id="task|42", message_id="1", user_id=7, text="",
                  task_id=42, bot_id=5,
                  file_url="http://f/x", file_name=None)
    data, name = find_pdf(_Client(), ev, ext=".pdf")
    assert data == b"data" and name == "document.pdf"


def test_webhook_xls_goes_to_welcome_not_recreate(env, monkeypatch):
    """M-3: .xls больше не Excel-маршрут (openpyxl его не парсит): файл report.xls
    в webhook уходит по PDF-пути и, не найдясь, приводит к welcome (PdfNotFound)."""
    called = {}

    def fake_recreate(*a, **kw):
        called["recreate"] = True
        return _pipeline_result()

    monkeypatch.setattr(srv, "recreate_order_from_report", fake_recreate)
    cfg, client, queue = env
    app = srv.create_app(cfg, client=client, queue=queue, start_worker=False)
    payload = {
        "event": "ONIMBOTMESSAGEADD",
        "data": {
            "PARAMS": {
                "DIALOG_ID": "task|42",
                "MESSAGE_ID": "1",
                "FROM_USER_ID": "7",
                "FILES": [{"url": "http://f/report.xls", "name": "report.xls"}],
            },
            "BOT": [{"BOT_ID": "5"}],
        },
    }
    resp = TestClient(app).post("/webhook/bot", json=payload,
                                headers={"X-Webhook-Token": "tok"})
    assert resp.status_code == 200
    assert "recreate" not in called
    assert queue.stats().get("pending", 0) == 0
    assert any("PDF" in m[1] for m in client.messages)  # welcome вместо recreate


def test_handler_routes_xlsx_to_recreate(env, monkeypatch):
    """make_handler: xlsx-задание идёт в recreate_order_from_report, не в run_pipeline."""
    cfg, client, queue = env
    job = queue.enqueue(
        Job(dialog_id="task|42", task_id=42, pdf_path="", file_name="report_order_839.xlsx",
            order_comment="c", bot_id=5),
        pdf_bytes=b"xlsx-bytes",
    )
    called = {}

    def fake_recreate(content, execute_url, base_comment="", timeout=280.0):
        called["recreate"] = True
        return _pipeline_result()

    def fake_run_pipeline(*a, **kw):
        called["run_pipeline"] = True
        return _pipeline_result()

    monkeypatch.setattr(srv, "recreate_order_from_report", fake_recreate)
    monkeypatch.setattr(srv, "run_pipeline", fake_run_pipeline)
    handler = srv.make_handler(cfg, client)
    handler(job)
    assert called.get("recreate") is True
    assert "run_pipeline" not in called


def test_handler_broken_xlsx_sends_error_and_stops(env, monkeypatch):
    """I-4: битый/чужой .xlsx → понятное сообщение в чат, без reschedule
    (EditedReportError — детерминированная валидация, не покидает handler)."""
    from report_xlsx import EditedReportError

    def fake_recreate(content, execute_url, base_comment="", timeout=280.0):
        raise EditedReportError("Лист «Загружено» пуст — нечего пересоздавать")

    monkeypatch.setattr(srv, "recreate_order_from_report", fake_recreate)
    cfg, client, queue = env
    job = queue.enqueue(
        Job(dialog_id="task|42", task_id=42, pdf_path="",
            file_name="report_order_839.xlsx", order_comment="c", bot_id=5),
        pdf_bytes=b"not an xlsx",
    )
    handler = srv.make_handler(cfg, client)
    handler(job)  # исключение не должно покинуть обработчик
    assert any("Не смог обработать отчёт" in m[1] for m in client.messages)


def test_find_pdf_skips_non_pdf_in_history():
    from bitrix_bot.events import find_pdf

    class Client:
        def __init__(self):
            self.downloaded = []

        def get_dialog_messages(self, dialog_id, limit=30):
            return [
                {"params": {"FILE_URL": "https://b24/disk/1", "FILE_NAME": "image.png"}},
                {"params": {"FILE_URL": "https://b24/disk/2", "FILE_NAME": "ОВ2.pdf"}},
            ]

        def download_file(self, url):
            self.downloaded.append(url)
            return b"%PDF"

    client = Client()
    ev = parse_event(_payload())
    data, name = find_pdf(client, ev)
    assert name == "ОВ2.pdf"
    assert client.downloaded == ["https://b24/disk/2"]


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

    def flaky(dialog_id, text, bot_id=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("network down")
        orig(dialog_id, text, bot_id=bot_id)

    client.send_message = flaky
    handler = srv.make_handler(cfg, client)
    # сбой доставки отчёта не должен уходить наружу (иначе воркер сделает
    # reschedule и создаст дубликат заказа в 1С)
    handler(job)
    assert calls["n"] >= 1


def _enqueue_pdf(queue, handler):
    job = queue.enqueue(
        Job(dialog_id="task|42", task_id=42, pdf_path="", file_name="spec.pdf",
            order_comment="c", bot_id=5),
        pdf_bytes=b"%PDF-1.4 fake",
    )
    handler(job)
    return job


def _pipeline_result():
    return PipelineResult(
        file_name="spec.pdf", order_number="839",
        loaded=[{"article": "1-1-1", "params": {}, "quantity": 2}],
        skipped=[], errors_1c=[], warnings_1c=[],
    )


def test_handler_sends_excel_file(env, monkeypatch):
    cfg, client, queue = env
    sent_files, sent_msgs = [], []

    class _Client:
        def send_file(self, dialog_id, file_name, content, caption, bot_id=None):
            sent_files.append((file_name, caption))

        def send_message(self, dialog_id, text, bot_id=None):
            sent_msgs.append(text)

    monkeypatch.setattr(srv, "run_pipeline", lambda *a, **k: _pipeline_result())
    handler = srv.make_handler(cfg, _Client())
    _enqueue_pdf(queue, handler)

    assert len(sent_files) == 1
    assert sent_files[0][0] == "report_order_839.xlsx"
    assert "№839" in sent_files[0][1]
    assert not sent_msgs  # текстовая нарезка не отправлялась


def test_handler_falls_back_to_text_on_send_file_error(env, monkeypatch):
    cfg, client, queue = env
    sent_msgs = []

    class _Client:
        def send_file(self, *a, **k):
            raise RuntimeError("disk unavailable")

        def send_message(self, dialog_id, text, bot_id=None):
            sent_msgs.append(text)

    monkeypatch.setattr(srv, "run_pipeline", lambda *a, **k: _pipeline_result())
    handler = srv.make_handler(cfg, _Client())
    _enqueue_pdf(queue, handler)

    assert sent_msgs and "№839" in sent_msgs[0]


def test_health(env):
    cfg, client, queue = env
    app = srv.create_app(cfg, client=client, queue=queue, start_worker=False)
    resp = TestClient(app).get("/health")
    assert resp.status_code == 200 and resp.json()["ok"] is True
