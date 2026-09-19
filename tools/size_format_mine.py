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

import llm_size_classifier as lsc
import size_notations as szn
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


_MAX_SYMBOL_LEN = 3
_PATCH_SECTIONS = ("add_prefixes", "add_suffixes", "add_separators")


def _sanitize_entry(entry, existing: set) -> str | None:
    e = str(entry or "").strip()
    if not e or len(e) > _MAX_SYMBOL_LEN:
        return None
    if re.search(r"\d", e):           # ЖЁСТКОЕ ОГРАНИЧЕНИЕ: никаких цифр
        return None
    low = e.lower()
    if low in {x.lower() for x in existing}:
        return None                   # строго аддитивно: дубликаты отбрасываем
    existing_chars = {ch for x in existing for ch in x.lower()}
    if all(ch in existing_chars for ch in low):
        return None                   # не добавляет ни одного нового символа (напр. "øØ")
    return e


def sanitize_proposals(prop: Dict, current: Dict) -> Dict:
    """Очищает сырой ответ kimi до аддитивного символьного патча.
    Возвращает ТОЛЬКО известные add_*-секции; всё числовое/неизвестное отброшено."""
    prop = prop if isinstance(prop, dict) else {}
    mapping = {
        "add_prefixes": "diameter_prefixes",
        "add_suffixes": "diameter_suffixes",
        "add_separators": "separators",
    }
    out: Dict[str, List[str]] = {}
    for section in _PATCH_SECTIONS:
        raw_list = prop.get(section) or []
        if not isinstance(raw_list, list):
            continue
        existing = set(current.get(mapping[section]) or [])
        cleaned = []
        for entry in raw_list:
            e = _sanitize_entry(entry, existing | set(cleaned))
            if e is not None:
                cleaned.append(e)
        out[section] = cleaned
    return out


def _propose_prompt(clusters: Dict[str, int], current: Dict) -> str:
    items = "\n".join(f"{json.dumps(s, ensure_ascii=False)}: {n} вхождений"
                      for s, n in clusters.items())
    return (
        "Ты помощник по расширению словаря вариантов записи размеров воздуховодов.\n"
        "Ниже — кластеры НЕРАСПОЗНАННЫХ строк размеров из производственных спецификаций "
        "(строка: число вхождений):\n"
        f"{items}\n\n"
        f"Текущий конфиг: diameter_prefixes={current['diameter_prefixes']}, "
        f"diameter_suffixes={current['diameter_suffixes']}, separators={current['separators']}.\n\n"
        "Задача: предложи, какие СИМВОЛЬНЫЕ записи добавить, чтобы каскад regex мог "
        "распознать эти строки. Верни строго один JSON-объект без пояснений:\n"
        '{"add_prefixes":["..."],"add_suffixes":["..."],"add_separators":["..."]}\n'
        "Правила: только символы-префиксы диаметра (до числа), символы-суффиксы (после числа), "
        "разделители сторон прямоугольника; 1-3 символа; НИКАКИХ цифр, длин, диапазонов; "
        "не предлагай то, что уже есть в конфиге; если кластер — это не символьная "
        "запись (например слэш-форма '315/315/160'), предложи пустые списки.\n"
        'Пример для "∅315": {"add_prefixes":["∅"],"add_suffixes":[],"add_separators":[]}'
    )


def propose_additions(clusters: Dict[str, int], runner=None) -> Dict:
    """kimi предлагает аддитивные символьные записи. Любой сбой → {} (пайплайн не падает)."""
    if not clusters:
        return {}
    cfg = lsc.get_llm_config()
    if not lsc.llm_enabled(cfg):
        return {}
    run = lsc._run_kimi if runner is None else runner
    n = szn.get_notations()
    current = {
        "diameter_prefixes": list(n["diameter_prefixes"]),
        "diameter_suffixes": list(n["diameter_suffixes"]),
        "separators": list(n["separators"]),
    }
    prompt = _propose_prompt(clusters, current)
    try:
        content = lsc._assistant_content(run(prompt, cfg))
        import json as _json
        obj, _ = _json.JSONDecoder().raw_decode(content[content.find("{"):])
        return obj if isinstance(obj, dict) else {}
    except Exception as exc:  # noqa: BLE001
        print(f"propose_additions: kimi недоступна: {exc}")
        return {}


def gate_check(patch, cluster_strings, run_pytest=None):
    raise NotImplementedError  # Task 3
