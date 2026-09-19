import pytest
from process_specification_table import parse_row, process_rows


def test_parse_row_round_duct():
    row = {"name": "Воздуховод", "size": "160", "unit": "м", "quantity": "400"}
    defaults = {"material": "оцинкованная", "thickness": "0.8"}
    parsed, skipped = parse_row(row, defaults)
    assert skipped is None
    assert parsed["article"] == "1-1-2"
    assert parsed["params"]["D0"] == 160
    assert parsed["params"]["L0"] == 3000
    # 400 м при стандартной длине 3000 мм → 134 шт (завод округляет вверх)
    assert parsed["quantity"] == 134


def test_parse_row_rect_duct():
    row = {"name": "Воздуховод", "size": "300x200", "unit": "м", "quantity": "50"}
    defaults = {"material": "оцинкованная", "thickness": "0.7"}
    parsed, skipped = parse_row(row, defaults)
    assert skipped is None
    assert parsed["article"] == "1-2-1"
    assert parsed["params"]["A0"] == 300
    assert parsed["params"]["B0"] == 200
    assert parsed["params"]["L0"] == 1250


def test_parse_row_unknown_skipped():
    row = {"name": "Непонятная деталь", "size": "", "unit": "шт", "quantity": "1"}
    defaults = {}
    parsed, skipped = parse_row(row, defaults)
    assert parsed is None
    assert "reason" in skipped


def test_parse_row_litened_nkd_rectangular():
    row = {"name": "Шумоглушитель LITENED 100-50 NKD", "size": "", "unit": "шт", "quantity": "2"}
    defaults = {"material": "оцинкованная", "thickness": "0.7"}
    parsed, skipped = parse_row(row, defaults)
    assert skipped is None
    assert parsed["article"] == "15-2-1"
    assert parsed["params"]["A0"] == 1000
    assert parsed["params"]["B0"] == 500
    assert parsed["params"]["L0"] == 1100


def test_parse_row_litened_nkk_rectangular():
    row = {"name": "Шумоглушитель LITENED 60-30 NKK", "size": "", "unit": "шт", "quantity": "1"}
    defaults = {"material": "оцинкованная", "thickness": "0.7"}
    parsed, skipped = parse_row(row, defaults)
    assert skipped is None
    assert parsed["article"] == "15-2-1"
    assert parsed["params"]["A0"] == 600
    assert parsed["params"]["B0"] == 300
    assert parsed["params"]["L0"] == 510


def test_parse_row_knk_round_with_length_code():
    row = {"name": "Шумоглушитель KNK 250/6", "size": "", "unit": "шт", "quantity": "2"}
    defaults = {"material": "оцинкованная", "thickness": "0.7"}
    parsed, skipped = parse_row(row, defaults)
    assert skipped is None
    assert parsed["article"] == "15-1-1"
    assert parsed["params"]["D0"] == 250
    assert parsed["params"]["L0"] == 600


def test_parse_row_knk_round_with_explicit_length():
    row = {"name": "Шумоглушитель круглый Ф160, L=900 мм KNK-160-900", "size": "", "unit": "шт", "quantity": "1"}
    defaults = {"material": "оцинкованная", "thickness": "0.7"}
    parsed, skipped = parse_row(row, defaults)
    assert skipped is None
    assert parsed["article"] == "15-1-1"
    assert parsed["params"]["D0"] == 160
    assert parsed["params"]["L0"] == 900


def test_parse_row_silencer_plate_15_2_4():
    row = {"name": "Пластина шумоглушителя 500x1100x200", "size": "", "unit": "шт", "quantity": "4"}
    defaults = {"material": "оцинкованная", "thickness": "0.7"}
    parsed, skipped = parse_row(row, defaults)
    assert skipped is None
    assert parsed["article"] == "15-2-4"
    assert parsed["params"]["B0"] == 500
    assert parsed["params"]["L0"] == 1100
    assert parsed["params"]["A2"] == 200


def test_parse_row_silencer_deflector_15_2_5():
    row = {"name": "Обтекатель шумоглушителя 500x200", "size": "", "unit": "шт", "quantity": "4"}
    defaults = {"material": "оцинкованная", "thickness": "0.7"}
    parsed, skipped = parse_row(row, defaults)
    assert skipped is None
    assert parsed["article"] == "15-2-5"
    assert parsed["params"]["B0"] == 500
    assert parsed["params"]["A2"] == 200


def test_trading_skip_contains_material_thickness_quantity():
    ok, skip = parse_row(
        {"name": "Гибкий воздуховод Ф125", "size": "Ф125", "unit": "м", "quantity": "10"},
        {},
    )
    assert ok is None
    assert skip["reason"].startswith("Покупная позиция")
    assert skip["material"] == "оцинкованная"
    assert skip["thickness"] == 0.8
    assert skip["quantity"] == 10


