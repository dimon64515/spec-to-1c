# LLM-нормализация пропущенных строк + словарь аббревиатур — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Строки, отброшенные `process_rows` по причинам распознавания («Отсутствует размер» при unit≠м², «Не удалось распознать размер / тип»), получают второй шанс: LLM переписывает name/size канонически (один батч-вызов на документ), строка проходит тот же `process_rows` повторно; плюс паттерн «возд.» в детекторе типа.

**Architecture:** Новый модуль `llm_row_fallback.py` (квалификация skipped по белому списку причин → батч-нормализация через kimi-cli → якорная валидация (ядро имени, size из исходника, quantity/unit неизменны) → повторный process_rows → мердж с дедупом). Оркестратор `run_row_fallback` вызывается из `process_pdf_to_positions` и `web_app.py` сразу после `run_doc_fallback`, независимо от его триггера.

**Tech Stack:** Python 3.12, pytest, kimi-cli subprocess (через `llm_client`), PyYAML config.

**Spec:** docs/superpowers/specs/2026-10-28-llm-row-normalizer-design.md

## Global Constraints

- Количество и единица строки после нормализации — ИСХОДНЫЕ (LLM их не возвращает и не меняет).
- Белый список причин: `"Отсутствует размер"` (только unit ∉ {"м2","м²"}) и `"Не удалось распознать размер / тип"`. Всё остальное (м², покупка, blocked, геометрия, количество, LLM-отклонено) — не нормализуется.
- Якорь имени: ≥1 общий алфавитно-цифровой токен длины ≥3 (токены с цифрами считаются) между исходным и новым name (lower).
- size от LLM — только если подстрока исходного name/size (lower), иначе "".
- Гейтинг: `llm_size_classifier.llm_enabled() AND row_fallback.enabled` (cfg `llm.row_fallback`).
- Один LLM-вызов на документ; дедлайн по `time.monotonic()`, cfg timeout 180.
- Мердж: дедуп по `_merge_key` из llm_document_fallback (`(comment.lower().strip(), quantity)`), приоритет детерминированных, флаг `"llm_source": True`.
- Повторно отброшенные строки возвращаются в skipped с ИСХОДНОЙ причиной.
- Существующие 416 тестов остаются зелёными; полный прогон `pytest -q` в финале.
- В репозитории чужие незакоммиченные правки (bitrix_bot/events.py, tests/test_bitrix_server.py и др.) — коммить точечно, git add только свои файлы.

## Review Focus

1. **М²-строка с reason «Отсутствует размер»** (classify_skip проверяет размер раньше м²) — не должна нормализоваться: фильтр по unit. Пин: Task 2.
2. **LLM подменила имя полностью** (нет общего токена ≥3) — строка отброшена, не попадает в заказ. Пин: Task 2.
3. **Падение kimi (runner бросает, таймаут, невалидный JSON)** — skipped и success без изменений, пайплайн не падает. Пин: Task 3.
4. **Нормализация при НЕсработавшем doc-fallback** (низкий skip_ratio, 1-2 странные строки) — row_fallback работает независимо. Пин: Task 4.
5. **Бизнес-пропуски** (перекупка, blocked-артикулы) при включённом флаге — не нормализуются. Пин: Task 2.

---

### Task 1: Паттерн «возд.» в detect_product_type

**Files:**
- Modify: `process_specification_table.py` (`PRODUCT_TYPE_PATTERNS` — список пар (regex, ptype) над `detect_product_type`, :200-214)
- Test: `tests/test_llm_row_fallback.py` (новый)

**Interfaces:**
- Consumes: ничего.
- Produces: `detect_product_type("Возд.оц 0.5мм 150х150 1.25")` возвращает тот же ptype, что `detect_product_type("Воздуховод оцинкованный 150х150")`.

- [ ] **Step 1: Write the failing test**

