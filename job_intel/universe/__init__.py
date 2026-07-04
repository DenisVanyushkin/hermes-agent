"""Company universe discovery (MVP-0): read-only weekly discovery report.

Writes nothing to production seeds or config — the only writes are the
`company_candidate_cache` table and the report notification row.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import date
from pathlib import Path

from . import dry_run, endpoints
from .anchors import BEHAVIORAL_ANCHORS, load_editorial_anchors
from .buckets import classify
from .discover import (discover_d1, discover_d7, ensure_cache_table,
                       is_discovery_noise, load_cache, merge_candidates, save_cache)
from .models import CandidateCompany
from .report import format_universe_report

log = logging.getLogger(__name__)

_PROBE_DELAY = 1.0
_MAX_PROBE_ATTEMPTS = 3
_BLOCKLIST = Path(__file__).parent / "data" / "blocklist.json"

_ATS_SOURCES = ("greenhouse", "lever", "ashby", "smartrecruiters", "recruitee",
                "teamtailor")


def _open_ro_conn():
    from ..cli import _store
    store = _store()
    store.bootstrap()
    return store.connect(read_only=True)


def _open_cache_conn():
    from ..cli import _store
    return _store().connect()


def _exclude_slugs() -> set[str]:
    from ..ats_sources import env_ats_seeds
    slugs: set[str] = set(BEHAVIORAL_ANCHORS)
    slugs.update(load_editorial_anchors())
    for source in _ATS_SOURCES:
        slugs.update(env_ats_seeds(source))
    try:
        from ..config import load_config_bundle
        cfg = load_config_bundle() or {}
        slugs.update((cfg.get("target_companies") or {}).keys())
    except Exception:  # noqa: BLE001 — config problems must not kill discovery
        log.warning("universe: could not load target_companies for exclusion")
    return slugs


def _apply_blocklist(candidates: list[CandidateCompany]) -> None:
    try:
        block = json.loads(_BLOCKLIST.read_text())
    except FileNotFoundError:
        return
    for reason in ("reputation_risk", "low_relevance"):
        listed = {s.strip().lower() for s in block.get(reason, [])}
        for c in candidates:
            if c.slug in listed or c.name.strip().lower() in listed:
                c.add_reason(reason, "static blocklist")


def run_universe_discovery(*, deliver: bool = True, probe_budget: int = 40) -> str:
    ro = _open_ro_conn()
    exclude = _exclude_slugs()
    candidates = merge_candidates(
        discover_d7(ro, exclude_slugs=exclude),
        discover_d1(exclude_slugs=exclude),
    )

    cache_conn = _open_cache_conn()
    ensure_cache_table(cache_conn)
    cached = load_cache(cache_conn)

    _apply_blocklist(candidates)

    probes_used = 0
    candidates.sort(key=lambda c: len(c.evidence), reverse=True)
    for c in candidates:
        if "reputation_risk" in c.reasons or "low_relevance" in c.reasons:
            continue
        entry = cached.get(c.slug)
        if entry and entry.get("ats_type"):
            c.ats_type = entry["ats_type"]
            c.endpoint_url = entry.get("endpoint_url")
            c.add_reason("supported_ats", f"{c.ats_type} (cached): {c.endpoint_url}")
            continue
        if entry and entry.get("probe_attempts", 0) >= _MAX_PROBE_ATTEMPTS:
            c.add_reason("no_endpoint", "no supported ATS endpoint (cached)")
            continue
        if probes_used >= probe_budget:
            continue  # unprobed this run; retried next week
        hit = endpoints.probe_ats(c.slug)
        probes_used += 1
        if _PROBE_DELAY:
            time.sleep(_PROBE_DELAY)
        if hit is None:
            if any("http" in e for e in c.evidence):
                c.add_reason("browser_required", "career page found but no ATS endpoint")
            else:
                c.add_reason("no_endpoint", "no supported ATS endpoint responded")
            continue
        c.ats_type, c.endpoint_url = hit
        c.add_reason("supported_ats", f"{c.ats_type}: {c.endpoint_url}")

    # HH/local-only noise never reaches the report body — suppressed into the
    # rejected summary unless a supported ATS endpoint rescued it above.
    for c in candidates:
        if is_discovery_noise(c) and "low_relevance" not in c.reasons:
            c.add_reason("low_relevance", "hh/local-only entity without supported ATS")

    for c in candidates:
        if "supported_ats" in c.reasons and c.dry_run_vacancies < 0:
            dry_run.dry_run_candidate(c)

    for c in candidates:
        classify(c)

    save_cache(cache_conn, candidates)

    iso = date.today().isocalendar()
    message = format_universe_report(candidates, week_label=f"{iso.year}-W{iso.week:02d}")

    if deliver:
        from ..cli import (_deliver_to_slack, _delivery_db_status,
                           _search_report_channel, _store)
        from ..config import DEFAULT_CONFIG, load_config_bundle
        store = _store()
        run_id = store.start_run("universe_discovery")
        cfg = load_config_bundle() or DEFAULT_CONFIG
        channel = _search_report_channel(cfg)
        notification_id = store.create_notification(
            run_id, channel, "company_universe", message,
            notification_kind="company_universe", delivery_status="pending")
        delivery = _deliver_to_slack(message, channel)
        store.mark_notification_delivery(
            notification_id, _delivery_db_status(delivery),
            attempts=delivery.attempts, delivery_error=delivery.error)
        store.finish_run(run_id, "ok")
    return message
