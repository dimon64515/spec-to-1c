# LLM-fallback на уровне документа + автоэскалация — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** При полном промахе или деградации парсера PDF бот запускает LLM-разбор сырого текста документа, мержит восстановленные строки через существующий `process_rows`, а при неудаче эскалирует менеджеру с понятной причиной.

**Architecture:** Новый модуль `llm_document_fallback.py` (триггер по skip-ratio → сырой текст PDF → kimi-cli → JSON-строки со spans → валидация → мердж). Общий kimi-клиент выносится в `llm_client.py`. Гейтинг: `llm.enabled AND llm.doc_fallback.enabled`. Эскалация через `im.message.add` (bot_id=None). Спек: `docs/superpowers/specs/2026-10-28-llm-document-fallback-design.md`.

**Tech Stack:** Python 3.12, pytest, PyMuPDF (текстовый слой), kimi-cli subprocess, stdlib only для новых модулей.

**Spec:** docs/superpowers/specs/2026-10-28-llm-document-fallback-design.md

## Global Constraints

- Бэкенд LLM — kimi-cli subprocess; runner инжектируется в тестах (`runner(prompt, cfg) -> stdout`).
- Числа из LLM-ответа извлекаются только детерминированно из spans исходного текста (anti-hallucination).
- `timeout: 180` — дедлайн на ВСЮ операцию fallback по `time.monotonic()`.
- Гейтинг: fallback активен только при `llm.enabled: true` И `llm.doc_fallback.enabled: true`; дефолт в config.yaml — оба `false`, выкатка кода не меняет поведение.
- Blocked-артикулы (20-1/20-2/20-9, константа `BLOCKED_1C_ARTICLES`) исключены из skip_ratio; мердж происходит ДО `_filter_blocked_articles`.
- Любой сбой fallback → возврат к детерминированному результату, пайплайн не падает.
- Fallback только на PDF-путях (бот + web_app); CLI `process_csv` и `recreate_order_from_report` не трогаем.
- Логирование: `logger = logging.getLogger(__name__)`, события `llm_doc_fallback.triggered/recovered/failed/escalated`.
- Полный прогон тестов в финале: `pytest` — все зелёные (сейчас 385).

## Review Focus

1. **PDF без текстового слоя вообще** — `extract_document_text` должен дать LLM OCR-текст, а не пустую строку (иначе бесполезный вызов kimi).
2. **LLM вернул spans вне текста / quantity 0 / мусор** — строки отбрасываются валидацией, не попадают в заказ.
3. **Дубликаты** между детерминированным результатом и LLM-строками — дедуп с приоритетом детерминированного.
4. **kimi-cli упал / таймаут / невалидный JSON** — заказ уходит с детерминированным результатом (или эскалация), без исключений.
5. **Строки с `llm_source: True` не ломают загрузку в 1С** — `json_positions.build_positions` читает только известные ключи, лишний ключ не должен мешать (закрепить тестом).
6. **Документ из одних blocked-артикулов** — skip_ratio считается до фильтра, fallback не запускается впустую.
7. **Частичная деградация после мерджа** — менеджер получает предупреждение «загружено частично».

---

### Task 1: Общий LLM-клиент `llm_client.py`

**Files:**
- Create: `llm_client.py` (корень проекта)
- Modify: `llm_size_classifier.py:88-115` (вынос `_run_kimi`, `_assistant_content`, `_extract_json_object`)
- Test: `tests/test_llm_client.py` (новый)

**Interfaces:**
- Consumes: ничего (stdlib: json, subprocess, shutil, logging).
- Produces:
  - `llm_client.run_kimi(prompt: str, cfg: Dict) -> str` — subprocess kimi-cli, timeout из `cfg["timeout"]`.
  - `llm_client.assistant_content(stdout: str) -> str` — извлечение content из stream-json.
  - `llm_client.extract_json_object(text: str) -> Tuple[Any, int]` — `json.JSONDecoder().raw_decode`.
  - Обратная совместимость: в `llm_size_classifier.py` остаются `_run_kimi = llm_client.run_kimi` и приватные алиасы `_assistant_content`/`_extract_json_object` (тесты и `classify_sizes_batch` продолжают работать).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_llm_client.py
from llm_client import assistant_content, extract_json_object

