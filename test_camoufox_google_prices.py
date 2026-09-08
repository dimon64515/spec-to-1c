#!/usr/bin/env python3
"""Извлечение цен из Google-выдачи через Camoufox (быстрая версия)."""
import asyncio
import json
import re
from urllib.parse import quote, unquote

from camoufox import AsyncCamoufox
from bs4 import BeautifulSoup

PRODUCTS = [
    "Вентилятор крышный VDNV-NT-56H-3x15-HF-У1",
    "Циркуляционный насос DAB A 50/180 M 505803001",
    "Диффузор потолочный ДПУ-М 125",
]

OUT_FILE = "/tmp/camoufox_google_prices.json"


def save_out(data: list):
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def extract_prices(text: str) -> list:
    found = re.findall(r"\d[\d\s]*(?:[,.]\d+)?\s*(?:руб|₽|ру\\.|грн|₴|UAH)?", text, re.IGNORECASE)
    return [f.strip() for f in found[:15]]


def parse_google_results(html: str) -> list:
    soup = BeautifulSoup(html, "lxml")
    results = []
    for a in soup.select("a[href]"):
        url = a.get("href", "")
        if not url.startswith("/url?q="):
            continue
        url = unquote(url.split("/url?q=")[1].split("&")[0])
        if not url.startswith("http") or "google.com" in url:
            continue
        title_tag = a.find("h3") or a
        title = title_tag.get_text(strip=True)[:120]
        parent = a.find_parent("div")
        snippet = ""
        if parent:
            snippet_tag = parent.select_one("div.VwiC3b, span.aCOpRe")
            snippet = snippet_tag.get_text(strip=True)[:200] if snippet_tag else ""
        results.append({"title": title, "url": url, "snippet": snippet})
    return results[:6]


async def search_google(page, query: str) -> dict:
    url = f"https://www.google.com/search?q={quote(query + ' купить цена')}"
    await page.goto(url, wait_until="domcontentloaded", timeout=45000)
    await asyncio.sleep(3)
    html = await page.content()
    text = await page.inner_text("body")
    blocked = any(k in text.lower() for k in (
        "unusual traffic", "are you not a robot", "captcha", "подтвердите"
    ))
    return {
        "query": query,
        "blocked": blocked,
        "prices_in_search": extract_prices(text),
        "results": parse_google_results(html),
    }


async def fetch_page_price(page, url: str) -> dict:
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=20000)
        await asyncio.sleep(2)
        title = await page.title()
        text = await page.inner_text("body")
        return {
            "url": url,
            "title": title[:120],
            "prices": extract_prices(text)[:8],
            "preview": text[:300].replace("\n", " "),
        }
    except Exception as e:
        return {"url": url, "error": str(e)[:200]}


async def main():
    out = []
    async with AsyncCamoufox(headless=True) as browser:
        for i, product in enumerate(PRODUCTS):
            try:
                search_page = await browser.new_page()
                res = await search_google(search_page, product)
                await search_page.close()
                print(f"[{i+1}/{len(PRODUCTS)}] {product} | blocked={res['blocked']} | results={len(res['results'])}")

                # Visit top 2 result pages with fresh page
                page_prices = []
                if res["results"]:
                    price_page = await browser.new_page()
                    for r in res["results"][:2]:
                        pp = await fetch_page_price(price_page, r["url"])
                        pp["result_title"] = r["title"]
                        page_prices.append(pp)
                        print(f"    -> {r['title'][:55]} | prices={pp.get('prices', [])[:3]} | err={pp.get('error', '')}")
                    await price_page.close()
                res["page_prices"] = page_prices
                out.append(res)
            except Exception as e:
                print(f"[{i+1}/{len(PRODUCTS)}] {product} ERROR: {e}")
                out.append({"query": product, "error": str(e)})
            save_out(out)
            await asyncio.sleep(2)

    print(f"\nSaved to {OUT_FILE}")


if __name__ == "__main__":
    asyncio.run(main())
