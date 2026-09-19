"""Собирает сырые строки размеров из проектов examples/ в фикстуру корпуса.

Usage: .venv/bin/python tools/collect_multi_project_sizes.py
Перезаписывает tests/fixtures/multi_project_sizes.json. Тест использует
снапшот — сеть и PDF-парсеры при обычном прогоне pytest не нужны.
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from process_specification_table import extract_dimensions, parse_size  # noqa: E402

EXAMPLES = ROOT / "examples"
OUT = ROOT / "tests" / "fixtures" / "multi_project_sizes.json"

# Пермиссивный сборщик кандидатов: круг с префиксом/суффиксом, AxB, с дефисной длиной.
CANDIDATE = re.compile(
    r"(?:\d{2,5}\s*[xх×*]\s*\d{2,5}(?:\s*[-_]\s*\d{2,5})?"
    r"|(?:dn|дн|ду|d|д|ф|ø|Ø|⌀)\s*\d{2,5}(?:\s*[-_]\s*\d{2,5})?"
    r"|\d{2,5}\s*[øØ⌀](?!\s*\d))"
)


def collect_from_text(source: str, text: str, found: dict) -> None:
    for m in CANDIDATE.finditer(text):
        raw = m.group(0).strip()
        if raw and raw not in found:
            section, _ = parse_size(raw)
            found[raw] = {
                "source": source,
                "raw": raw,
                "parse_size_ok": section is not None,
                "extract_ok": bool(extract_dimensions(raw)),
            }


def _collect_pdf(path_str: str) -> dict:
    """Работает в дочернем процессе: pdfplumber держит сотни МБ на страницу,
    поэтому память должна освобождаться между файлами."""
    import pdfplumber

    found: dict = {}
    with pdfplumber.open(path_str) as pdf:
        for page in pdf.pages:
            collect_from_text(Path(path_str).name, page.extract_text() or "", found)
            page.close()
    return found


def main() -> None:
    found: dict = {}
    # Excel-файлы: сканируем все строковые ячейки
    try:
        import pandas as pd

        for path in sorted(EXAMPLES.rglob("*.xls*")):
            try:
                df = pd.read_excel(path, dtype=str)
            except Exception as exc:  # noqa: BLE001
                print(f"skip {path.name}: {exc}")
                continue
            for row in df.fillna("").astype(str).values:
                collect_from_text(path.name, " ".join(row), found)
    except ImportError:
        print("pandas недоступен — xls/xlsx пропущены")
    # PDF: только если pdfplumber установлен; каждый файл — в своём процессе
    try:
        import pdfplumber  # noqa: F401

        from concurrent.futures import ProcessPoolExecutor

        for path in sorted(EXAMPLES.rglob("*.pdf")):
            try:
                with ProcessPoolExecutor(max_workers=1) as pool:
                    for raw, rec in pool.submit(_collect_pdf, str(path)).result().items():
                        if raw not in found:
                            found[raw] = rec
            except Exception as exc:  # noqa: BLE001
                print(f"skip {path.name}: {exc}")
                continue
    except ImportError:
        print("pdfplumber недоступен — pdf пропущены")
    corpus = sorted(found.values(), key=lambda r: (r["source"], r["raw"]))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(corpus, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"собрано {len(corpus)} уникальных строк размеров из {EXAMPLES}")


if __name__ == "__main__":
    main()
