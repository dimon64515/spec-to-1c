# План переезда на VPS 185.253.103.130 (ptg)

Дата: 2026-10-20. Статус: ПЕРЕЕЗД ЗАВЕРШЁН. HTTPS работает (сертификат Let's Encrypt на IIS, win-acme, авто-продление), внешний доступ проверен, остался smoke-тест в Битрикс24.

## Реальное положение дел (обнаружено 2026-09-29)

**Переезд частично выполнен заранее (предподготовка сентябрь):**

- VPS `185.253.103.130` (hostname `kp-service`, Debian 12, 2 CPU / 3.8 ГБ RAM /
  29 ГБ диск, Python 3.11 + uv, nginx 1.22 + certbot-nginx):
  - код в `/opt/xml-to-1c` (git clone с github.com/dimon64515/spec-to-1c),
    HEAD `fa6a256` — **на 1 коммит отстает** от рабочей копии (`c37fab6`, коммит
    локально, не запушен);
  - systemd-юнит `bitrix-bot.service` работает с 19 сентября, health OK;
  - nginx-сайт `bitrix-bot` (80 → 127.0.0.1:8080, body 25m, timeout 300s) для
    домена `bitrix-wok-ptg.point-clean.ru`;
  - camoufox/crawl4ai установлены (логи от 09.09), `price_search.db` устарел
    (от 11.07); `tmp/bitrix_bot/` пуст (jobs.db без накопленной истории);
  - **HTTPS-сертификата нет** (certbot: no certificates);
  - firewall (ufw) не установлен, cron пустой.
- **DNS:** `bitrix-wok-ptg.point-clean.ru` → `77.222.40.65` = `vh328.sweb.ru`
  (виртуальный хостинг SpaceWeb, не наш — там чужая заглушка, не бот).
  Реального прода нет: бот жил на ноутбуке разработчика (недоступен из
  интернета), событий Битрикс извне не было. На VPS трафика нет
  (access.log пуст). Единственное состояние — на ноутбуке: `tmp/bitrix_bot/`
  (96 МБ, 139 PDF + jobs.db), `price_search.db`, `bitrix.local.yaml`.
  Переезд = первый выход в прод + перенацеливание DNS на 185.253.103.130.
- Основной транспорт заказов — публичный HTTP NewZakaz (`www.1c-woc.ru`),
  VPN не нужен. MCP 6005 (execute_code, фолбэк) — туннель пользователь
  поднимает вручную с ноутбука при необходимости.

## Оставшиеся шаги

### 1. Код
- [x] Проверить тесты локально (378 passed), запушить `c37fab6` (сейчас только локально).
- [x] На VPS: `git pull`, `systemctl restart bitrix-bot`, проверить `/health`.

### 2. Данные (синк с ноутбука разработчика — единственное состояние)
- [x] Остановить локальный uvicorn, критичное скопировано на VPS (jobs.db + price_search.db + bitrix.local.yaml; очередь видит done 103 / failed 37):
  `tmp/bitrix_bot/` (jobs.db + 139 PDF, 96 МБ), `price_search.db`,
  `bitrix.local.yaml` (сверить секреты; `webhook_verify_token` сейчас
  пустой — заполнить случайной строкой).
- [x] Проверить целостность БД на VPS (integrity_check ok) (sqlite3 .tables / pragma integrity_check).

### 3. HTTPS и DNS-переключение
- [x] Перенацелить A-запись `bitrix-wok-ptg.point-clean.ru` → 185.253.103.130.
- [x] TLS: НЕ certbot (порты 80/443 заняты IIS на Win Server 2016) — вместо этого
  IIS reverse proxy (ARR+URL Rewrite) → VM:80, сертификат Let's Encrypt через
  win-acme (http-01, filesystem), привязка 443 SNI на сайте bitrix-wok-ptg.
- [x] Проверено извне 2026-10-20: https /health → {"ok":true}, /ui/ → 401 (basic auth), POST /webhook/bot → 200.
- [ ] В Битрикс24 сверить URL обработчика (домен тот же — менять не нужно;
      при переходе на https убедиться, что события продолжают приходить).

### 4. Туннель 1С (6005)
- [ ] По необходимости вручную с ноутбука:
  `ssh -N -R 6005:127.0.0.1:6005 -p 45022 root@185.253.103.130`
  (на VPS порт 6005 слушает только 127.0.0.1 — публиковать наружу нельзя).

### 5. Streamlit web_app
- [x] Решение: nginx `/ui/` → 8501 с basic auth, юнит `streamlit-webapp` (пароль выдан владельцу).

### 6. Hardening VPS
- [x] ufw: allow 22/45022/80/443; enable. PasswordAuthentication уже был `no`.
- [ ] sshd: `PasswordAuthentication no` (после проверки ключей), убрать порт 22
  если держится параллельно с 45022 без нужды.
- [ ] fail2ban (опционально).

### 7. Бэкапы и мониторинг
- [x] cron 1×/день: rsync `tmp/bitrix_bot/`, `price_search.db`,
  `bitrix.local.yaml`, `config*.yaml` → `/root/backups/spec-to-1c/` (+ ротация).
- [x] cron-проверка `/health` с алертом в Битрикс-чат через incoming webhook.

### 8. Финализация
- [ ] Smoke-тест: PDF → reply + @бот → заказ в 1С → отчёт в задаче.
- [x] Локальный uvicorn на ноутбуке остановлен.
- [x] Обновить `bitrix_bot/README.md` (реальные пути/домен на VPS).

## Риски
- price_search с датацентровского IP может получать баны от сайтов поставщиков —
  проверить после переноса.
- c37fab6 — wip-коммит (newzakaz flow): прогнать pytest перед пушем.

---

## ТЕКУЩИЙ СТАТУС (handoff, 2026-10-22) — продолжение сессии на VPS

**⚠️ Откат (2026-10-22): переезд на боевую базу 1С ОТМЕНЁН по решению владельца.**
`order_service_url` снова указывает на **тестовую** базу
(`https://www.1c-woc.ru/KA_vok-region_test/hs/Zakaz/NewZakaz`). Боевая база НЕ
используется до деплоя модуля v2 и отдельного решения о переходе.

**Инфраструктура: готова и работает.**
- Бот в проде: `systemctl status bitrix-bot` (uvicorn 127.0.0.1:8080), работает с
  20.10.2026, вебхуки Битрикс24 ходят (BOT_ID=377, домен bitrix-wok-ptg.point-clean.ru,
  IIS на Win Server 2016 терминирует TLS, ARR → VM:80 → nginx → uvicorn).
- Streamlit: `systemctl status streamlit-webapp` (8501), наружу через `/ui/` с basic auth.
- `bitrix_bot/README.md` — актуальная инфраструктурная документация.

**Следующая задача (вне этого сервера — нужен 1С-админ):**
1. Заменить модуль HTTP-сервиса `ПолучениеЗаказов` в ОБЕИХ базах
   (тест `KA_vok-region_test` и бой `KA_vok-region`) на актуальный код:
   `tools/as_order_loader/http_service_newzakaz.bsl` (v2 от 20.09: заголовок
   `X-API-Key` + фикс идемпотентности через СтрНайти). Сейчас в обеих базах v1
   (принимает только legacy-заголовок `key`), поэтому бот (шлёт X-API-Key) получает
   403 и падает в фолбэк execute_code.
2. После обновления модулей — проверка (делает Python-сторона):
   `POST .../hs/Zakaz/NewZakaz` с basic-auth и заголовком `X-API-Key` (ключ в
   `bitrix.local.yaml`, поле `order_service_key`), тело `{}` → ожидается HTTP 500
   `"Не найден массив positions"` (это успех: ключ принят, дошли до логики).
   Без ключа → HTTP 403 JSON `"Нет установленного ключа обработки"`.
3. Затем боевой smoke-тест: реальная задача с PDF → reply + @бот → заказ в
   **боевой** базе KA_vok-region + xlsx-отчёт в чат.
   `order_service_url` уже переключён на боевую базу
   (`https://www.1c-woc.ru/KA_vok-region/hs/Zakaz/NewZakaz`).

**Ключевые пути на VPS:** проект `/opt/xml-to-1c` (git clone, обновлять через
`git pull`), секреты — `bitrix.local.yaml` (в gitignore), бэкапы —
`/root/backups/spec-to-1c/` (cron 04:00, скрипт `/root/backup_spec_to_1c.sh`),
healthcheck каждые 5 мин (`/root/healthcheck_spec.sh`).
SSH к ноутбуку-разработчика не требуется; вся работа — на этом сервере.
**Осторожно:** `git pull` только внутри `/opt/xml-to-1c`; секреты не коммитить.
