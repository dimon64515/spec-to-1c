"""Контракт корпуса позиций полного покрытия (фикстура для приёмки HTTP-сервиса 1С.

Фикстура собирается один раз:
    .venv/bin/python tools/build_order_positions_fixture.py
"""
import json
from pathlib import Path

FIXTURE = Path(__file__).parent / "fixtures" / "order_positions_full.json"

CONTRACT_KEYS = {
    "article", "qty", "thickness", "material", "params", "comment",
    "shina", "conn0", "conn1",
}
MIN_ARTICLES = 198  # 199 валидных артикулов каталога минус заблокированный 20-2


def test_fixture_exists_and_covers_catalog():
    assert FIXTURE.exists(), (
        "фикстура не собрана: "
        ".venv/bin/python tools/build_order_positions_fixture.py"
    )
    corpus = json.loads(FIXTURE.read_text(encoding="utf-8"))
    positions = corpus["positions"]
    articles = {p["article"] for p in positions}
    assert len(articles) >= MIN_ARTICLES, f"покрыто артикулов: {len(articles)}"
    assert "20-2" not in articles  # дефект ПередЗаписью в 1С, см. BACKLOG
    assert "----" not in articles
    assert corpus["synthetic_count"] > 0
    assert corpus["real_count"] > 0


def test_fixture_positions_match_contract():
    corpus = json.loads(FIXTURE.read_text(encoding="utf-8"))
    for pos in corpus["positions"]:
        assert set(pos.keys()) == CONTRACT_KEYS, pos["article"]
        assert isinstance(pos["params"], dict) and pos["params"], pos["article"]
        for conn_key in ("shina", "conn0", "conn1"):
            assert conn_key in pos, pos["article"]
    # сериализация в тело запроса сервиса — без потерь (utf-8 round-trip)
    body = {
        "order_comment": "c",
        "request_id": None,
        "positions": corpus["positions"],
    }
    assert json.loads(json.dumps(body, ensure_ascii=False))["positions"] == corpus["positions"]
