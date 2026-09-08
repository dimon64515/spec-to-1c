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
from bitrix_bot.events import PdfNotFound, find_pdf, form_payload, parse_event
from bitrix_bot.pipeline import recreate_order_from_report, run_pipeline
from bitrix_bot.queue import MAX_ATTEMPTS, Job, JobQueue, run_worker
from bitrix_bot.report import build_report, build_summary
from report_xlsx import EditedReportError, build_excel_report

logger = logging.getLogger(__name__)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

WELCOME_TEXT = (
    "Здравствуйте! Я создаю заказы в 1С из PDF-спецификаций (воздуховоды, фасонные части, клапаны).\n\n"
    "Как работать со мной:\n"
    "1. Прикрепите PDF со спецификацией к сообщению.\n"
    "2. Отправьте файл мне напрямую в этот чат "
    "или ответьте (reply) на сообщение с файлом и упомяните меня @.\n"
    "3. Я отвечу: номер созданного заказа 1С, сводку и список пропущенных позиций.\n"
    "4. Исправили что-то в отчёте? Ответьте мне исправленным Excel-файлом (.xlsx) — "
    "пересоздам заказ под новым номером (старый удалите вручную).\n\n"
    "PDF без таблицы оборудования не обработаю — проверяйте, что в файле есть спецификация."
)


def make_handler(cfg: BotConfig, client) -> Callable[[Job], None]:
    """Обработчик задания очереди: пайплайн -> Excel-файл с резюме в чат;
    при сбое доставки файла — фолбэк на текстовую нарезку."""
    def handle(job: Job) -> None:
        data = Path(job.pdf_path).read_bytes()
        if job.file_name.lower().endswith(".xlsx"):
            # отредактированный Excel-отчёт: round-trip — пересоздаём заказ
            try:
                res = recreate_order_from_report(
                    data, cfg.execute_code_url, job.order_comment,
                    timeout=cfg.request_timeout,
                )
            except EditedReportError as e:
                # детерминированная валидация отчёта: reschedule бессмысленен —
                # повторная попытка даст тот же отказ. Отвечаем в чат и выходим.
                logger.exception("edited report rejected for job %s", job.id)
                try:
                    client.send_message(
                        job.dialog_id, f"Не смог обработать отчёт: {e}",
                        bot_id=job.bot_id,
                    )
                except Exception:
                    logger.exception("error message delivery failed for job %s", job.id)
                return
        else:
            res = run_pipeline(
                data, job.file_name, job.order_comment,
                cfg.execute_code_url, timeout=cfg.request_timeout,
            )
        try:
            xlsx = build_excel_report(res)
            client.send_file(
                job.dialog_id,
                f"report_order_{res.order_number or 'new'}.xlsx",
                xlsx, build_summary(res), bot_id=job.bot_id,
            )
        except Exception:
            # заказ в 1С уже создан — сбой доставки отчёта не должен
            # приводить к reschedule (иначе дубликат заказа)
            logger.exception("excel report delivery failed for job %s", job.id)
            for msg in build_report(res, cfg.report_limit):
                try:
                    client.send_message(job.dialog_id, msg, bot_id=job.bot_id)
                except Exception:
                    logger.exception("report delivery failed for job %s", job.id)
    return handle


def create_app(
    cfg: Optional[BotConfig] = None,
    client=None,
    queue: Optional[JobQueue] = None,
    start_worker: bool = True,
) -> FastAPI:
    cfg = cfg or load_bot_config()
    client = client or BitrixClient(cfg.incoming_webhook, timeout=cfg.request_timeout,
                                    app_client_id=cfg.client_id)
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

            def notify_failure(job: Job, error: str) -> None:
                # попытки исчерпаны — без ответа пользователь остаётся
                # с «обрабатываю…» и ничем
                try:
                    client.send_message(
                        job.dialog_id,
                        f"Не смог обработать «{job.file_name}» "
                        f"({MAX_ATTEMPTS} попытки): {error}",
                        bot_id=job.bot_id,
                    )
                except Exception:
                    logger.exception(
                        "failure notification failed for job %s", job.id)

            thread = threading.Thread(
                target=run_worker,
                args=(queue, handler, worker_stop),
                kwargs={"on_failure": notify_failure},
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
            if request.headers.get("content-type", "").startswith(
                "application/x-www-form-urlencoded"
            ):
                payload = form_payload(await request.body())
            else:
                payload = await request.json()
            logger.info("payload: %s", payload)  # временно: сверка схемы событий
            if payload.get("event") == "ONIMBOTJOINCHAT":
                params = ((payload.get("data") or {}).get("PARAMS")) or {}
                dialog_id = params.get("DIALOG_ID", "")
                bots = (payload.get("data") or {}).get("BOT") or []
                bot_id = int(bots[0]["BOT_ID"]) if bots else None
                if dialog_id:
                    background.add_task(
                        client.send_message, dialog_id, WELCOME_TEXT, bot_id=bot_id
                    )
                return JSONResponse({"ok": True})
            event = parse_event(payload)
            if event is None:
                return JSONResponse({"ok": True})
            if not event.file_url and not event.is_reply:
                # простое сообщение без файла и без reply: не подхватываем
                # старые файлы из истории — только короткая инструкция
                background.add_task(client.send_message, event.dialog_id,
                                    WELCOME_TEXT, bot_id=event.bot_id)
                return JSONResponse({"ok": True})
            is_excel = (event.file_name or "").lower().endswith(".xlsx")
            try:
                if is_excel:
                    pdf_bytes, file_name = find_pdf(client, event, ext=".xlsx")
                else:
                    pdf_bytes, file_name = find_pdf(client, event)
            except PdfNotFound:
                background.add_task(client.send_message, event.dialog_id,
                                    WELCOME_TEXT, bot_id=event.bot_id)
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
                    bot_id=event.bot_id,
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
            bot_id=event.bot_id,
        )
        return JSONResponse({"ok": True, "job_id": job.id})

    @app.get("/health")
    async def health():
        return {"ok": True, "stats": queue.stats()}

    return app


app = create_app()
