# Цены из 1С в xlsx-ответе битрикс-бота — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** В xlsx-отчёте бота (листы «Загружено» и «Восстановлено LLM») появляются колонки «ЦенаЕд» и «Сумма», заполненные ценами, которые 1С уже считает при загрузке заказа.

**Architecture:** Цены не запрашиваются отдельно — они уже считаются штатной обработкой `асВводНовогоЗаказа` при загрузке. Проблема только в доставке до xlsx: в HTTP-транспорте построчных данных нет вовсе, в execute_code-транспорте они есть в хвосте ответа после ` ## `, но `parse_1c_result` его отбрасывает. План: добавить массив `Строки` в ответ HTTP-сервиса 1С, распарсить оба источника в единый ключ `prices` результата, смержить по номеру строки в `PipelineResult.loaded`, добавить две колонки в xlsx и предупреждение в сводку.

**Tech Stack:** Python 3.12, openpyxl, pytest; 1С BSL (HTTP-сервис).

**Spec:** `docs/superpowers/specs/2026-11-01-xlsx-prices-design.md`

## Global Constraints

- Новые колонки xlsx — строго В КОНЕЦ `LOADED_HEADERS` (после «Комментарий»): `parse_edited_report` читает фиксированные индексы r[0]–r[12] и не должен меняться.
- Контракт результата транспорта 1С остаётся расширением `parse_1c_result`: `{"order_number", "errors", "warnings", "raw", "prices"}` — старый код, читающий старые ключи, не ломается.
- `prices` — список dict `{"row": int, "article": str, "price": float, "area": float, "qty": float}` (оба транспорта приводятся к одной форме; `price_type` сознательно не прокидываем — не выбран в дизайне).
- Цена 0 в ответе 1С → в xlsx пустая ячейка (не 0), позиция считается «без цены».
- Формат хвоста execute_code-транспорта (фиксирован, см. build_execute_payload.py): сегменты после `" ## "` вида `N|артикул|t=…|мат=…|шина0=…|с0=…|тип=…|цена=<float>|S=<float>|n=<float>`.
- Соответствие строк: `success[i]` (пайплайн) ↔ позиции `build_positions(success)` ↔ строка `ДокОбъект.Товары[i+1]` (1С `НомерСтроки`). Мерж по `row == i+1`.
- BSL-файл `tools/as_order_loader/http_service_newzakaz.bsl` — правки только в нём; синхронная правка ТЗ `docs/ТЗ_HTTP_СЕРВИС_ЗАГРУЗКА_ЗАКАЗА_1С.md` не требуется (расширение обратносовместимое), но упомянуть в коммите.

## Review Focus

- Ответ HTTP-сервиса по ранее созданному документу (идемпотентность, request_id повтор): в ответе нет `Строки` → `prices` должен быть пустым списком, бот не падает, xlsx без цен. Пин: тест Task 2.
- execute_code-транспорт: хвост ` ## ` содержит русские ключи и десятичные числа с точкой; сегмент может быть битым/коротким → такие сегменты пропускаются, не роняют парсинг. Пин: тест Task 3.
- Количество нецелое (n=2.5): сумма = цена × qty, округление до 2 знаков. Пин: тест Task 4.
- Round-trip: `parse_edited_report` читает старые 13 колонок из файла с 15 колонками (лишние игнорируются по индексам) и старого файла с 13 колонками. Пин: тест Task 5.
- Позиции, не доехавшие до 1С (фильтр BLOCKED_1C_ARTICLES / ошибки «не найден продукт»): у строки нет цены → пустая ячейка, предупреждение считается по факту. Пин: тест Task 4.

---

### Task 1: Массив `Строки` в ответе HTTP-сервиса 1С

**Files:**
- Modify: `tools/as_order_loader/http_service_newzakaz.bsl`

**Interfaces:**
- Consumes: после `РассчитатьВсеСтроки(ДокОбъект, Результат)` каждая строка `ДокОбъект.Товары` имеет `ЦенаЕд`, `S`, `n`, `ТипЦеныПоПрайсу`, `АртикулПродукции`.
- Produces: JSON-ответ дополнен ключом `Строки`: массив `{"Артикул", "ЦенаЕд", "S", "Количество"}`. Пустой массив на всех ранних выходах (ошибки, идемпотентность).

- [ ] **Step 1: Расширить структуру результата**

