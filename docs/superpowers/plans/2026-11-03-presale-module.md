# Presale Module (Stage 0+1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** FastAPI-приложение `presale/` в этом репозитории: загрузка Excel-таблицы Игоря (формат ChatGPT-книги, 6 листов) → детерминированный расчёт внутренних цен (прайс Бориса, 1С MCP, price_search, прайс работ) → веб-экран со сводкой и правками → выгрузка Excel (внутренняя 5+5 и КП заказчику).

**Architecture:** Весь расчёт детерминированный Python; LLM на этапах 0–1 не используется. Вход — книга ChatGPT (листы Итог/Работы/Материалы/Оборудование/Исходная ЛСР/Цены и источники). SQLite (WAL) хранит пользователей, расчёты, правки, ручные цены, прайс работ и переопределения прайса Бориса. Расчёт — фоновая задача в потоке; UI — server-rendered HTML.

**Tech Stack:** FastAPI, openpyxl, httpx, pydantic, SQLite (stdlib sqlite3, WAL), Decimal (не float) для всех денег.

**Spec:** `docs/superpowers/specs/2026-11-03-presale-module-design.md`

## Global Constraints

- Все денежные значения и площади — `Decimal`, округление `ROUND_HALF_UP` до 2 знаков только на границе отчёта; никакого float в расчётах.
- LLM не участвует в математике и подстановке цен (этапы 0–1 — вообще без LLM).
- Категории строго: `works` / `materials` / `equipment` (термины Игоря).
- КП-выгрузка для заказчика не содержит внутренних цен, маржи, источников — тест обязателен.
- Парсер входной книги толерантен к вариациям: имена листов/колонок матчатся по словарю синонимов (регистронезависимо, «кол-во»/«количество»/«qty»), неизвестное — не падает, строка уходит со статусом 🔴.
- Переиспользовать: `process_specification_table.normalize_thickness`, `price_search` (AsyncPriceEngine), транспорт MCP по образцу `bitrix_bot/pipeline.py:_load_via_execute_code`.
- Пользователи и переопределения цен правятся через UI, не через код.
- Русские docstrings/сообщения — как в окружающем коде.

## Review Focus

1. Книга ChatGPT с переименованными листами/колонками или лишними колонками → парсер не падает, маппит по синонимам, нераспознанные строки → 🔴. (Task 3)
2. Толщина из сметы отсутствует в сетке Бориса (0.65) → округление вверх внутри материальной группы (0.7), статус 🟡; толщина выше максимальной сетки (1.2) → 🔴, не молчаливый ноль. (Task 2)
3. Материал из сметы не матчится ни с одной группой Бориса («сталь оцинк.», «оцинкованная сталь», «08пс») → словарь синонимов материалов; неизвестный → 🔴. (Task 2)
4. Одновременная правка одной строки двумя менеджерами → обе правки сохраняются в `item_edits`, применяется последняя по времени, в UI виден лог. (Task 9)
5. Прайс Бориса с подозрительными значениями (AISI 304 0.80 = 1151) используется как есть, но админ может переопределить любую ячейку в UI, и переопределение применяется к новым расчётам. (Task 10)

---

### Task 1: Модели данных `presale/models.py`

**Files:**
- Create: `presale/__init__.py`, `presale/models.py`
- Test: `tests/test_presale_models.py`

**Interfaces:**
- Consumes: —
- Produces:
  - `Category(str, Enum)`: `WORKS="works"`, `MATERIALS="materials"`, `EQUIPMENT="equipment"`
  - `PriceStatus(str, Enum)`: `OK="ok"`, `ROUNDED="rounded"`, `MISSING="missing"`, `MANUAL="manual"`, `OVERRIDDEN="overridden"`
  - `EstimateItem(BaseModel)`: `row_id: str`, `category: Category`, `name: str`, `unit: str`, `qty: Decimal`, `market_price: Optional[Decimal]`, `material: Optional[str]`, `thickness: Optional[Decimal]`, `product_kind: Optional[str]`, `length_class: Optional[str]`, `internal_price: Optional[Decimal]`, `status: PriceStatus`, `status_note: str = ""`, `price_source: str = ""`
  - `EstimateItem.computed_total() -> Optional[Decimal]` — `qty * internal_price`, None если нет цены
  - `Summary(BaseModel)`: `by_category: dict[Category, dict[str, Decimal]]` (ключи `market`, `internal`), `market_total: Decimal`, `internal_total: Decimal`, `delta: Decimal`, `margin_pct: Optional[Decimal]`
  - `build_summary(items: list[EstimateItem]) -> Summary` — маржа = (internal−market)/internal*100, None если internal_total == 0

