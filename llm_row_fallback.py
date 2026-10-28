"""LLM-фолбэк на уровне строки: skipped-строки без размера → kimi → нормализация.

Skipped-строки, у которых не удалось распознать размер/тип (reason из
QUALIFYING_REASON_PREFIXES), отправляются в kimi одним батчем. Модель
возвращает JSON-массив `{name, size}` в том же порядке. Каждая строка
проходит детерминированную валидацию: якорь имени (общий алфавитно-цифровой
токен ≥3 символов с исходным наименованием) и размер, подтверждённый
подстрокой исходного name/size. Количество и единица измерения ВСЕГДА берутся
из исходной skipped-строки — LLM их не возвращает.

Любой сбой (runner бросил, невалидный JSON, истёк дедлайн) → [] + warning,
исключений наружу нет (по образцу llm_document_fallback).
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Callable, Dict, List, Optional

import llm_size_classifier
from config import get_config
from llm_client import (
    assistant_content as _assistant_content,
    run_kimi as _run_kimi,
)

logger = logging.getLogger(__name__)

# Причины skipped-строк, которые квалифицируются для LLM-нормализации.
QUALIFYING_REASON_PREFIXES = ("Отсутствует размер", "Не удалось распознать размер / тип")

# Агрегатные единицы: смета в м² без поканальных размеров LLM не лечит.
M2_UNITS = ("м2", "м²")

__all__ = [
    "QUALIFYING_REASON_PREFIXES",
    "M2_UNITS",
    "get_row_fallback_config",
    "row_fallback_enabled",
    "qualifying_skipped",
    "validate_normalized",
    "normalize_rows",
]


def get_row_fallback_config(cfg: Optional[Dict] = None) -> Dict:
    """Секция ``llm.row_fallback`` конфигурации (по образцу get_doc_fallback_config)."""
    llm_cfg = (get_config().get("llm") or {}) if cfg is None else cfg
    return dict((llm_cfg or {}).get("row_fallback") or {})


def row_fallback_enabled(cfg: Optional[Dict] = None) -> bool:
    """Гейтинг: общий гейт llm-секции И флаг row_fallback.enabled.

    ``llm_enabled`` проверяет llm-секцию (enabled/backend/command), поэтому
    вызывается без аргументов; row_fallback-секция передаётся только в
    get_row_fallback_config."""
    return bool(llm_size_classifier.llm_enabled()) and bool(
        get_row_fallback_config(cfg).get("enabled")
    )


def qualifying_skipped(skipped: List[Dict]) -> List[Dict]:
    """Skipped-строки, пригодные для LLM-нормализации: reason начинается с
    одного из QUALIFYING_REASON_PREFIXES; для «Отсутствует размер» единица не
    должна быть агрегатной (м2/м²)."""
    out: List[Dict] = []
    for row in skipped or []:
        if not isinstance(row, dict):
            continue
        reason = str(row.get("reason") or "")
        matched = next(
            (p for p in QUALIFYING_REASON_PREFIXES if reason.startswith(p)), None
        )
        if matched is None:
            continue
        if matched == "Отсутствует размер" and str(row.get("unit") or "").strip() in M2_UNITS:
            continue
        out.append(row)
    return out


def _core_tokens(s: str) -> set:
    """Алфавитно-цифровые токены длины ≥3 (lower) — ядро для проверки якоря."""
    return {t for t in re.findall(r"[a-zа-яё0-9]+", (s or "").lower()) if len(t) >= 3}


def validate_normalized(original: Dict, row: Dict) -> Optional[Dict]:
    """Детерминированная проверка строки LLM якорями исходной skipped-строки.

    None — отброс: пустое имя или нет общего токена ядра с исходным
    наименованием (LLM переименовала позицию). size принимается только если
    подстрока исходного name/size (lower), иначе обнуляется. unit/quantity
    всегда из original — LLM не является источником чисел."""
    orig_name = str(original.get("name") or "")
    name = str(row.get("name") or "").strip()
    if not name:
        return None
    if not (_core_tokens(name) & _core_tokens(orig_name)):
        return None  # полное переименование — отбрасываем
    size = str(row.get("size") or "").strip()
    if size:
        haystack = (orig_name + " " + str(original.get("size") or "")).lower()
        if size.lower() not in haystack:
            size = ""  # размер не из исходника — обнуляем, строка жива
    return {
        "name": name,
        "size": size,
        "unit": original.get("unit"),
        "quantity": original.get("quantity"),
    }


def _build_prompt(rows: List[Dict]) -> str:
    items = "\n".join(
        f"{i}: {json.dumps(str(r.get('name') or ''), ensure_ascii=False)}"
        for i, r in enumerate(rows)
    )
    return (
        "Ты нормализуешь наименования воздуховодов из проектной спецификации вентиляции.\n"
        f"Даны исходные наименования (номер: строка):\n{items}\n\n"
        "Верни СТРОГО один JSON-массив без пояснений, markdown и кода — по одному "
        "объекту для КАЖДОЙ строки, в ТОМ ЖЕ порядке:\n"
        '{"name": "...", "size": "..."}\n'
        "Правила:\n"
        '- «Возд.» / «Возд.оц» расшифровывай как «Воздуховод оцинкованный».\n'
        '- size — размер ДОСЛОВНО из исходного наименования (например "150х150", '
        '"ф125"); если размера нет — "".\n'
        "- Не выдумывай размеры и толщины; бери только то, что есть в исходной строке.\n"
        "- Количество и единицы измерения не возвращай — они берутся из исходных данных."
    )


def _loads_rows_array(content: str) -> List[Dict]:
    """Парсит JSON-массив из ответа модели (с терпимостью к мусору вокруг)."""
    data = json.loads(content)
    if not isinstance(data, list):
        raise ValueError("ответ LLM — не JSON-массив")
    return data


def normalize_rows(
    rows: List[Dict],
    cfg: Optional[Dict] = None,
    runner: Optional[Callable] = None,
    deadline: Optional[float] = None,
) -> List[Dict]:
    """Skipped-строки → один батч-вызов LLM → валидные нормализованные строки.

    runner(prompt, cfg) -> stdout (stream-json) инжектируется в тестах.
    LLM возвращает JSON-массив `{name, size}` в том же порядке; каждая пара
    (original, llm_row) проходит validate_normalized. Любой сбой / невалидный
    JSON (после max_retries попыток) / истёкший deadline → [] + logger.warning,
    исключений наружу нет."""
    cfg = get_row_fallback_config() if cfg is None else dict(cfg)
    run = _run_kimi if runner is None else runner
    prompt = _build_prompt(rows)
    max_retries = max(0, int(cfg.get("max_retries", 2)))

    for attempt in range(max_retries + 1):
        attempt_cfg = dict(cfg)
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                logger.warning("llm_row_fallback.failed: дедлайн истёк (попытка %d)", attempt + 1)
                return []
            attempt_cfg["timeout"] = max(1, int(remaining))
        try:
            raw = run(prompt, attempt_cfg)
        except Exception as exc:  # noqa: BLE001 — runner обязан не ронять пайплайн
            logger.warning(
                "llm_row_fallback.failed: вызов неудачен (попытка %d/%d): %s",
                attempt + 1, max_retries + 1, exc,
            )
            continue
        if deadline is not None and time.monotonic() >= deadline:
            logger.warning("llm_row_fallback.failed: дедлайн истёк после вызова LLM")
            return []
        try:
            content = _assistant_content(raw)
            llm_rows = _loads_rows_array(content)
        except (ValueError, json.JSONDecodeError) as exc:
            logger.warning(
                "llm_row_fallback.failed: ответ LLM не распознан (попытка %d/%d): %s; raw: %.300s",
                attempt + 1, max_retries + 1, exc, raw,
            )
            continue
        out: List[Dict] = []
        for original, llm_row in zip(rows, llm_rows):
            if not isinstance(llm_row, dict):
                logger.warning("llm_row_fallback.failed: элемент не объект: %r", llm_row)
                continue
            parsed = validate_normalized(original, llm_row)
            if parsed is None:
                logger.warning(
                    "llm_row_fallback.failed: строка отброшена (якорь не подтверждён): %r", llm_row,
                )
                continue
            out.append(parsed)
        logger.info("llm_row_fallback.normalized: %d/%d строк", len(out), len(rows))
        return out

    logger.warning("llm_row_fallback.failed: исчерпаны попытки (%d)", max_retries + 1)
    return []
