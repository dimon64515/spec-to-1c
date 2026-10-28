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


def test_silencer_name_guard_blocks_llm_adoption():
    # «Шумоглушитель трубчатый»: fittings-путь сдаётся (slash-форма «200/160»
    # не в его грамматике), adoptable-запись в кэше НЕ должна превращать
    # арматуру в круглый воздуховод 1-1-2.
    row = {"name": "Шумоглушитель трубчатый", "size": "200/160",
           "unit": "шт", "quantity": "2"}
    cache = {"200/160": _cls(dims={"D0": 200.0})}
    parsed, skip = pst.parse_row(row, DEFAULTS, llm_cache=cache)
    assert parsed is None
    assert skip["reason"] == lsc.REASON_LLM_REJECTED
    assert "усыновление запрещено" in skip["llm_detail"]


def test_duct_name_adoption_positive_control():
    # Контроль: то же adoptable-значение с именем воздуховода усыновляется.
    row = {"name": "Воздуховод спирально-навивной", "size": "200/160",
           "unit": "шт", "quantity": "2"}
    cache = {"200/160": _cls(dims={"D0": 200.0})}
    parsed, skip = pst.parse_row(row, DEFAULTS, llm_cache=cache)
    assert skip is None
    assert parsed["article"] == "1-1-2"
    assert parsed["params"]["D0"] == 200.0


def test_cache_lookup_falls_back_to_raw_row_size(monkeypatch):
    # Pre-pass кэширует по сырому row["size"], а parse_row ищет по
    # нормализованному (тут — OCR-исправленному) размеру: lookup должен
    # откатиться на сырой ключ.
    row = {"name": "Воздуховод спирально-навивной", "size": "2 00/160",
           "unit": "шт", "quantity": "2"}
    monkeypatch.setattr(pst, "correct_ocr_size", lambda name, size: ("200/160", []))
    cache = {"2 00/160": _cls(dims={"D0": 200.0})}
    parsed, skip = pst.parse_row(row, DEFAULTS, llm_cache=cache)
    assert skip is None
    assert parsed["article"] == "1-1-2"
    assert parsed["params"]["D0"] == 200.0


def test_tee_size_adopted_for_fitting(monkeypatch):
    import llm_size_classifier as lsc
    import process_specification_table as pst
    from llm_size_classifier import SizeClassification

    def fake_batch(strings, cfg=None, runner=None):
        return {s: SizeClassification(status="ok", format_class="tee_axbxc",
                                      dims={"A0": 300, "B0": 200, "C0": 150},
                                      section="rectangular", adoptable=True,
                                      raw_response="", detail="") for s in strings}
    monkeypatch.setattr(lsc, "llm_enabled", lambda cfg=None: True)
    monkeypatch.setattr(lsc, "classify_sizes_batch", fake_batch)
    rows = [{"name": "Тройник", "size": "300x200-150", "unit": "шт", "quantity": 1}]
    xml, skipped, success = pst.process_rows(rows)
    assert success, "тройник с LLM-классом tee_axbxc не должен уходить в skipped"
    assert not any(lsc.REASON_LLM_REJECTED in (r.get("reason") or "") for r in skipped)


def test_tee_size_adopted_via_reconstructed_candidate(monkeypatch):
    # Исходная строка фасонным парсером не подтверждается (spy возвращает
    # None только для неё): tee_axbxc усыновляется через реконструированную
    # кандидатную строку, которую try_parse_fitting подтверждает — LLM не
    # формирует размеры фасонки в обход фасонного парсера.
    row = {"name": "Тройник", "size": "300x200~150", "unit": "шт", "quantity": "1"}
    cache = {"300x200~150": _cls(format_class="tee_axbxc",
                                 dims={"A0": 300, "B0": 200, "C0": 150},
                                 section="rectangular")}
    real = pst.try_parse_fitting
    calls = []

    def spy(name, size, unit, quantity, **kw):
        calls.append(size)
        if size == "300x200~150":
            return None
        return real(name, size, unit, quantity, **kw)

    monkeypatch.setattr(pst, "try_parse_fitting", spy)
    parsed, skip = pst.parse_row(row, DEFAULTS, llm_cache=cache)
    assert skip is None
    assert parsed["article"].startswith("4-"), parsed
    assert len(calls) >= 2 and "150" in calls[-1]  # усыновлено через кандидата с ветвью


def test_tee_fitting_not_adopted_when_parser_rejects(monkeypatch):
    # Ни один LLM-кандидат не подтверждён фасонным парсером — строка
    # остаётся в skipped с REASON_LLM_REJECTED, в XML не попадает.
    row = {"name": "Тройник", "size": "300x200~150", "unit": "шт", "quantity": "1"}
    cache = {"300x200~150": _cls(format_class="tee_axbxc",
                                 dims={"A0": 300, "B0": 200, "C0": 150},
                                 section="rectangular")}
    monkeypatch.setattr(pst, "try_parse_fitting", lambda *a, **kw: None)
    parsed, skip = pst.parse_row(row, DEFAULTS, llm_cache=cache)
    assert parsed is None
    assert skip["reason"] == lsc.REASON_LLM_REJECTED


def test_cache_lookup_falls_back_to_raw_row_size_in_skip(monkeypatch):
    # То же в ветке финального пропуска: rejected-запись по сырому ключу.
    row = {"name": "Воздуховод спирально-навивной", "size": "2 00/160",
           "unit": "шт", "quantity": "2"}
    monkeypatch.setattr(pst, "correct_ocr_size", lambda name, size: ("200/160", []))
    cache = {"2 00/160": _cls(status="rejected", adoptable=False,
                              format_class="unknown", detail="spans пусты")}
    parsed, skip = pst.parse_row(row, DEFAULTS, llm_cache=cache)
    assert parsed is None
    assert skip["reason"] == lsc.REASON_LLM_REJECTED
    assert skip["llm_detail"] == "spans пусты"
