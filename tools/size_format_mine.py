"""Автообучение вариантов записи размеров из skipped-отчётов (петля C).

Цикл: агрегация skipped-отчётов → кластеры нераспознанных строк → kimi
предлагает АДДИТИВНЫЕ символьные записи для config/size_notations.yaml
(без единой цифры размера) → gate: кластер парсится каскадом + полный pytest
зелёный → коммит с provenance. Gate красный → патч отброшен, кластер в отчёте.
"""

from __future__ import annotations

import copy
import datetime
import json
import os
import re
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import Dict, List

import llm_size_classifier as lsc
import process_specification_table as pst
import size_notations as szn
import yaml
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
    if not e.isprintable():
        return None                   # zero-width/BOM и прочие непечатаемые символы
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


def _merged_notations(patch: Dict) -> Dict:
    """Текущий конфиг + аддитивный патч (без мутации кэша)."""
    n = copy.deepcopy(szn.get_notations())
    n["diameter_prefixes"] = list(n.get("diameter_prefixes") or []) + list(patch.get("add_prefixes") or [])
    n["diameter_suffixes"] = list(n.get("diameter_suffixes") or []) + list(patch.get("add_suffixes") or [])
    n["separators"] = list(n.get("separators") or []) + list(patch.get("add_separators") or [])
    return n


def _write_temp_config(notations: Dict) -> str:
    fd, path = tempfile.mkstemp(suffix=".yaml", prefix="size_notations_")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        yaml.safe_dump(notations, f, allow_unicode=True, sort_keys=False)
    return path


def _run_pytest_gate(env: Dict[str, str]) -> tuple:
    """Полный pytest под пропатченным конфигом. (False, хвост вывода) при красном."""
    e = dict(os.environ)
    e.update(env)
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/", "-q", "-x"],
        capture_output=True, text=True, encoding="utf-8", env=e, cwd=str(ROOT),
        timeout=900,
    )
    if proc.returncode != 0:
        tail = "\n".join((proc.stdout + proc.stderr).splitlines()[-15:])
        return False, tail
    return True, ""


def gate_check(patch: Dict, cluster_strings: List[str], run_pytest=None) -> tuple:
    """Gate: (0) НИ ОДНА строка не парсится под текущим конфигом (патч обязан быть нужен);
    (1) каждая строка кластера парсится под пропатченной КОПИЕЙ конфига;
    (2) полный pytest зелёный. Реальный конфиг не трогаем."""
    run_pytest = run_pytest or _run_pytest_gate
    szn.reload_notations()
    already = [s for s in cluster_strings
               if pst.parse_size(s)[0] is not None or pst.extract_dimensions(s)]
    if already:
        return False, f"уже парсится без патча: {already[:5]}"
    tmp_path = _write_temp_config(_merged_notations(patch))
    old_env = os.environ.get("SPEC_TO_1C_SIZE_NOTATIONS")
    os.environ["SPEC_TO_1C_SIZE_NOTATIONS"] = tmp_path
    try:
        szn.reload_notations()
        unparsed = [s for s in cluster_strings
                    if pst.parse_size(s)[0] is None and not pst.extract_dimensions(s)]
        if unparsed:
            return False, f"под патчем не парсятся: {unparsed[:5]}"
        ok, detail = run_pytest({"SPEC_TO_1C_SIZE_NOTATIONS": tmp_path})
        if not ok:
            return False, f"pytest красный: {detail}"
        return True, ""
    finally:
        if old_env is None:
            os.environ.pop("SPEC_TO_1C_SIZE_NOTATIONS", None)
        else:
            os.environ["SPEC_TO_1C_SIZE_NOTATIONS"] = old_env
        szn.reload_notations()
        Path(tmp_path).unlink(missing_ok=True)


def apply_patch(patch: Dict, cluster_info: Dict[str, int], session: str,
                path: str = "config/size_notations.yaml") -> str:
    """Пишет принятый патч в реальный конфиг + provenance. Возвращает итоговый текст."""
    merged = _merged_notations(patch)
    merged.setdefault("provenance", [])
    today = datetime.date.today().isoformat()
    for section, key in (("add_prefixes", "diameter_prefixes"),
                         ("add_suffixes", "diameter_suffixes"),
                         ("add_separators", "separators")):
        for entry in patch.get(section) or []:
            merged["provenance"].append({
                "entry": entry, "section": key,
                "source": ", ".join(list(cluster_info)[:5]),
                "count": sum(cluster_info.values()),
                "date": today, "session": session,
            })
    p = ROOT / path
    p.write_text(yaml.safe_dump(merged, allow_unicode=True, sort_keys=False), encoding="utf-8")
    szn.reload_notations()
    return p.read_text(encoding="utf-8")


def main(argv=None) -> int:
    """CLI: --reports P [P...] --min-occurrences N --dry-run/--apply [--no-commit]."""
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reports", nargs="+", required=True)
    parser.add_argument("--min-occurrences", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--no-commit", action="store_true")
    args = parser.parse_args(argv)

    lcfg = get_learning_config()
    if not args.apply:                       # default = dry-run
        args.dry_run = True
    min_occ = args.min_occurrences or int(lcfg.get("min_occurrences", 3))

    counter = load_skipped_sizes(args.reports)
    clusters = stable_clusters(counter, min_occ)
    print(f"кластеров (>= {min_occ} вхождений): {len(clusters)} из {sum(counter.values())} строк")
    if not clusters:
        return 0

    current = {
        "diameter_prefixes": list(szn.get_notations()["diameter_prefixes"]),
        "diameter_suffixes": list(szn.get_notations()["diameter_suffixes"]),
        "separators": list(szn.get_notations()["separators"]),
    }
    raw = propose_additions(clusters)
    patch = sanitize_proposals(raw, current)
    if not any(patch.values()):
        print("kimi не предложила аддитивных символьных записей")
        return 0

    print("патч:", patch)
    ok, reason = gate_check(patch, list(clusters))
    if not ok:
        print(f"GATE ОТКАЗ: {reason}")
        return 1
    if args.dry_run:
        print("GATE ЗЕЛЁНЫЙ (dry-run: конфиг не изменён, коммит не создан)")
        return 0

    apply_patch(patch, clusters, session="kimi-cli")
    if args.no_commit or not lcfg.get("auto_commit"):
        print("конфиг обновлён; коммит пропущен (auto_commit=false или --no-commit)")
        return 0

    subprocess.run(["git", "add", "config/size_notations.yaml"], check=True, cwd=str(ROOT))
    subprocess.run(["git", "commit", "-m",
                    "learn(size_notations): mined additive symbols "
                    f"{patch} (clusters: {list(clusters)[:5]})"],
                   check=True, cwd=str(ROOT))
    print("принято и закоммичено")
    return 0
