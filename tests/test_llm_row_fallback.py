def test_detect_product_type_knows_vozd_abbreviation():
    import process_specification_table as pst
    full = pst.detect_product_type("Воздуховод оцинкованный 150х150 1.25")
    abbr = pst.detect_product_type("Возд.оц 0.5мм 150х150 1.25")
    assert abbr is not None and abbr == full


def _skipped(name="Возд.оц 0.5мм 150х150 1.25", reason="Не удалось распознать размер / тип",
             unit="шт", quantity=21):
    return {"name": name, "size": "", "unit": unit, "quantity": quantity, "reason": reason}


TEXT_DOC = "1\nОтвод ф125 45\n2\n2\nВозд.оц 0.5мм 150х150 1.25\n21\n"


def test_qualifying_skipped_blacklist_excludes_only_m2():
    import llm_row_fallback as rf
    rows = [
        _skipped(),                                              # да (размер/тип)
        _skipped(reason="Отсутствует размер"),                    # да
        _skipped(reason="Не удалось распознать количество (0)", quantity=0, unit=""),  # да — v2
        _skipped(reason="Покупная позиция (клапан)"),             # да — пытаем (решение пользователя)
        _skipped(reason="Единица м² — агрегатная"),               # нет
        _skipped(reason="Отсутствует размер", unit="м²"),         # нет
    ]
    got = rf.qualifying_skipped(rows)
    assert len(got) == 4


def test_excluded_skipped_collects_m2_rows():
    import llm_row_fallback as rf
    m2_reason = _skipped(reason="Единица м² — агрегатная")
    m2_unit = _skipped(reason="Отсутствует размер", unit="м2")
    plain = _skipped()
    got = rf.excluded_skipped([m2_reason, plain, m2_unit])
    assert got == [m2_reason, m2_unit]


def test_validate_normalized_takes_qty_unit_from_llm_row():
    import llm_row_fallback as rf
    orig = _skipped()
    got = rf.validate_normalized(orig, {"name": "Воздуховод оцинкованный 150х150",
                                        "size": "150х150", "unit": "шт", "quantity": 3})
    assert got["quantity"] == 3 and got["unit"] == "шт"
    assert "150х150" in got["name"]


def test_validate_normalized_drops_full_rename():
    import llm_row_fallback as rf
    assert rf.validate_normalized(
        _skipped(), {"name": "Клапан приточный", "size": "", "unit": "шт", "quantity": 3}
    ) is None


def test_validate_normalized_drops_unconfirmed_size():
    import llm_row_fallback as rf
    got = rf.validate_normalized(_skipped(), {"name": "Воздуховод оцинкованный 150х150",
                                              "size": "ф315", "unit": "шт", "quantity": 3})
    assert got is not None and got["size"] == ""  # размер не из исходника — обнулили, строка жива
    assert got["quantity"] == 3  # количество — из LLM-строки


def test_validate_normalized_rejects_non_positive_qty():
    import llm_row_fallback as rf
    assert rf.validate_normalized(_skipped(), {"name": "Воздуховод оцинкованный 150х150",
                                               "size": "150х150", "unit": "шт", "quantity": 0}) is None


def test_validate_normalized_allows_m2_unit_from_llm():
    # Чёрный список м² — этап qualifying; валидатор принимает м², если исходная
    # единица пустая или сама агрегатная (гард смены шт/м → м² — отдельный тест).
    import llm_row_fallback as rf
    got = rf.validate_normalized(_skipped(unit=""), {"name": "Воздуховод оцинкованный 150х150",
                                                     "size": "150х150", "unit": "м²", "quantity": 3})
    assert got is not None and got["unit"] == "м²"


