"""Минимальный REST-клиент Битрикс24 поверх входящего вебхука.

Вебхук вида https://portal.bitrix24.ru/rest/{user}/{key}/ — каждый метод
вызывается POST-ом на {webhook}{method} с телом params.
"""

from __future__ import annotations

from typing import Optional

import base64
import httpx


class BitrixError(RuntimeError):
    """Ошибка REST API Битрикс24 (HTTP или error в ответе)."""


class BitrixClient:
    def __init__(self, webhook: str, timeout: float = 30.0,
                 app_client_id: str = ""):
        self._webhook = webhook.rstrip("/") + "/"
        self._timeout = timeout
        self._client_id = app_client_id

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

    def send_message(self, dialog_id: str, text: str,
                     bot_id: Optional[int] = None) -> None:
        """Отправить сообщение. bot_id задан — от имени чат-бота
        (imbot.message.add), иначе от имени пользователя вебхука."""
        if bot_id:
            params = dict(DIALOG_ID=dialog_id, MESSAGE=text, BOT_ID=bot_id)
            if self._client_id:
                params["CLIENT_ID"] = self._client_id
            self.call("imbot.message.add", **params)
        else:
            self.call("im.message.add", DIALOG_ID=dialog_id, MESSAGE=text)

    def send_file(self, dialog_id: str, file_name: str, content: bytes,
                  caption: str, bot_id: Optional[int] = None) -> None:
        """Прикрепить файл к сообщению чата: upload на Диск + im.message.add
        с ATTACH. Если attach не поддержан — фолбэк: текст со ссылкой на файл."""
        storages = self.call("disk.storage.getlist")
        if not storages:
            raise BitrixError("disk.storage.getlist: нет доступных хранилищ")
        storage = self.call("disk.storage.get", id=storages[0]["ID"])
        folder_id = storage["ROOT_OBJECT_ID"]
        uploaded = self.call(
            "disk.folder.uploadfile",
            id=folder_id,
            data={"NAME": file_name},
            fileContent=base64.b64encode(content).decode("ascii"),
            generateUniqueName=True,
        )
        file_id = uploaded["ID"]
        detail_url = uploaded.get("DETAIL_URL") or ""
        attach = [["DISK", file_id]]
        try:
            if bot_id:
                params = dict(DIALOG_ID=dialog_id, MESSAGE=caption,
                              ATTACH=attach, BOT_ID=bot_id)
                if self._client_id:
                    params["CLIENT_ID"] = self._client_id
                self.call("imbot.message.add", **params)
            else:
                self.call("im.message.add", DIALOG_ID=dialog_id,
                          MESSAGE=caption, ATTACH=attach)
        except BitrixError:
            # тариф/скоп не позволяет attach — хотя бы ссылка на файл
            suffix = f"\nФайл: {detail_url}" if detail_url else ""
            self.send_message(dialog_id, caption + suffix, bot_id=bot_id)

    def get_task_title(self, task_id: int) -> str:
        result = self.call("tasks.task.get", taskId=task_id)
        task = result.get("task") or {}
        return task.get("title") or ""

    def resolve_download_url(self, file_id) -> str:
        """Подписанный DOWNLOAD_URL файла Диска (scope disk)."""
        result = self.call("disk.file.get", id=file_id)
        return result.get("DOWNLOAD_URL") or ""

    def get_dialog_messages(self, dialog_id: str, limit: int = 30) -> list[dict]:
        """Последние сообщения диалога с развернутыми файлами.

        im.dialog.messages.get отдает messages[].params.FILE_ID и отдельный
        массив files[] со ссылками — собираем в вид, совместимый с
        _file_from_message (params.FILE_URL / FILE_NAME). urlDownload из
        истории не подписан (редиректит на авторизацию), поэтому берем
        подписанный URL через resolve_download_url.
        """
        result = self.call(
            "im.dialog.messages.get", DIALOG_ID=dialog_id, LIMIT=limit
        )
        files = {int(f["id"]): f for f in result.get("files", []) if f.get("id")}
        out: list[dict] = []
        for msg in result.get("messages", []):
            params = msg.get("params") or {}
            file_ids = params.get("FILE_ID") or []
            if isinstance(file_ids, (int, str)):
                file_ids = [file_ids]
            for fid in file_ids:
                f = files.get(int(fid))
                if not f:
                    continue
                url = f.get("urlDownload") or ""
                if "disk.api.file.download" in url:
                    url = self.resolve_download_url(f["id"])
                out.append({"params": {"FILE_URL": url,
                                       "FILE_NAME": f.get("name")}})
            out.append(msg)
        return out

    def download_file(self, download_url: str) -> bytes:
        resp = httpx.get(download_url, timeout=self._timeout)
        if resp.status_code != 200:
            raise BitrixError(f"download: HTTP {resp.status_code}")
        return resp.content
