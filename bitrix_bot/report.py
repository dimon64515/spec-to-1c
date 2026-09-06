"""Формирование итогового сообщения бота: сводка + детальные списки.

Длинная детализация нарезается на несколько сообщений (лимит im ~4000
символов; берём 3500 с запасом).
"""

from __future__ import annotations

from typing import List

from bitrix_bot.pipeline import PipelineResult

DEFAULT_LIMIT = 3500


def _position_line(p: dict) -> str:
    dims = p.get("params") or {}
    dim_txt = "x".join(str(int(v)) for v in dims.values() if isinstance(v, (int, float)))
    suffix = f" ({dim_txt})" if dim_txt else ""
    comment = f" — {p['comment']}" if p.get("comment") else ""
    return f"· {p['article']}{suffix}, {p['quantity']} шт{comment}"


def _skipped_line(s: dict) -> str:
    name = s.get("name") or "?"
    size = f" {s.get('size')}" if s.get("size") else ""
    qty = f", {s.get('quantity')}" if s.get("quantity") else ""
    return f"· {name}{size}{qty} — {s.get('reason', 'без причины')}"


def _join_chunks(lines: List[str], limit: int) -> List[str]:
    """Склеить строки в сообщения не длиннее limit, не рвя строки."""
    out: List[str] = []
    buf: List[str] = []
    for line in lines:
        cur = "\n".join(buf + [line])
        if buf and len(cur) > limit:
            out.append("\n".join(buf))
            buf = [line]
        else:
            buf.append(line)
    if buf:
        out.append("\n".join(buf))
    return out


def build_report(res: PipelineResult, limit: int = DEFAULT_LIMIT) -> List[str]:
    """Вернуть список сообщений для чата задачи."""
    header = (
        f"Заказ 1С: №{res.order_number}" if res.order_number else "Заказ НЕ создан"
    )
    summary = (
        f"Файл: {res.file_name}\n"
        f"Загружено: {len(res.loaded)} · Пропущено: {len(res.skipped)}"
        f" · Ошибок: {len(res.errors_1c)}"
    )
    lines: List[str] = [header, "", summary]

    if res.loaded:
        lines += ["", "✅ Загружены:"] + [_position_line(p) for p in res.loaded]
    if res.skipped:
        lines += ["", "⏭ Пропущены:"] + [_skipped_line(s) for s in res.skipped]
    if res.errors_1c:
        lines += ["", "❌ Ошибки 1С:"] + [f"· {e}" for e in res.errors_1c]
    if res.warnings_1c:
        lines += ["", "⚠ Предупреждения 1С:"] + [f"· {w}" for w in res.warnings_1c]

    chunks = _join_chunks(lines, limit)
    # Первый кусок начинается с шапки; продолжения помечаем
    if len(chunks) > 1:
        chunks = [chunks[0]] + [f"(продолжение {i + 2}/{len(chunks)})\n{c}" for i, c in enumerate(chunks[1:])]
    return chunks
