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
import logging
import math
import re
import sys
from pathlib import Path
from typing import List, Dict, Optional, Tuple

import pandas as pd

import size_notations as szn

import llm_size_classifier as lsc
from config import get_config, mapping_file
from generate_order_xml import build_characteristic, generate_order_xml, load_article_mapping
from project_spec_xlsx import is_project_spec_xlsx, parse_project_spec_xlsx
from spec_common import material_to_code


logger = logging.getLogger(__name__)


# Short-lived aliases for backwards compatibility and readability.
# Values are read from config.yaml via config.get_config().
_DEFAULTS = get_config()
DEFAULT_ROUND_ARTICLE = _DEFAULTS["default_round_article"]
DEFAULT_RECT_ARTICLE = _DEFAULTS["default_rect_article"]
DEFAULT_ROUND_LENGTH_MM = _DEFAULTS["default_round_length_mm"]
DEFAULT_RECT_LENGTH_MM = _DEFAULTS["default_rect_length_mm"]
DEFAULT_ADAPTER_L1_MM = _DEFAULTS["default_adapter_l1_mm"]


def load_allowed_articles(path: str = None) -> set:
    if path is None:
        path = mapping_file("allowed_articles")
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
        if re.fullmatch(r"\d+-\d+(?:-\d+)?", candidate):
            allowed.add(candidate)
    return allowed


ALLOWED_ARTICLES = load_allowed_articles()


# Справочник кодов LITENED и длины — в config/size_notations.yaml (size_notations).


def load_article_materials(path: str = None) -> Dict[str, List[str]]:
    if path is None:
        path = mapping_file("materials")
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
    (r"\bдефлектор\b", "roof_cap"),
    (r"\bпленум\b", "plenum"),
    (r"\bфланец\b", "flange"),
    (r"\bниппель\b", "nipple"),
    (r"\bбандаж\b", "band"),
    # Оборудование / арматура
    (r"\bдиффузор", "diffuser"),
    (r"\bксд\b", "ksd"),
    (r"\bрешетка\b", "grille"),
    (r"\bшумоглушител", "silencer"),
    (r"\bклапан\b", "damper"),
    (r"\bзаслонка\b", "damper"),
    (r"\bшибер\b", "shutter"),
    (r"\bфильтр\b", "filter"),
    (r"\bдроссель\b", "throttle"),
    (r"\bдк[-–\s]?\d", "throttle"),
    (r"\bстакан\b", "mounting_cup"),
    (r"\bвентилятор\b", "fan"),
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
    # Одиночный диаметр (в т.ч. с префиксом Ø/⌀)
    if re.search(rf"(?:{szn.prefix_group()})\s*\d+", text, re.IGNORECASE):
        return True
    # Суффиксный диаметр «125ø» (U+00F8)
    if re.search(rf"\d{{2,5}}\s*{szn.suffix_char_class()}", text):
        return True
    return False


# --- Извлечение размеров из имени, если отдельная колонка пуста ---

def normalize_dimension_prefix(token: str) -> str:
    """Приводит D/DN/Ø/ø/⌀ к Ф для единообразия."""
    token = token.replace("Ø", "Ф").replace("ø", "Ф").replace("⌀", "Ф")
    token = re.sub(rf"^(?:{szn.word_prefix_group()})\b", "ф", token, flags=re.IGNORECASE)
    return token


def _collapse_size_spaces(text: str) -> str:
    """Убирает пробелы внутри чисел, которые появляются при OCR (например, '7 00' -> '700')."""
    for sep in szn.separators():
        text = text.replace(sep, "x")
    # Повторяем, пока пробелы между цифрами не закончатся
    while re.search(r"(\d)\s+(\d)", text):
        text = re.sub(r"(\d)\s+(\d)", r"\1\2", text)
    return text


def _strip_gost_designation(text: str) -> str:
    """Убирает обозначение стандарта («по ГОСТ 14918-80»): его номер иначе
    склеивается со следующим размером («-80 400x300» → «80400x300»)."""
    return re.sub(r"(?i)гост\s*\d{4,5}\s*-\s*\d{2,3}\b", " ", text)


def extract_size_token(text: str) -> str:
    """Ищет в тексте размер в формате AxB[-L], AxBxL или D/DN/Ф/Ø/⌀D[-L]."""
    text = _strip_gost_designation(text)
    text = _collapse_size_spaces(text)
    # Прямоугольное сечение с длиной через x: AxBxL
    m = re.search(r"\b(\d{2,5}\s*x\s*\d{2,5}\s*x\s*\d{2,5})\b", text, re.IGNORECASE)
    if m:
        return m.group(1).replace(" ", "")
    # Прямоугольное сечение с опциональной длиной: AxB-L
    m = re.search(r"\b(\d{2,5}\s*x\s*\d{2,5}(?:\s*[-_]\s*\d{2,5})?)\b", text, re.IGNORECASE)
    if m:
        return m.group(1).replace(" ", "")
    # Круглое сечение: суффиксный диаметр «250ø» (FineReader ставит ø после числа).
    # «80 Ø250» из «ГОСТ 14918-80 Ø250» НЕ суффикс: ø здесь префикс следующего
    # числа, поэтому требуем, чтобы после ø не было цифры.
    m = re.search(rf"\b(\d{{2,5}})\s*{szn.suffix_char_class()}(?!\s*\d)", text)
    if m:
        return f"Ф{m.group(1)}"
    # Круглое сечение с опциональной длиной (⌀ — не буква, поэтому \b не подходит)
    m = re.search(rf"(?<![\w.])(?:(?:{szn.prefix_group()})\s*\d{{2,5}}(?:\s*[-_]\s*\d{{2,5}})?)", text, re.IGNORECASE)
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
    # Децимальные габариты в метрах: «1,35х0,86х0,3» (зонты вытяжные) —
    # иначе «1,35» определяется как толщина 1.35
    n = re.sub(r"\b\d{1,2}[.,]\d{1,3}\s*[xх×*]\s*\d{1,2}[.,]\d{1,3}(?:\s*[xх×*]\s*\d{1,2}[.,]\d{1,3})?\b",
               " ", n)

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

    # 5. Оставшееся десятичное число в диапазоне толщин (0.3..3.0).
    #    Предварительно убираем мусор, похожий на толщину, но ею не являющийся:
    #    «м3/ч» (кубометры в час) и номера систем «В2.2», «В3.1», «П1»
    n = re.sub(r"м\s*3\s*/\s*ч", " ", n)
    n = re.sub(r"(?<![а-яa-z])[ввпрт]+\.?\s*\d{1,2}(?:\.\d{1,3})?(?![0-9.])", " ", n)
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


