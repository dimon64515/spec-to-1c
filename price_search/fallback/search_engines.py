from decimal import Decimal
from urllib.parse import urlencode, urljoin

import aiohttp
from bs4 import BeautifulSoup

from price_search.models import PriceOffer
from price_search.sources.base import BasePriceSource


class SearchEngineFallback(BasePriceSource):
    name = "search_engine"

    _YANDEX_BASE = "https://yandex.ru/search/"
    _GOOGLE_BASE = "https://www.google.com/search"

    async def search(self, query: str, engine: str = "yandex", num_results: int = 5) -> list[PriceOffer]:
        if engine == "yandex":
            base_url = self._YANDEX_BASE
            params = {"text": query}
        else:
            base_url = self._GOOGLE_BASE
            params = {"q": query}

        url = f"{base_url}?{urlencode(params)}"
        headers = {"User-Agent": "Mozilla/5.0"}
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                html = await resp.text()
                return self._parse_html(html, query, engine, base_url)

    def _parse_html(self, html: str, query: str, engine: str, base_url: str | None = None) -> list[PriceOffer]:
        soup = BeautifulSoup(html, "lxml")
        offers = []
        if engine == "yandex":
            results = soup.select(".serp-item")[:5]
        else:
            results = soup.select("div.g")[:5]

        if base_url is None:
            base_url = self._YANDEX_BASE if engine == "yandex" else self._GOOGLE_BASE

        for item in results:
            title_el = item.select_one("h3 a") or item.select_one("a")
            if not title_el:
                continue
            title = title_el.get_text(strip=True)
            href = title_el.get("href", "")
            url = urljoin(base_url, href)
            # Цену из поисковой выдачи достоверно не извлечь, поэтому оффер без цены не создаём
            # или ставим 0 как маркер.
            offers.append(
                PriceOffer(
                    source=f"{engine}_search",
                    query=query,
                    title=title,
                    price=Decimal("0"),
                    currency="RUB",
                    supplier=None,
                    url=url,
                    is_fallback=True,
                )
            )
        return offers
