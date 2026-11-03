"""Тесты раннера обработки web_ui.runner (PDF → позиции + дилерские цены)."""

from decimal import Decimal

import pytest

from price_search.models import PriceOffer, SearchResult
from web_ui.runner import _attach_dealer_prices, run_processing
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
