"""FastAPI-сервис бота: webhook от Битрикса + фоновый воркер очереди."""

from __future__ import annotations

import logging
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Callable, Optional

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from bitrix_bot.bitrix_client import BitrixClient
from bitrix_bot.config import BotConfig, load_bot_config
from bitrix_bot.events import PdfNotFound, find_pdf, parse_event
from bitrix_bot.pipeline import run_pipeline
from bitrix_bot.queue import Job, JobQueue, run_worker
from bitrix_bot.report import build_report

logger = logging.getLogger(__name__)


def make_handler(cfg: BotConfig, client) -> Callable[[Job], None]:
    """Обработчик задания очереди: пайплайн -> отчёт в чат задачи."""
    def handle(job: Job) -> None:
        pdf_bytes = Path(job.pdf_path).read_bytes()
        res = run_pipeline(
            pdf_bytes, job.file_name, job.order_comment,
            cfg.execute_code_url, timeout=cfg.request_timeout,
        )
        for msg in build_report(res, cfg.report_limit):
            try:
                client.send_message(job.dialog_id, msg)
            except Exception:
                # заказ в 1С уже создан — сбой доставки отчёта не должен
                # приводить к reschedule (иначе дубликат заказа)
                logger.exception("report delivery failed for job %s", job.id)
    return handle


def create_app(
    cfg: Optional[BotConfig] = None,
    client=None,
    queue: Optional[JobQueue] = None,
    start_worker: bool = True,
) -> FastAPI:
    cfg = cfg or load_bot_config()
    client = client or BitrixClient(cfg.incoming_webhook, timeout=cfg.request_timeout)
    queue = queue or JobQueue(
        Path(cfg.tmp_dir) / "jobs.db", cfg.tmp_dir
    )

    worker_stop: Optional[threading.Event] = None

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        nonlocal worker_stop
        worker_stop = threading.Event()
        thread = None
        if start_worker:
            handler = make_handler(cfg, client)
            thread = threading.Thread(
                target=run_worker, args=(queue, handler, worker_stop),
                daemon=True, name="bitrix-bot-worker",
            )
            thread.start()
        yield
        worker_stop.set()
        if thread:
            thread.join(timeout=5)

    app = FastAPI(title="spec-to-1c bitrix bot", lifespan=lifespan)

    @app.post("/webhook/bot")
    async def webhook(request: Request, background: BackgroundTasks):
        if cfg.verify_token:
            token = request.headers.get("X-Webhook-Token", "")
            if token != cfg.verify_token:
                raise HTTPException(status_code=401, detail="bad token")
        try:
            payload = await request.json()
            event = parse_event(payload)
            if event is None:
                return JSONResponse({"ok": True})
            try:
                pdf_bytes, file_name = find_pdf(client, event)
            except PdfNotFound as exc:
                background.add_task(client.send_message, event.dialog_id, str(exc))
                return JSONResponse({"ok": True})
            title = client.get_task_title(event.task_id) if event.task_id else ""
            if event.task_id:
                comment = f"{cfg.task_comment_prefix} №{event.task_id}: {title}"
            else:
                comment = cfg.task_comment_prefix
            job = queue.enqueue(
                Job(
                    dialog_id=event.dialog_id,
                    task_id=event.task_id,
                    pdf_path="",
                    file_name=file_name,
                    order_comment=comment,
                ),
                pdf_bytes=pdf_bytes,
            )
        except Exception:
            # webhook обязан отвечать быстрым 200; сбой обработки не должен
            # уходить Битриксу как 500 (он ретраит доставку события)
            logger.exception("webhook processing failed")
            return JSONResponse({"ok": True})
        background.add_task(
            client.send_message, event.dialog_id,
            f"Принял «{file_name}», обрабатываю…",
        )
        return JSONResponse({"ok": True, "job_id": job.id})

    @app.get("/health")
    async def health():
        return {"ok": True, "stats": queue.stats()}

    return app


app = create_app()