В `NewZakazPost` (bsl:48) изменить:
```bsl
Результат = Новый Структура("Успех, НомерЗаказа, Ошибки, Предупреждения, Строки",
	Ложь, "", Новый Массив, Новый Массив, Новый Массив);
```
Ранние возвраты (403, 500, 400, идемпотентность в `ЗагрузитьЗаказ` bsl:174-183) менять не нужно — `Строки` уже инициализирован пустым.

- [ ] **Step 2: Заполнить `Строки` после расчёта**

В конец `ЗагрузитьЗаказ` после `Результат.НомерЗаказа = ДокОбъект.Номер;` (bsl:218):
```bsl
Для Каждого Стр Из ДокОбъект.Товары Цикл
	Результат.Строки.Добавить(Новый Структура("Артикул, ЦенаЕд, S, Количество",
		Стр.АртикулПродукции, Стр.ЦенаЕд, Стр.S, Стр.n));
КонецЦикла;
```

- [ ] **Step 3: Проверка синтаксиса глазами + коммит**

BSL-компилятора в репо нет; проверка — повторное чтение изменённого фрагмента (структура с 5 ключами, заполнение после расчёта). В заголовок файла добавить строку истории:
`//   01.11.2026 — в ответ добавлен массив Строки (Артикул, ЦенаЕд, S, Количество) для цен в отчёте бота.`

```bash
git add tools/as_order_loader/http_service_newzakaz.bsl
git commit -m "feat(1c): add Строки (prices) array to NewZakaz HTTP response"
```

---

### Task 2: `prices` в `HttpServiceTransport._to_result`

**Files:**
- Modify: `order_client.py:161-178`
- Test: `tests/test_bitrix_client.py` (дополнить; файл существует)

**Interfaces:**
- Consumes: Task 1 — `data["Строки"]` либо отсутствует, либо список dict с ключами `Артикул`, `ЦенаЕд`, `S`, `Количество`.
- Produces: `result["prices"]` — `List[dict]` вида `{"row": int, "article": str, "price": float, "area": float, "qty": float}`; row — 1-based номер по порядку в массиве.

- [ ] **Step 1: Написать падающий тест**

В `tests/test_bitrix_client.py`:
```python
def test_to_result_includes_prices():
    data = {"Успех": True, "НомерЗаказа": "851",
            "Ошибки": [], "Предупреждения": [],
            "Строки": [{"Артикул": "1-1", "ЦенаЕд": 1250.5, "S": 3.2, "Количество": 2},
                       {"Артикул": "2-1", "ЦенаЕд": 0, "S": 0, "Количество": 1}]}
    from order_client import HttpServiceTransport
    res = HttpServiceTransport._to_result(data, "{}")
    assert res["prices"][0] == {"row": 1, "article": "1-1", "price": 1250.5,
                                "area": 3.2, "qty": 2}
    assert res["prices"][1]["price"] == 0

def test_to_result_prices_missing_key_empty():
    from order_client import HttpServiceTransport
    res = HttpServiceTransport._to_result({"Успех": True}, "{}")
    assert res["prices"] == []
```
(Имена фикстур/стиля взять из существующих тестов файла.)

- [ ] **Step 2: Прогнать — должен упасть**

Run: `venv/bin/python -m pytest tests/test_bitrix_client.py -k prices -v` (или `pytest`, как в проекте)
Expected: FAIL — KeyError/AssertionError, ключа `prices` нет.

- [ ] **Step 3: Реализовать маппинг в `_to_result`**

В `_to_result` после `out["raw"] = raw` добавить:
```python
prices = []
for i, row in enumerate(data.get("Строки") or [], start=1):
    try:
        prices.append({
            "row": i,
            "article": str(row.get("Артикул", "")),
            "price": float(row.get("ЦенаЕд") or 0),
            "area": float(row.get("S") or 0),
            "qty": float(row.get("Количество") or 0),
        })
    except (TypeError, ValueError):
        continue
out["prices"] = prices
```

- [ ] **Step 4: Прогнать — должен пройти**

Run: `pytest tests/test_bitrix_client.py -v`
Expected: PASS (включая старые тесты файла).

- [ ] **Step 5: Коммит**

```bash
git add order_client.py tests/test_bitrix_client.py
git commit -m "feat: propagate per-row prices from 1С HTTP service response"
```

---

### Task 3: `prices` в `parse_1c_result` (execute_code-транспорт, обе копии)

