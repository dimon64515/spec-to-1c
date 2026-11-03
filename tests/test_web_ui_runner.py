"""Тесты раннера обработки web_ui.runner (PDF → позиции + дилерские цены)."""

from decimal import Decimal

import pytest

from price_search.models import PriceOffer, SearchResult
from web_ui.runner import _attach_dealer_prices, apply_analog, load_to_1c, run_processing
from web_ui.store import Store


def _offer(price: str, url: str = "http://x/1", supplier: str = "Поставщик") -> PriceOffer:
    return PriceOffer(
        source="stub", query="q", title="t", price=Decimal(price),
        supplier=supplier, url=url,
    )


def _result(offers: list, name: str = "Клапан") -> SearchResult:
    return SearchResult(item_name=name, item_size="D100", queries=["q"], offers=offers)


class EngineStub:
    """Заглушка AsyncPriceEngine: запоминает items, возвращает заданные результаты."""

    def __init__(self, results):
        self._results = results
        self.items = None

    async def search(self, items):
        self.items = items
        return self._results


@pytest.mark.anyio
async def test_run_processing_saves_positions(monkeypatch, tmp_path):
    loaded = [{"name": "Воздуховод", "size": "100x100", "quantity": 2, "sum": 1000.0}]
    skipped = [
        {"name": "Клапан обратный АОС 100", "reason": "Покупная позиция: клапан"},
        {"name": "Заглушка", "reason": "Не найден аналог"},
    ]
    meta = {"recovered": 1}

    monkeypatch.setattr(
        "web_ui.runner.pipeline.process_pdf_to_positions",
        lambda pdf_bytes: (loaded, skipped, meta),
    )
    engine = EngineStub([_result([_offer("1234.56")])])
    store = Store(str(tmp_path / "t.db"))
    rid = store.create_request("spec.pdf")

    await run_processing(store, rid, "spec.pdf", b"%PDF", engine)

    assert store.get_request(rid)["status"] == "done"
    assert store.get_positions(rid, "loaded") == loaded
    assert [p["name"] for p in store.get_positions(rid, "skipped")] == ["Заглушка"]
    trading = store.get_positions(rid, "trading")
    assert [p["name"] for p in trading] == ["Клапан обратный АОС 100"]
    # дилерская цена прикреплена к trading-строке
    assert trading[0]["price_info"] == {
        "price": "1234.56", "url": "http://x/1", "supplier": "Поставщик",
    }
    # поиск вызывался только по trading-строкам
    assert engine.items == [{"name": "Клапан обратный АОС 100", "size": ""}]
    assert store.get_request(rid)["meta"] == {"recovered": 1}


@pytest.mark.anyio
async def test_run_processing_empty_success_is_done_with_escalation(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "web_ui.runner.pipeline.process_pdf_to_positions",
        lambda pdf_bytes: ([], [], {"escalation": "пустой документ"}),
    )
    store = Store(str(tmp_path / "t.db"))
    rid = store.create_request("spec.pdf")

    await run_processing(store, rid, "spec.pdf", b"%PDF", None)

    req = store.get_request(rid)
    assert req["status"] == "done"
    assert req["meta"]["escalation"] == "пустой документ"
    assert store.get_positions(rid) == []


@pytest.mark.anyio
async def test_run_processing_exception_sets_error_status(monkeypatch, tmp_path):
    def _boom(pdf_bytes):
        raise RuntimeError("PDF corrupted")

    monkeypatch.setattr("web_ui.runner.pipeline.process_pdf_to_positions", _boom)
    store = Store(str(tmp_path / "t.db"))
    rid = store.create_request("spec.pdf")

    await run_processing(store, rid, "spec.pdf", b"%PDF", None)

    req = store.get_request(rid)
    assert req["status"] == "error"
    assert "PDF corrupted" in req["error"]


def test_trading_row_without_offers_has_none_price():
    trading = [{"name": "Вентилятор", "reason": "Покупная позиция: вентилятор"}]
    _attach_dealer_prices(trading, [_result([])])
    assert trading[0]["price_info"] == {"price": None, "url": None, "supplier": None}


# --- Task 4: загрузка в 1С и применение аналогов ---

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


def _store_with_loaded(tmp_path):
    store = Store(str(tmp_path / "t.db"))
    rid = store.create_request("spec.pdf")
    store.save_positions(rid, [dict(LOADED_ROW)], [])
    store.set_status(rid, "done")
    return store, rid


