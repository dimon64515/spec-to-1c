import sqlite3
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Optional

from price_search.models import PriceOffer


sqlite3.register_adapter(datetime, lambda dt: dt.isoformat())
sqlite3.register_converter("TIMESTAMP", lambda val: datetime.fromisoformat(val.decode()))


class PriceStorage:
    def __init__(self, db_path: str = "price_search.db"):
        self.db_path = Path(db_path)
        self._init_db()

    def _connection(self):
        return sqlite3.connect(self.db_path, detect_types=sqlite3.PARSE_DECLTYPES)

    def _init_db(self):
        with self._connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS price_offers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    item_name TEXT NOT NULL,
                    item_size TEXT NOT NULL,
                    query TEXT NOT NULL,
                    source TEXT NOT NULL,
                    title TEXT NOT NULL,
                    price TEXT NOT NULL,
                    currency TEXT NOT NULL,
                    supplier TEXT,
                    url TEXT NOT NULL,
                    scraped_at TIMESTAMP NOT NULL,
                    is_fallback BOOLEAN NOT NULL DEFAULT 0
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_offers_item ON price_offers(item_name, item_size, scraped_at)"
            )

    def save_offers(self, item_name: str, item_size: str, offers: list[PriceOffer], is_fallback: bool = False):
        if not offers:
            return
        with self._connection() as conn:
            for offer in offers:
                conn.execute(
                    """
                    INSERT INTO price_offers
                    (item_name, item_size, query, source, title, price, currency, supplier, url, scraped_at, is_fallback)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        item_name,
                        item_size,
                        offer.query,
                        offer.source,
                        offer.title,
                        str(offer.price),
                        offer.currency,
                        offer.supplier,
                        offer.url,
                        offer.scraped_at,
                        int(is_fallback),
                    ),
                )

    def get_cached_offers(self, item_name: str, item_size: str, max_age_days: int = 7) -> list[PriceOffer]:
        cutoff = datetime.now() - timedelta(days=max_age_days)
        with self._connection() as conn:
            rows = conn.execute(
                """
                SELECT query, source, title, price, currency, supplier, url, scraped_at, is_fallback
                FROM price_offers
                WHERE item_name = ? AND item_size = ? AND scraped_at >= ?
                ORDER BY scraped_at DESC
                """,
                (item_name, item_size, cutoff),
            ).fetchall()
        return [
            PriceOffer(
                source=row[1],
                query=row[0],
                title=row[2],
                price=Decimal(row[3]),
                currency=row[4],
                supplier=row[5],
                url=row[6],
                scraped_at=row[7],
            )
            for row in rows
        ]

    def get_history(self, item_name: str, item_size: str) -> list[PriceOffer]:
        with self._connection() as conn:
            rows = conn.execute(
                """
                SELECT query, source, title, price, currency, supplier, url, scraped_at
                FROM price_offers
                WHERE item_name = ? AND item_size = ?
                ORDER BY scraped_at DESC
                """,
                (item_name, item_size),
            ).fetchall()
        return [
            PriceOffer(
                source=row[1],
                query=row[0],
                title=row[2],
                price=Decimal(row[3]),
                currency=row[4],
                supplier=row[5],
                url=row[6],
                scraped_at=row[7],
            )
            for row in rows
        ]