# --- Исправление OCR-ошибок в размерах ---

# Сторона прямоугольного воздуховода/фасонки менее 100 мм в проектной
# спецификации почти всегда означает потерянный ноль при распознавании
# (порог — size_notations.min_rect_side_mm()).


def _is_rectangular_product(name: str) -> bool:
    """Определяет, относится ли позиция к прямоугольным изделиям."""
    n = name.lower()
    if "кругл" in n:
        return False
    if "прямоугольн" in n:
        return True
    return any(word in n for word in (
        "воздуховод", "отвод", "переход", "тройник", "врезка",
        "заглушка", "крестовина", "фланец", "утка", "диффузор",
        "решетка", "шумоглушител",
    ))


def correct_ocr_size(name: str, size: str) -> Tuple[str, List[str]]:
    """Исправляет типичные OCR-ошибки в размерах (потерянный ноль).

    Возвращает исправленный размер и список предупреждений.
    Применяется только к прямоугольным изделиям и только к сторонам < 100 мм.
    """
    warnings: List[str] = []
    if not size or not _is_rectangular_product(name):
        return size, warnings

    normalized = _collapse_size_spaces(size)

    def repl(match: re.Match) -> str:
        a = int(match.group(1))
        b = int(match.group(2))
        orig_a, orig_b = a, b
        if 10 <= a < szn.min_rect_side_mm():
            a *= 10
        if 10 <= b < szn.min_rect_side_mm():
            b *= 10
        if a != orig_a or b != orig_b:
            return f"{a}x{b}"
        return match.group(0)

    corrected, count = re.subn(r"(\d{2,5})\s*x\s*(\d{2,5})", repl, normalized, flags=re.IGNORECASE)
    if count and corrected != normalized:
        warnings.append(f"OCR: размер исправлен '{size}' -> '{corrected}'")
        size = corrected

    return size, warnings


# --- Извлечение размеров ---

def parse_size(size: str) -> Tuple[Optional[str], Dict[str, float]]:
    """Определяет тип сечения и извлекает размеры.

    Возвращает: (section, {D0/A0/B0/...})
    """
    size = size.strip().lower()
    for sep in szn.separators():
        size = size.replace(sep, "x")

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
    m = re.match(rf"^(?:{szn.prefix_group()})\s*(\d{{2,5}})\s*[-_]\s*(\d{{2,5}})$", size)
    if m:
        return "round", {"D0": float(m.group(1)), "L0": float(m.group(2))}

    # D160, DN160, Ф160
    m = re.match(rf"^(?:{szn.prefix_group()})\s*(\d{{2,5}})$", size)
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
    text = _strip_gost_designation(text)
    for sep in szn.separators():
        text = text.replace(sep, "x")
    text_lower = text.lower()

    # Все прямоугольные сечения AxB
    rect_matches = re.findall(r"(\d{2,5})\s*x\s*(\d{2,5})", text, re.IGNORECASE)
    rect_tokens = [(float(w), float(h)) for w, h in rect_matches]

    # Все круглые диаметры: префиксные (D/DN/Ф/Ø/⌀) и суффиксные «125ø» (U+00F8).
    # Суффиксный паттерн с отрицательным просмотром вперёд: «80 Ø250» из
    # «ГОСТ 14918-80 Ø250» не диаметр 80 — Ø там префикс следующего числа.
    round_matches = szn.round_diameter_pattern().findall(text)
    round_tokens = [
        float(re.search(r"\d{2,5}", m.replace("ø", "").replace("Ø", "").replace("⌀", "")).group(0))
        for m in round_matches
    ]

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

    # Длина: L=900, L 1000, 1000 мм и т.п.
    m = re.search(r"(?:l\s*=?\s*|длина\s*)(\d{3,5})", text, re.IGNORECASE)
    if not m:
        m = re.search(r"\b(\d{3,5})\s*мм\b", text, re.IGNORECASE)
    if m:
        dims["L0"] = float(m.group(1))

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

    # LITENED NKD / NKK: модельный код X-Y → сечение (X*100)×(Y*100) мм.
    m = re.search(r"\bLITENED\s+(\d{1,3}-\d{1,3})\s+(NKD|NKK)\b", text, re.IGNORECASE)
    if m:
        code = m.group(1)
        variant = m.group(2).upper()
        sizes = szn.litened_sizes()
        if code in sizes:
            a0, b0 = sizes[code]
            dims["A0"] = a0
            dims["B0"] = b0
            dims["L0"] = float(szn.litened_lengths().get(variant, 1100))

    # Оборудование по псевдонимам: MSN, KPN-S, PPK, ГЕРМИК, NKD, KNK, CHR, KCH
    if "D0" not in dims and "A0" not in dims:
        m = re.search(r"\b(?:MSN|KPN-S|PPK|ГЕРМИК|NKD|KNK|CHR|KCH|RV[NCS]|RSK|KON|ДК)[-\s]?(\d{2,4})\b", text, re.IGNORECASE)
        if m:
            dims["D0"] = float(m.group(1))

    # KNK D/L: L — длина ×100 мм (например, KNK 250/6 → L0=600).
    if "D0" not in dims or ("D0" in dims and "L0" not in dims):
        m = re.search(r"\bKNK[-\s]?(\d{2,4})\s*/\s*(\d)\b", text, re.IGNORECASE)
        if m:
            dims["D0"] = float(m.group(1))
            dims["L0"] = float(m.group(2)) * 100

    return dims


