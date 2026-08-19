#!/usr/bin/env python3
"""Bounded, auditable external-signal collector for ``idle-idea-prompt``.

This is deliberately a finite data pipeline, not an open-ended web research
loop.  It reads the checked-in source registry, makes at most one request plus
one retry per eligible source, validates publication dates, records every
rejection, and writes a structured brief for the downstream idea job.

The script is safe to run in dry-run mode from a checkout.  A scheduled runtime
may point ``--state-dir`` at ``$HERMES_HOME/state`` after review; this module
never configures or changes a cron job itself.
"""
from __future__ import annotations

import argparse
import http.client
import ipaddress
import json
import os
import queue
import re
import socket
import ssl
import sys
import threading
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Callable, Iterable
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

# ``defusedxml`` is only installed with the optional WeCom extra in some
# Hermes environments. Reject DTD/entity declarations before using stdlib XML
# so this standalone cron script remains runnable in the base runtime too.
import xml.etree.ElementTree as ET

import yaml

try:
    from idea_signal_state import state_lock
except ModuleNotFoundError:  # Imported as scripts.idea_signal_collector in tests.
    from scripts.idea_signal_state import state_lock

ALLOWED_STATUSES = frozenset({"candidate", "probation", "active", "degraded", "suspended", "retired"})
ELIGIBLE_STATUSES = frozenset({"probation", "active", "degraded"})
MAX_SOURCES_PER_RUN = 20
MAX_RETRIES = 1
MAX_ITEMS_PER_SOURCE = 2
MAX_ITEMS_PER_BASKET = 2
MAX_SIGNALS_PER_RUN = 10
FRESHNESS_DAYS = 7
MAX_RESPONSE_BYTES = 1_000_000
RESPONSE_READ_CHUNK_BYTES = 64 * 1024
MAX_REDIRECTS = 3
MAX_RESOLVED_ADDRESSES = 8
MAX_CONNECTION_ATTEMPTS = 2
DNS_RESOLUTION_TIMEOUT_SECONDS = 3.0
MAX_CONCURRENT_DNS_RESOLUTIONS = 4
_DNS_RESOLUTION_SLOTS = threading.BoundedSemaphore(MAX_CONCURRENT_DNS_RESOLUTIONS)
REQUIRED_BASKETS = frozenset({
    "health_habits_energy",
    "finance_purchases_risk",
    "learning_work_practices",
    "home_travel_organization",
    "programming_automation_hermes",
    "relationships_leisure_quality_of_life",
})
USER_AGENT = "HermesIdeaCollector/0.1 (+https://github.com/NousResearch/hermes-agent)"
TRACKING_QUERY_KEYS = frozenset({
    "fbclid", "gclid", "ref", "utm_campaign", "utm_content", "utm_medium", "utm_source", "utm_term",
})


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def resolve_registry_path(script_path: Path | None = None) -> Path:
    """Find registry in a checkout, or beside a synced runtime script.

    ``sync-runtime-scripts.sh`` copies scripts into ``$HERMES_HOME/scripts``.
    Keeping a sibling copy of the registry makes the runtime invocation work
    without relying on a checkout-specific absolute path.
    """
    script = script_path or Path(__file__).resolve()
    sibling = script.parent / "idea_sources.yaml"
    if sibling.is_file():
        return sibling
    return script.parents[1] / "config" / "idea_sources.yaml"


def default_registry_path() -> Path:
    return resolve_registry_path()


def default_state_dir() -> Path:
    home = Path(os.environ.get("HERMES_HOME") or (Path.home() / ".hermes"))
    return home / "state"


def canonical_url(url: str) -> str:
    """Remove fragments and common tracking params without altering content URLs."""
    raw = (url or "").strip()
    if not raw:
        return ""
    parts = urlsplit(raw)
    query = urlencode([(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True)
                       if key.lower() not in TRACKING_QUERY_KEYS])
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/") or "/", query, ""))


