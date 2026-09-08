"""Преобразование позиций пайплайна в JSON для as_order_loader (1С).

Закрывает на стороне прямого пути расхождения XML-импорта:
- материал передаётся точным именем справочника асМатериалы (рулон оц.,
  лист нерж. AISI 430, лист черн. Ст-3) — 1С не подменяет оцинковку на AISI;
- шина на соединение выбирается по правилам техотдела от наибольшего
  размера сечения: ≤350 → УГФ 65, 400–950 → УГФ 95, ≥1000 → УГФ 105
  (в XML-порте 1С порог УГФ-65 ошибочно стоит на 250 мм);
- соединения «0» не передаются — 1С оставляет дефолт изделия.

Имена материалов сверены с тестовой базой KA_vok-region_test (05.09.2026).
"""
from typing import Dict, List, Optional

# Имена справочника асМатериалы (два знака толщины после запятой!)
_OЦ = "Рулон оц.(08ПС){t}"          # 0.50–1.20
_OЦ_ШТРИПС = "Штрипс оц.(08ПС){t}"   # спирально-навивные воздуховоды 1-1-2
_NERЖ = "Лист.нерж. (AISI 430) {t}"  # 0.50/0.70/0.80/1.00
_CHERN = "Лист черн.(Ст-3){t}"       # 0.90–1.20, 3.00–5.00 (1.50 нет!)

_DIM_KEYS = ("A0", "B0", "D0", "A1", "B1", "D1")

# Реквизиты ТЧ Товары асСпецификацияЗаказа для угла/радиуса/фланца
# называются u/R/p (проверено по метаданным базы 08.09.2026). Обращение
# к ним по именам U0/R0/P0 падает, и у отводов/заглушек в заказе
# остаётся дефолтный угол. Остальные параметры совпадают без учёта
# регистра (1С регистронезависима): A1→a1, D2→d2, L2→l2 и т.д.
_PARAM_KEY_1C = {"U0": "u", "R0": "R", "P0": "p"}


def params_1c_names(params: Dict) -> Dict:
    """Переименовывает параметры под реальные имена реквизитов ТЧ Товары."""
    return {_PARAM_KEY_1C.get(k, k): v for k, v in params.items()}


def material_1c_name(material_code: str, thickness: float, article: str = None) -> str:
    """Точное имя материала в асМатериалы по коду пайплайна и толщине.

    Спирально-навивные круглые воздуховоды (1-1-2) идут из штрипса,
    остальные оцинкованные — из рулона (КП №1090: «Штрипс оц.(08ПС)0.55»
    у Ф160-3000 спирально-навивных, «Рулон оц.(08ПС)1.00» у прямошовного).
    """
    t = f"{thickness:.2f}"
    if str(material_code) == "2":
        return _NERЖ.format(t=t)
    if str(material_code) == "3":
        return _CHERN.format(t=t)
    if article == "1-1-2":
        return _OЦ_ШТРИПС.format(t=t)
    return _OЦ.format(t=t)


def shina_by_max_dim(max_dim: float) -> str:
    """Шина по правилам техотдела (от наибольшего размера сечения, мм)."""
    if max_dim <= 350:
        return "65"
    if max_dim <= 950:
        return "95"
    return "105"


def build_position(parsed: Dict) -> Dict:
    """Собирает позицию JSON-спецификации из распознанной строки пайплайна."""
    params = parsed.get("params", {}) or {}
    conns = [str(parsed.get(f"connection_{i}", "") or "") for i in range(4)]

    pos = {
        "article": parsed["article"],
        "qty": parsed["quantity"],
        "thickness": parsed["thickness"],
        "material": material_1c_name(
            parsed.get("material_code", "1"), parsed["thickness"],
            article=parsed.get("article")),
        "params": params_1c_names(params),
        "comment": parsed.get("comment", ""),
    }

    dims = [params[k] for k in _DIM_KEYS if isinstance(params.get(k), (int, float))]
    # Ключи shina/conn всегда присутствуют ("" = не задавать) — таков контракт
    # inline-загрузчика execute_code: обращение по точке к отсутствующему
    # ключу структуры в 1С бросает исключение.
    pos["shina"] = shina_by_max_dim(max(dims)) if (dims and "6" in (conns[0], conns[1])) else ""
    for i, code in enumerate(conns[:2]):
        pos[f"conn{i}"] = code if code in ("2", "6") else ""

    return pos


def build_positions(parsed_rows: List[Dict]) -> List[Dict]:
    """Собирает JSON-спецификацию из списка успешных строк пайплайна."""
    return [build_position(r) for r in parsed_rows]
