"""Тесты веб-приложения предварительного расчёта (presale/app.py, jobs.py).

TestClient + tmp db, stub backend/market, sync_jobs=True (джобы исполняются
внутри запроса). Два пользователя правят одну строку — обе правки в логе,
применяется последняя.
"""
import io
import time
from decimal import Decimal
from pathlib import Path
from typing import Optional

import openpyxl
import pytest
from fastapi.testclient import TestClient

from presale.app import create_app
from presale.db import PresaleDB
from presale.jobs import JobRunner
from presale.mcp_1c import BackendUnavailableError

FIXTURES = Path(__file__).parent / "fixtures"
IGOR_XLSX = FIXTURES / "igor" / "ИИ Рыночный_расчёт_ЛСР_26-09-07.xlsx"
BORIS = (FIXTURES / "boris" / "rect.xlsx", FIXTURES / "boris" / "round.xlsx")


class StubBackend:
    """Stub PriceBackend: цены из словаря; raise_unavailable имитирует падение 1С."""

    def __init__(self, prices: Optional[dict] = None,
                 raise_unavailable: bool = False):
        self._prices = prices or {}
        self.raise_unavailable = raise_unavailable

    def search_1c(self, names):
        if self.raise_unavailable:
            raise BackendUnavailableError("1С execute_code недоступен: таймаут")
        return {n: p for n, p in self._prices.items() if n in names}


class StubMarket:
    """Stub MarketPricer: одна цена на все позиции или None."""

    def __init__(self, price: Optional[Decimal] = None):
        self.price = price

    def market_price(self, name):
        return self.price


@pytest.fixture()
def db(tmp_path):
    db = PresaleDB(tmp_path / "presale.db")
    db.create_user("igor", "secret", "Игорь")
    db.create_user("petr", "secret2", "Пётр")
    return db


@pytest.fixture()
def app(db):
    return create_app(
        db._path, BORIS, None,
        sync_jobs=True,
        backend=StubBackend({"Решетка LITENED 70-40": Decimal("3500")}),
        market=StubMarket(Decimal("120")),
    )


@pytest.fixture()
def client(app):
    return TestClient(app)


@pytest.fixture()
def igor_bytes():
    return IGOR_XLSX.read_bytes()


def login(client, login="igor", password="secret"):
    return client.post("/login", data={"login": login, "password": password},
                       follow_redirects=False)


def upload(client, igor_bytes, **fields):
    data = {"customer": "ООО Ромашка"}
    data.update(fields)
    return client.post(
        "/upload",
        files={"file": ("lsr.xlsx", igor_bytes,
                        "application/vnd.openxmlformats-officedocument"
                        ".spreadsheetml.sheet")},
        data=data,
        follow_redirects=False,
    )


# ---- авторизация ----

def test_root_redirects_to_login(client):
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/login"


def test_login_logout_flow(client):
    r = login(client)
    assert r.status_code == 303 and r.headers["location"] == "/"
    assert "presale_session" in r.headers.get("set-cookie", "")
    assert client.get("/").status_code == 200
    r = client.get("/logout", follow_redirects=False)
    assert r.status_code == 303
    assert client.get("/", follow_redirects=False).status_code == 303


def test_wrong_password_shows_error(client):
    r = client.post("/login", data={"login": "igor", "password": "bad"})
    assert r.status_code == 401
    assert "Неверный" in r.text


def test_tampered_cookie_rejected(client):
    login(client)
    client.cookies.set("presale_session", "garbage.tampered")
    assert client.get("/", follow_redirects=False).status_code == 303


# ---- загрузка → расчёт → результат ----

def test_login_upload_progress_result_flow(client, igor_bytes):
    login(client)
    r = upload(client, igor_bytes)
    assert r.status_code == 303
    est_url = r.headers["location"]
    # sync_jobs: job отработал до ответа — сразу на странице расчёта
    assert est_url == "/estimate/1"
    page = client.get(est_url)
    assert page.status_code == 200
    assert "Сводка" in page.text
    assert "🟢" in page.text or "🔴" in page.text


def test_upload_creates_estimate_with_meta(client, igor_bytes, db):
    login(client)
    upload(client, igor_bytes, customer="ООО Ромашка", tender_no="Т-77",
           deadline="2026-12-01")
    meta, items = db.get_estimate(1)
    assert meta["customer"] == "ООО Ромашка"
    assert meta["tender_no"] == "Т-77"
    assert meta["deadline"] == "2026-12-01"
    assert meta["status"] == "done"
    assert len(items) == 104


def test_upload_without_auth_redirects(client, igor_bytes):
    r = upload(client, igor_bytes)
    assert r.status_code == 303
    assert r.headers["location"] == "/login"


def test_backend_unavailable_marks_missing(app, igor_bytes, db):
    app.state.backend = StubBackend(raise_unavailable=True)
    c = TestClient(app)
    login(c)
    upload(c, igor_bytes)
    meta, items = db.get_estimate(1)
    assert meta["status"] == "done"
    missing = [i for i in items if i.status == "missing"]
    assert missing
    # без 1С всё ненайденное красное с пометкой «1С недоступна»
    assert all("1С недоступна" in i.status_note for i in missing)
    # цены рынка не пострадали — расчёт живой
    market_priced = [i for i in items if i.price_source == "market"]
    assert market_priced and all(i.internal_price is not None for i in market_priced)


