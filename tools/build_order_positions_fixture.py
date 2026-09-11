#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Сборка корпуса позиций полного покрытия для приёмки HTTP-сервиса 1С.

Один раз: прогоняет PDF-корпуса пайплайна (examples/) и дополняет
синтетическими позициями артикулы, которых в корпусах нет. Результат —
tests/fixtures/order_positions_full.json (коммитится; тесты только читают).

Источник артикулов: reference/1c_products_all.json (снимок справочника
асПродукция). Мусорные артикулы «----» и заблокированные продукты
(bitrix_bot.pipeline.BLOCKED_1C_ARTICLES, сейчас 20-2) исключаются.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bitrix_bot.pipeline import BLOCKED_1C_ARTICLES, process_pdf_to_positions
from json_positions import build_positions

FIXTURE = ROOT / "tests" / "fixtures" / "order_positions_full.json"
PRODUCTS_REF = ROOT / "reference" / "1c_products_all.json"
EXAMPLES = ROOT / "examples"

SYNTH_MATERIAL = "Рулон оц.(08ПС)0.80"


def real_positions() -> list[dict]:
    """Позиции из всех PDF корпусов, прогнанных через боевой пайплайн."""
    positions = []
    for pdf in sorted(EXAMPLES.rglob("*.pdf")):
        print(f"pipeline: {pdf.relative_to(ROOT)}")
        success, _skipped = process_pdf_to_positions(pdf.read_bytes())
        positions.extend(build_positions(success))
    return positions


def catalog_articles() -> list[str]:
    data = json.loads(PRODUCTS_REF.read_text(encoding="utf-8"))["data"]
    arts = []
    for row in data:
        art = (row.get("Артикул") or "").strip()
        if not art or art == "----" or row.get("ЭтоГруппа"):
            continue
        arts.append(art)
    return sorted(set(arts))


def synthetic_position(article: str) -> dict:
    """Минимально валидная позиция для артикула, не встретившегося в корпусах.

    Сечение по средней цифре артикула: «1» — круглое (D0), иначе прямоугольное
    (A0×B0); длина L0=1000. Шина — по правилам техотдела от макс. размера
    сечения (json_positions.shina_by_max_dim): 200 мм → 65, 400 мм → 95.
    """
    parts = article.split("-")
    is_round = len(parts) > 1 and parts[1] == "1"
    if is_round:
        params = {"D0": 200, "L0": 1000}
        shina = "65"
    else:
        params = {"A0": 400, "B0": 200, "L0": 1000}
        shina = "95"
    return {
        "article": article,
        "qty": 1,
        "thickness": 0.8,
        "material": SYNTH_MATERIAL,
        "params": params,
        "comment": "synthetic coverage",
        "shina": shina,
        "conn0": "6",
        "conn1": "6",
    }


def build_fixture() -> dict:
    positions = real_positions()
    covered = {p["article"] for p in positions}
    blocked = set(BLOCKED_1C_ARTICLES)
    synth = [
        synthetic_position(a)
        for a in catalog_articles()
        if a not in covered and a not in blocked
    ]
    return {
        "real_count": len(positions),
        "synthetic_count": len(synth),
        "blocked": sorted(blocked),
        "positions": sorted(positions + synth, key=lambda p: p["article"]),
    }


def main() -> None:
    corpus = build_fixture()
    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE.write_text(
        json.dumps(corpus, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    articles = {p["article"] for p in corpus["positions"]}
    print(
        f"real={corpus['real_count']} synth={corpus['synthetic_count']} "
        f"total={len(corpus['positions'])} unique_articles={len(articles)} "
        f"blocked={corpus['blocked']}"
    )


if __name__ == "__main__":
    main()
