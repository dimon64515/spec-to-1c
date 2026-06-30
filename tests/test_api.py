import pandas as pd

from api import (
    ProcessResult,
    read_csv_or_excel_bytes,
    process_specification_file,
)


def test_process_specification_file_csv_round_duct():
    csv = (
        "Наименование;Размер;Ед;Количество;Материал;Толщина\n"
        "Воздуховод;160;м;10;оцинкованная;0.8\n"
    )
    result = process_specification_file(
        csv.encode("utf-8-sig"),
        "spec.csv",
        header={"order_name": "TEST"},
    )
    assert isinstance(result, ProcessResult)
    assert "1-1-2" in result.xml
    assert "D00160" in result.xml
    assert result.skipped == []


def test_read_csv_or_excel_bytes_parses_csv():
    csv = "name;size;unit;quantity\nВоздуховод;160;м;10\n"
    df = read_csv_or_excel_bytes(csv.encode("utf-8-sig"), "spec.csv")
    assert isinstance(df, pd.DataFrame)
    assert len(df) == 1
    assert df.iloc[0]["name"] == "Воздуховод"
