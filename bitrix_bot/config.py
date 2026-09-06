"""Конфигурация битрикс-бота: config.yaml + секреты из bitrix.local.yaml.

Секреты (вебхук, client_secret, токен верификации) живут ТОЛЬКО в
bitrix.local.yaml — файл в .gitignore, в git не коммитить.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict

import yaml

import config as app_config

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LOCAL_CONFIG = ROOT / "bitrix.local.yaml"

DEFAULT_EXECUTE_CODE_URL = "http://127.0.0.1:6005/api/execute_code"


@dataclass(frozen=True)
class BotConfig:
    portal: str = ""
    incoming_webhook: str = ""
    client_id: str = ""
    client_secret: str = ""
    verify_token: str = ""
    execute_code_url: str = DEFAULT_EXECUTE_CODE_URL
    tmp_dir: str = "tmp/bitrix_bot"
    report_limit: int = 3500
    request_timeout: float = 280.0
    task_comment_prefix: str = "Задача Битрикс24"


def _read_yaml(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_bot_config(
    base_path: str | Path | None = None,
    local_path: str | Path | None = None,
) -> BotConfig:
    """Собрать BotConfig из config.yaml (base) и bitrix.local.yaml (секреты)."""
    base = _read_yaml(Path(base_path)) if base_path else app_config.get_config()
    local = _read_yaml(Path(local_path)) if local_path else _read_yaml(DEFAULT_LOCAL_CONFIG)

    bx: Dict[str, Any] = {**(base.get("bitrix") or {}), **(local.get("bitrix") or {})}
    mw: Dict[str, Any] = {**(base.get("middleware") or {}), **(local.get("middleware") or {})}

    mcp_url = (base.get("mcp") or {}).get("url", "")
    execute_url = DEFAULT_EXECUTE_CODE_URL
    if mcp_url:
        # http://127.0.0.1:6005/mcp -> http://127.0.0.1:6005/api/execute_code
        execute_url = mcp_url.rstrip("/").rsplit("/", 1)[0] + "/api/execute_code"

    return BotConfig(
        portal=bx.get("portal", ""),
        incoming_webhook=bx.get("incoming_webhook", ""),
        client_id=bx.get("app_client_id", ""),
        client_secret=bx.get("app_client_secret", ""),
        verify_token=mw.get("webhook_verify_token", ""),
        execute_code_url=mw.get("execute_code_url", execute_url),
        tmp_dir=mw.get("tmp_dir", "tmp/bitrix_bot"),
        report_limit=int(mw.get("report_limit", 3500)),
        request_timeout=float(mw.get("request_timeout", 280)),
        task_comment_prefix=mw.get("task_comment_prefix", "Задача Битрикс24"),
    )
