from datetime import datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, Field


class PriceOffer(BaseModel):
    source: str
    query: str
    title: str
    price: Decimal = Field(..., ge=0)
    currency: str = "RUB"
    supplier: Optional[str] = None
    url: str
    scraped_at: datetime = Field(default_factory=datetime.now)


class SearchResult(BaseModel):
    item_name: str
    item_size: str
    queries: list[str]
    offers: list[PriceOffer]
    cached: bool = False
    created_at: datetime = Field(default_factory=datetime.now)

    @property
    def min_price(self) -> Optional[Decimal]:
        if not self.offers:
            return None
        return min(o.price for o in self.offers)

    @property
    def best_offer(self) -> Optional[PriceOffer]:
        if not self.offers:
            return None
        return min(self.offers, key=lambda o: o.price)