def test_assistant_content_finds_role():
    stdout = '{"role":"assistant","content":"{\\"a\\":1}"}\n{"role":"system","content":"x"}'
    assert '"a"' in assistant_content(stdout)

def test_extract_json_object_stops_at_trailing_text():
    obj, end = extract_json_object('{"rows":[]} garbage tail')
    assert obj == {"rows": []}
    assert end == 10
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_llm_client.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'llm_client'`

- [ ] **Step 3: Create `llm_client.py` and перенести код**

Создать `llm_client.py`: перенести тела `_run_kimi` (:88-98), `_assistant_content` (:101), `_extract_json_object` (:115) из `llm_size_classifier.py` без изменений логики, убрав подчёркивания из публичных имён. В `llm_size_classifier.py` заменить тела на импорт-алиасы:

```python
from llm_client import run_kimi as _run_kimi, assistant_content as _assistant_content, extract_json_object as _extract_json_object
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_llm_client.py tests/test_llm_size_classifier.py tests/test_llm_fallback_integration.py -v`
Expected: PASS (рефакторинг не меняет поведение)

- [ ] **Step 5: Commit**

```bash
git add llm_client.py llm_size_classifier.py tests/test_llm_client.py
git commit -m "refactor: extract shared kimi-cli client into llm_client module"
```

---

### Task 2: Модуль `llm_document_fallback.py` — извлечение текста и LLM-парсинг

**Files:**
- Create: `llm_document_fallback.py` (корень)
- Test: `tests/test_llm_document_fallback.py` (новый)

**Interfaces:**
- Consumes: `llm_client.run_kimi` (Task 1), `llm_size_classifier.llm_enabled` (гейтинг), `pdf_spec_extractor.extract_text_lines_from_pdf` (:70) и `extract_ocr_spec_rows` (:681).
- Produces:
  - `LlmDocRow = Dict[str, Any]` — `{"name": str, "size": str, "unit": str, "quantity": int}` (входной формат для `process_rows`).
  - `get_doc_fallback_config(cfg: Optional[Dict] = None) -> Dict` — секция `get_config()["llm"]["doc_fallback"]` (по образцу `llm_size_classifier.get_llm_config`, :52).
  - `doc_fallback_enabled(cfg: Optional[Dict] = None) -> bool` — `llm_enabled(cfg) and bool(df_cfg.get("enabled"))`.
  - `extract_document_text(pdf_path: str, max_chars: int = 60000) -> str` — текстовый слой постранично; если суммарно < 200 символов — OCR через `extract_ocr_spec_rows`, строки сериализуются как `"name size unit quantity"`; обрезка до `max_chars`.
  - `parse_document_with_llm(text: str, cfg: Optional[Dict] = None, runner: Optional[Callable] = None, deadline: Optional[float] = None) -> List[LlmDocRow]` — промпт → `run_kimi` (или runner) → stream-json → JSON-массив `{name, name_span, size, size_span, unit, quantity, quantity_span}`; spans — пары `[start, end]` в `text`; значения берутся детерминированно: `name = text[a:b]`, `size = text[c:d]`, `quantity = int(text[e:f])`; строки с spans вне границ / quantity <= 0 / unit не в `("шт", "м", "м2", "м²")` отбрасываются с `logger.warning`. Истёкший deadline → `[]`.
  - `build_escalation_reason(skipped: List[Dict]) -> str` — агрегирует `reason` skipped-строк: если все содержат «м²» → «документ — смета в м² без поканальных размеров»; если все начинаются с «Покупная позиция» → «все позиции — перекупное оборудование»; если текста не было — «нет текстового слоя»; иначе — «не распознано N строк, основная причина: <самая частая>».

- [ ] **Step 1: Write the failing test**

```python
# tests/test_llm_document_fallback.py
TEXT = ("1 Воздуховод прямоугольный 600x400 12 м\n"
        "2 Отвод 90 ф125 6 шт\n")

def _stdout(rows_json: str) -> str:
    import json as j
    return j.dumps({"role": "assistant", "content": rows_json})

def _rows_with_spans():
    rows = [
        {"name": TEXT[2:29], "name_span": [2, 29], "size": TEXT[30:37],
         "size_span": [30, 37], "unit": "м", "quantity": 12,
         "quantity_span": [38, 40]},
        {"name": TEXT[41:49], "name_span": [41, 49], "size": TEXT[50:54],
         "size_span": [50, 54], "unit": "шт", "quantity": 6,
         "quantity_span": [55, 56]},
    ]
    import json as j
    return j.dumps(rows, ensure_ascii=False)

