import os
from pathlib import Path

import pytest

from config import load_config, get_config, reload_config, mapping_file


def test_load_config_default():
    cfg = load_config()
    assert cfg["default_round_article"] == "1-1-2"
    assert cfg["default_rect_article"] == "1-2-1"
    assert cfg["default_round_length_mm"] == 3000
    assert cfg["default_rect_length_mm"] == 1250
    assert cfg["mapping_files"]["products"] == "product_article_mapping.json"
    assert cfg["mcp"]["url"] == "http://localhost:6003/mcp"


def test_mapping_file_helper():
    assert mapping_file("materials") == "article_materials.json"


def test_reload_config_updates_cache(tmp_path):
    custom = tmp_path / "custom.yaml"
    custom.write_text("default_round_article: \"9-9-9\"\n", encoding="utf-8")
    reload_config(custom)
    assert get_config()["default_round_article"] == "9-9-9"
    # restore default cache for other tests
    reload_config()
    assert get_config()["default_round_article"] == "1-1-2"


def test_load_config_missing_file_raises():
    with pytest.raises(FileNotFoundError):
        load_config("nonexistent_config.yaml")
