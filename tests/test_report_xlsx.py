import io

from openpyxl import load_workbook

from bitrix_bot.pipeline import PipelineResult
from report_xlsx import (
    SHEET_ERRORS, SHEET_LOADED, SHEET_SKIPPED, SHEET_TRADING,
    build_excel_report, is_trading_skip,
)


def _sample_result() -> PipelineResult:
    return PipelineResult(
        file_name="spec.pdf",
        order_number="839",
        loaded=[
            {"article": "1-1-1", "params": {"A0": 300, "B0": 200}, "quantity": 2,
             "material_code": "1", "thickness": 0.8,
             "connection_0": "6", "connection_1": "2",
             "connection_2": "0", "connection_3": "0",
             "system": "П1", "comment": "Воздуховод 300x200"},
        ],
        skipped=[
            {"name": "Гибкий воздуховод Ф125", "size": "Ф125", "unit": "м",
             "quantity": 10, "material": "оцинкованная", "thickness": 0.8,
             "reason": "Покупная позиция (гибкий воздуховод/трубопровод/изоляция) — завод не производит"},
            {"name": "Клапан ОЗ-60-НО-400*200", "size": "400x200", "unit": "шт",
             "quantity": 1, "material": "оцинкованная", "thickness": 0.8,
             "reason": "Покупная арматура (брендовый клапан/шумоглушитель) — завод не производит"},
            {"name": "Неизвестная деталь", "size": "100x100", "unit": "шт",
             "quantity": 3, "material": "оцинкованная", "thickness": 0.8,
             "reason": "Не удалось распознать артикул"},
            {"raw_name": "Диффузор SR-P 300", "model": "SR-P-300",
             "reason": "Оборудование не производится"},
        ],
        errors_1c=["Нет цены у артикула 1-1-1"],
        warnings_1c=["Проверьте шину"],
    )


def test_sheets_and_split():
    wb = load_workbook(io.BytesIO(build_excel_report(_sample_result())))
    assert wb.sheetnames == [SHEET_LOADED, SHEET_SKIPPED, SHEET_TRADING, SHEET_ERRORS]

    loaded_ws = wb[SHEET_LOADED]
    headers = [c.value for c in loaded_ws[1]]
    assert headers[:6] == ["Артикул", "A", "B", "D", "Кол-во", "Материал"]
    assert "Соед. 0" in headers and "Соед. 3" in headers
    row = [c.value for c in loaded_ws[2]]
    assert row[0] == "1-1-1" and row[1] == 300 and row[2] == 200

    skipped_ws = wb[SHEET_SKIPPED]
    names = [r[0] for r in skipped_ws.iter_rows(min_row=2, values_only=True)]
    assert names == ["Неизвестная деталь"]  # только не-перекупное

    trading_ws = wb[SHEET_TRADING]
    tnames = [r[0] for r in trading_ws.iter_rows(min_row=2, values_only=True)]
    assert "Гибкий воздуховод Ф125" in tnames
    assert "Диффузор SR-P 300" in tnames  # raw_name-строка тоже перекупное
    theaders = [c.value for c in trading_ws[1]]
    assert "Материал" in theaders and "Толщина" in theaders and "Включить в заказ" in theaders

    errors_ws = wb[SHEET_ERRORS]
    labels = [r[0] for r in errors_ws.iter_rows(values_only=True) if r[0]]
    assert "Номер заказа" in labels
    texts = [r[-1] for r in errors_ws.iter_rows(values_only=True) if r and r[0] in ("Ошибка", "Предупреждение")]
    assert "Нет цены у артикула 1-1-1" in texts


def test_is_trading_skip():
    assert is_trading_skip({"reason": "Покупная позиция (…) — завод не производит"})
    assert is_trading_skip({"reason": "Покупная арматура (…) — завод не производит"})
    assert is_trading_skip({"raw_name": "Диффузор", "reason": "Оборудование не производится"})
    assert not is_trading_skip({"reason": "Не удалось распознать артикул"})
