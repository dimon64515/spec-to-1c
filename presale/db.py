"""SQLite-хранилище предварительного расчёта.

Подключение открывается на каждый вызов метода: короткие транзакции
не блокируют друг друга, два пользователя могут править расчёты
одновременно (WAL + короткие записи). Все денежные значения хранятся
как TEXT (`str(Decimal)`), при чтении возвращаются как `Decimal`.
"""
import hashlib
import json
import re
import secrets
import sqlite3
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Optional, Union

from presale.models import EstimateItem

_WAL = "PRAGMA journal_mode=WAL"
_FK = "PRAGMA foreign_keys=ON"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    login TEXT NOT NULL UNIQUE,
    pass_hash TEXT NOT NULL,
    display_name TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS estimates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id INTEGER NOT NULL REFERENCES users(id),
    title TEXT NOT NULL DEFAULT '',
    customer TEXT NOT NULL DEFAULT '',
    tender_no TEXT NOT NULL DEFAULT '',
    deadline TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    source_filename TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'draft'
);
CREATE TABLE IF NOT EXISTS estimate_items (
    estimate_id INTEGER NOT NULL REFERENCES estimates(id) ON DELETE CASCADE,
    row_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    PRIMARY KEY (estimate_id, row_id)
);
CREATE TABLE IF NOT EXISTS item_edits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    estimate_id INTEGER NOT NULL REFERENCES estimates(id) ON DELETE CASCADE,
    row_id TEXT NOT NULL,
    user_id INTEGER,
    field TEXT NOT NULL,
    old_value TEXT,
    new_value TEXT,
    ts TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS manual_prices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    normalized_name TEXT NOT NULL UNIQUE,
    price TEXT NOT NULL,
    unit TEXT NOT NULL DEFAULT '',
    set_by INTEGER,
    ts TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS work_rates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    unit TEXT NOT NULL DEFAULT '',
    rate TEXT NOT NULL,
    coefficient TEXT NOT NULL DEFAULT '1'
);
CREATE TABLE IF NOT EXISTS price_overrides (
    scope TEXT NOT NULL,
    key_json TEXT NOT NULL,
    price TEXT NOT NULL,
    set_by INTEGER,
    ts TEXT NOT NULL,
    PRIMARY KEY (scope, key_json)
);
"""

_WS_RE = re.compile(r"\s+")


def _normalize(name: str) -> str:
    """Нормализация имени позиции: casefold + схлопывание пробелов."""
    return _WS_RE.sub(" ", name).strip().casefold()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class PresaleDB:
    """Хранилище расчётов, правок, ручных цен и переопределений прайсов."""

    def __init__(self, path: Path):
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path, timeout=30)
        conn.execute(_WAL)
        conn.execute(_FK)
        conn.row_factory = sqlite3.Row
        return conn

    # ---- пользователи ----

    def create_user(self, login: str, password: str, display_name: str) -> int:
        """Создаёт пользователя; возвращает id. Дубль логина — исключение."""
        salt = secrets.token_bytes(16)
        pass_hash = self._hash_password(password, salt)
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO users (login, pass_hash, display_name) VALUES (?, ?, ?)",
                (login, pass_hash, display_name),
            )
            return int(cur.lastrowid)

    def verify_user(self, login: str, password: str) -> Optional[int]:
        """Проверяет пароль; возвращает id пользователя или None."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id, pass_hash FROM users WHERE login = ?", (login,)
            ).fetchone()
        if row is None:
            return None
        salt_hex, hash_hex = row["pass_hash"].split(":", 1)
        candidate = self._hash_password(password, bytes.fromhex(salt_hex))
        return int(row["id"]) if candidate.split(":", 1)[1] == hash_hex else None

    @staticmethod
    def _hash_password(password: str, salt: bytes) -> str:
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), salt, 100_000
        ).hex()
        return f"{salt.hex()}:{digest}"

    # ---- расчёты ----

    def create_estimate(self, owner_id: int, meta: dict) -> int:
        """Создаёт пустой расчёт из meta (title, customer, tender_no, ...)."""
        fields = ("title", "customer", "tender_no", "deadline",
                  "source_filename", "status")
        data = {f: str(meta.get(f, "")) for f in fields}
        if not data["status"]:
            data["status"] = "draft"
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO estimates (owner_id, title, customer, tender_no,"
                " deadline, created_at, source_filename, status)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (owner_id, data["title"], data["customer"], data["tender_no"],
                 data["deadline"], _now(), data["source_filename"],
                 data["status"]),
            )
            return int(cur.lastrowid)

    def save_items(self, estimate_id: int, items: list[EstimateItem]) -> None:
        """Полностью заменяет строки расчёта (payload_json на каждую строку)."""
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM estimate_items WHERE estimate_id = ?", (estimate_id,)
            )
            conn.executemany(
                "INSERT INTO estimate_items (estimate_id, row_id, payload_json)"
                " VALUES (?, ?, ?)",
                [
                    (estimate_id, item.row_id, item.model_dump_json())
                    for item in items
                ],
            )

    def get_estimate(self, estimate_id: int) -> tuple[dict, list[EstimateItem]]:
        """Возвращает (meta, строки) расчёта; падает, если расчёта нет."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM estimates WHERE id = ?", (estimate_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"расчёт {estimate_id} не найден")
            rows = conn.execute(
                "SELECT payload_json FROM estimate_items"
                " WHERE estimate_id = ? ORDER BY row_id",
                (estimate_id,),
            ).fetchall()
        meta = dict(row)
        items = [EstimateItem.model_validate_json(r["payload_json"]) for r in rows]
        return meta, items

    def list_estimates(self) -> list[dict]:
        """Список расчётов (meta) от старых к новым."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM estimates ORDER BY id"
            ).fetchall()
        return [dict(r) for r in rows]

    # ---- правки ----

    def log_edit(self, estimate_id: int, row_id: str, user_id: int,
                 field: str, old: Optional[str], new: Optional[str]) -> None:
        """Протоколирует правку поля строки.

        user_id — audit-ссылка без FOREIGN KEY: система пользователя
        (Task 10) может логировать правки, даже если запись пользователя
        уже удалена или ещё не создана.
        """
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO item_edits (estimate_id, row_id, user_id, field,"
                " old_value, new_value, ts) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (estimate_id, row_id, user_id, field, old, new, _now()),
            )

    def apply_edit(self, estimate_id: int, row_id: str,
                   field: str, value: Union[str, Decimal, None]) -> None:
        """Обновляет поле в payload_json строки расчёта.

        Decimal-поля принимают str/Decimal и хранятся как Decimal в модели.
        Read-modify-write выполняется в одной транзакции BEGIN IMMEDIATE:
        строка залочена на запись на всю операцию, два одновременных
        редактора не теряют правки друг друга (второй ждёт busy_timeout).
        """
        conn = self._connect()
        conn.isolation_level = None
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute(
                "SELECT payload_json FROM estimate_items"
                " WHERE estimate_id = ? AND row_id = ?",
                (estimate_id, row_id),
            ).fetchone()
            if row is None:
                conn.execute("ROLLBACK")
                raise KeyError(
                    f"строка {row_id} не найдена в расчёте {estimate_id}"
                )
            item = EstimateItem.model_validate_json(row["payload_json"])
            annotation = EstimateItem.model_fields[field].annotation
            if annotation is Decimal or (
                hasattr(annotation, "__args__")
                and Decimal in getattr(annotation, "__args__", ())
            ):
                value = Decimal(str(value)) if value is not None else None
            setattr(item, field, value)
            conn.execute(
                "UPDATE estimate_items SET payload_json = ?"
                " WHERE estimate_id = ? AND row_id = ?",
                (item.model_dump_json(), estimate_id, row_id),
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def get_edits(self, estimate_id: int, row_id: str) -> list[dict]:
        """История правок строки от старых к новым."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM item_edits WHERE estimate_id = ? AND row_id = ?"
                " ORDER BY id",
                (estimate_id, row_id),
            ).fetchall()
        return [dict(r) for r in rows]

    # ---- ручные цены ----

    def find_manual_price(self, name: str) -> Optional[Decimal]:
        """Ищет ручную цену по имени с нормализацией (casefold, пробелы)."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT price FROM manual_prices WHERE normalized_name = ?",
                (_normalize(name),),
            ).fetchone()
        return Decimal(row["price"]) if row else None

    def set_manual_price(self, name: str, price: Decimal, unit: str,
                         user: int) -> None:
        """Создаёт или перезаписывает ручную цену позиции."""
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO manual_prices (normalized_name, price, unit,"
                " set_by, ts) VALUES (?, ?, ?, ?, ?)"
                " ON CONFLICT(normalized_name) DO UPDATE SET"
                " price = excluded.price, unit = excluded.unit,"
                " set_by = excluded.set_by, ts = excluded.ts",
                (_normalize(name), str(price), unit, user, _now()),
            )

    # ---- расценки работ ----

    def list_work_rates(self) -> list[dict]:
        """Все расценки работ; rate и coefficient — Decimal."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM work_rates ORDER BY id"
            ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["rate"] = Decimal(d["rate"])
            d["coefficient"] = Decimal(d["coefficient"])
            result.append(d)
        return result

    def upsert_work_rate(self, name: str, unit: str, rate: Decimal,
                         coefficient: Decimal) -> None:
        """Создаёт или обновляет расценку по имени (ключ — name)."""
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO work_rates (name, unit, rate, coefficient)"
                " VALUES (?, ?, ?, ?)"
                " ON CONFLICT(name) DO UPDATE SET"
                " unit = excluded.unit, rate = excluded.rate,"
                " coefficient = excluded.coefficient",
                (name, unit, str(rate), str(coefficient)),
            )

    # ---- переопределения цен ----

    def get_overrides(self, scope: str) -> dict:
        """Переопределения цен для scope (`boris` | `work_rate`); ключ — key_json."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT key_json, price FROM price_overrides WHERE scope = ?",
                (scope,),
            ).fetchall()
        return {
            json.loads(r["key_json"]): Decimal(r["price"]) for r in rows
        }

    def set_override(self, scope: str, key: str, price: Decimal,
                     user: int) -> None:
        """Создаёт или перезаписывает переопределение цены (scope, key)."""
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO price_overrides (scope, key_json, price, set_by,"
                " ts) VALUES (?, ?, ?, ?, ?)"
                " ON CONFLICT(scope, key_json) DO UPDATE SET"
                " price = excluded.price, set_by = excluded.set_by,"
                " ts = excluded.ts",
                (scope, json.dumps(key, ensure_ascii=False), str(price),
                 user, _now()),
            )
