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


def normalize_item(raw: dict) -> dict:
    pub = raw.get("published_at") or ""
    return {
        "source": raw.get("source", ""),
        "type": raw.get("type", ""),
        "title": (raw.get("title") or "").strip(),
        "canonical_url": canonical_url(raw.get("url", "")),
        "summary": (raw.get("summary") or "").strip(),
        "snippet": (raw.get("snippet") or "").strip(),
        "published_at": pub,
    }


def _parse_iso(s: str):
    try:
        dt = datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def select_candidates(items, conn, now, max_items, freshness_hours):
    cutoff = now - timedelta(hours=freshness_hours)
    fresh, batch_seen = [], set()
    for it in items:
        url = it["canonical_url"]
        if not url or url in batch_seen or is_seen(conn, url):
            continue
        pub = _parse_iso(it["published_at"])
        if pub is not None and pub < cutoff:
            continue
        batch_seen.add(url)
        fresh.append(it)
    # freshest first; unknown dates sort last (treated as epoch-far-future? no — keep, sort by known date desc)
    fresh.sort(key=lambda i: (_parse_iso(i["published_at"]) or now), reverse=True)
    emitted, carried = fresh[:max_items], fresh[max_items:]
    for it in emitted:
        mark_seen(conn, it["canonical_url"], now)
    return emitted, carried


import html as _html

_HREF_RE = re.compile(r'href="(https?://[^"]+)"')
_TAG_RE = re.compile(r"<[^>]+>")
# Split on the message CONTAINER only. The container class is
# "tgme_widget_message" followed by a space or the closing quote; the body div
# is "tgme_widget_message_text" (underscore), so the [ "] char class avoids
# splitting inside a message. A single regex cannot do this reliably: a
# non-greedy .*? before an OPTIONAL <time> group matches empty and never
# captures the timestamp — hence the two-pass approach (split, then search).
_TG_SPLIT_RE = re.compile(r'<div class="tgme_widget_message[ "]')
_TG_POST_RE = re.compile(r'data-post="([^"]+)"')
_TG_BODY_RE = re.compile(r'<div class="tgme_widget_message_text[^"]*">(.*?)</div>', re.DOTALL)
_TG_TIME_RE = re.compile(r'<time datetime="([^"]+)"')


def _strip_tags(fragment: str) -> str:
    text = _TAG_RE.sub(" ", fragment)
    return _html.unescape(re.sub(r"\s+", " ", text)).strip()


def parse_telegram_html(html_text: str, channel: str) -> list[dict]:
    items = []
    for chunk in _TG_SPLIT_RE.split(html_text)[1:]:
        post_m = _TG_POST_RE.search(chunk)
        body_m = _TG_BODY_RE.search(chunk)
        if not (post_m and body_m):
            continue
        time_m = _TG_TIME_RE.search(chunk)
        body = body_m.group(1)
        links = [u for u in _HREF_RE.findall(body) if "t.me/" not in u]
        permalink = f"https://t.me/{post_m.group(1)}"
        url = links[0] if links else permalink
        title = _strip_tags(body)[:200]
        if not title:
            continue
        items.append({
            "source": f"tg:{channel}",
            "type": "telegram",
            "title": title,
            "url": url,
            "summary": "",
            "snippet": "",
            "published_at": time_m.group(1) if time_m else "",
        })
    return items


from email.utils import parsedate_to_datetime


def _iso(text: str) -> str:
    text = (text or "").strip()
    if not text:
        return ""
    # try RFC-822 (RSS) first, then ISO (Atom)
    try:
        return parsedate_to_datetime(text).astimezone(timezone.utc).isoformat()
    except (TypeError, ValueError, IndexError):
        pass
    dt = _parse_iso(text.replace("Z", "+00:00"))
    return dt.isoformat() if dt else ""


