"""Тесты llm_document_fallback: извлечение текста и LLM-парсинг по spans.

ВАЖНО: span-офсеты скорректированы относительно брифа — там они не
соответствовали TEXT (например, name_span [2, 29] даёт «Воздуховод
прямоугольный 60» вместо ожидаемого «Воздуховод прямоугольный», а
quantity_span [38, 40] покрывал «м\\n», а не «12»). Все assert-ы брифа
неизменны; значения полей rows вычисляются срезами TEXT[...] как в брифе.
"""

TEXT = ("1 Воздуховод прямоугольный 600x400 12 м\n"
        "2 Отвод 90 ф125 6 шт\n")


def _stdout(rows_json: str) -> str:
    import json as j
    return j.dumps({"role": "assistant", "content": rows_json})


def _rows_with_spans():
    rows = [
        {"name": TEXT[2:26], "name_span": [2, 26], "size": TEXT[27:34],
         "size_span": [27, 34], "unit": "м", "quantity": 12,
         "quantity_span": [35, 37]},
        {"name": TEXT[42:50], "name_span": [42, 50], "size": TEXT[51:55],
         "size_span": [51, 55], "unit": "шт", "quantity": 6,
         "quantity_span": [56, 57]},
    ]
    import json as j
    return j.dumps(rows, ensure_ascii=False)


def test_parse_document_with_llm_extracts_rows_from_spans():
    import llm_document_fallback as f
    got = f.parse_document_with_llm(TEXT, runner=lambda p, c: _stdout(_rows_with_spans()))
    assert len(got) == 2
    assert got[0]["name"] == "Воздуховод прямоугольный"
    assert got[0]["quantity"] == 12 and got[0]["unit"] == "м"
    assert got[1]["size"] == "ф125"


def test_parse_document_with_llm_drops_invalid_spans():
    import llm_document_fallback as f
    bad = [{"name": "x", "name_span": [0, 9999], "size": "", "size_span": [0, 0],
            "unit": "шт", "quantity": 1, "quantity_span": [0, 1]}]
    import json as j
    got = f.parse_document_with_llm(TEXT, runner=lambda p, c: _stdout(j.dumps(bad)))
    assert got == []


def test_doc_fallback_enabled_requires_both_flags(monkeypatch):
    import llm_document_fallback as f
    import llm_size_classifier as lsc
    monkeypatch.setattr(lsc, "llm_enabled", lambda cfg=None: False)
    assert f.doc_fallback_enabled() is False


def test_build_escalation_reason_detects_m2():
    import llm_document_fallback as f
    reason = f.build_escalation_reason(
        [{"reason": "Единица м² — агрегатная площадь"}, {"reason": "Единица м² — агрегатная площадя"}])
    assert "м²" in reason


def test_parse_document_with_llm_returns_empty_on_runner_error():
    import llm_document_fallback as f
    def boom(prompt, cfg):
        raise RuntimeError("kimi-cli crashed")
    assert f.parse_document_with_llm(TEXT, runner=boom) == []


def test_process_rows_skips_llm_when_flag_false(monkeypatch):
    import process_specification_table as pst
    import llm_size_classifier as lsc
    called = []
    monkeypatch.setattr(lsc, "llm_enabled", lambda cfg=None: True)
    monkeypatch.setattr(lsc, "classify_sizes_batch",
                        lambda strings, cfg=None, runner=None: called.append(strings) or {})
    rows = [{"name": "Воздуховод", "size": "ф125", "unit": "шт", "quantity": 1}]
    pst.process_rows(rows, run_llm_size_classify=False)
    assert called == []


