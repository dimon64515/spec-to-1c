"""Тесты SQLite-очереди заданий."""
import threading
import time

import pytest

from bitrix_bot.queue import Job, JobQueue, MAX_ATTEMPTS, run_worker


@pytest.fixture()
def queue(tmp_path):
    return JobQueue(tmp_path / "jobs.db", tmp_path / "files")


def _job(**kw):
    base = dict(
        dialog_id="task|1",
        task_id=1,
        pdf_path="",
        file_name="spec.pdf",
        order_comment="c",
    )
    base.update(kw)
    return Job(**base)


def test_enqueue_stores_pdf_and_roundtrip(queue, tmp_path):
    job = queue.enqueue(_job(), pdf_bytes=b"%PDF-abc")
    assert (tmp_path / "files" / f"{job.id}.pdf").read_bytes() == b"%PDF-abc"
    got = queue.get(job.id)
    assert got.dialog_id == "task|1"
    assert queue.stats()["pending"] == 1


def test_next_pending_marks_running_and_complete(queue):
    job = queue.enqueue(_job())
    assert queue.next_pending().id == job.id
    assert queue.next_pending() is None  # уже running
    queue.complete(job.id)
    assert queue.stats()["done"] == 1


def test_reschedule_retries_then_fails(queue):
    job = queue.enqueue(_job())
    queue.next_pending()
    for _ in range(MAX_ATTEMPTS - 1):
        queue.reschedule(job.id, "boom")
        assert queue.next_pending() is not None
        queue.stats()
    queue.reschedule(job.id, "boom")  # последняя попытка исчерпана
    assert queue.next_pending() is None
    stats = queue.stats()
    assert stats["failed"] == 1


def test_persistence_across_instances(tmp_path):
    q1 = JobQueue(tmp_path / "jobs.db", tmp_path / "files")
    job = q1.enqueue(_job())
    q2 = JobQueue(tmp_path / "jobs.db", tmp_path / "files")
    assert q2.stats()["pending"] == 1
    assert q2.get(job.id).file_name == "spec.pdf"


def test_run_worker_processes_job(queue):
    done = []
    stop = threading.Event()

    def handler(job):
        done.append(job.id)
        stop.set()

    queue.enqueue(_job())
    run_worker(queue, handler, stop_event=stop, poll_seconds=0.05)
    assert len(done) == 1
    assert queue.stats()["done"] == 1


def test_run_worker_reschedules_on_error(queue):
    stop = threading.Event()
    calls = []

    def handler(job):
        calls.append(job.attempts)
        if len(calls) >= 2:
            stop.set()
        raise RuntimeError("transient")

    queue.enqueue(_job())
    run_worker(queue, handler, stop_event=stop, poll_seconds=0.05)
    assert len(calls) == 2
    stats = queue.stats()
    assert stats["pending"] + stats["failed"] == 1
