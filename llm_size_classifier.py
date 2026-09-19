"""LLM-фолбэк классификации форматов записи размеров.

Жёсткое ограничение: LLM никогда не генерирует и не переписывает цифры
размеров. Модель возвращает только format_class и spans (смещения подстрок
в ИСХОДНОЙ строке); цифры извлекаются детерминированно int(source[start:end]).
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from config import get_config

REASON_LLM_REJECTED = "LLM-классификация отклонена"

FORMAT_CLASSES = [
    "round_diameter",  # один диаметр: «Ø315», «315ø», «Ду315»
    "rect_axb",        # прямоугольное сечение: «1250x800»
    "tee_axbxc",       # тройник: «315/315/160»
    "reducer_pair",    # переход: «250/160»
    "silencer_code",   # кодовая форма: «LITENED 50-25»
    "unknown",
]

# Классы, применимые к воздуховодам (duct-путь parse_row). Остальные
# парсятся для лога/отчёта, но не усыновливаются как воздуховод.
ADOPTABLE_CLASSES = ("round_diameter", "rect_axb")

logger = __import__("logging").getLogger(__name__)


@dataclass
class SizeClassification:
    status: str                      # "ok" | "rejected"
    format_class: str = "unknown"
    dims: Dict[str, float] = field(default_factory=dict)
    section: Optional[str] = None    # "round" | "rectangular" | None
    adoptable: bool = False
    raw_response: str = ""
    detail: str = ""


def get_llm_config() -> Dict:
    return dict(get_config().get("llm") or {})


def llm_enabled(cfg: Optional[Dict] = None) -> bool:
    cfg = get_llm_config() if cfg is None else cfg
    if not cfg.get("enabled"):
        return False
    if cfg.get("backend", "kimi-cli") != "kimi-cli":
        return False
    command = str(cfg.get("command") or "").strip()
    return bool(command) and shutil.which(command) is not None


def classify_sizes_batch(strings, cfg=None, runner=None):
    raise NotImplementedError  # Task 2
