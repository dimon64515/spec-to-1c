"""Smoke-тест точки входа web_ui.app (uvicorn)."""

import runpy

import pytest
from fastapi import FastAPI


def test_main_module_starts_uvicorn(monkeypatch):
    calls = {}

    def _fake_run(app, host=None, port=None, **kwargs):
        calls["app"] = app
        calls["host"] = host
        calls["port"] = port

    monkeypatch.setattr("uvicorn.run", _fake_run)

    runpy.run_module("web_ui.app", run_name="__main__")

    assert isinstance(calls["app"], FastAPI)
    assert calls["host"] == "127.0.0.1"
    assert calls["port"] == 8092
