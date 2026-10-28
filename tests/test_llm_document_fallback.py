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


def test_config_has_doc_fallback_section():
    import config
    cfg = config.get_config()
    df = cfg.get("llm", {}).get("doc_fallback", {})
    assert df.get("enabled") is False
    assert 0 < df.get("skip_threshold", 0) <= 1


def test_web_app_xml_regenerated_includes_llm_recovered_rows(monkeypatch, tmp_path):
    # Review fix: восстановленные LLM-строки должны попадать в скачиваемый
    # order.xml — web_app перегенерирует xml_text из merged success_rows.
    import web_app
    import llm_document_fallback as f
    import process_specification_table as pst
    monkeypatch.setattr(f, "doc_fallback_enabled", lambda cfg=None: True)
    monkeypatch.setattr(f, "extract_document_text", lambda p, max_chars=60000: "txt")
    monkeypatch.setattr(f, "parse_document_with_llm",
                        lambda text, **kw: [{"name": "Воздуховод", "size": "ф125",
                                             "unit": "шт", "quantity": 2}])
    orig = pst.process_rows
    monkeypatch.setattr(pst, "process_rows",
                        lambda rows, **kw: orig(rows, run_llm_size_classify=False))
    pdf = tmp_path / "f.pdf"; pdf.write_bytes(b"%PDF-1.4")
    header = {"numberDate": "", "numberOrder": "TEST-1", "INN": "", "customer": "",
              "email": "", "phone": "", "contact": ""}
    # Детерминированный каскад всё пропустил — LLM восстанавливает 1 строку
    xml_text, skipped, success_rows = pst.process_rows([], header=header)
    success_rows, skipped, meta = f.run_doc_fallback(
        str(pdf), success_rows, skipped or [{"name": "x", "reason": "bad"}])
    assert meta["recovered"] == 1
    xml_text = web_app._regenerate_xml_if_llm_recovered(header, xml_text, success_rows, meta)
    assert "ф125" in xml_text
    assert "<quantity>2</quantity>" in xml_text


def test_web_app_xml_unchanged_without_llm_recovery():
    # recovered == 0 — исходный xml_text не трогаем.
    import web_app
    xml_text = "<Order/>"
    meta = {"triggered": True, "recovered": 0, "escalation": None}
    assert web_app._regenerate_xml_if_llm_recovered({}, xml_text, [], meta) is xml_text


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
    # 3 success + 1 skipped: skip_ratio 0.25 ≤ 0.3 — порог не превышен
    ok = [{"article": "1-1-1", "quantity": 1}] * 3
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
    # LLM-строка совпала с детерминированной по (comment, quantity) — дублей нет,
    # исходный комментарий не мутирован (регистр сохранён)
    assert meta["recovered"] == 0
    assert len(success) == 1
    assert success[0]["comment"] == det[0]["comment"]
    assert len([s for s in success
                if s.get("comment", "").lower().strip() == det[0]["comment"].lower().strip()]) == 1


def test_run_doc_fallback_extract_failure_escalates(monkeypatch, tmp_path):
    # Ошибка/пустота извлечения текста при полном промахе (success пуст) —
    # детерминированная эскалация «нет текстового слоя», иначе документ
    # молча не доезжает до менеджера.
    import llm_document_fallback as f
    monkeypatch.setattr(f, "doc_fallback_enabled", lambda cfg=None: True)

    def boom(p, max_chars=60000):
        raise RuntimeError("pdfplumber crashed")
    monkeypatch.setattr(f, "extract_document_text", boom)
    pdf = tmp_path / "f.pdf"; pdf.write_bytes(b"%PDF-1.4")
    bad = [{"name": "x", "reason": "bad"}]
    success, skipped, meta = f.run_doc_fallback(str(pdf), [], bad)
    assert success == [] and skipped is bad
    assert meta["triggered"] is True and meta["recovered"] == 0
    assert meta["skip_ratio"] == 1.0 and meta["skip_ratio_post"] == 1.0
    assert meta["escalation"] == "нет текстового слоя / OCR не дал результата"


def test_run_doc_fallback_empty_text_escalates(monkeypatch, tmp_path):
    # Пустой текст (PDF без текстового слоя) — то же, что и падение извлечения.
    import llm_document_fallback as f
    monkeypatch.setattr(f, "doc_fallback_enabled", lambda cfg=None: True)
    monkeypatch.setattr(f, "extract_document_text", lambda p, max_chars=60000: "  ")
    pdf = tmp_path / "f.pdf"; pdf.write_bytes(b"%PDF-1.4")
    success, skipped, meta = f.run_doc_fallback(str(pdf), [], [{"name": "x", "reason": "bad"}])
    assert meta["triggered"] is True and meta["recovered"] == 0
    assert meta["escalation"] == "нет текстового слоя / OCR не дал результата"


def test_run_doc_fallback_parse_failure_returns_deterministic(monkeypatch, tmp_path):
    # Сбой парсинга (kimi недоступен/вернул мусол) → исходные success/skipped
    # без изменений, meta triggered=True recovered=0, эскалация НЕ ставится
    # (анти-спам при массовом отказе бэкенда).
    import llm_document_fallback as f
    monkeypatch.setattr(f, "doc_fallback_enabled", lambda cfg=None: True)
    monkeypatch.setattr(f, "extract_document_text", lambda p, max_chars=60000: "txt")

    def boom(text, **kw):
        raise RuntimeError("kimi unavailable")
    monkeypatch.setattr(f, "parse_document_with_llm", boom)
    pdf = tmp_path / "f.pdf"; pdf.write_bytes(b"%PDF-1.4")
    ok = [{"article": "1-1-1", "quantity": 1, "comment": "Воздуховод ф125"}]
    bad = [{"name": "x", "reason": "bad"}]
    success, skipped, meta = f.run_doc_fallback(str(pdf), ok, bad)
    assert success is ok and skipped is bad
    assert meta["triggered"] is True and meta["recovered"] == 0
    assert meta["skip_ratio"] == 0.5 and meta["skip_ratio_post"] == 0.5
    assert meta["escalation"] is None


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
