"""Раннер обработки заявки: PDF → позиции пайплайна + дилерские цены trading-строк.

Часть 1 (Task 3): run_processing — пайплайн, статусы, price_search.
Часть 2 (Task 4): load_to_1c, apply_analog.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from bitrix_bot import pipeline
from report_xlsx import is_trading_skip
from web_ui.store import Store

# Конфигурация аналогов подгружается лениво в apply_analog — файл mapping'а
# может отсутствовать в тестах, не связанных с аналогами.


async def run_processing(
    store: Store,
    request_id: int,
    file_name: str,
    pdf_bytes: bytes,
    price_engine: Any = None,
) -> None:
    """Прогнать PDF через пайплайн и сохранить результат в store.

    Статусы: processing → done (позиции, meta, дилерские цены trading-строк)
    или error (текст исключения в requests.error). Пустой success — не ошибка:
    заявка уходит в done с meta.escalation пайплайна.

    Args:
        store: хранилище заявок.
        request_id: id заявки в статусе processing.
        file_name: имя файла (для логов будущих; пайплайну не нужно).
        pdf_bytes: сырые байты PDF.
        price_engine: AsyncPriceEngine или None — цены ищутся только для
            trading-строк; сбой price_search не блокирует обработку.
    """
    try:
        success, skipped, meta = pipeline.process_pdf_to_positions(pdf_bytes)
    except Exception as exc:  # пайплайн падает по-разному — эскалация в error
        store.set_status(request_id, "error", str(exc))
        return

    trading = [s for s in skipped if is_trading_skip(s)]
    if price_engine is not None and trading:
        items = [
            {"name": s.get("name") or s.get("raw_name") or "", "size": s.get("size") or ""}
            for s in trading
        ]
        try:
            results = await price_engine.search(items)
        except Exception:
            # недоступность источников цен не должна ронять заявку:
            # строки получат price_info=None («цена не найдена»)
            results = []
        _attach_dealer_prices(trading, results)

    store.save_positions(request_id, success, skipped)
    store.save_meta(request_id, meta or {})
    store.set_status(request_id, "done")


def _attach_dealer_prices(trading: List[dict], results: List[Any]) -> None:
    """Записать в каждую trading-строку price_info из результатов price_search.

    Соответствие по позиции (zip): results[i] → trading[i]. Пустые офферы или
    отсутствие результата → {"price": None, "url": None, "supplier": None}.
    """
    for idx, row in enumerate(trading):
        result = results[idx] if idx < len(results) else None
        best = result.best_offer if result is not None else None
        row["price_info"] = {
            "price": str(result.min_price) if result is not None and result.min_price is not None else None,
            "url": best.url if best is not None else None,
            "supplier": best.supplier if best is not None else None,
        }