def test_job_error_sets_estimate_error(app, igor_bytes, db):
    class BrokenMarket:
        def market_price(self, name):
            raise RuntimeError("price_search сломался")

    app.state.market = BrokenMarket()
    c = TestClient(app)
    login(c)
    upload(c, igor_bytes)
    meta, _ = db.get_estimate(1)
    assert meta["status"] == "error"


# ---- страница результата ----

def test_result_filter_missing(client, igor_bytes):
    login(client)
    upload(client, igor_bytes)
    page = client.get("/estimate/1?status=missing")
    assert page.status_code == 200
    assert "🔴" in page.text


def test_result_not_found(client):
    login(client)
    assert client.get("/estimate/999").status_code == 404


# ---- правки ----

def test_edit_price_recomputes_row_and_summary(client, igor_bytes, db):
    login(client)
    upload(client, igor_bytes)
    row_id = "Л40"
    r = client.post(
        f"/estimate/1/item/{row_id}/edit",
        data={"field": "internal_price", "value": "777.50"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    _, items = db.get_estimate(1)
    item = next(i for i in items if i.row_id == row_id)
    assert item.internal_price == Decimal("777.50")
    page = client.get("/estimate/1")
    assert "777.50" in page.text


def test_edit_qty_and_category(client, igor_bytes, db):
    login(client)
    upload(client, igor_bytes)
    row_id = "Л1"
    client.post(f"/estimate/1/item/{row_id}/edit",
                data={"field": "qty", "value": "3"})
    client.post(f"/estimate/1/item/{row_id}/edit",
                data={"field": "category", "value": "works"})
    _, items = db.get_estimate(1)
    item = next(i for i in items if i.row_id == row_id)
    assert item.qty == Decimal("3")
    assert item.category.value == "works"


def test_concurrent_edits_both_logged(client, igor_bytes):
    login(client)
    upload(client, igor_bytes)
    row_id = "Л40"
    client2 = TestClient(client.app)
    login(client2, "petr", "secret2")
    r1 = client.post(f"/estimate/1/item/{row_id}/edit",
                     data={"field": "internal_price", "value": "100"},
                     follow_redirects=False)
    r2 = client2.post(f"/estimate/1/item/{row_id}/edit",
                      data={"field": "internal_price", "value": "200"},
                      follow_redirects=False)
    assert r1.status_code == 303 and r2.status_code == 303
    edits = client.get(f"/estimate/1/item/{row_id}/edits")
    assert edits.status_code == 200
    assert edits.text.count("internal_price") == 2
    assert "Игорь" in edits.text and "Пётр" in edits.text
    assert "→ 100" in edits.text and "→ 200" in edits.text
    # применена последняя правка
    _, items = client.app.state.db.get_estimate(1)
    item = next(i for i in items if i.row_id == row_id)
    assert item.internal_price == Decimal("200")


def test_edit_invalid_field_rejected(client, igor_bytes):
    login(client)
    upload(client, igor_bytes)
    r = client.post("/estimate/1/item/Л40/edit",
                    data={"field": "name; DROP TABLE", "value": "x"},
                    follow_redirects=False)
    assert r.status_code == 400


# ---- выгрузки ----

def test_customer_export_endpoint(client, igor_bytes):
    login(client)
    upload(client, igor_bytes)
    r = client.get("/estimate/1/export/customer.xlsx")
    assert r.status_code == 200
    assert "spreadsheetml" in r.headers["content-type"]
    wb = openpyxl.load_workbook(io.BytesIO(r.content))
    text = " ".join(str(c.value) for ws in wb for row in ws.iter_rows()
                    for c in row)
    assert "Рыночн" not in text and "Источник" not in text


def test_internal_export_endpoint(client, igor_bytes):
    login(client)
    upload(client, igor_bytes)
    r = client.get("/estimate/1/export/internal.xlsx")
    assert r.status_code == 200
    assert len(r.content) > 1000


# ---- история ----

def test_history_lists_estimates_and_overdue(client, db, igor_bytes):
    login(client)
    upload(client, igor_bytes)
    uid = db.verify_user("igor", "secret")
    db.create_estimate(uid, {"customer": "Просроч", "deadline": "2020-01-01",
                             "status": "done"})
    db.create_estimate(uid, {"customer": "Обычный", "deadline": "2030-01-01",
                             "status": "done"})
    page = client.get("/")
    assert page.status_code == 200
    assert "Просроч" in page.text and "Обычный" in page.text
    assert 'class="overdue"' in page.text
    # просроченный выше обычного
    assert page.text.index("Просроч") < page.text.index("Обычный")


# ---- JobRunner ----

def test_job_runner_threaded_lifecycle():
    runner = JobRunner()
    job_id = runner.submit(lambda: 42)
    for _ in range(200):
        st = runner.status(job_id)
        if st["state"] in {"done", "error"}:
            break
    assert st == {"state": "done", "step": "done",
                  "error": None, "result_id": 42}


def test_job_runner_error_state():
    runner = JobRunner()

    def fail():
        raise ValueError("упс")

    job_id = runner.submit(fail)
    for _ in range(100):
        st = runner.status(job_id)
        if st["state"] == "error":
            break
        time.sleep(0.01)
    assert st["state"] == "error" and "упс" in st["error"]
    assert runner.status("нет-такого")["state"] == "missing"


def test_job_status_endpoint(client):
    login(client)
    job_id = client.app.state.runner.submit(lambda: 7)
    for _ in range(200):
        r = client.get(f"/job/{job_id}")
        data = r.json()
        if data["state"] in {"done", "error"}:
            break
    assert r.status_code == 200
    assert data["state"] == "done" and data["result_id"] == 7