def meters_to_pieces(quantity_m: float, length_mm: float) -> Tuple[int, float]:
    """Переводит метраж в количество штук стандартной длины.

    Завод округляет вверх (28.8 м / 1.25 м → 24 шт, 7.9 → 7, 0.9 → 1).
    """
    if quantity_m <= 0 or length_mm <= 0:
        return 0, length_mm
    length_m = length_mm / 1000.0
    pieces = int(math.ceil(quantity_m / length_m))
    return max(pieces, 1), length_mm


# --- Обработка строк ---

def classify_skip(name: str, size: str, unit: str, ptype: Optional[str]) -> str:
    """Определяет причину пропуска строки."""
    if ptype == "aggregate_fittings":
        return "Агрегатная строка фасонных изделий без детализации (требуется список: отводы, переходы, тройники и т.д.)"
    if ptype == "diffuser":
        return "Диффузор без артикула в каталоге асПродукция (нужна ручная загрузка или доработка 1С)"
    if ptype in ("shutter", "filter", "grille", "roof_cap"):
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
    if ptype not in ("elbow", "transition", "tee", "cross", "cap", "saddle", "offset", "flange", "silencer", "damper", "throttle", "mounting_cup", "nipple", "roof_cap"):
        return None

    dims = extract_dimensions(name + " " + size)

    # Тройник/переход без префиксов Ø: «315/315/200», «200/125» (проект
    # Владикавказ) — диаметры через слэш.
    if ptype in ("transition", "tee") and "D0" not in dims and "A0" not in dims:
        m = re.search(r"(?<![\d.,])(\d{2,5})\s*/\s*(\d{2,5})(?:\s*/\s*(\d{2,5}))?(?![\d.,])",
                      name + " " + size)
        if m:
            dims["D0"] = float(m.group(1))
            dims["D1"] = float(m.group(2))
            if m.group(3):
                dims["D2"] = float(m.group(3))

    if not dims:
        return None

    rectangular = is_rectangular(name + " " + size)
    round_ = is_round(name + " " + size)
    connections = ["0", "0", "0", "0"]

    # Для шумоглушителей модельный код LITENED NK даёт A0/B0,
    # но в тексте нет ни "прямоугольный", ни AxB — всё равно считаем прямоугольным.
    if ptype == "silencer":
        if "A0" in dims and "B0" in dims:
            rectangular = True
            round_ = False
        elif "D0" in dims:
            rectangular = False
            round_ = True

    # Отвод
    if ptype == "elbow":
        if rectangular:
            # Дефолт — прямой отвод (практика КП завода: все прямоугольные
            # отводы без уточнения — «прямой»); слова переопределяют тип.
            article = "2-2-2"
            name_lower = name.lower()
            if "радиусн" in name_lower:
                article = "2-2-1"
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
                # Дефолт — переход тип 5 (практика КП завода: все
                # прямоугольные переходы без уточнения — «тип 5»).
                article = "3-2-5"
                m_type = re.search(r"тип\s*(\d)", name, re.IGNORECASE)
                if m_type:
                    article = f"3-2-{m_type.group(1)}"
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
            # Стандартная глубина заглушки:
            # для A/D 100..950 мм -> 25 мм, для 1000 мм и выше -> 35 мм
            if "D0" in params:
                size = params["D0"]
            else:
                size = max(params.get("A0", 0), params.get("B0", 0))
            params["P0"] = 25 if size <= 950 else 35

    # Врезка
    elif ptype == "saddle":
        article = "8-1-1" if round_ else "8-2-1"
        params = {k: v for k, v in dims.items() if k in ("D0", "A0", "B0")}
        if round_:
            # Врезка с двумя диаметрами «Ø315/Ø125»: D0 — патрубок (меньший),
            # D2 — основной воздуховод (больший); у одиночного Ø200 — оба равны.
            # Без D2 расчёт 8-1-1 в 1С падает с «Геометрия недопустима»
            # (sqrt отрицательного числа); default_params маппинга (D2=D0)
            # действуют только в XML-пути.
            d_vals = [v for k, v in dims.items() if k in ("D0", "D1", "D2") and v]
            if len(d_vals) >= 2:
                params["D0"] = min(d_vals[0], d_vals[1])
                params["D2"] = max(d_vals[0], d_vals[1])
            else:
                params["D2"] = dims.get("D2") or dims.get("D1") or dims.get("D0")
        if "L0" not in params:
            params["L0"] = 150

    # Фланец
    elif ptype == "flange":
        article = "12-1-3" if round_ else "12-2-2"
        params = {k: v for k, v in dims.items() if k in ("D0", "A0", "B0", "P0")}
        if "P0" not in params:
            params["P0"] = 0

    # Ниппель
    elif ptype == "nipple":
        if round_:
            article = "12-1-1"
            params = {k: v for k, v in dims.items() if k in ("D0", "L0")}
            if "D0" not in params:
                return None
            # L0 отсутствует в спецификации — не задаём; mapping подставит
            # default_params (12-1-1: L0=100)
        else:
            article = "12-2-4"
            params = {k: v for k, v in dims.items() if k in ("A0", "B0", "L0")}
            if "A0" not in params or "B0" not in params:
                return None

    # Шумоглушитель
    elif ptype == "silencer":
        name_lower = name.lower()
        if rectangular:
            if "пластина шумоглушителя" in name_lower or "пластинчатый тип 4" in name_lower:
                article = "15-2-4"
                # Обозначение в 1С: B x L x a2.
                # Во входном тексте обычно "B x L x a2", поэтому первое число → B0,
                # второе → L0, третье → A2.
                a0 = dims.get("A0", 0)
                b0 = dims.get("B0", a0)
                a2 = dims.get("A2")
                if a2 is None:
                    m = re.search(r"(\d{2,5})\s*x\s*(\d{2,5})\s*x\s*(\d{2,5})", name + " " + size, re.IGNORECASE)
                    if m:
                        a0 = float(m.group(1))
                        b0 = float(m.group(2))
                        a2 = float(m.group(3))
                params = {"B0": a0, "L0": b0, "A2": a2 if a2 is not None else 200}
            elif "обтекатель шумоглушителя" in name_lower or "с обтекателем тип 5" in name_lower:
                article = "15-2-5"
                # Обозначение в 1С: B x a2.
                # Во входном тексте обычно "B x a2", первое число → B0, второе → A2.
                a0 = dims.get("A0", 0)
                b0 = dims.get("B0", a0)
                a2 = dims.get("A2")
                if a2 is None:
                    m = re.search(r"(\d{2,5})\s*x\s*(\d{2,5})", name + " " + size, re.IGNORECASE)
                    if m and not re.search(r"(\d{2,5})\s*x\s*(\d{2,5})\s*x\s*(\d{2,5})", name + " " + size, re.IGNORECASE):
                        a0 = float(m.group(1))
                        a2 = float(m.group(2))
                params = {"B0": a0, "A2": a2 if a2 is not None else 200}
            elif "пластинчатый" in name_lower:
                article = "15-2-2"
                params = {k: v for k, v in dims.items() if k in ("A0", "B0", "L0")}
                if "A0" not in params or "B0" not in params:
                    return None
            elif "с круглыми врезками" in name_lower:
                article = "15-2-6"
                params = {k: v for k, v in dims.items() if k in ("A0", "B0", "L0")}
                if "A0" not in params or "B0" not in params:
                    return None
            elif "канальный" in name_lower:
                article = "15-2-7"
                params = {k: v for k, v in dims.items() if k in ("A0", "B0", "L0")}
                if "A0" not in params or "B0" not in params:
                    return None
            elif "с обтекателем" in name_lower:
                article = "15-2-3"
                params = {k: v for k, v in dims.items() if k in ("A0", "B0", "L0")}
                if "A0" not in params or "B0" not in params:
                    return None
            else:
                article = "15-2-1"
                params = {k: v for k, v in dims.items() if k in ("A0", "B0", "L0")}
                if "A0" not in params or "B0" not in params:
                    return None
        else:
            if "спиральный" in name_lower:
                article = "15-1-2"
            else:
                article = "15-1-1"
            params = {k: v for k, v in dims.items() if k in ("D0", "L0")}
            if "D0" not in params:
                return None
        if "L0" not in params:
            params["L0"] = 1000

    # Монтажный стакан
    elif ptype == "mounting_cup":
        article = "29-1-3"
        params = {k: v for k, v in dims.items() if k in ("D0", "L0")}
        if "D0" not in params:
            return None
        if "L0" not in params:
            params["L0"] = 200

    # Зонт крышный / дефлектор (по техотделу — одно и то же изделие)
    elif ptype == "roof_cap":
        if "D0" in dims:
            article = "9-1-2"  # Зонт крышный круглого сечения (дефлектор)
            params = {"D0": dims["D0"]}
        elif "A0" in dims and "B0" in dims:
            article = "9-2-1"  # Зонт прямоугольного сечения с коньком
            params = {"A0": dims["A0"], "B0": dims["B0"],
                      "A1": dims["A0"], "B1": dims["B0"]}
        else:
            return None

    # Клапаны / дроссели / заслонки
    elif ptype in ("damper", "throttle"):
        name_lower = name.lower()
        if "обратный" in name_lower:
            article = "18-2-1" if rectangular else "18-1-3"
        elif "противопожарный" in name_lower or "дымовой" in name_lower:
            article = "20-2" if rectangular else "20-1"
        elif "воздушный" in name_lower and "авк" not in name_lower:
            article = "21-2-1" if rectangular else "21-1-1"
        elif "дроссель" in name_lower or "заслонка" in name_lower or "авк" in name_lower:
            # «Воздушный клапан с ручным приводом АВК» — по практике техотдела
            # (КП №1090 поз.4) это дроссель-клапан 16-x-1
            article = "16-2-1" if rectangular else "16-1-1"
        else:
            # Не удалось классифицировать — используем дроссель по умолчанию
            article = "16-2-1" if rectangular else "16-1-1"
        if rectangular:
            params = {k: v for k, v in dims.items() if k in ("A0", "B0", "L0")}
            if "A0" not in params or "B0" not in params:
                return None
        else:
            params = {k: v for k, v in dims.items() if k in ("D0", "L0")}
            if "D0" not in params:
                return None
        if "L0" not in params:
            params["L0"] = 200

    else:
        return None

    if not params:
        return None

    # Правило техотдела: прямоугольная фасонка (отводы, тройники, крестовины,
    # врезки, утки, зонты) собирается на шине/уголке (код 6).
    # Иначе 1С берёт соединение по умолчанию из изделия (УГФ-95/105 без УГФ-65
    # для сечений до 350 мм). Переходы уже получают свои соединения выше.
    # Заглушка — одностороннее изделие: шина только на стороне 0, иначе в 1С
    # появляется шина на несуществующем соединении 1.
    if rectangular and ptype in ("elbow", "tee", "cross", "saddle", "offset", "roof_cap"):
        if connections == ["0", "0", "0", "0"]:
            connections = ["6", "6", "0", "0"]
    if rectangular and ptype == "cap":
        if connections == ["0", "0", "0", "0"]:
            connections = ["6", "0", "0", "0"]

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


