from api import process_specification_file


def test_integration_csv_to_xml():
    csv = (
        "Наименование;Размер;Ед;Количество;Материал;Толщина\n"
        "Воздуховод;160;м;10;оцинкованная;0.8\n"
        "Воздуховод;200;м;5;оцинкованная;0.8\n"
    )
    result = process_specification_file(
        csv.encode("utf-8-sig"),
        "spec.csv",
        header={"order_name": "INTEGRATION"},
    )
    assert "<Order>" in result.xml
    assert "<productRow>" in result.xml
    assert "1-1-2" in result.xml
    assert result.skipped == []


def test_integration_csv_with_skipped():
    csv = (
        "Наименование;Размер;Ед;Количество;Материал;Толщина\n"
        "Воздуховод;160;м;10;оцинкованная;0.8\n"
        "Непонятная позиция;;;1;;\n"
    )
    result = process_specification_file(
        csv.encode("utf-8-sig"),
        "spec.csv",
        header={},
    )
    assert "<Order>" in result.xml
    assert len(result.skipped) == 1
    assert "reason" in result.skipped[0]