```python
def test_detect_product_type_knows_vozd_abbreviation():
    import process_specification_table as pst
    full = pst.detect_product_type("Воздуховод оцинкованный 150х150 1.25")
    abbr = pst.detect_product_type("Возд.оц 0.5мм 150х150 1.25")
    assert abbr is not None and abbr == full
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_llm_row_fallback.py -v`
Expected: FAIL (`abbr is None`).

- [ ] **Step 3: Implement**

Добавить в `PRODUCT_TYPE_PATTERNS` паттерн для «возд.»/«возд» с границей слова (`\bвозд\.?\b`), маппящий на тот же ptype, что полное «воздуховод» (смотреть существующий паттерн воздуховода в списке и использовать его значение). Позиция в списке: после специальных случаев (фасонные агрегаты, зонты), рядом с прочими воздуховодными паттернами.

- [ ] **Step 4: Run test**

Run: `.venv/bin/pytest tests/test_llm_row_fallback.py tests/test_llm_size_classifier.py -q`
Expected: PASS (16+ тестов, регрессий нет).

- [ ] **Step 5: Commit**

```bash
git add process_specification_table.py tests/test_llm_row_fallback.py
git commit -m "feat: recognize 'Возд.' abbreviation as duct in detect_product_type"
```

---

### Task 2: Модуль `llm_row_fallback.py` — квалификация и валидация

**Files:**
- Create: `llm_row_fallback.py` (корень)
- Test: `tests/test_llm_row_fallback.py` (дополнить)

**Interfaces:**
- Consumes: `llm_client.run_kimi/assistant_content/extract_json_object`, `llm_size_classifier.llm_enabled`, `llm_size_classifier.get_llm_config`, `config.get_config` (по образцу `llm_document_fallback.get_doc_fallback_config`).
- Produces:
  - `QUALIFYING_REASON_PREFIXES = ("Отсутствует размер", "Не удалось распознать размер / тип")`
  - `M2_UNITS = ("м2", "м²")`
  - `get_row_fallback_config(cfg: Optional[Dict] = None) -> Dict` — секция `get_config()["llm"]["row_fallback"]`.
  - `row_fallback_enabled(cfg: Optional[Dict] = None) -> bool` — `llm_enabled(cfg) and bool(cfg.get("enabled"))` (llm_enabled импортировать как модуль `llm_size_classifier`, не from-import — паттерн моков в тестах).
  - `qualifying_skipped(skipped: List[Dict]) -> List[Dict]` — строки, чей `reason` начинается с одного из префиксов И (`unit` не в M2_UNITS для «Отсутствует размер»).
  - `validate_normalized(original: Dict, row: Dict) -> Optional[Dict]` — `{name, size, unit, quantity}`: unit/quantity из original; name — из row после проверки ядра (≥1 общий алфавитно-цифровой токен ≥3 символов, lower); size — из row только если подстрока original name/size (lower), иначе "". None — отброс.
  - `normalize_rows(rows: List[Dict], cfg=None, runner=None, deadline=None) -> List[Dict]` — один батч-вызов: промпт (вернуть JSON-массив `{name, size}` в том же порядке; правила: «Возд.»→«Воздуховод оцинкованный», размер дословно из исходного, не выдумывать) → run_kimi → `_loads`-парсинг (по образцу llm_document_fallback) → `validate_normalized` для каждой пары (original, llm_row); результат — валидные строки. Сбой/дедлайн/невалидный JSON → `[]`, retry до max_retries.

- [ ] **Step 1: Write the failing tests**

