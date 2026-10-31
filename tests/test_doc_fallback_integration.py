# tests/test_doc_fallback_integration.py
import llm_row_fallback

FIX = "tests/fixtures"

def test_159_recovers_rows_with_llm(monkeypatch):
    import llm_document_fallback as f
    import process_specification_table as pst
    monkeypatch.setattr(f, "doc_fallback_enabled", lambda cfg=None: True)
    # Герметичность: row-fallback отключаем — иначе qualifying skipped-строки
    # пойдут в живой normalize_rows (kimi), тест станет медленным и флаки.
    monkeypatch.setattr(llm_row_fallback, "row_fallback_enabled", lambda cfg=None: False)
    # Герметичность (pre-existing): реальный process_rows дёргает живой LLM
    # size-classifier (13 вызовов kimi, ~2 мин) — тест про doc-fallback, не про
    # классификацию. Тот же приём, что в test_web_app_xml_regenerated_*.
    orig_process_rows = pst.process_rows
    monkeypatch.setattr(pst, "process_rows",
                        lambda rows, **kw: orig_process_rows(rows, run_llm_size_classify=False))
    text = f.extract_document_text(f"{FIX}/fallback_nii_kukuruzy.pdf")
    assert len(text) > 500  # Review Focus #1: текст извлёкся
    def fake_parse(text, **kw):
        # spans строим на реальном тексте: берём подстроки документа
        import json as j
        rows = []
        for name_sub, size_sub, qty in [("Отвод ф125 45", "ф125", 2),
                                        ("Отвод 200х150 90", "200х150", 1)]:
            i = text.find(name_sub)
            if i >= 0:
                rows.append({"name": name_sub, "name_span": [i, i + len(name_sub)],
                             "size": size_sub, "size_span": [i + text[i:].find(size_sub),
                                                             i + text[i:].find(size_sub) + len(size_sub)],
                             "unit": "шт", "quantity": qty,
                             "quantity_span": [0, 1]})
        return rows
    monkeypatch.setattr(f, "parse_document_with_llm", fake_parse)
    import bitrix_bot.pipeline as pl
    success, skipped, meta = pl.process_pdf_to_positions(open(f"{FIX}/fallback_nii_kukuruzy.pdf", "rb").read())
    assert len(success) > 0, "LLM-fallback должен спасти хотя бы часть строк заявки 159"
    assert meta["recovered"] > 0

def test_160_escalates_m2_smeta(monkeypatch):
    import llm_document_fallback as f
    monkeypatch.setattr(f, "doc_fallback_enabled", lambda cfg=None: True)
    # Герметичность: без патча qualifying skipped-строки уходят в живой
    # normalize_rows (kimi) — до ~360с на тест и риск, что LLM восстановит
    # строки и assert success == [] упадёт.
    monkeypatch.setattr(llm_row_fallback, "row_fallback_enabled", lambda cfg=None: False)
    monkeypatch.setattr(f, "parse_document_with_llm", lambda text, **kw: [])
    import bitrix_bot.pipeline as pl
    success, skipped, meta = pl.process_pdf_to_positions(open(f"{FIX}/fallback_525_smety.pdf", "rb").read())
    assert success == []
    assert meta["escalation"] and "м²" in meta["escalation"]

def test_159_no_text_layer_path():
    import llm_document_fallback as f
    text = f.extract_document_text(f"{FIX}/fallback_nii_kukuruzy.pdf")
    assert isinstance(text, str)
