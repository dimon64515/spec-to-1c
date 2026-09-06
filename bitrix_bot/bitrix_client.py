"""Минимальный REST-клиент Битрикс24 поверх входящего вебхука.

Вебхук вида https://portal.bitrix24.ru/rest/{user}/{key}/ — каждый метод
вызывается POST-ом на {webhook}{method} с телом params.
"""

from __future__ import annotations

import httpx


class BitrixError(RuntimeError):
    """Ошибка REST API Битрикс24 (HTTP или error в ответе)."""


class BitrixClient:
    def __init__(self, webhook: str, timeout: float = 30.0):
        self._webhook = webhook.rstrip("/") + "/"
        self._timeout = timeout

    def call(self, method: str, **params):
        resp = httpx.post(self._webhook + method, json=params, timeout=self._timeout)
        if resp.status_code != 200:
            raise BitrixError(f"{method}: HTTP {resp.status_code}: {getattr(resp, 'text', '')[:300]}")
        data = resp.json()
        if "error" in data:
            raise BitrixError(
                f"{method}: {data['error']}: {data.get('error_description', '')}"
            )
        return data["result"]

    def send_message(self, dialog_id: str, text: str) -> None:
        self.call("im.message.add", DIALOG_ID=dialog_id, MESSAGE=text)

    def get_task_title(self, task_id: int) -> str:
        result = self.call("tasks.task.get", taskId=task_id)
        task = result.get("task") or {}
        return task.get("title") or ""

    def get_dialog_messages(self, dialog_id: str, limit: int = 30) -> list[dict]:
        result = self.call("im.message.get", CHAT_ID=dialog_id, LIMIT=limit)
        if isinstance(result, list):
            return result
        return (result or {}).get("messages") or []

    def download_file(self, download_url: str) -> bytes:
        resp = httpx.get(download_url, timeout=self._timeout)
        if resp.status_code != 200:
            raise BitrixError(f"download: HTTP {resp.status_code}")
        return resp.content
