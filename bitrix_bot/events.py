"""Разбор событий webhook Битрикса и поиск PDF в чате задачи.

Схема событий Битрикс24 не документирована до конца (что приходит в
reply-контексте — см. Task 7, ручная сверка), поэтому парсинг ищет файл
по нескольким известным ключам. Цепочка: reply-контекст сообщения →
последние сообщения диалога → PdfNotFound.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple


class PdfNotFound(RuntimeError):
    """PDF не найден в задаче/диалоге."""


@dataclass
class BotEvent:
    dialog_id: str
    message_id: str
    user_id: Optional[int]
    text: str
    task_id: Optional[int]
    file_url: Optional[str] = None
    file_name: Optional[str] = None


def task_id_from_dialog(dialog_id: str) -> Optional[int]:
    """'task|42' -> 42; остальное -> None."""
    if dialog_id.startswith("task|"):
        try:
            return int(dialog_id.split("|", 1)[1])
        except ValueError:
            return None
    return None


def _first_file(params: dict) -> Tuple[Optional[str], Optional[str]]:
    """Вытащить (url, имя) файла из params сообщения — best effort по известным ключам."""
    files = params.get("FILES") or params.get("files") or []
    if isinstance(files, list) and files:
        f0 = files[0] or {}
        url = f0.get("url") or f0.get("downloadUrl") or f0.get("DOWNLOAD_URL")
        name = f0.get("name") or f0.get("FILE_NAME") or "document.pdf"
        if url:
            return url, name
    for key in ("FILE_URL", "DOWNLOAD_URL", "ATTACH_URL"):
        if params.get(key):
            return params[key], params.get("FILE_NAME") or "document.pdf"
    attach = params.get("ATTACH")
    if isinstance(attach, list):
        for block in attach:
            if isinstance(block, dict) and block.get("LINK"):
                return block["LINK"], block.get("NAME") or "document.pdf"
    return None, None


def parse_event(payload: dict) -> Optional[BotEvent]:
    """Разобрать тело webhook; None — событие не касается бота."""
    if payload.get("event") != "ONIMBOTMESSAGEADD":
        return None
    params = ((payload.get("data") or {}).get("PARAMS")) or {}
    dialog_id = params.get("DIALOG_ID", "")
    if not dialog_id:
        return None
    # reply-контекст: в некоторых версиях приходит целиком цитируемое сообщение
    replied = params.get("MESSAGE_REPLIED") or params.get("message_replied") or {}
    replied_params = replied.get("params") if isinstance(replied, dict) else None
    file_url, file_name = (None, None)
    if isinstance(replied_params, dict):
        file_url, file_name = _first_file(replied_params)
    if not file_url:
        file_url, file_name = _first_file(params)
    user_raw = params.get("FROM_USER_ID") or params.get("from_user_id")
    return BotEvent(
        dialog_id=dialog_id,
        message_id=str(params.get("MESSAGE_ID", "")),
        user_id=int(user_raw) if user_raw else None,
        text=params.get("MESSAGE", ""),
        task_id=task_id_from_dialog(dialog_id),
        file_url=file_url,
        file_name=file_name,
    )


def _file_from_message(msg: dict) -> Tuple[Optional[str], Optional[str]]:
    params = msg.get("params") if isinstance(msg, dict) else None
    if isinstance(params, dict):
        return _first_file(params)
    return None, None


def find_pdf(client, event: BotEvent) -> Tuple[bytes, str]:
    """Скачать PDF: из reply-контекста, иначе из последних сообщений диалога."""
    if event.file_url:
        return client.download_file(event.file_url), event.file_name or "document.pdf"
    for msg in client.get_dialog_messages(event.dialog_id, limit=30):
        url, name = _file_from_message(msg)
        if url:
            return client.download_file(url), name or "document.pdf"
    raise PdfNotFound(
        "Не нашёл PDF: ответьте (reply) на сообщение с файлом и упомяните меня ещё раз."
    )