- [ ] **Step 1: Write the failing test**

```python
def test_summary_math_and_margin():
    items = [
        EstimateItem(row_id="1", category=Category.MATERIALS, name="Воздуховод", unit="м²",
                     qty=Decimal("10"), market_price=Decimal("1000"), internal_price=Decimal("800"),
                     status=PriceStatus.OK),
        EstimateItem(row_id="2", category=Category.WORKS, name="Монтаж", unit="шт",
                     qty=Decimal("2"), market_price=None, internal_price=Decimal("5000"),
                     status=PriceStatus.MANUAL),
    ]
    s = build_summary(items)
    assert s.market_total == Decimal("10000")
    assert s.internal_total == Decimal("18000")
    assert s.delta == Decimal("8000")
    assert s.by_category[Category.WORKS]["internal"] == Decimal("10000")

def test_summary_margin_none_when_no_internal():
    assert build_summary([]).margin_pct is None
```

- [ ] **Step 2:** Run `venv/bin/python -m pytest tests/test_presale_models.py -v` → FAIL (module not found).
- [ ] **Step 3:** Implement models per Interfaces. Enum на str для сериализации в SQLite/JSON.
- [ ] **Step 4:** Run tests → PASS.
- [ ] **Step 5:** Commit `feat(presale): модели EstimateItem/Summary с детерминированной математикой`.

---

### Task 2: Прайс Бориса `presale/boris_prices.py`

**Files:**
- Create: `presale/boris_prices.py`
- Test: `tests/test_presale_boris.py`
- Fixture: скопировать `reference/boris_prices/*.xlsx` в `tests/fixtures/boris/` (те же файлы).

**Interfaces:**
- Consumes: —
- Produces:
  - `BorisPrice(BaseModel)`: `price: Decimal`, `thickness_used: Decimal`, `rounded: bool`
  - `class BorisPriceTable`: `__init__(self, overrides: dict | None = None)`; `@classmethod from_xlsx(cls, rect_path: Path, round_path: Path, overrides: dict | None = None) -> "BorisPriceTable"`
  - `lookup(self, *, material: str, thickness: Decimal, shape: str, product_kind: str = "standard") -> Optional[BorisPrice]` — `shape`: `"rect" | "round"`; `product_kind`: `"standard" | "short" | "fitting" | "nonstandard"` (колонки прайса); None если материал неизвестен или толщина выше сетки.
  - `MATERIAL_ALIASES: dict[str, str]` — «оцинкованная сталь»/«оцинк.»/«08пс» → `ocsink`, «нерж»/«aisi 430» → `aisi430`, «aisi 304» → `aisi304`, «черная сталь»/«ст3» → `black`.
  - `overrides` — `{(material_group, thickness, shape, product_kind): Decimal}`, применяется поверх xlsx (источник — таблица БД из Task 4).

Логика: материал нормализуется через `MATERIAL_ALIASES` (casefold, без «рулон/лист/штрипс»); толщина округляется ВВЕРХ до ближайшей доступной в сетке материальной группы (0.65→0.7, `rounded=True`); выше максимума — None. Сетка и цены читаются из двух xlsx (строки = материал+толщина, колонки = product_kind).

- [ ] **Step 1: Write the failing test**

```python
def test_lookup_rounds_thickness_up():
    t = BorisPriceTable.from_xlsx(RECT, ROUND)
    r = t.lookup(material="оцинкованная сталь", thickness=Decimal("0.65"), shape="round")
    assert r is not None and r.thickness_used == Decimal("0.7") and r.rounded is True
    assert r.price == Decimal("1520")  # кругляк, рулон оц. 0.70, прямошовный L=1250

def test_lookup_above_grid_returns_none():
    t = BorisPriceTable.from_xlsx(RECT, ROUND)
    assert t.lookup(material="оцинковка", thickness=Decimal("1.2"), shape="round") is None

def test_material_alias_unknown_returns_none():
    t = BorisPriceTable.from_xlsx(RECT, ROUND)
    assert t.lookup(material="титан", thickness=Decimal("0.7"), shape="round") is None

def test_override_beats_xlsx():
    t = BorisPriceTable.from_xlsx(RECT, ROUND, overrides={("aisi304", Decimal("0.8"), "rect", "standard"): Decimal("9000")})
    r = t.lookup(material="AISI 304", thickness=Decimal("0.8"), shape="rect")
    assert r.price == Decimal("9000")
```