**Files:**
- Modify: `order_client.py:24-53` и `bitrix_bot/pipeline.py:131-160` (дубли одной функции — править обе)
- Test: `tests/test_bitrix_pipeline.py` (дополнить)

**Interfaces:**
- Consumes: формат хвоста ответа 1С: сегменты после `" ## "` вида `N|артикул|t=…|…|цена=<float>|S=<float>|n=<float>` (см. Global Constraints).
- Produces: `parse_1c_result(text)["prices"]` — та же форма, что в Task 2.

- [ ] **Step 1: Написать падающий тест**

В `tests/test_bitrix_pipeline.py`:
```python
def test_parse_1c_result_extracts_prices_from_tail():
    from bitrix_bot.pipeline import parse_1c_result
    text = ("ЗАКАЗ 851 | строк=2 | ошибок=0 | предупр=0 ## "
            "1|1-1|t=0.8|мат=Оцинкованная|шина0=|с0=|тип=Розница|цена=1250.5|S=3.2|n=2 ## "
            "2|2-1|t=0.8|мат=Оцинкованная|шина0=|с0=|тип=|цена=0|S=0|n=1")
    res = parse_1c_result(text)
    assert res["prices"][0] == {"row": 1, "article": "1-1", "price": 1250.5,
                                "area": 3.2, "qty": 2}
    assert res["prices"][1]["price"] == 0

def test_parse_1c_result_no_tail_prices_empty():
    from bitrix_bot.pipeline import parse_1c_result
    assert parse_1c_result("ЗАКАЗ 851 | строк=0 | ошибок=0 | предупр=0")["prices"] == []

def test_parse_1c_result_skips_malformed_tail_segment():
    from bitrix_bot.pipeline import parse_1c_result
    res = parse_1c_result("ЗАКАЗ 851 | строк=1 ## битый-сегмент ## "
                          "1|1-1|цена=10|S=1|n=1")
    assert len(res["prices"]) == 1 and res["prices"][0]["price"] == 10
```

- [ ] **Step 2: Прогнать — должен упасть**

Run: `pytest tests/test_bitrix_pipeline.py -k prices -v`
Expected: FAIL — ключа `prices` нет.

- [ ] **Step 3: Реализовать парсинг хвоста в обеих копиях**

В каждой копии `parse_1c_result` добавить helper и ключ в возвращаемый dict:
```python
def _parse_price_tail(text: str) -> List[Dict[str, Any]]:
    parts = text.split(" ## ")[1:]
    prices = []
    for part in parts:
        seg = part.strip()
        m = re.match(r"^(\d+)\|([^|]*)\|", seg)
        if not m:
            continue
        fields = dict(re.findall(r"(цена|S|n)=([^|]*)", seg))
        try:
            prices.append({
                "row": int(m.group(1)),
                "article": m.group(2),
                "price": float(fields.get("цена", "0") or 0),
                "area": float(fields.get("S", "0") or 0),
                "qty": float(fields.get("n", "0") or 0),
            })
        except ValueError:
            continue
    return prices
```
и в return-добавить `"prices": _parse_price_tail(text)`. В `bitrix_bot/pipeline.py` `List`/`Dict`/`Any` уже импортированы; в `order_client.py` — тоже.

- [ ] **Step 4: Прогнать — должен пройти**

Run: `pytest tests/test_bitrix_pipeline.py tests/test_bitrix_client.py -v`
Expected: PASS.

- [ ] **Step 5: Коммит**

```bash
git add order_client.py bitrix_bot/pipeline.py tests/test_bitrix_pipeline.py
git commit -m "feat: parse per-row prices from 1С execute_code result tail"
```

---

### Task 4: Мерж цен в `PipelineResult.loaded` и предупреждение

**Files:**
- Modify: `bitrix_bot/pipeline.py` (`run_pipeline` ~292-325, `recreate_order_from_report` ~369-385)
- Test: `tests/test_bitrix_pipeline.py`

**Interfaces:**
- Consumes: `out["prices"]` из Task 2/3 (может отсутствовать в tuple-стабах).
- Produces: каждый dict в `PipelineResult.loaded` дополнен ключами `"price": float` и `"sum": float` (округление 2 знака; при цене 0 → `None` и `None`); `PipelineResult.warnings_1c` дополнена строкой `"N позиций без цены из 1С"` при N > 0.

- [ ] **Step 1: Написать падающий тест**

