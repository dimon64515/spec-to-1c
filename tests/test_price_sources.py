from decimal import Decimal

import pytest
from price_search.models import PriceOffer
from price_search.sources.base import BasePriceSource, SourceRegistry


class FakeSource(BasePriceSource):
    name = "fake"

    async def search(self, query: str):
        return [
            PriceOffer(
                source=self.name,
                query=query,
                title="Fake product",
                price=Decimal("100"),
                currency="RUB",
                supplier="Fake supplier",
                url="https://fake.example",
            )
        ]


@pytest.mark.asyncio
async def test_source_registry():
    registry = SourceRegistry()
    registry.register(FakeSource())
    assert "fake" in registry.names()

    source = registry.get("fake")
    offers = await source.search("test")
    assert len(offers) == 1
    assert offers[0].source == "fake"