def _localname(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def parse_feed(xml_bytes: bytes, feed_name: str) -> list[dict]:
    try:
        root = ET.fromstring(xml_bytes)
    except Exception:
        # Malformed XML (XMLParseError) or hostile XML (defusedxml raises
        # EntitiesForbidden / DTDForbidden) — drop the feed, never abort the run.
        return []
    items = []
    for node in root.iter():
        if _localname(node.tag) not in ("item", "entry"):
            continue
        title = url = summary = pub = ""
        for child in node:
            name = _localname(child.tag)
            if name == "title":
                title = (child.text or "").strip()
            elif name == "link":
                url = child.get("href") or (child.text or "").strip() or url
            elif name in ("description", "summary", "content"):
                summary = _strip_tags("".join(child.itertext()))[:500]
            elif name in ("pubdate", "published", "updated") and not pub:
                pub = _iso(child.text or "")
        if not (title and url):
            continue
        items.append({
            "source": f"rss:{feed_name}", "type": "rss",
            "title": title, "url": url, "summary": summary,
            "snippet": "", "published_at": pub,
        })
    return items


def select_hn(stories: list[dict], min_score: int) -> list[dict]:
    out = []
    for s in stories:
        if s.get("type") != "story" or not s.get("url"):
            continue
        if int(s.get("score", 0)) < min_score:
            continue
        ts = s.get("time")
        pub = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat() if ts else ""
        out.append({
            "source": "hn", "type": "hackernews",
            "title": s.get("title", ""), "url": s["url"],
            "summary": "", "snippet": "", "published_at": pub,
        })
    return out


def select_github(repos: list[dict], min_stars_week: int) -> list[dict]:
    out = []
    for r in repos:
        if int(r.get("stargazers_count", 0)) < min_stars_week:
            continue
        desc = r.get("description") or ""
        out.append({
            "source": "github", "type": "github",
            "title": f"{r.get('full_name', '')} — {desc}".strip(" —"),
            "url": r.get("html_url", ""),
            "summary": desc, "snippet": "", "published_at": "",
        })
    return out


# Blunt multilingual denylist for copy-paste prompt-injection payloads. Not a
# security boundary — a first-line filter that drops obvious attempts and logs
# them. Real defense is toolset trimming + write-approval staging (see plan).
_INJECTION_PATTERNS = (
    # English
    r"ignore\s+(all\s+|the\s+)?(previous|above|prior|preceding)\s+(instructions?|prompts?|messages?)",
    r"disregard\s+(all\s+|the\s+)?(previous|above|prior|system)",
    r"forget\s+(everything|all|the\s+above|previous)",
    r"you\s+are\s+now\b",
    r"new\s+instructions?\s*:",
    r"system\s+prompt",
    r"reveal\s+(your|the)\s+(system\s+)?(prompt|instructions)",
    r"override\s+(your|the)\s+(instructions|rules|guardrails)",
    # Russian
    r"игнорируй\s+(все\s+|предыдущие\s+|вышеуказанные\s+)?(инструкции|указания|сообщения)",
    r"забудь\s+(все|всё|предыдущие|указанные)",
    r"нов(ые|ая)\s+инструкц",
    r"систем(ный|ные)\s+промпт",
    r"ты\s+теперь\b",
    r"покажи\s+(свой\s+|системный\s+)?промпт",
    # Spanish
    r"ignora\s+(las\s+)?(instrucciones|indicaciones)\s+(anteriores|previas)",
    r"olvida\s+(todo|las\s+instrucciones)",
    # German
    r"ignoriere\s+(alle\s+|die\s+)?(vorherigen|obigen)\s+(anweisungen|instruktionen)",
    # French
    r"ignore[zr]?\s+les\s+instructions\s+(précédentes|precedentes)",
    # Chinese
    r"忽略(以上|之前|前面)(的)?(指令|指示|说明)",
    r"忘记(以上|之前|所有)",
)
_INJECTION_RE = [re.compile(p, re.IGNORECASE) for p in _INJECTION_PATTERNS]


def looks_like_injection(text: str) -> bool:
    if not text:
        return False
    norm = re.sub(r"\s+", " ", text)
    return any(rx.search(norm) for rx in _INJECTION_RE)


def item_text(item: dict) -> str:
    return " ".join([item.get("title", ""), item.get("summary", ""),
                     item.get("snippet", ""), item.get("canonical_url", "")])