```python
def test_run_pipeline_merges_prices_and_warns_on_zero():
    # стаб: process_pdf_to_positions → success с 2 строками;
    # load_order_to_1c (монкипатч bitrix_bot.pipeline.load_order_to_1c)
    # возвращает {"order_number": "851", "errors": [], "warnings": [],
    #             "raw": "", "prices": [
    #                 {"row": 1, "article": "1-1", "price": 1250.5, "area": 3.2, "qty": 2},
    #                 {"row": 2, "article": "2-1", "price": 0, "area": 0, "qty": 1}]}
    res = run_pipeline(b"%pdf", "f.pdf", "комментарий", "http://x/api/execute_code")
    assert res.loaded[0]["price"] == 1250.5
    assert res.loaded[0]["sum"] == 2501.0
    assert res.loaded[1]["price"] is None and res.loaded[1]["sum"] is None
    assert any("без цены" in w for w in res.warnings_1c)

def test_run_pipeline_no_prices_key_no_crash():
    # load_order_to_1c возвращает dict без "prices"
    res = run_pipeline(b"%pdf", "f.pdf", "комментарий", "http://x/api/execute_code")
    assert all("price" not in p or p["price"] is None for p in res.loaded)
```
(Стиль стабирования скопировать из существующих тестов `test_bitrix_pipeline.py` — там уже есть приёмы монкипатча `process_pdf_to_positions`/`load_order_to_1c`.)

- [ ] **Step 2: Прогнать — должен упасть**

Run: `pytest tests/test_bitrix_pipeline.py -k prices -v`
Expected: FAIL — ключей `price` нет.

- [ ] **Step 3: Реализовать мерж**