def test_parse_document_with_llm_extracts_rows_from_spans():
    import llm_document_fallback as f
    got = f.parse_document_with_llm(TEXT, runner=lambda p, c: _stdout(_rows_with_spans()))
    assert len(got) == 2
    assert got[0]["name"] == "Воздуховод прямоугольный"
    assert got[0]["quantity"] == 12 and got[0]["unit"] == "м"
    assert got[1]["size"] == "ф125"

def test_parse_document_with_llm_drops_invalid_spans():
    import llm_document_fallback as f
    bad = [{"name": "x", "name_span": [0, 9999], "size": "", "size_span": [0, 0],
            "unit": "шт", "quantity": 1, "quantity_span": [0, 1]}]
    import json as j
    got = f.parse_document_with_llm(TEXT, runner=lambda p, c: _stdout(j.dumps(bad)))
    assert got == []

def test_doc_fallback_enabled_requires_both_flags(monkeypatch):
    import llm_document_fallback as f
    import llm_size_classifier as lsc
    monkeypatch.setattr(lsc, "llm_enabled", lambda cfg=None: False)
    assert f.doc_fallback_enabled() is False

def test_build_escalation_reason_detects_m2():
    import llm_document_fallback as f
    reason = f.build_escalation_reason(
        [{"reason": "Единица м² — агрегатная площадь"}, {"reason": "Единица м² — агрегатная площадя"}])
    assert "м²" in reason

def test_parse_document_with_llm_returns_empty_on_runner_error():
    import llm_document_fallback as f
    def boom(prompt, cfg):
        raise RuntimeError("kimi-cli crashed")
    assert f.parse_document_with_llm(TEXT, runner=boom) == []

```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_llm_document_fallback.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement `llm_document_fallback.py`**

Сигнатуры из блока Interfaces. Промпт: просьба извлечь из спецификации строки оборудования и вернуть строго JSON-массив с полями и spans. Дедлайн: `time.monotonic()` в начале, проверка перед вызовом runner и после; остаток времени передаётся в cfg копию как `cfg["timeout"]`. При невалидном JSON — до `max_retries` попыток, затем `[]` + `logger.warning("llm_doc_fallback.failed")`.

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_llm_document_fallback.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add llm_document_fallback.py tests/test_llm_document_fallback.py
git commit -m "feat: add llm_document_fallback module (text extraction, span-validated LLM parsing)"
```

---

### Task 3: Флаг `run_llm_size_classify` в `process_rows`

**Files:**
- Modify: `process_specification_table.py:1582` (`process_rows`), `:1255` (`parse_row`), `:1613-1624` (LLM-блок)
- Test: `tests/test_llm_document_fallback.py` (дополнить)

**Interfaces:**
- Consumes: ничего нового.
- Produces: `process_rows(rows, header=None, defaults=None, *, run_llm_size_classify: bool = True)` — при `False` блок `:1613-1624` пропускается (не вызывается `classify_sizes_batch`). Все существующие вызовы (`pipeline.py:88`, `web_app.py:340`, `process_csv`) не меняются.

- [ ] **Step 1: Write the failing test**

```python
def test_process_rows_skips_llm_when_flag_false(monkeypatch):
    import process_specification_table as pst
    import llm_size_classifier as lsc
    called = []
    monkeypatch.setattr(lsc, "llm_enabled", lambda cfg=None: True)
    monkeypatch.setattr(lsc, "classify_sizes_batch",
                        lambda strings, cfg=None, runner=None: called.append(strings) or {})
    rows = [{"name": "Воздуховод", "size": "ф125", "unit": "шт", "quantity": 1}]
    pst.process_rows(rows, run_llm_size_classify=False)
    assert called == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_llm_document_fallback.py::test_process_rows_skips_llm_when_flag_false -v`
Expected: FAIL — `TypeError: process_rows() got an unexpected keyword argument 'run_llm_size_classify'`

- [ ] **Step 3: Implement**

