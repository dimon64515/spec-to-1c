"""SQLite-хранилище заявок web-загрузки заказов: заявки, позиции, метаданные."""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone

from report_xlsx import is_trading_skip

VALID_STATUSES = ("processing", "done", "error", "loaded_1c")

POSITION_KINDS = ("loaded", "skipped", "trading")


class Store:
    """Хранилище заявок: одна sqlite-база, миграция через CREATE TABLE IF NOT EXISTS."""

    def __init__(self, db_path: str):
        self._db_path = db_path
        parent = os.path.dirname(os.path.abspath(db_path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS requests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    file_name TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'processing',
                    meta TEXT,
                    header TEXT,
                    order_number TEXT,
                    error TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS positions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_id INTEGER NOT NULL,
                    kind TEXT NOT NULL,
                    idx INTEGER NOT NULL DEFAULT 0,
                    data TEXT NOT NULL
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def create_request(self, file_name: str) -> int:
        """Создать заявку в статусе processing, вернуть её id."""
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO requests (file_name, created_at, status) VALUES (?, ?, 'processing')",
                (file_name, datetime.now(timezone.utc).isoformat()),
            )
            return int(cur.lastrowid)

    def set_status(self, request_id: int, status: str, error: str | None = None) -> None:
        """Сменить статус заявки; error перезаписывается только при непустом значении."""
        if status not in VALID_STATUSES:
            raise ValueError(f"неизвестный статус: {status}")
        with self._connect() as conn:
            if error is not None:
                conn.execute(
                    "UPDATE requests SET status = ?, error = ? WHERE id = ?",
                    (status, error, request_id),
                )
            else:
                conn.execute(
                    "UPDATE requests SET status = ? WHERE id = ?",
                    (status, request_id),
                )

    def save_positions(self, request_id: int, loaded: list[dict], skipped: list[dict]) -> None:
        """Сохранить позиции пайплайна; trading-строки из skipped выделяются отдельно."""
        rows: list[tuple[str, int, str]] = []
        for idx, item in enumerate(loaded):
            rows.append(("loaded", idx, json.dumps(item, ensure_ascii=False)))
        skipped_idx = 0
        trading_idx = 0
        for item in skipped:
            kind = "trading" if is_trading_skip(item) else "skipped"
            idx = trading_idx if kind == "trading" else skipped_idx
            rows.append((kind, idx, json.dumps(item, ensure_ascii=False)))
            if kind == "trading":
                trading_idx += 1
            else:
                skipped_idx += 1
        with self._connect() as conn:
            for kind, idx, data in rows:
                conn.execute(
                    "INSERT INTO positions (request_id, kind, idx, data) VALUES (?, ?, ?, ?)",
                    (request_id, kind, idx, data),
                )

    def save_meta(self, request_id: int, meta: dict) -> None:
        """Сохранить meta пайплайна (escalation, llm_recovered, partial_warning и т.д.)."""
        with self._connect() as conn:
            conn.execute(
                "UPDATE requests SET meta = ? WHERE id = ?",
                (json.dumps(meta, ensure_ascii=False), request_id),
            )

    def save_header(self, request_id: int, header: dict) -> None:
        """Сохранить реквизиты заказа."""
        with self._connect() as conn:
            conn.execute(
                "UPDATE requests SET header = ? WHERE id = ?",
                (json.dumps(header, ensure_ascii=False), request_id),
            )

    def get_request(self, request_id: int) -> dict | None:
        """Заявка с распарсенными meta/header или None."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM requests WHERE id = ?", (request_id,)
            ).fetchone()
        if row is None:
            return None
        return self._request_row_to_dict(row)

    def list_requests(self) -> list[dict]:
        """Все заявки, новые первые, с подсчётом позиций и суммы (loaded+trading)."""
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM requests ORDER BY id DESC").fetchall()
            pos_rows = conn.execute("SELECT request_id, kind, data FROM positions").fetchall()
        counts: dict[int, dict[str, int]] = {}
        sums: dict[int, float] = {}
        for p in pos_rows:
            rid = p["request_id"]
            counters = counts.setdefault(rid, {k: 0 for k in POSITION_KINDS})
            counters[p["kind"]] = counters.get(p["kind"], 0) + 1
            if p["kind"] in ("loaded", "trading"):
                try:
                    d = json.loads(p["data"])
                except json.JSONDecodeError:
                    continue
                sums[rid] = sums.get(rid, 0.0) + float(d.get("sum") or 0)
        result = []
        for row in rows:
            item = self._request_row_to_dict(row)
            rid = item["id"]
            counters = counts.get(rid, {k: 0 for k in POSITION_KINDS})
            item["loaded_count"] = counters.get("loaded", 0)
            item["skipped_count"] = counters.get("skipped", 0)
            item["trading_count"] = counters.get("trading", 0)
            item["total_sum"] = sums.get(rid, 0.0)
            result.append(item)
        return result

    def get_positions(self, request_id: int, kind: str | None = None) -> list[dict]:
        """Позиции заявки, отсортированные по порядковому номеру внутри kind."""
        with self._connect() as conn:
            if kind is None:
                rows = conn.execute(
                    "SELECT kind, idx, data FROM positions WHERE request_id = ?"
                    " ORDER BY kind, idx, id",
                    (request_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT kind, idx, data FROM positions WHERE request_id = ? AND kind = ?"
                    " ORDER BY idx, id",
                    (request_id, kind),
                ).fetchall()
        return [json.loads(r["data"]) for r in rows]

    def update_position(self, request_id: int, kind: str, index: int, data: dict) -> None:
        """Заменить строку с порядковым номером index внутри kind (принятие аналога, мерж цен)."""
        with self._connect() as conn:
            conn.execute(
                "UPDATE positions SET data = ? WHERE request_id = ? AND kind = ? AND idx = ?",
                (json.dumps(data, ensure_ascii=False), request_id, kind, index),
            )

    def set_order_number(self, request_id: int, order_number: str) -> None:
        """Сохранить номер заказа 1С."""
        with self._connect() as conn:
            conn.execute(
                "UPDATE requests SET order_number = ? WHERE id = ?",
                (order_number, request_id),
            )

    @staticmethod
    def _request_row_to_dict(row: sqlite3.Row) -> dict:
        return {
            "id": row["id"],
            "file_name": row["file_name"],
            "created_at": row["created_at"],
            "status": row["status"],
            "meta": json.loads(row["meta"]) if row["meta"] else {},
            "header": json.loads(row["header"]) if row["header"] else {},
            "order_number": row["order_number"],
            "error": row["error"],
        }
