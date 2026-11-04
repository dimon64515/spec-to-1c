"""Тесты парсера журнала дашборда (bitrix_bot.dashboard.parse_journal)."""

from bitrix_bot.dashboard import parse_journal

SAMPLE = """\
2026-10-24T20:50:52+03:00 vpsmaks uvicorn[806738]: INFO:     104.210.55.152:0 - "GET / HTTP/1.1" 404 Not Found
2026-10-24T20:54:06+03:00 vpsmaks uvicorn[834258]: INFO:     195.208.184.34:0 - "POST /webhook/bot?CLIENT_ID=local.abc HTTP/1.1" 200 OK
2026-10-24T20:55:29+03:00 vpsmaks uvicorn[834258]: 2026-10-24 20:55:29,608 INFO httpx: HTTP Request: POST https://www.1c-woc.ru/KA_vok-region/hs/Zakaz/NewZakaz "HTTP/1.1 500 Internal server error"
2026-10-25T07:08:55+03:00 vpsmaks uvicorn[834258]: INFO:     89.208.230.2:0 - "POST /webhook/bot HTTP/1.1" 200 OK
2026-10-25T07:09:10+03:00 vpsmaks uvicorn[834258]: 2026-10-25 07:09:10,006 INFO httpx: HTTP Request: POST https://www.1c-woc.ru/KA_vok-region/hs/Zakaz/NewZakaz "HTTP/1.1 200 "
2026-10-26T10:56:36+03:00 vpsmaks uvicorn[1135217]: 2026-10-26 10:56:36,720 ERROR bitrix_bot.server: webhook processing failed
2026-10-26T10:56:36+03:00 vpsmaks uvicorn[1135217]: Traceback (most recent call last):
2026-10-26T10:56:36+03:00 vpsmaks uvicorn[1135217]:   File "/home/dimon64515/projects/xml-to-1c/bitrix_bot/server.py", line 191, in webhook
2026-10-26T10:56:36+03:00 vpsmaks uvicorn[1135217]:     raise BitrixError(f"{method}: HTTP {resp.status_code}: ...")
2026-10-26T10:56:36+03:00 vpsmaks uvicorn[1135217]: bitrix_bot.bitrix_client.BitrixError: disk.file.get: HTTP 403: {"error":"ACCESS_DENIED"}
2026-10-26T11:12:41+03:00 vpsmaks uvicorn[1135217]: INFO:     195.208.184.200:0 - "POST /webhook/bot HTTP/1.1" 200 OK
2026-10-26T11:14:09+03:00 vpsmaks uvicorn[1135217]: 2026-10-26 11:14:09,006 ERROR bitrix_bot.server: webhook processing failed
2026-10-26T11:14:09+03:00 vpsmaks uvicorn[1135217]: Traceback (most recent call last):
2026-10-26T11:14:09+03:00 vpsmaks uvicorn[1135217]: bitrix_bot.bitrix_client.BitrixError: disk.file.get: HTTP 403: {"error":"ACCESS_DENIED"}
"""


def test_days_aggregation():
    r = parse_journal(SAMPLE)
    d24 = r["days"]["2026-10-24"]
    assert d24["events"] == 1
    assert d24["orders_ok"] == 0
    assert d24["orders_err"] == 1
    d25 = r["days"]["2026-10-25"]
    assert d25["events"] == 1
    assert d25["orders_ok"] == 1
    d26 = r["days"]["2026-10-26"]
    assert d26["events"] == 1
    assert d26["errors"] == 2


def test_error_details_extracted():
    r = parse_journal(SAMPLE)
    assert len(r["errors"]) == 2
    e = r["errors"][0]
    assert e["date"] == "2026-10-26"
    assert e["type"] == "BitrixError"
    assert "ACCESS_DENIED" in e["detail"]


def test_restarts_counted_by_pid_change():
    r = parse_journal(SAMPLE)
    # PIDs: 806738 -> 834258 -> 1135217 = 2 смены
    assert r["restarts"] == 2


def test_access_log_noise_ignored():
    r = parse_journal(SAMPLE)
    assert "2026-10-24" in r["days"]
    # GET / 404 в тот же день не должен создавать событие
    assert r["days"]["2026-10-24"]["events"] == 1


def test_empty_input():
    r = parse_journal("")
    assert r["days"] == {}
    assert r["errors"] == []
    assert r["restarts"] == 0