def test_validate_normalized_accepts_llm_qty_with_text_anchor():
    import llm_row_fallback as rf
    orig = _skipped(name="Отвод ф125 45", reason="Не удалось распознать количество (0)", quantity=0, unit="")
    got = rf.validate_normalized(orig, {"name": "Отвод ф125 45", "size": "ф125", "unit": "шт", "quantity": 2}, TEXT_DOC)
    assert got["quantity"] == 2 and got["unit"] == "шт"


def test_validate_normalized_rejects_unconfirmed_qty():
    import llm_row_fallback as rf
    orig = _skipped(name="Отвод ф125 45", quantity=0, unit="")
    assert rf.validate_normalized(orig, {"name": "Отвод ф125 45", "size": "ф125", "unit": "шт", "quantity": 99}, TEXT_DOC) is None


def test_validate_normalized_rejects_qty_when_name_not_in_text():
    import llm_row_fallback as rf
    orig = _skipped(name="Отвод ф125 45", quantity=0, unit="")
    assert rf.validate_normalized(orig, {"name": "Отвод ф125 45", "size": "ф125", "unit": "шт", "quantity": 2}, "вообще другой документ") is None


def test_validate_normalized_anchors_qty_to_original_name():
    # Якорь количества — по ИСХОДНОМУ имени: нормализатор переименовывает
    # («Возд.оц…» → «Воздуховод оцинкованный…»), канонического имени нет в тексте.
    import llm_row_fallback as rf
    orig = _skipped()
    got = rf.validate_normalized(orig, {"name": "Воздуховод оцинкованный 150х150",
                                        "size": "150х150", "unit": "шт", "quantity": 21}, TEXT_DOC)
    assert got is not None and got["quantity"] == 21


def test_validate_normalized_rejects_m2_unit_change_from_piece_or_meter():
    # Гард смены единицы: шт/м → м2/м² — отброс (агрегат LLM не навязывает).
    import llm_row_fallback as rf
    for orig_unit in ("шт", "м"):
        orig = _skipped(unit=orig_unit)
        assert rf.validate_normalized(orig, {"name": "Воздуховод оцинкованный 150х150",
                                             "size": "150х150", "unit": "м²", "quantity": 3}) is None
        assert rf.validate_normalized(orig, {"name": "Воздуховод оцинкованный 150х150",
                                             "size": "150х150", "unit": "м2", "quantity": 3}) is None


def test_validate_normalized_allows_m2_unit_when_original_empty_or_m2():
    import llm_row_fallback as rf
    for orig_unit in ("", "м2", "м²"):
        orig = _skipped(unit=orig_unit)
        got = rf.validate_normalized(orig, {"name": "Воздуховод оцинкованный 150х150",
                                            "size": "150х150", "unit": "м²", "quantity": 3})
        assert got is not None and got["unit"] == "м²"


def test_validate_normalized_allows_piece_meter_swap():
    # Смена шт↔м — норма, LLM имеет право уточнить единицу.
    import llm_row_fallback as rf
    assert rf.validate_normalized(_skipped(unit="шт"),
                                  {"name": "Воздуховод оцинкованный 150х150", "size": "150х150",
                                   "unit": "м", "quantity": 3}) is not None
    assert rf.validate_normalized(_skipped(unit="м"),
                                  {"name": "Воздуховод оцинкованный 150х150", "size": "150х150",
                                   "unit": "шт", "quantity": 3}) is not None


def test_excluded_skipped_strips_reason_whitespace():
    import llm_row_fallback as rf
    ws = _skipped(reason="  Единица м² — агрегатная")
    assert rf.excluded_skipped([ws]) == [ws]


def test_consume_original_requires_strong_token_overlap():
    import llm_row_fallback as rf
    remaining = [_skipped(name="Заглушка 150")]
    # только числовое совпадение «150» — недостаточно
    row = {"name": "Воздуховод оцинкованный 150", "size": "", "comment": "", "quantity": 1}
    assert rf._consume_original(row, remaining) is None and len(remaining) == 1
    # ≥2 общих токена — достаточно
    row2 = {"name": "Заглушка торцевая 150 2", "size": "", "comment": "", "quantity": 1}
    assert rf._consume_original(row2, remaining) is not None and remaining == []
    # точный мэтч имени — первым приоритетом
    remaining2 = [_skipped(name="Отвод ф125 45")]
    row3 = {"name": "Отвод ф125 45", "size": "ф125", "comment": "", "quantity": 2}
    assert rf._consume_original(row3, remaining2) is not None and remaining2 == []


