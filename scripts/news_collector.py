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
    if not url or not url.strip():
        return ""          # empty input -> empty, so url-less items are skipped
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
    _oldest = datetime.min.replace(tzinfo=timezone.utc)
    fresh.sort(key=lambda i: (_parse_iso(i["published_at"]) or _oldest), reverse=True)
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
                href = child.get("href") or (child.text or "").strip()
                rel = (child.get("rel") or "alternate").lower()
                if href and (rel == "alternate" or not url):
                    url = href
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
# NOTE: keep patterns IMPERATIVE multi-word attack phrasings, not bare topic
# words — this is AI/LLM news where "system prompt"/"prompt injection" are
# legitimate subjects; bare patterns cause false drops.
_INJECTION_PATTERNS = (
    # English
    r"ignore\s+(all\s+|the\s+)?(previous|above|prior|preceding)\s+(instructions?|prompts?|messages?)",
    r"disregard\s+(all\s+|the\s+)?(previous|above|prior|system)",
    r"forget\s+(everything|all|the\s+above|previous)",
    r"new\s+instructions?\s*:",
    r"reveal\s+(your|the)\s+(system\s+)?(prompt|instructions)",
    r"override\s+(your|the)\s+(instructions|rules|guardrails)",
    # Russian
    r"игнорируй\s+(все\s+|предыдущие\s+|вышеуказанные\s+)?(инструкции|указания|сообщения)",
    r"забудь\s+(все|всё|предыдущие|указанные)",
    r"нов(ые|ая)\s+инструкц",
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


_UA = "Mozilla/5.0 (compatible; HermesNewsCollector/1.0)"


def http_get(url: str, timeout: int) -> bytes:
    if urlsplit(url).scheme not in ("http", "https"):
        raise ValueError(f"refusing non-http(s) URL: {url!r}")
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def fetch_telegram(channel: str, cfg: dict) -> list[dict]:
    html_text = http_get(f"https://t.me/s/{channel}", cfg["http_timeout"]).decode("utf-8", "replace")
    return parse_telegram_html(html_text, channel)


def fetch_feed(url: str, cfg: dict) -> list[dict]:
    name = urlsplit(url).netloc.split(".")[-2] if "." in urlsplit(url).netloc else url
    return parse_feed(http_get(url, cfg["http_timeout"]), name)


def fetch_hn(cfg: dict) -> list[dict]:
    ids = json.loads(http_get("https://hacker-news.firebaseio.com/v0/topstories.json",
                              cfg["http_timeout"]))[:60]
    stories = []
    for i in ids:
        try:
            stories.append(json.loads(http_get(
                f"https://hacker-news.firebaseio.com/v0/item/{i}.json", cfg["http_timeout"])))
        except Exception:
            continue
    return select_hn(stories, cfg["hackernews"]["min_score"])


def fetch_github(cfg: dict) -> list[dict]:
    topics = "+".join(f"topic:{t}" for t in cfg["github_trending"]["topics"])
    q = f"https://api.github.com/search/repositories?q={topics}+pushed:>2026-01-01&sort=stars&order=desc&per_page=30"
    repos = json.loads(http_get(q, cfg["http_timeout"])).get("items", [])
    return select_github(repos, cfg["github_trending"]["min_stars_week"])


def gather_items(cfg: dict, fetchers: dict) -> tuple[list, list, list]:
    raw, errors = [], []
    for ch in cfg.get("telegram_channels", []):
        try:
            raw += [normalize_item(x) for x in fetchers["telegram"](ch, cfg)]
        except Exception as e:
            errors.append(f"tg:{ch}: {e}")
    for url in cfg.get("rss_feeds", []):
        try:
            raw += [normalize_item(x) for x in fetchers["feed"](url, cfg)]
        except Exception as e:
            errors.append(f"rss:{url}: {e}")
    for key in ("hn", "github"):
        try:
            raw += [normalize_item(x) for x in fetchers[key](cfg)]
        except Exception as e:
            errors.append(f"{key}: {e}")
    kept, dropped = [], []
    for it in raw:
        (dropped if looks_like_injection(item_text(it)) else kept).append(it)
    return kept, errors, dropped


def write_candidates(dir_path: Path, emitted, carried, errors, dropped, now) -> Path:
    payload = {
        "generated_at": now.isoformat(),
        "count": len(emitted),
        "carried_count": len(carried),
        "dropped_injection": len(dropped),
        "errors": errors,
        "items": emitted,
    }
    blob = json.dumps(payload, ensure_ascii=False, indent=2)
    latest = Path(dir_path) / "candidates-latest.json"
    dated = Path(dir_path) / f"candidates-{now:%Y%m%d}.json"
    latest.write_text(blob, encoding="utf-8")
    dated.write_text(blob, encoding="utf-8")
    for old in Path(dir_path).glob("candidates-2*.json"):
        try:
            stamp = datetime.strptime(old.stem.split("-")[1], "%Y%m%d").replace(tzinfo=timezone.utc)
            if (now - stamp).days > 14:
                old.unlink()
        except (ValueError, IndexError):
            continue
    return latest


def main() -> int:
    cfg = load_sources(news_dir() / "sources.yaml")
    now = datetime.now(timezone.utc)
    conn = seen_connect(news_dir() / "seen.sqlite")
    prune_seen(conn, now)
    fetchers = {"telegram": fetch_telegram, "feed": fetch_feed,
                "hn": fetch_hn, "github": fetch_github}
    items, errors, dropped = gather_items(cfg, fetchers)
    emitted, carried = select_candidates(
        items, conn, now, cfg["max_items_per_day"], cfg["freshness_hours"])
    write_candidates(news_dir(), emitted, carried, errors, dropped, now)
    log = news_dir() / "collector.log"
    with log.open("a", encoding="utf-8") as f:
        f.write(f"{now.isoformat()} emitted={len(emitted)} carried={len(carried)} "
                f"dropped_injection={len(dropped)} errors={len(errors)}\n")
        for it in dropped:
            f.write(f"  DROPPED_INJECTION source={it.get('source')} "
                    f"url={it.get('canonical_url')} title={it.get('title','')[:80]!r}\n")
        for e in errors:
            f.write(f"  SOURCE_ERROR {e}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
