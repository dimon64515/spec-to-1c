# Row-fallback v2: все skipped + починка количества — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Row-нормализатор обрабатывает ВСЕ skipped (кроме м²-агрегатов), LLM чинит и количество, и единицу (с якорной проверкой по тексту документа); восстановленные LLM-строки выводят своих сломанных детерминированных двойников из «Пропущено».

**Architecture:** Blacklist-квалификация вместо whitelist; протокол {name, size, unit, quantity} с валидацией quantity окном цифр после имени в тексте PDF (re-use `_name_positions`/`_quantity_matches` из llm_document_fallback); `run_row_fallback` принимает pdf_path и чистит двойников по совпадению lower-name + причина «Не удалось распознать количество».

**Tech Stack:** Python 3.12, pytest, kimi-cli subprocess (llm_client).

**Spec:** docs/superpowers/specs/2026-10-29-row-fallback-v2-design.md

## Global Constraints

- quantity от LLM: целое > 0 и обязано встречаться среди `\d+`-токенов в 30 символах после вхождения name в текст документа.
- unit ∈ {шт, м, м2, м²}.
- Blacklist: reason начинается с «Единица м²» ИЛИ unit ∈ {м2, м²} — не нормализуются. Blocked 20-x на этом этапе отсутствуют (фильтр после) — пин-тестом зафиксировать.
- Якорь name (общий токен ≥3) и size-подстрока — как в v1, без изменений.
- Повторно отброшенные — в skipped с исходной причиной; сбой нормализатора — исходные списки, пайплайн не падает.
- Сигнатуры: `normalize_rows(rows, cfg=None, runner=None, deadline=None, text=None)`; `run_row_fallback(success, skipped, cfg=None, pdf_path=None)`.
- Тесты герметичны (моки runner/гейтов; живой kimi запрещён). Полный прогон `.venv/bin/pytest -q` зелёный. Точечный git add.

## Review Focus

1. **quantity=0/unit='' в исходной строке** — LLM-значения принимаются, не исходные (баг v1). Пин: Task 1.
2. **quantity не подтверждено текстом** — строка отброшена. Пин: Task 1.
3. **м²-строка** — не нормализуется (blacklist). Пин: Task 1.
4. **Двойник** («Не удалось распознать количество», то же имя) — исчезает из skipped после восстановления. Пин: Task 2.
5. **Blocked 20-x** — не появляются в success (фильтр после row-fallback). Пин: Task 2.

---

### Task 1: Протокол v2 в llm_row_fallback (blacklist + qty/unit от LLM)

**Files:**
- Modify: `llm_row_fallback.py` (qualifying_skipped → blacklist, validate_normalized, normalize_rows)
- Test: `tests/test_llm_row_fallback.py`

**Interfaces:**
- Consumes: `llm_document_fallback._name_positions`, `_quantity_matches`, `extract_document_text`; остальное как в v1.
- Produces:
  - `excluded_skipped(skipped) -> List[Dict]` — м²-строки (reason «Единица м²» или unit м²/м2).
  - `qualifying_skipped(skipped) -> List[Dict]` — ВСЕ skipped минус excluded (старые тесты whitelist обновить под новую семантику — имена тестов и сценарии по шагам ниже).
  - `validate_normalized(original: Dict, row: Dict, text: Optional[str]) -> Optional[Dict]` — name/size якоря как v1; unit из row по whitelist; quantity из row: int > 0; если text is not None — `_quantity_matches(text, pos+len(name), quantity)` для любого вхождения name (при text=None — без quantity-якоря, только >0).
  - `normalize_rows(rows, cfg=None, runner=None, deadline=None, text=None) -> List[Dict]` — промпт просит {name, size, unit, quantity} на каждую строку; validate_normalized(original, llm_row, text).

- [ ] **Step 1: Обновить failing-тесты под v2**

