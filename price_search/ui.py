#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Streamlit UI helpers for the price-search tab."""

import asyncio
import json
from datetime import datetime

import pandas as pd
import streamlit as st

from price_search.engine import AsyncPriceEngine
from price_search.models import SearchResult
from price_search.sources.aggregators import BlizkoSource, PulscenSource, TiuSource
from price_search.sources.base import SourceRegistry
from price_search.sources.hvac import GenericHvacSource
from price_search.fallback.search_engines import SearchEngineFallback
from price_search.storage import PriceStorage


DEFAULT_DB_PATH = "price_search.db"


def get_engine() -> AsyncPriceEngine:
    """Build the default async price engine with all configured sources."""
    storage = PriceStorage(DEFAULT_DB_PATH)
    registry = SourceRegistry()
    registry.register(PulscenSource())
    registry.register(TiuSource())
    registry.register(BlizkoSource())
    # Generic HVAC source configured for ventportal.ru selectors from the plan.
    registry.register(
        GenericHvacSource(
            base_url="https://ventportal.ru",
            search_path="/search",
            item_selector=".product",
            title_selector=".title",
            price_selector=".price",
        )
    )
    fallback = SearchEngineFallback()
    return AsyncPriceEngine(
        storage,
        registry,
        fallback_source=fallback,
        min_offers=3,
        max_age_days=7,
    )


def render_price_search_tab(skipped_items: list[dict]):
    """Render the "Price search for resold equipment" tab."""
    st.markdown(
        """
        <style>
        .price-card { background: #161618; border-radius: 12px; padding: 24px; margin-bottom: 16px; }
        .price-title { color: #DFDFD6; font-size: 16px; font-weight: 600; }
        .price-link { color: #3E63DD; }
        </style>
        """,
        unsafe_allow_html=True,
    )

    st.markdown(
        "<div class='price-title'>Цены на перекупное оборудование</div>",
        unsafe_allow_html=True,
    )

    if not skipped_items:
        st.info("Нет позиций для поиска цен.")
        return

    df = pd.DataFrame(skipped_items)
    if "category" not in df.columns:
        df["category"] = ""

    # Selection helpers stored in session state so "select all" / "deselect all"
    # can update the data editor defaults across reruns.
    if "price_search_select_all" not in st.session_state:
        st.session_state["price_search_select_all"] = True

    df["search"] = st.session_state["price_search_select_all"]
    df["include_in_report"] = True

    categories = sorted(df["category"].dropna().unique().tolist())
    selected_categories = st.multiselect(
        "Категории",
        categories,
        default=categories,
        key="price_categories",
    )
    filtered = df[df["category"].isin(selected_categories)].copy()

    edited = st.data_editor(
        filtered,
        num_rows="dynamic",
        use_container_width=True,
        key="price_skipped_editor",
    )

    if "search" not in edited.columns:
        edited["search"] = True
    selected = edited[edited["search"] == True]

    col1, col2 = st.columns(2)
    with col1:
        if st.button("Выбрать все", key="price_select_all"):
            st.session_state["price_search_select_all"] = True
            st.rerun()
    with col2:
        if st.button("Снять выделение", key="price_deselect_all"):
            st.session_state["price_search_select_all"] = False
            st.rerun()

    if st.button("Найти цены", type="primary", key="price_search_btn"):
        items = selected[["name", "size", "category"]].to_dict("records")
        if not items:
            st.warning("Выберите хотя бы одну позицию.")
            return

        engine = get_engine()
        progress = st.progress(0)
        results: list[SearchResult] = []
        for i, item in enumerate(items):
            try:
                batch = asyncio.run(engine.search([item]))
                results.extend(batch)
            except Exception as exc:
                st.error(f"Ошибка при поиске «{item.get('name', '')}»: {exc}")
            progress.progress(min(1.0, (i + 1) / len(items)))

        st.session_state["price_results"] = results

    if "price_results" in st.session_state:
        results: list[SearchResult] = st.session_state["price_results"]
        st.markdown("<div class='price-card'>", unsafe_allow_html=True)
        for r in results:
            if not isinstance(r, SearchResult):
                continue
            min_price = r.min_price
            best = r.best_offer
            if best:
                st.markdown(
                    f"**{r.item_name} {r.item_size}** — мин. цена: "
                    f"<span class='price-link'>{min_price} ₽</span> "
                    f"([{best.supplier or best.source}]({best.url}))",
                    unsafe_allow_html=True,
                )
                with st.expander("Топ-3"):
                    for offer in r.offers:
                        st.markdown(
                            f"- {offer.price} ₽ — [{offer.title[:60]}]({offer.url})"
                        )
            else:
                st.markdown(
                    f"**{r.item_name} {r.item_size}** — цена не найдена"
                )
        st.markdown("</div>", unsafe_allow_html=True)

        _download_results(results)


def _download_results(results: list[SearchResult]):
    data = []
    for r in results:
        for offer in r.offers:
            data.append(
                {
                    "name": r.item_name,
                    "size": r.item_size,
                    "source": offer.source,
                    "title": offer.title,
                    "price": float(offer.price),
                    "supplier": offer.supplier,
                    "url": offer.url,
                    "scraped_at": offer.scraped_at.isoformat(),
                }
            )
    df = pd.DataFrame(data)
    if not df.empty:
        st.download_button(
            "Скачать prices.xlsx",
            data=df.to_excel(index=False),
            file_name="equipment_prices.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key="price_download_xlsx",
        )
    st.download_button(
        "Скачать prices.json",
        data=json.dumps(data, ensure_ascii=False, indent=2),
        file_name="equipment_prices.json",
        mime="application/json",
        key="price_download_json",
    )
