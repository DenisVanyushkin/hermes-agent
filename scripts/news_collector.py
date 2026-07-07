#!/usr/bin/env python3
"""Collect news candidates from Telegram (t.me/s), RSS/Atom, HN, and GitHub
trending into ~/.hermes/news/candidates-latest.json for the nightly digest
agent. Prints nothing on success so the no-agent cron stays silent.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml
# defusedxml hardens against XXE / billion-laughs on untrusted remote feeds.
import defusedxml.ElementTree as ET

DEFAULTS = {
    "telegram_channels": ["llm_news"],
    "rss_feeds": [
        "https://hnrss.org/frontpage",
        "https://openai.com/blog/rss.xml",
        "https://www.anthropic.com/rss.xml",
        "https://huggingface.co/blog/feed.xml",
        "https://simonwillison.net/atom/everything/",
    ],
    "hackernews": {"min_score": 150},
    "github_trending": {"topics": ["llm", "ai-agents"], "min_stars_week": 200},
    "max_items_per_day": 40,
    "freshness_hours": 36,
    "http_timeout": 20,
}


def news_dir() -> Path:
    home = Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes")))
    d = home / "news"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _deep_merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_sources(path: Path) -> dict:
    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        data = {}
    return _deep_merge(DEFAULTS, data)


from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

_TRACKING = ("utm_source", "utm_medium", "utm_campaign", "utm_term",
             "utm_content", "ref", "fbclid", "gclid")


def canonical_url(url: str) -> str:
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    netloc = parts.netloc.lower()
    query = urlencode([(k, v) for k, v in parse_qsl(parts.query)
                       if k.lower() not in _TRACKING])
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((scheme, netloc, path, query, ""))


def seen_connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE IF NOT EXISTS seen "
                 "(url TEXT PRIMARY KEY, first_seen REAL)")
    conn.commit()
    return conn


def is_seen(conn: sqlite3.Connection, url: str) -> bool:
    row = conn.execute("SELECT 1 FROM seen WHERE url = ?",
                       (canonical_url(url),)).fetchone()
    return row is not None


def mark_seen(conn: sqlite3.Connection, url: str, now: datetime) -> None:
    conn.execute("INSERT OR IGNORE INTO seen(url, first_seen) VALUES (?, ?)",
                 (canonical_url(url), now.timestamp()))
    conn.commit()


def prune_seen(conn: sqlite3.Connection, now: datetime, ttl_days: int = 14) -> int:
    cutoff = (now - timedelta(days=ttl_days)).timestamp()
    cur = conn.execute("DELETE FROM seen WHERE first_seen < ?", (cutoff,))
    conn.commit()
    return cur.rowcount
