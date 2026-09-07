"""Тесты формирования итогового отчёта бота."""
from bitrix_bot.pipeline import PipelineResult
from bitrix_bot.report import build_report, build_summary


def _res(**kw):
    base = dict(
        file_name="spec.pdf",
        order_number="000000860",
        loaded=[
            {"article": "1-2-1", "quantity": 6, "comment": "", "params": {"A0": 400, "B0": 200}},
            {"article": "4-2-3", "quantity": 2, "comment": "отвод", "params": {"D0": 315}},
        ],
        skipped=[
            {"name": "Клапан КПУ", "size": "200", "quantity": "1", "reason": "нет маппинга"},
        ],
        errors_1c=[],
        warnings_1c=["Строка 1 (1-2-1): цена 0 — проверьте прайс"],
    )
    base.update(kw)
    return PipelineResult(**base)


def test_report_has_header_summary_and_lists():
    msgs = build_report(_res())
    assert len(msgs) == 1
    text = msgs[0]
    assert "Заказ 1С: №000000860" in text
    assert "Загружено: 2" in text and "Пропущено: 1" in text
    assert "Ошибок: 0" in text
    assert "1-2-1" in text and "4-2-3" in text
    assert "Клапан КПУ" in text and "нет маппинга" in text
    assert "цена 0" in text  # предупреждение 1С видно


def test_report_no_order():
    text = build_report(_res(order_number=None, errors_1c=["не найден продукт \"9-9-9\""]))[0]
    assert "Заказ НЕ создан" in text
    assert "Ошибок: 1" in text
    assert "9-9-9" in text


def test_report_splits_long_details():
    loaded = [
        {"article": f"1-2-1", "quantity": i, "comment": "x" * 100, "params": {}}
        for i in range(80)
    ]
    msgs = build_report(_res(loaded=loaded, warnings_1c=[]), limit=3500)
    assert len(msgs) > 1
    assert all(len(m) <= 3500 for m in msgs)
    assert msgs[0].startswith("Заказ 1С")
    total = "".join(msgs)
    assert total.count("1-2-1") == 80


def test_build_summary_counts_trading():
    res = PipelineResult(
        file_name="spec.pdf", order_number="839",
        loaded=[{"article": "1-1-1", "params": {}, "quantity": 2}],
        skipped=[
            {"name": "гибкий", "reason": "Покупная позиция (…) — завод не производит"},
            {"name": "мусор", "reason": "Не удалось распознать артикул"},
        ],
        errors_1c=["ошибка1"],
        warnings_1c=[],
    )
    text = build_summary(res)
    assert "№839" in text
    assert "Загружено: 1" in text
    assert "Пропущено: 1" in text
    assert "Перекупное: 1" in text
    assert "Ошибок 1С: 1" in text
