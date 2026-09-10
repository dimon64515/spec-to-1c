# Size Notation Config + Coverage Baseline + LLM Fallback — Design

Date: 2026-09-10
Status: approved (design)
Incremental: пункты 1 и 3 сначала, пункт 2 (LLM) вторым этапом.

## Проблема

Размеры/диаметры в проектных спецификациях записаны по-разному: `Ø315`, `315ø`,
`Ду315`, `DN315`, `Ф315`, `1250x800`, `315/315/200`, коды LITENED и т.д.
Сейчас покрытие — regex-каскад с точечными костылями, словари захардкожены
в `process_specification_table.py`, `reload_config()` не влияет на снапшот
конфига при импорте (стр. 44).

## Жёсткое ограничение

LLM никогда не генерирует и не переписывает цифры размеров. Цифры извлекаются
детерминированным парсером из ИСХОДНОЙ строки (по spans). LLM выбирает только
класс формата и границы подстрок.

## Архитектура

### 1. Конфиг вариантов записи размеров

- `config/size_notations.yaml`:
  - `diameter_prefixes`: `["Ø", "ø", "⌀", "Ду", "ду", "ДН", "дн", "DN", "dn", "D", "d", "Ф", "ф"]`
  - `diameter_suffixes`: `["ø", "Ø", "⌀"]` (форма «125ø»)
  - `separators`: `["х", "×", "*"]` → `x`
  - `ocr_heuristics`: потерянный ноль, «7 00», порог `_OCR_MIN_RECT_SIDE = 100`
  - `code_tables.litened_silencer_sizes`: 9 кодов `X-Y` → `(X*100, Y*100)` мм
    (перенос из `process_specification_table.py:81-91`)
  - `code_tables.litened_lengths`: `{NKD: 1100, NKK: 510}`
  - `validation`: `min_side_mm`, `max_side_mm` (диапазоны для guard-валидации)
- Новый модуль `size_notations.py`:
  - загружает YAML один раз (кэш), `reload_config()` сбрасывает кэш;
  - собирает единые compiled-паттерны из списков конфига (префикс-набор
    `dn|d|дн|ду|д|ф|ø|⌀` сейчас продублирован в 5+ regex'ах: стр. 220, 267,
    271, 431, 436, 470, 612);
  - экспортирует `validate_dimensions(dims, section) -> bool`.
- `process_specification_table.py`: `parse_size`, `extract_dimensions`,
  `extract_size_token`, `is_round`, `normalize_dimension_prefix`,
  `try_parse_ksd` читают паттерны из `size_notations`, литералы удаляются.
- Снапшот `_DEFAULTS = get_config()` (стр. 44) заменяется ленивым доступом,
  чтобы `reload_config()` реально работал.
- OCR-эвристики НЕ удаляются — только переносятся в конфиг.
- Поведение на существующих тестах не меняется (контролируется прогоном).

### 2. Эталонный корпус и метрика покрытия

- Снапшот `tmp/vladik_success.json` (234 позиции, реальное имя файла —
  НЕ `vladikavkaz_success.json`) → `tests/fixtures/vladik_success.json`.
  `tmp/kp_raw.txt` — источник имён позиций (таб-разделённый КП №1090).
- `tests/test_size_coverage.py`:
  - для каждой позиции кормим `extract_dimensions(comment)` с выключенным LLM
    (флаг конфига `llm.enabled: false`, дефолт);
  - сравниваем извлечённые dims с эталонными `params`;
  - метрики: recall по классам форматов (round prefix, round suffix,
    rect AxB, tee, transition, litened-code, knk D/L, elbow, length) +
    общий recall по позициям;
  - baseline фиксируется числом в тесте; падение ниже baseline = fail;
  - тест не ходит в сеть и в LLM.

### 3. LLM-фолбэк классификации (`llm_size_classifier.py`, этап 2)

- Бэкенд: subprocess `kimi -p --output-format stream-json` (проверено
  smoke-тестом 2026-09-10). Batched-вызов: неизвлечённые строки копятся и
  отправляются одним вызовом (`llm.batch_size` в конфиге).
- Вход: исходная строка размера (+ наименование позиции).
- Ответ по strict JSON: `{"format_class": enum["round_diameter","rect_axb",
  "tee_axbxc","reducer_pair","silencer_code","unknown"],
  "spans": [{"role": "a|b|c|length|diameter", "start": int, "end": int}]}` —
  смещения подстрок в ИСХОДНОЙ строке.
- После ответа: цифры извлекаются детерминированно `int(source[start:end])`,
  прогоняются через `size_notations.validate_dimensions`. Невалидное → None.
- Конфиг в `config.yaml`: `llm: {enabled: false, backend: kimi-cli, command,
  model, batch_size, timeout, max_retries}`. Без конфига / при недоступности —
  graceful-off, пайплайн работает как раньше.
- Интеграция в `parse_row`: каскад вернул None → LLM-классификация (если
  enabled) → отказ/невалидация → `skipped` с причиной «LLM-классификация
  отклонена» (по образцу существующего `classify_skip`,
  `process_specification_table.py:581-593`).
- Логирование: исходная строка, сырой ответ LLM, результат валидации →
  `logger` + в skipped-отчёт `<output>_skipped.json` (механизм есть на
  стр. 1490-1521).
- Зависимости: без новых pip-пакетов (subprocess + json из stdlib).

## Тестирование

TDD-порядок:
1. `tests/test_size_coverage.py` фиксирует baseline на текущем коде.
2. Рефакторинг (пункт 1) должен сохранить baseline зелёным.
3. Новые юнит-тесты: `size_notations` (варианты из конфига),
   `llm_size_classifier` (мок subprocess, валидация spans, отказ → None,
   llm disabled → no-op).
4. Критерий приёмки: `pytest tests/` зелёный, включая
   `test_process_specification_table.py`, `test_vladikavkaz_kp1090.py`,
   `test_techdept_rules.py`.

## Отчёт (по завершении)

- что вынесено в `config/size_notations.yaml`;
- форматы из эталона, всё ещё не покрытые каскадом (список);
- форматы, которые покрывает только LLM.

## Не делаем

- Не переписываем мэтчинг/артикулы и god-файл целиком — изменения локальны
  вокруг `parse_size`/`extract_dimensions` и точек вызова.
- Не добавляем pip-зависимостей.
- Не удаляем существующие OCR-эвристики.