- [ ] **Step 2:** Run pytest → FAIL.
- [ ] **Step 3:** Implement. Чтение xlsx через openpyxl (`data_only=True`); маппинг строк прайса: регулярка `(AISI 430|AISI 304|оц|08ПС|черн)` + толщина из имени. Колонки rect: `standard`=«Прямой участок L=1250», `short`=«L<1250», `fitting`=«Фасонные изделия», `nonstandard`=«Нестандартные»; кругляк: `standard`=«Прямошовный L=1250», `short`=«L<1250», `fitting`, `nonstandard`; спирально-навивной — отдельная колонка, в v1 матчить в `standard` кругляка не нужно (фиксируем `product_kind="spiral"` → None → 🔴, отметить в status_note).
- [ ] **Step 4:** Run tests → PASS.
- [ ] **Step 5:** Commit `feat(presale): прайс Бориса из xlsx с округлением толщины и переопределениями`.

---

### Task 3: Парсер книги ChatGPT `presale/chatgpt_table.py`

**Files:**
- Create: `presale/chatgpt_table.py`
- Test: `tests/test_presale_chatgpt_table.py`
- Fixture: `tests/fixtures/igor/ИИ Рыночный_расчёт_ЛСР_26-09-07.xlsx` (копия из `tmp/igor_samples/lsr_26_09_07/reference_report.xlsx`).

**Interfaces:**
- Consumes: `presale.models` (Task 1).
- Produces:
  - `parse_chatgpt_workbook(path: Path | bytes) -> list[EstimateItem]` — читает листы Работы/Материалы/Оборудование (матч по словарю синонимов имён листов, casefold), колонки маппит по синонимам заголовков: name ← «наименование», unit ← «ед.»/«ед. изм.»/«единица», qty ← «количество»/«кол-во»/«qty», market_price ← «цена за ед.»/«цена». Значения-формулы читать с `data_only=True`; если кэш значений пуст (книга без вычисленных значений) — читать число из строки формулы нельзя → qty/price None → 🔴 с note. `row_id` = значение колонки «ID» либо `f"{sheet}:{row}"`. Категория — по листу. `status=MISSING` до прогона ценообразования.
  - `SHEET_ALIASES: dict[Category, tuple[str, ...]]`, `COLUMN_ALIASES: dict[str, tuple[str, ...]]`

- [ ] **Step 1: Write the failing test**

```python
def test_parse_real_igor_workbook():
    items = parse_chatgpt_workbook(FIXTURE)
    assert {i.category for i in items} == {Category.WORKS, Category.MATERIALS, Category.EQUIPMENT}
    works = [i for i in items if i.category == Category.WORKS]
    assert works[0].name  # «Установка воздухонагревателей…»
    assert works[0].qty > 0 and works[0].unit == "шт"

def test_renamed_columns_still_parse(tmp_path):
    # книга с колонками «Кол-во» и листом «работы» в нижнем регистре парсится
```

- [ ] **Step 2:** Run pytest → FAIL.
- [ ] **Step 3:** Implement. Синонимы через нормализацию: `casefold`, схлопнуть пробелы/неразрывные, убрать точки. Пустые строки и строки-заголовки («Работы», «Рубли без НДС…») пропускать: строка валидна, если есть name и (qty или market_price).
- [ ] **Step 4:** Run tests → PASS.
- [ ] **Step 5:** Commit `feat(presale): толерантный парсер книги ChatGPT (6 листов)`.

---

### Task 4: Хранилище SQLite `presale/db.py`

**Files:**
- Create: `presale/db.py`
- Test: `tests/test_presale_db.py`

**Interfaces:**
- Consumes: `presale.models`.
- Produces:
  - `class PresaleDB`: `__init__(self, path: Path)` — создаёт схему (WAL), идемпотентно.
  - Таблицы: `users(id, login UNIQUE, pass_hash, display_name)`; `estimates(id, owner_id, title, customer, tender_no, deadline, created_at, source_filename, status)`; `estimate_items(estimate_id, row_id, payload_json, PRIMARY KEY(estimate_id, row_id))`; `item_edits(id, estimate_id, row_id, user_id, field, old_value, new_value, ts)`; `manual_prices(id, normalized_name UNIQUE, price, unit, set_by, ts)`; `work_rates(id, name, unit, rate, coefficient)`; `price_overrides(scope, key_json, price, set_by, ts)` (scope: `boris` | `work_rate`).
  - Методы: `create_estimate(owner_id, meta: dict) -> int`; `save_items(estimate_id, items: list[EstimateItem])`; `get_estimate(estimate_id) -> tuple[dict, list[EstimateItem]]`; `list_estimates() -> list[dict]`; `log_edit(estimate_id, row_id, user_id, field, old, new)`; `apply_edit(estimate_id, row_id, field, value)` (обновляет payload_json); `get_edits(estimate_id, row_id) -> list[dict]`; `find_manual_price(name: str) -> Optional[Decimal]` (нормализация: casefold, схлопнуть пробелы); `set_manual_price(name, price, unit, user)`; `list_work_rates() -> list[dict]`; `upsert_work_rate(...)`; `get_overrides(scope) -> dict`; `set_override(scope, key, price, user)`; `verify_user(login, password) -> Optional[int]` (hashlib pbkdf2_hmac, 100k итераций); `create_user(login, password, display_name)`.

