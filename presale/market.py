"""Рыночные цены предрасчёта через обёртку над price_search.

`PriceSearchMarketPricer` — синхронная реализация протокола `MarketPricer`
из presale/pricing.py поверх асинхронного `AsyncPriceEngine`: один запрос
на позицию (`{"name": name, "size": ""}`), ответ — минимальная цена оффера.

Fail-safe: пустой список офферов, исключение движка или вызов из кода с
уже запущенным event loop никогда не приводят к падению — возвращается None,
ошибка логируется.
"""
import asyncio
import logging
import threading
from decimal import Decimal
from typing import Optional

from price_search.engine import AsyncPriceEngine

logger = logging.getLogger(__name__)


class PriceSearchMarketPricer:
    """Синхронный MarketPricer поверх AsyncPriceEngine."""

    def __init__(self, engine: AsyncPriceEngine):
        self._engine = engine

    def market_price(self, name: str) -> Optional[Decimal]:
        """Минимальная рыночная цена позиции; None при отсутствии или ошибке."""
        try:
            results = self._run([{"name": name, "size": ""}])
        except Exception:
            logger.exception("price_search: ошибка поиска цены для %r", name)
            return None
        if not results:
            return None
        price = results[0].min_price
        if price is None:
            logger.info("price_search: нет офферов для %r", name)
        return price

    def _run(self, items: list[dict]):
        """Выполнить search, даже если нас вызвали внутри чужого event loop."""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self._engine.search(items))
        return self._run_in_thread(items)

    def _run_in_thread(self, items: list[dict]):
        """Отдельный поток со своим event loop (asyncio.run запрещён внутри чужого)."""
        box: list = []

        def worker():
            try:
                box.append(asyncio.run(self._engine.search(items)))
            except Exception as exc:  # пробросим в вызывающий поток
                box.append(exc)

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        thread.join()
        if box and isinstance(box[0], Exception):
            raise box[0]
        return box[0] if box else []
