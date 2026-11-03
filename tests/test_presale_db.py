"""Тесты SQLite-хранилища предварительного расчёта (presale/db.py)."""
from decimal import Decimal

import pytest

from presale.db import PresaleDB
from presale.models import Category, EstimateItem, PriceStatus


@pytest.fixture()
def db(tmp_path):
    return PresaleDB(tmp_path / "t.db")


def _item(row_id="Л1", **kwargs):
    kwargs.setdefault("category", Category.WORKS)
    kwargs.setdefault("name", "Монтаж")
    kwargs.setdefault("unit", "шт")
    kwargs.setdefault("qty", Decimal("1"))
    kwargs.setdefault("status", PriceStatus.MISSING)
    return EstimateItem(row_id=row_id, **kwargs)


def test_roundtrip_estimate_and_edits(db):
    uid = db.create_user("igor", "secret", "Игорь")
    assert db.verify_user("igor", "secret") == uid
    assert db.verify_user("igor", "wrong") is None
    eid = db.create_estimate(uid, {"title": "ЛСР 26-09-07", "customer": "ООО Ромашка"})
    db.save_items(eid, [_item()])
    db.apply_edit(eid, "Л1", "internal_price", "5000")
    db.log_edit(eid, "Л1", uid, "internal_price", None, "5000")
    meta, items = db.get_estimate(eid)
    assert items[0].internal_price == Decimal("5000")
    assert db.get_edits(eid, "Л1")[0]["new_value"] == "5000"


def test_manual_price_lookup_normalizes(db):
    db.set_manual_price("Болт  анкерный М10", Decimal("25"), "шт", 1)
    assert db.find_manual_price("болт анкерный м10") == Decimal("25")
    assert db.find_manual_price("несуществующая позиция") is None


def test_schema_idempotent(tmp_path):
    path = tmp_path / "t.db"
    PresaleDB(path)
    PresaleDB(path)  # повторное создание не падает


def test_get_estimate_meta(db):
    uid = db.create_user("igor", "secret", "Игорь")
    eid = db.create_estimate(uid, {
        "title": "ЛСР 26-09-07",
        "customer": "ООО Ромашка",
        "tender_no": "Т-1",
        "deadline": "2026-12-01",
        "source_filename": "kp.pdf",
        "status": "draft",
    })
    meta, items = db.get_estimate(eid)
    assert meta["title"] == "ЛСР 26-09-07"
    assert meta["customer"] == "ООО Ромашка"
    assert meta["tender_no"] == "Т-1"
    assert meta["owner_id"] == uid
    assert meta["status"] == "draft"
    assert items == []


def test_save_items_replaces(db):
    uid = db.create_user("u", "p", "U")
    eid = db.create_estimate(uid, {"title": "t"})
    db.save_items(eid, [_item("Л1"), _item("Л2")])
    db.save_items(eid, [_item("Л3", qty=Decimal("2.5"))])
    _, items = db.get_estimate(eid)
    assert [i.row_id for i in items] == ["Л3"]
    assert items[0].qty == Decimal("2.5")


def test_list_estimates(db):
    uid = db.create_user("u", "p", "U")
    assert db.list_estimates() == []
    db.create_estimate(uid, {"title": "А", "customer": "К1"})
    db.create_estimate(uid, {"title": "Б", "customer": "К2"})
    rows = db.list_estimates()
    assert [r["title"] for r in rows] == ["А", "Б"]


def test_verify_user_unknown_login(db):
    assert db.verify_user("nobody", "x") is None


def test_create_user_duplicate_login_raises(db):
    db.create_user("u", "p", "U")
    with pytest.raises(Exception):
        db.create_user("u", "p2", "U2")


def test_work_rates(db):
    db.upsert_work_rate(name="Монтаж воздуховодов", unit="м²", rate=Decimal("450"),
                        coefficient=Decimal("1.2"))
    db.upsert_work_rate(name="Монтаж воздуховодов", unit="м²", rate=Decimal("500"),
                        coefficient=Decimal("1.2"))
    rates = db.list_work_rates()
    assert len(rates) == 1
    assert rates[0]["rate"] == Decimal("500")
    assert rates[0]["coefficient"] == Decimal("1.2")


def test_overrides(db):
    db.set_override("boris", "ключ1", Decimal("100"), user=1)
    db.set_override("boris", "ключ1", Decimal("120"), user=1)
    db.set_override("work_rate", "ключ2", Decimal("300"), user=1)
    boris = db.get_overrides("boris")
    assert boris["ключ1"] == Decimal("120")
    assert db.get_overrides("work_rate") == {"ключ2": Decimal("300")}


def test_payload_json_roundtrip_full_item(db):
    uid = db.create_user("u", "p", "U")
    eid = db.create_estimate(uid, {"title": "t"})
    item = _item(
        market_price=Decimal("1250.50"),
        material="оцинк.",
        thickness=Decimal("0.7"),
        product_kind="труба",
        length_class="станд.",
        price_source="boris",
        status_note="округлено",
    )
    db.save_items(eid, [item])
    _, items = db.get_estimate(eid)
    assert items[0] == item
