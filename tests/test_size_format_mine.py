"""Майнер автообучения форматов. Все внешние части замоканы."""
import json

import pytest

import tools.size_format_mine as mine


def test_get_learning_config_defaults():
    cfg = mine.get_learning_config()
    assert cfg["enabled"] is False
    assert cfg["min_occurrences"] >= 2
    assert "auto_commit" in cfg


def test_load_skipped_sizes_filters_reasons(tmp_path):
    report = [
        {"name": "a", "size": "∅315", "reason": "Не удалось распознать размер / тип"},
        {"name": "b", "size": "Ø400", "reason": "LLM-классификация отклонена"},
        {"name": "c", "size": "Ф160", "reason": "Покупная позиция — завод не производит"},
        {"name": "d", "size": "", "reason": "Не удалось распознать размер / тип"},
    ]
    p = tmp_path / "order_skipped.json"
    p.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    counter = mine.load_skipped_sizes([str(p)])
    assert counter == {"∅315": 1, "Ø400": 1}


def test_load_skipped_sizes_aggregates_multiple_reports(tmp_path):
    rows = [{"name": "a", "size": "∅315", "reason": "Не удалось распознать размер / тип"}]
    for name in ("one_skipped.json", "two_skipped.json"):
        (tmp_path / name).write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    counter = mine.load_skipped_sizes([str(tmp_path / "one_skipped.json"),
                                       str(tmp_path / "two_skipped.json")])
    assert counter["∅315"] == 2


def test_extract_symbol_candidates():
    assert mine.extract_symbol_candidates("∅315") == ["∅"]
    assert mine.extract_symbol_candidates("1250✕800") == ["✕"]
    assert mine.extract_symbol_candidates("315ø") == ["ø"]
    assert mine.extract_symbol_candidates("315") == []
    assert mine.extract_symbol_candidates("Ду 315") == ["Ду"]  # буквенный префикс с пробелом


def test_cluster_filter_min_occurrences():
    counter = {"∅315": 5, "Ø400": 1, "Х500": 3}
    clusters = mine.stable_clusters(counter, min_occurrences=3)
    assert clusters == {"∅315": 5, "Х500": 3}
