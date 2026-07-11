from abc import ABC, abstractmethod
from typing import Optional

from price_search.models import PriceOffer


class BasePriceSource(ABC):
    name: str = ""

    @abstractmethod
    async def search(self, query: str) -> list[PriceOffer]:
        ...


class SourceRegistry:
    def __init__(self):
        self._sources: dict[str, BasePriceSource] = {}

    def register(self, source: BasePriceSource):
        self._sources[source.name] = source

    def get(self, name: str) -> Optional[BasePriceSource]:
        return self._sources.get(name)

    def names(self) -> list[str]:
        return list(self._sources.keys())

    def all(self) -> list[BasePriceSource]:
        return list(self._sources.values())
