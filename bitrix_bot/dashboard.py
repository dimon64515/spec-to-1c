"""Локальный дашборд статистики бота: парсинг journald + HTML-страница.

Отдельное FastAPI-приложение (не боевой бот!) — только чтение:
  journalctl -u bitrix-bot + sqlite-очередь jobs.db (read-only).

Запуск (из корня проекта):
    .venv/bin/uvicorn bitrix_bot.dashboard:app --host 127.0.0.1 --port 8081
"""

from __future__ import annotations

import logging
import re
import sqlite3
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse

from bitrix_bot.config import ROOT, load_bot_config

logger = logging.getLogger(__name__)

UNIT = "bitrix-bot"
CACHE_TTL_SECONDS = 60

LINE_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2})T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}\s+\S+\s+"
    r"uvicorn\[(\d+)\]:\s?(.*)$"
)
WEBHOOK_RE = re.compile(r'"POST /webhook/bot[^"]*"\s+(\d{3})')
NEWZAKAZ_RE = re.compile(r"hs/Zakaz/NewZakaz\s+\"HTTP/1\.1\s+(\d{3})")
ERROR_RE = re.compile(r"\d{4}-\d{2}-\d{2}[\sT]\d{2}:\d{2}:\d{2}.*?\sERROR\s+\S+:\s?(.*)$")
EXC_RE = re.compile(r"^(\S*(?:Error|Exception)\S*):\s?(.*)$")


def parse_journal(text: str) -> Dict[str, Any]:
    """Разобрать вывод journalctl -o short-iso в статистику.

    Возвращает: events/orders/errors по дням, список ошибок, число рестартов.
    """
    days: Dict[str, Dict[str, int]] = {}
    errors: List[Dict[str, str]] = []
    pids: List[int] = []
    cur_error: Optional[Dict[str, str]] = None

    def day_bucket(d: str) -> Dict[str, int]:
        return days.setdefault(d, {"events": 0, "orders_ok": 0, "orders_err": 0,
                                   "errors": 0, "webhook_err": 0})

    for raw in text.splitlines():
        m = LINE_RE.match(raw)
        if m:
            date, pid, msg = m.group(1), int(m.group(2)), m.group(3)
            if not pids or pids[-1] != pid:
                pids.append(pid)
            wm = WEBHOOK_RE.search(msg)
            if wm:
                b = day_bucket(date)
                b["events"] += 1
                if not wm.group(1).startswith("2"):
                    b["webhook_err"] += 1
                continue
            zm = NEWZAKAZ_RE.search(msg)
            if zm:
                b = day_bucket(date)
                if zm.group(1).startswith("2"):
                    b["orders_ok"] += 1
                else:
                    b["orders_err"] += 1
                continue
            em = ERROR_RE.search(msg)
            if em:
                b = day_bucket(date)
                b["errors"] += 1
                cur_error = {"date": date, "message": em.group(1)[:200],
                             "type": "", "detail": ""}
                errors.append(cur_error)
                continue
            if cur_error is not None:
                xm = EXC_RE.match(msg.strip())
                if xm:
                    cur_error["type"] = xm.group(1).split(".")[-1]
                    cur_error["detail"] = xm.group(2)[:300]
                    cur_error = None
        elif cur_error is not None:
            xm = EXC_RE.match(raw.strip())
            if xm:
                cur_error["type"] = xm.group(1).split(".")[-1]
                cur_error["detail"] = xm.group(2)[:300]
                cur_error = None

    return {
        "days": days,
        "errors": errors,
        "restarts": max(len(pids) - 1, 0),
    }


def read_journal(days: int, unit: str = UNIT) -> str:
    out = subprocess.run(
        ["journalctl", "-u", unit, "-o", "short-iso",
         "--since", f"{days} days ago", "--no-pager"],
        capture_output=True, text=True, timeout=60,
    )
    return out.stdout


def service_status(unit: str = UNIT) -> str:
    try:
        out = subprocess.run(
            ["systemctl", "is-active", unit],
            capture_output=True, text=True, timeout=10,
        )
        return out.stdout.strip() or "unknown"
    except Exception:  # noqa: BLE001 - дашборд не должен падать без systemd
        return "unknown"


def queue_stats() -> Dict[str, int]:
    cfg = load_bot_config()
    db_path = Path(cfg.tmp_dir) / "jobs.db"
    if not db_path.exists():
        db_path = ROOT / cfg.tmp_dir / "jobs.db"
    if not db_path.exists():
        return {}
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT status, COUNT(*) FROM jobs GROUP BY status"
        ).fetchall()
    finally:
        conn.close()
    return {status: n for status, n in rows}


def collect_stats(days: int) -> Dict[str, Any]:
    parsed = parse_journal(read_journal(days))
    q = queue_stats()
    return {
        "unit": UNIT,
        "period_days": days,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "service": service_status(),
        "restarts": parsed["restarts"],
        "queue": q,
        "totals": {
            "events": sum(d["events"] for d in parsed["days"].values()),
            "orders_ok": sum(d["orders_ok"] for d in parsed["days"].values()),
            "orders_err": sum(d["orders_err"] for d in parsed["days"].values()),
            "errors": sum(d["errors"] for d in parsed["days"].values()),
        },
        "days": parsed["days"],
        "recent_errors": parsed["errors"][-30:][::-1],
    }