```python
def _skipped(name="Возд.оц 0.5мм 150х150 1.25", reason="Не удалось распознать размер / тип",
             unit="шт", quantity=21):
    return {"name": name, "size": "", "unit": unit, "quantity": quantity, "reason": reason}

def test_qualifying_skipped_whitelist():
    import llm_row_fallback as rf
    rows = [
        _skipped(),                                    # да
        _skipped(reason="Отсутствует размер"),          # да
        _skipped(reason="Отсутствует размер", unit="м²"),  # нет — м²
        _skipped(reason="Единица м² — агрегатная"),      # нет
        _skipped(reason="Покупная позиция (клапан)"),    # нет
        _skipped(reason="Не удалось распознать количество: -"),  # нет
    ]
    got = rf.qualifying_skipped(rows)
    assert len(got) == 2

def test_validate_normalized_keeps_qty_unit_and_core():
    import llm_row_fallback as rf
    orig = _skipped()
    got = rf.validate_normalized(orig, {"name": "Воздуховод оцинкованный 150х150", "size": "150х150"})
    assert got["quantity"] == 21 and got["unit"] == "шт"
    assert "150х150" in got["name"]

def test_validate_normalized_drops_full_rename():
    import llm_row_fallback as rf
    assert rf.validate_normalized(_skipped(), {"name": "Клапан приточный", "size": ""}) is None

def test_validate_normalized_drops_unconfirmed_size():
    import llm_row_fallback as rf
    got = rf.validate_normalized(_skipped(), {"name": "Воздуховод оцинкованный 150х150", "size": "ф315"})
    assert got is not None and got["size"] == ""  # размер не из исходника — обнулили, строка жива
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_llm_row_fallback.py -q`
Expected: FAIL (`ModuleNotFoundError: llm_row_fallback`).

- [ ] **Step 3: Implement `llm_row_fallback.py`**

Сигнатуры из блока Interfaces. Токенизация для проверки ядра: `re.findall(r"[a-zа-яё0-9]+", s.lower())` для обеих строк, пересечение токенов длины ≥3 — непустое. `normalize_rows` по образцу `parse_document_with_llm` в llm_document_fallback.py (deadline → remaining в копии cfg, retry, logger.warning события `llm_row_fallback.failed`).

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest tests/test_llm_row_fallback.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add llm_row_fallback.py tests/test_llm_row_fallback.py
git commit -m "feat: add llm_row_fallback module (qualify, validate, batch-normalize)"
```

---

### Task 3: Оркестратор `run_row_fallback`

**Files:**
- Modify: `llm_row_fallback.py`
- Test: `tests/test_llm_row_fallback.py` (дополнить)

**Interfaces:**
- Consumes: `qualifying_skipped`, `normalize_rows`, `row_fallback_enabled`, `get_row_fallback_config` (Task 2); `process_specification_table.process_rows(rows, run_llm_size_classify=False)`; `llm_document_fallback._merge_key`, `build_escalation_reason`, `_skip_ratio`.
- Produces:
  - `run_row_fallback(success: List[dict], skipped: List[dict], cfg: Optional[Dict] = None) -> Tuple[List[dict], List[dict], Dict[str, Any]]` — дедлайн `time.monotonic() + timeout` из cfg; если нет квалифицируемых строк или не `row_fallback_enabled` → `(success, skipped, {"row_fallback_triggered": False, "row_fallback_recovered": 0})`; иначе `normalize_rows` → `process_rows(..., run_llm_size_classify=False)` → мердж (дедуп через `_merge_key`, приоритет детерминированных, `"llm_source": True`) → meta `{"row_fallback_triggered": True, "row_fallback_recovered": N}`; любой сбой → исходные списки + `triggered: True, recovered: 0`.
  - ВАЖНО: пересчёт `skip_ratio_post` и `escalation` — НЕ здесь. meta оркестратора несёт только свои ключи; финальные значения считает Task 4.

- [ ] **Step 1: Write the failing tests**

```python
def test_run_row_fallback_recovers_vozd_row(monkeypatch, tmp_path):
    import llm_row_fallback as rf
    import process_specification_table as pst
    monkeypatch.setattr(rf, "row_fallback_enabled", lambda cfg=None: True)
    monkeypatch.setattr(rf, "normalize_rows",
                        lambda rows, **kw: [{"name": "Воздуховод оцинкованный 150х150",
                                             "size": "150х150", "unit": r["unit"],
                                             "quantity": r["quantity"]} for r in rows])
    ok = [{"article": "1-1-1", "quantity": 1, "comment": "«Другое» «ф100»"}]
    success, skipped, meta = rf.run_row_fallback(ok, [_skipped()] * 4)
    assert meta["row_fallback_triggered"] is True and meta["row_fallback_recovered"] >= 1
    assert any(s.get("llm_source") for s in success)
    # детерминированная строка не продублирована и не потеряна
    assert any(s.get("comment") == "«Другое» «ф100»" and not s.get("llm_source") for s in success)