- [ ] **Step 1: Write the failing test**

```python
def test_roundtrip_estimate_and_edits(tmp_path):
    db = PresaleDB(tmp_path / "t.db")
    uid = db.create_user("igor", "secret", "Игорь")
    assert db.verify_user("igor", "secret") == uid
    assert db.verify_user("igor", "wrong") is None
    eid = db.create_estimate(uid, {"title": "ЛСР 26-09-07", "customer": "ООО Ромашка"})
    db.save_items(eid, [EstimateItem(row_id="Л1", category=Category.WORKS, name="Монтаж", unit="шт",
                                     qty=Decimal("1"), status=PriceStatus.MISSING)])
    db.apply_edit(eid, "Л1", "internal_price", "5000")
    db.log_edit(eid, "Л1", uid, "internal_price", None, "5000")
    meta, items = db.get_estimate(eid)
    assert items[0].internal_price == Decimal("5000")
    assert db.get_edits(eid, "Л1")[0]["new_value"] == "5000"

def test_manual_price_lookup_normalizes(tmp_path):
    db = PresaleDB(tmp_path / "t.db")
    db.set_manual_price("Болт  анкерный М10", Decimal("25"), "шт", 1)
    assert db.find_manual_price("болт анкерный м10") == Decimal("25")
```

- [ ] **Step 2:** Run pytest → FAIL.
- [ ] **Step 3:** Implement. Все Decimal в SQLite — как TEXT (`str(value)`), обратно `Decimal(...)`. payload_json — `EstimateItem.model_dump_json()`.
- [ ] **Step 4:** Run tests → PASS.
- [ ] **Step 5:** Commit `feat(presale): SQLite-хранилище расчётов, правок, ручных цен и прайсов`.

---

### Task 5: Ценообразование `presale/pricing.py`

**Files:**
- Create: `presale/pricing.py`
- Test: `tests/test_presale_pricing.py`

**Interfaces:**
- Consumes: Task 1 (models), Task 2 (`BorisPriceTable`), Task 4 (`PresaleDB`).
- Produces:
  - `class PriceBackend(Protocol)`: `search_1c(self, names: list[str]) -> dict[str, Decimal]` — батч; реализация в Task 6, в тестах — stub.
  - `class MarketPricer(Protocol)`: `market_price(self, name: str) -> Optional[Decimal]` — обёртка над price_search (Task 7) или stub.
  - `price_items(items: list[EstimateItem], *, boris: BorisPriceTable, backend: PriceBackend, market: MarketPricer, db: PresaleDB, work_coefficient: Decimal = Decimal("1.5")) -> list[EstimateItem]` — мутирует копии, возвращает новый список.

Правила (детерминированно, в порядке приоритета):
1. `MATERIALS` с material+thickness → `boris.lookup(...)`; hit → `internal_price=price`, `status=OK`/`ROUNDED`, `price_source="boris"`; miss → дальше.
2. Ручная база: `db.find_manual_price(name)` → `MANUAL`, `price_source="manual"`.
3. `EQUIPMENT` и материалы без Бориса: батч `backend.search_1c(names)` по всем строкам разом → `OK`, `price_source="1c"`.
4. Мелочёвка (эвристика: name матчит `(болт|анкер|гаек|гайка|шайб|саморез|винт|шпилька|хомут|лента|герметик)` casefold) → `market.market_price(name)` → `OK`, `price_source="market"`.
5. `WORKS`: `db.list_work_rates()` — матч по имени (синонимы/подстрока); hit → `rate*coefficient`… нет: `rate * work_coefficient` применяется только если `rate` помечен как рыночный (поле rate уже «наша» ставка по умолчанию; коэффициент — отдельная колонка `coefficient` в work_rates, по умолчанию 1.5 для рыночных) → `internal = rate * coefficient`; miss → `market_price(name) * 1.5` → `OK`, `price_source="market_works"`; совсем нет → 🔴.
6. Всё ненайденное → `status=MISSING`, `internal_price=None`, `status_note="нет цены: борис/1с/рынок"`.
7. `thickness` отсутствует у материала → подставить `normalize_thickness` из `process_specification_table` (explicit=False, params={})… Нет: для смет нет габаритов — оставить 🔴 `status_note="не указана толщина"`, НЕ вызывать ГОСТ-таблицу без размеров. (Решение: без габаритов ГОСТ неприменим.)