def test_bad_quantity_skip_contains_material():
    ok, skip = parse_row(
        {"name": "Отвод нержавеющий 0.7 300x200", "size": "300x200",
         "unit": "шт", "quantity": "abc"},
        {},
    )
    assert ok is None
    assert skip["reason"].startswith("Не удалось распознать количество")
    assert skip["material"] == "нержавеющая"
    assert skip["thickness"] == 0.7
    assert skip["quantity"] is None


def test_saddle_round_two_diameters_passes_d2():
    """8-1-1: второй диаметр — D2 (основной воздуховод), а не D1.

    Без D2 1С считает sqrt(d2^2 - D^2) с d2=0 и пишет
    «Геометрия недопустима» (прямой JSON-путь as_order_loader не
    подставляет default_params из product_article_mapping.json).
    """
    parsed, skipped = parse_row(
        {"name": "Врезка круглая", "size": "⌀160-⌀315", "unit": "шт", "quantity": "1"},
        {"material": "оцинкованная", "thickness": "0.8"},
    )
    assert skipped is None
    assert parsed["article"] == "8-1-1"
    assert parsed["params"]["D0"] == 160
    assert parsed["params"]["D2"] == 315
    assert "D1" not in parsed["params"]
    assert parsed["params"]["L0"] > 0


def test_saddle_round_single_diameter_d2_falls_back_to_d0():
    parsed, skipped = parse_row(
        {"name": "Врезка круглая", "size": "⌀160", "unit": "шт", "quantity": "1"},
        {"material": "оцинкованная", "thickness": "0.8"},
    )
    assert skipped is None
    assert parsed["article"] == "8-1-1"
    assert parsed["params"]["D0"] == 160
    assert parsed["params"]["D2"] == 160


def test_gost_designation_number_is_not_diameter():
    """«по ГОСТ 14918-80 Ø200/ Ø200/Ø160»: номер ГОСТа не диаметр.

    Старый парсер брал «80» из «14918-80» как суффиксный диаметр «80 Ø…»
    → D0=80; у тройника 4-1-1 ветвь d2>D0 давала в 1С
    «Геометрия недопустима» (sqrt отрицательного), у перехода 3-1-1 —
    молча неверную площадь. Реальный дефект заказа №000000871.
    """
    parsed, skipped = parse_row(
        {"name": "Тройник-90° из оцинк. стали по ГОСТ 14918-80 Ø200/ Ø200/Ø160",
         "size": "", "unit": "шт", "quantity": "1"},
        {"material": "оцинкованная", "thickness": "0.6"},
    )
    assert skipped is None
    assert parsed["article"] == "4-1-1"
    assert parsed["params"]["D0"] == 200
    assert parsed["params"]["D2"] == 160

    parsed, skipped = parse_row(
        {"name": "Переход из оцинк. стали по ГОСТ 14918-80 Ø200/ Ø160",
         "size": "", "unit": "шт", "quantity": "1"},
        {"material": "оцинкованная", "thickness": "0.6"},
    )
    assert skipped is None
    assert parsed["article"] == "3-1-1"
    assert parsed["params"]["D0"] == 200
    assert parsed["params"]["D1"] == 160


def test_tee_slash_diameters_without_prefix():
    """«Тройник-90° … 250/250/160» и «Переход … 200/125» без знаков Ø."""
    parsed, skipped = parse_row(
        {"name": "Тройник-90° из оцинк. стали по ГОСТ 14918-80 250/250/160",
         "size": "", "unit": "шт", "quantity": "1"},
        {"material": "оцинкованная", "thickness": "0.6"},
    )
    assert skipped is None
    assert parsed["article"] == "4-1-1"
    assert parsed["params"]["D0"] == 250
    assert parsed["params"]["D2"] == 160

    parsed, skipped = parse_row(
        {"name": "Переход из оцинк. стали по ГОСТ 14918-80 200/125",
         "size": "", "unit": "шт", "quantity": "1"},
        {"material": "оцинкованная", "thickness": "0.6"},
    )
    assert skipped is None
    assert parsed["article"] == "3-1-1"
    assert parsed["params"]["D0"] == 200
    assert parsed["params"]["D1"] == 125


def test_saddle_rectangular_has_no_d2():
    parsed, skipped = parse_row(
        {"name": "Врезка прямоугольная", "size": "500x300", "unit": "шт", "quantity": "1"},
        {"material": "оцинкованная", "thickness": "0.8"},
    )
    assert skipped is None
    assert parsed["article"] == "8-2-1"
    assert parsed["params"]["A0"] == 500
    assert parsed["params"]["B0"] == 300