Добавить keyword-only параметр в `process_rows` и прокинуть в `parse_row` (новый keyword-only параметр, по умолчанию `True`); в блоке `:1613` условие `if run_llm_size_classify and lsc.llm_enabled():`.

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_llm_document_fallback.py tests/test_llm_fallback_integration.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add process_specification_table.py tests/test_llm_document_fallback.py
git commit -m "feat: add run_llm_size_classify flag to process_rows"
```

---

### Task 4: Триггер и мердж в `process_pdf_to_positions`

**Files:**
- Modify: `bitrix_bot/pipeline.py:70-89` (`process_pdf_to_positions`), `:57-67` (`PipelineResult`)
- Modify: `llm_document_fallback.py` — добавить `run_doc_fallback`
- Test: `tests/test_llm_document_fallback.py` (дополнить), `tests/test_bitrix_pipeline.py:140` (обновить мок)

**Interfaces:**
- Consumes: `parse_document_with_llm`, `extract_document_text`, `build_escalation_reason`, `doc_fallback_enabled` (Task 2); `process_rows(..., run_llm_size_classify=False)` (Task 3).
- Produces:
  - `run_doc_fallback(pdf_path: str, success: List[dict], skipped: List[dict], cfg: Optional[Dict] = None) -> Tuple[List[dict], List[dict], Dict[str, Any]]` — оркестратор: `skip_ratio = skipped/(skipped+success)` (None при 0 знаменателе); триггер `success == 0 or skip_ratio > threshold`; если не триггер или не `doc_fallback_enabled` → `(success, skipped, {"triggered": False, "recovered": 0, "skip_ratio": skip_ratio, "escalation": None})`; иначе `extract_document_text` → `parse_document_with_llm` → `process_rows(llm_rows, run_llm_size_classify=False)` → мердж: дедуп по ключу `(comment.lower().strip(), quantity)` (приоритет детерминированных), LLM-строкам в success добавить `"llm_source": True`; вернуть `(merged_success, merged_skipped, meta)`, где `meta["escalation"] = build_escalation_reason(merged_skipped) if not merged_success else None`; в meta также `skip_ratio_post` — skip_ratio после мерджа (для partial warning).
  - `process_pdf_to_positions(pdf_bytes: bytes) -> Tuple[List[dict], List[dict], Dict[str, Any]]` — после `process_rows` (:88) вызывает `run_doc_fallback(temp_path, success, skipped)`, затем `_filter_blocked_articles` (мердж ДО фильтра), возвращает 3-кортеж с meta.

- [ ] **Step 1: Write the failing test**

```python
def test_run_doc_fallback_merges_and_flags(monkeypatch, tmp_path):
    import llm_document_fallback as f
    monkeypatch.setattr(f, "doc_fallback_enabled", lambda cfg=None: True)
    monkeypatch.setattr(f, "extract_document_text", lambda p, max_chars=60000: "txt")
    monkeypatch.setattr(f, "parse_document_with_llm",
                        lambda text, **kw: [{"name": "Воздуховод", "size": "ф125",
                                             "unit": "шт", "quantity": 2}])
    import process_specification_table as pst
    orig = pst.process_rows
    monkeypatch.setattr(pst, "process_rows",
                        lambda rows, **kw: orig(rows, run_llm_size_classify=False))
    pdf = tmp_path / "f.pdf"; pdf.write_bytes(b"%PDF-1.4")
    success, skipped, meta = f.run_doc_fallback(str(pdf), [], [{"name": "x", "reason": "bad"}])
    assert meta["triggered"] is True and meta["recovered"] == 1
    assert success[0]["llm_source"] is True
    assert all(r.get("llm_source") for r in success if meta["recovered"])

def test_run_doc_fallback_no_trigger_when_healthy(monkeypatch, tmp_path):
    import llm_document_fallback as f
    monkeypatch.setattr(f, "doc_fallback_enabled", lambda cfg=None: True)
    pdf = tmp_path / "f.pdf"; pdf.write_bytes(b"%PDF-1.4")
    ok = [{"article": "1-1-1", "quantity": 1}]
    success, skipped, meta = f.run_doc_fallback(str(pdf), ok, [{"name": "x", "reason": "bad"}])
    assert success is ok and meta["triggered"] is False