- [ ] **Step 1: Write the failing test**

```python
def test_material_priced_by_boris_rounded(stub_backend, stub_market, db, boris):
    items = [EstimateItem(row_id="1", category=Category.MATERIALS, name="Воздуховод оцинк. 0.65",
                          unit="м²", qty=Decimal("41"), material="оцинкованная сталь",
                          thickness=Decimal("0.65"), status=PriceStatus.MISSING)]
    out = price_items(items, boris=boris, backend=stub_backend, market=stub_market, db=db)
    assert out[0].status == PriceStatus.ROUNDED and out[0].price_source == "boris"
    assert out[0].computed_total() == out[0].internal_price * Decimal("41")

def test_equipment_uses_batched_1c(stub_backend, stub_market, db, boris):
    items = [EstimateItem(row_id="1", category=Category.EQUIPMENT, name="Решетка LITENED 70-40",
                          unit="шт", qty=Decimal("4"), status=PriceStatus.MISSING)]
    out = price_items(items, boris=boris, backend=stub_backend, market=stub_market, db=db)
    assert stub_backend.called_with == [["Решетка LITENED 70-40"]]  # один батч-вызов
    assert out[0].price_source == "1c"

def test_works_market_times_1_5(stub_backend, stub_market, db, boris):
    stub_market.price = Decimal("2000")
    items = [EstimateItem(row_id="1", category=Category.WORKS, name="Монтаж вентиляции",
                          unit="м²", qty=Decimal("10"), status=PriceStatus.MISSING)]
    out = price_items(items, boris=boris, backend=stub_backend, market=stub_market, db=db)
    assert out[0].internal_price == Decimal("3000") and out[0].price_source == "market_works"

def test_missing_everywhere_is_red(stub_backend_empty, stub_market_empty, db, boris):
    items = [EstimateItem(row_id="1", category=Category.EQUIPMENT, name="Неведомая хрень",
                          unit="шт", qty=Decimal("1"), status=PriceStatus.MISSING)]
    out = price_items(items, boris=boris, backend=stub_backend_empty, market=stub_market_empty, db=db)
    assert out[0].status == PriceStatus.MISSING and out[0].internal_price is None
```

- [ ] **Step 2:** Run pytest → FAIL.
- [ ] **Step 3:** Implement. Порядок правил строго как выше; `price_items` чистый (без I/O кроме db-чтений).
- [ ] **Step 4:** Run tests → PASS.
- [ ] **Step 5:** Commit `feat(presale): детерминированное ценообразование по категориям с приоритетами источников`.

---

### Task 6: Батч-клиент цен 1С `presale/mcp_1c.py`

**Files:**
- Create: `presale/mcp_1c.py`
- Test: `tests/test_presale_mcp_1c.py`

**Interfaces:**
- Consumes: Task 5 (`PriceBackend`).
- Produces:
  - `class OneCPriceBackend`: `__init__(self, execute_url: str, timeout: float = 120.0)`; `search_1c(self, names: list[str]) -> dict[str, Decimal]` — один HTTP-вызов `execute_code` с BSL-пayload: цикл по массиву имён, поиск номенклатуры по наименованию (подобие), выборка последней цены; ответ — текст `ИМЯ<TAB>ЦЕНА` построчно в хвосте после ` ## ` (формат как в `bitrix_bot/pipeline._parse_price_tail`). Парсер: `parse_price_response(text: str) -> dict[str, Decimal]`.
  - Транспорт и envelope (`success/data/result`) — по образцу `bitrix_bot/pipeline.py:_load_via_execute_code`; сетевой сбой → `BackendUnavailableError` (вызывающий код ловит и помечает строки 🔴, не падает).

- [ ] **Step 1: Write the failing test**

```python
def test_parse_price_response():
    text = "Успех=true ## Решетка LITENED 70-40\t1250.50\nВентилятор ВК 300\t18900"
    assert parse_price_response(text) == {"Решетка LITENED 70-40": Decimal("1250.50"),
                                          "Вентилятор ВК 300": Decimal("18900")}

def test_search_1c_batches_single_http_call(httpx_mock):
    httpx_mock.add_response(json={"success": True, "data": "Успех=true ## А\t10\nБ\t20"})
    backend = OneCPriceBackend("http://localhost:6005/api/execute_code")
    assert backend.search_1c(["А", "Б"]) == {"А": Decimal("10"), "Б": Decimal("20")}
    assert len(httpx_mock.get_requests()) == 1

def test_envelope_error_raises_unavailable(httpx_mock):
    httpx_mock.add_response(json={"success": False, "error": "boom"})
    with pytest.raises(BackendUnavailableError):
        OneCPriceBackend("http://x").search_1c(["А"])
```

