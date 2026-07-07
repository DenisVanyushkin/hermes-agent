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


from datetime import timedelta


def test_canonical_url_strips_tracking_and_fragment():
    a = nc.canonical_url("https://Example.com/Post?utm_source=x&id=5#frag")
    b = nc.canonical_url("https://example.com/Post?id=5")
    assert a == b
    assert "utm_source" not in a and "#frag" not in a


def test_canonical_url_removes_trailing_slash():
    assert nc.canonical_url("https://x.io/a/") == nc.canonical_url("https://x.io/a")


def test_seen_store_roundtrip_and_prune(tmp_path):
    conn = nc.seen_connect(tmp_path / "seen.sqlite")
    now = datetime(2026, 7, 7, tzinfo=timezone.utc)
    url = "https://x.io/a"
    assert nc.is_seen(conn, url) is False
    nc.mark_seen(conn, url, now)
    assert nc.is_seen(conn, url) is True
    # not pruned within TTL
    assert nc.prune_seen(conn, now + timedelta(days=13), ttl_days=14) == 0
    assert nc.is_seen(conn, url) is True
    # pruned past TTL
    assert nc.prune_seen(conn, now + timedelta(days=15), ttl_days=14) == 1
    assert nc.is_seen(conn, url) is False
