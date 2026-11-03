"""Сквозные тесты: образец Игоря end-to-end, похожие расчёты, дедлайны.

E2E гоняет реальную фикстуру Игоря через приложение (sync_jobs, stub'ы
backend/market — без сети). Критерий «< 30 с» из постановки относится к
прогону против живых 1С/price_search; в CI это недостижимо, поэтому здесь
проверяем бюджет на масштаб фикстуры: весь цикл загрузки (парсинг+цены)
< 5 с со stub'ами — реальные сетевые источники только добавляют latency.
"""
import time
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from presale.app import create_app, find_similar_estimates
from presale.chatgpt_table import parse_chatgpt_workbook
from presale.db import PresaleDB
from presale.models import Category, EstimateItem, PriceStatus

FIXTURES = Path(__file__).parent / "fixtures"
IGOR_XLSX = FIXTURES / "igor" / "ИИ Рыночный_расчёт_ЛСР_26-09-07.xlsx"
BORIS = (FIXTURES / "boris" / "rect.xlsx", FIXTURES / "boris" / "round.xlsx")

ONE_C_PRICE = Decimal("5000")


class EquipmentOnlyBackend:
    """Stub 1С: знает только оборудование (LITENED и т.п.).

    Листовые изделия в 1С не заведены — их цена только у Бориса; строки
    без цены Бориса (неизвестный материал/толщина вне сетки/не распознано)
    должны остаться 🔴.
    """

    def __init__(self, equipment_names: set):
        self._names = equipment_names

    def search_1c(self, names):
        return {n: ONE_C_PRICE for n in names if n in self._names}


class FixedMarket:
    """Stub рынка: одна цена на всё."""

    def __init__(self, price: Decimal):
        self._price = price

    def market_price(self, name):
        return self._price


@pytest.fixture()
def db(tmp_path):
    db = PresaleDB(tmp_path / "presale.db")
    db.create_user("igor", "secret", "Игорь")
    return db


@pytest.fixture()
def igor_bytes():
    return IGOR_XLSX.read_bytes()


@pytest.fixture()
def app(db, igor_bytes):
    """Приложение, где 1С знает только оборудование из фикстуры."""
    equipment_names = {
        i.name for i in parse_chatgpt_workbook(igor_bytes)
        if i.category == Category.EQUIPMENT
    }
    return create_app(
        db._path, BORIS, None,
        sync_jobs=True,
        backend=EquipmentOnlyBackend(equipment_names),
        market=FixedMarket(Decimal("120")),
    )


@pytest.fixture()
def client(app):
    return TestClient(app)


def login(client, login="igor", password="secret"):
    return client.post("/login", data={"login": login, "password": password},
                       follow_redirects=False)


def upload(client, content, **fields):
    data = {"customer": "ООО ЛСР"}
    data.update(fields)
    return client.post(
        "/upload",
        files={"file": ("lsr.xlsx", content,
                        "application/vnd.openxmlformats-officedocument"
                        ".spreadsheetml.sheet")},
        data=data,
        follow_redirects=False,
    )


# ---- E2E на реальной фикстуре Игоря ----

def test_e2e_igor_fixture_under_30s(client, igor_bytes, db):
    login(client)
    started = time.perf_counter()
    r = upload(client, igor_bytes)
    elapsed = time.perf_counter() - started
    assert r.status_code == 303 and r.headers["location"] == "/estimate/1"
    # бюджет на масштаб фикстуры: парсинг + ценообразование < 5 с со stub'ами
    assert elapsed < 5.0

    meta, items = db.get_estimate(1)
    assert meta["status"] == "done"
    assert len(items) == 104  # 32 работы / 45 материалов / 27 оборудования

    missing = [i for i in items if i.status == PriceStatus.MISSING]
    priced = [i for i in items if i.status != PriceStatus.MISSING]
    # 🔴 — только материалы без цены Бориса (неизвестный материал,
    # толщина вне сетки, материал/толщина не распознаны парсером)
    assert missing and all(i.category == Category.MATERIALS for i in missing)
    assert all(i.status_note for i in missing)
    # остальные оценены: оборудование — 1С, работы — рынок × коэффициент
    assert len(priced) == 104 - len(missing)
    assert all(i.internal_price is not None for i in priced)
    assert any(i.price_source == "1c" for i in priced)
    assert any(i.price_source == "market_works" for i in priced)
    # все строки имеют статус
    assert all(i.status is not None for i in items)


