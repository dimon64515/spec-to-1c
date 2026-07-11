from decimal import Decimal
from datetime import datetime
from price_search.models import PriceOffer, SearchResult


def test_price_offer_creation():
    offer = PriceOffer(
        source="pulscen",
        query="Клапан обратный RVN-560",
        title="Клапан обратный RVN-560",
        price=Decimal("12500"),
        currency="RUB",
        supplier="ООО ВентПрофи",
        url="https://example.com/1",
        scraped_at=datetime.now(),
    )
    assert offer.price == Decimal("12500")
    assert offer.currency == "RUB"


def test_search_result_min_price():
    offers = [
        PriceOffer(source="pulscen", query="q", title="t1", price=Decimal("100"), currency="RUB", supplier="s1", url="u1", scraped_at=datetime.now()),
        PriceOffer(source="tiu", query="q", title="t2", price=Decimal("90"), currency="RUB", supplier="s2", url="u2", scraped_at=datetime.now()),
    ]
    result = SearchResult(item_name="Клапан", item_size="RVN-560", queries=["q"], offers=offers, cached=False, created_at=datetime.now())
    assert result.min_price == Decimal("90")
    assert result.best_offer.supplier == "s2"