- [ ] **Step 2:** Run pytest → FAIL (нужен `pytest-httpx` — добавить в dev-deps).
- [ ] **Step 3:** Implement. BSL-текст payload — константа-шаблон с json-подстановкой массива имён (экранирование через `json.dumps`).
- [ ] **Step 4:** Run tests → PASS.
- [ ] **Step 5:** Commit `feat(presale): батч-клиент цен 1С через MCP execute_code`.

---

### Task 7: Рыночные цены `presale/market.py`

**Files:**
- Create: `presale/market.py`
- Test: `tests/test_presale_market.py`

**Interfaces:**
- Consumes: Task 5 (`MarketPricer`), `price_search.engine.AsyncPriceEngine`, `price_search.models.SearchResult`.
- Produces:
  - `class PriceSearchMarketPricer`: `__init__(self, engine: AsyncPriceEngine)`; `market_price(self, name: str) -> Optional[Decimal]` — синхронная обёртка: `asyncio.run(engine.search([{"name": name, "size": ""}]))`, вернуть `result[0].min_price`, None если нет офферов или исключение (логировать, не падать).

- [ ] **Step 1: Write the failing test**

```python
def test_returns_min_price(fake_engine):
    pricer = PriceSearchMarketPricer(fake_engine)  # engine.search -> [SearchResult(offers=[...10, 25, 14])]
    assert pricer.market_price("болт анкерный М10") == Decimal("10")

def test_engine_error_returns_none(broken_engine):
    assert PriceSearchMarketPricer(broken_engine).market_price("x") is None
```

- [ ] **Step 2:** Run pytest → FAIL.
- [ ] **Step 3:** Implement. Fake/stub engine в тестах — тот же интерфейс `search`.
- [ ] **Step 4:** Run tests → PASS.
- [ ] **Step 5:** Commit `feat(presale): рыночные цены через price_search с fail-safe`.

---

### Task 8: Экспорт Excel `presale/report.py`

**Files:**
- Create: `presale/report.py`
- Test: `tests/test_presale_report.py`

**Interfaces:**
- Consumes: Task 1 (models, `build_summary`).
- Produces:
  - `build_internal_xlsx(items: list[EstimateItem], meta: dict) -> bytes` — листы: «Итог» (сводка из `build_summary`: по категориям market/internal/delta + НДС 22% колонкой), «Работы», «Материалы», «Оборудование». Колонки листа категории (порядок фиксирован): `Наименование, Ед., Количество, Рыночная цена, Рыночная сумма, Наша цена, Наша сумма, Источник цены, Статус, Примечание`.
  - `build_customer_xlsx(items: list[EstimateItem], meta: dict) -> bytes` — те же листы, колонки: `Наименование, Ед., Количество, Цена, Сумма` (цена = internal, НДС добавляется на «Итог»). Ни одной колонки market/маржа/источник/статус.
  - Статусы рендерятся символами 🟢🟡🔴 + текст.

- [ ] **Step 1: Write the failing test**

```python
def test_internal_has_5_plus_5_columns():
    data = build_internal_xlsx(ITEMS, META)
    ws = openpyxl.load_workbook(io.BytesIO(data))["Материалы"]
    headers = [c.value for c in ws[1]]
    assert headers == ["Наименование", "Ед.", "Количество", "Рыночная цена", "Рыночная сумма",
                       "Наша цена", "Наша сумма", "Источник цены", "Статус", "Примечание"]

def test_customer_export_has_no_internal_data():
    data = build_customer_xlsx(ITEMS, META)
    wb = openpyxl.load_workbook(io.BytesIO(data))
    text = " ".join(str(c.value) for ws in wb for row in ws.iter_rows() for c in row)
    assert "Рыночн" not in text and "маржа" not in text.lower() and "Источник" not in text
    assert [c.value for c in wb["Материалы"][1]] == ["Наименование", "Ед.", "Количество", "Цена", "Сумма"]

def test_totals_use_decimal_not_float():
    data = build_internal_xlsx(ITEMS, META)
    ws = openpyxl.load_workbook(io.BytesIO(data))["Итог"]
    # сумма по категории сходится с Decimal-расчётом build_summary (точное значение, не 1234.5699999)
```

