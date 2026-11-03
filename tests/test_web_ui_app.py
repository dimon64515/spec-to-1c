"""Тесты FastAPI-приложения web_ui.app (страницы, экспорт, polling)."""

import pytest
from fastapi.testclient import TestClient

from web_ui.app import create_app

LOADED_ROW = {
    "article": "18-2-1",
    "name": "Клапан обратный прямоугольного сечения",
    "size": "100x100",
    "unit": "шт",
    "quantity": 2,
    "params": {"A0": 100.0, "B0": 100.0},
    "material": "оцинкованная",
    "material_code": "1",
    "thickness": 0.8,
    "connection_0": "6", "connection_1": "6",
    "connection_2": "0", "connection_3": "0",
    "system": "",
    "comment": "",
}


@pytest.fixture
def client(tmp_path):
    app = create_app(db_path=str(tmp_path / "t.db"))
    with TestClient(app, follow_redirects=False) as c:
        yield c


@pytest.fixture
def app_store(client):
    return client.app.state.store


def test_index_lists_requests(client, app_store):
    app_store.create_request("a.pdf")
    app_store.create_request("b.pdf")

    resp = client.get("/")

    assert resp.status_code == 200
    assert "a.pdf" in resp.text and "b.pdf" in resp.text


def test_upload_creates_request_and_redirects(client, app_store, monkeypatch):
    calls = []

    async def _fake_run_processing(store, request_id, file_name, pdf_bytes, price_engine):
        calls.append((request_id, file_name, pdf_bytes))

    monkeypatch.setattr("web_ui.app.run_processing", _fake_run_processing)

    resp = client.post(
        "/upload", files={"file": ("spec.pdf", b"%PDF-bytes", "application/pdf")},
    )

    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/r/")
    rid = int(resp.headers["location"].rstrip("/").rsplit("/", 1)[-1])
    assert app_store.get_request(rid)["file_name"] == "spec.pdf"


def test_status_endpoint_returns_json(client, app_store):
    rid = app_store.create_request("spec.pdf")
    app_store.set_status(rid, "done")

    resp = client.get(f"/api/status/{rid}")

    assert resp.status_code == 200
    assert resp.json() == {"status": "done", "error": None}


def test_request_page_renders_sections(client, app_store):
    rid = app_store.create_request("spec.pdf")
    app_store.save_positions(
        rid,
        [dict(LOADED_ROW)],
        [
            {"name": "Вентилятор", "reason": "Покупная позиция: вентилятор"},
            {"name": "Заглушка", "size": "D100", "reason": "Не найден аналог"},
        ],
    )
    app_store.set_status(rid, "done")

    resp = client.get(f"/r/{rid}")

    assert resp.status_code == 200
    assert "Загружено" in resp.text
    assert "Перекупное" in resp.text
    assert "Пропущено" in resp.text
    assert "Вентилятор" in resp.text and "Заглушка" in resp.text


def test_load_1c_endpoint_calls_runner(client, app_store, monkeypatch):
    rid = app_store.create_request("spec.pdf")
    app_store.save_positions(rid, [dict(LOADED_ROW)], [])
    app_store.set_status(rid, "done")
    calls = []

    def _fake_load_to_1c(store, request_id, header, base_comment="Web-заявка"):
        calls.append((request_id, header, base_comment))
        return {"order_number": "А-1", "errors": [], "warnings": []}

    monkeypatch.setattr("web_ui.app.load_to_1c", _fake_load_to_1c)

    resp = client.post(
        f"/r/{rid}/load-1c",
        data={
            "numberDate": "01.11.2026", "numberOrder": "123", "INN": "7701",
            "customer": "ООО Заказчик", "email": "a@b.ru",
            "phone": "+7", "contact": "Иван",
        },
    )

    assert resp.status_code == 303
    assert resp.headers["location"] == f"/r/{rid}"
    assert calls[0][0] == rid
    assert calls[0][1]["customer"] == "ООО Заказчик"
    assert calls[0][1]["numberOrder"] == "123"
    assert app_store.get_request(rid)["header"]["INN"] == "7701"


def test_xml_endpoint_returns_xml(client, app_store):
    rid = app_store.create_request("spec.pdf")
    app_store.save_positions(rid, [dict(LOADED_ROW)], [])
    app_store.save_header(rid, {"numberOrder": "А-1", "customer": "ООО Заказчик"})

    resp = client.get(f"/r/{rid}/xml")

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/xml")
    assert "<Order>" in resp.text
    assert "<numberOrder>А-1</numberOrder>" in resp.text
    assert "<article>18-2-1</article>" in resp.text


def test_error_status_shows_error_and_export_buttons(client, app_store):
    rid = app_store.create_request("spec.pdf")
    app_store.save_positions(rid, [dict(LOADED_ROW)], [])
    app_store.set_status(rid, "error", "PDF corrupted")

    resp = client.get(f"/r/{rid}")

    assert resp.status_code == 200
    assert "PDF corrupted" in resp.text
    # экспорт доступен даже при ошибке (Review Focus #5)
    assert f"/r/{rid}/xml" in resp.text
    assert f"/r/{rid}/report.xlsx" in resp.text
