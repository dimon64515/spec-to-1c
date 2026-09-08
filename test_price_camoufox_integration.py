#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Integration test for Camoufox + Google price source."""

import asyncio
import json
import os
import tempfile
from datetime import datetime

from price_search.engine import AsyncPriceEngine
from price_search.models import SearchResult
from price_search.sources.google_camoufox import GoogleCamoufoxSource
from price_search.sources.hvac import AirvekSource, UmClimatSource
from price_search.sources.base import SourceRegistry
from price_search.storage import PriceStorage


PRODUCTS = [
    {"name": "Вентилятор крышный VDNV-NT-56H-3x15-HF-У1", "size": ""},
    {"name": "Вентилятор канальный KVR 160", "size": ""},
    {"name": "Диффузор потолочный ДПУ-М 125", "size": ""},
    {"name": "Приточно-вытяжная решетка РОН-110 800x500", "size": ""},
    {"name": "Циркуляционный насос DAB A 50/180 M 505803001", "size": ""},
    {"name": "Датчик перепада давления DVL-500", "size": ""},
]

OUT_FILE = "/tmp/test_price_camoufox_integration.json"


def get_engine(db_path: str) -> AsyncPriceEngine:
    storage = PriceStorage(db_path)
    registry = SourceRegistry()
    registry.register(UmClimatSource())
    registry.register(AirvekSource())
    registry.register(GoogleCamoufoxSource())
    return AsyncPriceEngine(
        storage,
        registry,
        fallback_source=None,
        min_offers=1,
        max_age_days=7,
    )


async def main():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "price_search.db")
        engine = get_engine(db_path)

        results: list[SearchResult] = await engine.search(PRODUCTS, force_refresh=True)

        summary = []
        found_count = 0
        for item, result in zip(PRODUCTS, results):
            best = result.best_offer
            if best:
                found_count += 1
                summary.append({
                    "name": item["name"],
                    "size": item["size"],
                    "found": True,
                    "best_price": float(best.price),
                    "currency": best.currency,
                    "source": best.source,
                    "supplier": best.supplier,
                    "url": best.url,
                    "title": best.title,
                    "offers_count": len(result.offers),
                })
                print(
                    f"[OK] {item['name']}: {best.price} {best.currency} "
                    f"via {best.source} ({best.supplier}) | "
                    f"{len(result.offers)} offers | {best.url}"
                )
            else:
                summary.append({
                    "name": item["name"],
                    "size": item["size"],
                    "found": False,
                    "best_price": None,
                    "currency": None,
                    "source": None,
                    "supplier": None,
                    "url": None,
                    "title": None,
                    "offers_count": 0,
                })
                print(f"[--] {item['name']}: no offers")

        output = {
            "run_at": datetime.now().isoformat(),
            "products_total": len(PRODUCTS),
            "products_with_prices": found_count,
            "summary": summary,
            "all_results": [
                {
                    "item_name": r.item_name,
                    "item_size": r.item_size,
                    "queries": r.queries,
                    "offers": [
                        {
                            "source": o.source,
                            "title": o.title,
                            "price": float(o.price),
                            "currency": o.currency,
                            "supplier": o.supplier,
                            "url": o.url,
                        }
                        for o in r.offers
                    ],
                }
                for r in results
            ],
        }

        with open(OUT_FILE, "w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False, indent=2)

        print(f"\nFound prices for {found_count}/{len(PRODUCTS)} products")
        print(f"Results saved to {OUT_FILE}")

        if found_count < 4:
            raise SystemExit(f"FAIL: expected at least 4 products with prices, got {found_count}")


if __name__ == "__main__":
    asyncio.run(main())
