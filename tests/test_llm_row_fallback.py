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