def gost_min_thickness(max_dim: float) -> float:
    """Минимальная толщина металла по ГОСТ (правила техотдела):
    до 350 мм — 0.55; 400–950 — 0.7; 1000 — 0.8; свыше 1000 — 1.0."""
    if max_dim <= 350:
        return 0.55
    if max_dim <= 950:
        return 0.7
    if max_dim <= 1000:
        return 0.8
    return 1.0


def normalize_thickness(
    thickness: float,
    params: Dict[str, float],
    text: str = "",
    explicit: bool = False,
) -> float:
    """Приводит толщину к правилам техотдела.

    - заводские рулоны: 0.5→0.55, 0.6→0.70, 0.9→1.00 (сверка с КП №1090,
      поз.14–15 и 198–201); 0.7/0.8/1.0 без изменений;
    - толщина НЕ указана в проекте (explicit=False): берётся точное значение
      из ГОСТ-таблицы по наибольшему размеру сечения (0.55/0.7/0.8/1.0),
      а не значение по умолчанию;
    - толщина указана в проекте (explicit=True): сохраняется (после
      нормировки под рулон), ГОСТ-таблицей не перебивается
      (решение от 05.09.2026 по сверке с КП);
    - для дымоудаления минимум 0.8.
    """
    t = _FACTORY_THICKNESS.get(round(float(thickness or 0), 2), thickness)
    dims = [params[k] for k in ("A0", "B0", "D0", "A1", "B1", "D1")
            if isinstance(params.get(k), (int, float))]
    if dims and not explicit:
        t = gost_min_thickness(max(dims))
    if "дым" in (text or "").lower():
        t = max(t, 0.8)
    return t


