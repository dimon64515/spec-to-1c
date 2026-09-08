#!/usr/bin/env python3
"""Test Crawl4AI extraction from B2B and HVAC supplier sites."""

import asyncio
import json
import re
import urllib.parse
from datetime import datetime

from crawl4ai import AsyncWebCrawler

PRODUCTS = [
    "Вентилятор крышный VDNV-NT-56H-3x15-HF-У1",
    "Циркуляционный насос DAB A 50/180 M 505803001",
    "Диффузор потолочный ДПУ-М 125",
    "Приточно-вытяжная решетка РОН-110 800x500",
]

PLATFORMS = {
    "pulscen": "https://pulscen.ru/search?q={q}",
    "tiu": "https://tiu.ru/search?q={q}",
    "blizko": "https://blizko.ru/search?q={q}",
}

HVAC_SITES = [
    "https://www.air-ned.com/catalog/search/?q={q}",
    "https://vents.ru/catalog/search/?q={q}",
    "https://www.ventvtb.ru/search/?q={q}",
]


def extract_prices(text: str):
    """Naive Russian ruble price extraction."""
    prices = []
    # Common patterns: 12 345 ₽, 12345 руб, 12 345,00 ₽
    pattern = re.compile(
        r"(?:(\d[\d\s]*)[\.,]?\d{0,2})\s*(?:₽|руб|руб\.|RUB|rub)",
        re.IGNORECASE,
    )
    for m in pattern.finditer(text):
        raw = m.group(1).replace(" ", "").replace("\u00a0", "")
        try:
            prices.append(int(raw))
        except ValueError:
            continue
    # Fallback: any suspicious integer followed by whitespace and currency symbol
    pattern2 = re.compile(r"(\d[\d\s]{2,})\s*₽")
    for m in pattern2.finditer(text):
        raw = m.group(1).replace(" ", "").replace("\u00a0", "")
        try:
            prices.append(int(raw))
        except ValueError:
            continue
    return sorted(set(prices))


def extract_links(text: str, base_url: str):
    """Very naive link extraction from raw text or href attributes."""
    links = set()
    # href="..."
    for m in re.finditer(r'href="([^"]+)"', text):
        links.add(m.group(1))
    # http(s)://
    for m in re.finditer(r'https?://[^\s"<>]+', text):
        links.add(m.group(0))
    return sorted(links)


def summarize_content(text: str, length: int = 400):
    """Return a short snippet of page content."""
    t = re.sub(r"\s+", " ", text).strip()
    return t[:length]


async def crawl_one(crawler: AsyncWebCrawler, name: str, url: str):
    result = {
        "name": name,
        "url": url,
        "status": None,
        "blocked": False,
        "title": None,
        "snippet": None,
        "prices": [],
        "links": [],
        "error": None,
        "fetched_at": datetime.utcnow().isoformat(),
    }
    try:
        page = await crawler.arun(url=url)
        # crawl4ai 0.9.2 result object attributes
        text = ""
        if hasattr(page, "markdown") and page.markdown:
            text = str(page.markdown)
        elif hasattr(page, "text") and page.text:
            text = str(page.text)
        elif hasattr(page, "html") and page.html:
            text = str(page.html)

        result["status"] = getattr(page, "status_code", None)
        result["title"] = getattr(page, "title", None)
        result["snippet"] = summarize_content(text)
        result["prices"] = extract_prices(text)
        result["links"] = extract_links(text, url)[:20]

        low = text.lower()
        blocked_keywords = [
            "captcha",
            "капча",
            "доступ ограничен",
            "доступ к сайту ограничен",
            "подозрительная активность",
            "security check",
            "cloudflare",
            "ddos-guard",
            "blocked",
            "access denied",
            "403",
            "please enable javascript",
        ]
        result["blocked"] = any(k in low for k in blocked_keywords) or result["status"] in (403, 429)
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result


async def main():
    results = []
    async with AsyncWebCrawler(
        verbose=False,
        headless=True,
        user_agent_mode="random",
    ) as crawler:
        tasks = []
        labels = []

        # B2B platforms
        for product in PRODUCTS:
            q = urllib.parse.quote(product)
            for platform, template in PLATFORMS.items():
                url = template.format(q=q)
                tasks.append(crawl_one(crawler, f"{platform}:{product}", url))
                labels.append(f"{platform}:{product}")

        # HVAC sites
        for product in PRODUCTS:
            q = urllib.parse.quote(product)
            for template in HVAC_SITES:
                url = template.format(q=q)
                domain = urllib.parse.urlparse(url).netloc
                tasks.append(crawl_one(crawler, f"{domain}:{product}", url))
                labels.append(f"{domain}:{product}")

        results = await asyncio.gather(*tasks, return_exceptions=True)
        results = [
            r if not isinstance(r, Exception) else {"error": str(r)}
            for r in results
        ]

    output = {
        "meta": {
            "created_at": datetime.utcnow().isoformat(),
            "products": PRODUCTS,
            "platforms": list(PLATFORMS.keys()),
            "hvac_sites": HVAC_SITES,
        },
        "results": results,
    }

    out_path = "/tmp/b2b_hvac_results.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"Saved {len(results)} results to {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
