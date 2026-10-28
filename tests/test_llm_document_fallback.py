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
