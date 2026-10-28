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