- [ ] **Step 2:** Run pytest → FAIL.
- [ ] **Step 3:** Implement на openpyxl (стиль — жирный заголовок, авто-ширина колонок). Decimal → float только в ячейку, после квантизации 0.01.
- [ ] **Step 4:** Run tests → PASS.
- [ ] **Step 5:** Commit `feat(presale): выгрузки Excel — внутренняя 5+5 и КП заказчику без внутренних данных`.

---

### Task 9: Веб-приложение `presale/app.py` + фоновые задачи + правки

**Files:**
- Create: `presale/app.py`, `presale/jobs.py`, `presale/templates/base.html`, `login.html`, `history.html`, `upload.html`, `result.html`, `progress.html`
- Test: `tests/test_presale_app.py`

**Interfaces:**
- Consumes: Tasks 1–8.
- Produces:
  - `create_app(db_path: Path, boris_paths: tuple[Path, Path], execute_url: str | None) -> FastAPI`
  - `presale/jobs.py`: `class JobRunner`: `submit(self, fn, *args) -> str` (job_id), `status(self, job_id) -> dict` (`{"state": "pending|running|done|error", "step": str, "error": str|None, "result_id": int|None}`). Поток на задачу, состояние в памяти + threading.Lock.
  - Эндпоинты (все кроме `/login` требуют сессию):
    - `GET /login`, `POST /login`, `GET /logout`
    - `GET /` — история расчётов (всех пользователей, сортировка: просроченный дедлайн сверху, затем created_at desc)
    - `GET /upload`, `POST /upload` (файл + customer/tender_no/deadline) → создаёт estimate (status="running"), job: `parse_chatgpt_workbook` → `price_items` → `db.save_items` → status="done"; ответ — redirect на `/estimate/{id}`
    - `GET /job/{job_id}` — JSON статуса (progress.html поллит раз в 1 сек)
    - `GET /estimate/{id}` — сводка (`build_summary`) + таблица по категориям с фильтром `?status=missing`; кнопки скачивания
    - `POST /estimate/{id}/item/{row_id}/edit` (form: `field` in {internal_price, qty, category}, `value`) — в транзакции: `apply_edit` + `log_edit(user из сессии, old, new)`; redirect обратно. Два менеджера могут править одну строку: обе правки логируются, применяется последняя (проверка тестом двух POST подряд от разных сессий).
    - `GET /estimate/{id}/export/internal.xlsx`, `GET /estimate/{id}/export/customer.xlsx`
    - `GET /estimate/{id}/item/{row_id}/edits` — лог правок строки
  - Аутентификация: cookie-сессия (подписанная itsdangerous, секрет из env `PRESALE_SECRET`, fallback — случайный при старте с предупреждением в лог). Пароли — через `db.verify_user`. Пользователи заводятся скриптом `python -m presale.app --create-user` (команда в app.py через argparse).
  - Фоновые правки цен пересчитывают только affected строки и summary на лету (пересчёт — чистая функция, без job).
  - Запуск: `uvicorn presale.app:create_app --factory` (документировать в README блоке файла).

- [ ] **Step 1: Write the failing test** (httpx/TestClient, tmp db, stub backend/market через dependency override):

```python
def test_login_upload_progress_result_flow(client, igor_fixture_bytes):
    assert client.get("/").status_code == 303  # редирект на /login
    client.post("/login", data={"login": "igor", "password": "secret"})
    r = client.post("/upload", files={"file": ("lsr.xlsx", igor_fixture_bytes)},
                    data={"customer": "ООО Ромашка"}, follow_redirects=False)
    est_url = r.headers["location"]
    # job отработал (runner в тесте синхронный)
    page = client.get(est_url)
    assert "Сводка" in page.text and "🟢" in page.text or "🔴" in page.text

def test_concurrent_edits_both_logged(client):
    # два разных пользователя правят одну строку: обе записи в /edits, применена последняя

def test_customer_export_endpoint(client):
    r = client.get("/estimate/1/export/customer.xlsx")
    assert r.status_code == 200 and "Рыночн" not in ...  # повтор проверки Task 8 через HTTP
```

- [ ] **Step 2:** Run pytest → FAIL.
- [ ] **Step 3:** Implement. В тестах JobRunner подменяется синхронным (`submit` выполняет сразу) через параметр `create_app(..., sync_jobs=True)`.
- [ ] **Step 4:** Run tests → PASS.
- [ ] **Step 5:** Commit `feat(presale): веб-приложение — авторизация, загрузка, расчёт, правки с логом, выгрузки`.

---

### Task 10: Админка прайсов (глобальные правки цен)

**Files:**
- Create: `presale/templates/admin_prices.html`
- Modify: `presale/app.py` (новые роуты)
- Test: `tests/test_presale_admin.py`

