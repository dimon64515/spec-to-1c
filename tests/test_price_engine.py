import os
import tempfile
from decimal import Decimal

import pytest

from price_search.engine import AsyncPriceEngine
from price_search.models import PriceOffer
from price_search.sources.base import BasePriceSource, SourceRegistry
from price_search.storage import PriceStorage


class CheapSource(BasePriceSource):
    name = "cheap"

    async def search(self, query: str):
        return [PriceOffer(source=self.name, query=query, title="Cheap Клапан RVN-560", price=Decimal("100"), currency="RUB", supplier="s", url="u")]


class ExpensiveSource(BasePriceSource):
    name = "expensive"

    async def search(self, query: str):
        return [PriceOffer(source=self.name, query=query, title="Expensive Клапан RVN-560", price=Decimal("200"), currency="RUB", supplier="s", url="u")]


@pytest.mark.asyncio
async def test_engine_returns_min_price():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "test.db")
        storage = PriceStorage(db_path)
        registry = SourceRegistry()
        registry.register(CheapSource())
        registry.register(ExpensiveSource())
        engine = AsyncPriceEngine(storage, registry, min_offers=1)

        items = [{"name": "Клапан", "size": "RVN-560"}]
        results = await engine.search(items)
        assert len(results) == 1
        assert results[0].min_price == Decimal("100")
