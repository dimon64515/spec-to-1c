"""Локальный запуск демо пресейл-приложения: uvicorn presale_launcher:app"""
from decimal import Decimal
from pathlib import Path
from typing import Optional

from presale.app import create_app

ROOT = Path(__file__).parent


class _DemoMarket:
    """Рыночные цены отключены в демо (быстрый расчёт без сети).

    Строки без цены уходят в 🔴 — их можно вбить руками прямо в интерфейсе
    (это демонстрирует ручные правки и лог).
    """

    def market_price(self, name: str) -> Optional[Decimal]:
        return None


app = create_app(
    db_path=ROOT / "tmp/presale_demo/app.db",
    boris_paths=(ROOT / "tests/fixtures/boris/rect.xlsx",
                 ROOT / "tests/fixtures/boris/round.xlsx"),
    execute_url=None,  # 1С не подключена: оборудование уйдёт в 🔴/ручные цены
    market=_DemoMarket(),
)
