"""Тесты рыночных цен предрасчёта (presale/market.py)."""
import asyncio
from decimal import Decimal

import pytest

from presale.market import PriceSearchMarketPricer
from price_search.models import PriceOffer, SearchResult


def _offer(price: str) -> PriceOffer:
    return PriceOffer(
        source="fake",
        query="q",
        title="t",
        price=Decimal(price),
        url="http://x",
    )


class FakeEngine:
    """Тот же интерфейс, что у AsyncPriceEngine: async search(items)."""

    def __init__(self, offers: list[PriceOffer]):
        self._offers = offers
        self.calls: list[list[dict]] = []

    async def search(self, items: list[dict]) -> list[SearchResult]:
        self.calls.append(list(items))
        return [
            SearchResult(
                item_name=item.get("name", ""),
                item_size=item.get("size", ""),
                queries=[],
                offers=self._offers,
            )
            for item in items
        ]


class BrokenEngine:
    """Async search всегда падает."""

    async def search(self, items: list[dict]) -> list[SearchResult]:
        raise RuntimeError("сеть недоступна")


@pytest.fixture()
def fake_engine():
    return FakeEngine([_offer("10"), _offer("25"), _offer("14")])


@pytest.fixture()
def empty_engine():
    return FakeEngine([])


@pytest.fixture()
def broken_engine():
    return BrokenEngine()


def test_returns_min_price(fake_engine):
    pricer = PriceSearchMarketPricer(fake_engine)
    assert pricer.market_price("болт анкерный М10") == Decimal("10")
    assert fake_engine.calls == [[{"name": "болт анкерный М10", "size": ""}]]


def test_empty_offers_returns_none(empty_engine):
    assert PriceSearchMarketPricer(empty_engine).market_price("гайка М8") is None


def test_engine_error_returns_none(broken_engine):
    assert PriceSearchMarketPricer(broken_engine).market_price("x") is None


def test_works_inside_running_loop(fake_engine):
    """Вызов из кода с уже запущенным event loop не падает (отдельный поток)."""

    async def main():
        return PriceSearchMarketPricer(fake_engine).market_price("шайба")

    assert asyncio.run(main()) == Decimal("10")
