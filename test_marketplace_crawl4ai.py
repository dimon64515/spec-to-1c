#!/usr/bin/env python3
"""Test Crawl4AI extraction of HVAC prices from Ozon and Wildberries."""

import asyncio
import json
import re
from urllib.parse import quote

from crawl4ai import AsyncWebCrawler, BrowserConfig, CacheMode, CrawlerRunConfig

PRODUCTS = [
    "Вентилятор крышный VDNV-NT-56H-3x15-HF-У1",
    "Циркуляционный насос DAB A 50/180 M",
    "Диффузор потолочный ДПУ-М 125",
    "Датчик перепада давления DVL-500",
]


def build_urls(query: str):
    ozon = f"https://www.ozon.ru/search/?text={quote(query)}"
    wb = f"https://www.wildberries.ru/catalog/0/search.aspx?search={quote(query)}"
    return {"ozon": ozon, "wildberries": wb}


PRICE_RE = re.compile(r"(\d{1,3}(?:[\s\xa0]\d{3})+(?:[.,]\d{1,2})?)", re.UNICODE)


def extract_prices(text: str):
    """Return unique price-like numbers found in text."""
    found = []
    for m in PRICE_RE.finditer(text):
        raw = m.group(1)
        normalized = raw.replace(" ", "").replace("\xa0", "").replace(",", ".")
        try:
            found.append(float(normalized))
        except ValueError:
            continue
    # deduplicate preserving order
    seen = set()
    result = []
    for v in found:
        if v not in seen:
            seen.add(v)
            result.append(v)
    return result


def extract_cards(markup: str, base_url: str):
    """Best-effort extraction of product cards from raw HTML."""
    cards = []
    # Ozon tile pattern
    ozon_tiles = re.findall(
        r'<a[^>]*href="(/product/[^"]+)"[^>]*>.*?<span[^>]*>([^<]*(?:руб|₽)[^<]*)</span>',
        markup,
        re.IGNORECASE | re.DOTALL,
    )
    for href, price_span in ozon_tiles[:10]:
        prices = extract_prices(price_span)
        if prices:
            cards.append({
                "title": "",
                "url": "https://www.ozon.ru" + href.split("?")[0],
                "prices": prices,
            })

    # Wildberries product card pattern
    wb_cards = re.findall(
        r'class="product-card__name[^"]*"[^>]*>([^<]+)<.*?href="(/catalog/[^"]+)"',
        markup,
        re.IGNORECASE | re.DOTALL,
    )
    for title, href in wb_cards[:10]:
        cards.append({
            "title": title.strip(),
            "url": "https://www.wildberries.ru" + href,
            "prices": [],
        })

    # Generic price extraction from whole page if no structured cards
    if not cards:
        prices = extract_prices(markup)
        if prices:
            cards.append({"title": "", "url": base_url, "prices": prices[:20]})

    return cards


async def fetch_one(crawler: AsyncWebCrawler, marketplace: str, query: str, url: str):
    result = {
        "marketplace": marketplace,
        "query": query,
        "url": url,
        "status": None,
        "success": False,
        "blocked": False,
        "block_reason": None,
        "cards": [],
        "prices": [],
        "error": None,
        "page_title": None,
        "content_length": 0,
    }
    try:
        run_cfg = CrawlerRunConfig(
            cache_mode=CacheMode.BYPASS,
            page_timeout=45000,
            wait_until="networkidle",
            magic=True,
            scan_full_page=True,
        )
        resp = await crawler.arun(url, config=run_cfg)
        result["status"] = getattr(resp, "status_code", None)
        result["page_title"] = getattr(resp, "title", None)
        markup = getattr(resp, "html", "") or ""
        result["content_length"] = len(markup)

        if not markup:
            result["error"] = "empty HTML response"
            return result

        lower = markup.lower()
        block_signals = [
            "captcha", "капча", "докажите, что вы не робот", "are you human",
            "подтвердите, что вы не робот", "access denied", "403",
            "проверка безопасности", "ddos-guard", "cloudflare",
        ]
        for sig in block_signals:
            if sig in lower:
                result["blocked"] = True
                result["block_reason"] = sig
                break

        cards = extract_cards(markup, url)
        result["cards"] = cards
        result["prices"] = sorted({p for c in cards for p in c["prices"]}, reverse=True)
        result["success"] = not result["blocked"]
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result


async def main():
    browser_cfg = BrowserConfig(
        headless=True,
        verbose=False,
        extra_args=["--disable-blink-features=AutomationControlled"],
    )
    tasks = []
    async with AsyncWebCrawler(config=browser_cfg) as crawler:
        for query in PRODUCTS:
            urls = build_urls(query)
            for marketplace, url in urls.items():
                tasks.append(fetch_one(crawler, marketplace, query, url))
        results = await asyncio.gather(*tasks, return_exceptions=True)

    final_results = []
    for r in results:
        if isinstance(r, Exception):
            final_results.append({"error": f"{type(r).__name__}: {r}"})
        else:
            final_results.append(r)

    output_path = "/tmp/marketplace_results.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(final_results, f, ensure_ascii=False, indent=2)
    print(f"Saved {len(final_results)} results to {output_path}")

    # Console summary
    for r in final_results:
        status = r.get("status")
        blocked = r.get("blocked")
        prices = r.get("prices", [])
        print(
            f"[{r['marketplace']}] {r['query'][:40]:<40} "
            f"status={status} blocked={blocked} cards={len(r.get('cards', []))} prices={prices[:5]}"
        )


if __name__ == "__main__":
    asyncio.run(main())
