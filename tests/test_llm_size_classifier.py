"""LLM-фолбэк классификации размеров. Все тесты без сети/subprocess."""
import pytest

import llm_size_classifier as lsc


def test_get_llm_config_defaults(monkeypatch):
    cfg = lsc.get_llm_config()
    assert cfg["enabled"] is False
    assert cfg["backend"] == "kimi-cli"
    assert cfg["batch_size"] >= 1
    assert cfg["timeout"] > 0
    assert cfg["max_retries"] >= 0


def test_llm_enabled_requires_flag():
    assert lsc.llm_enabled({"enabled": False, "backend": "kimi-cli", "command": "kimi"}) is False
    assert lsc.llm_enabled({"enabled": True, "backend": "other", "command": "kimi"}) is False
    assert lsc.llm_enabled({"enabled": True, "backend": "kimi-cli", "command": ""}) is False


def test_llm_enabled_requires_command_on_path(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda cmd: None)
    assert lsc.llm_enabled({"enabled": True, "backend": "kimi-cli", "command": "kimi"}) is False
    monkeypatch.setattr("shutil.which", lambda cmd: "/usr/bin/kimi")
    assert lsc.llm_enabled({"enabled": True, "backend": "kimi-cli", "command": "kimi"}) is True


def test_reason_constant():
    assert lsc.REASON_LLM_REJECTED == "LLM-классификация отклонена"
