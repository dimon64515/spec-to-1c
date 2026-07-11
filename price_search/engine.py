import asyncio
from decimal import Decimal
from typing import Optional

from price_search.models import PriceOffer, SearchResult
from price_search.sources.base import BasePriceSource, SourceRegistry
from price_search.storage import PriceStorage


class AsyncPriceEngine:
    def __init__(
        self,
        storage: PriceStorage,
        source_registry: SourceRegistry,
        fallback_source: Optional[BasePriceSource] = None,
        min_offers: int = 3,
        max_age_days: int = 7,
    ):
        self.storage = storage
        self.registry = source_registry
        self.fallback_source = fallback_source
        self.min_offers = min_offers
        self.max_age_days = max_age_days

    async def search(self, items: list[dict], force_refresh: bool = False) -> list[SearchResult]:
        tasks = [self._search_one(item, force_refresh) for item in items]
        return await asyncio.gather(*tasks)

    async def _search_one(self, item: dict, force_refresh: bool) -> SearchResult:
        name = item.get("name", "")
        size = item.get("size", "")

        if not force_refresh:
            cached = self.storage.get_cached_offers(name, size, self.max_age_days)
            if len(cached) >= self.min_offers:
                return SearchResult(item_name=name, item_size=size, queries=[], offers=cached, cached=True)

        queries = self._build_queries(item)
        all_offers: list[PriceOffer] = []

        for query in queries:
            source_tasks = [self._run_source(source, query) for source in self.registry.all()]
            results = await asyncio.gather(*source_tasks, return_exceptions=True)
            for offers in results:
                if isinstance(offers, Exception):
                    continue
                all_offers.extend(self._filter_relevant(offers, query))

            if len(all_offers) >= self.min_offers:
                break

        # Fallback
        if len(all_offers) < self.min_offers and self.fallback_source:
            for query in queries:
                try:
                    fallback_offers = await self.fallback_source.search(query)
                    all_offers.extend(self._filter_relevant(fallback_offers, query))
                except Exception:
                    continue
                if len(all_offers) >= self.min_offers:
                    break

        all_offers.sort(key=lambda o: o.price)
        top_offers = all_offers[:3]

        self.storage.save_offers(name, size, top_offers, is_fallback=False)

        return SearchResult(
            item_name=name,
            item_size=size,
            queries=queries,
            offers=top_offers,
            cached=False,
        )

    async def _run_source(self, source: BasePriceSource, query: str) -> list[PriceOffer]:
        try:
            return await source.search(query)
        except Exception:
            return []

    def _build_queries(self, item: dict) -> list[str]:
        name = item.get("name", "").strip()
        size = item.get("size", "").strip()
        brand = item.get("brand", "").strip()
        queries = []
        if name and size:
            queries.append(f"{name} {size}")
        if name and brand and size:
            queries.append(f"{name} {brand} {size}")
        if name:
            queries.append(name)
        return queries

    def _filter_relevant(self, offers: list[PriceOffer], query: str) -> list[PriceOffer]:
        keywords = [w.lower() for w in query.split() if len(w) > 2]
        relevant = []
        for offer in offers:
            title_lower = offer.title.lower()
            if all(kw in title_lower for kw in keywords):
                relevant.append(offer)
        return relevant
