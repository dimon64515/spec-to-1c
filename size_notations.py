"""Загрузка и сборка паттернов вариантов записи размеров из config/size_notations.yaml.

Единая точка знания о том, какие префиксы/суффиксы/разделители размеров
существуют в проектных спецификациях. Заменяет литералы, ранее
размазанные по process_specification_table.py.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Dict, List, Tuple

import yaml

DEFAULT_PATH = Path(__file__).parent / "config" / "size_notations.yaml"

_CACHE: Dict | None = None
_PATTERN_CACHE: Dict[str, re.Pattern] = {}


def _load() -> Dict:
    global _CACHE
    if _CACHE is None:
        path = Path(os.environ.get("SPEC_TO_1C_SIZE_NOTATIONS", DEFAULT_PATH))
        with open(path, "r", encoding="utf-8") as f:
            _CACHE = yaml.safe_load(f)
    return _CACHE


def get_notations() -> Dict:
    return _load()


def reload_notations() -> Dict:
    global _CACHE
    _CACHE = None
    _PATTERN_CACHE.clear()
    return _load()


def _prefixes() -> List[str]:
    return list(_load()["diameter_prefixes"])


def prefix_group() -> str:
    """Alternation префиксов диаметра, длинные первыми (regex priority)."""
    return "|".join(re.escape(p) for p in sorted(_prefixes(), key=len, reverse=True))


def word_prefix_group() -> str:
    """Буквенные префиксы — для паттернов с границей слова (\b)."""
    words = [p for p in _prefixes() if p.isalnum()]
    return "|".join(re.escape(p) for p in sorted(words, key=len, reverse=True))


def suffix_char_class() -> str:
    return "[" + "".join(re.escape(s) for s in _load()["diameter_suffixes"]) + "]"


def round_diameter_pattern() -> re.Pattern:
    """Префиксный ИЛИ суффиксный диаметр. Эквивалент исторического
    (?:\\d{2,5}\\s*[øØ⌀](?!\\s*\\d)|(?:dn|d|дн|ду|д|ф|ø|⌀)\\s*\\d{2,5}),
    но префикс не должен быть концом слова: «переход 125/100», «Отвод 45»
    иначе дают ложный диаметр из буквы «д»/«d» конца слова."""
    key = "round_diameter"
    if key not in _PATTERN_CACHE:
        _PATTERN_CACHE[key] = re.compile(
            rf"(?:\d{{2,5}}\s*{suffix_char_class()}(?!\s*\d)"
            rf"|(?<![\w])(?:{prefix_group()})\s*\d{{2,5}})",
            re.IGNORECASE,
        )
    return _PATTERN_CACHE[key]


def separators() -> List[str]:
    return list(_load()["separators"])


def litened_sizes() -> Dict[str, Tuple[int, int]]:
    return {k: tuple(v) for k, v in _load()["code_tables"]["litened"]["sizes"].items()}


def litened_lengths() -> Dict[str, int]:
    return dict(_load()["code_tables"]["litened"]["lengths"])


def min_rect_side_mm() -> int:
    return int(_load()["ocr"]["min_rect_side_mm"])


def validate_dimensions(dims: Dict[str, float], section: str | None) -> bool:
    """Guard-валидация извлечённых размеров (используется LLM-фолбэком,
    в каскад не встроена — поведение каскада не меняем)."""
    cfg = _load()["validation"]
    lo, hi = int(cfg["min_side_mm"]), int(cfg["max_side_mm"])
    for key, value in (dims or {}).items():
        if not isinstance(value, (int, float)):
            return False
        if key == "L0":
            if not (10 <= value <= hi * 4):
                return False
        elif key == "U0":
            if not (0 < value <= 180):
                return False
        elif key.startswith(("A", "B", "D", "R")):
            if not (lo <= value <= hi):
                return False
    return True
