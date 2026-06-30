"""Configuration loader for spec-to-1c."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict

import yaml


DEFAULT_CONFIG_PATH = Path(__file__).parent / "config.yaml"

_CONFIG: Dict[str, Any] | None = None


def load_config(path: str | os.PathLike | None = None) -> Dict[str, Any]:
    """Load application configuration from a YAML file.

    If no path is provided, the environment variable ``SPEC_TO_1C_CONFIG``
    is checked first, otherwise ``config.yaml`` next to this module is used.
    """
    if path is None:
        path = os.environ.get("SPEC_TO_1C_CONFIG", DEFAULT_CONFIG_PATH)
    path = Path(path)
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def get_config() -> Dict[str, Any]:
    """Return the cached configuration, loading it on first call."""
    global _CONFIG
    if _CONFIG is None:
        _CONFIG = load_config()
    return _CONFIG


def reload_config(path: str | os.PathLike | None = None) -> Dict[str, Any]:
    """Reload configuration and update the cached copy."""
    global _CONFIG
    _CONFIG = load_config(path)
    return _CONFIG


def mapping_file(key: str) -> str:
    """Return the configured path for a mapping/reference file."""
    return get_config()["mapping_files"][key]
