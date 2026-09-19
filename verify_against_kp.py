#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Сверка результата конвейера «проектная спецификация ОВ → XML» с КП завода.

Эталон — позиции 1–44 КП ``examples/для обучения 03.09.26/софт1200 КП №69649 №1,5 (1).xls``
(14 строк воздуховодов + 30 фасонных изделий). Позиции КП 45–59 (диффузоры,
гибкий воздуховод, пенофол, медные трубки, K-FLEX, крепёж) и оборудование
в XML не должны попадать.

Запуск:
    .venv/bin/python verify_against_kp.py

Отчёт печатается в stdout: по каждой позиции КП 1–44 — найдена/нет в XML
(по категории изделия, сечению и количеству), расхождения и лишние строки.
"""

import re
import warnings
from pathlib import Path

import pandas as pd

warnings.filterwarnings("ignore")

from process_specification_table import process_rows
from project_spec_xlsx import parse_project_spec_xlsx

BASE_DIR = Path(__file__).parent
SPEC_XLSX = BASE_DIR / "examples" / "для обучения 03.09.26" / "КР-0324-ОВ_страницы.xlsx"
KP_XLS = BASE_DIR / "examples" / "для обучения 03.09.26" / "софт1200 КП №69649 №1,5 (1).xls"

# Категория изделия из наименования КП → префикс артикула в XML
CATEGORY_ARTICLES = {
    "duct_round": ("1-1",),
    "duct_rect": ("1-2",),
    "saddle_round": ("8-1",),
    "saddle_rect": ("8-2",),
    "elbow_round": ("2-1",),
    "elbow_rect": ("2-2",),
    "transition_round": ("3-1",),
    "transition_rect_round": ("3-3",),
    "transition_rect": ("3-2",),
    "tee_round": ("4-1",),
    "tee_rect": ("4-2",),
    "nipple_round": ("12-1",),
    "nipple_rect": ("12-2",),
    "cap_round": ("6-1",),
    "cap_rect": ("6-2",),
    "throttle_round": ("16-1",),
    "throttle_rect": ("16-2",),
    "roof_cap_round": ("9-1",),
    "roof_cap_rect": ("9-2",),
    "hood_rect": ("19-2",),
}


def kp_category(name: str, designation: str = "") -> str:
    n = name.lower()
    # Для коротких наименований («Ниппель», «Тройник») форму сечения берём
    # из обозначения: начинается с Ф — круглое
    round_ = "кругл" in n or designation.strip().lower().startswith(("ф", "ø", "Ø"))
    # Фасонные изделия проверяем раньше: в их названиях часто есть слово
    # «воздуховод» («врезка ... в прямоугольный воздуховод»)
    if "врезка" in n:
        return "saddle_round" if round_ else "saddle_rect"
    if "отвод" in n:
        return "elbow_round" if round_ else "elbow_rect"
    if "тройник" in n:
        return "tee_round" if round_ else "tee_rect"
    if "ниппель" in n:
        return "nipple_round" if round_ else "nipple_rect"
    if "заглушк" in n:
        return "cap_round" if round_ else "cap_rect"
    if "дроссель" in n:
        return "throttle_round" if round_ else "throttle_rect"
    if "переход" in n:
        if "с прямоугольного на круглое" in n:
            return "transition_rect_round"
        return "transition_round" if round_ else "transition_rect"
    # Зонты: крышный (дефлектор) 9-x, вытяжной/островной 19-x
    if "зонт" in n:
        if "вытяжной" in n:
            return "hood_rect"
        return "roof_cap_round" if round_ else "roof_cap_rect"
    if "воздуховод" in n:
        return "duct_round" if round_ else "duct_rect"
    return "unknown"


def parse_kp_designation(designation: str) -> dict:
    """Разбирает обозначение КП в эталонные параметры (без длин L*)."""
    d = (
        designation.replace("х", "x")
        .replace("×", "x")
        .replace("*", "x")
        .replace("ø", "ф")
        .replace("Ø", "ф")
        .replace("⌀", "ф")
        .lower()
    )
    # Ф125-90-125 (отвод круглый: D-U-R)
    m = re.fullmatch(r"ф(\d+)-(\d+)-(\d+)", d)
    if m:
        return {"D0": int(m.group(1)), "U0": int(m.group(2)), "R0": int(m.group(3))}
    # Ф125-3000 (воздуховод/ниппель круглый)
    m = re.fullmatch(r"ф(\d+)-(\d+)", d)
    if m:
        return {"D0": int(m.group(1))}
    # 150x150-1250 (воздуховод прямоугольный)
    m = re.fullmatch(r"(\d+)x(\d+)-(\d+)", d)
    if m:
        return {"A0": int(m.group(1)), "B0": int(m.group(2))}
    # Ф100/Ф125-60 (переход круглый)
    m = re.fullmatch(r"ф(\d+)/ф(\d+)-(\d+)", d)
    if m:
        return {"D0": int(m.group(1)), "D1": int(m.group(2))}
    # Ф100/Ф100-100 (врезка круглая)
    m = re.fullmatch(r"ф(\d+)/ф(\d+)-(\d+)", d)
    if m:
        return {"D0": int(m.group(1)), "D1": int(m.group(2))}
    # Ф125-200/Ф100-100 (тройник круглый: D0-L0/D2-L2)
    m = re.fullmatch(r"ф(\d+)-(\d+)/ф(\d+)-(\d+)", d)
    if m:
        return {"D0": int(m.group(1)), "D2": int(m.group(3))}
    # 150x150/Ф125-300 (переход прямоугольное→круглое)
    m = re.fullmatch(r"(\d+)x(\d+)/ф(\d+)-(\d+)", d)
    if m:
        return {"A0": int(m.group(1)), "B0": int(m.group(2)), "D0": int(m.group(3))}
    # 250x150/150x150-300 (переход прямоугольный)
    m = re.fullmatch(r"(\d+)x(\d+)/(\d+)x(\d+)-(\d+)", d)
    if m:
        return {
            "A0": int(m.group(1)),
            "B0": int(m.group(2)),
            "A1": int(m.group(3)),
            "B1": int(m.group(4)),
        }
    # 500x700-600/500x700-100 (тройник прямоугольный: A0xB0-L0/A2xB2-L2)
    m = re.fullmatch(r"(\d+)x(\d+)-\d+/(\d+)x(\d+)-\d+", d)
    if m:
        return {
            "A0": int(m.group(1)),
            "B0": int(m.group(2)),
            "A2": int(m.group(3)),
            "B2": int(m.group(4)),
        }
    # 150x150-90-100-100 (отвод прямоугольный: AxB-U-L1-L2)
    m = re.fullmatch(r"(\d+)x(\d+)-(\d+)-(\d+)-(\d+)", d)
    if m:
        return {"A0": int(m.group(1)), "B0": int(m.group(2)), "U0": int(m.group(3))}
    # 250/350x1000 (зонт вытяжной: сечение A/B — длина L; порядок A/B не важен)
    m = re.fullmatch(r"(\d+)/(\d+)x(\d+)", d)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        return {"A0": max(a, b), "B0": min(a, b), "L0": int(m.group(3))}
    return {}


def load_kp_positions() -> list:
    """Читает позиции КП (со 2-й строки таблицы, колонки 0-5)."""
    df = pd.read_excel(KP_XLS, header=None)
    positions = []
    for _, row in df.iterrows():
        num = row.iloc[0]
        if pd.isna(num):
            continue
        num_s = str(num).strip()
        if not re.fullmatch(r"\d+", num_s):
            continue
        num_i = int(num_s)
        if num_i < 1 or num_i > 59:
            continue
        name = str(row.iloc[1]).strip()
        designation = str(row.iloc[2]).strip() if pd.notna(row.iloc[2]) else ""
        qty = float(str(row.iloc[5]).replace(",", ".").replace(" ", ""))
        positions.append(
            {"num": num_i, "name": name, "designation": designation, "qty": qty}
        )
    return positions


def xml_row_category(article: str) -> str:
    for cat, prefixes in CATEGORY_ARTICLES.items():
        if any(article.startswith(p) for p in prefixes):
            return cat
    return "other"


def match_position(pos: dict, xml_rows: list, used: set) -> tuple:
    """Ищет неиспользованную строку XML для позиции КП.

    Сравнение: категория изделия, количество и эталонные параметры сечения
    (A0/B0/D0/D1/D2/U0 — без длин L*, которые в спецификации не указаны).
    Для круглых переходов D0/D1 сравниваются как множество (в спецификации
    порядок сторон может быть обратным).
    """
    cat = kp_category(pos["name"], pos["designation"])
    expected = parse_kp_designation(pos["designation"])
    if cat in ("saddle_round", "saddle_rect") and "D1" in expected:
        # Обозначение КП «Ф125/Ф125-100» даёт D1, а в XML у 8-1-1/8-2-1
        # ветвь обозначена D2 (магистраль) — приводим к общему виду.
        expected = {("D2" if k == "D1" else k): v for k, v in expected.items()}
    for i, row in enumerate(xml_rows):
        if i in used:
            continue
        if xml_row_category(row["article"]) != cat:
            continue
        if float(row["quantity"]) != pos["qty"]:
            continue
        params = row["params"]
        ok = True
        # Для круглых переходов D0/D1 сравниваем как множество ниже — здесь
        # пропускаем (порядок сторон в спецификации может быть обратным).
        skip_keys = {"D0", "D1"} if cat == "transition_round" else set()
        # Пары сторон прямоугольных сечений сравниваем без учёта порядка
        # («300x400» в КП vs «400x300» в XML): (A0,B0) — основное, (A2,B2) — ветвь.
        rect_pairs = [
            (a, b) for a, b in (("A0", "B0"), ("A2", "B2"))
            if a in expected and b in expected and a in params and b in params
        ]
        for key, val in expected.items():
            if key in skip_keys:
                continue
            if any(key in pair for pair in rect_pairs):
                continue  # сравниваем парой ниже
            if key not in params or float(params[key]) != float(val):
                ok = False
                break
        if ok:
            for a, b in rect_pairs:
                if {params[a], params[b]} != {float(expected[a]),
                                              float(expected[b])}:
                    ok = False
                    break
        if cat == "transition_round" and ok:
            # Порядок сторон в спецификации может быть обратным
            d0, d1 = expected.get("D0"), expected.get("D1")
            if {params.get("D0"), params.get("D1")} != {float(d0), float(d1)}:
                ok = False
        if ok:
            return i, row
    return None, None


def main() -> None:
    rows = parse_project_spec_xlsx(SPEC_XLSX)
    xml_text, skipped, success = process_rows(rows)

    positions = load_kp_positions()
    producible = [p for p in positions if p["num"] <= 44]
    buyout = [p for p in positions if p["num"] >= 45]

    print(f"Строк в XML (success): {len(success)}, пропущено: {len(skipped)}")
    print(f"Позиций КП 1-44 (производимые): {len(producible)}, 45-59 (покупные): {len(buyout)}")
    print("=" * 100)

    used: set = set()
    mismatches = []
    for pos in producible:
        idx, row = match_position(pos, success, used)
        if idx is None:
            mismatches.append(pos)
            print(f"КП {pos['num']:>2} | НЕТ В XML | {pos['name'][:55]:<55} | {pos['designation']}")
        else:
            used.add(idx)
            print(f"КП {pos['num']:>2} | OK      | {pos['name'][:55]:<55} | {pos['designation']} x{pos['qty']:g}")

    print("=" * 100)
    if mismatches:
        print("РАСХОЖДЕНИЯ (позиции КП 1-44, не сошедшиеся с XML):")
        for pos in mismatches:
            # ближайшие кандидаты для пояснения
            cat = kp_category(pos["name"], pos["designation"])
            cands = [
                (r["article"], r["quantity"], r["params"], r["comment"][:60])
                for r in success
                if xml_row_category(r["article"]) == cat
            ]
            print(f"  КП {pos['num']}: {pos['name']} {pos['designation']} x{pos['qty']:g}")
            for art, qty, params, comment in cands[:4]:
                mark = " <== вероятный источник" if qty != pos["qty"] else ""
                print(f"      XML: {art} x{qty:g} {params} | {comment}{mark}")
    else:
        print("РАСХОЖДЕНИЙ НЕТ: все позиции КП 1-44 найдены в XML.")

    print("=" * 100)
    extra = [
        (i, r)
        for i, r in enumerate(success)
        if i not in used and xml_row_category(r["article"]) not in ("other",)
    ]
    other_extra = [(i, r) for i, r in enumerate(success) if i not in used and xml_row_category(r["article"]) == "other"]
    print(f"Лишние строки в XML (не соответствуют позициям КП 1-44): {len(extra) + len(other_extra)}")
    for _, r in extra:
        print(f"  {r['article']} x{r['quantity']:g} {r['params']} | {r['comment'][:70]}")
    for _, r in other_extra:
        print(f"  [оборудование] {r['article']} x{r['quantity']:g} {r['params']} | {r['comment'][:70]}")

    print("=" * 100)
    # Контроль: покупные позиции КП 45-59 не должны попасть в XML
    buyout_leaks = []
    for pos in buyout:
        for r in success:
            if pos["name"].lower()[:25] in str(r.get("comment", "")).lower():
                buyout_leaks.append((pos, r))
    if buyout_leaks:
        print("ВНИМАНИЕ: покупные позиции КП 45-59 попали в XML:")
        for pos, r in buyout_leaks:
            print(f"  КП {pos['num']}: {pos['name']} -> {r['article']} x{r['quantity']:g}")
    else:
        print("OK: покупные позиции КП 45-59 в XML не попали.")

    out_path = BASE_DIR / "verify_order.xml"
    out_path.write_text(xml_text, encoding="utf-8")
    print(f"XML сохранён: {out_path}")


if __name__ == "__main__":
    main()