def test_run_row_fallback_noop_when_disabled(monkeypatch):
    import llm_row_fallback as rf
    monkeypatch.setattr(rf, "row_fallback_enabled", lambda cfg=None: False)
    ok = [{"article": "1-1-1"}]
    bad = [_skipped()]
    success, skipped, meta = rf.run_row_fallback(ok, bad)
    assert success is ok and skipped is bad and meta["row_fallback_triggered"] is False

def test_run_row_fallback_runner_failure_returns_deterministic(monkeypatch):
    import llm_row_fallback as rf
    monkeypatch.setattr(rf, "row_fallback_enabled", lambda cfg=None: True)
    def boom(rows, **kw):
        raise RuntimeError("kimi down")
    monkeypatch.setattr(rf, "normalize_rows", boom)
    ok = [{"article": "1-1-1"}]
    success, skipped, meta = rf.run_row_fallback(ok, [_skipped()])
    assert success is ok and meta["row_fallback_recovered"] == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_llm_row_fallback.py -k run_row_fallback -q`
Expected: FAIL (`AttributeError: module 'llm_row_fallback' has no attribute 'run_row_fallback'`).

- [ ] **Step 3: Implement `run_row_fallback`**

По Interfaces. `process_rows` вызывать позиционно/keyword как `pst.process_rows(norm_rows, run_llm_size_classify=False)` (импорт модуля `process_specification_table` внутри функции — как в llm_document_fallback, чтобы не тянуть цикл). Логи: `llm_row_fallback.triggered / .recovered / .failed`.

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest tests/test_llm_row_fallback.py -q`
Expected: PASS (все тесты файла).

- [ ] **Step 5: Commit**

```bash
git add llm_row_fallback.py tests/test_llm_row_fallback.py
git commit -m "feat: run_row_fallback orchestrator with merge into pipeline rows"
```

---

### Task 4: Интеграция в pipeline + web_app + partial warning

**Files:**
- Modify: `bitrix_bot/pipeline.py` (`process_pdf_to_positions` ~:92-97, `run_pipeline` partial warning ~:266-272, `PipelineResult`)
- Modify: `web_app.py` (~:365-390, область вызова `run_doc_fallback`)
- Modify: `config.yaml` (секция `llm`)
- Test: `tests/test_llm_row_fallback.py` (дополнить), `tests/test_bitrix_pipeline.py` (дополнить)

