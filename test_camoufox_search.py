#!/usr/bin/env python3
"""Тест Camoufox на поиске цен для перекупного оборудования."""
import asyncio
import json
import re
from urllib.parse import quote

from camoufox import AsyncCamoufox
from playwright.async_api import async_playwright

PRODUCTS = [
    "Вентилятор крышный VDNV-NT-56H-3x15-HF-У1",
    "Циркуляционный насос DAB A 50/180 M 505803001",
    "Диффузор потолочный ДПУ-М 125",
]


def extract_prices(text: str) -> list:
    return re.findall(r"\d[\d\s]*(?:[,.]\d+)?\s*(?:руб|₽|ру\\.)?", text, re.IGNORECASE)


async def search_engine(page, engine: str, query: str) -> dict:
    if engine == "yandex":
        url = f"https://yandex.ru/search/?text={quote(query + ' купить цена')}"
    else:
        url = f"https://www.google.com/search?q={quote(query + ' купить цена')}"
    await page.goto(url, wait_until="domcontentloaded", timeout=60000)
    await asyncio.sleep(4)
    title = await page.title()
    text = await page.inner_text("body")
    blocked = any(k in text.lower() for k in (
        "я не робот", "подтвердите", "smartcaptcha", "captcha",
        "are you not a robot", "unusual traffic", "похоже", "робот"
    ))
    return {
        "engine": engine,
        "query": query,
        "title": title,
        "blocked": blocked,
        "prices": extract_prices(text)[:10],
        "text_preview": text[:600].replace("\n", " "),
    }


async def main():
    results = []
    async with AsyncCamoufox(headless=True) as browser:
        context = await browser.new_context()
        page = await context.new_page()
        for product in PRODUCTS:
            for engine in ("yandex", "google"):
                try:
                    res = await search_engine(page, engine, product)
                    results.append(res)
                    print(f"--- {product} | {engine}")
                    print(f"title: {res['title']}")
                    print(f"blocked: {res['blocked']}")
                    print(f"prices: {res['prices'][:5]}")
                except Exception as e:
                    results.append({"engine": engine, "query": product, "error": str(e)})
                    print(f"--- {product} | {engine} ERROR: {e}")
        await browser.close()

    out_path = "/tmp/camoufox_results.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\nResults saved to {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
