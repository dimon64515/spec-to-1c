from decimal import Decimal
from urllib.parse import urlencode, urljoin

import aiohttp
from bs4 import BeautifulSoup

from price_search.models import PriceOffer
from price_search.sources.base import BasePriceSource


class GenericHvacSource(BasePriceSource):
    """Generic HVAC source with configurable selectors."""

    name = "generic_hvac"

    def __init__(self, base_url: str, search_path: str, item_selector: str, title_selector: str, price_selector: str, supplier_selector: str | None = None):
        self.base_url = base_url.rstrip("/")
        self.search_path = search_path
        self.item_selector = item_selector
        self.title_selector = title_selector
        self.price_selector = price_selector
        self.supplier_selector = supplier_selector

    async def search(self, query: str) -> list[PriceOffer]:
        url = f"{self.base_url}{self.search_path}?{urlencode({'q': query})}"
        headers = {"User-Agent": "Mozilla/5.0"}
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                html = await resp.text()
                return self._parse_html(html, query)

    def _parse_html(self, html: str, query: str) -> list[PriceOffer]:
        soup = BeautifulSoup(html, "lxml")
        offers = []
        for item in soup.select(self.item_selector):
            title_el = item.select_one(self.title_selector)
            price_el = item.select_one(self.price_selector)
            supplier_el = item.select_one(self.supplier_selector) if self.supplier_selector else None
            if not title_el or not price_el:
                continue
            title = title_el.get_text(strip=True)
            price = self._normalize_price(price_el.get_text(strip=True))
            if price is None:
                continue
            href = title_el.get("href", "")
            url = urljoin(self.base_url, href)
            supplier = supplier_el.get_text(strip=True) if supplier_el else None
            offers.append(
                PriceOffer(
                    source=self.name,
                    query=query,
                    title=title,
                    price=price,
                    currency="RUB",
                    supplier=supplier,
                    url=url,
                )
            )
        return offers

    def _normalize_price(self, text: str):
        digits = "".join(ch for ch in text if ch.isdigit())
        if not digits:
            return None
        return Decimal(digits)