def test_run_doc_fallback_dedups_against_deterministic(monkeypatch, tmp_path):
    import llm_document_fallback as f
    monkeypatch.setattr(f, "doc_fallback_enabled", lambda cfg=None: True)
    monkeypatch.setattr(f, "extract_document_text", lambda p, max_chars=60000: "txt")
    monkeypatch.setattr(f, "parse_document_with_llm",
                        lambda text, **kw: [{"name": "Воздуховод", "size": "ф125",
                                             "unit": "шт", "quantity": 1}])
    import process_specification_table as pst
    orig = pst.process_rows
    monkeypatch.setattr(pst, "process_rows",
                        lambda rows, **kw: orig(rows, run_llm_size_classify=False))
    pdf = tmp_path / "f.pdf"; pdf.write_bytes(b"%PDF-1.4")
    det = [{"article": "1-1-1", "quantity": 1, "comment": "«Воздуховод» «ф125»"}]
    success, skipped, meta = f.run_doc_fallback(str(pdf), det, [{"name": "x", "reason": "bad"}] * 5)
    # LLM-строка совпала с детерминированной по (comment, quantity) — дублей нет
    assert meta["recovered"] == 0
    assert len([s for s in success if s.get("comment", "").lower().strip() == det[0]["comment"]]) == 1

def test_pipeline_order_fallback_before_blocked_filter(monkeypatch):
    # Review Focus #6: skip_ratio и fallback считаются ДО _filter_blocked_articles,
    # поэтому blocked-строки (20-1/20-2/20-9) не раздувают skip_ratio.
    import bitrix_bot.pipeline as pl
    import pandas as pd
    order = []
    df = pd.DataFrame([{"name": "Клапан 20-1", "size": "ф125", "unit": "шт", "quantity": 1}])
    monkeypatch.setattr(pl, "load_tables_from_pdf", lambda b, **kw: {"tables": {1: [df]}})
    meta = {"triggered": False, "recovered": 0, "skip_ratio": 0.0,
            "skip_ratio_post": 0.0, "escalation": None}
    monkeypatch.setattr("llm_document_fallback.run_doc_fallback",
                        lambda path, s, sk: (order.append("fallback"), (s, sk, meta))[1])
    orig_filter = pl._filter_blocked_articles
    monkeypatch.setattr(pl, "_filter_blocked_articles",
                        lambda s, sk: (order.append("filter"), orig_filter(s, sk))[1])
    pl.process_pdf_to_positions(b"%PDF-fake")
    assert order == ["fallback", "filter"]

```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_llm_document_fallback.py -k run_doc_fallback -v`
Expected: FAIL — `AttributeError: module 'llm_document_fallback' has no attribute 'run_doc_fallback'`

- [ ] **Step 3: Implement `run_doc_fallback` и обновить `process_pdf_to_positions`**

Порог: `get_doc_fallback_config().get("skip_threshold", 0.3)`. Лог-события: `logger.info("llm_doc_fallback.triggered")`, `...recovered` (с recovered=N), `...failed`, `...escalated`.

