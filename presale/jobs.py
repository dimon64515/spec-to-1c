"""Фоновые задачи предварительного расчёта: поток на задачу, статус в памяти.

JobRunner.submit(fn, *args) запускает fn в daemon-потоке и возвращает job_id;
status(job_id) отдаёт {"state", "step", "error", "result_id"}.
Шаги задачи — «парсинг», «цены», «сохранение», финальные «done»/«error».
JobRunner(sync=True) исполняет задачу синхронно внутри submit — для тестов.
"""
import logging
import secrets
import threading
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


class JobRunner:
    """Поток на задачу; состояние — dict в памяти под threading.Lock."""

    def __init__(self, sync: bool = False):
        self._sync = sync
        self._lock = threading.Lock()
        self._jobs: dict[str, dict] = {}

    def submit(self, fn: Callable[..., Any], *args: Any) -> str:
        """Запустить fn(*args) в потоке; вернуть job_id."""
        job_id = secrets.token_hex(8)
        with self._lock:
            self._jobs[job_id] = {
                "state": "pending", "step": "ожидание",
                "error": None, "result_id": None,
            }
        if self._sync:
            self._run(job_id, fn, args)
        else:
            thread = threading.Thread(
                target=self._run, args=(job_id, fn, args), daemon=True
            )
            thread.start()
        return job_id

    def set_step(self, job_id: str, step: str) -> None:
        """Обновить строку шага выполняющейся задачи."""
        with self._lock:
            if job_id in self._jobs:
                self._jobs[job_id]["step"] = step

    def status(self, job_id: str) -> dict:
        """Состояние задачи; неизвестный id — {"state": "missing", ...}."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return {"state": "missing", "step": "", "error": None,
                        "result_id": None}
            return dict(job)

    def _run(self, job_id: str, fn: Callable[..., Any], args: tuple) -> None:
        with self._lock:
            self._jobs[job_id]["state"] = "running"
        try:
            result = fn()
        except Exception as exc:
            logger.exception("job %s упал", job_id)
            with self._lock:
                self._jobs[job_id].update(
                    state="error", step="error", error=str(exc)
                )
            return
        with self._lock:
            self._jobs[job_id].update(
                state="done", step="done", result_id=result
            )
