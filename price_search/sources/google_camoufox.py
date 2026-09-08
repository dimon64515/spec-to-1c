#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Google price source via Camoufox stealth browser."""

import asyncio
import logging
import random
import re
from decimal import Decimal, InvalidOperation
from urllib.parse import quote, unquote

from bs4 import BeautifulSoup

from price_search.models import PriceOffer
from price_search.sources.base import BasePriceSource

logger = logging.getLogger(__name__)

# Camoufox is imported lazily inside search() so the module can be imported
# even when the dependency is not installed.

_BLOCK_KEYWORDS = (
    "unusual traffic",
    "are you not a robot",
    "captcha",
    "подтвердите",
)

_PRICE_RE = re.compile(
    r"(\d[\d\s]*(?:[.,]\d{1,2})?)\s*(руб|₽|ру\\.|грн|₴|UAH)?",
    re.IGNORECASE,
)

_RESULT_LIMIT = 6
_PRICES_PER_RESULT_LIMIT = 3


class GoogleCamoufoxSource(BasePriceSource):
    """Search Google via Camoufox and extract prices from result snippets."""

    name = "google_camoufox"

    async def search(self, query: str) -> list[PriceOffer]:
        try:
            from camoufox import AsyncCamoufox
        except Exception as exc:
            logger.warning("Camoufox not available: %s", exc)
            return []

        search_url = (
            f"https://www.google.com/search?q={quote(query + ' купить цена')}"
        )

        try:
            async with AsyncCamoufox(headless=True) as browser:
                page = await browser.new_page()
                try:
                    await page.goto(
                        search_url, wait_until="domcontentloaded", timeout=45000
                    )
                    await asyncio.sleep(random.uniform(2.0, 4.0))

                    html = await page.content()
                    text = await page.inner_text("body")
                finally:
                    await page.close()
        except Exception as exc:
            logger.warning("Camoufox request failed for %r: %s", query, exc)
            return []

        if self._is_blocked(text):
            logger.info("Google blocked request for %r", query)
            return []

        results = self._parse_google_results(html)
        offers: list[PriceOffer] = []
        for result in results[:_RESULT_LIMIT]:
            source_text = f"{result.get('title', '')} {result.get('snippet', '')}"
            prices = self._extract_prices(source_text)[:_PRICES_PER_RESULT_LIMIT]
            for price, currency in prices:
                offers.append(
                    PriceOffer(
                        source=self.name,
                        query=query,
                        title=result.get("title", "")[:250],
                        price=price,
                        currency=currency,
                        supplier="google",
                        url=result.get("url", search_url)[:500],
                    )
                )

        await asyncio.sleep(random.uniform(1.0, 3.0))
        return offers

    @staticmethod
    def _is_blocked(text: str) -> bool:
        lowered = text.lower()
        return any(kw in lowered for kw in _BLOCK_KEYWORDS)

    @staticmethod
    def _parse_google_results(html: str) -> list[dict]:
        soup = BeautifulSoup(html, "lxml")
        results: list[dict] = []
        for a in soup.select("a[href]"):
            href = a.get("href", "")
            if not href.startswith("/url?q="):
                continue
            raw_url = href.split("/url?q=")[1].split("&")[0]
            url = unquote(raw_url)
            if not url.startswith("http") or "google.com" in url:
                continue

            title_tag = a.find("h3") or a
            title = title_tag.get_text(strip=True)

            snippet = ""
            parent = a.find_parent("div")
            if parent:
                snippet_tag = parent.select_one("div.VwiC3b, span.aCOpRe")
                snippet = snippet_tag.get_text(strip=True) if snippet_tag else ""

            results.append({"title": title, "url": url, "snippet": snippet})
        return results

    @staticmethod
    def _extract_prices(text: str) -> list[tuple[Decimal, str]]:
        found: list[tuple[Decimal, str]] = []
        for match in _PRICE_RE.finditer(text):
            raw_value = match.group(1)
            currency_marker = (match.group(2) or "").lower()
            normalized = GoogleCamoufoxSource._normalize_price_value(raw_value)
            if normalized is None:
                continue
            value = normalized
            if value <= 0:
                continue
            # Heuristic: avoid treating plain years/quantities as prices.
            if value > 9_999_999:
                continue
            currency = "UAH" if currency_marker in ("грн", "₴", "uah") else "RUB"
            found.append((value, currency))
        return found

    @staticmethod
    def _normalize_price_value(raw_value: str) -> Decimal | None:
        """Convert a raw price string like '12 500', '12.50' or '1,250.50' to Decimal."""
        cleaned = raw_value.replace(" ", "")
        if "," in cleaned and "." in cleaned:
            # Treat comma as thousands separator: 1,250.50 -> 1250.50
            cleaned = cleaned.replace(",", "")
        elif "," in cleaned:
            # Could be decimal separator (12,50) or thousands (12 500,00).
            # Use comma as decimal separator; trailing zeros after comma make it unambiguous.
            cleaned = cleaned.replace(",", ".")
        try:
            return Decimal(cleaned)
        except InvalidOperation:
            return None