В `process_pdf_to_positions` (:70-89): принять 3-кортеж от `run_doc_fallback` и вернуть его. Обновить существующие моки: `tests/test_bitrix_pipeline.py:140` — `lambda b: ([], [{"name": "x", "reason": "bad"}], {"triggered": False, "recovered": 0, "skip_ratio": 1.0, "escalation": None})` (grep по проекту на `process_pdf_to_positions` в тестах — все моки 2-кортежа обновить).

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_llm_document_fallback.py tests/test_bitrix_pipeline.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add llm_document_fallback.py bitrix_bot/pipeline.py tests/
git commit -m "feat: wire document-level LLM fallback into pdf pipeline with merge"
```

---

### Task 5: Эскалация, частичное предупреждение и транспорт уведомлений

**Files:**
- Modify: `bitrix_bot/pipeline.py:206-238` (`run_pipeline`), `:57-67` (`PipelineResult`)
- Modify: `bitrix_bot/server.py:127-139` (`notify_failure`), хендлер отправки отчёта (~:92-100)
- Modify: `bitrix_bot/report.py` и `report_xlsx.py` — секция «Восстановлено LLM»
- Test: `tests/test_bitrix_pipeline.py` (дополнить), `tests/test_bitrix_server.py`

**Interfaces:**
- Consumes: meta от `process_pdf_to_positions` (Task 4): `skip_ratio`, `recovered`, `escalation`.
- Produces:
  - `PipelineResult` — новые поля: `escalation: Optional[str] = None`, `partial_warning: Optional[str] = None`, `llm_recovered: int = 0`.
  - В `run_pipeline`: `partial_warning` ставится, если `success` не пуст, а post-merge skip_ratio (из meta: `skipped/(skipped+success)` после мерджа — передать в meta полем `skip_ratio_post`) выше порога; текст: «Заказ загружен частично: распознано X из Y строк, проверьте отчёт».
  - В `server.py`: при `res.escalation` — `client.send_message(job.dialog_id, text, bot_id=None)` (fallback на `im.message.add` внутри `send_message`, `bitrix_client.py:37-47`); при `res.partial_warning` — аналогично. `notify_failure` — тоже `bot_id=None` (чинит APP_ID_ERROR для сообщений о сбоях).
  - Отчёт: строки success с `llm_source: True` выводятся секцией «Восстановлено LLM (проверить)» в текстовом отчёте и отдельным листом/блоком в xlsx.

- [ ] **Step 1: Write the failing test**

```python
def test_run_pipeline_sets_escalation_when_empty_after_fallback(monkeypatch):
    import bitrix_bot.pipeline as pl
    meta = {"triggered": True, "recovered": 0, "skip_ratio": 1.0, "skip_ratio_post": 1.0,
            "escalation": "документ — смета в м² без поканальных размеров"}
    monkeypatch.setattr(pl, "process_pdf_to_positions", lambda b: ([], [{"name": "x", "reason": "r"}], meta))
    res = pl.run_pipeline(b"pdf", "f.pdf", "c", "http://x")
    assert "м²" in res.escalation
    assert res.loaded == []

def test_run_pipeline_partial_warning(monkeypatch):
    import bitrix_bot.pipeline as pl
    meta = {"triggered": True, "recovered": 1, "skip_ratio": 0.9, "skip_ratio_post": 0.9,
            "escalation": None}
    monkeypatch.setattr(pl, "process_pdf_to_positions",
                        lambda b: ([{"article": "1-1-1", "quantity": 1}],
                                   [{"name": "x", "reason": "r"}] * 9, meta))
    monkeypatch.setattr(pl, "load_order_to_1c", lambda *a, **k: ("1", [], []))
    res = pl.run_pipeline(b"pdf", "f.pdf", "c", "http://x")
    assert res.partial_warning and "частично" in res.partial_warning
    assert res.llm_recovered == 1

def test_llm_source_flag_does_not_break_positions():
    # Review Focus #5: лишний ключ llm_source не мешает сборке JSON для 1С
    import json_positions as jp
    row = {"article": "1-1-1", "params": {"D0": 125}, "quantity": 2,
           "material_code": "1", "thickness": 0.6, "system": "", "comment": "c",
           "llm_source": True}
    positions = jp.build_positions([row])
    assert positions and all("llm_source" not in p for p in positions)

```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_bitrix_pipeline.py -k "escalation or partial" -v`
Expected: FAIL (AttributeError на новых полях / meta не используется)

- [ ] **Step 3: Implement**

Добавить поля в `PipelineResult`; в `run_pipeline` распаковать 3-кортеж от `process_pdf_to_positions`, заполнить `escalation`, `llm_recovered`, `partial_warning` (порог из `get_doc_fallback_config().get("skip_threshold", 0.3)`). В `server.py`: после отправки отчёта — эскалация/предупреждение через `send_message(..., bot_id=None)`; `notify_failure` → `bot_id=None`. В `report.py`/`report_xlsx.py`: секция «Восстановлено LLM» по флагу `llm_source`.

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_bitrix_pipeline.py tests/test_bitrix_server.py tests/test_bitrix_report.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add bitrix_bot/pipeline.py bitrix_bot/server.py bitrix_bot/report.py bitrix_bot/report_xlsx.py tests/
git commit -m "feat: escalate unparseable docs and warn on partial load via im.message.add"
```

---

### Task 6: web_app интеграция и конфиг

**Files:**
- Modify: `web_app.py:340` (область вызова `process_rows`)
- Modify: `config.yaml:42-49` (секция `llm`)
- Test: `tests/test_llm_document_fallback.py` (дополнить)

**Interfaces:**
- Consumes: `run_doc_fallback` (Task 4).
- Produces: в web_app после `process_rows(rows, header=header)` — если PDF-путь (есть `file_bytes`): записать байты во временный файл, вызвать `run_doc_fallback`, показать в UI счётчики «Восстановлено LLM: N» и текст эскалации при полном промахе.

- [ ] **Step 1: Write the failing test**

```python
def test_config_has_doc_fallback_section():
    import config
    cfg = config.get_config()
    df = cfg.get("llm", {}).get("doc_fallback", {})
    assert df.get("enabled") is False
    assert 0 < df.get("skip_threshold", 0) <= 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_llm_document_fallback.py::test_config_has_doc_fallback_section -v`
Expected: FAIL — `doc_fallback` отсутствует.

- [ ] **Step 3: Implement config и web_app**

В `config.yaml` добавить подсекцию `doc_fallback` под `llm`:
```yaml
  doc_fallback:
    enabled: false
    skip_threshold: 0.3
    max_chars: 60000
    timeout: 180
    max_retries: 1
