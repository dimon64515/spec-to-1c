from decimal import Decimal
from urllib.parse import urlencode, urljoin

import aiohttp
from bs4 import BeautifulSoup

from price_search.models import PriceOffer


class SearchEngineFallback:
    name = "search_engine"

    async def search(self, query: str, engine: str = "yandex", num_results: int = 5) -> list[PriceOffer]:
        if engine == "yandex":
            base_url = "https://yandex.ru/search/"
            params = {"text": query}
        else:
            base_url = "https://www.google.com/search"
            params = {"q": query}

        url = f"{base_url}?{urlencode(params)}"
        headers = {"User-Agent": "Mozilla/5.0"}
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                html = await resp.text()
                return self._parse_html(html, query, engine)

    def _parse_html(self, html: str, query: str, engine: str) -> list[PriceOffer]:
        soup = BeautifulSoup(html, "lxml")
        offers = []
        if engine == "yandex":
            results = soup.select(".serp-item")[:5]
        else:
            results = soup.select("div.g")[:5]

        for item in results:
            title_el = item.select_one("h3 a") or item.select_one("a")
            if not title_el:
                continue
            title = title_el.get_text(strip=True)
            href = title_el.get("href", "")
            url = urljoin("https://", href)
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
                )
            )
        return offers