def test_validate_normalized_rejects_bad_unit():
    import llm_row_fallback as rf
    orig = _skipped(name="Отвод ф125 45", quantity=0, unit="")
    assert rf.validate_normalized(orig, {"name": "Отвод ф125 45", "size": "ф125", "unit": "компл", "quantity": 2}, TEXT_DOC) is None


def _stream(content: str) -> str:
    import json
    return json.dumps({"role": "assistant", "content": content}) + "\n"


def test_normalize_rows_anchors_qty_to_text_and_updates_prompt():
    import json
    import llm_row_fallback as rf
    rows = [_skipped(name="Отвод ф125 45", reason="Не удалось распознать количество (0)", quantity=0, unit="")]
    good = json.dumps([{"name": "Отвод ф125 45", "size": "ф125", "unit": "шт", "quantity": 2}], ensure_ascii=False)
    prompts = []

    def runner(prompt, cfg):
        prompts.append(prompt)
        return _stream(good)

    got = rf.normalize_rows(rows, cfg={"max_retries": 0}, runner=runner, text=TEXT_DOC)
    assert len(got) == 1 and got[0]["quantity"] == 2 and got[0]["unit"] == "шт"
    assert "quantity" in prompts[0] and "unit" in prompts[0]


def test_normalize_rows_rejects_qty_not_in_text():
    import json
    import llm_row_fallback as rf
    rows = [_skipped(name="Отвод ф125 45", reason="Не удалось распознать количество (0)", quantity=0, unit="")]
    bad = json.dumps([{"name": "Отвод ф125 45", "size": "ф125", "unit": "шт", "quantity": 99}], ensure_ascii=False)
    got = rf.normalize_rows(rows, cfg={"max_retries": 0}, runner=lambda p, c: _stream(bad), text=TEXT_DOC)
    assert got == []


def test_run_row_fallback_recovers_vozd_row(monkeypatch, tmp_path):
    import llm_row_fallback as rf
    import process_specification_table as pst
    monkeypatch.setattr(rf, "row_fallback_enabled", lambda cfg=None: True)
    monkeypatch.setattr(rf, "normalize_rows",
                        lambda rows, **kw: [{"name": "Воздуховод оцинкованный 150х150",
                                             "size": "150х150", "unit": r["unit"],
                                             "quantity": r["quantity"]} for r in rows])
    ok = [{"article": "1-1-1", "quantity": 1, "comment": "«Другое» «ф100»"}]
    success, skipped, meta = rf.run_row_fallback(ok, [_skipped()] * 4)
    assert meta["row_fallback_triggered"] is True and meta["row_fallback_recovered"] >= 1
    assert any(s.get("llm_source") for s in success)
    # детерминированная строка не продублирована и не потеряна
    assert any(s.get("comment") == "«Другое» «ф100»" and not s.get("llm_source") for s in success)


def test_run_row_fallback_noop_when_disabled(monkeypatch):
    import llm_row_fallback as rf
    monkeypatch.setattr(rf, "row_fallback_enabled", lambda cfg=None: False)
    ok = [{"article": "1-1-1"}]
    bad = [_skipped()]
    success, skipped, meta = rf.run_row_fallback(ok, bad)
    assert success is ok and skipped is bad and meta["row_fallback_triggered"] is False


