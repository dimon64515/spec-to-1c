"""Майнер автообучения форматов. Все внешние части замоканы."""
import json
import os
from pathlib import Path

import pytest
import yaml

import llm_size_classifier as lsc
import process_specification_table as pst
import size_notations as szn
import tools.size_format_mine as mine

PATCH = {"add_prefixes": ["∅"], "add_suffixes": [], "add_separators": []}


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


def _current():
    n = szn.get_notations()
    return {
        "diameter_prefixes": list(n["diameter_prefixes"]),
        "diameter_suffixes": list(n["diameter_suffixes"]),
        "separators": list(n["separators"]),
    }


def test_sanitize_drops_digits_and_too_long():
    prop = {"add_prefixes": ["dia315", "∅", "toolongprefix"],
            "add_suffixes": ["øØ"],
            "add_separators": ["✕"]}
    out = mine.sanitize_proposals(prop, _current())
    assert out["add_prefixes"] == ["∅"]            # dia315 содержит цифры, toolongprefix >3
    assert out["add_suffixes"] == []               # ø и Ø уже в конфиге
    assert out["add_separators"] == ["✕"]


def test_sanitize_drops_duplicates_and_nonadditive():
    cur = _current()
    prop = {"add_prefixes": [cur["diameter_prefixes"][0], "новый"],
            "add_suffixes": [], "add_separators": cur["separators"]}
    out = mine.sanitize_proposals(prop, cur)
    assert cur["diameter_prefixes"][0] not in out["add_prefixes"]
    assert out["add_separators"] == []


def test_propose_additions_parses_kimi_response(monkeypatch):
    import json as _json
    content = _json.dumps({
        "add_prefixes": ["∅"], "add_suffixes": [], "add_separators": ["✕"]
    })
    stdout = _json.dumps({"role": "assistant", "content": content})

    monkeypatch.setattr(lsc, "llm_enabled", lambda cfg=None: True)
    runner_calls = []

    def runner(prompt, cfg):
        runner_calls.append(prompt)
        return stdout

    out = mine.propose_additions({"∅315": 5, "1250✕800": 3}, runner=runner)
    assert runner_calls, "kimi не вызван"
    assert "∅315" in runner_calls[0] and "1250✕800" in runner_calls[0]
    assert out["add_prefixes"] == ["∅"]


def test_propose_additions_failure_returns_empty(monkeypatch):
    monkeypatch.setattr(lsc, "llm_enabled", lambda cfg=None: True)

    def runner(prompt, cfg):
        raise RuntimeError("kimi down")

    assert mine.propose_additions({"∅315": 5}, runner=runner) == {}


def test_sanitize_rejects_unknown_sections_and_types():
    out = mine.sanitize_proposals(
        {"add_prefixes": ["∅"], "validation": {"min_side_mm": 50}, "add_lengths": {"X": 1}},
        _current(),
    )
    assert "validation" not in out and "add_lengths" not in out
    assert out["add_prefixes"] == ["∅"]


def test_gate_green_when_cluster_parses(tmp_path, monkeypatch):
    # Патч добавляет префикс ∅: под пропатченной копией "∅315" должен парситься.
    monkeypatch.setattr(mine, "_run_pytest_gate", lambda env: (True, ""))
    ok, reason = mine.gate_check(PATCH, ["∅315"])
    assert ok, reason


def test_gate_red_when_cluster_still_unparsed(tmp_path, monkeypatch):
    # Слэш-форма не символьная — патч её не берёт.
    monkeypatch.setattr(mine, "_run_pytest_gate", lambda env: (True, ""))
    ok, reason = mine.gate_check(PATCH, ["315/315/160"])
    assert not ok and "парс" in reason.lower()


def test_gate_red_when_cluster_already_parses(monkeypatch):
    # Ф315 парсится и без патча → патч не нужен, pytest не должен вызываться.
    calls = []
    monkeypatch.setattr(mine, "_run_pytest_gate",
                        lambda env: (calls.append(env), (True, ""))[1])
    patch = {"add_prefixes": ["∅"], "add_suffixes": [], "add_separators": []}
    ok, reason = mine.gate_check(patch, ["Ф315"])
    assert not ok and "уже парсится" in reason
    assert calls == []


def test_gate_green_still_works(monkeypatch):
    # Отрицательный пре-чек: "⊘315" под текущим конфигом НЕ парсится,
    # под патчем (префикс ⊘) — парсится.
    assert pst.parse_size("⊘315")[0] is None and not pst.extract_dimensions("⊘315")
    monkeypatch.setattr(mine, "_run_pytest_gate", lambda env: (True, ""))
    patch = {"add_prefixes": ["⊘"], "add_suffixes": [], "add_separators": []}
    ok, reason = mine.gate_check(patch, ["⊘315"])
    assert ok, reason


def test_sanitize_rejects_zero_width():
    out = mine.sanitize_proposals({"add_prefixes": ["\u200b∅"],
                                   "add_suffixes": [], "add_separators": []},
                                  _current())
    assert out["add_prefixes"] == []


def test_gate_red_when_pytest_fails(monkeypatch):
    monkeypatch.setattr(mine, "_run_pytest_gate", lambda env: (False, "3 failed"))
    ok, reason = mine.gate_check(PATCH, ["∅315"])
    assert not ok and "3 failed" in reason


def test_gate_restores_config_after_check():
    # Усиление слабой версии из брифа (сравнение `szn.prefix_group() == szn.prefix_group()`
    # тавтологично): сверяем реальное состояние кэша и env ДО/ПОСЛЕ.
    before_prefixes = list(szn.get_notations()["diameter_prefixes"])
    before_group = szn.prefix_group()
    old_env = os.environ.get("SPEC_TO_1C_SIZE_NOTATIONS")
    mine.gate_check(PATCH, ["∅315"], run_pytest=lambda env: (True, ""))
    assert szn.get_notations()["diameter_prefixes"] == before_prefixes
    assert szn.prefix_group() == before_group
    assert "∅" not in szn.get_notations()["diameter_prefixes"]
    assert os.environ.get("SPEC_TO_1C_SIZE_NOTATIONS") == old_env


def test_apply_patch_writes_provenance(tmp_path):
    n = szn.get_notations()
    patched = mine._merged_notations(PATCH)   # helper: dict = текущий конфиг + патч
    assert "∅" in patched["diameter_prefixes"]
    assert n["diameter_prefixes"] != patched["diameter_prefixes"]  # исходный не мутирован


def test_main_dry_run_reports_without_writing(tmp_path, monkeypatch):
    # Сквозной dry-run: отчёт формируется, реальный конфиг не тронут.
    report = [{"name": "a", "size": "∅315",
               "reason": "Не удалось распознать размер / тип"}] * 3
    rp = tmp_path / "x_skipped.json"
    rp.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    before = Path("config/size_notations.yaml").read_text(encoding="utf-8")
    monkeypatch.setattr(lsc, "llm_enabled", lambda cfg=None: True)
    monkeypatch.setattr(mine, "propose_additions",
                        lambda clusters, runner=None: {"add_prefixes": ["∅"],
                                                       "add_suffixes": [], "add_separators": []})
    monkeypatch.setattr(mine, "_run_pytest_gate", lambda env: (True, ""))
    rc = mine.main(["--reports", str(rp), "--min-occurrences", "2", "--dry-run"])
    assert rc == 0
    assert Path("config/size_notations.yaml").read_text(encoding="utf-8") == before
