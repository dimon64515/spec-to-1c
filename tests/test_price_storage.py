import os
import tempfile
from datetime import datetime, timedelta
from decimal import Decimal

from price_search.models import PriceOffer
from price_search.storage import PriceStorage


def test_storage_save_and_cache():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "test.db")
        storage = PriceStorage(db_path)

        offer = PriceOffer(
            source="pulscen",
            query="Клапан обратный RVN-560",
            title="Клапан обратный RVN-560",
            price=Decimal("12500"),
            currency="RUB",
            supplier="s1",
            url="https://example.com",
            scraped_at=datetime.now(),
        )
        storage.save_offers("Клапан обратный", "RVN-560", [offer])

        cached = storage.get_cached_offers("Клапан обратный", "RVN-560", max_age_days=7)
        assert len(cached) == 1
        assert cached[0].price == Decimal("12500")


def test_storage_cache_expired():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "test.db")
        storage = PriceStorage(db_path)

        old = datetime.now() - timedelta(days=10)
        offer = PriceOffer(
            source="pulscen", query="q", title="t", price=Decimal("100"),
            currency="RUB", supplier="s", url="u", scraped_at=old,
        )
        storage.save_offers("Name", "Size", [offer])
        cached = storage.get_cached_offers("Name", "Size", max_age_days=7)
        assert len(cached) == 0