def test_run_row_fallback_runner_failure_returns_deterministic(monkeypatch):
    import llm_row_fallback as rf
    monkeypatch.setattr(rf, "row_fallback_enabled", lambda cfg=None: True)
    def boom(rows, **kw):
        raise RuntimeError("kimi down")
    monkeypatch.setattr(rf, "normalize_rows", boom)
    ok = [{"article": "1-1-1"}]
    success, skipped, meta = rf.run_row_fallback(ok, [_skipped()])
    assert success is ok and meta["row_fallback_recovered"] == 0


def test_run_row_fallback_cleans_qty_broken_twins(monkeypatch, tmp_path):
    import llm_row_fallback as rf
    monkeypatch.setattr(rf, "row_fallback_enabled", lambda cfg=None: True)
    # герметизация: без реального pdfminer на мусорном pdf
    monkeypatch.setattr(rf.llm_document_fallback, "extract_document_text",
                        lambda p, max_chars=60000: "")
    twin = _skipped(name="Отвод ф125 45", reason="Не удалось распознать количество (0)", quantity=0, unit="")
    def fake_norm(rows, **kw):
        return [{"name": r["name"], "size": "ф125", "unit": "шт", "quantity": 2} for r in rows]
    monkeypatch.setattr(rf, "normalize_rows", fake_norm)
    pdf = tmp_path / "f.pdf"; pdf.write_bytes(b"%PDF-1.4")
    success, skipped, meta = rf.run_row_fallback([], [twin], pdf_path=str(pdf))
    assert meta["row_fallback_recovered"] == 1
    assert skipped == []  # двойник выведен из пропущенных

def test_run_row_fallback_keeps_non_twin_skips(monkeypatch, tmp_path):
    import llm_row_fallback as rf
    monkeypatch.setattr(rf, "row_fallback_enabled", lambda cfg=None: True)
    monkeypatch.setattr(rf, "normalize_rows", lambda rows, **kw: [])
    monkeypatch.setattr(rf.llm_document_fallback, "extract_document_text",
                        lambda p, max_chars=60000: "")
    other = _skipped(name="Что-то иное", reason="Покупная позиция (клапан)")
    pdf = tmp_path / "f.pdf"; pdf.write_bytes(b"%PDF-1.4")
    _, skipped, meta = rf.run_row_fallback([], [other], pdf_path=str(pdf))
    assert skipped == [other] and meta["row_fallback_recovered"] == 0

def test_config_has_row_fallback_section():
    import config
    df = config.get_config().get("llm", {}).get("row_fallback", {})
    assert df.get("enabled") is True
    assert df.get("timeout", 0) > 0


def test_order_independence_row_fallback_without_doc_trigger(monkeypatch, tmp_path):
    # Review Focus #4: doc-fallback не триггерится (success есть, ratio низкий),
    # но qualifying skipped нормализуется
    import llm_row_fallback as rf
    import process_specification_table as pst
    monkeypatch.setattr(rf, "row_fallback_enabled", lambda cfg=None: True)
    monkeypatch.setattr(rf, "normalize_rows",
                        lambda rows, **kw: [{"name": "Воздуховод оцинкованный 150х150",
                                             "size": "150х150", "unit": r["unit"],
                                             "quantity": r["quantity"]} for r in rows])
    ok = [{"article": "1-1-1", "quantity": 5, "comment": "«Воздуховод» «ф100»"}]
    success, skipped, meta = rf.run_row_fallback(ok, [_skipped()])
    assert meta["row_fallback_recovered"] == 1


def test_non_m2_skips_sent_to_llm_in_v2(monkeypatch):
    # v2: чёрный список вместо whitelist — покупные и прочие НЕ-м² строки идут в LLM
    import llm_row_fallback as rf
    monkeypatch.setattr(rf, "row_fallback_enabled", lambda cfg=None: True)
    called = []
    monkeypatch.setattr(rf, "normalize_rows", lambda rows, **kw: called.append(rows) or [])
    ok = [{"article": "1-1-1"}]
    business = [_skipped(reason="Покупная позиция (клапан)"),
                _skipped(reason="Артикул 20-1 не входит в разрешённый список")]
    rf.run_row_fallback(ok, business)
    assert called == [business]  # обе строки квалифицируются — не м²


