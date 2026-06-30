#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
process_specification_table.py

Превращает таблицу спецификации вентиляции (CSV/Excel) в XML для загрузки в 1С.

Ожидаемые столбцы:
    - name        : наименование / описание
    - size        : размер (100, 300x200, ДПУ-М 100, 4АПН 600x600 + КСД 200 и т.д.)
    - unit        : единица измерения (м, шт, м2)
    - quantity    : количество
    - material    : тип материала (оцинкованная/нержавеющая/черная) — опционально
    - thickness   : толщина (0.8, 1.0) — опционально

Пример CSV:
    name;size;unit;quantity;material;thickness
    "Воздуховод из оцинкованной стали";100;м;400;оцинкованная;0.8
    "Отвод круглый 90 градусов";D160;шт;10;оцинкованная;0.8
    "Квадратный диффузор с адаптером";4АПН 600x600 + КСД 200;шт;48;оцинкованная;0.8
"""

import json
import re
import sys
from pathlib import Path
from typing import List, Dict, Optional, Tuple

import pandas as pd

from generate_order_xml import generate_order_xml


# --- Материал ---

def material_to_code(material: str) -> str:
    """Преобразует текстовое описание материала в цифровой код для 1С.

    Коды в 1С (функция НахождениеМатериалаПоТипу):
        1 — оцинкованная сталь
        2 — нержавеющая сталь
        3 — чёрная сталь
    """
    text = str(material or "").lower()
    text = text.replace("ё", "е")

    # Прямые цифровые коды
    if text.strip() in ("1", "2", "3"):
        return text.strip()

    # По ключевым фрагментам (порядок важен: нерж/черн проверяем раньше оц)
    if "нерж" in text:
        return "2"
    if "черн" in text:
        return "3"
    if "оц" in text or "оцинк" in text:
        return "1"

    # По умолчанию оцинкованная сталь — наиболее частый случай
    return "1"


# Настройки по умолчанию
DEFAULT_ROUND_ARTICLE = "1-1-2"       # спирально-навивной
DEFAULT_RECT_ARTICLE = "1-2-1"        # прямоугольный
DEFAULT_ROUND_LENGTH_MM = 3000        # стандартная длина круглого воздуховода
DEFAULT_RECT_LENGTH_MM = 1250         # стандартная длина прямоугольного воздуховода
DEFAULT_ADAPTER_L1_MM = 50            # стандартная длина выступающего патрубка КСД


def load_allowed_articles(path: str = "articles_all.txt") -> set:
    """Загружает множество артикулов, разрешённых к выгрузке в XML."""
    allowed = set()
    p = Path(path)
    if not p.is_absolute():
        p = Path(__file__).parent / p
    if not p.exists():
        return allowed
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("---") or line.lower().startswith("total"):
            continue
        parts = line.split()
        if not parts:
            continue
        candidate = parts[0]
        if re.fullmatch(r"\d+-\d+-\d+", candidate):
            allowed.add(candidate)
    return allowed


ALLOWED_ARTICLES = load_allowed_articles()


def load_article_materials(path: str = "article_materials.json") -> Dict[str, List[str]]:
    """Загружает разрешённые коды материала (1/2/3) для каждого артикула.

    Если файл отсутствует — возвращает пустой словарь, и валидация не выполняется.
    """
    p = Path(path)
    if not p.is_absolute():
        p = Path(__file__).parent / p
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return {k: [str(c) for c in v] for k, v in data.items()}
    except Exception:
        pass
    return {}


ARTICLE_MATERIALS = load_article_materials()


def validate_material_code(article: str, code: str) -> Tuple[str, Optional[str]]:
    """Проверяет, допустим ли код материала для артикула.

    Возвращает кортеж (код, предупреждение). Если код не разрешён,
    подменяет на первый разрешённый (обычно 1 — оцинкованная).
    """
    code = str(code or "1").strip()
    allowed = ARTICLE_MATERIALS.get(article)
    if not allowed:
        return code, None
    if code in allowed:
        return code, None
    default_code = allowed[0]
    return default_code, (
        f"материал {code} не поддерживается артикулом {article}, "
        f"использован {default_code}"
    )


def default_connections(section: str, article: Optional[str] = None) -> List[str]:
    """Возвращает соединения по умолчанию для сечения/артикула.

    Круглые воздуховоды: 2 = "без соединения".
    Прямоугольные воздуховоды: 6 = "шина/уголок".
    Переход прямоугольное → круглое (3-3-x): side 0 = шина/уголок (6), side 1 = без соединения (2).
    Неиспользуемые стороны: 0.
    """
    if article and article.startswith("3-3"):
        return ["6", "2", "0", "0"]
    if section == "round":
        return ["2", "2", "0", "0"]
    return ["6", "6", "0", "0"]


# --- Определение типа продукта из текста ---

PRODUCT_TYPE_PATTERNS = [
    # Фасонные изделия — проверяем раньше воздуховодов, т.к. в их названиях часто
    # встречается слово "воздуховод(а)": "Отвод прямоугольного воздуховода ..."
    (r"\bотвод\b", "elbow"),
    (r"\bпереход\b", "transition"),
    (r"\bтройник\b", "tee"),
    (r"\bкрестовина\b", "cross"),
    (r"\bзаглушка\b", "cap"),
    (r"\bврезка\b", "saddle"),
    (r"\bутка\b", "offset"),
    (r"\bзонт\b", "roof_cap"),
    (r"\bпленум\b", "plenum"),
    (r"\bфланец\b", "flange"),
    (r"\bниппель\b", "nipple"),
    (r"\bбандаж\b", "band"),
    # Оборудование / арматура
    (r"\bдиффузор\b", "diffuser"),
    (r"\bксд\b", "ksd"),
    (r"\bрешетка\b", "grille"),
    (r"\bшумоглушитель\b", "silencer"),
    (r"\bклапан\b", "damper"),
    (r"\bшибер\b", "shutter"),
    (r"\bфильтр\b", "filter"),
    (r"\bдроссель\b", "throttle"),
    # Воздуховоды — самый общий случай
    (r"воздуховод", "duct"),
    # Агрегатные / неопределённые фасонные изделия
    (r"фасонные\s+изделия", "aggregate_fittings"),
]


def detect_product_type(name: str) -> Optional[str]:
    text = name.lower()
    # Агрегатные строки фасонных изделий проверяем первыми, чтобы не спутать с воздуховодами
    if re.search(r"фасонные\s+изделия", text):
        return "aggregate_fittings"
    for pattern, ptype in PRODUCT_TYPE_PATTERNS:
        if re.search(pattern, text):
            return ptype
    return None


def is_rectangular(text: str) -> bool:
    text = text.lower()
    if re.search(r"\bпрямоугольн", text):
        return True
    if re.search(r"\bкругл", text):
        return False
    # Если есть размер AxB — прямоугольное
    if re.search(r"\d{2,4}\s*[xх×*]\s*\d{2,4}", text):
        return True
    return False


def is_round(text: str) -> bool:
    text = text.lower()
    if re.search(r"\bкругл", text):
        return True
    if re.search(r"\bпрямоугольн", text):
        return False
    # Одиночный диаметр
    if re.search(r"(?:d|dn|ф)\s*\d+", text, re.IGNORECASE):
        return True
    return False


# --- Извлечение размеров из имени, если отдельная колонка пуста ---

def normalize_dimension_prefix(token: str) -> str:
    """Приводит D/DN/Ø/⌀ к Ф для единообразия."""
    token = token.replace("Ø", "Ф").replace("⌀", "Ф")
    token = re.sub(r"^(d|dn)\b", "ф", token, flags=re.IGNORECASE)
    return token


def extract_size_token(text: str) -> str:
    """Ищет в тексте размер в формате AxB[-L], AxBxL или D/DN/Ф/Ø/⌀D[-L]."""
    text = text.replace("х", "x").replace("×", "x").replace("*", "x")
    # Прямоугольное сечение с длиной через x: AxBxL
    m = re.search(r"\b(\d{2,5}\s*x\s*\d{2,5}\s*x\s*\d{2,5})\b", text, re.IGNORECASE)
    if m:
        return m.group(1).replace(" ", "")
    # Прямоугольное сечение с опциональной длиной: AxB-L
    m = re.search(r"\b(\d{2,5}\s*x\s*\d{2,5}(?:\s*[-_]\s*\d{2,5})?)\b", text, re.IGNORECASE)
    if m:
        return m.group(1).replace(" ", "")
    # Круглое сечение с опциональной длиной (⌀ — не буква, поэтому \b не подходит)
    m = re.search(r"(?<![\w.])(?:(?:D|DN|Ф|Ø|⌀)\s*\d{2,5}(?:\s*[-_]\s*\d{2,5})?)", text, re.IGNORECASE)
    if m:
        return normalize_dimension_prefix(m.group(0)).replace(" ", "")
    return ""


def _material_from_name(name: str) -> str:
    """Определяет тип материала стали по наименованию."""
    n = name.lower().replace("ё", "е")
    if "нерж" in n or "aisi" in n:
        return "нержавеющая"
    if "черн" in n or "ст3" in n or "ст.3" in n or "ст-3" in n:
        return "черная"
    if "оц" in n or "оцинк" in n or "цинк" in n:
        return "оцинкованная"
    return "оцинкованная"


def _thickness_from_name(name: str) -> Optional[float]:
    """Извлекает толщину стенки из наименования.

    Поддерживает записи вида:
      - толщина 0,7 мм
      - t0.7 / толщина стенки 1.0
      - (08пс)0.70, AISI 430 0.80
      - 0.7 мм
    """
    n = name.lower().replace("ё", "е").replace(",", ".")

    # Убираем размеры, чтобы не спутать толщину с длиной/сечением
    n = re.sub(r"\b\d{2,5}\s*[xх×*]\s*\d{2,5}(?:\s*[-_]\s*\d{2,5})?\b", " ", n)
    n = re.sub(r"\b(?:d|dn|ф)\s*\d{2,5}(?:\s*[-_]\s*\d{2,5})?\b", " ", n)

    # 1. Явное указание толщины
    m = re.search(r"толщин(?:а|ой)(?:\s*стенки)?\s*(\d{1,2}(?:\.\d{1,3})?)\s*мм?", n)
    if m:
        return float(m.group(1))

    # 2. t=0.7 / т 0.7 / t 0.7
    m = re.search(r"\b[тt]\s*[=:]?\s*(\d{1,2}(?:\.\d{1,3})?)\b", n)
    if m:
        return float(m.group(1))

    # 3. После марки стали: (08пс)0.70, AISI 430 0.80, Ст3 1.5
    m = re.search(r"(?:08пс|aisi\s*\d{3}|ст\.?3|ст-3)[()\s]*(\d{1,2}(?:\.\d{1,3})?)\b", n)
    if m:
        val = float(m.group(1))
        if 0.3 <= val <= 3.0:
            return val

    # 4. Число с единицей мм
    m = re.search(r"(\d{1,2}(?:\.\d{1,3})?)\s*мм", n)
    if m:
        val = float(m.group(1))
        if 0.3 <= val <= 3.0:
            return val

    # 5. Оставшееся десятичное число в диапазоне толщин (0.3..3.0)
    for token in re.findall(r"\d{1,2}(?:\.\d{1,3})?", n):
        val = float(token)
        if 0.3 <= val <= 3.0:
            return val

    return None


def extract_material_thickness_from_name(name: str) -> Tuple[str, Optional[float]]:
    """Извлекает материал и толщину стенки из наименования."""
    material = _material_from_name(name)
    thickness = _thickness_from_name(name)
    return material, thickness


# --- Извлечение размеров ---

def parse_size(size: str) -> Tuple[Optional[str], Dict[str, float]]:
    """Определяет тип сечения и извлекает размеры.

    Возвращает: (section, {D0/A0/B0/...})
    """
    size = size.strip().lower()
    size = size.replace("х", "x").replace("×", "x").replace("*", "x")

    # Прямоугольное сечение AxBxL: 600x400x1250
    m = re.match(r"^(\d{2,5})\s*x\s*(\d{2,5})\s*x\s*(\d{2,5})$", size)
    if m:
        return "rectangular", {"A0": float(m.group(1)), "B0": float(m.group(2)), "L0": float(m.group(3))}

    # Прямоугольное сечение AxB с необязательной длиной: 600x400-1250, 600x400_1250
    m = re.match(r"^(\d{2,5})\s*x\s*(\d{2,5})\s*[-_]\s*(\d{2,5})$", size)
    if m:
        return "rectangular", {"A0": float(m.group(1)), "B0": float(m.group(2)), "L0": float(m.group(3))}

    # Прямоугольное сечение AxB
    m = re.match(r"^(\d{2,5})\s*x\s*(\d{2,5})$", size)
    if m:
        return "rectangular", {"A0": float(m.group(1)), "B0": float(m.group(2))}

    # Круглое сечение с длиной: Ф100-3000, D160-1250
    m = re.match(r"^(?:d|dn|ф)\s*(\d{2,5})\s*[-_]\s*(\d{2,5})$", size)
    if m:
        return "round", {"D0": float(m.group(1)), "L0": float(m.group(2))}

    # D160, DN160, Ф160
    m = re.match(r"^(?:d|dn|ф)\s*(\d{2,5})$", size)
    if m:
        return "round", {"D0": float(m.group(1))}

    # Одиночное число — диаметр
    m = re.match(r"^(\d{2,5})$", size)
    if m:
        return "round", {"D0": float(m.group(1))}

    return None, {}


def extract_dimensions(text: str) -> Dict[str, float]:
    """Извлекает все возможные размеры из текста.

    Поддерживает:
    - воздуховоды: Ф160, 600x400-1250
    - отводы: Ф160-Ф160, 300x200-300x200
    - переходы: Ф250-Ф160, 900x900-800x800, 300x200-Ф200
    - тройники: Ф315-Ф315-Ф160, 400x300-400x300-160x160
    """
    dims = {}
    text = text.replace("х", "x").replace("×", "x").replace("*", "x")
    text_lower = text.lower()

    # Все прямоугольные сечения AxB
    rect_matches = re.findall(r"(\d{2,5})\s*x\s*(\d{2,5})", text, re.IGNORECASE)
    rect_tokens = [(float(w), float(h)) for w, h in rect_matches]

    # Все круглые диаметры (D/DN/Ф/Ø/⌀)
    round_matches = re.findall(r"(?<![\w.])(?:d|dn|ф|ø|⌀)\s*(\d{2,5})", text, re.IGNORECASE)
    round_tokens = [float(d) for d in round_matches]

    # Основное сечение
    if rect_tokens:
        dims["A0"] = rect_tokens[0][0]
        dims["B0"] = rect_tokens[0][1]
    elif round_tokens:
        dims["D0"] = round_tokens[0]

    # Второе сечение (переход / тройник / отвод)
    if len(rect_tokens) >= 2:
        # Для перехода прямоугольный->прямоугольный: второе сечение = A1/B1
        # Для тройника: второе совпадает с основным, третье = врезка
        if len(rect_tokens) == 2:
            dims["A1"] = rect_tokens[1][0]
            dims["B1"] = rect_tokens[1][1]
        else:
            # 3 и более: считаем третье сечение ветвью
            dims["A1"] = rect_tokens[1][0]
            dims["B1"] = rect_tokens[1][1]
            dims["A2"] = rect_tokens[2][0]
            dims["B2"] = rect_tokens[2][1]
    elif len(round_tokens) >= 2:
        if len(round_tokens) == 2:
            dims["D1"] = round_tokens[1]
        else:
            # 3 и более (тройник): второй совпадает с основным, третий — ветвь
            dims["D1"] = round_tokens[1]
            dims["D2"] = round_tokens[2]

    # Переход с прямоугольного на круглое: в тексте есть и AxB, и D
    if rect_tokens and len(round_tokens) == 1:
        # Если D ещё не назначен — это D0 круглого патрубка
        if "D0" not in dims:
            dims["D0"] = round_tokens[0]
        elif "D1" not in dims:
            dims["D1"] = round_tokens[0]

    # Специальный случай перехода: с D200 на D160
    m = re.search(r"с\s+(?:d|dn|ф)?\s*(\d{2,5})\s+на\s+(?:d|dn|ф)?\s*(\d{2,5})", text, re.IGNORECASE)
    if m:
        dims["D0"] = float(m.group(1))
        dims["D1"] = float(m.group(2))

    # Угол для отводов
    m = re.search(r"(\d{2,3})\s*°?\s*град", text, re.IGNORECASE)
    if not m:
        m = re.search(r"\b(90|45|30|60|15|75)\b", text)
    if m:
        dims["U0"] = float(m.group(1))

    # Радиус
    m = re.search(r"r\s*(\d{2,4})", text, re.IGNORECASE)
    if m:
        dims["R0"] = float(m.group(1))

    return dims


def meters_to_pieces(quantity_m: float, length_mm: float) -> Tuple[int, float]:
    """Переводит метраж в количество штук стандартной длины."""
    if quantity_m <= 0 or length_mm <= 0:
        return 0, length_mm
    length_m = length_mm / 1000.0
    pieces = int(round(quantity_m / length_m))
    return max(pieces, 1), length_mm


# --- Обработка строк ---

def classify_skip(name: str, size: str, unit: str, ptype: Optional[str]) -> str:
    """Определяет причину пропуска строки."""
    if ptype == "aggregate_fittings":
        return "Агрегатная строка фасонных изделий без детализации (требуется список: отводы, переходы, тройники и т.д.)"
    if ptype == "diffuser":
        return "Диффузор без артикула в каталоге асПродукция (нужна ручная загрузка или доработка 1С)"
    if ptype in ("damper", "shutter", "filter", "throttle", "grille", "roof_cap"):
        return f"{ptype}: оборудование/арматура — требуется проверка наличия артикула"
    if not size:
        return "Отсутствует размер"
    if unit.lower() in ("м2", "м²", "кв.м", "кв м"):
        return "Единица м² — агрегатная площадь, нельзя разбить на позиции без детализации"
    return "Не удалось распознать размер / тип"


def try_parse_ksd(size: str, name: str, material_code: str = "1", thickness: float = 0.7) -> Optional[dict]:
    """Пытается распознать КСД-адаптер из описания диффузора."""
    text = (name + " " + size).lower()
    text = text.replace("х", "x").replace("×", "x").replace("*", "x")

    if "ксд" not in text and "4апн" not in text and "адаптер" not in text:
        return None

    # Ищем размер диффузора AxB
    m = re.search(r"(\d{2,5})\s*x\s*(\d{2,5})", text)
    if not m:
        return None
    a0 = float(m.group(1))
    b0 = float(m.group(2))

    # Ищем диаметр патрубка КСД
    m = re.search(r"(?:ксд|d|ф)\s*(\d{2,5})", text)
    d1 = float(m.group(1)) if m else 0

    # Определяем тип: круглый/прямоугольный диффузор и врезка
    round_diffuser = "кругл" in text
    rect_diffuser = "прямоугольн" in text or "квадратн" in text or "4апн" in text

    if round_diffuser and d1:
        # Круглый диффузор с круглой врезкой
        article = "10-1-1"
        params = {"D0": a0 if a0 == b0 else max(a0, b0), "D1": d1, "L0": d1 + 100, "L1": DEFAULT_ADAPTER_L1_MM}
    elif round_diffuser:
        article = "10-1-1"
        params = {"D0": a0 if a0 == b0 else max(a0, b0), "D1": 0, "L0": 200, "L1": DEFAULT_ADAPTER_L1_MM}
    elif rect_diffuser and d1:
        # Прямоугольный диффузор с круглой врезкой
        article = "10-2-3"
        params = {"A0": a0, "B0": b0, "D1": d1, "L0": d1 + 100, "L1": DEFAULT_ADAPTER_L1_MM}
    elif rect_diffuser:
        # Прямоугольный диффузор с прямоугольной врезкой — нет данных о врезке
        return None
    else:
        return None

    return {
        "article": article,
        "params": params,
        "quantity": 1,
        "material_code": material_code,
        "thickness": thickness,
        "connection_0": "0",
        "connection_1": "0",
        "connection_2": "0",
        "connection_3": "0",
        "system": "",
        "comment": f"КСД адаптер: {name} {size}",
    }


def try_parse_fitting(
    name: str,
    size: str,
    unit: str,
    quantity: float,
    material_code: str = "1",
    thickness: float = 0.8,
) -> Optional[dict]:
    """Пытается распознать фасонное изделие."""
    ptype = detect_product_type(name)
    if ptype not in ("elbow", "transition", "tee", "cross", "cap", "saddle", "offset", "flange", "silencer"):
        return None

    dims = extract_dimensions(name + " " + size)
    if not dims:
        return None

    rectangular = is_rectangular(name + " " + size)
    round_ = is_round(name + " " + size)
    connections = ["0", "0", "0", "0"]

    # Отвод
    if ptype == "elbow":
        if rectangular:
            article = "2-2-1"  # радиусный по умолчанию
            name_lower = name.lower()
            if "прямой" in name_lower:
                article = "2-2-2"
            elif "косой" in name_lower or re.search(r"\bкос\b", name_lower):
                article = "2-2-3"
            elif "пирамид" in name_lower:
                article = "2-2-4"
            params = {k: v for k, v in dims.items() if k in ("A0", "B0", "U0", "R0")}
        else:
            article = "2-1-1"
            if "конич" in name.lower():
                article = "2-1-2"
            elif "гофрир" in name.lower():
                article = "2-1-3"
            params = {k: v for k, v in dims.items() if k in ("D0", "U0", "R0")}
        if "U0" not in params:
            params["U0"] = 90
        if "R0" not in params and "D0" in params:
            params["R0"] = params["D0"]  # радиус отвода = диаметру по умолчанию
        elif "R0" not in params and "A0" in params:
            params["R0"] = max(params.get("A0", 0), params.get("B0", 0))

    # Переход
    elif ptype == "transition":
        if rectangular:
            if round_ and "A0" in dims and "D0" in dims:
                # Переход с прямоугольного на круглое
                article = "3-3-1"
                params = {"A0": dims["A0"], "B0": dims["B0"], "D0": dims["D0"]}
            else:
                article = "3-2-1"
                params = {k: v for k, v in dims.items() if k in ("A0", "B0", "A1", "B1")}
        else:
            article = "3-1-1"
            params = {k: v for k, v in dims.items() if k in ("D0", "D1")}
        if "L0" not in params:
            params["L0"] = 200
        # Для переходных сечений ставим разумные соединения по умолчанию
        connections = default_connections("round" if round_ else "rectangular", article)

    # Тройник
    elif ptype == "tee":
        # Для тройника с 3+ диаметрами ветвь — последний диаметр
        branch_d = dims.get("D2") if "D2" in dims else dims.get("D1")
        # Если основное сечение прямоугольное, а в тексте есть диаметр — это диаметр круглой врезки
        if not branch_d and rectangular and "D0" in dims:
            branch_d = dims["D0"]
        # Для прямоугольного тройника с 3+ сечениями ветвь — последнее (A2/B2)
        branch_rect = ("A2" in dims and "B2" in dims) or ("A1" in dims and "B1" in dims)

        if rectangular:
            params = {k: v for k, v in dims.items() if k in ("A0", "B0")}
            if branch_d:
                # Прямоугольный тройник с круглой врезкой
                article = "4-2-1"
                params["D2"] = branch_d
            elif branch_rect:
                article = "4-2-3"
                # Приоритет отдаём последнему (ветвь) сечению
                for k in ("A2", "B2", "A1", "B1"):
                    if k in dims:
                        params[k] = dims[k]
            else:
                # Нет данных о ветви — пропускаем
                return None
        else:
            params = {k: v for k, v in dims.items() if k in ("D0",)}
            if branch_d:
                article = "4-1-1"
                params["D2"] = branch_d
            elif "A2" in dims and "B2" in dims:
                # Круглый тройник с прямоугольной врезкой
                article = "4-1-3"
                params["A2"] = dims["A2"]
                params["B2"] = dims["B2"]
            else:
                return None
        if "L0" not in params:
            params["L0"] = 200
        if "L2" not in params:
            params["L2"] = 100

    # Крестовина
    elif ptype == "cross":
        article = "5-1-1" if round_ else "5-2-1"
        params = {k: v for k, v in dims.items() if k in ("D0", "A0", "B0")}
        if "L0" not in params:
            params["L0"] = 200

    # Заглушка
    elif ptype == "cap":
        article = "6-1-1" if round_ else "6-2-1"
        params = {k: v for k, v in dims.items() if k in ("D0", "A0", "B0")}
        if "P0" not in params:
            params["P0"] = 0

    # Врезка
    elif ptype == "saddle":
        article = "8-1-1" if round_ else "8-2-1"
        params = {k: v for k, v in dims.items() if k in ("D0", "D1", "A0", "B0")}
        if "L0" not in params:
            params["L0"] = 150

    # Фланец
    elif ptype == "flange":
        article = "12-1-3" if round_ else "12-2-2"
        params = {k: v for k, v in dims.items() if k in ("D0", "A0", "B0", "P0")}
        if "P0" not in params:
            params["P0"] = 0

    # Шумоглушитель
    elif ptype == "silencer":
        if rectangular:
            article = "15-2-1"
            params = {k: v for k, v in dims.items() if k in ("A0", "B0", "L0")}
        else:
            article = "15-1-1"
            params = {k: v for k, v in dims.items() if k in ("D0", "L0")}
        if "L0" not in params:
            params["L0"] = 1000

    else:
        return None

    if not params:
        return None

    return {
        "article": article,
        "params": params,
        "quantity": int(quantity) if unit.lower() in ("шт", "штук", "pcs", "pc", "шт.") else max(1, int(round(quantity))),
        "material_code": material_code,
        "thickness": thickness,
        "connection_0": connections[0],
        "connection_1": connections[1],
        "connection_2": connections[2],
        "connection_3": connections[3],
        "system": "",
        "comment": f"{name} {size}",
    }


def _finalize_row(parsed: dict) -> dict:
    """Проверяет и корректирует код материала по справочнику применимости."""
    article = parsed.get("article", "")
    code = parsed.get("material_code", "1")
    validated, warning = validate_material_code(article, code)
    parsed["material_code"] = validated
    if warning:
        parsed["comment"] = f"{parsed.get('comment', '')} [{warning}]".strip()
    return parsed


def parse_row(row: dict, defaults: dict) -> Tuple[Optional[dict], Optional[dict]]:
    """Преобразует одну строку таблицы в строку для XML или причину пропуска."""
    name = str(row.get("name", "")).strip()
    size = str(row.get("size", "")).strip()
    unit = str(row.get("unit", "")).strip().lower()
    quantity_raw = row.get("quantity", "0")

    # Если размер не вынесен в отдельную колонку — ищем его в наименовании
    if not size:
        size = extract_size_token(name)

    # Материал и толщина также могут быть внутри наименования
    material = str(row.get("material", defaults.get("material", "оцинкованная"))).strip()
    thickness_raw = row.get("thickness", defaults.get("thickness", "0.8"))
    try:
        thickness = float(str(thickness_raw).replace(",", "."))
    except ValueError:
        thickness = 0.8
    mat_from_name, thick_from_name = extract_material_thickness_from_name(name)
    # Если в наименовании явно указан материал — он приоритетнее значения по умолчанию
    if mat_from_name != material.lower() or re.search(r"(нерж|черн|оц|оцинк|aisi|ст3|ст\.3|ст-3)", name, re.IGNORECASE):
        material = mat_from_name
    if thick_from_name is not None:
        thickness = thick_from_name

    ptype = detect_product_type(name)

    try:
        quantity = float(str(quantity_raw).replace(",", ".").replace(" ", ""))
    except ValueError:
        return None, {
            "name": name, "size": size, "unit": unit,
            "reason": f"Не удалось распознать количество: {quantity_raw}"
        }

    # Если строка уже содержит явный артикул и параметры (например, из map_customer_equipment),
    # используем их напрямую, минуя эвристики.
    explicit_article = str(row.get("article", "")).strip()
    explicit_params = row.get("params")
    if explicit_article and isinstance(explicit_params, dict) and explicit_params:
        section = "rectangular" if any(k in explicit_params for k in ("A0", "B0")) else "round"
        connections = default_connections(section)
        if explicit_article.startswith("3-3"):
            connections = ["6", "2", "0", "0"]

        pieces = int(quantity) if unit in ("шт", "штук", "pcs", "pc", "шт.") else max(1, int(round(quantity)))

        return _finalize_row({
            "article": explicit_article,
            "params": dict(explicit_params),
            "quantity": pieces,
            "material_code": material_to_code(material),
            "thickness": thickness,
            "connection_0": connections[0],
            "connection_1": connections[1],
            "connection_2": connections[2],
            "connection_3": connections[3],
            "system": row.get("system", ""),
            "comment": f"{name} {size}".strip(),
        }), None

    # Агрегатные строки фасонных изделий / оборудование без артикула
    if ptype == "aggregate_fittings" or unit in ("м2", "м²", "кв.м", "кв м"):
        return None, {"name": name, "size": size, "unit": unit, "reason": classify_skip(name, size, unit, ptype)}

    material_code = material_to_code(material)

    # Попытка распознать КСД-адаптер
    ksd = try_parse_ksd(size, name, material_code=material_code, thickness=thickness)
    if ksd:
        ksd["quantity"] = int(quantity) if unit in ("шт", "штук", "pcs", "pc", "шт.") else max(1, int(round(quantity)))
        return _finalize_row(ksd), None

    # Попытка распознать фасонное изделие
    fitting = try_parse_fitting(name, size, unit, quantity, material_code=material_code, thickness=thickness)
    if fitting:
        return _finalize_row(fitting), None

    # Воздуховоды
    section, dims = parse_size(size)
    if section:
        if section == "round":
            # Выбор артикула по типу круглого воздуховода
            name_lower = name.lower()
            if "прямошовный" in name_lower:
                article = "1-1-1"
            elif "спирально-навивной" in name_lower:
                article = "1-1-2"
            else:
                article = DEFAULT_ROUND_ARTICLE
            length_mm = DEFAULT_ROUND_LENGTH_MM
        else:
            article = DEFAULT_RECT_ARTICLE
            length_mm = DEFAULT_RECT_LENGTH_MM

        material_code = material_to_code(material)
        connections = default_connections(section)

        # Если размер уже содержит длину (например, Ф100-3000), используем её
        length_mm = dims.get("L0", length_mm)

        if unit in ("м", "m", "метр", "mtr"):
            pieces, length_mm = meters_to_pieces(quantity, length_mm)
        elif unit in ("шт", "штук", "pcs", "pc", "шт."):
            pieces = int(quantity)
        else:
            return None, {"name": name, "size": size, "unit": unit, "reason": classify_skip(name, size, unit, ptype)}

        dims["L0"] = length_mm

        return _finalize_row({
            "article": article,
            "params": dims,
            "quantity": pieces,
            "material_code": material_code,
            "thickness": thickness,
            "connection_0": connections[0],
            "connection_1": connections[1],
            "connection_2": connections[2],
            "connection_3": connections[3],
            "system": "",
            "comment": f"{name} {size}",
        }), None

    # Диффузоры и прочее без возможности автозагрузки
    return None, {"name": name, "size": size, "unit": unit, "reason": classify_skip(name, size, unit, ptype)}


def process_rows(
    rows: List[dict],
    header: Optional[dict] = None,
    defaults: Optional[dict] = None,
) -> Tuple[str, List[dict]]:
    """Принимает список строк спецификации и возвращает XML-строку и список пропущенных.

    Args:
        rows: список словарей с ключами name, size, unit, quantity, material, thickness.
        header: заголовок заказа для XML (numberDate, numberOrder, INN, ...).
        defaults: значения по умолчанию для material и thickness.

    Returns:
        Кортеж (xml_string, skipped_list).
    """
    if header is None:
        header = {
            "numberDate": "",
            "numberOrder": "",
            "INN": "",
            "customer": "",
            "email": "",
            "phone": "",
            "contact": "",
        }
    if defaults is None:
        defaults = {"material": "оцинкованная", "thickness": "0.8"}

    success_rows: List[dict] = []
    skipped: List[dict] = []

    for row in rows:
        name = str(row.get("name", "")).strip()
        size = str(row.get("size", "")).strip()
        if not name and not size:
            continue
        parsed, skip = parse_row(row, defaults)
        if parsed:
            if ALLOWED_ARTICLES and parsed["article"] not in ALLOWED_ARTICLES:
                skipped.append(
                    {
                        "name": row.get("name", ""),
                        "size": row.get("size", ""),
                        "unit": row.get("unit", ""),
                        "quantity": row.get("quantity", ""),
                        "article": parsed["article"],
                        "reason": f"Артикул {parsed['article']} не входит в разрешённый список производимой номенклатуры",
                    }
                )
                continue
            success_rows.append(parsed)
        elif skip:
            skipped.append(skip)

    xml_text = generate_order_xml(header, success_rows)
    return xml_text, skipped


def process_csv(
    input_path: str,
    output_xml: str = "order_from_spec.xml",
    header: Optional[dict] = None,
    defaults: Optional[dict] = None,
    delimiter: str = ";",
) -> Tuple[List[dict], List[dict]]:
    """Читает CSV/Excel-спецификацию и генерирует XML."""
    if header is None:
        header = {
            "numberDate": "",
            "numberOrder": "",
            "INN": "",
            "customer": "",
            "email": "",
            "phone": "",
            "contact": "",
        }
    if defaults is None:
        defaults = {"material": "оцинкованная", "thickness": "0.8"}

    path = Path(input_path)
    suffix = path.suffix.lower()

    if suffix in (".xlsx", ".xls", ".xlsm"):
        df = pd.read_excel(input_path, dtype=str)
    else:
        df = pd.read_csv(input_path, delimiter=delimiter, dtype=str, encoding="utf-8-sig")

    # Нормализуем имена столбцов и заменяем NaN на пустые строки
    df = df.rename(columns=lambda c: str(c).strip().lower())
    df = df.fillna("")

    raw_rows = df.to_dict("records")
    rows = []
    for raw_row in raw_rows:
        row = {k.strip().lower(): str(v).strip() for k, v in raw_row.items()}
        rows.append(row)

    xml_text, skipped = process_rows(rows, header=header, defaults=defaults)

    success_rows: List[dict] = []
    for row in rows:
        parsed, _ = parse_row(row, defaults)
        if parsed:
            success_rows.append(parsed)

    if not success_rows:
        print("Не удалось распознать ни одной строки.")
        return success_rows, skipped

    Path(output_xml).write_text(xml_text, encoding="utf-8")

    # Сохраняем отчёт о пропущенных позициях
    report_path = str(Path(output_xml).with_suffix("")) + "_skipped.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(skipped, f, ensure_ascii=False, indent=2)

    print(f"Сгенерировано строк: {len(success_rows)}")
    print(f"Пропущено строк: {len(skipped)}")
    if skipped:
        print("\nПропущенные позиции:")
        for s in skipped:
            print(f"  - {s['name']} {s['size']} ({s['unit']}): {s['reason']}")
    print(f"XML сохранён: {output_xml}")
    print(f"Отчёт по пропускам: {report_path}")

    return success_rows, skipped


def main():
    if len(sys.argv) < 2:
        print("Использование: python process_specification_table.py <input.csv> [output.xml]")
        sys.exit(1)

    input_path = sys.argv[1]
    output_xml = sys.argv[2] if len(sys.argv) > 2 else "order_from_spec.xml"

    header = {
        "numberDate": "",
        "numberOrder": "",
        "INN": "",
        "customer": "",
        "email": "",
        "phone": "",
        "contact": "",
    }

    process_csv(input_path, output_xml, header=header)


if __name__ == "__main__":
    main()
