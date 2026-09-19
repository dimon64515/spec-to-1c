"""Интеграция LLM-фолбэка в parse_row/process_rows. Subprocess/сеть запрещены."""
import pytest

import llm_size_classifier as lsc
import process_specification_table as pst


def _cls(**kw):
    base = dict(status="ok", format_class="round_diameter", dims={"D0": 315.0},
                section="round", adoptable=True, raw_response="{}", detail="")
    base.update(kw)
    return lsc.SizeClassification(**base)


DEFAULTS = {"material": "оцинкованная", "thickness": "0.5"}


def test_parse_row_without_cache_identical_behavior():
    row = {"name": "Воздуховод спирально-навивной", "size": "315/315/160",
           "unit": "шт", "quantity": "2"}
    p1, s1 = pst.parse_row(row, DEFAULTS)
    p2, s2 = pst.parse_row(row, DEFAULTS, llm_cache={})
    assert (p1, s1) == (p2, s2)
    assert p1 is None and s1 is not None


def test_parse_row_adopts_llm_dims():
    # Имя — обычный воздуховод: проходит мимо fittings/equipment-веток,
    # size каскад не берёт (slash-форма), LLM-кэш даёт round_diameter.
    row = {"name": "Воздуховод спирально-навивной", "size": "315/315-160",
           "unit": "шт", "quantity": "2"}
    cache = {"315/315-160": _cls()}
    parsed, skip = pst.parse_row(row, DEFAULTS, llm_cache=cache)
    assert skip is None
    assert parsed["article"] == "1-1-2"
    assert parsed["params"]["D0"] == 315.0


def test_parse_row_llm_rejected_gets_reason():
    row = {"name": "Воздуховод спирально-навивной", "size": "??",
           "unit": "шт", "quantity": "2"}
    cache = {"??": _cls(status="rejected", adoptable=False,
                        format_class="unknown", detail="spans пусты")}
    parsed, skip = pst.parse_row(row, DEFAULTS, llm_cache=cache)
    assert parsed is None
    assert skip["reason"] == lsc.REASON_LLM_REJECTED
    assert skip["llm_format_class"] == "unknown"
    assert skip["llm_detail"]


def test_fittings_path_not_preempted_by_llm():
    # Фасонку fittings-путь берёт САМ (до duct-пути): даже adoptable-запись
    # в кэше не должна превратить отвод в воздуховод.
    row = {"name": "Отвод 90 град Ф160-Ф160", "size": "Ф160-Ф160",
           "unit": "шт", "quantity": "3"}
    cache = {"Ф160-Ф160": _cls(format_class="round_diameter", dims={"D0": 160.0})}
    parsed, skip = pst.parse_row(row, DEFAULTS, llm_cache=cache)
    assert parsed is not None and parsed["article"].startswith("2-"), parsed


def test_process_rows_builds_cache_and_calls_classifier(monkeypatch):
    import re as _re
    calls = []

    def fake_batch(strings, cfg=None, runner=None):
        calls.append(list(strings))
        out = {}
        for s in strings:
            d0 = float(_re.match(r"\d+", s).group())
            out[s] = _cls(dims={"D0": d0})
        return out

    monkeypatch.setattr(lsc, "llm_enabled", lambda cfg=None: True)
    monkeypatch.setattr(lsc, "classify_sizes_batch", fake_batch)
    monkeypatch.setattr(pst, "ALLOWED_ARTICLES", set())

    rows = [
        {"name": "Воздуховод спирально-навивной", "size": "315/315-160",
         "unit": "шт", "quantity": "2"},
        {"name": "Воздуховод спирально-навивной", "size": "400/400-200",
         "unit": "шт", "quantity": "1"},
    ]
    xml, skipped, success = pst.process_rows(rows, defaults=DEFAULTS)
    assert len(success) == 2 and not skipped
    assert calls and len(calls[0]) == 2  # обе нераспознанные строки ушли в батч
    assert {r["params"]["D0"] for r in success} == {315.0, 400.0}


def test_process_rows_llm_disabled_no_classifier_call(monkeypatch):
    def boom(*a, **kw):
        raise AssertionError("classify_sizes_batch не должен вызываться")

    monkeypatch.setattr(lsc, "llm_enabled", lambda cfg=None: False)
    monkeypatch.setattr(lsc, "classify_sizes_batch", boom)
    rows = [{"name": "Воздуховод", "size": "315/315-160",
             "unit": "шт", "quantity": "2"}]
    xml, skipped, success = pst.process_rows(rows, defaults=DEFAULTS)
    assert not success and skipped
    assert skipped[0]["reason"] != lsc.REASON_LLM_REJECTED
