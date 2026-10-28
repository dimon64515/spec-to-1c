"""LLM-фолбэк на уровне документа: сырой текст PDF → kimi → JSON со spans.

При полном промахе/деградации основного парсера сырой текст документа
отправляется в kimi-cli; модель возвращает JSON-массив строк спецификации
с spans — смещениями подстрок В ИСХОДНОМ тексте. Все значения (name, size,
quantity) вырезаются детерминированно text[a:b]; LLM никогда не является
источником чисел.

Любой сбой (runner бросил, невалидный JSON, истёк дедлайн) → [] + warning,
исключений наружу нет.
"""

from __future__ import annotations

import json
import logging
import time
from collections import Counter
from typing import Any, Callable, Dict, List, Optional, Tuple

import llm_size_classifier
from config import get_config
from llm_client import (
    assistant_content as _assistant_content,
    run_kimi as _run_kimi,
)

logger = logging.getLogger(__name__)

LlmDocRow = Dict[str, Any]

# Единицы, допустимые для позиций спецификации (м2 = «м²» в разных записях).
VALID_UNITS = ("шт", "м", "м2", "м²")

MIN_TEXT_CHARS = 200       # меньше — считаем, что текстового слоя нет, идём в OCR
DEFAULT_MAX_CHARS = 60000  # обрезка входа для kimi

__all__ = [
    "LlmDocRow",
    "get_doc_fallback_config",
    "doc_fallback_enabled",
    "extract_document_text",
    "parse_document_with_llm",
    "build_escalation_reason",
]


def get_doc_fallback_config(cfg: Optional[Dict] = None) -> Dict:
    """Секция ``llm.doc_fallback`` конфигурации (по образцу get_llm_config)."""
    llm_cfg = (get_config().get("llm") or {}) if cfg is None else cfg
    return dict((llm_cfg or {}).get("doc_fallback") or {})


def doc_fallback_enabled(cfg: Optional[Dict] = None) -> bool:
    """Гейтинг: глобальный llm_enabled И флаг doc_fallback.enabled."""
    return bool(llm_size_classifier.llm_enabled(cfg)) and bool(
        get_doc_fallback_config(cfg).get("enabled")
    )


def extract_document_text(pdf_path: str, max_chars: int = DEFAULT_MAX_CHARS) -> str:
    """Сырой текст документа: текстовый слой постранично; если суммарно
    меньше MIN_TEXT_CHARS символов — OCR-строки, сериализованные как
    «name size unit quantity». Результат обрезается до max_chars."""
    import pdf_spec_extractor

    pages = pdf_spec_extractor.extract_text_lines_from_pdf(pdf_path)
    text = "\n".join(
        "\n".join(pages[p]) for p in sorted(pages)
    )
    if len(text) < MIN_TEXT_CHARS:
        ocr_pages = pdf_spec_extractor.extract_ocr_spec_rows(pdf_path)
        lines = []
        for p in sorted(ocr_pages):
            for row in ocr_pages[p]:
                lines.append(
                    "{} {} {} {}".format(
                        row.get("name") or "",
                        row.get("size") or "",
                        row.get("unit") or "",
                        row.get("quantity") if row.get("quantity") is not None else "",
                    ).strip()
                )
        if lines:
            text = "\n".join(lines)
            logger.info("llm_doc_fallback.ocr: извлечено %d OCR-строк из %s", len(lines), pdf_path)
    return text[:max_chars]


def _build_prompt(text: str) -> str:
    return (
        "Ты извлекаешь строки оборудования из проектной спецификации вентиляции.\n"
        "Ниже — сырой текст документа (может содержать мусор, колонтитулы, разрывы).\n\n"
        f"<document>\n{text}\n</document>\n\n"
        "Верни СТРОГО один JSON-массив без пояснений, markdown и кода. Каждый элемент:\n"
        '{"name": "...", "name_span": [a, b], "size": "...", "size_span": [c, d], '
        '"unit": "...", "quantity": N, "quantity_span": [e, f]}\n'
        "Правила:\n"
        "- spans — смещения подстрок В ПРЕДОСТАВЛЕННОМ ТЕКСТЕ (start включительно, "
        "end исключительно); name = текст[a:b] должен давать полное наименование, "
        "quantity_span должен покрывать ТОЛЬКО целое число.\n"
        "- НИКОГДА не выдумывай значения и числа: spans обязаны указывать на реальные "
        "подстроки документа. Если размера нет — size_span = [x, x] (пустой), "
        "size = \"\".\n"
        '- unit — одно из: "шт", "м", "м2", "м²".\n'
        "- Номера позиций, единицы измерения и прочие служебные колонки в name "
        "не включай."
    )