def test_tee_branch_larger_than_main_goes_to_skipped():
    """4-1-1 с d2 > D0 (заказ №000000871: D=80, d2=160).

    Геометрия 1С невозможна: sqrt(D²/4 − d2²/4) из отрицательного и
    asin(d2/D > 1) — расчёт падает, S=0 и цена=0. Строка должна уходить
    в пропущенные с понятной причиной, а не в заказ.
    """
    xml, skipped, success = process_rows([
        {"name": "Тройник-90° из оцинк. стали по ГОСТ 14918-80 80/160",
         "size": "", "unit": "шт", "quantity": "1"},
    ])
    assert success == []
    assert len(skipped) == 1
    assert skipped[0]["article"] == "4-1-1"
    assert "врезк" in skipped[0]["reason"]
    assert "80" in skipped[0]["reason"] and "160" in skipped[0]["reason"]
    assert "4-1-1" not in xml


def test_tee_valid_diameters_pass_geometry_check():
    """Обычный тройник и равные диаметры (d2 == D0) — геометрия допустима."""
    xml, skipped, success = process_rows([
        {"name": "Тройник-90° из оцинк. стали по ГОСТ 14918-80 250/160",
         "size": "", "unit": "шт", "quantity": "2"},
        {"name": "Тройник-90° из оцинк. стали по ГОСТ 14918-80 200/200",
         "size": "", "unit": "шт", "quantity": "1"},
    ])
    assert skipped == []
    assert [r["params"]["D2"] for r in success] == [160, 200]


def test_gost_vedomost_elbow_angle_in_name():
    """ГОСТ-ведомость Заявки №1274: «Отвод 45» + size «100» → D0=100, U0=45.

    Регрессия: угол из наименования крался как диаметр (D0=45, U0=45, R0=45).
    """
    parsed, skipped = parse_row(
        {"name": "Отвод 45", "size": "100", "unit": "шт", "quantity": "2"},
        {"material": "оцинкованная", "thickness": "0.5"},
    )
    assert skipped is None
    assert parsed["article"] == "2-1-1"
    assert parsed["params"]["D0"] == 100
    assert parsed["params"]["U0"] == 45
    assert parsed["params"]["R0"] == 100  # радиус по умолчанию = диаметру

    parsed, skipped = parse_row(
        {"name": "Отвод 90", "size": "400x300", "unit": "шт", "quantity": "1"},
        {"material": "оцинкованная", "thickness": "0.7"},
    )
    assert skipped is None
    assert parsed["article"] == "2-2-2"
    assert parsed["params"]["A0"] == 400
    assert parsed["params"]["B0"] == 300
    assert parsed["params"]["U0"] == 90


def test_gost_vedomost_tee_and_transition_slash_sizes():
    """«Тройник 125/125/100», «Переход 125/100» — диаметры в наименовании."""
    parsed, skipped = parse_row(
        {"name": "Тройник 125/125/100", "size": "", "unit": "шт", "quantity": "4"},
        {"material": "оцинкованная", "thickness": "0.5"},
    )
    assert skipped is None
    assert parsed["article"] == "4-1-1"
    assert parsed["params"]["D0"] == 125
    assert parsed["params"]["D2"] == 100

    parsed, skipped = parse_row(
        {"name": "Переход 125/100", "size": "", "unit": "шт", "quantity": "2"},
        {"material": "оцинкованная", "thickness": "0.5"},
    )
    assert skipped is None
    assert parsed["article"] == "3-1-1"
    assert parsed["params"]["D0"] == 125
    assert parsed["params"]["D1"] == 100


def test_gost_vedomost_transition_rect_and_rect_round():
    """«Переход 600x200/400x200» → 3-2-5; «Переход 710/700x500» → 3-3-1.

    Регрессия: буква «д» конца слова «переход» читалась как префикс
    диаметра → ложный D0=600 и переход типа 3-3-1 без второго сечения.
    """
    parsed, skipped = parse_row(
        {"name": "Переход 600x200/400x200", "size": "", "unit": "шт",
         "quantity": "1"},
        {"material": "оцинкованная", "thickness": "0.7"},
    )
    assert skipped is None
    assert parsed["article"] == "3-2-5"
    assert parsed["params"]["A0"] == 600
    assert parsed["params"]["B0"] == 200
    assert parsed["params"]["A1"] == 400
    assert parsed["params"]["B1"] == 200

    parsed, skipped = parse_row(
        {"name": "Переход 710/700x500", "size": "", "unit": "шт",
         "quantity": "1"},
        {"material": "оцинкованная", "thickness": "0.7"},
    )
    assert skipped is None
    assert parsed["article"] == "3-3-1"
    assert parsed["params"]["A0"] == 700
    assert parsed["params"]["B0"] == 500
    assert parsed["params"]["D0"] == 710


def test_gost_vedomost_rect_tee_same_branch():
    """«Тройник 500x700/500x700» — прямоугольный тройник с ветвью равной магистрали."""
    parsed, skipped = parse_row(
        {"name": "Тройник 500x700/500x700", "size": "", "unit": "шт",
         "quantity": "1"},
        {"material": "оцинкованная", "thickness": "0.9"},
    )
    assert skipped is None
    assert parsed["article"] == "4-2-3"
    assert parsed["params"]["A0"] == 500
    assert parsed["params"]["B0"] == 700