**Interfaces:**
- Consumes: `run_row_fallback` (Task 3) — meta ключи `row_fallback_triggered`, `row_fallback_recovered`; `run_doc_fallback` meta (`triggered`, `recovered`, `skip_ratio_post`, `escalation`).
- Produces:
  - `process_pdf_to_positions` после вызова `run_doc_fallback` вызывает `run_row_fallback(success, skipped)` и возвращает meta, дополненную ключами row-fallback; ПОРЯДОК: `process_rows → run_doc_fallback → run_row_fallback → _filter_blocked_articles` (обновить pipeline-order test: теперь три вызова до фильтра).
  - `PipelineResult` — новое поле `row_fallback_recovered: int = 0` *(моя поправка к спеке «новых полей не требуется»: число нужно в отчёте, транспорт — только через PipelineResult)*.
  - `run_pipeline`: `partial_warning` ставится если `(meta.get("triggered") or meta.get("row_fallback_triggered")) and skip_ratio_post > threshold`; `skip_ratio_post` пересчитывается ПОСЛЕ row-fallback как `len(skipped)/(len(skipped)+len(success))` на финальных списках; `escalation` — если success пуст: сначала doc-meta эскалация, иначе `build_escalation_reason(skipped)`; `llm_recovered = doc recovered`, `row_fallback_recovered` — из meta.
  - `web_app.py`: после `run_doc_fallback` — `run_row_fallback`; `recovered_total = meta.get("recovered", 0) + meta.get("row_fallback_recovered", 0)` — на него завязаны st.info и `_regenerate_xml_if_llm_recovered`.
  - config.yaml: подсекция `row_fallback` под `llm`: `enabled: true`, `timeout: 180`, `max_retries: 1`.

- [ ] **Step 1: Write the failing tests**

```python
def test_config_has_row_fallback_section():
    import config
    df = config.get_config().get("llm", {}).get("row_fallback", {})
    assert df.get("enabled") is True
    assert df.get("timeout", 0) > 0

def test_run_pipeline_partial_warning_via_row_fallback_only(monkeypatch):
    # doc-fallback не триггерился, но row-fallback сработал и skip_ratio высокий
    import bitrix_bot.pipeline as pl
    meta = {"triggered": False, "recovered": 0, "skip_ratio": 0.9, "skip_ratio_post": 0.9,
            "escalation": None, "row_fallback_triggered": True, "row_fallback_recovered": 1}
    monkeypatch.setattr(
        pl, "process_pdf_to_positions",
        lambda b: ([{"article": "1-1-1", "quantity": 1}], [{"name": "x", "reason": "r"}] * 9, meta))
    monkeypatch.setattr(pl, "load_order_to_1c", lambda *a, **k: ("1", [], []))
    res = pl.run_pipeline(b"pdf", "f.pdf", "c", "http://x")
    assert res.row_fallback_recovered == 1
    assert res.partial_warning and "частично" in res.partial_warning
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_llm_row_fallback.py::test_config_has_row_fallback_section tests/test_llm_row_fallback.py::test_run_pipeline_partial_warning_via_row_fallback_only -q`
Expected: FAIL (секции нет / `AttributeError: row_fallback_recovered`).

- [ ] **Step 3: Implement**

config.yaml — добавить секцию. `process_pdf_to_positions` — добавить вызов и прокидывание meta (внимание: чужие незакоммиченные правки в pipeline.py — только свои строки). `run_pipeline` — пересчёт skip_ratio_post/escalation/partial_warning по финальным спискам. web_app — вызов + recovered_total. Обновить существующий pipeline-order test до порядка `["fallback", "row_fallback", "filter"]` (импорт/патч llm_row_fallback.run_row_fallback).

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest tests/test_llm_row_fallback.py tests/test_bitrix_pipeline.py tests/test_llm_document_fallback.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bitrix_bot/pipeline.py web_app.py config.yaml tests/
git commit -m "feat: wire row fallback into pipeline, web_app and partial warning"
```

---

### Task 5: Отчёт — раздельные цифры восстановления

**Files:**
- Modify: `bitrix_bot/report.py` (текстовая сводка), `bitrix_bot/report_xlsx.py` (при необходимости — только если сводка дублируется там)
- Test: `tests/test_bitrix_report.py` (дополнить)

**Interfaces:**
- Consumes: `PipelineResult.llm_recovered` (doc-fallback), `PipelineResult.row_fallback_recovered` (Task 4).
- Produces: текстовая сводка содержит строки «Восстановлено LLM (документ): N» (заменяет текущее «Восстановлено LLM: N») и «Нормализовано LLM (строки): M»; секция «Восстановлено LLM (проверить)» — без изменений (единый флаг llm_source).

- [ ] **Step 1: Write the failing test**

```python
def test_report_summary_shows_split_llm_counts():
    import bitrix_bot.pipeline as pl
    import bitrix_bot.report as rep
    res = pl.PipelineResult(file_name="f.pdf", loaded=[{"article": "1-1-1", "llm_source": True}],
                            skipped=[], llm_recovered=18, row_fallback_recovered=12)
    text = rep.build_report(res)
    assert "Восстановлено LLM (документ): 18" in text
    assert "Нормализовано LLM (строки): 12" in text