_cache: Dict[str, Any] = {"ts": 0.0, "data": None}


def create_app() -> FastAPI:
    app = FastAPI(title="bitrix-bot dashboard")

    @app.get("/api/stats")
    def api_stats(days: int = Query(30, ge=1, le=365)):
        now = time.time()
        if _cache["data"] is None or now - _cache["ts"] > CACHE_TTL_SECONDS \
                or _cache["data"]["period_days"] != days:
            _cache["data"] = collect_stats(days)
            _cache["ts"] = now
        return _cache["data"]

    @app.get("/", response_class=HTMLResponse)
    def index():
        return INDEX_HTML

    return app


INDEX_HTML = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<title>Статистика бота (Bitrix → 1С)</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<style>
  body { font-family: system-ui, sans-serif; background:#14181f; color:#e2e8f0;
         margin:0; padding:24px; }
  h1 { font-size:20px; margin:0 0 4px; }
  .sub { color:#94a3b8; font-size:13px; margin-bottom:20px; }
  .cards { display:flex; flex-wrap:wrap; gap:12px; margin-bottom:20px; }
  .card { background:#1e2530; border-radius:10px; padding:12px 18px; min-width:130px; }
  .card .v { font-size:26px; font-weight:600; }
  .card .l { font-size:12px; color:#94a3b8; }
  .ok { color:#4ade80; } .bad { color:#f87171; } .warn { color:#fbbf24; }
  .grid { display:grid; grid-template-columns:1fr 1fr; gap:16px; }
  @media (max-width: 900px) { .grid { grid-template-columns:1fr; } }
  .panel { background:#1e2530; border-radius:10px; padding:16px; }
  table { width:100%; border-collapse:collapse; font-size:13px; }
  th, td { text-align:left; padding:6px 8px; border-bottom:1px solid #2b3442;
           vertical-align:top; }
  th { color:#94a3b8; font-weight:500; }
  td.err { color:#f87171; word-break:break-word; }
</style>
</head>
<body>
<h1>Статистика бота «Спецификация → Заказ 1С»</h1>
<div class="sub" id="sub">загрузка…</div>

<div class="cards" id="cards"></div>

<div class="grid">
  <div class="panel"><canvas id="chEvents"></canvas></div>
  <div class="panel"><canvas id="chErrors"></canvas></div>
</div>

<div class="panel" style="margin-top:16px">
  <h3 style="margin-top:0">Последние ошибки</h3>
  <table><thead><tr><th>Дата</th><th>Тип</th><th>Сообщение</th></tr></thead>
  <tbody id="errBody"></tbody></table>
</div>

<script>
const params = new URLSearchParams(location.search);
const days = params.get('days') || 30;

fetch('/api/stats?days=' + days).then(r => r.json()).then(s => {
  document.getElementById('sub').textContent =
    'Юнит: ' + s.unit + ' · период: ' + s.period_days + ' дн. · обновлено: ' + s.generated_at;

  const q = s.queue || {};
  const active = s.service === 'active';
  const cards = [
    ['Сервис', s.service, active ? 'ok' : 'bad'],
    ['События webhook', s.totals.events, ''],
    ['Заказов создано', s.totals.orders_ok, 'ok'],
    ['Ошибок 1С', s.totals.orders_err, s.totals.orders_err ? 'bad' : ''],
    ['Ошибок обработки', s.totals.errors, s.totals.errors ? 'warn' : ''],
    ['Рестартов', s.restarts, ''],
    ['В очереди', (q.pending||0) + ' / ' + (q.running||0), (q.pending||q.running) ? 'warn' : ''],
    ['Failed', q.failed||0, q.failed ? 'bad' : ''],
  ];
  document.getElementById('cards').innerHTML = cards.map(c =>
    '<div class="card"><div class="v ' + c[2] + '">' + c[1] + '</div><div class="l">' + c[0] + '</div></div>'
  ).join('');

  const labels = Object.keys(s.days).sort();
  const val = k => labels.map(d => s.days[d][k]);
  const mk = (id, ds) => new Chart(document.getElementById(id), {
    type: 'bar',
    data: { labels, datasets: ds },
    options: { plugins: { legend: { labels: { color: '#e2e8f0' } } },
               scales: { x: { ticks: { color: '#94a3b8' } },
                         y: { beginAtZero: true, ticks: { color: '#94a3b8', precision: 0 } } } }
  });
  mk('chEvents', [
    { label: 'События webhook', data: val('events'), backgroundColor: '#38bdf8' },
    { label: 'Заказы 1С (успех)', data: val('orders_ok'), backgroundColor: '#4ade80' },
    { label: 'Заказы 1С (ошибка)', data: val('orders_err'), backgroundColor: '#f87171' },
  ]);
  mk('chErrors', [
    { label: 'Ошибки обработки', data: val('errors'), backgroundColor: '#fbbf24' },
  ]);

  document.getElementById('errBody').innerHTML = s.recent_errors.map(e =>
    '<tr><td>' + e.date + '</td><td>' + (e.type||'') + '</td><td class="err">' +
    (e.detail ? e.detail : e.message) + '</td></tr>'
  ).join('') || '<tr><td colspan="3">Ошибок нет 🎉</td></tr>';
});
</script>
</body>
</html>
"""

app = create_app()