```
`llm.enabled` оставить `false`. В `web_app.py` после :340 добавить вызов `run_doc_fallback` (temp-файл через `tempfile.NamedTemporaryFile(suffix=".pdf")`) и вывод `st.info`/`st.warning` с результатом.

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_llm_document_fallback.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add config.yaml web_app.py tests/test_llm_document_fallback.py
git commit -m "feat: enable doc fallback config section and wire into web app"
```

---

### Task 7: Интеграционные тесты на сегодняшних сбоях

**Files:**
- Create: `tests/fixtures/fallback_nii_kukuruzy.pdf` (копия `tmp/bitrix_bot/159.pdf`)
- Create: `tests/fixtures/fallback_525_smety.pdf` (копия `tmp/bitrix_bot/160.pdf`)
- Test: `tests/test_doc_fallback_integration.py` (новый)

**Interfaces:**
- Consumes: всё предыдущее. Фикстуры — реальные PDF из сегодняшних инцидентов.
- Produces: пины поведения на двух классах сбоев.

- [ ] **Step 1: Скопировать фикстуры**

```bash
cp tmp/bitrix_bot/159.pdf tests/fixtures/fallback_nii_kukuruzy.pdf
cp tmp/bitrix_bot/160.pdf tests/fixtures/fallback_525_smety.pdf
```

- [ ] **Step 2: Write the failing test**

```python
# tests/test_doc_fallback_integration.py
FIX = "tests/fixtures"

def test_159_recovers_rows_with_llm(monkeypatch):
    import llm_document_fallback as f
    import process_specification_table as pst
    monkeypatch.setattr(f, "doc_fallback_enabled", lambda cfg=None: True)
    text = f.extract_document_text(f"{FIX}/fallback_nii_kukuruzy.pdf")
    assert len(text) > 500  # Review Focus #1: текст извлёкся
    def fake_parse(text, **kw):
        # spans строим на реальном тексте: берём подстроки документа
        import json as j
        rows = []
        for name_sub, size_sub, qty in [("Возд.оц 0.6мм ф125", "ф125", 3),
                                        ("Возд.оц 0.6мм ф160", "ф160", 5)]:
            i = text.find(name_sub)
            if i >= 0:
                rows.append({"name": name_sub, "name_span": [i, i + len(name_sub)],
                             "size": size_sub, "size_span": [i + text[i:].find(size_sub),
                                                             i + text[i:].find(size_sub) + len(size_sub)],
                             "unit": "шт", "quantity": qty,
                             "quantity_span": [0, 1]})
        return rows
    monkeypatch.setattr(f, "parse_document_with_llm", fake_parse)
    import bitrix_bot.pipeline as pl
    success, skipped, meta = pl.process_pdf_to_positions(open(f"{FIX}/fallback_nii_kukuruzy.pdf", "rb").read())
    assert len(success) > 0, "LLM-fallback должен спасти хотя бы часть строк заявки 159"
    assert meta["recovered"] > 0

def test_160_escalates_m2_smeta(monkeypatch):
    import llm_document_fallback as f
    monkeypatch.setattr(f, "doc_fallback_enabled", lambda cfg=None: True)
    monkeypatch.setattr(f, "parse_document_with_llm", lambda text, **kw: [])
    import bitrix_bot.pipeline as pl
    success, skipped, meta = pl.process_pdf_to_positions(open(f"{FIX}/fallback_525_smety.pdf", "rb").read())
    assert success == []
    assert meta["escalation"] and "м²" in meta["escalation"]

def test_159_no_text_layer_path():
    import llm_document_fallback as f
    text = f.extract_document_text(f"{FIX}/fallback_nii_kukuruzy.pdf")
    assert isinstance(text, str)
```

