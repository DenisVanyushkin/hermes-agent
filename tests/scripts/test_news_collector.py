import importlib.util
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "news_collector.py"
SPEC = importlib.util.spec_from_file_location("news_collector", SCRIPT_PATH)
nc = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(nc)


def test_load_sources_uses_defaults_when_file_missing(tmp_path):
    cfg = nc.load_sources(tmp_path / "nope.yaml")
    assert cfg["max_items_per_day"] == 40
    assert cfg["freshness_hours"] == 36
    assert "llm_news" in cfg["telegram_channels"]
    assert isinstance(cfg["rss_feeds"], list) and cfg["rss_feeds"]


def test_load_sources_merges_file_over_defaults(tmp_path):
    p = tmp_path / "sources.yaml"
    p.write_text("max_items_per_day: 10\ntelegram_channels: [foo]\n", encoding="utf-8")
    cfg = nc.load_sources(p)
    assert cfg["max_items_per_day"] == 10          # overridden
    assert cfg["telegram_channels"] == ["foo"]     # overridden
    assert cfg["freshness_hours"] == 36            # default preserved
