"""Разбор событий webhook Битрикса и поиск PDF в чате задачи.

Схема событий Битрикс24 не документирована до конца (что приходит в
reply-контексте — см. Task 7, ручная сверка), поэтому парсинг ищет файл
по нескольким известным ключам. Цепочка: reply-контекст сообщения →
последние сообщения диалога → PdfNotFound.

Битрикс шлёт события не JSON'ом, а application/x-www-form-urlencoded
(PHP-стиль: event=ONIMBOTMESSAGEADD&data[PARAMS][DIALOG_ID]=...&auth[...]=...)
— поэтому тело сначала собирают в dict через form_payload.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Optional, Tuple
from urllib.parse import parse_qsl


class PdfNotFound(RuntimeError):
    """PDF не найден в задаче/диалоге."""


def _insert_nested(root: dict, path: list[str], value: str) -> None:
    node = root
    for key in path[:-1]:
        node = node.setdefault(key, {})
    node[path[-1]] = value


def _lists_from_int_keys(obj):
    """dict с числовыми ключами {'0':..,'1':..} -> list (PHP-массив)."""
    if isinstance(obj, dict):
        converted = {k: _lists_from_int_keys(v) for k, v in obj.items()}
        if converted and all(k.isdigit() for k in converted):
            return [converted[k] for k in sorted(converted, key=int)]
        return converted
    return obj


def form_payload(body: bytes) -> dict:
    """Собрать payload из form-urlencoded тела события Битрикса."""
    root: dict = {}
    for key, value in parse_qsl(body.decode("utf-8", "replace"),
                               keep_blank_values=True):
        path = [k for part in key.split("[") for k in [part.rstrip("]")]]
        _insert_nested(root, path, value)
    return _lists_from_int_keys(root)


@dataclass
class BotEvent:
    dialog_id: str
    message_id: str
    user_id: Optional[int]
    text: str
    task_id: Optional[int]
    bot_id: Optional[int] = None
    file_url: Optional[str] = None
    file_name: Optional[str] = None
    is_reply: bool = False


def task_id_from_dialog(dialog_id: str) -> Optional[int]:
    """'task|42' -> 42; остальное -> None."""
    if dialog_id.startswith("task|"):
        try:
            return int(dialog_id.split("|", 1)[1])
        except ValueError:
            return None
    return None


def _looks_like(name: Optional[str], exts: tuple) -> bool:
    """Фильтр: без имени пропускаем (best effort), иначе проверяем расширение."""
    return not name or name.lower().endswith(exts)


def _first_file(params: dict, exts: tuple = (".pdf",)) -> Tuple[Optional[str], Optional[str]]:
    """Вытащить (url, имя) файла с нужным расширением из params сообщения."""
    files = params.get("FILES") or params.get("files") or []
    if isinstance(files, list) and files:
        f0 = files[0] or {}
        url = (f0.get("url") or f0.get("urlDownload") or f0.get("downloadUrl")
               or f0.get("DOWNLOAD_URL"))
        name = f0.get("name") or f0.get("FILE_NAME")
        if url and _looks_like(name, exts):
            return url, name
    for key in ("FILE_URL", "DOWNLOAD_URL", "ATTACH_URL"):
        if params.get(key):
            name = params.get("FILE_NAME")
            if _looks_like(name, exts):
                return params[key], name
    attach = params.get("ATTACH")
    if isinstance(attach, list):
        for block in attach:
            if isinstance(block, dict) and block.get("LINK"):
                name = block.get("NAME")
                if _looks_like(name, exts):
                    return block["LINK"], name
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
        file_url, file_name = _first_file(replied_params, exts=(".pdf", ".xlsx"))
    if not file_url:
        file_url, file_name = _first_file(params, exts=(".pdf", ".xlsx"))
    user_raw = params.get("FROM_USER_ID") or params.get("from_user_id")
    bot_id = None
    bots = (payload.get("data") or {}).get("BOT") or []
    if bots and str(bots[0].get("BOT_ID", "")).isdigit():
        bot_id = int(bots[0]["BOT_ID"])
    return BotEvent(
        dialog_id=dialog_id,
        message_id=str(params.get("MESSAGE_ID", "")),
        user_id=int(user_raw) if user_raw else None,
        text=params.get("MESSAGE", ""),
        task_id=task_id_from_dialog(dialog_id),
        bot_id=bot_id,
        file_url=file_url,
        file_name=file_name,
        is_reply=isinstance(replied, dict) and bool(replied),
    )


def _file_from_message(msg: dict, ext: str = ".pdf") -> Tuple[Optional[str], Optional[str]]:
    params = msg.get("params") if isinstance(msg, dict) else None
    if isinstance(params, dict):
        return _first_file(params, exts=(ext,))
    return None, None


def find_pdf(client, event: BotEvent, ext: str = ".pdf") -> Tuple[bytes, str]:
    """Скачать файл с расширением ext: из сообщения, иначе из истории."""
    if event.file_url and _looks_like(event.file_name, (ext,)):
        url = event.file_url
        # голые ссылки disk требуют сессии — берём подписанный URL
        m = re.search(r"fileId=(\d+)", url)
        if m and hasattr(client, "resolve_download_url"):
            url = client.resolve_download_url(int(m.group(1)))
        return client.download_file(url), event.file_name or f"document{ext}"
    for msg in client.get_dialog_messages(event.dialog_id, limit=30):
        url, name = _file_from_message(msg, ext)
        if url:
            return client.download_file(url), name or f"document{ext}"
    raise PdfNotFound(
        f"Не нашёл файл {ext}: ответьте (reply) на сообщение с файлом и упомяните меня ещё раз."
    )
