from decimal import Decimal
from urllib.parse import urlencode, urljoin, urlparse

import aiohttp
from bs4 import BeautifulSoup

from price_search.models import PriceOffer
from price_search.sources.base import BasePriceSource


class GenericHvacSource(BasePriceSource):
    """Generic HVAC source with configurable selectors."""

    name = "generic_hvac"

    def __init__(
        self,
        base_url: str,
        search_path: str,
        item_selector: str,
        title_selector: str,
        price_selector: str,
        supplier_selector: str | None = None,
        search_param: str = "q",
        name: str | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.search_path = search_path
        self.item_selector = item_selector
        self.title_selector = title_selector
        self.price_selector = price_selector
        self.supplier_selector = supplier_selector
        self.search_param = search_param
        if name:
            self.name = name

    async def search(self, query: str) -> list[PriceOffer]:
        url = self._build_search_url(query)
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                html = await resp.text()
                return self._parse_html(html, query)

    def _build_search_url(self, query: str) -> str:
        """Build a search URL respecting existing query parameters in search_path."""
        sep = "&" if "?" in self.search_path else "?"
        return f"{self.base_url}{self.search_path}{sep}{urlencode({self.search_param: query})}"

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
            price = self._extract_price(price_el)
            if price is None:
                continue
            href = self._extract_href(title_el, item)
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

    def _extract_price(self, price_el) -> Decimal | None:
        """Try structured schema.org price first, then visible text."""
        meta_price = price_el.select_one("meta[itemprop='price']")
        if meta_price:
            value = meta_price.get("content", "").strip()
            if value:
                try:
                    return Decimal(value)
                except Exception:
                    pass
        text = price_el.get_text(strip=True)
        return self._normalize_price(text)

    def _extract_href(self, title_el, item) -> str:
        """Return the first usable link from the title element or its container."""
        if title_el.name == "a":
            return title_el.get("href", "")
        link = title_el.select_one("a")
        if link:
            return link.get("href", "")
        link = item.select_one("a[href]")
        return link.get("href", "") if link else ""

    def _normalize_price(self, text: str):
        digits = "".join(ch for ch in text if ch.isdigit())
        if not digits:
            return None
        return Decimal(digits)