def test_m2_skips_not_sent_to_llm_in_v2(monkeypatch):
    # v2: м²-строки (причина «Единица м²» или единица м2/м²) остаются вне LLM
    import llm_row_fallback as rf
    monkeypatch.setattr(rf, "row_fallback_enabled", lambda cfg=None: True)
    called = []
    monkeypatch.setattr(rf, "normalize_rows", lambda rows, **kw: called.append(rows) or [])
    ok = [{"article": "1-1-1"}]
    m2_rows = [_skipped(reason="Единица м² — агрегатная"),
               _skipped(reason="Отсутствует размер", unit="м²")]
    success, skipped, meta = rf.run_row_fallback(ok, m2_rows)
    assert called == [] and meta["row_fallback_triggered"] is False
    assert skipped == m2_rows


def test_blocked_20x_never_in_success_after_row_fallback(monkeypatch):
    # Review Focus #5: blocked-артикулы фильтруются ПОСЛЕ row-fallback.
    # Реальный row-fallback поднимает skipped-строку «Клапан противопожарный»
    # (детерминированный мэппинг даёт 20-1, llm_source=True), но финальный
    # фильтр _filter_blocked_articles в process_pdf_to_positions обязан убрать
    # её из success и положить в skipped с not_trading.
    import bitrix_bot.pipeline as pl
    import llm_row_fallback as rf
    import pandas as pd
    df = pd.DataFrame([{"name": "Клапан противопожарный", "size": "",
                        "unit": "шт", "quantity": 1}])
    monkeypatch.setattr(pl, "load_tables_from_pdf", lambda b, **kw: {"tables": {1: [df]}})
    doc_meta = {"triggered": False, "recovered": 0, "skip_ratio": 1.0,
                "skip_ratio_post": 1.0, "escalation": None}
    monkeypatch.setattr("llm_document_fallback.run_doc_fallback",
                        lambda path, s, sk: (s, sk, doc_meta))
    monkeypatch.setattr("llm_document_fallback.extract_document_text",
                        lambda p, max_chars=60000: "")
    monkeypatch.setattr(rf, "row_fallback_enabled", lambda cfg=None: True)
    monkeypatch.setattr(rf, "normalize_rows",
                        lambda rows, **kw: [{"name": r["name"], "size": "ф125",
                                             "unit": "шт", "quantity": 1}
                                            for r in rows])
    success, skipped, meta = pl.process_pdf_to_positions(b"%PDF-fake")
    assert meta["row_fallback_recovered"] == 1  # строка реально поднята фолбэком
    assert not any(s.get("article") == "20-1" for s in success)
    blocked = [s for s in skipped if s.get("article") == "20-1"]
    assert len(blocked) == 1 and blocked[0].get("not_trading") is True


def test_run_pipeline_partial_warning_via_row_fallback_only(monkeypatch):
    # doc-fallback не триггерился, но row-fallback сработал и skip_ratio высокий
    import bitrix_bot.pipeline as pl
    meta = {"triggered": False, "recovered": 0, "skip_ratio": 0.9, "skip_ratio_post": 0.9,
            "escalation": None, "row_fallback_triggered": True, "row_fallback_recovered": 1}
    monkeypatch.setattr(
        pl, "process_pdf_to_positions",
        lambda b: ([{"article": "1-1-1", "quantity": 1}], [{"name": "x", "reason": "r"}] * 9, meta))
    monkeypatch.setattr(pl, "load_order_to_1c", lambda *a, **k: ("1", [], []))
    res = pl.run_pipeline(b"pdf", "f.pdf", "c", "http://x")
    assert res.row_fallback_recovered == 1
    assert res.partial_warning and "частично" in res.partial_warning