# Нормировка проектных толщин под фактические заводские рулоны/штрипс
# (сверка с КП №1090: S=0,5→0.55; S=0,6→0.70; S=0,9→1.00).
_FACTORY_THICKNESS = {
    0.5: 0.55,
    0.6: 0.7,
    0.7: 0.7,
    0.8: 0.8,
    0.9: 1.0,
    1.0: 1.0,
}


def geometry_skip_reason(article: str, params: dict) -> Optional[str]:
    """Инварианты геометрии расчёта 1С. Строка-нарушитель получит S=0 и цену 0.

    Проверено по коду расчёта обработки асВводНовогоЗаказа (docs/модуль
    ввод нового заказа.txt): «Геометрия недопустима» падает, когда
    sqrt(D²/4 − d2²/4) берётся из отрицательного или asin(d2/D) получает
    аргумент > 1. Реальный дефект — заказ №000000871 (D0=80, d2=160).
    """
    if article in ("4-1-1", "4-1-2"):  # круглый тройник с круглой врезкой
        d0, d2 = params.get("D0", 0), params.get("D2", 0)
        if not d0:
            return f"{article}: не задан диаметр магистрали D0 — 1С не сможет рассчитать геометрию"
        if d2 > d0:
            return (
                f"Геометрия недопустима: диаметр врезки d2={d2:g} больше диаметра "
                f"магистрали D0={d0:g} (1С даст площадь 0 и цену 0) — проверьте "
                f"порядок диаметров в спецификации"
            )
    return None


def _finalize_row(parsed: dict) -> dict:
    """Проверяет и корректирует код материала по справочнику применимости."""
    article = parsed.get("article", "")
    code = parsed.get("material_code", "1")
    validated, warning = validate_material_code(article, code)
    parsed["material_code"] = validated
    parsed["thickness"] = normalize_thickness(
        parsed.get("thickness", 0.8),
        parsed.get("params", {}),
        f"{parsed.get('system', '')} {parsed.get('comment', '')}",
        explicit=bool(parsed.pop("thickness_explicit", False)),
    )
    if warning:
        parsed["comment"] = f"{parsed.get('comment', '')} [{warning}]".strip()
    return parsed


def _apply_ocr_warnings(result: Optional[dict], warnings: List[str]) -> Optional[dict]:
    """Добавляет OCR-предупреждения в успешную строку или в причину пропуска."""
    if not result or not warnings:
        return result
    text = "; ".join(warnings)
    if "reason" in result:
        # Словарь с причиной пропуска
        result["ocr_warning"] = text
    else:
        result["comment"] = f"{result.get('comment', '')} [{text}]".strip()
    return result


def normalize_unit_value(unit: str) -> str:
    """Приводит единицу измерения к каноническому виду.

    «п.м.»/«пм»/«м.п.» и варианты записи погонных метров → «м»,
    «шт.»/«штк» → «шт». «кг»/«т» и прочие единицы не трогаем.
    """
    u = unit.strip().lower().replace(".", "").replace(" ", "")
    if u in ("м", "m", "метр", "mtr", "пм", "мп"):
        return "м"
    if u in ("шт", "штк", "pcs", "pc"):
        return "шт"
    if u in ("м2", "м²", "кв.м", "квм", "m2"):
        return "м²"
    return unit.strip()


