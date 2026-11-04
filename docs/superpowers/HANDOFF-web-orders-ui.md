# Handoff: веб-интерфейс заявок (web_ui)

Дата: 2026-11-03. Автор сессии: Kimi Code (процесс superpowers SDD).

## Состояние на момент передачи

- **Код готов и запушен**: ветка `feature/web-orders-ui`, PR https://github.com/dimon64515/spec-to-1c/pull/2 (base: `feature/llm-doc-fallback`).
- **Worktree живой**: `/home/dimon64515/projects/xml-to-1c-web-ui` — правки по ревью PR делать там.
- **Тесты**: 33 новых по `tests/test_web_ui_*.py`; полный пакет проекта 489 passed / 4 skipped (в env `/home/dimon64515/projects/xml-to-1c/.venv`, в нём есть fastapi; в `venv/` fastapi нет).
- **Внешний доступ закрыт**: проброс 185.50.203.136:8092 остановлен 2026-11-03 по просьбе пользователя.

## Что построено

Пакет `web_ui/` (FastAPI + Jinja2, sqlite, порт 8092):

| Файл | Ответственность |
|---|---|
| `web_ui/store.py` | sqlite-хранилище: `requests` + `positions` (kind: loaded/skipped/trading), статусы processing/done/error/loaded_1c, meta/header JSON |
| `web_ui/analogs.py` | подбор аналога для skipped-строки (map_customer_equipment) и конвертация в loaded-позицию; `AnalogError` |
| `web_ui/runner.py` | `run_processing` (пайплайн бота + дилерские цены price_search), `load_to_1c` (build_positions → load_order_to_1c → merge цен), `apply_analog` |
| `web_ui/app.py` | маршруты: GET /, POST /upload, GET /r/{id}, GET /api/status/{id}, POST /r/{id}/load-1c, POST /r/{id}/analog/{index}, GET /r/{id}/xml, GET /r/{id}/report.xlsx |
| `web_ui/templates/` + `static/` | серверные страницы; polling каждые 2 с |

Запуск: `cd /home/dimon64515/projects/xml-to-1c-web-ui && /home/dimon64515/projects/xml-to-1c/.venv/bin/python -m web_ui.app` → http://127.0.0.1:8092 (внутри `python -m web_ui.app` поднимет create_app с БД `tmp/web_ui/web_ui.db`; там уже есть демо-заявки).

## Документы

- Спек: `docs/superpowers/specs/2026-11-02-web-orders-dashboard-design.md`
- План: `docs/superpowers/plans/2026-11-03-web-orders-ui.md`
- Ledger с rulings и parked-файндингами: `.superpowers/sdd/2026-11-03-web-orders-ui/progress.md`

## Ключевые решения и инварианты (не нарушать)

1. `load_to_1c` ставит статус `loaded_1c` ТОЛЬКО при непустом `order_number` и пустых `errors` — бизнес-ошибка 1С оставляет `done` и разрешает повтор (коммит 3b8c76d).
2. Гард загрузки в 1С: разрешено только из статуса `done` (защита от дублей заказов в 1С).
3. `accept_analog` валидирует quantity (число) и размер; при неуспехе строка остаётся пропущенной с `analog_error` (коммиты 1175498, e94fc15).
4. `_merge_prices` обёрнут в try/except: битые цены не роняют успешную загрузку заказа.
5. Пайплайн вызывается как `pipeline.process_pdf_to_positions` (атрибут модуля) — нужно для monkeypatch в тестах.
6. Новых зависимостей не добавлять — только существующий `.venv` (fastapi/uvicorn/jinja2/httpx уже есть).

## Что отложено (следующие шаги)

1. **Task 7 — дизайн-QA по dashboard-design-skill со скриншотами** (отложено пользователем): запустить сервер, открыть в браузере с расширением Kimi WebBridge, чек-лист из плана Task 7. Демо-данные уже в `tmp/web_ui/web_ui.db`.
2. **BACKLOG из финального ревью** (все parked, не блочеры):
   - `asyncio.to_thread` для пайплайна и `load_to_1c` (сейчас синхронный вызов замораживает event loop; для 1 пользователя терпимо).
   - Recovery заявок, зависших в `processing` после рестарта сервера.
   - TOCTOU на гарде статуса load_to_1c (условный UPDATE по статусу).
   - `except` мержа цен расширить на KeyError.
   - `list_requests` N+1 (агрегация в SQL) и `float(sum)` без try.
   - `_price_engine` молча глотает сбой импорта price_search — залогировать.
   - Мёртвый `app.state.db_path`; плейсхолдеры полей формы сырыми ключами; trading-цена без разделителей тысяч.
3. **Внешний доступ** (при необходимости): НЕ открывать порт напрямую — сделать поддомен `zayavki.fanat-mv.ru` через nginx + certbot по образцу `report.fanat-mv.ru` (конфиг в /etc/nginx/sites-available/). UI без авторизации и «Загрузить в 1С» создаёт реальные заказы — наружу только за auth.
4. **Мерж**: PR #2 → `feature/llm-doc-fallback` → дальше по вашему процессу. После мержа удалить worktree (`git worktree remove ../xml-to-1c-web-ui`) и ветку.

## Быстрый старт для следующего агента

```bash
cd /home/dimon64515/projects/xml-to-1c-web-ui
git status && git log --oneline -10          # состояние ветки
/home/dimon64515/projects/xml-to-1c/.venv/bin/python -m pytest tests/test_web_ui_*.py -v   # 33 теста
/home/dimon64515/projects/xml-to-1c/.venv/bin/python -m web_ui.app &                        # сервер на 127.0.0.1:8092
```
