import asyncio
from decimal import Decimal
from urllib.parse import urlencode, urljoin

import aiohttp
from bs4 import BeautifulSoup

from price_search.models import PriceOffer
from price_search.sources.base import BasePriceSource


class PulscenSource(BasePriceSource):
    name = "pulscen"
    base_url = "https://pulscen.ru"

    async def search(self, query: str) -> list[PriceOffer]:
        url = f"{self.base_url}/search?{urlencode({'q': query})}"
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                html = await resp.text()
                return self._parse_html(html, query)

    def _parse_html(self, html: str, query: str) -> list[PriceOffer]:
        soup = BeautifulSoup(html, "lxml")
        offers = []
        for item in soup.select(".catalog-item"):
            title_el = item.select_one(".catalog-item__name")
            price_el = item.select_one(".catalog-item__price")
            company_el = item.select_one(".catalog-item__company")
            if not title_el or not price_el:
                continue
            title = title_el.get_text(strip=True)
            price = self._normalize_price(price_el.get_text(strip=True))
            if price is None:
                continue
            href = title_el.get("href", "")
            url = urljoin(self.base_url, href)
            supplier = company_el.get_text(strip=True) if company_el else None
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