def parse_row(row: dict, defaults: dict, llm_cache: Optional[Dict[str, "lsc.SizeClassification"]] = None) -> Tuple[Optional[dict], Optional[dict]]:
    """Преобразует одну строку таблицы в строку для XML или причину пропуска."""
    name = str(row.get("name", "")).strip()
    size = str(row.get("size", "")).strip()
    unit = normalize_unit_value(str(row.get("unit", "")).strip().lower())
    quantity_raw = row.get("quantity", "0")
    ptype = detect_product_type(name)

    # Если размер не вынесен в отдельную колонку — ищем его в наименовании
    if not size:
        size = extract_size_token(name)
    if not size and ptype in (None, "duct"):
        # «Голый» диаметр в конце наименования (проектные спецификации):
        # «... класс воздуховодов "П" 200» → Ф200. Для фасонки не применяем:
        # «Тройник ... 315/315/200» — это не диаметр 200, а ветвь.
        m = re.search(r"\b(\d{3,4})\s*$", name)
        if m:
            size = f"Ф{m.group(1)}"

    # Исправляем типичные OCR-ошибки (потерянный ноль) в прямоугольных размерах
    size, ocr_warnings = correct_ocr_size(name, size)

    # Материал и толщина также могут быть внутри наименования
    material = str(row.get("material", defaults.get("material", "оцинкованная"))).strip()
    thickness_raw = str(row.get("thickness", "")).strip()
    thickness_explicit = bool(thickness_raw)
    if thickness_explicit:
        try:
            thickness = float(thickness_raw.replace(",", "."))
        except ValueError:
            thickness = 0.8
            thickness_explicit = False
    else:
        try:
            thickness = float(str(defaults.get("thickness", "0.8")).replace(",", "."))
        except ValueError:
            thickness = 0.8
    mat_from_name, thick_from_name = extract_material_thickness_from_name(name)
    # Если в наименовании явно указан материал — он приоритетнее значения по умолчанию
    if mat_from_name != material.lower() or re.search(r"(нерж|черн|оц|оцинк|aisi|ст3|ст\.3|ст-3)", name, re.IGNORECASE):
        material = mat_from_name
    if thick_from_name is not None:
        thickness = thick_from_name
        thickness_explicit = True

    try:
        parsed_quantity = float(str(quantity_raw).replace(",", ".").replace(" ", ""))
    except ValueError:
        parsed_quantity = None

    # Покупные позиции (КП 45–59) завод не производит: гибкие воздуховоды,
    # медные/металлопластиковые трубы и трубки, изоляция K-FLEX. Без явного
    # пропуска после нормализации «п.м.»→«м» они распознавались бы как жёсткие
    # круглые воздуховоды (например, «Труба металлопластиковая ф16х2,0» → Ф16,
    # «K-FLEX ... Ф10х6мм» → Ф10).
    name_lower = name.lower()
    if (
        ("гибкий" in name_lower and "воздуховод" in name_lower)
        or re.search(r"\bтруба\b|\bтрубка\b", name_lower)
        or "k-flex" in name_lower
    ):
        return None, _apply_ocr_warnings(
            {"name": name, "size": size, "unit": unit,
             "quantity": parsed_quantity, "material": material, "thickness": thickness,
             "reason": "Покупная позиция (гибкий воздуховод/трубопровод/изоляция) — завод не производит"},
            ocr_warnings,
        )

    # Покупные клапаны/шумоглушители (брендовые серии, электропривод) — завод
    # не производит. Проверяем до try_parse_fitting, иначе «Клапан ОЗ-60-НО-400*200»
    # стал бы дросселем 16-2-1, а «Канал-ГКП-40-20» — шумоглушителем 15-2-x.
    # Огнезадерживающие/ПРОК/КПП/КЛОП/Belimo/ВК-ЗС/ВК-ЛО — покупные (КП 229–245).
    # NB: «противопожарный дымовой» без привода — производимый 20-x (тест ниже).
    if ptype in ("damper", "silencer") and re.search(
        r"оз-\d|клара|канал-|электропривод|ик/\d|вектор"
        r"|огнезадерж|клоп|belimo|пдв-|вк-зс|вк-ло"
        r"|электромеханическим\s+приводом",
        name_lower
    ):
        return None, _apply_ocr_warnings(
            {"name": name, "size": size, "unit": unit,
             "quantity": parsed_quantity, "material": material, "thickness": thickness,
             "reason": "Покупная арматура (брендовый клапан/шумоглушитель) — завод не производит"},
            ocr_warnings,
        )

    if parsed_quantity is None:
        return None, _apply_ocr_warnings(
            {"name": name, "size": size, "unit": unit,
             "quantity": None, "material": material, "thickness": thickness,
             "reason": f"Не удалось распознать количество: {quantity_raw}"},
            ocr_warnings,
        )
    quantity = parsed_quantity
    if quantity <= 0:
        # Молча подменять 0 на 1 нельзя — количество теряется без следа.
        # Ноль здесь означает сбой извлечения (см. parse_spec_text_blocks).
        return None, _apply_ocr_warnings(
            {"name": name, "size": size, "unit": unit,
             "quantity": quantity, "material": material, "thickness": thickness,
             "reason": "Не удалось распознать количество (0)"},
            ocr_warnings,
        )

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

        return _apply_ocr_warnings(_finalize_row({
            "article": explicit_article,
            "params": dict(explicit_params),
            "quantity": pieces,
            "material_code": material_to_code(material),
            "thickness": thickness,
            "thickness_explicit": thickness_explicit,
            "connection_0": connections[0],
            "connection_1": connections[1],
            "connection_2": connections[2],
            "connection_3": connections[3],
            "system": row.get("system", ""),
            "comment": f"{name} {size}".strip(),
        }), ocr_warnings), None

    material_code = material_to_code(material)

    # Попытка распознать КСД-адаптер. Толщина — по таблице техотдела от
    # максимального размера сечения (600x600 -> 0.7), явная толщина из
    # проекта сохраняется как есть.
    ksd_thickness = thickness
    if not thickness_explicit:
        m = re.match(r"\s*(\d+)\s*[xх×*]\s*(\d+)", size)
        ksd_thickness = gost_min_thickness(max(int(m.group(1)), int(m.group(2)))) if m else 0.7
    ksd = try_parse_ksd(size, name, material_code=material_code, thickness=ksd_thickness)
    if ksd:
        ksd["quantity"] = int(quantity) if unit in ("шт", "штук", "pcs", "pc", "шт.") else max(1, int(round(quantity)))
        ksd["thickness_explicit"] = thickness_explicit
        return _apply_ocr_warnings(_finalize_row(ksd), ocr_warnings), None

    # Попытка распознать фасонное изделие / арматуру (в том числе для м²)
    fitting = try_parse_fitting(name, size, unit, quantity, material_code=material_code, thickness=thickness)
    if fitting:
        fitting["thickness_explicit"] = thickness_explicit
        return _apply_ocr_warnings(_finalize_row(fitting), ocr_warnings), None

    # Агрегатные строки фасонных изделий / оборудование без артикула
    if ptype == "aggregate_fittings" or unit in ("м2", "м²", "кв.м", "кв м"):
        return None, _apply_ocr_warnings(
            {"name": name, "size": size, "unit": unit, "reason": classify_skip(name, size, unit, ptype)},
            ocr_warnings,
        )

    # Оборудование (решетки, диффузоры, фильтры, вентиляторы и т.п.) — в XML не
    # попадает, иначе размер в имени превращал бы его в воздуховод
    # (например, «Наружные решетки … 550*400» → прямоугольный воздуховод 1-2-1).
    if ptype in ("diffuser", "grille", "shutter", "filter", "fan", "roof_cap", "ksd", "plenum"):
        return None, _apply_ocr_warnings(
            {"name": name, "size": size, "unit": unit, "reason": classify_skip(name, size, unit, ptype)},
            ocr_warnings,
        )

    # Строки без признаков воздуховода (шкафы автоматики, реле, узлы, крепеж и
    # прочее оборудование): раньше любой размер в имени («ШСАУ … Ф200») делал из
    # них круглый воздуховод 1-1-2.
    if ptype is None and name and not re.search(r"воздуховод|спирально|прямошовн", name_lower):
        return None, _apply_ocr_warnings(
            {"name": name, "size": size, "unit": unit, "reason": classify_skip(name, size, unit, ptype)},
            ocr_warnings,
        )

    # Воздуховоды
    section, dims = parse_size(size)
    if not section and llm_cache:
        llm_result = llm_cache.get(size)
        if llm_result is not None and llm_result.adoptable:
            logger.info("LLM-фолбэк: размер %r усыновлён (%s)", size, llm_result.format_class)
            section, dims = llm_result.section, dict(llm_result.dims)
    if section:
        if section == "rectangular" and dims.get("A0", 0) < dims.get("B0", 0):
            # Правило техотдела: воздуховоды — от большего размера к меньшему
            dims["A0"], dims["B0"] = dims["B0"], dims["A0"]
        if section == "round":
            # Выбор артикула по типу круглого воздуховода
            name_lower = name.lower()
            if "прямошовный" in name_lower:
                article = "1-1-1"
            elif "спирально-навивной" in name_lower:
                article = "1-1-2"
            else:
                article = DEFAULT_ROUND_ARTICLE
            # Ø ≥ 500 или стенка ≥ 0.9 — прямошовной из рулона по ГОСТ 16523
            # (КП №1090 поз.198: Ф630-1250 Рулон оц. 1.00)
            if article == "1-1-2" and (thickness >= 0.9 or dims.get("D0", 0) >= 500):
                article = "1-1-1"
            length_mm = DEFAULT_ROUND_LENGTH_MM
        else:
            article = DEFAULT_RECT_ARTICLE
            length_mm = DEFAULT_RECT_LENGTH_MM

        material_code = material_to_code(material)
        connections = default_connections(section)

        # Если размер уже содержит длину (например, Ф100-3000), используем её
        length_mm = dims.get("L0", length_mm)

        if unit in ("м", "m", "метр", "mtr"):
            # Единое правило техотдела (ответ по КП №1090): все воздуховоды
            # считаются отрезками 1250 мм с округлением вверх, кроме
            # спирально-навивного круглого — он 3 м. Чёрная сталь — без
            # исключений (293x187 м1.6 → 2×1250, 400x400 м2.0 → 2×1250).
            if article == "1-1-2":
                pieces, length_mm = meters_to_pieces(quantity, length_mm)
            else:
                # явная длина в размере (Ф100-3000 / 400x400-1000) уважаем
                base_mm = length_mm if "L0" in dims else 1250
                pieces, length_mm = meters_to_pieces(quantity, base_mm)
        elif unit in ("шт", "штук", "pcs", "pc", "шт."):
            pieces = int(quantity)
        else:
            return None, _apply_ocr_warnings(
                {"name": name, "size": size, "unit": unit, "reason": classify_skip(name, size, unit, ptype)},
                ocr_warnings,
            )

        dims["L0"] = length_mm

        return _apply_ocr_warnings(_finalize_row({
            "article": article,
            "params": dims,
            "quantity": pieces,
            "material_code": material_code,
            "thickness": thickness,
            "thickness_explicit": thickness_explicit,
            "connection_0": connections[0],
            "connection_1": connections[1],
            "connection_2": connections[2],
            "connection_3": connections[3],
            "system": row.get("system", ""),
            "comment": f"{name} {size}",
        }), ocr_warnings), None

    # Диффузоры и прочее без возможности автозагрузки
    reason = classify_skip(name, size, unit, ptype)
    skip_dict = {"name": name, "size": size, "unit": unit, "reason": reason}
    if reason == "Не удалось распознать размер / тип" and llm_cache:
        llm_result = llm_cache.get(size)
        if llm_result is not None:
            skip_dict["reason"] = lsc.REASON_LLM_REJECTED
            skip_dict["llm_format_class"] = llm_result.format_class
            skip_dict["llm_detail"] = llm_result.detail
            skip_dict["llm_raw"] = llm_result.raw_response[:2000]
            logger.warning(
                "LLM-классификация отклонена: %r → %s (%s)",
                size, llm_result.format_class, llm_result.detail,
            )
    return None, _apply_ocr_warnings(skip_dict, ocr_warnings)