```python
def test_qualifying_skipped_blacklist_excludes_only_m2():
    import llm_row_fallback as rf
    rows = [
        _skipped(),                                              # да (размер/тип)
        _skipped(reason="Отсутствует размер"),                    # да
        _skipped(reason="Не удалось распознать количество (0)", quantity=0, unit=""),  # да — v2
        _skipped(reason="Покупная позиция (клапан)"),             # да — пытаем (решение пользователя)
        _skipped(reason="Единица м² — агрегатная"),               # нет
        _skipped(reason="Отсутствует размер", unit="м²"),         # нет
    ]
    got = rf.qualifying_skipped(rows)
    assert len(got) == 4

TEXT_DOC = "1\nОтвод ф125 45\n2\n2\nВозд.оц 0.5мм 150х150 1.25\n21\n"

def test_validate_normalized_accepts_llm_qty_with_text_anchor():
    import llm_row_fallback as rf
    orig = _skipped(name="Отвод ф125 45", reason="Не удалось распознать количество (0)", quantity=0, unit="")
    got = rf.validate_normalized(orig, {"name": "Отвод ф125 45", "size": "ф125", "unit": "шт", "quantity": 2}, TEXT_DOC)
    assert got["quantity"] == 2 and got["unit"] == "шт"

def test_validate_normalized_rejects_unconfirmed_qty():
    import llm_row_fallback as rf
    orig = _skipped(name="Отвод ф125 45", quantity=0, unit="")
    assert rf.validate_normalized(orig, {"name": "Отвод ф125 45", "size": "ф125", "unit": "шт", "quantity": 99}, TEXT_DOC) is None

def test_validate_normalized_rejects_bad_unit():
    import llm_row_fallback as rf
    orig = _skipped(name="Отвод ф125 45", quantity=0, unit="")
    assert rf.validate_normalized(orig, {"name": "Отвод ф125 45", "size": "ф125", "unit": "компл", "quantity": 2}, TEXT_DOC) is None
```

