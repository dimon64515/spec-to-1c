"""FastAPI-приложение веб-интерфейса заявок: загрузка, просмотр, 1С, экспорт."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from bitrix_bot.pipeline import PipelineResult
from generate_order_xml import generate_order_xml
from map_customer_equipment import load_mapping
from report_xlsx import build_excel_report
from web_ui import analogs
from web_ui.runner import apply_analog, load_to_1c, run_processing
from web_ui.store import Store

BASE_DIR = Path(__file__).parent

HEADER_KEYS = ("numberDate", "numberOrder", "INN", "customer", "email", "phone", "contact")

XLSX_MEDIA_TYPE = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)


def _price_engine(app: FastAPI):
    """Движок дилерских цен, создаётся один раз; при недоступности price_search — None."""
    engine = getattr(app.state, "price_engine", "unset")
    if engine == "unset":
        try:
            from price_search.ui import get_engine

            engine = get_engine()
        except Exception:
            # price_search недоступен (зависимости/сеть) — заявка обработается без цен
            engine = None
        app.state.price_engine = engine
    return engine


def create_app(db_path: str = "tmp/web_ui/web_ui.db") -> FastAPI:
    """Приложение web_ui; Store живёт в app.state (lifespan)."""
    templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.store = Store(db_path)
        yield

    app = FastAPI(lifespan=lifespan)
    app.state.db_path = db_path
    app.mount(
        "/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static"
    )

    def _store(request: Request) -> Store:
        return request.app.state.store

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        """Список заявок, новые первые."""
        return templates.TemplateResponse(
            request,
            "index.html",
            {"requests": _store(request).list_requests()},
        )

    @app.post("/upload")
    async def upload(request: Request, file: UploadFile):
        """Загрузка PDF: заявка в processing, обработка — фоновой задачей."""
        pdf_bytes = await file.read()
        store = _store(request)
        file_name = file.filename or "file.pdf"
        rid = store.create_request(file_name)
        asyncio.create_task(
            run_processing(store, rid, file_name, pdf_bytes, _price_engine(request.app))
        )
        return RedirectResponse(f"/r/{rid}", status_code=303)

    @app.get("/r/{request_id}", response_class=HTMLResponse)
    async def request_page(request: Request, request_id: int):
        """Детали заявки: KPI, позиции, форма реквизитов, действия."""
        store = _store(request)
        req = store.get_request(request_id)
        if req is None:
            return HTMLResponse("Заявка не найдена", status_code=404)
        skipped = store.get_positions(request_id, "skipped")
        loaded = store.get_positions(request_id, "loaded")
        trading = store.get_positions(request_id, "trading")
        loaded_sum = sum(float(p.get("sum") or 0) for p in loaded)
        mapping = load_mapping()
        suggestions = []
        for row in skipped:
            try:
                suggestions.append(analogs.suggest_analog(row, mapping))
            except Exception:
                suggestions.append(None)
        return templates.TemplateResponse(
            request,
            "request.html",
            {
                "req": req,
                "loaded": loaded,
                "trading": trading,
                "skipped": skipped,
                "loaded_sum": loaded_sum,
                "suggestions": suggestions,
                "header_keys": HEADER_KEYS,
            },
        )

    @app.get("/api/status/{request_id}")
    async def api_status(request: Request, request_id: int):
        """JSON-статус для polling со страницы заявки."""
        req = _store(request).get_request(request_id)
        if req is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        return {"status": req["status"], "error": req["error"]}

    @app.post("/r/{request_id}/load-1c")
    async def load_1c_route(request: Request, request_id: int):
        """Сохранить реквизиты и загрузить заказ в 1С одной кнопкой."""
        store = _store(request)
        form = await request.form()
        header = {k: str(form.get(k) or "") for k in HEADER_KEYS}
        store.save_header(request_id, header)
        try:
            load_to_1c(store, request_id, header)
        except ValueError:
            # уже loaded_1c или нет заявки — страница покажет актуальное состояние
            pass
        return RedirectResponse(f"/r/{request_id}", status_code=303)

    @app.post("/r/{request_id}/analog/{index}")
    async def analog_route(request: Request, request_id: int, index: int):
        """Принять аналог для skipped-строки; при ошибке строка помечается, UI не падает."""
        store = _store(request)
        try:
            apply_analog(store, request_id, index)
        except (ValueError, analogs.AnalogError):
            pass
        return RedirectResponse(f"/r/{request_id}", status_code=303)

    @app.get("/r/{request_id}/xml")
    async def xml_route(request: Request, request_id: int):
        """Экспорт XML для импорта в 1С (доступен и при статусе error)."""
        store = _store(request)
        req = store.get_request(request_id)
        if req is None:
            return HTMLResponse("Заявка не найдена", status_code=404)
        xml = generate_order_xml(req["header"] or {}, store.get_positions(request_id, "loaded"))
        return Response(xml, media_type="application/xml")

    @app.get("/r/{request_id}/report.xlsx")
    async def report_route(request: Request, request_id: int):
        """xlsx-отчёт по заявке (переиспользуем report_xlsx)."""
        store = _store(request)
        req = store.get_request(request_id)
        if req is None:
            return HTMLResponse("Заявка не найдена", status_code=404)
        result = PipelineResult(
            file_name=req["file_name"],
            order_number=req["order_number"],
            loaded=store.get_positions(request_id, "loaded"),
            skipped=(
                store.get_positions(request_id, "skipped")
                + store.get_positions(request_id, "trading")
            ),
        )
        content = build_excel_report(result)
        return Response(
            content,
            media_type=XLSX_MEDIA_TYPE,
            headers={
                "Content-Disposition": f'attachment; filename="report_{request_id}.xlsx"'
            },
        )

    return app


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(create_app(), host="127.0.0.1", port=8092)
