#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Парсер «заявки» от менеджера — неструктурированного Excel с позициями
по системам вентиляции (в отличие от проектной спецификации ОВ-листа).

Структура файла (например, «Заявка Столовая_ Молельная (1).xlsx»):
- секции — строки-заголовки без количества: система верхнего уровня
  в первой колонке («Молельная», «Столовая»), подсистема во второй
  («Приток (1000 м3/ч; 250 Па)», «Вытяжка кухня (5250 м3/ч; 550 Па)»);
- строки позиций: № | Наименование | примечание (опционально) | Кол-во | Ед.изм.;
- единицы измерения: шт, п.м., м2, пара;
- наименования — «магазинный» жаргон завода: «Воздуховод с/н 315»,
  «Отвод н.ж. (90) 400», «Переход односторонний 315/250», «Врезка 315/200»,
  «Заглушка 200», «Дроссель-клапан н.ж. 315» и т.д. (расшифровка — в
  process_specification_table.py).

Строки получают поле ``system`` («Молельная/Приток») и пометку
``source="zayavka"``, по которой process_specification_table применяет
расширенный список покупных позиций (вентиляторы, шумоглушители, КИПиА и
прочее, что завод не производит и в КП не включает).
"""

import re
from pathlib import Path
from typing import List, Optional

import pandas as pd

from project_spec_xlsx import is_project_spec_xlsx

# Единицы измерения, характерные для заявки (проектные спецификации и
# CSV-таблицы используют «м»/«м²» и колонки name/size/unit/quantity).
_ZAYAVKA_UNITS = {"шт", "п.м.", "п.м", "м.п.", "м2", "м²", "пара", "пар"}

_NUM_RE = re.compile(r"^\d+(?:[.,]\d+)?$")


def _is_number(value: str) -> bool:
    return bool(_NUM_RE.match(value.strip()))


def is_zayavka_xlsx(df: pd.DataFrame) -> bool:
    """Эвристика: заявка менеджера, а не проектный Excel и не CSV-таблица.

    df читается с ``header=None`` (заголовков колонок в файле нет).
    Признаки: ни одной ячейки с «наименование», ≥3 строк с единицами
    «шт»/«п.м.»/«м2»/«пара» и ≥1 строка-секция (текст без чисел и единиц).
    """
    if is_project_spec_xlsx(df):
        return False
    data_rows = 0
    section_rows = 0
    for row in df.iterrows():
        cells = [
            str(v).strip()
            for _, v in row[1].items()
            if pd.notna(v) and str(v).strip()
        ]
        if not cells:
            continue
        if any("наименование" in c.lower() for c in cells):
            return False
        if any(c.lower() in _ZAYAVKA_UNITS for c in cells):
            data_rows += 1
        elif all(not _is_number(c) and c.lower() not in _ZAYAVKA_UNITS for c in cells):
            # строка-заголовок секции: только текст, ни чисел, ни единиц
            section_rows += 1
    return data_rows >= 3 and section_rows >= 1


def parse_zayavka_xlsx(path: str | Path, sheet_name=0) -> List[dict]:
    """Извлекает строки позиций из заявки менеджера.

    Возвращает список dict с ключами name, size, unit, quantity, material,
    thickness, system, note, source. Позиции без количества сохраняются
    (quantity=None) — они уйдут в skipped с понятной причиной, а не
    потеряются молча.
    """
    df = pd.read_excel(path, sheet_name=sheet_name, header=None, dtype=object)

    rows: List[dict] = []
    level1: Optional[str] = None
    level2: Optional[str] = None

    for _, row in df.iterrows():
        cells = [None if pd.isna(v) else v for v in row.values]
        c0 = cells[0] if len(cells) > 0 else None
        c1 = cells[1] if len(cells) > 1 else None
        c2 = cells[2] if len(cells) > 2 else None
        c3 = cells[3] if len(cells) > 3 else None
        c4 = cells[4] if len(cells) > 4 else None

        name = str(c1).strip() if c1 is not None else ""

        # Строка позиции: номер в первой колонке + наименование во второй.
        # Всё остальное без количества/единиц — заголовок секции или
        # служебный комментарий («Позиции, выделенные желтым…»).
        is_pos_row = c0 is not None and _is_number(str(c0)) and bool(name)
        unit = str(c4).strip() if c4 is not None else ""
        has_unit = unit.lower() in _ZAYAVKA_UNITS
        if not is_pos_row:
            # заголовок секции или служебный комментарий. Длинные
            # комментарии в col0 («Заказчик просил вытяжку полностью…»)
            # заголовками не считаем — иначе затрут имя системы.
            if c0 is not None and not _is_number(str(c0)):
                t0 = str(c0).strip()
                if not re.search(r"\d", t0) and len(t0) <= 40:
                    level1 = t0
            elif name:
                level2 = name
            continue

        if not has_unit:
            # строка вида «12 | Опуск н.ж. …» без количества и единицы —
            # сохраняем как позицию, количество неизвестно
            unit = ""

        system = "/".join(s for s in (level1, level2) if s)

        note = str(c2).strip() if c2 is not None else ""
        quantity: Optional[float] = None
        if c3 is not None:
            try:
                quantity = float(str(c3).strip().replace(" ", "").replace(",", "."))
            except ValueError:
                quantity = None

        rows.append(
            {
                "name": name,
                "size": "",
                "unit": unit,
                "quantity": quantity,
                "material": "",
                "thickness": "",
                "system": system,
                "note": note,
                "source": "zayavka",
            }
        )

    return rows
