"""LLM-фолбэк классификации форматов записи размеров.

Жёсткое ограничение: LLM никогда не генерирует и не переписывает цифры
размеров. Модель возвращает только format_class и spans (смещения подстрок
в ИСХОДНОЙ строке); цифры извлекаются детерминированно int(source[start:end]).
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import size_notations
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

logger = logging.getLogger(__name__)

_ROLE_KEYS = {"diameter": "D0", "a": "A0", "b": "B0", "length": "L0"}


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


def _build_prompt(strings: List[str]) -> str:
    items = "\n".join(f'{i}: {json.dumps(s, ensure_ascii=False)}' for i, s in enumerate(strings))
    classes = ", ".join(FORMAT_CLASSES)
    return (
        "Ты классификатор форматов записи размеров воздуховодов.\n"
        f"Даны строки (номер: JSON-строка):\n{items}\n\n"
        "Для КАЖДОЙ строки верни строго один JSON-объект без пояснений, markdown и кода:\n"
        '{"results":[{"format_class":"<класс>","spans":[{"role":"...","start":N,"end":N}]}...]}\n'
        f"format_class — одно из: {classes}.\n"
        "spans — смещения подстрок В ИСХОДНОЙ строке (символы, start включительно, end исключительно), "
        "которые являются ЧИСЛАМИ размеров. role — одно из: diameter, a, b, c, length.\n"
        "Правила: round_diameter — role diameter (обязателен), опционально length; "
        "rect_axb — a и b (обязательны), опционально length; "
        "tee_axbxc — a, b, c (три числа, тройник); reducer_pair — два числа (role diameter или a/b); "
        "silencer_code — span на код (role a); unknown — spans пустой.\n"
        "НИКОГДА не выводи сами числа и не переписывай строку — только класс и смещения. "
        "Несколько диаметров round_diameter: первый — diameter, остальные тоже diameter.\n"
        'Пример для строки "Ø315": {"results":[{"format_class":"round_diameter",'
        '"spans":[{"role":"diameter","start":1,"end":4}]}]}'
    )


def _run_kimi(prompt: str, cfg: Dict) -> str:
    """Один вызов kimi -p; возвращает сырой stdout. Бросает исключения при сбое."""
    cmd = [str(cfg.get("command") or "kimi"), "-p", prompt, "--output-format", "stream-json"]
    model = str(cfg.get("model") or "").strip()
    if model:
        cmd += ["-m", model]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=int(cfg.get("timeout", 120)))
    if proc.returncode != 0:
        raise RuntimeError(f"kimi exit {proc.returncode}: {proc.stderr[:300]}")
    return proc.stdout


def _assistant_content(stdout: str) -> str:
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if rec.get("role") == "assistant" and isinstance(rec.get("content"), str):
            return rec["content"]
    raise ValueError("assistant-ответ не найден в stream-json")


def _extract_json_object(text: str) -> Dict:
    start = text.find("{")
    if start < 0:
        raise ValueError("JSON не найден в ответе")
    obj, _ = json.JSONDecoder().raw_decode(text[start:])
    if not isinstance(obj, dict):
        raise ValueError("ответ — не объект")
    return obj


def _spans_to_dims(format_class: str, spans: List[Dict], source: str) -> Optional[Dict[str, float]]:
    """Детерминированное извлечение цифр ИСХОДНОЙ строки по spans. None = отказ."""
    values: Dict[str, float] = {}
    ordered: List[float] = []
    for sp in spans:
        role = str(sp.get("role", ""))
        try:
            start, end = int(sp["start"]), int(sp["end"])
        except (KeyError, TypeError, ValueError):
            return None
        if not (0 <= start < end <= len(source)):
            return None
        token = source[start:end].strip()
        if not re.fullmatch(r"\d+", token):
            return None  # LLM прислала не цифры — отказ
        try:
            value = float(int(token))
        except (ValueError, OverflowError):
            return None
        ordered.append(value)
        if role in _ROLE_KEYS and _ROLE_KEYS[role] not in values:
            values[_ROLE_KEYS[role]] = value
        elif role == "c":
            values.setdefault("D2", value)
    if format_class == "round_diameter":
        if "D0" not in values:
            return None
    elif format_class == "rect_axb":
        if "A0" not in values or "B0" not in values:
            return None
    elif format_class == "tee_axbxc":
        if len(ordered) < 3:
            return None
        return {"D0": ordered[0], "D1": ordered[1], "D2": ordered[2]}
    elif format_class == "reducer_pair":
        if len(ordered) < 2:
            return None
        return {"D0": ordered[0], "D1": ordered[1]}
    elif format_class == "silencer_code":
        return {}  # кодовые таблицы — забота каскада/этапа 3
    return values or None


def _classify_one(source: str, result: Dict, raw_response: str) -> "SizeClassification":
    """status='ok' — spans дали валидные числа И валидация диапазонов пройдена.
    adoptable — дополнительно: класс применим к воздуховодам (duct-путь)."""
    fmt = str(result.get("format_class") or "unknown")
    if fmt not in FORMAT_CLASSES:
        fmt = "unknown"
    spans = result.get("spans") or []
    if not isinstance(spans, list):
        spans = []
    dims = _spans_to_dims(fmt, spans, source)
    if dims is None:
        return SizeClassification(
            status="rejected", format_class=fmt, raw_response=raw_response,
            detail="spans не дали валидных чисел из исходной строки",
        )
    section = None
    if fmt == "round_diameter":
        section = "round"
    elif fmt == "rect_axb":
        section = "rectangular"
    if not size_notations.validate_dimensions(dims, section):
        return SizeClassification(
            status="rejected", format_class=fmt, dims=dims, section=section,
            raw_response=raw_response, detail="валидация диапазонов не пройдена",
        )
    adoptable = fmt in ADOPTABLE_CLASSES
    detail = "" if adoptable else f"класс {fmt} не усыновляется в воздуховоды"
    return SizeClassification(
        status="ok", format_class=fmt, dims=dims, section=section,
        adoptable=adoptable, raw_response=raw_response, detail=detail,
    )


def classify_sizes_batch(strings, cfg=None, runner=None):
    """Классифицирует строки батчами. runner(prompt, cfg)->stdout инжектируется в тестах.
    Любой сбой → status=rejected (пайплайн не падает)."""
    cfg = get_llm_config() if cfg is None else cfg
    run = _run_kimi if runner is None else runner
    batch_size = max(1, int(cfg.get("batch_size", 20)))
    out: Dict[str, SizeClassification] = {}

    def reject_batch(batch, detail):
        for s in batch:
            logger.warning("LLM-классификация отклонена: %r — %s", s, detail)
            out[s] = SizeClassification(status="rejected", detail=detail)

    for i in range(0, len(strings), batch_size):
        batch = [s for s in strings[i:i + batch_size] if s and s not in out]
        if not batch:
            continue
        prompt = _build_prompt(batch)
        raw = ""
        last_exc: Optional[Exception] = None
        for attempt in range(int(cfg.get("max_retries", 2)) + 1):
            try:
                raw = run(prompt, cfg)
                break
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                logger.warning("kimi вызов неудачен (попытка %d): %s", attempt + 1, exc)
        else:
            reject_batch(batch, f"LLM недоступна: {last_exc}")
            continue
        try:
            content = _assistant_content(raw)
            obj = _extract_json_object(content)
            results = obj.get("results")
            if not isinstance(results, list) or len(results) < len(batch):
                raise ValueError(f"ожидалось {len(batch)} результатов, получено {len(results) if isinstance(results, list) else 0}")
        except ValueError as exc:
            reject_batch(batch, f"ответ LLM не распознан: {exc}")
            continue
        for source, result in zip(batch, results):
            if not isinstance(result, dict):
                result = {}
            cls = _classify_one(source, result, raw)
            logger.info(
                "LLM-классификация: %r → %s (%s) %s",
                source, cls.format_class, cls.status, cls.detail,
            )
            out[source] = cls
    return out
