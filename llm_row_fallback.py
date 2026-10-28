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
from typing import Any, Callable, Dict, List, Optional, Tuple

import llm_document_fallback
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
    "run_row_fallback",
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


def _consume_original(row: Dict, remaining: List[Dict]) -> Optional[Dict]:
    """Снимает с учёта исходную skipped-строку, породившую LLM-строку.

    normalize_rows копирует unit/quantity из исходника, поэтому мэтчим по ним
    (мультимножество: одинаковые строки взаимозаменимы). None — исходник
    не найден (строка пришла не из qualifying-батча)."""
    for s in remaining:
        if s.get("unit") == row.get("unit") and s.get("quantity") == row.get("quantity"):
            remaining.remove(s)
            return s
    return None


def run_row_fallback(
    success: List[dict],
    skipped: List[dict],
    cfg: Optional[Dict] = None,
) -> Tuple[List[dict], List[dict], Dict[str, Any]]:
    """Оркестратор строкового LLM-фолбэка: qualifying skipped → мердж в success.

    Квалифицируемые skipped-строки (qualifying_skipped) одним батчем идут в
    normalize_rows; нормализованные строки повторно проходят детерминированный
    process_rows (без LLM-классификации размеров) и мерджатся в success с
    дедупом по _merge_key (при совпадении приоритет у детерминированных,
    LLM-строкам ``"llm_source": True``). Повторно отброшенные строки остаются
    в skipped с ИСХОДНОЙ причиной (extra_skipped из process_rows не добавляем:
    их исходники уже там). skip_ratio_post/escalation здесь НЕ считаются —
    только row_fallback_triggered / row_fallback_recovered; финальные
    значения считает Task 4.

    Без квалифицируемых строк или при выключенном row_fallback_enabled —
    исходные списки без изменений (triggered=False). Любой сбой нормализации —
    исходные success/skipped + triggered=True, recovered=0, пайплайн не падает.
    """
    noop_meta: Dict[str, Any] = {
        "row_fallback_triggered": False,
        "row_fallback_recovered": 0,
    }
    qualifying = qualifying_skipped(skipped)
    if not qualifying or not row_fallback_enabled(cfg):
        return success, skipped, noop_meta

    logger.info("llm_row_fallback.triggered: %d строк", len(qualifying))
    # MERGED cfg (парковочное решение Task 2): command/model из общей секции
    # llm + timeout/max_retries из row_fallback.
    merged_cfg = {**llm_size_classifier.get_llm_config(), **get_row_fallback_config(cfg)}
    deadline = time.monotonic() + float(merged_cfg.get("timeout") or 180)

    try:
        norm_rows = normalize_rows(qualifying, cfg=merged_cfg, deadline=deadline)
        import process_specification_table as pst

        _, _extra_skipped, extra_success = pst.process_rows(
            norm_rows, run_llm_size_classify=False
        )
    except Exception as exc:  # noqa: BLE001 — fallback обязан не ронять пайплайн
        logger.info("llm_row_fallback.failed: %s", exc)
        return success, skipped, {
            "row_fallback_triggered": True,
            "row_fallback_recovered": 0,
        }

    # Мердж success: дедуп по (comment, quantity) — при совпадении приоритет
    # у детерминированных строк, LLM-дубли отбрасываем (по образцу
    # run_doc_fallback). Строки не мутируем: регистр комментария уезжает в 1С.
    merged_success = list(success)
    seen = {llm_document_fallback._merge_key(r) for r in merged_success}
    recovered = 0
    remaining = list(qualifying)
    for row in extra_success:
        key = llm_document_fallback._merge_key(row)
        if key in seen:
            continue  # дубль детерминированной строки — исходник в skipped остаётся
        seen.add(key)
        _consume_original(row, remaining)  # исходник восстановлен — из skipped уйдёт
        row["llm_source"] = True
        merged_success.append(row)
        recovered += 1
    # В skipped остаются неквалифицируемые строки как есть плюс исходники
    # qualifying-строк, которые LLM/валидация/process_rows не подняли —
    # с исходной причиной.
    merged_skipped = [s for s in skipped if s not in qualifying] + remaining
    if recovered:
        logger.info("llm_row_fallback.recovered: %d", recovered)
    return merged_success, merged_skipped, {
        "row_fallback_triggered": True,
        "row_fallback_recovered": recovered,
    }
