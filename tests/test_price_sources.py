from decimal import Decimal

import pytest
from price_search.models import PriceOffer
from price_search.sources.aggregators.blizko import BlizkoSource
from price_search.sources.aggregators.pulscen import PulscenSource
from price_search.sources.aggregators.tiu import TiuSource
from price_search.sources.base import BasePriceSource, SourceRegistry
from price_search.sources.hvac.generic import GenericHvacSource
from price_search.fallback.search_engines import SearchEngineFallback


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


PULSCEN_HTML = """
<html>
<body>
<div class="catalog-item">
    <a class="catalog-item__name" href="/product/1">Клапан обратный RVN-560</a>
    <span class="catalog-item__price">12 500 ₽</span>
    <span class="catalog-item__company">ООО ВентПрофи</span>
</div>
<div class="catalog-item">
    <a class="catalog-item__name" href="/product/2">Клапан обратный RVN-560</a>
    <span class="catalog-item__price">13 000 ₽</span>
    <span class="catalog-item__company">ООО Аэрос</span>
</div>
</body>
</html>
"""

TIU_HTML = """
<html>
<body>
<div class="product-card">
    <a class="product-card__title" href="/product/1">Клапан обратный RVN-560</a>
    <span class="product-card__price">12 500 ₽</span>
    <span class="product-card__company">ООО ВентПрофи</span>
</div>
<div class="product-card">
    <a class="product-card__title" href="/product/2">Клапан обратный RVN-560</a>
    <span class="product-card__price">13 000 ₽</span>
    <span class="product-card__company">ООО Аэрос</span>
</div>
</body>
</html>
"""

BLIZKO_HTML = """
<html>
<body>
<div class="product-item">
    <a class="title" href="/product/1">Клапан обратный RVN-560</a>
    <span class="price">12 500 ₽</span>
    <span class="company">ООО ВентПрофи</span>
</div>
<div class="product-item">
    <a class="title" href="/product/2">Клапан обратный RVN-560</a>
    <span class="price">13 000 ₽</span>
    <span class="company">ООО Аэрос</span>
</div>
</body>
</html>
"""


def test_pulscen_parse_html():
    source = PulscenSource()
    offers = source._parse_html(PULSCEN_HTML, "Клапан обратный RVN-560")
    assert len(offers) == 2
    assert offers[0].price == Decimal("12500")
    assert offers[0].supplier == "ООО ВентПрофи"


def test_tiu_parse_html():
    source = TiuSource()
    offers = source._parse_html(TIU_HTML, "Клапан обратный RVN-560")
    assert len(offers) == 2
    assert offers[0].price == Decimal("12500")
    assert offers[0].supplier == "ООО ВентПрофи"


def test_blizko_parse_html():
    source = BlizkoSource()
    offers = source._parse_html(BLIZKO_HTML, "Клапан обратный RVN-560")
    assert len(offers) == 2
    assert offers[0].price == Decimal("12500")
    assert offers[0].supplier == "ООО ВентПрофи"


def test_generic_hvac_parse():
    html = """
    <html><body>
    <div class="product">
        <a class="title" href="/p/1">Клапан обратный RVN-560</a>
        <span class="price">12 500 ₽</span>
        <span class="company">ООО ВентПрофи</span>
    </div>
    </body></html>
    """
    source = GenericHvacSource(
        base_url="https://example.com",
        search_path="/search",
        item_selector=".product",
        title_selector=".title",
        price_selector=".price",
        supplier_selector=".company",
    )
    offers = source._parse_html(html, "query")
    assert len(offers) == 1
    assert offers[0].price == Decimal("12500")


def test_search_engine_fallback_parse():
    html = """
    <html><body>
    <div class="serp-item">
        <h3><a href="https://example.com/1">Клапан обратный RVN-560 — цена 12500</a></h3>
    </div>
    </body></html>
    """
    fallback = SearchEngineFallback()
    offers = fallback._parse_html(html, "query", "yandex")
    assert len(offers) == 1
    assert offers[0].source == "yandex_search"