(старые тесты `test_qualifying_skipped_whitelist`, `test_validate_normalized_keeps_qty_unit_and_core` и пр. обновить под v2-семантику: quantity/unit теперь из row LLM.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_llm_row_fallback.py -q`
Expected: FAIL (старая whitelist-семантика/сигнатуры).

- [ ] **Step 3: Implement**

По Interfaces. Промпт: на каждую входную строку вернуть {name, size, unit, quantity} — каноническое имя, размер дословно, единица из whitelist, целое количество из документа; правила анти-выдумки те же + «количество — число из колонки Кол-во для этой позиции».

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest tests/test_llm_row_fallback.py -q`
Expected: PASS (все, включая обновлённые v1-тесты).

- [ ] **Step 5: Commit**

```bash
git add llm_row_fallback.py tests/test_llm_row_fallback.py
git commit -m "feat: row fallback v2 - all skipped except m2, LLM fixes qty/unit with text anchor"
```

---

### Task 2: run_row_fallback v2 (pdf_path + чистка двойников) + интеграция

**Files:**
- Modify: `llm_row_fallback.py` (run_row_fallback), `bitrix_bot/pipeline.py` (вызов), `web_app.py` (вызов)
- Test: `tests/test_llm_row_fallback.py`, `tests/test_bitrix_pipeline.py` (order test обновить под новый вызов)

**Interfaces:**
- Consumes: Task 1; `llm_document_fallback.extract_document_text`.
- Produces:
  - `run_row_fallback(success, skipped, cfg=None, pdf_path=None)` — text извлекается лениво (только если qualifying непуст и гейт пройден) через extract_document_text(pdf_path); при pdf_path=None — text=None (quantity-якорь пропускается, поведение деградирует мягко).
  - Чистка двойников: после мерджа, для каждой восстановленной LLM-строки удалить из skipped строки с `reason.startswith("Не удалось распознать количество")` и совпадающим `name.strip().lower()`.
  - pipeline/web_app: вызовы `run_row_fallback(..., pdf_path=<temp pdf path>)`.

- [ ] **Step 1: Write the failing tests**

```python
def test_run_row_fallback_cleans_qty_broken_twins(monkeypatch, tmp_path):
    import llm_row_fallback as rf
    monkeypatch.setattr(rf, "row_fallback_enabled", lambda cfg=None: True)
    twin = _skipped(name="Отвод ф125 45", reason="Не удалось распознать количество (0)", quantity=0, unit="")
    def fake_norm(rows, **kw):
        return [{"name": r["name"], "size": "ф125", "unit": "шт", "quantity": 2} for r in rows]
    monkeypatch.setattr(rf, "normalize_rows", fake_norm)
    pdf = tmp_path / "f.pdf"; pdf.write_bytes(b"%PDF-1.4")
    success, skipped, meta = rf.run_row_fallback([], [twin], pdf_path=str(pdf))
    assert meta["row_fallback_recovered"] == 1
    assert skipped == []  # двойник выведен из пропущенных

def test_run_row_fallback_keeps_non_twin_skips(monkeypatch, tmp_path):
    import llm_row_fallback as rf
    monkeypatch.setattr(rf, "row_fallback_enabled", lambda cfg=None: True)
    monkeypatch.setattr(rf, "normalize_rows", lambda rows, **kw: [])
    other = _skipped(name="Что-то иное", reason="Покупная позиция (клапан)")
    pdf = tmp_path / "f.pdf"; pdf.write_bytes(b"%PDF-1.4")
    _, skipped, meta = rf.run_row_fallback([], [other], pdf_path=str(pdf))
    assert skipped == [other] and meta["row_fallback_recovered"] == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_llm_row_fallback.py -k twin -q`
Expected: FAIL (старый run_row_fallback без pdf_path/чистки).

- [ ] **Step 3: Implement**

По Interfaces. Обновить вызовы в pipeline.py/web_app.py (передать pdf_path; в web_app temp-файл тот же, что для run_doc_fallback — проверить по коду; если удаляется раньше, перенести удаление после row-fallback). Pipeline-order test: порядок ["fallback", "row_fallback", "filter"] — сигнатура row-fallback изменилась, патч теста обновить.

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest tests/test_llm_row_fallback.py tests/test_bitrix_pipeline.py tests/test_llm_document_fallback.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add llm_row_fallback.py bitrix_bot/pipeline.py web_app.py tests/
git commit -m "feat: row fallback v2 wiring - pdf_path, twin cleanup"
```

---

### Task 3: Регрессионные пины + полный прогон + боевой смоук

**Files:**
- Test: `tests/test_llm_row_fallback.py` (дополнить)

- [ ] **Step 1: Пины Review Focus**

```python
def test_blocked_20x_never_in_success_after_row_fallback(monkeypatch):
    # Review Focus #5: blocked-артикулы фильтруются ПОСЛЕ row-fallback
    import bitrix_bot.pipeline as pl
    import llm_row_fallback as rf
    monkeypatch.setattr(rf, "row_fallback_enabled", lambda cfg=None: True)
    monkeypatch.setattr(rf, "normalize_rows",
                        lambda rows, **kw: [{"name": r["name"], "size": "ф125", "unit": "шт", "quantity": 1} for r in rows])
    ok, skipped, meta = rf.run_row_fallback(
        [], [_skipped(name="Клапан 20-1", reason="Отсутствует размер")])
    # row-fallback до фильтра: строка может попасть в success на этом этапе...
    # ...но фильтр отработает в process_pdf_to_positions — пин на уровне пайплайна:
    assert True  # placeholder, заменить: прогнать process_pdf_to_positions с моками
```

(Placeholder заменить реальным пином: process_pdf_to_positions с замоканными load_tables_from_pdf/run_doc_fallback/normalize_rows, где skipped содержит 20-1-строку, — assert: в финальном success артикула 20-1 нет, в skipped есть с not_trading.)

- [ ] **Step 2-4: Полный прогон + боевой смоук**

Run: `.venv/bin/pytest -q` — все зелёные.
Затем (ручной шаг контроллера, не субагента): живой прогон 168.pdf как dimon — ожидание SUCCESS ~55–58, skipped без qty-близнецов.

- [ ] **Step 5: Commit**

```bash
git add tests/test_llm_row_fallback.py
git commit -m "test: row fallback v2 regression pins"
```
