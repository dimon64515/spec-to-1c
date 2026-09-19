"""Автообучение вариантов записи размеров из skipped-отчётов (петля C).

Цикл: агрегация skipped-отчётов → кластеры нераспознанных строк → kimi
предлагает АДДИТИВНЫЕ символьные записи для config/size_notations.yaml
(без единой цифры размера) → gate: кластер парсится каскадом + полный pytest
зелёный → коммит с provenance. Gate красный → патч отброшен, кластер в отчёте.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Dict, List

from config import get_config

ROOT = Path(__file__).resolve().parent.parent

UNRECOGNIZED_REASONS = (
    "Не удалось распознать размер / тип",
    "LLM-классификация отклонена",
)


def get_learning_config() -> Dict:
    return dict(get_config().get("learning") or {})


def load_skipped_sizes(report_paths: List[str]) -> "Counter[str]":
    """Собирает счётчики size из skipped-отчётов по причинам нераспознанного размера."""
    counter: Counter[str] = Counter()
    for path in report_paths:
        try:
            rows = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            if row.get("reason") not in UNRECOGNIZED_REASONS:
                continue
            size = str(row.get("size") or "").strip()
            if size:
                counter[size] += 1
    return counter


def stable_clusters(counter: "Counter[str]", min_occurrences: int) -> Dict[str, int]:
    return {s: n for s, n in Counter(counter).most_common() if n >= min_occurrences}


_SYMBOL_RE = re.compile(r"[^\d\s]+")


def extract_symbol_candidates(s: str) -> List[str]:
    """Не-цифровые токены вокруг чисел: '∅315' → ['∅'], '1250✕800' → ['✕'],
    'Ду 315' → ['Ду']. Цифры/пробелы игнорируются."""
    return [t for t in _SYMBOL_RE.findall(s) if not t.isdigit()]


def sanitize_proposals(prop, current):
    raise NotImplementedError  # Task 2


def gate_check(patch, cluster_strings, run_pytest=None):
    raise NotImplementedError  # Task 3