- [ ] **Step 3: Run tests — они должны пройти**

Run: `pytest tests/test_doc_fallback_integration.py -v`
Expected: PASS. Если test_159 падает (LLM-строки отбрасываются process_rows — например, причина «Не удалось распознать количество»), отладить: причина должна быть понятна из skipped-reason; допустимо подкорректировать `fake_parse`-данные, но НЕ ослаблять валидацию.

- [ ] **Step 4: Полный прогон**

Run: `pytest -q`
Expected: все тесты зелёные (база — 385).

- [ ] **Step 5: Commit**

```bash
git add tests/fixtures/fallback_nii_kukuruzy.pdf tests/fixtures/fallback_525_smety.pdf tests/test_doc_fallback_integration.py
git commit -m "test: integration tests for today's failed documents (159, 160)"
```

---

### Task 8 (отдельная задача): расширение ADOPTABLE_CLASSES для фасонки

**Files:**
- Modify: `llm_size_classifier.py:34` (`ADOPTABLE_CLASSES`)
- Modify: `process_specification_table.py:1474-1479` (усыновление)
- Test: `tests/test_llm_fallback_integration.py` (дополнить)

**Interfaces:**
- Consumes: `SizeClassification` (Task 1 не менял).
- Produces: `ADOPTABLE_CLASSES` включает `tee_axbxc`, `reducer_pair`; усыновление для фасонного пути с валидацией через `try_parse_fitting`.

- [ ] **Step 1: Write the failing test**

В `tests/test_llm_fallback_integration.py` (по образцу существующих тестов файла — мок `lsc.llm_enabled`/`classify_sizes_batch` через monkeypatch, как в `tests/test_llm_fallback_integration.py:73-94`):

```python
def test_tee_size_adopted_for_fitting(monkeypatch):
    import llm_size_classifier as lsc
    import process_specification_table as pst
    from llm_size_classifier import SizeClassification

    def fake_batch(strings, cfg=None, runner=None):
        return {s: SizeClassification(status="ok", format_class="tee_axbxc",
                                      dims={"A0": 300, "B0": 200, "C0": 150},
                                      section="rectangular", adoptable=True,
                                      raw_response="", detail="") for s in strings}
    monkeypatch.setattr(lsc, "llm_enabled", lambda cfg=None: True)
    monkeypatch.setattr(lsc, "classify_sizes_batch", fake_batch)
    rows = [{"name": "Тройник", "size": "300x200-150", "unit": "шт", "quantity": 1}]
    xml, skipped, success = pst.process_rows(rows)
    assert success, "тройник с LLM-классом tee_axbxc не должен уходить в skipped"
    assert not any(lsc.REASON_LLM_REJECTED in (r.get("reason") or "") for r in skipped)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_llm_fallback_integration.py::test_tee_size_adopted_for_fitting -v`
Expected: FAIL — success пуст, skipped содержит `REASON_LLM_REJECTED`.

- [ ] **Step 3: Implement**

Добавить `"tee_axbxc"` и `"reducer_pair"` в `ADOPTABLE_CLASSES` (`llm_size_classifier.py:34`). В точке усыновления (`process_specification_table.py:1474-1479`) для этих классов дополнительно прогонять `try_parse_fitting` (:697) с подставленными dims — усыновлять только если фасонный парсер подтверждает строку.

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_llm_fallback_integration.py tests/test_llm_size_classifier.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add llm_size_classifier.py process_specification_table.py tests/test_llm_fallback_integration.py
git commit -m "feat: adopt LLM tee/reducer size classes for fittings"
```

---

## Порядок выкатки (не код)

1. Выкатить код (конфиги выключены — поведение не меняется).
2. На проде: `llm.enabled: true`, затем `llm.doc_fallback.enabled: true`.
3. Проверить на копиях `tmp/bitrix_bot/159.pdf` / `160.pdf`, что эскалация доходит в чат (транспорт `im.message.add`).
