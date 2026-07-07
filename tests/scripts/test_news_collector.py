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


def _raw(url, title="t", published=None, source="s", typ="rss"):
    return {"source": source, "type": typ, "title": title, "url": url,
            "summary": "", "snippet": "", "published_at": published}


def test_normalize_item_defaults_and_canonicalizes():
    it = nc.normalize_item(_raw("https://x.io/a/?utm_source=z", title="Hi"))
    assert it["canonical_url"] == "https://x.io/a"
    assert it["title"] == "Hi"
    assert it["summary"] == "" and it["snippet"] == ""


def test_select_candidates_dedups_caps_and_carries(tmp_path):
    conn = nc.seen_connect(tmp_path / "seen.sqlite")
    now = datetime(2026, 7, 7, 12, 0, tzinfo=timezone.utc)
    iso = lambda h: (now - timedelta(hours=h)).isoformat()
    items = [
        nc.normalize_item(_raw("https://x.io/1", published=iso(1))),
        nc.normalize_item(_raw("https://x.io/2", published=iso(2))),
        nc.normalize_item(_raw("https://x.io/2?utm_source=z", published=iso(2))),  # dup of /2
        nc.normalize_item(_raw("https://x.io/3", published=iso(99))),              # stale
        nc.normalize_item(_raw("https://x.io/4", published=iso(3))),
    ]
    emitted, carried = nc.select_candidates(items, conn, now, max_items=2, freshness_hours=36)
    urls = [i["canonical_url"] for i in emitted]
    assert urls == ["https://x.io/1", "https://x.io/2"]   # freshest first, dup collapsed
    assert [i["canonical_url"] for i in carried] == ["https://x.io/4"]  # stale dropped, overflow carried
    # emitted are marked seen; carried is not
    assert nc.is_seen(conn, "https://x.io/1") and nc.is_seen(conn, "https://x.io/2")
    assert nc.is_seen(conn, "https://x.io/4") is False
    # second run: /1 and /2 now suppressed, /4 becomes emittable
    emitted2, _ = nc.select_candidates(items, conn, now, max_items=2, freshness_hours=36)
    assert [i["canonical_url"] for i in emitted2] == ["https://x.io/4"]
