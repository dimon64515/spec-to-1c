"""Детерминированное ценообразование строк расчёта по категориям.

Приоритеты источников (строго в этом порядке):
1. MATERIALS с material+thickness → прайс Бориса; hit → OK/ROUNDED, "boris".
2. Ручная база цен → MANUAL, "manual".
3. EQUIPMENT и материалы без цены Бориса → батч-запрос 1С → OK, "1c".
4. Мелочёвка (болты/анкеры/...) → рыночная цена → OK, "market".
5. WORKS: расценка из work_rates (rate × coefficient из строки) → "work_rates";
   иначе market_price × work_coefficient → "market_works".
6. Всё ненайденное → MISSING с поясняющим status_note.

Особые случаи: у материала без толщины ГОСТ-таблица не вызывается (нет
габаритов) — строка остаётся красной с note "не указана толщина";
спирально-навивные изделия в прайсе Бориса не представлены — note про спираль;
переходы без явного «тип N» в батч-поиске 1С идут как «… тип 1».

Функция чистая: единственное I/O — чтения из PresaleDB (ручные цены,
расценки работ). Входные строки не мутируются — работа идёт с копиями.
"""
import re
from decimal import Decimal
from typing import Optional, Protocol

from presale.boris_prices import BorisPriceTable
from presale.db import PresaleDB
from presale.models import Category, EstimateItem, PriceStatus

# Мелочёвка: крепёж и расходники; в 1С их обычно нет, после батч-мисса
# уходят на рынок.
_SMALL_STUFF_RE = re.compile(
    r"(болт|анкер|гаек|гайка|шайб|саморез|винт|шпилька|хомут|лента|герметик)"
)
# Форма воздуховода из имени: «кругл», «Ø» или диаметр «d200» → круглый.
_SHAPE_ROUND_RE = re.compile(r"(кругл|ø|d\d)")
_WS_RE = re.compile(r"\s+")
# Переходы: в номенклатуре 1С бывают «тип 1»/«тип 2» с разными ценами.
_TRANSITION_RE = re.compile(r"переход", re.IGNORECASE)
_TYPE_RE = re.compile(r"тип\s*\d", re.IGNORECASE)


def _one_c_query_name(name: str) -> str:
    """Имя для батч-поиска в 1С: переход без явного типа → «… тип 1».

    Дефолт заказчика: все переходы (круглые и прямоугольные) — тип 1.
    Явный «тип N» в наименовании не трогаем.
    """
    if _TRANSITION_RE.search(name) and not _TYPE_RE.search(name):
        return f"{name} тип 1"
    return name


class PriceBackend(Protocol):
    """Батч-поиск внутренних цен в 1С; реализация — Task 6."""

    def search_1c(self, names: list[str]) -> dict[str, Decimal]:
        """Вернуть {имя: цена} для известных позиций; один вызов на весь расчёт."""
        ...


class MarketPricer(Protocol):
    """Рыночная цена позиции; обёртка над price_search (Task 7) или stub."""

    def market_price(self, name: str) -> Optional[Decimal]:
        """Цена позиции на рынке; None, если не найдена."""
        ...


def _norm(name: str) -> str:
    """Нормализация имени: casefold + схлопывание пробелов."""
    return _WS_RE.sub(" ", name).strip().casefold()


def _shape_of(name: str) -> str:
    """Форма изделия из имени: «кругл|Ø|d\\d» → round, иначе rect.

    Эвристика намеренно минимальна: имя в смете почти всегда содержит
    явный маркер формы; всё остальное считаем прямоугольным.
    """
    return "round" if _SHAPE_ROUND_RE.search(name.casefold()) else "rect"


def _find_work_rate(name: str, rows: list[dict]) -> Optional[dict]:
    """Расценка работы по имени: взаимная подстрока после нормализации.

    Эвристика намеренно простая (без словаря синонимов): короткие общие
    слова вроде «монтаж» матчатся за счёт взаимности подстроки.
    """
    needle = _norm(name)
    for row in rows:
        candidate = _norm(row["name"])
        if needle and candidate and (needle in candidate or candidate in needle):
            return row
    return None


def price_items(
    items: list[EstimateItem],
    *,
    boris: BorisPriceTable,
    backend: PriceBackend,
    market: MarketPricer,
    db: PresaleDB,
    work_coefficient: Decimal = Decimal("1.5"),
) -> list[EstimateItem]:
    """Расставить внутренние цены строкам расчёта; вернуть новые копии."""
    out = [item.model_copy(deep=True) for item in items]
    work_rates = db.list_work_rates()

    # Проход 1: прайс Бориса (материалы с material+thickness) и ручные цены.
    need_1c: list[EstimateItem] = []
    for item in out:
        if (
            item.category == Category.MATERIALS
            and item.material
            and item.thickness is not None
        ):
            found = boris.lookup(
                material=item.material,
                thickness=item.thickness,
                shape=_shape_of(item.name),
                product_kind=item.product_kind or "standard",
            )
            if found is not None:
                item.internal_price = found.price
                item.status = PriceStatus.ROUNDED if found.rounded else PriceStatus.OK
                item.price_source = "boris"
                if found.rounded:
                    item.status_note = f"толщина округлена до {found.thickness_used}"
                continue
        manual = db.find_manual_price(item.name)
        if manual is not None:
            item.internal_price = manual
            item.status = PriceStatus.MANUAL
            item.price_source = "manual"
            continue
        if item.category != Category.WORKS:
            need_1c.append(item)

    # Проход 2: батч-запрос 1С по всем неоценённым позициям разом.
    if need_1c:
        found_1c = backend.search_1c(
            [_one_c_query_name(item.name) for item in need_1c]
        )
        for item in need_1c:
            price = found_1c.get(_one_c_query_name(item.name))
            if price is not None:
                item.internal_price = price
                item.status = PriceStatus.OK
                item.price_source = "1c"

    # Проход 3: мелочёвка через рынок.
    for item in out:
        if item.internal_price is not None or item.category == Category.WORKS:
            continue
        if _SMALL_STUFF_RE.search(item.name.casefold()):
            price = market.market_price(item.name)
            if price is not None:
                item.internal_price = price
                item.status = PriceStatus.OK
                item.price_source = "market"

    # Проход 4: работы — расценка из базы, иначе рынок × work_coefficient.
    for item in out:
        if item.category != Category.WORKS or item.internal_price is not None:
            continue
        rate = _find_work_rate(item.name, work_rates)
        if rate is not None:
            item.internal_price = rate["rate"] * rate["coefficient"]
            item.status = PriceStatus.OK
            item.price_source = "work_rates"
            continue
        price = market.market_price(item.name)
        if price is not None:
            item.internal_price = price * work_coefficient
            item.status = PriceStatus.OK
            item.price_source = "market_works"

    # Проход 5: всё ненайденное — красное с поясняющей причиной.
    for item in out:
        if item.internal_price is not None:
            continue
        item.status = PriceStatus.MISSING
        if item.category == Category.MATERIALS and item.material and item.thickness is None:
            item.status_note = "не указана толщина"
        elif item.product_kind == "spiral":
            item.status_note = "спирально-навивной: нет цены в прайсе"
        else:
            item.status_note = "нет цены: борис/1с/рынок"
    return out