def test_load_to_1c_merges_prices_and_sets_status(monkeypatch, tmp_path):
    from bitrix_bot.config import BotConfig

    monkeypatch.setattr(
        "web_ui.runner.load_bot_config", lambda: BotConfig(execute_code_url="http://x"),
    )
    calls = {}

    def _stub_load(positions, execute_url, order_comment, timeout=280.0,
                   order_service=None, request_id=None):
        calls.update(
            positions=positions, execute_url=execute_url,
            order_comment=order_comment, order_service=order_service,
            request_id=request_id,
        )
        return {
            "order_number": "А-1", "errors": [], "warnings": [],
            "raw": "", "prices": [{"row": 1, "article": "18-2-1", "price": 100.0}],
        }

    monkeypatch.setattr("web_ui.runner.pipeline.load_order_to_1c", _stub_load)
    store, rid = _store_with_loaded(tmp_path)

    result = load_to_1c(store, rid, {})

    assert result["order_number"] == "А-1"
    assert result["errors"] == []
    assert calls["execute_url"] == "http://x"
    assert calls["order_comment"] == "Web-заявка"
    assert calls["order_service"] is None  # order_service_url пустой
    assert calls["positions"][0]["article"] == "18-2-1"

    req = store.get_request(rid)
    assert req["status"] == "loaded_1c"
    assert req["order_number"] == "А-1"
    loaded = store.get_positions(rid, "loaded")
    assert loaded[0]["price"] == 100.0  # цена из 1С смержена в строку
    assert loaded[0]["sum"] == 200.0


def test_load_to_1c_failure_keeps_done_and_records_error(monkeypatch, tmp_path):
    from bitrix_bot.config import BotConfig

    monkeypatch.setattr(
        "web_ui.runner.load_bot_config", lambda: BotConfig(execute_code_url="http://x"),
    )

    def _boom(*args, **kwargs):
        raise RuntimeError("1С недоступна")

    monkeypatch.setattr("web_ui.runner.pipeline.load_order_to_1c", _boom)
    store, rid = _store_with_loaded(tmp_path)

    result = load_to_1c(store, rid, {})

    assert result["order_number"] is None
    assert "1С недоступна" in result["errors"]
    req = store.get_request(rid)
    assert req["status"] == "done"  # не loaded_1c, повтор возможен
    assert "1С недоступна" in req["meta"]["load_error"]
    assert store.get_positions(rid, "loaded")[0].get("price") is None


def test_load_to_1c_refused_when_already_loaded(tmp_path):
    store, rid = _store_with_loaded(tmp_path)
    store.set_status(rid, "loaded_1c")
    store.set_order_number(rid, "А-1")

    with pytest.raises(ValueError, match="loaded_1c"):
        load_to_1c(store, rid, {})


def test_apply_analog_moves_row_to_loaded(tmp_path):
    store = Store(str(tmp_path / "t.db"))
    rid = store.create_request("spec.pdf")
    skipped = [
        {"name": "Клапан обратный АОС 100", "size": "100x100",
         "unit": "шт", "quantity": 2, "reason": "Не найден аналог",
         "not_trading": True},
        {"name": "Заглушка", "size": "D100",
         "unit": "шт", "quantity": 1, "reason": "Не найден аналог"},
    ]
    store.save_positions(rid, [], skipped)

    position = apply_analog(store, rid, 0)

    assert position["article"] == "18-2-1"
    assert position["quantity"] == 2
    assert store.get_positions(rid, "loaded") == [position]
    rest = store.get_positions(rid, "skipped")
    assert [p["name"] for p in rest] == ["Заглушка"]  # индексы уплотнены


def test_apply_analog_bad_size_marks_row(tmp_path):
    store = Store(str(tmp_path / "t.db"))
    rid = store.create_request("spec.pdf")
    skipped = [
        {"name": "Клапан обратный АОС", "size": "б/у без размеров",
         "unit": "шт", "quantity": 1, "reason": "Не найден аналог",
         "not_trading": True},
    ]
    store.save_positions(rid, [], skipped)

    row = apply_analog(store, rid, 0)

    assert row["analog_error"] == "размер не распознан"
    assert store.get_positions(rid, "skipped")[0]["analog_error"] == "размер не распознан"
    assert store.get_positions(rid, "loaded") == []