def _parse_span(value: Any, source_len: int, allow_empty: bool = False) -> Optional[Tuple[int, int]]:
    """Валидирует пару [start, end]. None — span невалиден."""
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    try:
        start, end = int(value[0]), int(value[1])
    except (TypeError, ValueError):
        return None
    if start == end and allow_empty:
        return start, end
    if not (0 <= start < end <= source_len):
        return None
    return start, end


def _row_from_spans(row: Dict, text: str) -> Optional[LlmDocRow]:
    """Детерминированная сборка LlmDocRow из spans исходного текста.
    None — строка отброшена."""
    name_span = _parse_span(row.get("name_span"), len(text))
    if name_span is None:
        return None
    name = text[name_span[0]:name_span[1]].strip()
    if not name:
        return None

    size_span = _parse_span(row.get("size_span"), len(text), allow_empty=True)
    if size_span is None:
        return None
    size = text[size_span[0]:size_span[1]].strip()

    qty_span = _parse_span(row.get("quantity_span"), len(text))
    if qty_span is None:
        return None
    token = text[qty_span[0]:qty_span[1]].strip()
    try:
        quantity = int(token)
    except ValueError:
        return None
    if quantity <= 0:
        return None

    unit = str(row.get("unit") or "").strip()
    if unit not in VALID_UNITS:
        return None
    return {"name": name, "size": size, "unit": unit, "quantity": quantity}


def _loads_rows_array(content: str) -> List[Dict]:
    """Парсит JSON-массив из ответа модели (с терпимостью к мусору вокруг)."""
    data = json.loads(content)
    if not isinstance(data, list):
        raise ValueError("ответ LLM — не JSON-массив")
    return data


def parse_document_with_llm(
    text: str,
    cfg: Optional[Dict] = None,
    runner: Optional[Callable] = None,
    deadline: Optional[float] = None,
) -> List[LlmDocRow]:
    """Текст документа → LLM → список LlmDocRow, собранных строго по spans.

    runner(prompt, cfg) -> stdout (stream-json) инжектируется в тестах.
    Любой сбой / невалидный JSON (после max_retries попыток) / истёкший
    deadline → [] + logger.warning, исключений наружу нет."""
    cfg = get_doc_fallback_config() if cfg is None else dict(cfg)
    run = _run_kimi if runner is None else runner
    prompt = _build_prompt(text)
    max_retries = max(0, int(cfg.get("max_retries", 2)))

    for attempt in range(max_retries + 1):
        attempt_cfg = dict(cfg)
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                logger.warning("llm_doc_fallback.failed: дедлайн истёк (попытка %d)", attempt + 1)
                return []
            attempt_cfg["timeout"] = max(1, int(remaining))
        try:
            raw = run(prompt, attempt_cfg)
        except Exception as exc:  # noqa: BLE001 — runner обязан не ронять пайплайн
            logger.warning(
                "llm_doc_fallback.failed: вызов неудачен (попытка %d/%d): %s",
                attempt + 1, max_retries + 1, exc,
            )
            continue
        if deadline is not None and time.monotonic() >= deadline:
            logger.warning("llm_doc_fallback.failed: дедлайн истёк после вызова LLM")
            return []
        try:
            content = _assistant_content(raw)
            rows = _loads_rows_array(content)
        except (ValueError, json.JSONDecodeError) as exc:
            logger.warning(
                "llm_doc_fallback.failed: ответ LLM не распознан (попытка %d/%d): %s; raw: %.300s",
                attempt + 1, max_retries + 1, exc, raw,
            )
            continue
        out: List[LlmDocRow] = []
        for row in rows:
            if not isinstance(row, dict):
                logger.warning("llm_doc_fallback.failed: элемент не объект: %r", row)
                continue
            parsed = _row_from_spans(row, text)
            if parsed is None:
                logger.warning(
                    "llm_doc_fallback.failed: строка отброшена (невалидные spans/значения): %r", row,
                )
                continue
            out.append(parsed)
        logger.info("llm_doc_fallback.parsed: %d/%d строк из текста (%d символов)",
                    len(out), len(rows), len(text))
        return out

    logger.warning("llm_doc_fallback.failed: исчерпаны попытки (%d)", max_retries + 1)
    return []


def build_escalation_reason(skipped: List[Dict]) -> str:
    """Человекочитаемая причина эскалации из reason skipped-строк."""
    reasons = [str(s.get("reason", "")) for s in skipped if isinstance(s, dict)]
    if not reasons:
        return "нет текстового слоя"
    if all("м²" in r for r in reasons):
        return "документ — смета в м² без поканальных размеров"
    if all(r.startswith("Покупная позиция") for r in reasons):
        return "все позиции — перекупное оборудование"
    most_common = Counter(reasons).most_common(1)[0][0]
    return f"не распознано {len(reasons)} строк, основная причина: {most_common}"