def normalized_title(title: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", (title or "").casefold())).strip()


def parse_published_at(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(text)
        except (TypeError, ValueError):
            # Some official RSS feeds (notably CPSC) publish a date-only
            # English label instead of RFC 822. It is still a publication date,
            # not an arbitrary page-updated timestamp, so parse it explicitly.
            parsed = None
            for pattern in ("%B %d, %Y", "%b %d, %Y"):
                try:
                    parsed = datetime.strptime(text, pattern)
                    break
                except ValueError:
                    continue
            if parsed is None:
                return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def iso_or_none(value: str | None) -> str | None:
    parsed = parse_published_at(value)
    return parsed.isoformat() if parsed else None


def _text(element: ET.Element | None) -> str:
    return "".join(element.itertext()).strip() if element is not None else ""


def _child_text(element: ET.Element, names: Iterable[str]) -> str:
    wanted = {name.casefold() for name in names}
    for child in element:
        if child.tag.rsplit("}", 1)[-1].casefold() in wanted:
            text = _text(child)
            if text:
                return text
    return ""


def _atom_link(entry: ET.Element) -> str:
    for child in entry:
        if child.tag.rsplit("}", 1)[-1].casefold() != "link":
            continue
        rel = (child.attrib.get("rel") or "alternate").casefold()
        href = child.attrib.get("href") or _text(child)
        if href and rel in {"alternate", ""}:
            return href.strip()
    return ""


def parse_feed(document: str, *, channel: str) -> list[dict]:
    """Parse RSS, Atom, or the documented GitHub releases JSON response."""
    if channel == "github_releases":
        payload = json.loads(document)
        if not isinstance(payload, list):
            raise ValueError("GitHub releases response must be a list")
        items: list[dict] = []
        for release in payload:
            if not isinstance(release, dict):
                continue
            items.append({
                "title": str(release.get("name") or release.get("tag_name") or "").strip(),
                "canonical_url": canonical_url(str(release.get("html_url") or "")),
                "published_at": iso_or_none(release.get("published_at")),
                "summary": str(release.get("body") or "").strip(),
                "channel": channel,
            })
        return items
    if re.search(r"<!\s*(?:DOCTYPE|ENTITY)\b", document, flags=re.IGNORECASE):
        raise ValueError("DTD/entity declarations are not allowed in source feeds")
    root = ET.fromstring(document)
    root_name = root.tag.rsplit("}", 1)[-1].casefold()
    items: list[dict] = []
    if root_name == "rss":
        channel_element = next((child for child in root if child.tag.rsplit("}", 1)[-1].casefold() == "channel"), None)
        entries = [] if channel_element is None else [child for child in channel_element if child.tag.rsplit("}", 1)[-1].casefold() == "item"]
        for entry in entries:
            published = _child_text(entry, ("pubDate", "published", "date"))
            items.append({
                "title": _child_text(entry, ("title",)),
                "canonical_url": canonical_url(_child_text(entry, ("link", "guid"))),
                "published_at": iso_or_none(published),
                "summary": _child_text(entry, ("description", "encoded", "summary")),
                "channel": channel,
            })
    elif root_name == "feed":
        entries = [child for child in root if child.tag.rsplit("}", 1)[-1].casefold() == "entry"]
        for entry in entries:
            # Atom's updated value is intentionally not accepted as publication
            # date: freshness must not be faked by a later metadata edit.
            published = _child_text(entry, ("published",))
            items.append({
                "title": _child_text(entry, ("title",)),
                "canonical_url": canonical_url(_atom_link(entry)),
                "published_at": iso_or_none(published),
                "summary": _child_text(entry, ("summary", "content")),
                "channel": channel,
            })
    else:
        raise ValueError(f"unsupported feed document root: {root_name}")
    return items


def validate_registry(registry: dict) -> list[dict]:
    """Validate registry shape early, before any network work begins."""
    if not isinstance(registry, dict) or not isinstance(registry.get("sources"), list):
        raise ValueError("registry must contain a sources list")
    sources = registry["sources"]
    if len(sources) > MAX_SOURCES_PER_RUN:
        raise ValueError(f"registry has more than {MAX_SOURCES_PER_RUN} sources")
    seen_ids: set[str] = set()
    for source in sources:
        if not isinstance(source, dict):
            raise ValueError("each source must be a mapping")
        source_id = str(source.get("id") or "").strip()
        if not source_id or not re.fullmatch(r"[a-z0-9_\-]+", source_id):
            raise ValueError("source id must be lowercase slug text")
        if source_id in seen_ids:
            raise ValueError(f"duplicate source id: {source_id}")
        seen_ids.add(source_id)
        status = str(source.get("status") or "").strip()
        if status not in ALLOWED_STATUSES:
            raise ValueError(f"source {source_id}: unsupported status {status!r}")
        if not str(source.get("title") or "").strip() or not str(source.get("basket") or "").strip():
            raise ValueError(f"source {source_id}: title and basket are required")
        if status in ELIGIBLE_STATUSES:
            if source.get("channel") not in {"rss", "atom", "github_releases"}:
                raise ValueError(f"source {source_id}: eligible source needs rss, atom, or github_releases channel")
            if not str(source.get("feed_url") or "").startswith("https://"):
                raise ValueError(f"source {source_id}: eligible source needs HTTPS feed_url")
            if source.get("requires_published_date") is not True:
                raise ValueError(f"source {source_id}: eligible source requires requires_published_date: true")
        if status == "candidate" and not str(source.get("discovery_url") or "").startswith("https://"):
            raise ValueError(f"source {source_id}: candidate needs HTTPS discovery_url")
    return sources


def load_registry(path: Path) -> tuple[dict, list[dict]]:
    registry = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return registry, validate_registry(registry)


def request_headers(url: str) -> dict[str, str]:
    """Return content negotiation headers for known registry transports."""
    if urlsplit(url).netloc.casefold() == "api.github.com":
        return {"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"}
    return {
        "User-Agent": USER_AGENT,
        "Accept": "application/atom+xml, application/rss+xml, application/xml, text/xml",
    }


def _is_public_address(address: str) -> bool:
    parsed = ipaddress.ip_address(address)
    return not any((
        parsed.is_loopback,
        parsed.is_private,
        parsed.is_link_local,
        parsed.is_unspecified,
        parsed.is_multicast,
        parsed.is_reserved,
    ))


def _bounded_getaddrinfo(hostname: str, port: int) -> list[tuple]:
    """Resolve in a capped worker so a stalled resolver cannot block a run."""
    if not _DNS_RESOLUTION_SLOTS.acquire(blocking=False):
        raise ValueError("DNS resolution capacity exhausted")
    result: queue.Queue[tuple[bool, object]] = queue.Queue(maxsize=1)

    def resolve() -> None:
        try:
            result.put((True, socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)))
        except OSError as exc:
            result.put((False, exc))
        finally:
            _DNS_RESOLUTION_SLOTS.release()

    threading.Thread(target=resolve, daemon=True).start()
    try:
        succeeded, payload = result.get(timeout=DNS_RESOLUTION_TIMEOUT_SECONDS)
    except queue.Empty as exc:
        raise ValueError("DNS resolution timed out") from exc
    if not succeeded:
        raise ValueError("unsafe redirect destination") from payload
    return payload  # type: ignore[return-value]


def resolve_https_target(url: str) -> tuple[str, str, int, str, tuple[str, ...]]:
    """Resolve a safe HTTPS URL once and return only pinned public addresses."""
    parts = urlsplit(url)
    hostname = parts.hostname
    if parts.scheme.casefold() != "https" or not hostname or parts.username or parts.password:
        raise ValueError("unsafe redirect destination")
    if hostname.casefold() == "localhost" or hostname.casefold().endswith(".localhost"):
        raise ValueError("unsafe redirect destination")
    try:
        literal_address = ipaddress.ip_address(hostname)
    except ValueError:
        literal_address = None
    if literal_address is not None and not _is_public_address(str(literal_address)):
        raise ValueError("unsafe redirect destination")
    try:
        port = parts.port or 443
    except ValueError as exc:
        raise ValueError("unsafe redirect destination") from exc
    addresses = _bounded_getaddrinfo(hostname, port)
    if not addresses or len(addresses) > MAX_RESOLVED_ADDRESSES:
        if len(addresses) > MAX_RESOLVED_ADDRESSES:
            raise ValueError("too many resolved addresses")
        raise ValueError("unsafe redirect destination")
    pinned_addresses = tuple(dict.fromkeys(str(address[0]) for _family, _kind, _proto, _canonname, address in addresses))
    if not pinned_addresses or len(pinned_addresses) > MAX_RESOLVED_ADDRESSES:
        raise ValueError("too many resolved addresses")
    for address in pinned_addresses:
        if not _is_public_address(address):
            raise ValueError("unsafe redirect destination")
    request_target = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
    return url, hostname, port, request_target, pinned_addresses


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """HTTPS connection that dials one vetted IP but verifies its hostname."""

    def __init__(self, hostname: str, port: int, address: str, timeout: int):
        super().__init__(hostname, port=port, timeout=timeout, context=ssl.create_default_context())
        self._pinned_address = address

    def connect(self) -> None:
        self.sock = socket.create_connection(
            (self._pinned_address, self.port), self.timeout, self.source_address,
        )
        self.sock = self._context.wrap_socket(self.sock, server_hostname=self.host)


def _redirect_location(response) -> str | None:
    if hasattr(response, "getheader"):
        return response.getheader("Location")
    return response.headers.get("Location") if response.headers else None


def _fetch_pinned_target(target: tuple[str, str, int, str, tuple[str, ...]], timeout: int) -> tuple[int, str | None, bytes | None]:
    url, hostname, port, request_target, addresses = target
    last_error: Exception | None = None
    for address in addresses[:MAX_CONNECTION_ATTEMPTS]:
        connection = _PinnedHTTPSConnection(hostname, port, address, timeout)
        try:
            connection.request("GET", request_target, headers=request_headers(url))
            response = connection.getresponse()
            if response.status in {301, 302, 303, 307, 308}:
                return response.status, _redirect_location(response), None
            if not 200 <= response.status < 300:
                raise ValueError(f"unexpected HTTP status: {response.status}")
            return response.status, None, _read_response_limited(response)
        except (OSError, ssl.SSLError, http.client.HTTPException) as exc:
            last_error = exc
        finally:
            connection.close()
    raise ValueError("pinned HTTPS connection failed") from last_error


def fetch_url(url: str, timeout: int) -> str:
    current_url = url
    for redirect_count in range(MAX_REDIRECTS + 1):
        target = resolve_https_target(current_url)
        _status, location, body = _fetch_pinned_target(target, timeout)
        if body is not None:
            return body.decode("utf-8", errors="replace")
        if not location or redirect_count == MAX_REDIRECTS:
            raise ValueError("unsafe redirect destination")
        current_url = urljoin(current_url, location)
    raise ValueError("unsafe redirect destination")


def _read_response_limited(response) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = response.read(min(RESPONSE_READ_CHUNK_BYTES, MAX_RESPONSE_BYTES - total + 1))
        if not chunk:
            return b"".join(chunks)
        if not isinstance(chunk, bytes):
            raise ValueError("response body must be bytes")
        total += len(chunk)
        if total > MAX_RESPONSE_BYTES:
            raise ValueError("response exceeds byte ceiling")
        chunks.append(chunk)




def _source_state(state: dict, source: dict) -> dict:
    source_id = source["id"]
    record = dict(state.get(source_id) or {})
    # States persisted before reviewed_status existed retain their current
    # admission; only an explicit reviewed lifecycle change resets a trial.
    record.setdefault("reviewed_status", source["status"])
    record.setdefault("effective_status", source["status"])
    record.setdefault("runs", 0)
    record.setdefault("successful_runs", 0)
    record.setdefault("items_seen", 0)
    record.setdefault("valid_date_items", 0)
    record.setdefault("accepted_items", 0)
    record.setdefault("duplicate_items", 0)
    record.setdefault("recent_results", [])
    return record


def _fresh_source_state(reviewed_status: str) -> dict:
    return {
        "reviewed_status": reviewed_status,
        "effective_status": reviewed_status,
        "runs": 0,
        "successful_runs": 0,
        "items_seen": 0,
        "valid_date_items": 0,
        "accepted_items": 0,
        "duplicate_items": 0,
        "recent_results": [],
    }


def next_source_status(record: dict) -> str:
    """Apply documented promotion, degradation, and suspension thresholds."""
    current = str(record.get("effective_status") or "probation")
    recent = list(record.get("recent_results") or [])
    items_seen = int(record.get("items_seen") or 0)
    valid_dates = int(record.get("valid_date_items") or 0)
    duplicates = int(record.get("duplicate_items") or 0)
    if len(recent) >= 3 and recent[-3:] == [False, False, False]:
        return "suspended"
    if len(recent[-7:]) >= 7 and recent[-7:].count(False) >= 5:
        return "suspended"
    if items_seen >= 10 and valid_dates / items_seen < 0.90:
        return "suspended"
    if current != "probation" and items_seen >= 10 and duplicates / items_seen > 0.70:
        return "suspended"
    if current == "active" and recent and recent[-1] is False:
        return "degraded"
    if current == "degraded" and len(recent) >= 3 and recent[-3:] == [True, True, True]:
        return "active"
    if current == "degraded":
        return "degraded"
    if current == "probation":
        accepted = int(record.get("accepted_items") or 0)
        if (
            int(record.get("runs") or 0) >= 5
            and int(record.get("successful_runs") or 0) >= 3
            and items_seen >= 10
            and valid_dates / items_seen >= 0.90
            and accepted / items_seen >= 0.30
        ):
            return "active"
    return current


def _event(source_id: str, event: str, now: datetime, **extra: object) -> dict:
    return {"source_id": source_id, "event": event, "observed_at": now.isoformat(), **extra}


def _brief_status(
    *,
    signals: list[dict],
    failures: list[dict],
    completed_sources: int,
    skipped_sources: int,
    min_usable: int,
    used_degraded_source: bool,
    missing_baskets: list[str],
) -> str:
    if signals and (failures or used_degraded_source or missing_baskets):
        return "degraded"
    if signals and len(signals) >= min_usable:
        return "ok"
    if signals:
        return "degraded"
    if skipped_sources and not failures and not completed_sources:
        return "no_signals"
    return "no_signals" if completed_sources else "failed"


def collect_sources(
    sources: list[dict],
    fetch: Callable[[str, int], str] = fetch_url,
    *,
    now: datetime | None = None,
    state: dict | None = None,
    seen: dict[str, str] | None = None,
    min_usable_signals: int = 1,
) -> tuple[dict, dict, list[dict]]:
    """Fetch one bounded batch and return (brief, new_health_state, events)."""
    now = now or utc_now()
    state = dict(state or {})
    seen = seen if seen is not None else {}
    prune_seen(seen, now=now)
    cutoff = now - timedelta(days=FRESHNESS_DAYS)
    signals: list[dict] = []
    failures: list[dict] = []
    attempted: list[dict] = []
    rejections: list[dict] = []
    events: list[dict] = []
    batch_urls: set[str] = set()
    batch_titles: set[str] = set()
    source_counts: dict[str, int] = {}
    basket_counts: dict[str, int] = {}
    completed_sources = 0
    skipped_sources = 0
    used_degraded_source = False

    for source in sources[:MAX_SOURCES_PER_RUN]:
        source_id = source["id"]
        configured_status = source["status"]
        record = _source_state(state, source)
        if record.get("reviewed_status") != configured_status:
            # A reviewed lifecycle change restarts admission. In particular,
            # candidate demotion must erase stale active/probation evidence so
            # a later probation promotion is a genuinely new trial.
            record = _fresh_source_state(configured_status)
        effective_status = record["effective_status"]
        # collection. A source in probation may be promoted by the audited
        # health state without rewriting the registry on every successful run.
        if configured_status in {"candidate", "suspended", "retired"} or effective_status in {"suspended", "retired"}:
            skipped_sources += 1
            attempted.append({"source_id": source_id, "outcome": "skipped", "reason": effective_status})
            state[source_id] = record
            continue

        response: str | None = None
        error_type: str | None = None
        attempt_budget = min(MAX_RETRIES, max(0, int(source.get("retry_count") or 0))) + 1
        actual_attempts = 0
        for _attempt in range(attempt_budget):
            actual_attempts += 1
            try:
                response = fetch(source["feed_url"], min(10, max(1, int(source.get("timeout_seconds") or 10))))
                break
            except Exception as exc:  # bounded retry; error is recorded, never retried indefinitely
                error_type = type(exc).__name__
        record["runs"] += 1
        if response is None:
            record["recent_results"] = (list(record["recent_results"]) + [False])[-10:]
            failures.append({"source_id": source_id, "reason": f"fetch_failed: {error_type or 'unknown'}"})
            attempted.append({"source_id": source_id, "outcome": "failed", "attempts": actual_attempts, "reason": error_type or "unknown"})
        else:
            try:
                items = parse_feed(response, channel=source["channel"])
            except Exception as exc:
                record["recent_results"] = (list(record["recent_results"]) + [False])[-10:]
                failures.append({"source_id": source_id, "reason": f"parse_failed: {type(exc).__name__}"})
                attempted.append({"source_id": source_id, "outcome": "failed", "attempts": actual_attempts, "reason": type(exc).__name__})
            else:
                completed_sources += 1
                record["successful_runs"] += 1
                record["recent_results"] = (list(record["recent_results"]) + [True])[-10:]
                record["items_seen"] += len(items)
                attempted.append({"source_id": source_id, "outcome": "ok", "attempts": actual_attempts, "items_seen": len(items)})
                for item in items:
                    title = (item.get("title") or "").strip()
                    url = item.get("canonical_url") or ""
                    published_at = item.get("published_at")
                    if not title or not url:
                        rejections.append({"source_id": source_id, "reason": "missing_title_or_url", "title": title})
                        continue
                    if source.get("requires_published_date", True) and not published_at:
                        rejections.append({"source_id": source_id, "reason": "missing_published_date", "title": title})
                        continue
                    parsed_date = parse_published_at(published_at)
                    if not parsed_date:
                        rejections.append({"source_id": source_id, "reason": "invalid_published_date", "title": title})
                        continue
                    record["valid_date_items"] += 1
                    if parsed_date > now + timedelta(minutes=5):
                        rejections.append({"source_id": source_id, "reason": "future_dated", "title": title})
                        continue
                    if parsed_date < cutoff:
                        rejections.append({"source_id": source_id, "reason": "stale", "title": title})
                        continue
                    title_key = normalized_title(title)
                    if f"url:{url}" in seen or f"title:{title_key}" in seen:
                        # Durable prior-run rejections are duplicates too: omitting
                        # them would let a source replay the same small corpus
                        # forever without reaching the documented duplicate-rate
                        # suspension threshold.
                        if record["effective_status"] != "probation":
                            record["duplicate_items"] += 1
                        rejections.append({"source_id": source_id, "reason": "seen_in_prior_run", "title": title})
                        continue
                    if url in batch_urls:
                        if record["effective_status"] != "probation":
                            record["duplicate_items"] += 1
                        rejections.append({"source_id": source_id, "reason": "duplicate_url", "title": title})
                        continue
                    if title_key in batch_titles:
                        if record["effective_status"] != "probation":
                            record["duplicate_items"] += 1
                        rejections.append({"source_id": source_id, "reason": "duplicate_title", "title": title})
                        continue
                    batch_urls.add(url)
                    batch_titles.add(title_key)
                    seen[f"url:{url}"] = now.isoformat()
                    seen[f"title:{title_key}"] = now.isoformat()
                    record["accepted_items"] += 1
                    if record["effective_status"] not in {"active", "degraded"}:
                        # Probation is observable but cannot feed ideas before promotion.
                        continue
                    if source_counts.get(source_id, 0) >= min(MAX_ITEMS_PER_SOURCE, int(source.get("max_items_per_run") or MAX_ITEMS_PER_SOURCE)):
                        rejections.append({"source_id": source_id, "reason": "source_cap", "title": title})
                        continue
                    basket = source["basket"]
                    if basket_counts.get(basket, 0) >= MAX_ITEMS_PER_BASKET:
                        rejections.append({"source_id": source_id, "reason": "basket_cap", "title": title})
                        continue
                    if len(signals) >= MAX_SIGNALS_PER_RUN:
                        rejections.append({"source_id": source_id, "reason": "run_cap", "title": title})
                        continue
                    source_counts[source_id] = source_counts.get(source_id, 0) + 1
                    basket_counts[basket] = basket_counts.get(basket, 0) + 1
                    seen[f"url:{url}"] = now.isoformat()
                    seen[f"title:{title_key}"] = now.isoformat()
                    if record["effective_status"] == "degraded":
                        used_degraded_source = True
                    signals.append({
                        "source_id": source_id,
                        "source_status": record["effective_status"],
                        "basket": basket,
                        "title": title,
                        "url": url,
                        "published_at": parsed_date.isoformat(),
                        "source_type": source.get("authority", "unknown"),
                        "trust_tier": source.get("trust_tier", "C"),
                        "fact": re.sub(r"\s+", " ", (item.get("summary") or title)).strip()[:800],
                        "practical_angle": "Нужна отдельная осторожная интерпретация; сам сигнал не является рекомендацией.",
                    })
        old_status = record["effective_status"]
        new_status = next_source_status(record)
        if new_status != old_status:
            record["effective_status"] = new_status
            events.append(_event(source_id, new_status, now, previous_status=old_status, metrics={
                "runs": record["runs"], "successful_runs": record["successful_runs"], "items_seen": record["items_seen"],
                "valid_date_items": record["valid_date_items"], "accepted_items": record["accepted_items"],
            }))
        state[source_id] = record

    known_baskets = sorted(REQUIRED_BASKETS | {source["basket"] for source in sources})
    missing_baskets = [basket for basket in known_baskets if not basket_counts.get(basket)]
    brief = {
        "run_id": f"idea-signals-{now.strftime('%Y%m%dT%H%M%SZ')}",
        "run_status": _brief_status(
            signals=signals,
            failures=failures,
            completed_sources=completed_sources,
            skipped_sources=skipped_sources,
            min_usable=min_usable_signals,
            used_degraded_source=used_degraded_source,
            missing_baskets=missing_baskets,
        ),
        "collected_at": now.isoformat(),
        "cutoff": cutoff.isoformat(),
        "signals": signals,
        "missing_baskets": missing_baskets,
        "attempted_sources": attempted,
        "source_failures": failures,
        "rejections": rejections,
        "emitted_seen": seen,
    }
    return brief, state, events


def load_state(state_dir: Path) -> dict:
    try:
        return json.loads((state_dir / "idea_source_health.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def load_seen(state_dir: Path) -> dict[str, str]:
    """Load durable prior-run de-duplication keys without failing collection."""
    try:
        payload = json.loads((state_dir / "idea_signal_seen.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    return {str(key): str(value) for key, value in payload.items()}


def prune_seen(seen: dict[str, str], *, now: datetime, ttl: timedelta = timedelta(days=30)) -> None:
    for key, seen_at in list(seen.items()):
        parsed = parse_published_at(seen_at)
        if parsed is None or now - parsed > ttl:
            seen.pop(key, None)


def load_usable_brief(
    state_dir: Path,
    *,
    now: datetime | None = None,
    max_age: timedelta = timedelta(hours=30),
) -> dict | None:
    """Return only a current, successful/degraded collector handoff.

    Consumers must never quietly treat a failed, no-signal, malformed, or stale
    output as fresh research.  ``None`` means "use no external-signal claims".
    """
    try:
        brief = json.loads((state_dir / "idea_signal_brief.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(brief, dict) or brief.get("run_status") not in {"ok", "degraded"}:
        return None
    if not isinstance(brief.get("run_id"), str) or not isinstance(brief.get("signals"), list):
        return None
    if not any(
        isinstance(signal, dict)
        and str(signal.get("title") or "").strip()
        and str(signal.get("url") or "").startswith("https://")
        for signal in brief["signals"]
    ):
        return None
    collected_at = parse_published_at(brief.get("collected_at"))
    current = now or utc_now()
    if collected_at is None or collected_at > current + timedelta(minutes=5) or current - collected_at > max_age:
        return None
    return brief


def _atomic_write_json(path: Path, payload: object) -> None:
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp_path.replace(path)


def persist_run(
    state_dir: Path,
    brief: dict,
    state: dict,
    events: list[dict],
    *,
    seen: dict[str, str] | None = None,
) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    _atomic_write_json(state_dir / "idea_signal_brief.json", brief)
    _atomic_write_json(state_dir / "idea_source_health.json", state)
    if seen is not None:
        _atomic_write_json(state_dir / "idea_signal_seen.json", seen)
    if events:
        with (state_dir / "idea_source_events.jsonl").open("a", encoding="utf-8") as event_file:
            for event in events:
                event_file.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")


def run(registry_path: Path, state_dir: Path, *, dry_run: bool = False) -> dict:
    if dry_run:
        registry, sources = load_registry(registry_path)
        brief, _state, _events = collect_sources(
            sources,
            now=utc_now(),
            state=load_state(state_dir),
            seen=load_seen(state_dir),
            min_usable_signals=max(1, int(registry.get("min_usable_signals") or 1)),
        )
        brief.pop("emitted_seen", None)
        return brief
    with state_lock(state_dir):
        registry, sources = load_registry(registry_path)
        prior_seen = load_seen(state_dir)
        brief, state, events = collect_sources(
            sources,
            now=utc_now(),
            state=load_state(state_dir),
            seen=prior_seen,
            min_usable_signals=max(1, int(registry.get("min_usable_signals") or 1)),
        )
        persist_run(state_dir, brief, state, events, seen=brief.pop("emitted_seen"))
        return brief


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, default=default_registry_path())
    parser.add_argument("--state-dir", type=Path, default=default_state_dir())
    parser.add_argument("--dry-run", action="store_true", help="Collect and print JSON without writing state")
    args = parser.parse_args(argv)
    try:
        brief = run(args.registry, args.state_dir, dry_run=args.dry_run)
    except Exception as exc:
        failure = {"run_id": f"idea-signals-{utc_now().strftime('%Y%m%dT%H%M%SZ')}", "run_status": "failed", "error": f"{type(exc).__name__}: {exc}"}
        print(json.dumps(failure, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps(brief, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