# ---- похожие расчёты ----

def test_find_similar_estimates_matches_customer_casefold(db, igor_bytes):
    uid = db.verify_user("igor", "secret")
    first = db.create_estimate(uid, {"customer": "ООО Ромашка",
                                     "status": "done"})
    # половина строк — пересечение 50%, ниже полного совпадения second
    db.save_items(first, parse_chatgpt_workbook(igor_bytes)[:52])
    items = parse_chatgpt_workbook(igor_bytes)
    # тот же заказчик другим регистром + другой заказчик без пересечений
    second = db.create_estimate(uid, {"customer": "ооо ромашка",
                                      "status": "done"})
    db.save_items(second, parse_chatgpt_workbook(igor_bytes))
    stranger = db.create_estimate(uid, {"customer": "ООО Чужой",
                                        "status": "done"})
    db.save_items(stranger, [
        EstimateItem(row_id="Х1", category=Category.WORKS,
                     name="Уникальная работа не из сметы", unit="шт",
                     qty=Decimal("1"), status=PriceStatus.MISSING),
    ])
    found = find_similar_estimates(db, items, "ООО Ромашка")
    assert first in found and second in found
    assert stranger not in found
    # релевантнее — полное пересечение имён (100%) и тот же заказчик
    assert found.index(second) < found.index(first)


def test_find_similar_estimates_matches_half_name_overlap(db):
    uid = db.verify_user("igor", "secret")
    old = db.create_estimate(uid, {"customer": "Другой заказчик",
                                   "status": "done"})
    names = [f"Позиция номер {n} общая" for n in range(4)]
    db.save_items(old, [
        EstimateItem(row_id=f"О{n}", category=Category.MATERIALS,
                     name=name, unit="шт", qty=Decimal("1"),
                     status=PriceStatus.MISSING)
        for n, name in enumerate(names)
    ])

    def item(row_id, name):
        return EstimateItem(row_id=row_id, category=Category.MATERIALS,
                            name=name, unit="шт", qty=Decimal("1"),
                            status=PriceStatus.MISSING)

    # все имена текущей сметы есть в старой — совпадение
    current = [item(f"Н{n}", name) for n, name in enumerate(names[:2])]
    assert find_similar_estimates(db, current, "Новый заказчик") == [old]
    # ровно 50% имён пересекается — ещё совпадение
    current = [item(f"Н{n}", name) for n, name in enumerate(names[:2])] \
        + [item("У1", "Уникальная позиция А"),
           item("У2", "Уникальная позиция Б")]
    assert find_similar_estimates(db, current, "Новый заказчик") == [old]
    # меньше половины имён — не похоже
    current = [item("Н0", names[0]), item("У1", "Уникальная позиция А"),
               item("У2", "Уникальная позиция Б")]
    assert find_similar_estimates(db, current, "Новый заказчик") == []


def test_similar_badge_same_customer(client, igor_bytes):
    login(client)
    upload(client, igor_bytes, customer="ООО ЛСР")
    r = upload(client, igor_bytes, customer="ООО ЛСР")
    assert r.headers["location"] == "/estimate/2"
    page = client.get("/estimate/2")
    assert page.status_code == 200
    assert "похожий расчёт от" in page.text
    assert 'href="/estimate/1"' in page.text
    # на странице первого расчёта нет бейджа (сам на себя не ссылается)
    first_page = client.get("/estimate/1")
    assert "похожий расчёт от" not in first_page.text


# ---- дедлайны ----

def test_overdue_deadline_highlighted(client, db, igor_bytes):
    login(client)
    upload(client, igor_bytes, customer="ООО ЛСР", deadline="2026-11-02")
    page = client.get("/")
    assert page.status_code == 200
    assert 'class="overdue"' in page.text
