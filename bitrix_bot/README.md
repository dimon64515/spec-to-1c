# Битрикс-бот «Спецификация → Заказ 1С»

Чат-бот в задачах Битрикс24: reply на сообщение с PDF + @упоминание →
заказ в 1С (асСпецификацияЗаказа) + отчёт в чат задачи.
Спека: `docs/superpowers/specs/2026-09-06-bitrix-bot-design.md`.

## Запуск локально

    .venv/bin/uvicorn bitrix_bot.server:app --host 127.0.0.1 --port 8080

- `server.py` экспортирует модульный `app = create_app()`, так что таргет
  `bitrix_bot.server:app` для uvicorn корректен.
- Endpoint'ы: `POST /webhook/bot` (события Битрикса), `GET /health`
  (статус + очередь `{"ok": true, "stats": ...}`).
- Конфиг: `config.yaml` (секции `bitrix:`/`middleware:`) + секреты в
  `bitrix.local.yaml` (в `.gitignore`, в git не коммитить):
  `incoming_webhook`, `app_client_id`, `app_client_secret`,
  `webhook_verify_token`.
- Сам `bitrix.local.yaml` резолвится относительно корня проекта
  (`bitrix_bot/config.py`), cwd не важен. НО `middleware.tmp_dir`
  по умолчанию `"tmp/bitrix_bot"` — относительный путь: sqlite-очередь
  (`jobs.db`) и PDF лягут туда, откуда запущен uvicorn. Поэтому
  запускать нужно из корня проекта (или задать `tmp_dir` абсолютным).

## Верификация webhook (опционально)

Сервер сравнивает заголовок `X-Webhook-Token` запроса со значением
`middleware.webhook_verify_token` из `bitrix.local.yaml`
(`server.py:webhook`). Если значение пустое — проверка отключена и
endpoint открыт для всех.

- Заполните `webhook_verify_token` случайной строкой и задайте этот же
  заголовок в настройках приложения Битрикса (URL событий +
  произвольные заголовки), если ваша версия портала это позволяет.
- Если локальное приложение Битрикса не умеет задавать произвольные
  заголовки — оставьте поле пустым, но учтите риск: любой, кто узнает
  URL `/webhook/bot`, сможет слать боту события.

## Настройка Битрикс24 (svok-kavkaz.bitrix24.ru, админ)

1. Разработчикам → Другое → Локальное приложение: создать, указать
   URL обработчика событий `https://<домен>/webhook/bot`, права im, task, disk.
   Скопировать `client_id`/`client_secret` в `bitrix.local.yaml`
   (`app_client_id` / `app_client_secret`).
2. Регистрация чат-бота (выполнить один раз из консоли с токеном приложения):
   `imbot.register` с `EVENT_MESSAGE_ADD` → `https://<домен>/webhook/bot`,
   тип открытый, права im/task/disk. Бот ждёт событие `ONIMBOTMESSAGEADD`.
3. Вебхук для REST уже есть (`incoming_webhook` в `bitrix.local.yaml`).

## Прод (сервер завода)

systemd — `/etc/systemd/system/bitrix-bot.service`:

    [Unit]
    Description=spec-to-1c bitrix bot
    After=network.target

    [Service]
    WorkingDirectory=/home/dimon64515/projects/xml-to-1c
    ExecStart=/home/dimon64515/projects/xml-to-1c/.venv/bin/uvicorn bitrix_bot.server:app --host 127.0.0.1 --port 8080
    Restart=always
    RestartSec=5

    [Install]
    WantedBy=multi-user.target

`WorkingDirectory` обязателен: конфиги резолвятся от корня проекта, но
`middleware.tmp_dir` (sqlite-очередь и временные PDF) относителен cwd.
Без него очередь и файлы уйдут в каталог запуска systemd.

nginx: server 443 ssl для `<домена>` → `proxy_pass http://127.0.0.1:8080`
(websockets не нужны; `certbot --nginx`). Порт 6005 (MCP/1С) наружу НЕ публиковать.

## Ручная сверка событий (один раз после настройки)

Схема событий Битрикс24 недокументирована до конца (что именно приходит
в reply-контексте), поэтому `bitrix_bot/events.py` ищет файл по
нескольким предполагаемым ключам. Проверить на реальных событиях:

1. Создать тестовую задачу, приложить PDF, reply + @бот.
2. Посмотреть тело POST `/webhook/bot` и сравнить с предположениями кода:
   - временно добавить в начало `webhook` в `server.py` строку
     `logger.info("payload: %s", await request.json())`
     (или `print(payload)`), выполнить `journalctl -u bitrix-bot -f`;
   - access-лог nginx тело запроса НЕ покажет (по умолчанию логируются
     только метод/путь/код) — не полагайтесь на него, используйте
     логирование в коде. Отладочный endpoint можно не сносить:
     `GET /health` уже отдаёт `queue.stats()`, для payload достаточно
     временного logger.
3. Сверяемые предположения (`events.py`):
   - reply-контекст: `parse_event` читает цитируемое сообщение из
     `params.MESSAGE_REPLIED` или `params.message_replied` → его `params`;
   - файлы в сообщении: `_first_file(params)` ищет `FILES`/`files`
     (с ключами `url`/`downloadUrl`/`DOWNLOAD_URL`, `name`/`FILE_NAME`),
     затем `FILE_URL`/`DOWNLOAD_URL`/`ATTACH_URL`, затем `ATTACH[].LINK`;
   - fallback (уже реализован): `find_pdf` берёт последние 30 сообщений
     диалога через `client.get_dialog_messages` и ищет файл в них
     (`_file_from_message`).
4. При расхождении поправить `_first_file`/`_file_from_message` (или
   ключи в `parse_event`) под реальную схему.

## Проверка конца в конец

Тестовая задача с реальным PDF из `examples/customer_projects/` → в чате
«Принял …, обрабатываю…» → «Заказ 1С: №…» + сводка + списки.
Заказ в 1С проверить вручную (тестовая база).