**Interfaces:**
- Consumes: Task 4 (`get_overrides`/`set_override`, `list_work_rates`/`upsert_work_rate`, `set_manual_price`), Task 2 (ключи overrides), Task 9.
- Produces:
  - `GET /admin/prices` — три блока: (1) переопределения ячеек Бориса (форма: материал select, толщина select, shape, product_kind, цена); (2) прайс работ (таблица work_rates: name/unit/rate/coefficient, редактирование построчно); (3) ручные цены (поиск, добавление name/unit/price).
  - `POST /admin/prices/boris` — `set_override("boris", key, price, user)`; ключ = json `{"material": ..., "thickness": ..., "shape": ..., "product_kind": ...}` в том же формате, что читает `BorisPriceTable.from_xlsx(overrides=...)`.
  - `POST /admin/prices/work-rates`, `POST /admin/prices/manual`.
  - Новые расчёты строят `BorisPriceTable` с `overrides=db.get_overrides("boris")` (подключить в Task 9 upload-job: модифицировать `create_app` — чтение overrides на каждый расчёт).

- [ ] **Step 1: Write the failing test**

```python
def test_boris_override_applies_to_new_calculation(client):
    client.post("/admin/prices/boris", data={"material": "aisi304", "thickness": "0.8",
                                             "shape": "rect", "product_kind": "standard",
                                             "price": "9000"})
    # новый upload с позицией AISI 304 0.8 → internal_price == 9000, price_source == "boris"

def test_work_rate_edit_applies(client):
    client.post("/admin/prices/work-rates", data={"id": "1", "rate": "3500", "coefficient": "1.5"})
    # пересчёт работ в новом расчёте использует 3500*1.5
```

- [ ] **Step 2:** Run pytest → FAIL.
- [ ] **Step 3:** Implement. Доступ к `/admin/*` — любой авторизованный (ролей нет, по спеке).
- [ ] **Step 4:** Run tests → PASS.
- [ ] **Step 5:** Commit `feat(presale): админка глобальных цен — переопределения Бориса, прайс работ, ручные цены`.

---

### Task 11: Сквозной тест на образце Игоря + дедлайны/похожесть

**Files:**
- Create: `tests/test_presale_e2e.py`
- Modify: `presale/app.py` (бейдж «похожий расчёт» на /upload, подсветка дедлайна в history.html)

**Interfaces:**
- Consumes: всё выше.
- Produces:
  - `find_similar_estimates(db, items: list[EstimateItem], customer: str) -> list[int]` — совпадение по customer (casefold) ИЛИ пересечение ≥50% нормализованных имён строк с существующим расчётом; возвращает id по убыванию релевантности.

- [ ] **Step 1: Write the failing test**

```python
def test_e2e_igor_fixture_under_30s():
    # фикстура Игоря → upload → done; все позиции имеют status != None;
    # строки с толщиной вне сетки и неизвестным материалом — 🔴, остальные оценены;
    # время ценообразования (без сети, stub backend/market) < 5 сек на 200 позиций

def test_similar_badge_same_customer(client):
    # два upload с одинаковым customer → на странице второго виден бейдж со ссылкой на первый

def test_overdue_deadline_highlighted(client):
    # расчёт с deadline вчера → в истории строка имеет класс "overdue"
```

- [ ] **Step 2:** Run pytest → FAIL.
- [ ] **Step 3:** Implement `find_similar_estimates` (rapidfuzz `token_set_ratio` по именам; если зависимость тяжелая — difflib.SequenceMatcher, решить по факту установки) и правки шаблонов.
- [ ] **Step 4:** Run tests → PASS + прогон всего `pytest tests/ -x` (регрессия существующих тестов).
- [ ] **Step 5:** Commit `feat(presale): сквозной тест на образце Игоря, похожие расчёты, дедлайны`.

---

## Self-review notes

- Покрытие спеки: этап 0 (Tasks 3,5,8) ✓; этап 1: мультиюзер/правки/история (4,9), ручная база (4,9), прайс работ (5,10), глобальные правки цен (10), дедлайны и похожесть (11), КП-выгрузка (8) ✓. Этапы 2–3 (парсинг сырых смет LLM, проектная документация) — отдельный план после получения ~10 фикстур от Игоря.
- Прогресс-этапы (парсинг→цены→отчёт) в UI: job.status["step"] обновляется в upload-job (Task 9).
- Кэш цен 1С общий — обеспечивается price_search.storage для market и single-flight батчем для 1С; отдельный кэш 1С не строим (YAGNI на 5–10 пользователей).
