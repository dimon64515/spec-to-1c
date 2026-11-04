# Веб-интерфейс заявок (web_ui) — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Веб-интерфейс на FastAPI для загрузки PDF-спецификаций, просмотра позиций заявки с ценами (из 1С и дилерскими), подбора аналогов и загрузки заказа в 1С одной кнопкой.

**Architecture:** Новый пакет `web_ui/` поверх существующего пайплайна бота (`bitrix_bot.pipeline.process_pdf_to_positions`, `load_order_to_1c`, `_merge_prices`) и `price_search`. Состояние — sqlite (`requests` + `positions`). Серверные страницы Jinja2, polling статуса, без websockets и без фронтенд-фреймворков.

**Tech Stack:** Python 3.12, FastAPI, uvicorn, Jinja2, sqlite3 (stdlib), pytest, TestClient (httpx).

**Spec:** `docs/superpowers/specs/2026-11-02-web-orders-dashboard-design.md`

## Global Constraints

- Зависимости только из существующего venv проекта — новые пакеты не ставить (FastAPI, uvicorn, jinja2, httpx уже есть — см. `bitrix_bot/`).
- Порт по умолчанию **8092**, bind **127.0.0.1**.
- Локализация UI — русская; минимум текста на экранах, тултипы вместо подписей (дизайн-гайд dashboard-design-skill).
- Код и комментарии — в стиле существующих модулей (русские docstring'и, типы).
- Каждая задача завершается проходом тестов и коммитом.

## Review Focus

1. **Пустой success после пайплайна** (PDF без распознаваемых строк) — заявка должна уйти в статус `done` с эскалацией и пустыми таблицами, не падать: тест в Task 3.
2. **Дубли при повторном клике «Загрузить в 1С»** — повторная загрузка создаёт дубликат заказа в 1С; кнопка блокируется при статусе `loaded_1c`, повтор — только через явное действие с подтверждением: тест в Task 4.
3. **Skipped-строка без распознаваемого размера при «принять аналог»** — строка остаётся пропущенной со статусом «размер не распознан», UI не падает: тест в Task 2.
4. **Недоступность price_search источников** — перекупные позиции без дилерских цен показываются с «цена не найдена», не блокируют остальное: тест в Task 3.
5. **Прерванный polling** — страница деталей при статусе `error` показывает ошибку и разблокированные кнопки экспорта того, что есть: тест в Task 5.

---

### Task 1: Хранилище заявок (store.py)

**Files:**
- Create: `web_ui/__init__.py` (пустой)
- Create: `web_ui/store.py`
- Test: `tests/test_web_ui_store.py`

**Interfaces:**
- Produces:
  - `class Store` — `__init__(db_path: str)`; sqlite-таблицы `requests(id INTEGER PK, file_name TEXT, created_at TEXT, status TEXT, meta TEXT, header TEXT, order_number TEXT, error TEXT)` и `positions(id INTEGER PK, request_id INTEGER, kind TEXT, data TEXT)` (`kind` ∈ `loaded|skipped|trading`; `data`/`meta`/`header` — JSON-строки).
  - `create_request(file_name: str) -> int` — статус `processing`.
  - `set_status(request_id: int, status: str, error: str | None = None)` — статусы: `processing|done|error|loaded_1c`.
  - `save_positions(request_id: int, loaded: list[dict], skipped: list[dict]) -> None` — trading-строки из `skipped` выделяются через `report_xlsx.is_trading_skip` в `kind='trading'`, остальные в `kind='skipped'`.
  - `save_meta(request_id: int, meta: dict) -> None` — meta пайплайна (escalation, llm_recovered, partial_warning и т.д.).
  - `save_header(request_id: int, header: dict) -> None` — реквизиты заказа.
  - `get_request(request_id: int) -> dict | None` — распарсенные meta/header + статус.
  - `list_requests() -> list[dict]` — все заявки, новые первые, с подсчётом позиций и суммы из positions.
  - `get_positions(request_id: int, kind: str | None = None) -> list[dict]`.
  - `update_position(request_id: int, kind: str, index: int, data: dict) -> None` — заменить строку (для «принять аналог» и мержа цен).
  - `set_order_number(request_id: int, order_number: str) -> None`.

- [ ] **Step 1: Написать failing test `tests/test_web_ui_store.py`**

```python
def test_create_and_get_request(tmp_path):
    s = Store(str(tmp_path / "t.db"))
    rid = s.create_request("spec.pdf")
    r = s.get_request(rid)
    assert r["status"] == "processing" and r["file_name"] == "spec.pdf"

def test_save_and_list_positions_with_trading_split(tmp_path):
    # loaded 2 позиции с price/sum; skipped 2, из них одна is_trading_skip → trading
def test_update_position_replaces_row(tmp_path)
def test_status_error_stored(tmp_path)
def test_list_requests_orders_newest_first(tmp_path)
```

- [ ] **Step 2: Прогнать — упасть с ImportError/AttributeError**

Run: `venv/bin/python -m pytest tests/test_web_ui_store.py -v`
Expected: FAIL (модуль `web_ui.store` не существует)

- [ ] **Step 3: Реализовать `Store` в `web_ui/store.py`**

sqlite3 с `row_factory=sqlite3.Row`, одна миграция `CREATE TABLE IF NOT EXISTS` в `__init__`. JSON через `json.dumps/loads`. Подсчёт суммы в `list_requests`: `SELECT kind, data FROM positions WHERE request_id=?` и агрегация `sum(d.get("sum") or 0)` по loaded+trading в Python.

- [ ] **Step 4: Прогнать тесты — PASS**

Run: `venv/bin/python -m pytest tests/test_web_ui_store.py -v`

- [ ] **Step 5: Commit**

```bash
git add web_ui/ tests/test_web_ui_store.py
git commit -m "feat(web_ui): sqlite-хранилище заявок"
```

---

### Task 2: Подбор аналогов (analogs.py)

**Files:**
- Create: `web_ui/analogs.py`
- Test: `tests/test_web_ui_analogs.py`

**Interfaces:**
- Consumes: `map_customer_equipment.load_mapping()`, `detect_category(raw_name, mapping)`, `parse_dimensions_from_text(text, patterns)`, `determine_section(dims, category)`, `select_article(category, section)`, `build_spec_name`, `build_spec_size` (все в `map_customer_equipment.py`).
- Produces:
  - `def suggest_analog(row: dict, mapping: dict) -> dict | None` — по skipped-строке `{name, size, unit, quantity}` вернуть предложение `{"article": str, "spec_name": str, "spec_size": str, "category": str}` или None, если категория не определена.
  - `def accept_analog(row: dict, suggestion: dict, mapping: dict) -> dict` — собрать loaded-позицию: ключи как у `process_rows` (`article, name, size, unit, quantity, params, material, material_code, thickness, connection_0..3, system, comment`); `params` — из размеров (`parse_dimensions_from_text` по `f"{name} {size}"`, patterns из категории); raises `AnalogError("размер не распознан")`, если dims пуст. Дефолты совпадают с `run_pipeline`: `params={}` + стандартные `thickness`/`connection_*` через `default_connections(section, article)` из `process_specification_table`.

- [ ] **Step 1: Failing test `tests/test_web_ui_analogs.py`**

```python
MAPPING = load_mapping()  # реальный json проекта

def test_suggest_analog_known_category()  # "Клапан обратный АОС 100" → article, spec_name непустые
def test_suggest_analog_unknown_returns_none()
def test_accept_analog_builds_loaded_row()  # ключи loaded, quantity сохранён
def test_accept_analog_bad_size_raises()  # строка-мусор → AnalogError
```

- [ ] **Step 2: Прогнать — FAIL**

Run: `venv/bin/python -m pytest tests/test_web_ui_analogs.py -v`

- [ ] **Step 3: Реализовать `web_ui/analogs.py`**

Тонкая обёртка над `map_customer_equipment`: `detect_category` → (None → None) → `parse_dimensions_from_text` (для suggest достаточно непустых dims; для accept — обязательны) → `determine_section` → `select_article` → `build_spec_name/build_spec_size`. `class AnalogError(ValueError)`.

- [ ] **Step 4: PASS**

Run: `venv/bin/python -m pytest tests/test_web_ui_analogs.py -v`

- [ ] **Step 5: Commit**

```bash
git add web_ui/analogs.py tests/test_web_ui_analogs.py
git commit -m "feat(web_ui): подбор и конвертация аналогов для пропущенных строк"
```

---

### Task 3: Раннер обработки (runner.py, часть 1: PDF → позиции + дилерские цены)

**Files:**
- Create: `web_ui/runner.py`
- Test: `tests/test_web_ui_runner.py`

**Interfaces:**
- Consumes: `bitrix_bot.pipeline.process_pdf_to_positions(pdf_bytes) -> (success, skipped, meta)`; `price_search.ui.get_engine() -> AsyncPriceEngine`; `engine.search(items: list[dict]) -> list[SearchResult]` (`offers`, `min_price`, `best_offer.url/supplier`); `Store` из Task 1.
- Produces:
  - `async def run_processing(store: Store, request_id: int, file_name: str, pdf_bytes: bytes, price_engine: AsyncPriceEngine | None) -> None` — обновляет статусы: `processing` → `done` (+мета, позиции, дилерские цены) или `error` (при исключении, текст в `error`). Пустой `success` — не ошибка: статус `done`, meta.escalation сохраняется.
  - `def _attach_dealer_prices(trading: list[dict], results: list[SearchResult]) -> None` — в каждую trading-строку писать `price_info: {"price": str|None, "url": str|None, "supplier": str|None}`; пустые офферы → `price=None` («цена не найдена»).

- [ ] **Step 1: Failing test**

```python
def test_run_processing_saves_positions(monkeypatch, tmp_path)
    # monkeypatch web_ui.runner.process_pdf_to_positions → ([loaded...], [skipped...], {"recovered": 1})
    # price_engine-заглушка с search() → [SearchResult(offers=[PriceOffer(...)])]
def test_run_processing_empty_success_is_done_with_escalation()
def test_run_processing_exception_sets_error_status()
def test_trading_row_without_offers_has_none_price()
```

- [ ] **Step 2: Прогнать — FAIL**

Run: `venv/bin/python -m pytest tests/test_web_ui_runner.py -v`

- [ ] **Step 3: Реализовать `web_ui/runner.py` (часть 1)**

`process_pdf_to_positions` импортировать модуль-атрибутом (`from bitrix_bot import pipeline` и вызывать `pipeline.process_pdf_to_positions` — так тесты подменяют атрибут, как в tests бота). Дилерские цены: `asyncio.to_thread` не нужно — `engine.search` уже async; вызывать только для `kind='trading'` строк `{name, size}`; каждая позиция в try/except, при исключении — `price_info=None`.

- [ ] **Step 4: PASS**

Run: `venv/bin/python -m pytest tests/test_web_ui_runner.py -v`

- [ ] **Step 5: Commit**

```bash
git add web_ui/runner.py tests/test_web_ui_runner.py
git commit -m "feat(web_ui): раннер обработки PDF и дилерские цены"
```

---

### Task 4: Загрузка в 1С (runner.py, часть 2 + analog accept)

**Files:**
- Modify: `web_ui/runner.py`
- Modify: `web_ui/store.py` (ничего — `update_position` уже есть)
- Test: `tests/test_web_ui_runner.py` (дописать)

**Interfaces:**
- Consumes: `json_positions.build_positions(parsed_rows) -> list[dict]`; `bitrix_bot.pipeline.load_order_to_1c(positions, execute_url, order_comment, timeout, order_service, request_id)`; `pipeline._merge_prices(success, prices)`; `bitrix_bot.config.load_config() -> BotConfig` (execute_code_url, order_service_url/key/user/password, request_timeout).
- Produces:
  - `def load_to_1c(store: Store, request_id: int, header: dict, base_comment: str = "Web-заявка") -> dict` — собрать loaded-позиции из store → `build_positions` → `load_order_to_1c` → `_merge_prices` обратно в store через `update_position` → `set_order_number` → статус `loaded_1c`. Возвращает dict результата 1С (order_number, errors, warnings). При исключении — статус остаётся `done`, ошибка пишется в meta.
  - `def apply_analog(store: Store, request_id: int, index: int) -> dict` — skipped-строка №index → `analogs.suggest_analog` → `accept_analog` → строка переезжает из `skipped` в `loaded` (delete + insert), возвращает новую позицию; `AnalogError` → в строку пишется `"analog_error": "размер не распознан"`, kind не меняется.

- [ ] **Step 1: Failing test**

```python
def test_load_to_1c_merges_prices_and_sets_status(monkeypatch, tmp_path)
    # заглушка load_order_to_1c → {"order_number": "А-1", "errors": [], "warnings": [], "raw": "", "prices": [{"row":1,"article":"...","price":100.0}]}
    # после: status == "loaded_1c", order_number == "А-1", loaded[0]["price"] == 100.0
def test_load_to_1c_failure_keeps_done_and_records_error()
def test_apply_analog_moves_row_to_loaded(monkeypatch)
def test_apply_analog_bad_size_marks_row(monkeypatch)
def test_load_to_1c_refused_when_already_loaded(tmp_path)  # ValueError при status == "loaded_1c"
```

- [ ] **Step 2: Прогнать — FAIL**

Run: `venv/bin/python -m pytest tests/test_web_ui_runner.py -k 1c_or_analog -v`

- [ ] **Step 3: Реализовать в `web_ui/runner.py`**

`load_to_1c`: загрузка конфигурации через `load_config()`; `order_service` собирается из BotConfig, если `order_service_url` непустой, иначе None; `request_id` из заявки. Проверка статуса в начале. `apply_analog`: индексация — порядковый номер в списке `kind='skipped'`.

- [ ] **Step 4: PASS (все тесты runner)**

Run: `venv/bin/python -m pytest tests/test_web_ui_runner.py -v`

- [ ] **Step 5: Commit**

```bash
git add web_ui/ tests/test_web_ui_runner.py
git commit -m "feat(web_ui): загрузка заказа в 1С и применение аналогов"
```

---

### Task 5: FastAPI-приложение и маршруты (app.py)

**Files:**
- Create: `web_ui/app.py`
- Create: `web_ui/templates/base.html`, `index.html`, `request.html`
- Create: `web_ui/static/style.css`
- Test: `tests/test_web_ui_app.py`

**Interfaces:**
- Consumes: `Store`, `run_processing`, `load_to_1c`, `apply_analog` (Tasks 1–4).
- Produces:
  - `def create_app(db_path: str = "tmp/web_ui/web_ui.db") -> FastAPI` — Jinja2Templates(dir=`web_ui/templates`), StaticFiles mount `/static`, lifespan открывает/закрывает Store.
  - Routes: `GET /` (список заявок), `POST /upload` (multipart `file: UploadFile`; pdf_bytes → `create_request` → `asyncio.create_task(run_processing(...))` → редирект `303 /r/{id}`), `GET /r/{request_id}` (страница деталей), `GET /api/status/{request_id}` → JSON `{status, error}` для polling, `POST /r/{id}/load-1c` (form-поля header: numberDate, numberOrder, INN, customer, email, phone, contact → `save_header` + `load_to_1c` → редирект), `POST /r/{id}/analog/{index}` → `apply_analog` → редирект, `GET /r/{id}/xml` → Response(generate_order_xml(header, loaded), media_type="application/xml"), `GET /r/{id}/report.xlsx` → Response(build_excel_report(PipelineResult(...)), media_type=...). Заглушка price engine: если импорт `price_search.ui` падает — `price_engine=None`.

- [ ] **Step 1: Failing test `tests/test_web_ui_app.py`**

```python
def test_index_lists_requests(client)
def test_upload_creates_request_and_redirects(client, monkeypatch)  # run_processing подменена
def test_status_endpoint_returns_json(client)
def test_request_page_renders_sections(client)  # «Загружено», «Перекупное», «Пропущено»
def test_load_1c_endpoint_calls_runner(client, monkeypatch)
def test_xml_endpoint_returns_xml(client)
def test_error_status_shows_error_and_export_buttons(client)
```

TestClient: `from fastapi.testclient import TestClient`; фикстура `client(tmp_path)` → `create_app(db_path=str(tmp_path/"t.db"))`.

- [ ] **Step 2: Прогнать — FAIL**

Run: `venv/bin/python -m pytest tests/test_web_ui_app.py -v`

- [ ] **Step 3: Реализовать `web_ui/app.py` и шаблоны**

Шаблоны — серверный рендер по гайду: KPI-полоса числами, секции-таблицы, чипы статусов, тултипы через `title=`. Polling в `request.html`: `<script>` c `setInterval(fetch('/api/status/'+id), 2000)`, при смене статуса — `location.reload()`. Кнопка «Загрузить в 1С» неактивна при статусе `loaded_1c`. Форма реквизитов — поля header по умолчанию из `process_rows` (пустые строки).

- [ ] **Step 4: PASS**

Run: `venv/bin/python -m pytest tests/test_web_ui_app.py -v`

- [ ] **Step 5: Commit**

```bash
git add web_ui/ tests/test_web_ui_app.py
git commit -m "feat(web_ui): FastAPI-приложение, страницы и экспорт XML/xlsx"
```

---

### Task 6: Точка входа и запуск

**Files:**
- Modify: `web_ui/app.py` (`if __name__ == "__main__":` блок)
- Modify: `web_ui/__init__.py` (docstring пакета)
- Modify: `docs/superpowers/specs/2026-11-02-web-orders-dashboard-design.md` (статус «реализовано»)

**Interfaces:**
- Produces: `venv/bin/python -m web_ui.app` — uvicorn на 127.0.0.1:8092, `tmp/web_ui/` создаётся автоматически.

- [ ] **Step 1: Failing тест smoke**

```python
def test_main_module_starts_uvicorn(monkeypatch)  # monkeypatch uvicorn.run, вызвать runpy.run_module("web_ui.app", run_name="__main__")
```

- [ ] **Step 2: Прогнать — FAIL; Step 3: реализовать `main()` с `uvicorn.run(create_app(), host="127.0.0.1", port=8092)`; Step 4: PASS**

Run: `venv/bin/python -m pytest tests/test_web_ui_app.py -k main -v`

- [ ] **Step 5: Полный прогон всех тестов проекта web_ui + commit**

```bash
venv/bin/python -m pytest tests/test_web_ui_*.py -v
git add web_ui/ docs/
git commit -m "feat(web_ui): точка входа uvicorn"
```

---

### Task 7: Дизайн-QA (скриншот-проход)

**Files:**
- Modify: `web_ui/static/style.css`, шаблоны — по итогам QA.

- [ ] **Step 1: Запустить сервер, в браузере (kimi-webbridge) открыть `/` и `/r/{id}` на тестовых данных.**
- [ ] **Step 2: Проверить по чек-листу гайда: где глаз падает первым (KPI-полоса); нет ли предложений-подписей под числами; классированные цвета чипов; круглые числа в суммах (разделители тысяч); тёмные строки без тонировок.**
- [ ] **Step 3: Исправить найденное, переснять скриншоты, прогнать тесты.**
- [ ] **Step 4: Commit**

```bash
git add web_ui/
git commit -m "style(web_ui): QA-правки по дизайн-гайду дашбордов"
```

## Self-Review (пройден)

- Spec-покрытие: загрузка/просмотр/цены/аналоги/1С/XML/xlsx/форма реквизитов/авто-страницы — Tasks 1–7; аутентификация вне скоупа (spec).
- Типы согласованы: `Store`, `run_processing`, `load_to_1c`, `apply_analog`, `suggest_analog`/`accept_analog` используются в последующих задачах с теми же именами.
- Review Focus (5 пунктов) распределены по тестам Tasks 2–5.