В `bitrix_bot/pipeline.py` добавить:
```python
def _merge_prices(success: List[Dict[str, Any]], prices: List[Dict[str, Any]]) -> None:
    """Смержить построчные цены из ответа 1С в loaded по НомерСтроки (row == i+1)."""
    by_row = {int(p.get("row") or 0): p for p in prices or []}
    for i, row in enumerate(success, start=1):
        info = by_row.get(i)
        price = float(info["price"]) if info else None
        if price == 0:
            price = None
        row["price"] = price
        qty = row.get("quantity")
        row["sum"] = round(price * qty, 2) if price is not None and qty else None
    n_no_price = sum(1 for row in success if row["price"] is None)
    if n_no_price:
        warnings_proxy.append(f"{n_no_price} позиций без цены из 1С")
```
(`warnings_proxy` — вынести подпись на вызывающего: helper возвращает `n_no_price`, вызывающий добавляет в warnings_1c. Оба места — `run_pipeline` после мержа `out`, и `recreate_order_from_report` — вызывают helper и добавляют предупреждение в список warnings, который уходит в PipelineResult. В обеих функциях извлечь `prices = out.get("prices") or []` — корректно и для tuple-стабов (у них out.get вернёт дефолт, если out tuple — добавить `if isinstance(out, dict)`-охрану.)

- [ ] **Step 4: Прогнать — должен пройти**

Run: `pytest tests/test_bitrix_pipeline.py -v`
Expected: PASS.

- [ ] **Step 5: Коммит**

```bash
git add bitrix_bot/pipeline.py tests/test_bitrix_pipeline.py
git commit -m "feat: merge 1С per-row prices into pipeline result with no-price warning"
```

---

### Task 5: Колонки «ЦенаЕд», «Сумма» в xlsx-отчёте

**Files:**
- Modify: `report_xlsx.py:47-49, 91-99`
- Test: `tests/test_report_xlsx.py`

**Interfaces:**
- Consumes: `PipelineResult.loaded[i]["price"]`, `["sum"]` из Task 4.
- Produces: `LOADED_HEADERS` = 15 колонок, последние — «ЦенаЕд», «Сумма»; `parse_edited_report` НЕ меняется и работает на обеих версиях файла.

- [ ] **Step 1: Написать падающий тест**

В `tests/test_report_xlsx.py`:
```python
def test_excel_report_has_price_columns():
    res = PipelineResult(file_name="f.pdf", order_number="851",
                         loaded=[{"article": "1-1", "params": {"A0": 100, "B0": 50},
                                  "quantity": 2, "material_code": "1", "thickness": 0.8,
                                  "connection_0": "", "connection_1": "",
                                  "connection_2": "", "connection_3": "",
                                  "system": "", "comment": "", "price": 1250.5, "sum": 2501.0},
                                 {"article": "2-1", "params": {"A0": 100, "B0": 50},
                                  "quantity": 1, "material_code": "1", "thickness": 0.8,
                                  "connection_0": "", "connection_1": "",
                                  "connection_2": "", "connection_3": "",
                                  "system": "", "comment": "", "price": None, "sum": None}])
    data = build_excel_report(res)
    ws = load_workbook(io.BytesIO(data))["Загружено"]
    headers = [c.value for c in ws[1]]
    assert headers[-2:] == ["ЦенаЕд", "Сумма"]
    assert ws.cell(row=2, column=14).value == 1250.5
    assert ws.cell(row=2, column=15).value == 2501.0
    assert ws.cell(row=3, column=14).value is None  # цена 0 → пусто

def test_parse_edited_report_ignores_price_columns():
    # файл с 15 колонками разбирается; r[13], r[14] не читаются
    ...  # собрать через build_excel_report, затем parse_edited_report → loaded_rows[0]["article"] == "1-1"
```

- [ ] **Step 2: Прогнать — должен упасть**

Run: `pytest tests/test_report_xlsx.py -k price -v`
Expected: FAIL — заголовки не совпадают.

- [ ] **Step 3: Реализовать**

В `report_xlsx.py`:
```python
LOADED_HEADERS = ["Артикул", "A", "B", "D", "Кол-во", "Материал", "Толщина",
                  "Соед. 0", "Соед. 1", "Соед. 2", "Соед. 3",
                  "Система", "Комментарий", "ЦенаЕд", "Сумма"]
```
В `_loaded_row` в конец списка добавить `p.get("price"), p.get("sum")`. `parse_edited_report` не трогать.

- [ ] **Step 4: Прогнать — должен пройти**

Run: `pytest tests/test_report_xlsx.py -v`
Expected: PASS (включая старые round-trip тесты).

- [ ] **Step 5: Коммит**

```bash
git add report_xlsx.py tests/test_report_xlsx.py
git commit -m "feat: add ЦенаЕд/Сумма columns to loaded sheets of xlsx report"
```

---

### Task 6: Строка о позициях без цены в сводке сообщения

**Files:**
- Modify: `bitrix_bot/report.py:98-114`
- Test: `tests/test_bitrix_report.py` (дополнить; файл существует)

**Interfaces:**
- Consumes: `res.loaded[i]["price"] is None` из Task 4; предупреждение уже есть в `res.warnings_1c` (Task 4), значит дублировать логику подсчёта не нужно — сводка просто показывает.
- Produces: в `build_summary` строка `"⚠ Без цены: N"` при N > 0.

- [ ] **Step 1: Написать падающий тест**

```python
def test_summary_mentions_positions_without_price():
    res = PipelineResult(file_name="f.pdf", loaded=[{"price": 10.0}, {"price": None}])
    assert "Без цены: 1" in build_summary(res)
```

- [ ] **Step 2: Прогнать — должен упасть**

Run: `pytest tests/test_bitrix_report.py -k price -v`
Expected: FAIL.

- [ ] **Step 3: Реализовать**

В `build_summary` после блока `if n_llm:` добавить:
```python
n_no_price = sum(1 for p in res.loaded if p.get("price") is None)
if n_no_price:
    lines.append(f"⚠ Без цены: {n_no_price}")
```

- [ ] **Step 4: Прогнать + полный прогон тестов**

Run: `pytest tests/test_bitrix_report.py -v && pytest tests/ -q`
Expected: PASS весь suite.

- [ ] **Step 5: Коммит**

```bash
git add bitrix_bot/report.py tests/test_bitrix_report.py
git commit -m "feat: mention no-price positions in bitrix summary"
```

---

### Task 7: Обновить дизайн-документ и финальная проверка

**Files:**
- Modify: `docs/superpowers/specs/2026-11-01-xlsx-prices-design.md` (отметить реализацию: ключ `prices`, точные имена)

- [ ] **Step 1: Привести дизайн в соответствие с кодом** (имена ключей `price`/`sum`/«ЦенаЕд»/«Сумма», форма `prices`-элементов).

- [ ] **Step 2: Финальный прогон**

Run: `pytest tests/ -q`
Expected: PASS.

- [ ] **Step 3: Коммит**

```bash
git add docs/superpowers/specs/2026-11-01-xlsx-prices-design.md
git commit -m "docs: mark xlsx-prices design as implemented"
```