def test_run_doc_fallback_merges_and_flags(monkeypatch, tmp_path):
    import llm_document_fallback as f
    monkeypatch.setattr(f, "doc_fallback_enabled", lambda cfg=None: True)
    monkeypatch.setattr(f, "extract_document_text", lambda p, max_chars=60000: "txt")
    monkeypatch.setattr(f, "parse_document_with_llm",
                        lambda text, **kw: [{"name": "Воздуховод", "size": "ф125",
                                             "unit": "шт", "quantity": 2}])
    import process_specification_table as pst
    orig = pst.process_rows
    monkeypatch.setattr(pst, "process_rows",
                        lambda rows, **kw: orig(rows, run_llm_size_classify=False))
    pdf = tmp_path / "f.pdf"; pdf.write_bytes(b"%PDF-1.4")
    success, skipped, meta = f.run_doc_fallback(str(pdf), [], [{"name": "x", "reason": "bad"}])
    assert meta["triggered"] is True and meta["recovered"] == 1
    assert success[0]["llm_source"] is True
    assert all(r.get("llm_source") for r in success if meta["recovered"])


def test_run_doc_fallback_no_trigger_when_healthy(monkeypatch, tmp_path):
    import llm_document_fallback as f
    monkeypatch.setattr(f, "doc_fallback_enabled", lambda cfg=None: True)
    pdf = tmp_path / "f.pdf"; pdf.write_bytes(b"%PDF-1.4")
    ok = [{"article": "1-1-1", "quantity": 1}]
    success, skipped, meta = f.run_doc_fallback(str(pdf), ok, [{"name": "x", "reason": "bad"}])
    assert success is ok and meta["triggered"] is False


def test_run_doc_fallback_dedups_against_deterministic(monkeypatch, tmp_path):
    import llm_document_fallback as f
    monkeypatch.setattr(f, "doc_fallback_enabled", lambda cfg=None: True)
    monkeypatch.setattr(f, "extract_document_text", lambda p, max_chars=60000: "txt")
    monkeypatch.setattr(f, "parse_document_with_llm",
                        lambda text, **kw: [{"name": "Воздуховод", "size": "ф125",
                                             "unit": "шт", "quantity": 1}])
    import process_specification_table as pst
    orig = pst.process_rows
    monkeypatch.setattr(pst, "process_rows",
                        lambda rows, **kw: orig(rows, run_llm_size_classify=False))
    pdf = tmp_path / "f.pdf"; pdf.write_bytes(b"%PDF-1.4")
    det = [{"article": "1-1-1", "quantity": 1, "comment": "«Воздуховод» «ф125»"}]
    success, skipped, meta = f.run_doc_fallback(str(pdf), det, [{"name": "x", "reason": "bad"}] * 5)
    # LLM-строка совпала с детерминированной по (comment, quantity) — дублей нет
    assert meta["recovered"] == 0
    assert len([s for s in success if s.get("comment", "").lower().strip() == det[0]["comment"]]) == 1


def test_pipeline_order_fallback_before_blocked_filter(monkeypatch):
    # Review Focus #6: skip_ratio и fallback считаются ДО _filter_blocked_articles,
    # поэтому blocked-строки (20-1/20-2/20-9) не раздувают skip_ratio.
    import bitrix_bot.pipeline as pl
    import pandas as pd
    order = []
    df = pd.DataFrame([{"name": "Клапан 20-1", "size": "ф125", "unit": "шт", "quantity": 1}])
    monkeypatch.setattr(pl, "load_tables_from_pdf", lambda b, **kw: {"tables": {1: [df]}})
    meta = {"triggered": False, "recovered": 0, "skip_ratio": 0.0,
            "skip_ratio_post": 0.0, "escalation": None}
    monkeypatch.setattr("llm_document_fallback.run_doc_fallback",
                        lambda path, s, sk: (order.append("fallback"), (s, sk, meta))[1])
    orig_filter = pl._filter_blocked_articles
    monkeypatch.setattr(pl, "_filter_blocked_articles",
                        lambda s, sk: (order.append("filter"), orig_filter(s, sk))[1])
    pl.process_pdf_to_positions(b"%PDF-fake")
    assert order == ["fallback", "filter"]