```

(Имя функции сборки текстового отчёта сверить по report.py; если сводка строится в server.py — тест и правку делать там.)

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_bitrix_report.py::test_report_summary_shows_split_llm_counts -q`
Expected: FAIL (строки нет).

- [ ] **Step 3: Implement**

Обновить формат сводки в месте сборки (report.py / server.py — по факту), сохранив остальные строки сводки.

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest tests/test_bitrix_report.py tests/test_bitrix_server.py -q`
Expected: PASS (существующие тесты сводки адаптировать, если матчат старый формат).

- [ ] **Step 5: Commit**

```bash
git add bitrix_bot/report.py tests/test_bitrix_report.py
git commit -m "feat: split doc/row LLM recovery counts in report summary"
```

---

### Task 6: Интеграционные тесты и полный прогон

**Files:**
- Test: `tests/test_llm_row_fallback.py` (дополнить)

**Interfaces:**
- Consumes: всё предыдущее.
- Produces: пины Review Focus + регрессия.

- [ ] **Step 1: Write the failing tests**

```python
def test_order_independence_row_fallback_without_doc_trigger(monkeypatch, tmp_path):
    # Review Focus #4: doc-fallback не триггерится (success есть, ratio низкий),
    # но qualifying skipped нормализуется
    import llm_row_fallback as rf
    import process_specification_table as pst
    monkeypatch.setattr(rf, "row_fallback_enabled", lambda cfg=None: True)
    monkeypatch.setattr(rf, "normalize_rows",
                        lambda rows, **kw: [{"name": "Воздуховод оцинкованный 150х150",
                                             "size": "150х150", "unit": r["unit"],
                                             "quantity": r["quantity"]} for r in rows])
    ok = [{"article": "1-1-1", "quantity": 5, "comment": "«Воздуховод» «ф100»"}]
    success, skipped, meta = rf.run_row_fallback(ok, [_skipped()])
    assert meta["row_fallback_recovered"] == 1

def test_business_skips_untouched_even_when_enabled(monkeypatch):
    # Review Focus #5
    import llm_row_fallback as rf
    monkeypatch.setattr(rf, "row_fallback_enabled", lambda cfg=None: True)
    called = []
    monkeypatch.setattr(rf, "normalize_rows", lambda rows, **kw: called.append(rows) or [])
    ok = [{"article": "1-1-1"}]
    business = [_skipped(reason="Покупная позиция (клапан)"),
                _skipped(reason="Артикул 20-1 не входит в разрешённый список")]
    rf.run_row_fallback(ok, business)
    assert called == []  # нормализатор не вызывался — квалифицируемых строк нет
```

- [ ] **Step 2-4: Реализовать (обычно зелёные сразу — они фиксируют уже сделанное), прогнать полный сьют**

Run: `.venv/bin/pytest -q`
Expected: все зелёные (416 + новые).

- [ ] **Step 5: Commit**

```bash
git add tests/test_llm_row_fallback.py
git commit -m "test: row fallback integration and review-focus pins"
```

---

## После реализации (ручная проверка, не код)

1. Рестарт `bitrix-bot` (сервис читает код при старте).
2. Отправить «Заявку на НИИ Кукурузы» в чат: ожидается ~30 из 74, сводка с раздельными цифрами, «Возд.»-строки в «Загружено».
3. Проверить, что перекупные 9 остались в «Пропущено».
