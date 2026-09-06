"""SQLite-очередь заданий бота: персистентность между рестартами, ретраи."""

from __future__ import annotations

import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Optional

MAX_ATTEMPTS = 3
RETRY_DELAYS = [5, 20, 60]  # паузы перед повтором, сек


@dataclass
class Job:
    dialog_id: str
    task_id: Optional[int]
    pdf_path: str
    file_name: str
    order_comment: str
    id: int = 0
    attempts: int = 0


class JobQueue:
    def __init__(self, db_path: str | Path, tmp_dir: str | Path):
        self._db_path = str(db_path)
        self._tmp_dir = Path(tmp_dir)
        self._tmp_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    dialog_id TEXT NOT NULL,
                    task_id INTEGER,
                    pdf_path TEXT NOT NULL,
                    file_name TEXT NOT NULL,
                    order_comment TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'pending',
                    error TEXT
                )
                """
            )
            # рестарт сервиса: вернуть прерванные задания в очередь
            conn.execute("UPDATE jobs SET status = 'pending' WHERE status = 'running'")

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def _row_to_job(row: sqlite3.Row) -> Job:
        return Job(
            id=row["id"],
            dialog_id=row["dialog_id"],
            task_id=row["task_id"],
            pdf_path=row["pdf_path"],
            file_name=row["file_name"],
            order_comment=row["order_comment"],
            attempts=row["attempts"],
        )

    def enqueue(self, job: Job, pdf_bytes: Optional[bytes] = None) -> Job:
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO jobs (dialog_id, task_id, pdf_path, file_name, order_comment)"
                " VALUES (?, ?, ?, ?, ?)",
                (job.dialog_id, job.task_id, job.pdf_path, job.file_name, job.order_comment),
            )
            job_id = cur.lastrowid
            if pdf_bytes is not None:
                pdf_path = self._tmp_dir / f"{job_id}.pdf"
                pdf_path.write_bytes(pdf_bytes)
                conn.execute(
                    "UPDATE jobs SET pdf_path = ? WHERE id = ?", (str(pdf_path), job_id)
                )
            job.id = job_id
            job.pdf_path = str(self._tmp_dir / f"{job_id}.pdf")
            return job

    def get(self, job_id: int) -> Job:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            raise KeyError(f"job {job_id} not found")
        return self._row_to_job(row)

    def next_pending(self) -> Optional[Job]:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM jobs WHERE status = 'pending' ORDER BY id LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            conn.execute(
                "UPDATE jobs SET status = 'running' WHERE id = ?", (row["id"],)
            )
            return self._row_to_job(row)

    def complete(self, job_id: int) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("UPDATE jobs SET status = 'done' WHERE id = ?", (job_id,))

    def reschedule(self, job_id: int, error: str) -> None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT attempts FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
            attempts = row["attempts"] + 1
            status = "pending" if attempts < MAX_ATTEMPTS else "failed"
            conn.execute(
                "UPDATE jobs SET status = ?, attempts = ?, error = ? WHERE id = ?",
                (status, attempts, error[:500], job_id),
            )

    def stats(self) -> Dict[str, int]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT status, COUNT(*) AS n FROM jobs GROUP BY status"
            ).fetchall()
        stats = {r["status"]: r["n"] for r in rows}
        for status in ("pending", "running", "done", "failed"):
            stats.setdefault(status, 0)
        return stats


def run_worker(
    queue: JobQueue,
    handler: Callable[[Job], None],
    stop_event: Optional[threading.Event] = None,
    poll_seconds: float = 2.0,
) -> None:
    """Блокирующий цикл воркера: handler(job); исключение -> reschedule."""
    stop_event = stop_event or threading.Event()
    while not stop_event.is_set():
        job = queue.next_pending()
        if job is None:
            time.sleep(poll_seconds)
            continue
        try:
            handler(job)
            queue.complete(job.id)
        except Exception as exc:  # noqa: BLE001 - любой сбой -> ретрай
            queue.reschedule(job.id, f"{type(exc).__name__}: {exc}")
            # пауза перед повторной обработкой (5с/20с/60с по номеру попытки);
            # stop_event.wait прерывается при остановке воркера
            delay = RETRY_DELAYS[min(job.attempts + 1, len(RETRY_DELAYS)) - 1]
            if stop_event.wait(delay):
                return
