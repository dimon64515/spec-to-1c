"""Тесты sqlite-хранилища заявок web_ui.store."""

from web_ui.store import Store


def test_create_and_get_request(tmp_path):
    s = Store(str(tmp_path / "t.db"))
    rid = s.create_request("spec.pdf")
    r = s.get_request(rid)
    assert r["status"] == "processing" and r["file_name"] == "spec.pdf"
    assert r["meta"] == {}
    assert r["header"] == {}
    assert s.get_request(9999) is None


def test_save_and_list_positions_with_trading_split(tmp_path):
    s = Store(str(tmp_path / "t.db"))
    rid = s.create_request("spec.pdf")
    loaded = [
        {"name": "Воздуховод", "price": 100.0, "sum": 1000.0},
        {"name": "Отвод", "price": 50.0, "sum": 500.0},
    ]
    skipped = [
        {"name": "Клапан", "reason": "Покупная позиция: вентилятор"},
        {"name": "Заглушка", "reason": "Не найден аналог"},
    ]
    s.save_positions(rid, loaded, skipped)

    assert [p["name"] for p in s.get_positions(rid, "loaded")] == ["Воздуховод", "Отвод"]
    assert [p["name"] for p in s.get_positions(rid, "trading")] == ["Клапан"]
    assert [p["name"] for p in s.get_positions(rid, "skipped")] == ["Заглушка"]
    # все позиции без фильтра
    assert len(s.get_positions(rid)) == 4

    listings = s.list_requests()
    assert len(listings) == 1
    assert listings[0]["loaded_count"] == 2
    assert listings[0]["trading_count"] == 1
    assert listings[0]["skipped_count"] == 1
    # сумма считается по loaded+trading
    assert listings[0]["total_sum"] == 1500.0


def test_update_position_replaces_row(tmp_path):
    s = Store(str(tmp_path / "t.db"))
    rid = s.create_request("spec.pdf")
    s.save_positions(rid, [{"name": "Воздуховод", "price": 100.0}], [])
    s.update_position(rid, "loaded", 0, {"name": "Воздуховод", "price": 120.0, "accepted": True})
    rows = s.get_positions(rid, "loaded")
    assert len(rows) == 1
    assert rows[0]["price"] == 120.0
    assert rows[0]["accepted"] is True


def test_status_error_stored(tmp_path):
    s = Store(str(tmp_path / "t.db"))
    rid = s.create_request("spec.pdf")
    s.set_status(rid, "error", error="PDF не распознан")
    r = s.get_request(rid)
    assert r["status"] == "error"
    assert r["error"] == "PDF не распознан"
    s.set_status(rid, "done")
    assert s.get_request(rid)["status"] == "done"
    assert s.get_request(rid)["error"] == "PDF не распознан"


def test_list_requests_orders_newest_first(tmp_path):
    s = Store(str(tmp_path / "t.db"))
    r1 = s.create_request("a.pdf")
    s.set_order_number(r1, "З-1")
    r2 = s.create_request("b.pdf")
    r3 = s.create_request("c.pdf")
    s.save_meta(r3, {"escalation": True, "llm_recovered": 2})
    s.save_header(r3, {"customer": "ООО Ромашка"})

    listings = s.list_requests()
    assert [l["id"] for l in listings] == [r3, r2, r1]
    assert listings[0]["file_name"] == "c.pdf"
    assert s.get_request(r3)["meta"] == {"escalation": True, "llm_recovered": 2}
    assert s.get_request(r3)["header"] == {"customer": "ООО Ромашка"}
    assert s.get_request(r1)["order_number"] == "З-1"
