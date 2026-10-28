def test_detect_product_type_knows_vozd_abbreviation():
    import process_specification_table as pst
    full = pst.detect_product_type("Воздуховод оцинкованный 150х150 1.25")
    abbr = pst.detect_product_type("Возд.оц 0.5мм 150х150 1.25")
    assert abbr is not None and abbr == full


def _skipped(name="Возд.оц 0.5мм 150х150 1.25", reason="Не удалось распознать размер / тип",
             unit="шт", quantity=21):
    return {"name": name, "size": "", "unit": unit, "quantity": quantity, "reason": reason}


def test_qualifying_skipped_whitelist():
    import llm_row_fallback as rf
    rows = [
        _skipped(),                                    # да
        _skipped(reason="Отсутствует размер"),          # да
        _skipped(reason="Отсутствует размер", unit="м²"),  # нет — м²
        _skipped(reason="Единица м² — агрегатная"),      # нет
        _skipped(reason="Покупная позиция (клапан)"),    # нет
        _skipped(reason="Не удалось распознать количество: -"),  # нет
    ]
    got = rf.qualifying_skipped(rows)
    assert len(got) == 2


def test_validate_normalized_keeps_qty_unit_and_core():
    import llm_row_fallback as rf
    orig = _skipped()
    got = rf.validate_normalized(orig, {"name": "Воздуховод оцинкованный 150х150", "size": "150х150"})
    assert got["quantity"] == 21 and got["unit"] == "шт"
    assert "150х150" in got["name"]


def test_validate_normalized_drops_full_rename():
    import llm_row_fallback as rf
    assert rf.validate_normalized(_skipped(), {"name": "Клапан приточный", "size": ""}) is None


def test_validate_normalized_drops_unconfirmed_size():
    import llm_row_fallback as rf
    got = rf.validate_normalized(_skipped(), {"name": "Воздуховод оцинкованный 150х150", "size": "ф315"})
    assert got is not None and got["size"] == ""  # размер не из исходника — обнулили, строка жива


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


def test_business_skips_untouched_even_when_enabled(monkeypatch):
    # Review Focus #5
    import llm_row_fallback as rf
    monkeypatch.setattr(rf, "row_fallback_enabled", lambda cfg=None: True)
    called = []
    monkeypatch.setattr(rf, "normalize_rows", lambda rows, **kw: called.append(rows) or [])
    ok = [{"article": "1-1-1"}]
    business = [_skipped(reason="Покупная позиция (клапан)"),
                _skipped(reason="Артикул 20-1 не входит в разрешённый список")]
    rf.run_row_fallback(ok, business)
    assert called == []  # нормализатор не вызывался — квалифицируемых строк нет


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