def process_rows(
    rows: List[dict],
    header: Optional[dict] = None,
    defaults: Optional[dict] = None,
) -> Tuple[str, List[dict], List[dict]]:
    """Принимает список строк спецификации и возвращает XML-строку, пропущенные и успешные строки.

    Args:
        rows: список словарей с ключами name, size, unit, quantity, material, thickness.
        header: заголовок заказа для XML (numberDate, numberOrder, INN, ...).
        defaults: значения по умолчанию для material и thickness.

    Returns:
        Кортеж (xml_string, skipped_list, success_rows).
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

    llm_cache: Optional[Dict[str, lsc.SizeClassification]] = None
    if lsc.llm_enabled():
        candidates = []
        seen = set()
        for row in rows:
            size = str(row.get("size", "")).strip()
            if size and size not in seen and parse_size(size)[0] is None:
                seen.add(size)
                candidates.append(size)
        if candidates:
            logger.info("LLM-фолбэк: классификация %d нераспознанных размеров", len(candidates))
            llm_cache = lsc.classify_sizes_batch(candidates)

    for row in rows:
        name = str(row.get("name", "")).strip()
        size = str(row.get("size", "")).strip()
        if not name and not size:
            continue
        parsed, skip = parse_row(row, defaults, llm_cache=llm_cache)
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

    # Валидация XML-характеристик по строкам: одна битая строка (например,
    # не хватает обязательного параметра) уходит в skipped с причиной,
    # а не роняет весь файл без ответа пользователю.
    article_map = load_article_mapping()
    valid_rows: List[dict] = []
    for row in success_rows:
        mapping = article_map.get(row["article"])
        geo_reason = geometry_skip_reason(row["article"], row.get("params", {}))
        if geo_reason is None:
            try:
                if mapping is None:
                    raise ValueError(
                        f"Артикул {row['article']} не найден в сопоставлении"
                    )
                build_characteristic(row["params"], mapping)
            except ValueError as exc:
                geo_reason = str(exc)
        if geo_reason is not None:
            skipped.append({
                "name": row.get("comment", ""),
                "size": "",
                "unit": "шт",
                "quantity": row.get("quantity", ""),
                "article": row["article"],
                "reason": geo_reason,
            })
            continue
        valid_rows.append(row)

    xml_text = generate_order_xml(header, valid_rows)
    return xml_text, skipped, valid_rows


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
        if is_project_spec_xlsx(df):
            rows = parse_project_spec_xlsx(input_path)
            xml_text, skipped, success_rows = process_rows(
                rows, header=header, defaults=defaults
            )
            if not success_rows:
                logger.warning("Не удалось распознать ни одной строки.")
                return success_rows, skipped

            Path(output_xml).write_text(xml_text, encoding="utf-8")
            report_path = str(Path(output_xml).with_suffix("")) + "_skipped.json"
            with open(report_path, "w", encoding="utf-8") as f:
                json.dump(skipped, f, ensure_ascii=False, indent=2)

            logger.info("Сгенерировано строк: %d", len(success_rows))
            logger.info("Пропущено строк: %d", len(skipped))
            return success_rows, skipped
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

    xml_text, skipped, success_rows = process_rows(rows, header=header, defaults=defaults)

    if not success_rows:
        logger.warning("Не удалось распознать ни одной строки.")
        return success_rows, skipped

    Path(output_xml).write_text(xml_text, encoding="utf-8")

    # Сохраняем отчёт о пропущенных позициях
    report_path = str(Path(output_xml).with_suffix("")) + "_skipped.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(skipped, f, ensure_ascii=False, indent=2)

    logger.info("Сгенерировано строк: %d", len(success_rows))
    logger.info("Пропущено строк: %d", len(skipped))
    if skipped:
        logger.info("Пропущенные позиции:")
        for s in skipped:
            logger.info("  - %s %s (%s): %s", s["name"], s["size"], s["unit"], s["reason"])
    logger.info("XML сохранён: %s", output_xml)
    logger.info("Отчёт по пропускам: %s", report_path)

    return success_rows, skipped


def main():
    if len(sys.argv) < 2:
        logger.error("Использование: python process_specification_table.py <input.csv> [output.xml]")
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
