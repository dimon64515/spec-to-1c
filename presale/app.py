"""Веб-приложение предварительного расчёта (FastAPI + Jinja2).

Эндпоинты: /login, /logout, / (история), /upload (файл ChatGPT → расчёт),
/job/{id} (JSON статуса), /estimate/{id} (сводка + правки), выгрузки xlsx,
лог правок строки. Аутентификация — подписанная itsdangerous cookie
`presale_session` (секрет из env PRESALE_SECRET; без него — случайный
с предупреждением в лог).

Пользователи заводятся через CLI:
    python -m presale.app --db presale.db --create-user igor secret "Игорь"

Запуск сервера — фабрика не принимает аргументов командной строки,
поэтому через маленький модуль-обёртку:
    # run_presale.py
    from presale.app import create_app
    app = create_app("presale.db", ("rect.xlsx", "round.xlsx"),
                     "http://1c/mcp/execute")  # execute_url можно None
    # PRESALE_SECRET=... uvicorn run_presale:app
"""
import argparse
import json
import logging
import os
import re
import secrets as _secrets
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Optional, Union

from fastapi import FastAPI, Request, Response, UploadFile
from fastapi.exceptions import HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from itsdangerous import BadSignature, TimestampSigner

from presale.boris_prices import (
    MATERIAL_GROUP_LABELS,
    PRODUCT_KIND_LABELS,
    SHAPE_LABELS,
    BorisPriceTable,
)
from presale.chatgpt_table import parse_chatgpt_workbook
from presale.db import PresaleDB
from presale.jobs import JobRunner
from presale.mcp_1c import BackendUnavailableError, OneCPriceBackend
from presale.models import CENT, Category, EstimateItem, PriceStatus, build_summary
from presale.pricing import MarketPricer, PriceBackend, price_items
from presale.report import build_customer_xlsx, build_internal_xlsx

logger = logging.getLogger(__name__)

COOKIE_NAME = "presale_session"

CATEGORY_LABELS = {
    Category.WORKS: "Работы",
    Category.MATERIALS: "Материалы",
    Category.EQUIPMENT: "Оборудование",
}

STATUS_ICONS = {
    PriceStatus.OK: "🟢",
    PriceStatus.ROUNDED: "🟡",
    PriceStatus.MISSING: "🔴",
    PriceStatus.MANUAL: "🟢",
    PriceStatus.OVERRIDDEN: "🟢",
}

EDITABLE_FIELDS = {
    "internal_price": "цена",
    "qty": "количество",
    "category": "категория",
}


class AuthRequired(Exception):
    """Нет валидной сессии — редирект на /login (обработчик ниже)."""


class _NullBackend:
    """PriceBackend без 1С: всё мимо (1С не подключена — execute_url=None)."""

    def search_1c(self, names):
        return {}


class _NullMarket:
    """MarketPricer без price_search: цен нет."""

    def market_price(self, name):
        return None


def _money(value: Optional[Decimal]) -> str:
    """Decimal → строка с 2 знаками; None и не-Decimal — как есть."""
    if value is None:
        return ""
    if isinstance(value, Decimal):
        return str(value.quantize(CENT, rounding=ROUND_HALF_UP))
    return str(value)


_WORD_RE = re.compile(r"[0-9a-zа-яё]+")


def _norm_item_name(name: str) -> str:
    """Нормализация имени строки: casefold + только слова/цифры."""
    return " ".join(_WORD_RE.findall(name.casefold()))


def find_similar_estimates(db: PresaleDB,
                           items: list[EstimateItem],
                           customer: str) -> list[int]:
    """id похожих расчётов по убыванию релевантности.

    Похожесть: совпадение заказчика (casefold) ИЛИ пересечение ≥ 50%
    нормализованных имён строк. Оценка = 2 (заказчик) + доля пересечения,
    иначе 1 + доля пересечения — заказчик ранжируется выше чистого
    совпадения имён. Расчёт, из которого взяты items, в выборку попадает
    (вызывальщик отсекает самого себя).
    """
    names = {n for n in (_norm_item_name(i.name) for i in items) if n}
    customer_key = (customer or "").casefold()
    scored: list[tuple[float, int]] = []
    for meta in db.list_estimates():
        score = 0.0
        if customer_key and (meta.get("customer") or "").casefold() \
                == customer_key:
            score = 2.0
        try:
            _, other_items = db.get_estimate(meta["id"])
        except KeyError:
            continue
        other = {n for n in (_norm_item_name(i.name) for i in other_items)
                 if n}
        overlap = len(names & other) / len(names) if names and other else 0.0
        if score:
            score += overlap
        elif overlap >= 0.5:
            score = 1.0 + overlap
        if score:
            scored.append((score, meta["id"]))
    scored.sort(key=lambda p: (-p[0], p[1]))
    return [est_id for _, est_id in scored]


