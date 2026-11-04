"""Тесты админки глобальных цен (/admin/prices): переопределения Бориса,
расценки работ, ручные цены. Правка через админку применяется к НОВОМУ
расчёту (таблица Бориса и work_rates читаются на каждый расчёт).
"""
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from presale.app import create_app
from presale.db import PresaleDB
from presale.models import Category, EstimateItem, PriceStatus

FIXTURES = Path(__file__).parent / "fixtures"
BORIS = (FIXTURES / "boris" / "rect.xlsx", FIXTURES / "boris" / "round.xlsx")


class StubBackend:
    def search_1c(self, names):
        return {}


class StubMarket:
    def market_price(self, name):
        return None


@pytest.fixture()
def app(tmp_path):
    db = PresaleDB(tmp_path / "presale.db")
    db.create_user("igor", "secret", "Игорь")
    return create_app(
        db._path, BORIS, None, sync_jobs=True,
        backend=StubBackend(), market=StubMarket(),
    )


@pytest.fixture()
def client(app):
    return TestClient(app)


def login(client):
    r = client.post("/login", data={"login": "igor", "password": "secret"},
                    follow_redirects=False)
    assert r.status_code == 303


def upload_single(app, client, item, monkeypatch):
    """Загрузка расчёта из одной строки (парсер подменён stub'ом)."""
    import presale.app as app_module

    monkeypatch.setattr(app_module, "parse_chatgpt_workbook",
                        lambda content: [item])
    r = client.post(
        "/upload",
        files={"file": ("one.xlsx", b"x",
                        "application/vnd.openxmlformats-officedocument"
                        ".spreadsheetml.sheet")},
        data={"customer": "Тест"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    _, items = app.state.db.get_estimate(1)
    return items[0]


# ---- доступ ----

def test_admin_prices_requires_login(client):
    for method, url in (("GET", "/admin/prices"),
                        ("POST", "/admin/prices/boris"),
                        ("POST", "/admin/prices/work-rates"),
                        ("POST", "/admin/prices/manual")):
        r = client.request(method, url, follow_redirects=False)
        assert r.status_code == 303, url
        assert r.headers["location"] == "/login", url


def test_admin_prices_page(client):
    login(client)
    r = client.get("/admin/prices")
    assert r.status_code == 200
    assert "Бориса" in r.text and "Расценки работ" in r.text
    assert "Ручные цены" in r.text
    # селекты заполнены из реальной таблицы Бориса
    assert "aisi304" in r.text and "0.8" in r.text


# ---- переопределения Бориса ----

def test_boris_override_applies_to_new_calculation(client, app, monkeypatch):
    login(client)
    r = client.post("/admin/prices/boris",
                    data={"material": "aisi304", "thickness": "0.8",
                          "shape": "rect", "product_kind": "standard",
                          "price": "9000"},
                    follow_redirects=False)
    assert r.status_code == 303
    item = EstimateItem(
        row_id="Т1", category=Category.MATERIALS,
        name="Воздуховод прямоугольный из AISI 304 0.8", unit="м²",
        qty=Decimal("10"), material="AISI 304", thickness=Decimal("0.8"),
        status=PriceStatus.MISSING,
    )
    priced = upload_single(app, client, item, monkeypatch)
    assert priced.internal_price == Decimal("9000")
    assert priced.price_source == "boris"
    # override лежит в БД с ключом-массивом из 4 строк
    overrides = app.state.db.get_overrides("boris")
    assert overrides['["aisi304", "0.8", "rect", "standard"]'] == Decimal("9000")


def test_boris_override_bad_cell_rejected(client):
    login(client)
    r = client.post("/admin/prices/boris",
                    data={"material": "aisi304", "thickness": "0.5",
                          "shape": "rect", "product_kind": "standard",
                          "price": "9000"},
                    follow_redirects=False)
    assert r.status_code == 400
    assert client.app.state.db.get_overrides("boris") == {}


# ---- расценки работ ----

def test_work_rate_edit_applies(client, app, monkeypatch):
    login(client)
    r = client.post("/admin/prices/work-rates",
                    data={"name": "Монтаж воздуховода", "unit": "м²",
                          "rate": "3500", "coefficient": "1.5"},
                    follow_redirects=False)
    assert r.status_code == 303
    item = EstimateItem(
        row_id="Р1", category=Category.WORKS,
        name="Монтаж воздуховода", unit="м²", qty=Decimal("5"),
        status=PriceStatus.MISSING,
    )
    priced = upload_single(app, client, item, monkeypatch)
    assert priced.internal_price == Decimal("3500") * Decimal("1.5")
    assert priced.price_source == "work_rates"


def test_work_rate_invalid_number_rejected(client):
    login(client)
    r = client.post("/admin/prices/work-rates",
                    data={"name": "Монтаж", "unit": "м²",
                          "rate": "abc", "coefficient": "1.5"},
                    follow_redirects=False)
    assert r.status_code == 400


# ---- ручные цены ----

def test_manual_price_add_applies(client, app, monkeypatch):
    login(client)
    r = client.post("/admin/prices/manual",
                    data={"name": "Клапан КВБ 600×300", "unit": "шт",
                          "price": "1200"},
                    follow_redirects=False)
    assert r.status_code == 303
    item = EstimateItem(
        row_id="О1", category=Category.EQUIPMENT,
        name="Клапан КВБ 600×300", unit="шт", qty=Decimal("2"),
        status=PriceStatus.MISSING,
    )
    priced = upload_single(app, client, item, monkeypatch)
    assert priced.internal_price == Decimal("1200")
    assert priced.price_source == "manual"


# ---- не-конечные числа (NaN/Infinity) ----

@pytest.mark.parametrize("route,data", [
    ("/admin/prices/boris",
     {"material": "aisi304", "thickness": "0.8", "shape": "rect",
      "product_kind": "standard"}),
    ("/admin/prices/work-rates",
     {"name": "Монтаж воздуховода", "unit": "м²", "coefficient": "1.5"}),
    ("/admin/prices/manual",
     {"name": "Клапан КВБ 600×300", "unit": "шт"}),
])
@pytest.mark.parametrize("bad", ["nan", "inf", "Infinity"])
def test_non_finite_price_rejected(client, app, route, data, bad):
    """NaN/±Infinity парсятся Decimal'ом без InvalidOperation — роуты 400."""
    login(client)
    payload = dict(data)
    payload["price" if route != "/admin/prices/work-rates" else "rate"] = bad
    r = client.post(route, data=payload, follow_redirects=False)
    assert r.status_code == 400
    # борис: в БД ничего не записалось
    if route == "/admin/prices/boris":
        assert app.state.db.get_overrides("boris") == {}
