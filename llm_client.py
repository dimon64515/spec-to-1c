"""Общий клиент kimi-cli (subprocess, stream-json).

Вынесен из llm_size_classifier.py для переиспользования другими LLM-фолбэками.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from typing import Any, Dict, Tuple

logger = logging.getLogger(__name__)


def run_kimi(prompt: str, cfg: Dict) -> str:
    """Один вызов kimi -p; возвращает сырой stdout. Бросает исключения при сбое."""
    cmd = [str(cfg.get("command") or "kimi"), "-p", prompt, "--output-format", "stream-json"]
    model = str(cfg.get("model") or "").strip()
    if model:
        cmd += ["-m", model]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                          timeout=int(cfg.get("timeout", 120)))
    if proc.returncode != 0:
        raise RuntimeError(f"kimi exit {proc.returncode}: {proc.stderr[:300]}")
    return proc.stdout


def assistant_content(stdout: str) -> str:
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if rec.get("role") == "assistant" and isinstance(rec.get("content"), str):
            return rec["content"]
    raise ValueError("assistant-ответ не найден в stream-json")


def extract_json_object(text: str) -> Tuple[Any, int]:
    start = text.find("{")
    if start < 0:
        raise ValueError("JSON не найден в ответе")
    obj, end = json.JSONDecoder().raw_decode(text[start:])
    if not isinstance(obj, dict):
        raise ValueError("ответ — не объект")
    return obj, end
