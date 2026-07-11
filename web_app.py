#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
Веб-приложение «PDF/CSV/XLSX спецификация → XML для 1С».

Запуск:
    venv\Scripts\streamlit run web_app.py
"""

import json
import logging
from typing import List, Optional

import pandas as pd
import streamlit as st

from api import (
    count_pdf_pages,
    extract_equipment_from_bytes,
    load_tables_from_pdf,
    read_uploaded_csv_or_excel,
)
from logging_config import configure_logging
from pdf_spec_extractor import df_to_spec_rows, normalize_columns, parse_text_fallback
from process_specification_table import detect_product_type, process_rows
from price_search.ui import render_price_search_tab


configure_logging()
logger = logging.getLogger(__name__)


DEFAULT_HEADER = {
    "numberDate": "",
    "numberOrder": "",
    "INN": "",
    "customer": "",
    "email": "",
    "phone": "",
    "contact": "",
}

# Product types treated as resold equipment for the price-search tab.
# Ducts and own fittings are produced and therefore excluded.
EQUIPMENT_PTYPES = {
    "diffuser",
    "ksd",
    "grille",
    "silencer",
    "damper",
    "shutter",
    "filter",
    "throttle",
    "roof_cap",
}


def _prepare_row(raw: dict) -> dict:
    """Нормализует строку перед передачей в process_rows."""
    row = {str(k).strip().lower(): v if not isinstance(v, str) else v.strip() for k, v in raw.items()}

    # Восстанавливаем params из JSON-строки, если data_editor сериализовал dict
    params_raw = row.get("params")
    if isinstance(params_raw, str) and params_raw:
        try:
            row["params"] = json.loads(params_raw)
        except json.JSONDecodeError:
            row["params"] = {}

    quantity_raw = str(row.get("quantity", "0")).replace(" ", "").replace(",", ".")
    try:
        row["quantity"] = float(quantity_raw)
    except ValueError:
        row["quantity"] = 0.0

    thickness_raw = str(row.get("thickness", "0.8")).replace(" ", "").replace(",", ".")
    try:
        row["thickness"] = float(thickness_raw)
    except ValueError:
        row["thickness"] = 0.8

    return row


def _normalize_skipped_for_prices(skipped_rows: List[dict]) -> List[dict]:
    """Приводит пропущенные позиции к формату, нужному для поиска цен.

    Итоговый элемент содержит поля: name, size, unit, quantity, category.
    """
    normalized: List[dict] = []
    seen = set()

    for row in skipped_rows:
        # Строки из режима автомаппинга оборудования имеют raw_name/model.
        if "raw_name" in row:
            name = str(row.get("raw_name", "")).strip()
            size = str(row.get("model", "")).strip()
            unit = "шт"
            quantity = 1.0
        else:
            name = str(row.get("name", "")).strip()
            size = str(row.get("size", "")).strip()
            unit = str(row.get("unit", "")).strip() or "шт"
            quantity_raw = row.get("quantity", 1)
            try:
                quantity = float(str(quantity_raw).replace(",", ".").replace(" ", ""))
            except (ValueError, TypeError):
                quantity = 1.0

        if not name:
            continue

        ptype = detect_product_type(name)
        if ptype not in EQUIPMENT_PTYPES:
            continue

        item = {
            "name": name,
            "size": size,
            "unit": unit,
            "quantity": quantity,
            "category": ptype,
        }
        key = (name, size, unit, quantity, ptype)
        if key in seen:
            continue
        seen.add(key)
        normalized.append(item)

    return normalized


def _render_main_tab(tab_main):
    """Render the specification → XML tab. Early returns here do not stop the app."""
    with tab_main:
        # --- Загрузка файла ---
        uploaded_file = st.file_uploader(
            "Загрузите файл",
            type=["pdf", "csv", "xlsx", "xls"],
            accept_multiple_files=False,
        )

        if uploaded_file is None:
            st.info("Загрузите файл для начала работы.")
            return

        file_name = uploaded_file.name
        file_bytes = uploaded_file.getvalue()

        combined_df: Optional[pd.DataFrame] = None
        equipment_skipped: Optional[List[dict]] = None
        is_equipment_mode = False

        # --- PDF ---
        if file_name.lower().endswith(".pdf"):
            # Определяем количество страниц
            total_pages = count_pdf_pages(file_bytes)

            st.write(f"**Страниц в PDF:** {total_pages}")
            selected_pages = st.multiselect(
                "Выберите страницы для обработки",
                options=list(range(1, total_pages + 1)),
                default=list(range(1, total_pages + 1)),
            )

            if not selected_pages:
                st.warning("Выберите хотя бы одну страницу.")
                return

            is_equipment_mode = st.checkbox(
                "Это ведомость оборудования заказчика (автомаппинг брендовых позиций)",
                value=False,
                help=(
                    "Если включено, PDF будет разобран как ведомость оборудования: "
                    "шумоглушители, фильтры, клапаны и т.п. будут сопоставлены с артикулами 1С."
                ),
            )

            if is_equipment_mode:
                with st.spinner("Извлекаю оборудование из PDF и сопоставляю с артикулами..."):
                    spec_rows, equipment_skipped = extract_equipment_from_bytes(
                        file_bytes, selected_pages=selected_pages
                    )

                if not spec_rows:
                    st.error("Не удалось распознать производимые позиции в ведомости оборудования.")
                else:
                    combined_df = pd.DataFrame(spec_rows)
                    # params в виде dict не всегда корректно сохраняется в data_editor,
                    # поэтому сериализуем его в JSON-строку и восстановим при генерации XML.
                    if "params" in combined_df.columns:
                        combined_df["params"] = combined_df["params"].apply(
                            lambda x: json.dumps(x, ensure_ascii=False) if isinstance(x, dict) else x
                        )

                if equipment_skipped:
                    with st.expander(f"Пропущено оборудования ({len(equipment_skipped)})"):
                        st.json(equipment_skipped)
            else:
                with st.spinner("Извлекаю таблицы из PDF..."):
                    result = load_tables_from_pdf(file_bytes, selected_pages=selected_pages)

                rows: List[dict] = []

                if "tables" in result:
                    tables_by_page = result["tables"]
                    selected_tables = []
                    for page_num, tables in tables_by_page.items():
                        if not tables:
                            continue
                        st.subheader(f"Страница {page_num}")
                        for idx, df in enumerate(tables, start=1):
                            st.write(f"**Таблица {idx}** ({len(df)} строк)")
                            st.dataframe(df, width="stretch")
                            if st.checkbox(
                                f"Включить таблицу {idx} со страницы {page_num}",
                                value=True,
                                key=f"tbl_{page_num}_{idx}",
                            ):
                                selected_tables.append(df)

                    if not selected_tables:
                        st.warning("Не выбрано ни одной таблицы.")
                        return

                    for df in selected_tables:
                        rows.extend(df_to_spec_rows(df))

                else:
                    # Fallback: текст
                    text_by_page = result["text_fallback"]
                    for page_num, lines in text_by_page.items():
                        st.subheader(f"Текст страницы {page_num}")
                        st.text("\n".join(lines[:50]))
                        rows.extend(parse_text_fallback(lines))

                if not rows:
                    st.error("Не удалось извлечь строки из PDF.")
                    return

                combined_df = pd.DataFrame(rows)

        # --- CSV / Excel ---
        else:
            with st.spinner("Читаю файл..."):
                df = read_uploaded_csv_or_excel(uploaded_file)
            st.write(f"**Строк в файле:** {len(df)}")
            combined_df = normalize_columns(df)

        # --- Редактирование таблицы ---
        if combined_df is None or combined_df.empty:
            st.error("Не удалось получить данные для редактирования.")
            return

        st.subheader("Редактирование спецификации")
        if is_equipment_mode:
            st.caption(
                "Колонки name, size, unit, quantity, material, thickness заполнены автоматически. "
                "Колонки article и params содержат выбранный артикул и параметры для 1С."
            )
            disabled_cols = [
                "name",
                "size",
                "article",
                "params",
                "detected_category",
                "detected_article",
                "source_raw_name",
                "source_model",
            ]
            # Оставляем видимыми только полезные колонки в первую очередь
            display_df = combined_df.copy()
        else:
            st.caption(
                "Колонки: name (наименование), size (размер), unit (ед.изм), "
                "quantity (количество), material (материал), thickness (толщина)."
            )
            disabled_cols = []
            display_df = combined_df

        edited_df = st.data_editor(
            display_df,
            num_rows="dynamic",
            width="stretch",
            disabled=[c for c in disabled_cols if c in display_df.columns],
            column_config={
                "quantity": st.column_config.NumberColumn(format="%.2f"),
                "thickness": st.column_config.NumberColumn(format="%.2f"),
            },
        )

        # --- Заголовок заказа ---
        with st.expander("Заголовок заказа (необязательно)"):
            header = {
                "numberDate": st.text_input("Дата заказа", value=""),
                "numberOrder": st.text_input("Номер заказа", value=""),
                "INN": st.text_input("ИНН", value=""),
                "customer": st.text_input("Заказчик", value=""),
                "email": st.text_input("Email", value=""),
                "phone": st.text_input("Телефон", value=""),
                "contact": st.text_input("Контактное лицо", value=""),
            }

        # --- Генерация XML ---
        if st.button("⚙️ Сгенерировать XML", type="primary"):
            # Преобразуем DataFrame в список dict
            raw_rows = edited_df.to_dict("records")
            rows = [_prepare_row(raw) for raw in raw_rows]

            with st.spinner("Генерирую XML..."):
                xml_text, skipped = process_rows(rows, header=header)

            # Учитываем уже пропущенное оборудование
            all_skipped = list(skipped)
            if equipment_skipped:
                all_skipped.extend(equipment_skipped)

            st.success(
                f"Готово! Загружено строк: {len(rows) - len(skipped)}, "
                f"пропущено в XML: {len(skipped)}"
            )
            if equipment_skipped:
                st.info(f"Пропущено оборудования (не производится/нет артикула): {len(equipment_skipped)}")

            # Сохраняем оборудование для вкладки с ценами
            st.session_state["skipped_for_prices"] = _normalize_skipped_for_prices(all_skipped)

            col1, col2 = st.columns(2)
            with col1:
                st.download_button(
                    label="⬇️ Скачать order.xml",
                    data=xml_text,
                    file_name="order.xml",
                    mime="application/xml",
                )
            with col2:
                skipped_json = json.dumps(all_skipped, ensure_ascii=False, indent=2)
                st.download_button(
                    label="⬇️ Скачать skipped.json",
                    data=skipped_json,
                    file_name="skipped.json",
                    mime="application/json",
                )

            if all_skipped:
                with st.expander(f"Пропущенные позиции ({len(all_skipped)})"):
                    st.json(all_skipped)

            with st.expander("Предпросмотр XML"):
                st.code(xml_text, language="xml")


def main():
    st.set_page_config(page_title="Спецификация → XML для 1С", layout="wide")
    st.title("📄 Спецификация → XML для 1С")
    st.markdown(
        "Загрузите PDF, CSV или Excel со спецификацией вентиляции. "
        "Выберите страницы/таблицы, отредактируйте данные и сгенерируйте XML."
    )

    tab_main, tab_prices = st.tabs(
        ["Спецификация → XML", "Цены на перекупное оборудование"]
    )

    _render_main_tab(tab_main)

    with tab_prices:
        skipped_for_prices = st.session_state.get("skipped_for_prices", [])
        render_price_search_tab(skipped_for_prices)


if __name__ == "__main__":
    main()