def create_app(
    db_path: Union[str, Path],
    boris_paths: tuple,
    execute_url: Optional[str] = None,
    *,
    sync_jobs: bool = False,
    backend: Optional[PriceBackend] = None,
    market: Optional[MarketPricer] = None,
) -> FastAPI:
    """Фабрика приложения. boris_paths = (rect.xlsx, round.xlsx).

    backend/market — переопределения для тестов (stub'ы); по умолчанию
    берутся OneCPriceBackend(execute_url) и PriceSearchMarketPricer.
    sync_jobs=True исполняет job'ы внутри запроса (тесты).
    """
    app = FastAPI(title="Предварительный расчёт")
    db = PresaleDB(Path(db_path))
    rect_path = Path(boris_paths[0])
    round_path = Path(boris_paths[1])

    def build_boris() -> BorisPriceTable:
        """Таблица Бориса с актуальными overrides из БД (на каждый расчёт).

        Ключ override в БД — JSON-массив строк
        [material_group, thickness, shape, product_kind]; толщина
        приводится к Decimal (в таблице ключ по округлённой толщине).
        """
        overrides = {}
        for key, price in db.get_overrides("boris").items():
            material, thickness, shape, kind = json.loads(key)
            overrides[(material, Decimal(str(thickness)), shape, kind)] = price
        return BorisPriceTable.from_xlsx(
            rect_path, round_path, overrides=overrides
        )

    if backend is None:
        backend = (OneCPriceBackend(execute_url)
                   if execute_url else _NullBackend())
    if market is None:
        try:
            from presale.market import PriceSearchMarketPricer
            from price_search.engine import AsyncPriceEngine
            from price_search.sources.base import SourceRegistry
            from price_search.sources.hvac import AirvekSource, UmClimatSource
            from price_search.storage import PriceStorage
            registry = SourceRegistry()
            registry.register(UmClimatSource())
            registry.register(AirvekSource())
            market = PriceSearchMarketPricer(
                AsyncPriceEngine(PriceStorage(), registry)
            )
        except Exception:
            logger.exception("price_search недоступен")
            logger.warning("price_search недоступен, рыночные цены отключены")
            market = _NullMarket()

    secret = os.environ.get("PRESALE_SECRET")
    if not secret:
        secret = _secrets.token_hex(32)
        logger.warning(
            "PRESALE_SECRET не задан — сессии протухнут при перезапуске"
        )
    signer = TimestampSigner(secret)

    app.state.db = db
    app.state.backend = backend
    app.state.market = market
    app.state.runner = JobRunner(sync=sync_jobs)
    app.state.signer = signer
    templates = Jinja2Templates(
        directory=str(Path(__file__).parent / "templates")
    )
    templates.env.globals["money"] = _money
    templates.env.globals["category_label"] = CATEGORY_LABELS.get
    templates.env.globals["status_icon"] = (
        lambda s: STATUS_ICONS.get(s, "")
    )
    app.state.templates = templates

    # ---- аутентификация ----

    def current_user(request: Request) -> dict:
        """Пользователь из cookie; None, если сессии нет или подпись битая."""
        token = request.cookies.get(COOKIE_NAME)
        if not token:
            return None
        try:
            user_id = int(signer.unsign(token, max_age=7 * 86400))
        except (BadSignature, ValueError):
            return None
        user = db.get_user(user_id)
        return user

    def require_user(request: Request) -> dict:
        user = current_user(request)
        if user is None:
            raise AuthRequired()
        return user

    def set_session(response: Response, user_id: int) -> None:
        response.set_cookie(
            COOKIE_NAME, signer.sign(str(user_id)).decode(),
            httponly=True, samesite="lax",
        )

    @app.exception_handler(AuthRequired)
    async def auth_redirect(request: Request, exc: AuthRequired):
        return RedirectResponse("/login", status_code=303)

    # ---- страницы ----

    @app.get("/login", response_class=HTMLResponse)
    async def login_form(request: Request):
        return templates.TemplateResponse(
            request, "login.html", {"error": None}
        )

    @app.post("/login", response_class=HTMLResponse)
    async def login_submit(request: Request):
        form = await request.form()
        user_id = db.verify_user(
            str(form.get("login", "")), str(form.get("password", ""))
        )
        if user_id is None:
            return templates.TemplateResponse(
                request, "login.html",
                {"error": "Неверный логин или пароль"},
                status_code=401,
            )
        response = RedirectResponse("/", status_code=303)
        set_session(response, user_id)
        return response

    @app.get("/logout")
    async def logout():
        response = RedirectResponse("/login", status_code=303)
        response.delete_cookie(COOKIE_NAME)
        return response

    @app.get("/", response_class=HTMLResponse)
    async def history(request: Request):
        user = require_user(request)
        estimates = db.list_estimates()
        today = date.today().isoformat()
        for meta in estimates:
            meta["overdue"] = bool(meta["deadline"]) \
                and meta["deadline"] < today
        # просроченные сверху; внутри групп — created_at desc
        estimates.sort(key=lambda m: m["created_at"], reverse=True)
        estimates.sort(key=lambda m: m["overdue"], reverse=True)
        return templates.TemplateResponse(
            request, "history.html",
            {"user": user, "estimates": estimates}
        )

    # ---- загрузка и расчёт ----

    def run_estimate_job(estimate_id: int, content: bytes,
                         set_step) -> int:
        """Тело job'ы: парсинг → цены → сохранение; вернуть estimate_id.

        Таблица Бориса собирается здесь, на каждый расчёт — с актуальными
        overrides из БД. backend/market читаются из app.state на момент
        исполнения — тесты подменяют их через app.state.*.
        """
        try:
            set_step("парсинг")
            items = parse_chatgpt_workbook(content)
            set_step("цены")
            try:
                priced = price_items(
                    items, boris=build_boris(),
                    backend=app.state.backend,
                    market=app.state.market, db=db,
                )
            except BackendUnavailableError:
                # 1С легла: пересчитываем без неё, ненайденное — красное
                # с пометкой «1С недоступна» (не падаем, расчёт живой).
                priced = price_items(
                    items, boris=build_boris(), backend=_NullBackend(),
                    market=app.state.market, db=db,
                )
                for item in priced:
                    if item.internal_price is None:
                        item.status = PriceStatus.MISSING
                        item.status_note = "1С недоступна"
            set_step("сохранение")
            db.save_items(estimate_id, priced)
            db.set_estimate_status(estimate_id, "done")
            return estimate_id
        except Exception:
            db.set_estimate_status(estimate_id, "error")
            raise

    @app.get("/upload", response_class=HTMLResponse)
    async def upload_form(request: Request):
        user = require_user(request)
        return templates.TemplateResponse(
            request, "upload.html", {"user": user}
        )

    @app.post("/upload")
    async def upload_submit(request: Request):
        user = require_user(request)
        form = await request.form()
        file: UploadFile = form.get("file")
        if file is None or not getattr(file, "filename", None):
            raise HTTPException(400, "нет файла")
        content = await file.read()
        meta = {
            "customer": str(form.get("customer", "")),
            "tender_no": str(form.get("tender_no", "")),
            "deadline": str(form.get("deadline", "") or ""),
            "source_filename": file.filename,
            "status": "running",
        }
        estimate_id = db.create_estimate(user["id"], meta)

        holder: dict = {}

        def set_step(step: str) -> None:
            if holder.get("job_id"):
                app.state.runner.set_step(holder["job_id"], step)

        def work() -> int:
            return run_estimate_job(estimate_id, content, set_step)

        holder["job_id"] = app.state.runner.submit(work)
        if app.state.runner.status(holder["job_id"])["state"] == "done":
            return RedirectResponse(
                f"/estimate/{estimate_id}", status_code=303
            )
        return RedirectResponse(
            f"/job/{holder['job_id']}/progress", status_code=303
        )

    @app.get("/job/{job_id}")
    async def job_status(request: Request, job_id: str):
        require_user(request)
        return JSONResponse(app.state.runner.status(job_id))

    @app.get("/job/{job_id}/progress", response_class=HTMLResponse)
    async def job_progress(request: Request, job_id: str):
        require_user(request)
        status = app.state.runner.status(job_id)
        if status["state"] == "done" and status["result_id"] is not None:
            return RedirectResponse(
                f"/estimate/{status['result_id']}", status_code=303
            )
        return templates.TemplateResponse(
            request, "progress.html",
            {"job_id": job_id, "status": status}
        )

    # ---- результат, правки, выгрузки ----

    @app.get("/estimate/{estimate_id}", response_class=HTMLResponse)
    async def estimate_page(request: Request, estimate_id: int):
        user = require_user(request)
        try:
            meta, items = db.get_estimate(estimate_id)
        except KeyError:
            raise HTTPException(404, "расчёт не найден")
        only = request.query_params.get("status")
        shown = items
        if only:
            shown = [i for i in items if i.status.value == only]
        summary = build_summary(items)
        grouped = {c: [i for i in shown if i.category == c]
                   for c in Category}
        similar = []
        if meta.get("status") == "done":
            metas = {m["id"]: m for m in db.list_estimates()}
            for est_id in find_similar_estimates(
                    db, items, meta.get("customer") or ""):
                # бейдж — только на более ранние расчёты («от <date>»)
                if est_id != estimate_id and est_id in metas \
                        and metas[est_id]["created_at"] \
                        < meta["created_at"]:
                    similar.append({
                        "id": est_id,
                        "created_at": metas[est_id]["created_at"][:10],
                    })
                if len(similar) >= 3:
                    break
        return templates.TemplateResponse(
            request, "result.html",
            {"user": user, "meta": meta,
             "items": items, "summary": summary, "grouped": grouped,
             "only": only, "editable_fields": EDITABLE_FIELDS,
             "similar": similar},
        )

    @app.post("/estimate/{estimate_id}/item/{row_id}/edit")
    async def edit_item(request: Request, estimate_id: int, row_id: str):
        user = require_user(request)
        form = await request.form()
        field = str(form.get("field", ""))
        value = str(form.get("value", ""))
        if field not in EDITABLE_FIELDS:
            raise HTTPException(400, f"неизвестное поле: {field!r}")
        try:
            db.get_estimate(estimate_id)
        except KeyError:
            raise HTTPException(404, "расчёт не найден")

        new_value: Union[str, Decimal, Category] = value
        if field in ("internal_price", "qty"):
            try:
                new_value = Decimal(value.replace(",", "."))
            except InvalidOperation:
                raise HTTPException(400, f"не число: {value!r}")
        elif field == "category":
            try:
                new_value = Category(value)
            except ValueError:
                raise HTTPException(400, f"нет такой категории: {value!r}")

        # Обновление + лог — одна транзакция (old value читается внутри неё)
        try:
            db.apply_edit_logged(estimate_id, row_id, user["id"], field,
                                 new_value)
        except KeyError:
            raise HTTPException(404, "строка не найдена")
        back = request.query_params.get("back")
        target = back if back and back.startswith("/estimate/") \
            else f"/estimate/{estimate_id}"
        return RedirectResponse(target, status_code=303)

    @app.get("/estimate/{estimate_id}/item/{row_id}/edits",
             response_class=HTMLResponse)
    async def item_edits(request: Request, estimate_id: int, row_id: str):
        user = require_user(request)
        edits = db.get_edits(estimate_id, row_id)
        for edit in edits:
            author = db.get_user(edit["user_id"]) if edit["user_id"] else None
            edit["user_name"] = author["display_name"] if author else "?"
        return templates.TemplateResponse(
            request, "edits.html",
            {"user": user, "edits": edits,
             "estimate_id": estimate_id, "row_id": row_id},
        )

    def _export(estimate_id: int, customer: bool) -> Response:
        try:
            meta, items = db.get_estimate(estimate_id)
        except KeyError:
            raise HTTPException(404, "расчёт не найден")
        meta_out = {
            "Заказчик": meta["customer"], "Тендер": meta["tender_no"],
            "Дедлайн": meta["deadline"], "Файл": meta["source_filename"],
        }
        data = (build_customer_xlsx if customer else build_internal_xlsx)(
            items, meta_out
        )
        name = "kp.xlsx" if customer else "internal.xlsx"
        return Response(
            data,
            media_type="application/vnd.openxmlformats-officedocument"
                       ".spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{name}"'},
        )

    @app.get("/estimate/{estimate_id}/export/internal.xlsx")
    async def export_internal(request: Request, estimate_id: int):
        require_user(request)
        return _export(estimate_id, customer=False)

    @app.get("/estimate/{estimate_id}/export/customer.xlsx")
    async def export_customer(request: Request, estimate_id: int):
        require_user(request)
        return _export(estimate_id, customer=True)

    # ---- админка глобальных цен ----

    def _boris_cell_map() -> dict[str, str]:
        """Карта ячеек сетки Бориса "group|thickness|kind|shape" → цена.

        Толщина — строка сетки после округления ("0.7", не "0.65"); ключ
        совпадает с ключом override в БД (массив из 4 строк).
        """
        cells = {}
        for shape, group, thickness, kind, price in build_boris().cells():
            # нормализация "0.80" → "0.8": ключ формы и ключ override — одна строка
            t = format(thickness.normalize(), "f")
            cells[f"{group}|{t}|{kind}|{shape}"] = str(price)
        return cells

    def _parse_decimal(raw: str) -> Decimal:
        """Decimal из формы; не-числа и NaN/±Infinity → 400.

        Decimal("nan")/("inf") парсятся без InvalidOperation — отсекаем
        по is_finite(), иначе яд отравит override/расценку/ручную цену.
        Отрицательные значения допустимы (скидочный override возможен).
        """
        try:
            value = Decimal(raw.replace(",", ".").strip())
        except InvalidOperation:
            raise HTTPException(400, f"не число: {raw!r}")
        if not value.is_finite():
            raise HTTPException(400, f"не число: {raw!r}")
        return value

    @app.get("/admin/prices", response_class=HTMLResponse)
    async def admin_prices(request: Request):
        user = require_user(request)
        overrides = []
        for key, price in sorted(db.get_overrides("boris").items()):
            try:
                material, thickness, shape, kind = json.loads(key)
            except (ValueError, TypeError):
                continue  # посторонний ключ в БД — не роняем страницу
            overrides.append({
                "material": material, "thickness": thickness,
                "shape": shape, "kind": kind, "price": price,
            })
        return templates.TemplateResponse(
            request, "admin_prices.html",
            {"user": user, "saved": request.query_params.get("saved"),
             "cells": _boris_cell_map(), "overrides": overrides,
             "work_rates": db.list_work_rates(),
             "manual_prices": db.list_manual_prices(),
             "material_labels": MATERIAL_GROUP_LABELS,
             "shape_labels": SHAPE_LABELS, "kind_labels": PRODUCT_KIND_LABELS},
        )

    @app.post("/admin/prices/boris")
    async def admin_boris_override(request: Request):
        user = require_user(request)
        form = await request.form()
        material = str(form.get("material", ""))
        thickness = str(form.get("thickness", ""))
        kind = str(form.get("product_kind", ""))
        shape = str(form.get("shape", ""))
        cells = _boris_cell_map()
        if f"{material}|{thickness}|{kind}|{shape}" not in cells:
            raise HTTPException(400, "такой ячейки нет в прайсе Бориса")
        price = _parse_decimal(str(form.get("price", "")))
        key_json = json.dumps([material, thickness, shape, kind],
                              ensure_ascii=False)
        db.set_override("boris", key_json, price, user["id"])
        return RedirectResponse("/admin/prices?saved=1", status_code=303)

    @app.post("/admin/prices/work-rates")
    async def admin_work_rate(request: Request):
        require_user(request)
        form = await request.form()
        name = str(form.get("name", "")).strip()
        if not name:
            raise HTTPException(400, "нет названия расценки")
        rate = _parse_decimal(str(form.get("rate", "")))
        coefficient = _parse_decimal(str(form.get("coefficient", "")))
        db.upsert_work_rate(
            name, str(form.get("unit", "")), rate, coefficient
        )
        return RedirectResponse("/admin/prices?saved=1", status_code=303)

    @app.post("/admin/prices/manual")
    async def admin_manual_price(request: Request):
        user = require_user(request)
        form = await request.form()
        name = str(form.get("name", "")).strip()
        if not name:
            raise HTTPException(400, "нет названия позиции")
        price = _parse_decimal(str(form.get("price", "")))
        db.set_manual_price(name, price, str(form.get("unit", "")),
                            user["id"])
        return RedirectResponse("/admin/prices?saved=1", status_code=303)

    return app


# ---- CLI ----

def main(argv=None) -> None:
    """Точка входа: создание пользователей.

    python -m presale.app --db presale.db --create-user igor secret "Игорь"
    """
    parser = argparse.ArgumentParser(description="Предварительный расчёт")
    parser.add_argument("--db", required=True, help="путь к SQLite")
    parser.add_argument("--create-user", nargs=3,
                        metavar=("LOGIN", "PASSWORD", "ИМЯ"),
                        help="создать пользователя и выйти")
    args = parser.parse_args(argv)
    db = PresaleDB(Path(args.db))
    if args.create_user:
        login, password, name = args.create_user
        user_id = db.create_user(login, password, name)
        print(f"пользователь {login} создан, id={user_id}")


if __name__ == "__main__":
    main()
