"""LLM-фолбэк на уровне строки: skipped-строки → kimi → нормализация (v2).

Протокол v2 (blacklist): в LLM идут ВСЕ skipped-строки, кроме м²-строк
(reason «Единица м²» или единица м2/м² — excluded_skipped). Модель одним
батчем возвращает JSON-массив `{name, size, unit, quantity}`; в промпте —
исходные наименования и текст документа (колонку «Кол-во» модель читает из
текста сама). Порядок ответа НЕ является ключом: каждая LLM-строка мэтчится
к исходнику по токенам имени (≥2 общих токена ИЛИ ≥1 не-числовой) и только
потом проходит детерминированную валидацию: якорь имени (общий алфавитно-
цифровой токен ≥3 символов с исходным наименованием), размер, подтверждённый
подстрокой исходного name/size, единица из whitelist — и количество int > 0,
при наличии текста документа подтверждённое числом в 30 символах после
любого вхождения ИСХОДНОГО имени (нормализатор переименовывает,
канонического имени в тексте нет; fallback — LLM-имя) — через якоря
llm_document_fallback._name_positions/_quantity_matches. М2-единица из LLM
принимается только при пустой/агрегатной исходной единице (гард смены шт/м →
м²).

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

# Агрегатные единицы: смета в м² без поканальных размеров LLM не лечит.
M2_UNITS = ("м2", "м²")

# Причина skipped-строки, с которой строка исключается из LLM как м²-агрегат.
M2_REASON_PREFIX = "Единица м²"

# Причина сломанного детерминированного разбора количества: такие строки —
# близнецы восстановленных LLM-позиций, после мерджа выводим их из skipped.
QTY_BROKEN_REASON_PREFIX = "Не удалось распознать количество"

# Единицы, которые LLM имеет право вернуть (whitelist для validate_normalized).
ALLOWED_UNITS = ("шт", "м", "м2", "м²")

__all__ = [
    "M2_UNITS",
    "M2_REASON_PREFIX",
    "ALLOWED_UNITS",
    "get_row_fallback_config",
    "row_fallback_enabled",
    "excluded_skipped",
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


def excluded_skipped(skipped: List[Dict]) -> List[Dict]:
    """М²-строки, исключаемые из LLM-нормализации: reason (со strip) начинается
    с «Единица м²» ИЛИ единица измерения агрегатная (м2/м²)."""
    out: List[Dict] = []
    for row in skipped or []:
        if not isinstance(row, dict):
            continue
        reason = str(row.get("reason") or "").strip()
        unit = str(row.get("unit") or "").strip()
        if reason.startswith(M2_REASON_PREFIX) or unit in M2_UNITS:
            out.append(row)
    return out


def qualifying_skipped(skipped: List[Dict]) -> List[Dict]:
    """Skipped-строки, пригодные для LLM-нормализации: ВСЕ skipped минус
    м²-строки из excluded_skipped (v2 — blacklist вместо whitelist), минус
    фрагменты без цифр в name/size.

    Фрагмент без цифр — обломок разорванной ячейки ГОСТ-бланка или шапка
    («канальный», «Согласовано:», «Вентиляция подвала»): размера в нём нет,
    валидация всё равно отбросит результат, а батч из сотен таких фрагментов
    сжигает минуты LLM-времени (инцидент: job 170, 1346 qualifying)."""
    excluded = excluded_skipped(skipped)
    out = []
    for row in skipped or []:
        if not isinstance(row, dict) or any(row is e for e in excluded):
            continue
        text = f"{row.get('name') or ''} {row.get('size') or ''}"
        if not any(ch.isdigit() for ch in text):
            continue
        out.append(row)
    return out


def _core_tokens(s: str) -> set:
    """Алфавитно-цифровые токены длины ≥3 (lower) — ядро для проверки якоря."""
    return {t for t in re.findall(r"[a-zа-яё0-9]+", (s or "").lower()) if len(t) >= 3}


# Минимальная длина префикса для fuzzy-связи токенов: «Возд.оц» →
# «Воздуховод оцинкованный» не даёт общего токена (токенизатор режет
# «возд»/«оц» vs «воздуховод»/«оцинкованный») — легальное расширение
# аббревиатуры проваливало гард переименования (см. смоук 168.pdf v4).
PREFIX_MIN_LEN = 4


def _names_related(llm_name: str, orig_name: str) -> bool:
    """Связь имён: общий core-токен ИЛИ общий префикс длины ≥4 символов
    у пары core-токенов (аббревиатуры «Возд.» → «Воздуховод»)."""
    a, b = _core_tokens(llm_name), _core_tokens(orig_name)
    if a & b:
        return True
    for ta in a:
        for tb in b:
            if len(ta) >= PREFIX_MIN_LEN and len(tb) >= PREFIX_MIN_LEN and (
                ta.startswith(tb) or tb.startswith(ta)
            ):
                return True
    return False


def _validated_with_reason(
    original: Dict, row: Dict, text: Optional[str] = None
) -> Tuple[Optional[Dict], Optional[str]]:
    """validate_normalized + причина: None — чистый приём; "size" — принято,
    но размер обнулён; иначе имя провалившейся проверки ("name"/"unit"/
    "quantity") и None вместо строки."""
    orig_name = str(original.get("name") or "")
    name = str(row.get("name") or "").strip()
    if not name:
        return None, "name"
    if not _names_related(name, orig_name):
        return None, "name"  # полное переименование — отбрасываем
    unit = str(row.get("unit") or "").strip()
    if unit not in ALLOWED_UNITS:
        return None, "unit"  # единица вне whitelist
    if unit in M2_UNITS:
        # Гард смены единицы: м2/м² принимаем только если исходная единица
        # пустая или сама агрегатная; шт/м → м² — отброс (агрегат навязан).
        orig_unit = str(original.get("unit") or "").strip()
        if orig_unit and orig_unit not in M2_UNITS:
            return None, "unit"
    try:
        quantity = int(row.get("quantity"))
    except (TypeError, ValueError):
        return None, "quantity"
    if quantity <= 0:
        return None, "quantity"
    if text is not None:
        # Якорь количества — по ИСХОДНОМУ имени: нормализатор переименовывает
        # («Возд.оц…» → «Воздуховод оцинкованный…»), канонического имени нет
        # в тексте, а исходник — дословная подстрока (из него и извлечён).
        anchor = orig_name.strip()
        positions = llm_document_fallback._name_positions(anchor, text) if anchor else []
        if not positions:
            anchor = name  # исходника нет в тексте (теоретически) — fallback
            positions = llm_document_fallback._name_positions(name, text)
        if not positions:
            return None, "name"  # наименования нет в документе — отбрасываем
        if not any(
            llm_document_fallback._quantity_matches(text, pos + len(anchor), quantity)
            for pos in positions
        ):
            return None, "quantity"  # количество не подтверждено текстом
    size = str(row.get("size") or "").strip()
    size_note: Optional[str] = None
    if size:
        haystack = (orig_name + " " + str(original.get("size") or "")).lower()
        if size.lower() not in haystack:
            size = ""  # размер не из исходника — обнуляем, строка жива
            size_note = "size"
    return {
        "name": name,
        "size": size,
        "unit": unit,
        "quantity": quantity,
    }, size_note


def validate_normalized(
    original: Dict, row: Dict, text: Optional[str] = None
) -> Optional[Dict]:
    """Детерминированная проверка строки LLM якорями исходной skipped-строки.

    None — отброс: пустое имя или нет общего токена ядра с исходным
    наименованием (LLM переименовала позицию); единица вне whitelist
    {шт, м, м2, м²} или смена шт/м → м2/м² (исходная пустая/агрегатная —
    можно); количество не целое > 0; при text is not None — исходное
    наименование не найдено в тексте (fallback — LLM-имя) или количество
    не встречается среди чисел в 30 символах после любого вхождения якоря.
    size принимается только если подстрока исходного name/size (lower),
    иначе обнуляется. quantity/unit берутся из LLM-строки (v2), а не из
    исходника. Причина отброса — во внутреннем _validated_with_reason."""
    parsed, _reason = _validated_with_reason(original, row, text)
    return parsed


# Бюджет текста документа в промпте: якорь валидации смотрит ±30 символов
# вокруг имени, поэтому усечение хвоста не ломает quantity-проверку для
# позиций из начала спецификации, а промпту хватает контекста на Кол-во.
PROMPT_TEXT_BUDGET = 8000


def _build_prompt(rows: List[Dict], text: Optional[str] = None) -> str:
    items = "\n".join(
        f"{i}: {json.dumps(str(r.get('name') or ''), ensure_ascii=False)}"
        for i, r in enumerate(rows)
    )
    doc_section = ""
    if text:
        doc_section = (
            "\n\nТекст документа (та же спецификация; колонка «Кол-во» — числа "
            "после наименования позиции):\n"
            + text[:PROMPT_TEXT_BUDGET]
        )
    return (
        "Ты нормализуешь позиции воздуховодов из проектной спецификации вентиляции.\n"
        f"Даны исходные наименования (номер: строка):\n{items}\n"
        "Верни СТРОГО один JSON-массив без пояснений, markdown и кода — по одному "
        "объекту для КАЖДОЙ строки, в ТОМ ЖЕ порядке:\n"
        '{"name": "...", "size": "...", "unit": "...", "quantity": 0}\n'
        "Правила:\n"
        '- «Возд.» / «Возд.оц» расшифровывай как «Воздуховод оцинкованный».\n'
        '- name — каноническое наименование позиции.\n'
        '- size — размер ДОСЛОВНО из исходного наименования (например "150х150", '
        '"ф125"); если размера нет — "".\n'
        '- unit — единица измерения, строго одна из: шт, м, м2, м².\n'
        '- quantity — целое количество из колонки «Кол-во» документа для этой '
        "позиции (целое число > 0): найди строку наименования в тексте документа "
        "и возьми число сразу после неё." + doc_section
    )


def _match_score(llm_row: Dict, original: Dict) -> int:
    """Сила связи LLM-строки с исходником: число общих алфавитно-цифровых
    токенов имени/size/comment (lower). Позиционный zip не используем —
    модель может пропустить/переставить строки, и тогда пары склеивались
    по чужим оригиналам (массовые ложные (name)-отказы, см. смоук 168.pdf)."""
    row_tokens = _name_tokens(
        " ".join(str(llm_row.get(k) or "") for k in ("name", "size", "comment"))
    )
    orig_tokens = _name_tokens(
        " ".join(str(original.get(k) or "") for k in ("name", "size", "comment"))
    )
    return len(row_tokens & orig_tokens)


def _match_acceptable(llm_row: Dict, original: Dict) -> bool:
    """Порог приёмки мэтча: ≥2 общих токена ИЛИ ≥1 общий не-числовой токен
    (одного числа-размера мало — «Заглушка 150» не мэтчится с «Отвод 150»)."""
    row_tokens = _name_tokens(
        " ".join(str(llm_row.get(k) or "") for k in ("name", "size", "comment"))
    )
    common = row_tokens & _name_tokens(str(original.get("name") or ""))
    return len(common) >= 2 or any(not t.isdigit() for t in common)


def _loads_rows_array(content: str) -> List[Dict]:
    """Парсит JSON-массив из ответа модели (с терпимостью к мусору вокруг)."""
    data = json.loads(content)
    if not isinstance(data, list):
        raise ValueError("ответ LLM — не JSON-массив")
    return data


# Размер батча строк на один вызов LLM: больше — и промпт раздувается
# (текст документа + сотни имён), kimi-cli не укладывается в таймаут вызова
# (инцидент: 1275 строк одним батчем, оба вызова умерли по таймауту).
ROW_BATCH_SIZE = 60


def normalize_rows(
    rows: List[Dict],
    cfg: Optional[Dict] = None,
    runner: Optional[Callable] = None,
    deadline: Optional[float] = None,
    text: Optional[str] = None,
) -> List[Dict]:
    """Skipped-строки → один батч-вызов LLM → валидные нормализованные строки.

    runner(prompt, cfg) -> stdout (stream-json) инжектируется в тестах.
    LLM возвращает JSON-массив `{name, size, unit, quantity}`; текст документа
    (text) подмешивается в промпт, чтобы модель брала quantity из колонки
    «Кол-во» сама. Пары со исходниками строятся токенным мэтчем (не по позиции
    в массиве ответа — она ненадёжна) и валидируются validate_normalized с
    text-якорем количества (text=None — якорь отключен, только int > 0).
    Большие входы нарезаются на чанки по ROW_BATCH_SIZE строк — общий
    deadline сохраняется между чанками. Любой сбой / невалидный JSON (после
    max_retries попыток) / истёкший deadline → [] + logger.warning,
    исключений наружу нет."""
    cfg = get_row_fallback_config() if cfg is None else dict(cfg)
    if len(rows) > ROW_BATCH_SIZE:
        out: List[Dict] = []
        for i in range(0, len(rows), ROW_BATCH_SIZE):
            out.extend(
                normalize_rows(
                    rows[i : i + ROW_BATCH_SIZE],
                    cfg=cfg, runner=runner, deadline=deadline, text=text,
                )
            )
        return out
    run = _run_kimi if runner is None else runner
    prompt = _build_prompt(rows, text)
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
        remaining = list(rows)
        for llm_row in llm_rows:
            if not isinstance(llm_row, dict):
                logger.warning("llm_row_fallback.failed: элемент не объект: %r", llm_row)
                continue
            # Fuzzy-мэтч к оригиналам: позиция в массиве ответа НЕ является
            # надёжным ключом (модель переставляет/пропускает строки).
            candidates = sorted(
                remaining,
                key=lambda o: -_match_score(llm_row, o),
            )
            best_reason: Optional[str] = None
            accepted: Optional[Tuple[Dict, Dict, Optional[str]]] = None
            for original in candidates[:5]:
                if not _match_acceptable(llm_row, original):
                    break  # дальше слабее — не рассматриваем
                parsed, reason = _validated_with_reason(original, llm_row, text)
                if parsed is not None:
                    accepted = (original, parsed, reason)
                    break
                best_reason = reason
            if accepted is None:
                logger.warning(
                    "llm_row_fallback.failed: строка отброшена (%s): %r",
                    best_reason or "match", llm_row,
                )
                continue
            original, parsed, reason = accepted
            remaining.remove(original)
            if reason:
                logger.info("llm_row_fallback: размер обнулён (%s): %r", reason, llm_row)
            out.append(parsed)
        logger.info("llm_row_fallback.normalized: %d/%d строк", len(out), len(rows))
        return out

    logger.warning("llm_row_fallback.failed: исчерпаны попытки (%d)", max_retries + 1)
    return []


def _name_tokens(s: str) -> set:
    """Алфавитно-цифровые токены (lower, без фильтра длины) для fuzzy-мэтча."""
    return set(re.findall(r"[a-zа-яё0-9]+", (s or "").lower()))


def _consume_original(row: Dict, remaining: List[Dict]) -> Optional[Dict]:
    """Снимает с учёта исходную skipped-строку, породившую LLM-строку.

    1) точный мэтч по name.strip().lower() — надёжно, пока LLM не меняла имя;
    2) токенный fallback — только при сильном пересечении: ≥2 общих токена
    ИЛИ ≥1 общий не-числовой токен (числовое совпадение одного размера
    недостаточно — иначе «Заглушка 150» съедалась чужой строкой).
    None — исходник не найден (строка пришла не из qualifying-батча)."""
    name_l = str(row.get("name") or "").strip().lower()
    if name_l:
        for s in remaining:
            if str(s.get("name") or "").strip().lower() == name_l:
                remaining.remove(s)
                return s
    row_tokens = _name_tokens(
        " ".join(
            str(row.get(k) or "")
            for k in ("name", "size", "comment")
        )
    )
    if row_tokens:
        for s in remaining:
            common = row_tokens & _name_tokens(str(s.get("name") or ""))
            if len(common) >= 2 or any(not t.isdigit() for t in common):
                remaining.remove(s)
                return s
    return None


def run_row_fallback(
    success: List[dict],
    skipped: List[dict],
    cfg: Optional[Dict] = None,
    pdf_path: Optional[str] = None,
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

    pdf_path даёт текст документа для quantity-якоря валидации: извлекается
    лениво (только при непустом qualifying и пройденном гейте) через
    llm_document_fallback.extract_document_text; pdf_path=None или сбой
    извлечения — text=None, якорь отключён (мягкая деградация). После мерджа
    из skipped удаляются сломанные близнецы (причина «Не удалось распознать
    количество») восстановленных позиций — совпадение по name.lower().

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
    # Кап строк на LLM: сверх него качество не растёт (остальное — обломки
    # разорванных таблиц, которые валидация всё равно отбросит), а обход
    # сотен чанков занимает десятки минут (инцидент: job 170). Хвост остаётся
    # в skipped с исходной причиной.
    MAX_LLM_ROWS = 400
    if len(qualifying) > MAX_LLM_ROWS:
        logger.warning(
            "llm_row_fallback: %d qualifying строк, в LLM идут первые %d",
            len(qualifying), MAX_LLM_ROWS,
        )
        qualifying = qualifying[:MAX_LLM_ROWS]
    # MERGED cfg (парковочное решение Task 2): command/model из общей секции
    # llm + timeout/max_retries из row_fallback.
    merged_cfg = {**llm_size_classifier.get_llm_config(), **get_row_fallback_config(cfg)}
    # Дедлайн масштабируется от числа чанков: normalize_rows нарезает большие
    # входы по ROW_BATCH_SIZE, и одного timeout на всё не хватает
    # (инцидент: 1275 строк → 22 чанка). Потолок — защита от бесконечного жанра.
    n_chunks = max(1, -(-len(qualifying) // ROW_BATCH_SIZE))
    chunk_timeout = float(merged_cfg.get("timeout") or 180)
    deadline = time.monotonic() + min(chunk_timeout * n_chunks, 900.0)

    # Текст документа — лениво и мягко: только для quantity-якоря; сбой
    # извлечения (битый pdf и т.п.) → text=None, якорь отключается.
    text: Optional[str] = None
    if pdf_path is not None:
        try:
            text = llm_document_fallback.extract_document_text(pdf_path)
        except Exception as exc:  # noqa: BLE001 — деградация, не сбой фолбэка
            logger.warning("llm_row_fallback.failed: текст документа не извлечён: %s", exc)
            text = None

    try:
        norm_rows = normalize_rows(qualifying, cfg=merged_cfg, deadline=deadline, text=text)
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
    # Чистка двойников: сломанный детерминированный близнец («Не удалось
    # распознать количество», qty=0) восстановленной LLM-позиции уже есть
    # в заказе (поднят сейчас или doc-fallback ранее) — из skipped убираем.
    # Матчим по имени ВСЕ нормализованные строки, в т.ч. отброшенные дедупом:
    # их двойники в skipped остались именно из-за этого дубля.
    if extra_success:
        recovered_names = {
            str(r.get("name") or "").strip().lower() for r in extra_success
        }
        merged_skipped = [
            s for s in merged_skipped
            if not (
                str(s.get("reason") or "").startswith(QTY_BROKEN_REASON_PREFIX)
                and str(s.get("name") or "").strip().lower() in recovered_names
            )
        ]
    if recovered:
        logger.info("llm_row_fallback.recovered: %d", recovered)
    return merged_success, merged_skipped, {
        "row_fallback_triggered": True,
        "row_fallback_recovered": recovered,
    }
