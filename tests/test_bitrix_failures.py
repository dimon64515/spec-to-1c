"""Регрессии бесшумных сбоев бота (07.09.2026): файлы без ответа в чате."""
import threading

from bitrix_bot.queue import Job, JobQueue, run_worker
from process_specification_table import process_rows
from report_xlsx import is_trading_skip


def test_process_rows_broken_row_goes_to_skipped_not_kills_file(monkeypatch):
    # Одна успешная строка пайплайна без обязательного параметра (job 38:
    # «Для артикула 8-2-1 не хватает параметра A0») — раньше роняла весь
    # process_rows, задание падало 3 раза, пользователь не получал ответа.
    import process_specification_table as pst

    good = {"article": "1-2-1", "params": {"A0": 300, "B0": 200},
            "quantity": 5, "material_code": "1", "thickness": 0.8,
            "connection_0": "6", "connection_1": "6",
            "connection_2": "0", "connection_3": "0",
            "system": "", "comment": "Воздуховод 300x200"}
    bad = {"article": "8-2-1", "params": {"B0": 300, "L0": 150},
           "quantity": 1, "material_code": "1", "thickness": 0.8,
           "connection_0": "2", "connection_1": "2",
           "connection_2": "0", "connection_3": "0",
           "system": "", "comment": "Врезка прямоугольная 300"}
    monkeypatch.setattr(pst, "parse_row", lambda row, defaults: (dict(bad), None)
                        if "Врезка" in row.get("name", "")
                        else (dict(good), None))

    xml, skipped, success = process_rows(
        [{"name": "Воздуховод"}, {"name": "Врезка прямоугольная"}])

    assert [r["article"] for r in success] == ["1-2-1"]
    assert len(skipped) == 1
    assert skipped[0]["article"] == "8-2-1"
    assert "A0" in skipped[0]["reason"]
    # XML построен по валидным строкам
    assert "1-2-1" in xml and "8-2-1" not in xml


def test_pipeline_blocks_article_20_2(monkeypatch):
    # 20-2 заведомо роняет Записать() в 1С (дефект ПередЗаписью) — одна такая
    # строка обвалила весь Шипиловский (job 39). Теперь уходит в skipped.
    import sys

    import bitrix_bot.pipeline as pl
    import process_specification_table as pst

    def fake_process_rows(rows):
        success = [
            {"article": "1-2-1", "params": {"A0": 300, "B0": 200},
             "quantity": 5, "material_code": "1", "thickness": 0.8,
             "comment": "Воздуховод 300x200"},
            {"article": "20-2", "params": {"A0": 600, "B0": 600},
             "quantity": 2, "material_code": "1", "thickness": 0.8,
             "comment": "Клапан противопожарный 600x600"},
        ]
        return "", [], success

    monkeypatch.setattr(pst, "process_rows", fake_process_rows)
    # process_pdf_to_positions импортирует api внутри функции — подменяем
    # модуль, чтобы вернуть block_rows без чтения настоящего PDF
    fake_api = type(sys)("api")
    fake_api.load_tables_from_pdf = lambda b: {"block_rows": [{}]}
    monkeypatch.setitem(sys.modules, "api", fake_api)

    success, skipped = pl.process_pdf_to_positions(b"pdf")
    assert [r["article"] for r in success] == ["1-2-1"]
    assert len(skipped) == 1
    assert skipped[0]["article"] == "20-2"
    assert "20-2" in skipped[0]["reason"]
    # не должно улетать в «Перекупное»: это производимая номенклатура
    assert is_trading_skip(skipped[0]) is False


def test_run_worker_notifies_on_final_failure(tmp_path):
    # Попытки исчерпаны — пользователь получает сообщение об ошибке,
    # а не молчаливое «обрабатываю…» (jobs 35/38).
    queue = JobQueue(tmp_path / "jobs.db", tmp_path / "files")
    job = queue.enqueue(Job(dialog_id="305", task_id=None, pdf_path="",
                            file_name="spec.pdf", order_comment="c"))
    failures = []
    stop = threading.Event()

    def handler(j):
        raise ValueError("Для артикула 8-2-1 не хватает параметра A0")

    def on_failure(j, error):
        failures.append((j.id, error))
        stop.set()

    run_worker(queue, handler, stop_event=stop, poll_seconds=0.05,
               on_failure=on_failure)

    assert queue.stats()["failed"] == 1
    assert failures == [(job.id,
                         "ValueError: Для артикула 8-2-1 не хватает параметра A0")]


def test_reschedule_returns_new_status(tmp_path):
    queue = JobQueue(tmp_path / "jobs.db", tmp_path / "files")
    job = queue.enqueue(Job(dialog_id="1", task_id=None, pdf_path="",
                            file_name="f.pdf", order_comment="c"))
    queue.next_pending()
    assert queue.reschedule(job.id, "boom") == "pending"
    assert queue.reschedule(job.id, "boom") == "pending"
    assert queue.reschedule(job.id, "boom") == "failed"
