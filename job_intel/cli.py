from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from time import perf_counter
from pathlib import Path
from typing import Any

import requests

from tools.send_message_tool import send_message_tool

from .browser_sourcing import metrics_from_counts
from .ats_sources import (
    env_ats_seeds,
    fetch_ashby,
    fetch_greenhouse,
    fetch_lever,
    discover_ats_seeds_from_career_urls,
    fetch_personio,
    fetch_recruitee,
    fetch_smartrecruiters,
    fetch_teamtailor,
    status_from_hits_errors,
)
from .company_intel import build_market_report, monitor_target_companies
from .config import DEFAULT_CONFIG, load_config_bundle
from .crm_service import CRMService
from .crm_reconciler import CRMReconciler
from .strategic import build_strategic_report, update_strategic_layer
from .dedup import canonical_job_url, canonical_vacancy_key, description_similarity, is_duplicate
from .digest import (
    format_daily_digest,
    format_enrichment_questions,
    format_executive_opportunity_report,
    format_health_warning,
    format_vacancy_summary,
    format_weekly_source_quality,
    reject_reason_bucket,
)
from .evaluator import classify_vacancy, score_vacancy, score_vacancy_with_version, score_vacancy_v3_shadow
from .enrichment import detect_high_value_questions
from .observability import JobIntelObservabilityExporter, derive_source_reason, record_daily_observability
from .performance import (
    PerformanceSpan,
    RunPerformanceRecorder,
    build_cli_performance_report,
    build_source_value_rows,
    format_compact_performance_block,
    format_detailed_performance_report,
    performance_trigger_reason,
)
from .models import Evaluation, Vacancy
from .runtime import (
    assert_runtime_contract,
    build_runtime_contract,
    file_access_flags,
    parse_iso_datetime,
    resolve_db_path,
    resolve_environment_name,
    resolve_scripts_dir,
    resolve_workdir,
    runtime_home,
    runtime_user,
    retry_with_backoff,
    sha256_text,
)
from .sources import (
    SourceFetchError,
    discovery_queries,
    extract_duckduckgo_destination_url,
    fetch_company_career_vacancies,
    fetch_headhunter_vacancies,
    fetch_linkedin_vacancies,
    normalize_search_hit,
    rotating_source_queries,
    search_duckduckgo,
    search_remoteok_jobs,
    search_remotive_jobs,
)
from .store import JobIntelStore

DEFAULT_DB = resolve_db_path()
logger = logging.getLogger(__name__)
TIER_ORDER = {"reject": 0, "weak_fit": 1, "possible_fit": 2, "strong_fit": 3, "exceptional_fit": 4}
RECOMMENDATION_ORDER = {
    "reject": 0,
    "near_miss": 1,
    "possible_fit": 2,
    "needs_review": 2,
    "potential_fit": 2,
    "strong_fit": 3,
    "exceptional_fit": 4,
}


@dataclass(frozen=True)
class SlackDeliveryResult:
    success: bool
    attempts: int
    error: str | None = None
    status: str = "sent"
    message_ts: str | None = None
    channel_id: str | None = None
    platform_message_id: str | None = None


@dataclass(frozen=True)
class CollectedVacancies:
    vacancies: list[Vacancy]
    source_statuses: dict[str, dict[str, Any]]


@dataclass(frozen=True)
class CardDecisionPlan:
    should_notify: bool
    decision: str
    suppression_reason: str | None
    previous_sent_run_id: int | None
    previous_sent_at: str | None
    previous_feedback_label: str | None
    feedback_state_active: bool
    next_allowed_send_at: str | None
    score_delta_since_last_sent: int | None
    recommendation_changed_since_last_sent: bool
    content_hash_changed: bool
    description_hash_changed: bool


@dataclass
class _NullPerfSpan:
    def set_counts(self, **kwargs: Any) -> None:
        return None

    def add_metadata(self, **kwargs: Any) -> None:
        return None

    def set_status(self, status: str) -> None:
        return None


class _NullSpanContext:
    def __enter__(self) -> _NullPerfSpan:
        return _NullPerfSpan()

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


def _null_span() -> _NullSpanContext:
    return _NullSpanContext()


def _aggregate_browser_trace(target: dict[str, Any], trace: dict[str, Any] | None) -> None:
    if not trace:
        return
    for key, value in trace.items():
        if isinstance(value, (int, float)):
            target[key] = int(target.get(key) or 0) + int(value)
            continue
        if key == "zero_result_reason" and value:
            reasons = target.setdefault("zero_result_reasons", {})
            reasons[str(value)] = int(reasons.get(str(value)) or 0) + 1


def _emit_browser_trace_spans(
    performance: RunPerformanceRecorder | None,
    *,
    source: str,
    parent_span_name: str,
    trace: dict[str, Any],
) -> None:
    if not performance or not trace:
        return
    mappings = [
        (f"{source}.browser_start", "browser_start_ms"),
        (f"{source}.search_pages", "search_pages_ms"),
        (f"{source}.detail_pages", "detail_pages_ms"),
        (f"{source}.extract", "extract_ms"),
        (f"{source}.normalize", "normalize_ms"),
        (f"{source}.filter", "filter_ms"),
        (f"{source}.session_health", "session_health_ms"),
        (f"{source}.login_wall_check", "login_wall_check_ms"),
    ]
    shared_metadata = {
        "pages_fetched": trace.get("pages_fetched"),
        "detail_pages_opened": trace.get("detail_pages_opened"),
        "vacancies_extracted": trace.get("vacancies_extracted"),
        "login_wall_hits": trace.get("login_wall_hits"),
        "auth_redirects": trace.get("auth_redirects"),
        "anti_bot_events": trace.get("anti_bot_events"),
        "zero_result_reasons": trace.get("zero_result_reasons"),
        "browser_attach_retry_count": trace.get("browser_attach_retry_count"),
        "planned_search_pages": trace.get("planned_search_pages"),
    }
    for span_name, field in mappings:
        performance.record_completed(
            span_name,
            parent_span_name=parent_span_name,
            source_name=source,
            duration_ms=int(trace.get(field) or 0),
            metadata=shared_metadata,
            found_count=int(trace.get("pages_fetched") or 0),
            normalized_count=int(trace.get("vacancies_extracted") or 0),
            error_count=int(trace.get("anti_bot_events") or 0),
            timeout_count=int(trace.get("timeout_count") or 0),
        )


def _slack_delivery_phase_status(
    *,
    summary_delivery: SlackDeliveryResult,
    vacancy_deliveries: list[dict[str, Any]],
    performance_delivery: SlackDeliveryResult | None = None,
) -> str:
    if not summary_delivery.success:
        return "error"
    card_failures = sum(1 for item in vacancy_deliveries if not item["delivery"].success)
    if card_failures:
        return "partial"
    if performance_delivery is not None and not performance_delivery.success:
        return "partial"
    return "ok"


def _daily_run_total_status(run_status: str, spans: list[PerformanceSpan]) -> str:
    if run_status != "ok":
        return "error"
    if any(str(span.status) == "partial" for span in spans):
        return "partial"
    return "ok"



def _coerce_collected(result: CollectedVacancies | tuple[list[Vacancy], dict[str, dict[str, Any]]]) -> CollectedVacancies:
    if isinstance(result, CollectedVacancies):
        return result
    if isinstance(result, list):
        return CollectedVacancies(vacancies=list(result), source_statuses={})
    vacancies, source_statuses = result
    return CollectedVacancies(vacancies=list(vacancies), source_statuses=source_statuses)



def _store() -> JobIntelStore:
    return JobIntelStore(resolve_db_path())



def _source_status_template(source: str, *, status: str, **details: Any) -> dict[str, Any]:
    payload = {"source": source, "status": status}
    payload.update(details)
    return payload


def _is_timeout_only_error(message: str) -> bool:
    lowered = (message or "").lower()
    return "connecttimeout" in lowered or "timed out" in lowered or "read timed out" in lowered


_SOURCE_FILTER_ALIASES = {
    "target_companies": "target_companies",
    "target-companies": "target_companies",
    "linkedin": "linkedin",
    "headhunter": "headhunter",
    "hh": "headhunter",
    "greenhouse": "greenhouse",
    "lever": "lever",
    "ashby": "ashby",
    "teamtailor": "teamtailor",
    "smartrecruiters": "smartrecruiters",
    "personio": "personio",
    "recruitee": "recruitee",
    "duckduckgo": "duckduckgo",
    "remoteok": "remoteok",
    "remotive": "remotive",
}

_REGISTRY_ATS_SOURCES = {
    "greenhouse",
    "lever",
    "ashby",
    "teamtailor",
    "smartrecruiters",
    "personio",
    "recruitee",
}


def _registry_seed_path() -> Path:
    raw = (os.getenv("JOB_INTEL_COMPANY_REGISTRY_PATH", "") or "").strip()
    if raw:
        return Path(raw)
    return resolve_workdir() / "docs" / "company-registry-seed.yaml"


def _load_company_registry() -> list[dict[str, Any]]:
    """Load machine-readable registry seed.

    Format is JSON content (valid YAML superset) with a list of company records.
    """
    path = _registry_seed_path()
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(payload, list):
        return []
    records: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        records.append(dict(item))
    return records


def _registry_entries_for_source(source: str) -> list[dict[str, Any]]:
    source_key = (source or "").strip().lower()
    out: list[dict[str, Any]] = []
    for row in _load_company_registry():
        vendor = str(row.get("ats_vendor") or "").strip().lower()
        enabled = bool(row.get("acquisition_enabled", True))
        slug = str(row.get("ats_slug") or "").strip()
        if vendor != source_key or not enabled or not slug:
            continue
        out.append(row)
    return out


def _enabled_sources() -> set[str] | None:
    raw = os.getenv("JOB_INTEL_ENABLED_SOURCES", "").strip()
    if not raw:
        # Safe-by-default production path.
        # RemoteOK/Remotive are intentionally excluded (executive density is too low).
        return {
            "target_companies",
            "linkedin",
            "headhunter",
            "greenhouse",
            "lever",
            "ashby",
            "teamtailor",
            "smartrecruiters",
            "personio",
            "recruitee",
        }
    enabled: set[str] = set()
    for part in raw.split(','):
        key = _SOURCE_FILTER_ALIASES.get(part.strip().lower())
        if key:
            enabled.add(key)
    return enabled


def _source_enabled(enabled: set[str] | None, source: str) -> bool:
    return enabled is None or source in enabled


def _skipped_source_status(source: str, *, acquisition: str | None = None) -> dict[str, Any]:
    payload = _source_status_template(
        source,
        status="skipped",
        hits=0,
        errors=[],
        acquisition=acquisition or source,
        runtime_seconds=0.0,
    )
    payload["reason"] = "disabled_by_config"
    payload["reason_detail"] = "disabled by JOB_INTEL_ENABLED_SOURCES"
    return payload



def _collect_vacancies(
    store: JobIntelStore | None = None,
    performance: RunPerformanceRecorder | None = None,
) -> CollectedVacancies:
    cfg = load_config_bundle() or DEFAULT_CONFIG
    store = store or _store()
    store.bootstrap()
    vacancies: list[Vacancy] = []
    statuses: dict[str, dict[str, Any]] = {}
    enabled_sources = _enabled_sources()
    ats_seed_urls: list[str] = []
    ats_seeds: dict[str, list[str]] = {}

    if _source_enabled(enabled_sources, "target_companies"):
        with (performance.span("source_acquisition.target_companies", parent_span_name="source_acquisition_total", source_name="target_companies") if performance else _null_span()) as perf_span:
            target_started = perf_counter()
            target_result = monitor_target_companies(store)
            target_runtime_seconds = perf_counter() - target_started
            vacancies.extend(target_result.vacancies)
            company_ok = any(status.get("status") == "ok" for status in target_result.company_statuses.values())
            target_outcomes = [str(s.get("outcome") or "") for s in target_result.company_statuses.values()]
            if target_result.vacancies:
                target_reason = "ok_non_empty"
            elif target_result.browser.get("requested") and not target_result.browser.get("available"):
                target_reason = str(target_result.browser.get("reason") or "browser_unavailable")
            elif any(o == "ats_seeds_discovered_only" for o in target_outcomes) and not any(o == "js_render_required" for o in target_outcomes):
                target_reason = "ats_seeds_discovered_only"
            elif any(o == "js_render_required" for o in target_outcomes):
                target_reason = "js_render_required"
            else:
                target_reason = "real_empty"
            statuses["target_companies"] = _source_status_template(
                "target-companies",
                status="ok" if company_ok or target_result.vacancies else ("error" if target_result.company_statuses else "empty"),
                hits=len(target_result.vacancies),
                companies=len(target_result.company_statuses),
                company_statuses=target_result.company_statuses,
                runtime_seconds=target_runtime_seconds,
                reason=target_reason,
                browser=target_result.browser,
            )
            perf_span.set_counts(
                found_count=len(target_result.vacancies),
                normalized_count=len(target_result.vacancies),
                error_count=sum(1 for status in target_result.company_statuses.values() if status.get("source_status") == "error"),
            )
            perf_span.add_metadata(companies=len(target_result.company_statuses))
            # ATS seeds: reuse links already discovered via target company monitoring.
            try:
                for v in target_result.vacancies or []:
                    if getattr(v, "url", ""):
                        ats_seed_urls.append(str(v.url))
                for st in (target_result.company_statuses or {}).values():
                    for u in (st.get("career_urls") or []):
                        ats_seed_urls.append(str(u))
            except Exception:
                pass
    else:
        statuses["target_companies"] = _skipped_source_status("target-companies", acquisition="target-companies")

    ats_seed_urls = [u for u in ats_seed_urls if isinstance(u, str) and u.strip()][:200]
    if ats_seed_urls:
        ats_seeds = discover_ats_seeds_from_career_urls(ats_seed_urls, max_pages=60)

    if not _source_enabled(enabled_sources, "linkedin"):
        statuses["linkedin"] = _skipped_source_status("linkedin", acquisition="browser-native")
    else:
        with (performance.span("source_acquisition.linkedin", parent_span_name="source_acquisition_total", source_name="linkedin") if performance else _null_span()) as perf_span:
            linkedin_queries = rotating_source_queries("linkedin", limit=6)
            linkedin_hits = 0
            linkedin_errors: list[str] = []
            linkedin_trace: dict[str, Any] = {}
            linkedin_started = perf_counter()
            for query in linkedin_queries:
                try:
                    results = fetch_linkedin_vacancies(query, max_pages=2)
                    linkedin_hits += len(results)
                    vacancies.extend(results)
                    _aggregate_browser_trace(linkedin_trace, getattr(fetch_linkedin_vacancies, "last_trace", None))
                except Exception as exc:
                    linkedin_errors.append(str(exc))
            if linkedin_hits:
                linkedin_status = "ok"
            elif linkedin_errors and any("Playwright" in error or "browser-native" in error for error in linkedin_errors):
                linkedin_status = "blocked"
            elif linkedin_errors:
                linkedin_status = "error"
            else:
                linkedin_status = "empty"
            linkedin_source_status = _source_status_template(
                "linkedin",
                status=linkedin_status,
                hits=linkedin_hits,
                errors=linkedin_errors,
                acquisition="browser-native",
                runtime_seconds=perf_counter() - linkedin_started,
            )
            linkedin_health = getattr(fetch_linkedin_vacancies, "last_health", None)
            if linkedin_health:
                linkedin_source_status["session_health"] = linkedin_health
            statuses["linkedin"] = linkedin_source_status
            perf_span.set_counts(
                found_count=linkedin_hits,
                normalized_count=linkedin_hits,
                error_count=len(linkedin_errors),
                timeout_count=sum(1 for err in linkedin_errors if "timeout" in err.lower()),
            )
            _emit_browser_trace_spans(performance, source="linkedin", parent_span_name="source_acquisition.linkedin", trace=linkedin_trace)

    if not _source_enabled(enabled_sources, "headhunter"):
        statuses["headhunter"] = _skipped_source_status("headhunter", acquisition="browser-native-first")
    else:
        with (performance.span("source_acquisition.headhunter", parent_span_name="source_acquisition_total", source_name="headhunter") if performance else _null_span()) as perf_span:
            hh_query_limit = max(1, int(os.getenv("JOB_INTEL_HEADHUNTER_QUERY_LIMIT", "6")))
            hh_per_page = max(1, int(os.getenv("JOB_INTEL_HEADHUNTER_PER_PAGE", "10")))
            hh_queries = rotating_source_queries("headhunter", limit=hh_query_limit)
            hh_hits = 0
            hh_errors: list[str] = []
            hh_trace: dict[str, Any] = {}
            hh_started = perf_counter()
            for query in hh_queries:
                try:
                    results = fetch_headhunter_vacancies(query, per_page=hh_per_page)
                    hh_hits += len(results)
                    vacancies.extend(results)
                    _aggregate_browser_trace(hh_trace, getattr(fetch_headhunter_vacancies, "last_trace", None))
                except Exception as exc:
                    hh_errors.append(str(exc))
            if hh_hits:
                hh_status = "ok"
            elif hh_errors and any("403" in error for error in hh_errors):
                hh_status = "blocked"
            elif hh_errors:
                hh_status = "error"
            else:
                hh_status = "empty"
            hh_source_status = _source_status_template(
                "headhunter",
                status=hh_status,
                hits=hh_hits,
                errors=hh_errors,
                acquisition="browser-native-first",
                runtime_seconds=perf_counter() - hh_started,
            )
            hh_health = getattr(fetch_headhunter_vacancies, "last_health", None)
            if hh_health:
                hh_source_status["session_health"] = hh_health
            statuses["headhunter"] = hh_source_status
            perf_span.set_counts(
                found_count=hh_hits,
                normalized_count=hh_hits,
                error_count=len(hh_errors),
                timeout_count=sum(1 for err in hh_errors if "timeout" in err.lower()),
            )
            _emit_browser_trace_spans(performance, source="headhunter", parent_span_name="source_acquisition.headhunter", trace=hh_trace)

    # ATS Wave 1 (production sources). These must not fail the whole run.
    def _collect_ats(source: str, *, fetcher, acquisition: str) -> None:
        if not _source_enabled(enabled_sources, source):
            statuses[source] = _skipped_source_status(source, acquisition=acquisition)
            return
        queries = rotating_source_queries(source, limit=6)
        with (performance.span(f"source_acquisition.{source}", parent_span_name="source_acquisition_total", source_name=source) if performance else _null_span()) as perf_span:
            started = perf_counter()
            try:
                registry_entries = _registry_entries_for_source(source) if source in _REGISTRY_ATS_SOURCES else []
                registry_statuses: list[dict[str, Any]] = []
                all_vacancies: list[Vacancy] = []
                all_errors: list[str] = []
                pages_fetched = 0
                discovered_companies = 0
                seen_slugs: set[str] = set()
                for entry in registry_entries:
                    slug = str(entry.get("ats_slug") or "").strip()
                    if not slug:
                        continue
                    seen_slugs.add(slug.lower())
                    company_name = str(entry.get("company_name") or slug).strip() or slug
                    attempt_payload = {
                        "company_name": company_name,
                        "tier": entry.get("tier"),
                        "ats_vendor": source,
                        "ats_slug": slug,
                        "collection_url": entry.get("collection_url"),
                        "validation_url": entry.get("validation_url"),
                        "acquisition_enabled": bool(entry.get("acquisition_enabled", True)),
                        "attempted": True,
                        "collected": False,
                        "vacancies_found": 0,
                        "source_status": "error",
                        "reason": "collection_failure",
                        "errors": [],
                    }
                    try:
                        per = fetcher(queries, companies=[slug])
                        per_errors = list(per.errors or [])
                        per_hits = len(per.vacancies or [])
                        attempt_payload["errors"] = per_errors
                        attempt_payload["vacancies_found"] = per_hits
                        attempt_payload["collected"] = bool((per.pages_fetched or 0) > 0)
                        if per_hits > 0:
                            attempt_payload["source_status"] = "ok"
                            attempt_payload["reason"] = "vacancies_found"
                        elif per_errors:
                            attempt_payload["source_status"] = "error"
                            attempt_payload["reason"] = "collection_error"
                        else:
                            attempt_payload["source_status"] = "empty"
                            attempt_payload["reason"] = "no_open_roles_or_query_empty"

                        for vacancy in per.vacancies or []:
                            md = dict(vacancy.metadata or {})
                            md["acquisition_path"] = "registry"
                            md["registry_company_name"] = company_name
                            md["registry_ats_vendor"] = source
                            md["registry_ats_slug"] = slug
                            vacancy.metadata = md
                            all_vacancies.append(vacancy)
                        all_errors.extend(per_errors)
                        pages_fetched += int(per.pages_fetched or 0)
                        discovered_companies += int(per.discovered_companies or 0)
                    except Exception as exc:
                        attempt_payload["errors"] = [str(exc)]
                        attempt_payload["source_status"] = "error"
                        attempt_payload["reason"] = "collection_exception"
                        all_errors.append(f"{source} registry slug={slug}: {exc}")
                    registry_statuses.append(attempt_payload)

                env_seed_companies = [c for c in env_ats_seeds(source) if str(c).strip().lower() not in seen_slugs]
                for company in env_seed_companies:
                    seen_slugs.add(str(company).strip().lower())

                discovered_seed_companies = (ats_seeds.get(source) or []) if isinstance(ats_seeds, dict) else []
                discovery_companies = [c for c in discovered_seed_companies if str(c).strip().lower() not in seen_slugs]
                if env_seed_companies or discovery_companies:
                    seed_companies = env_seed_companies + discovery_companies
                    result = fetcher(queries, companies=seed_companies)
                    all_vacancies.extend(result.vacancies or [])
                    all_errors.extend(list(result.errors or []))
                    pages_fetched += int(result.pages_fetched or 0)
                    discovered_companies += int(result.discovered_companies or 0)

                vacancies.extend(all_vacancies)
                hits = len(all_vacancies)
                errors = list(all_errors)
                status = status_from_hits_errors(hits, errors)
                payload = _source_status_template(source, status=status, hits=hits, errors=errors, acquisition=acquisition)
                seeds_present = bool(registry_entries or env_seed_companies or discovery_companies)
                payload["seeds_present"] = seeds_present
                if not seeds_present and hits == 0 and not errors:
                    payload["reason"] = "missing_seeds"
                payload["discovered_companies"] = int(discovered_companies or 0)
                payload["pages_fetched"] = int(pages_fetched or 0)
                payload["runtime_seconds"] = perf_counter() - started
                if registry_statuses:
                    payload["registry_companies"] = registry_statuses
                    payload["registry_companies_attempted"] = len(registry_statuses)
                    payload["registry_companies_with_hits"] = sum(1 for item in registry_statuses if int(item.get("vacancies_found") or 0) > 0)
                statuses[source] = payload
                perf_span.set_counts(
                    found_count=hits,
                    normalized_count=hits,
                    error_count=len(errors),
                    timeout_count=sum(1 for err in errors if "timeout" in err.lower()),
                )
                perf_span.add_metadata(
                    pages_fetched=int(pages_fetched or 0),
                    tenants_checked=len(registry_statuses) if registry_statuses else None,
                    discovered_companies=int(discovered_companies or 0),
                )
            except Exception as exc:
                statuses[source] = _source_status_template(
                    source,
                    status="error",
                    hits=0,
                    errors=[str(exc)],
                    acquisition=acquisition,
                    runtime_seconds=perf_counter() - started,
                )
                perf_span.set_counts(error_count=1)
                perf_span.set_status("error")

    _collect_ats("greenhouse", fetcher=fetch_greenhouse, acquisition="ats-api")
    _collect_ats("lever", fetcher=fetch_lever, acquisition="ats-api")
    _collect_ats("ashby", fetcher=fetch_ashby, acquisition="ats-api")
    _collect_ats("teamtailor", fetcher=fetch_teamtailor, acquisition="ats-web")
    _collect_ats("smartrecruiters", fetcher=fetch_smartrecruiters, acquisition="ats-api")
    _collect_ats("personio", fetcher=fetch_personio, acquisition="ats-xml")
    _collect_ats("recruitee", fetcher=fetch_recruitee, acquisition="ats-xml")

    if not _source_enabled(enabled_sources, "duckduckgo"):
        statuses["duckduckgo"] = _skipped_source_status("duckduckgo")
    else:
        with (performance.span("source_acquisition.duckduckgo", parent_span_name="source_acquisition_total", source_name="duckduckgo") if performance else _null_span()) as perf_span:
            ddg_hits = 0
            ddg_errors: list[str] = []
            for _, query in discovery_queries():
                try:
                    for hit in search_duckduckgo(query, max_results=5):
                        vacancies.append(normalize_search_hit(hit))
                        ddg_hits += 1
                except Exception as exc:
                    ddg_errors.append(str(exc))
            if ddg_hits:
                ddg_status = "ok"
            elif ddg_errors and all(_is_timeout_only_error(error) for error in ddg_errors):
                ddg_status = "empty"
            elif ddg_errors:
                ddg_status = "error"
            else:
                ddg_status = "empty"
            statuses["duckduckgo"] = _source_status_template("duckduckgo", status=ddg_status, hits=ddg_hits, errors=ddg_errors)
            perf_span.set_counts(found_count=ddg_hits, normalized_count=ddg_hits, error_count=len(ddg_errors))

    if not _source_enabled(enabled_sources, "remoteok"):
        statuses["remoteok"] = _skipped_source_status("remoteok")
    else:
        with (performance.span("source_acquisition.remoteok", parent_span_name="source_acquisition_total", source_name="remoteok") if performance else _null_span()) as perf_span:
            remoteok_hits = 0
            remoteok_errors: list[str] = []
            try:
                remoteok_vacancies = search_remoteok_jobs(max_results=25)
                vacancies.extend(remoteok_vacancies)
                remoteok_hits = len(remoteok_vacancies)
            except Exception as exc:
                remoteok_errors.append(str(exc))
            statuses["remoteok"] = _source_status_template("remoteok", status="ok" if remoteok_hits else ("error" if remoteok_errors else "empty"), hits=remoteok_hits, errors=remoteok_errors)
            perf_span.set_counts(found_count=remoteok_hits, normalized_count=remoteok_hits, error_count=len(remoteok_errors))

    if not _source_enabled(enabled_sources, "remotive"):
        statuses["remotive"] = _skipped_source_status("remotive")
    else:
        with (performance.span("source_acquisition.remotive", parent_span_name="source_acquisition_total", source_name="remotive") if performance else _null_span()) as perf_span:
            remotive_hits = 0
            remotive_errors: list[str] = []
            try:
                remotive_vacancies = search_remotive_jobs(max_results=25)
                vacancies.extend(remotive_vacancies)
                remotive_hits = len(remotive_vacancies)
            except Exception as exc:
                remotive_errors.append(str(exc))
            statuses["remotive"] = _source_status_template("remotive", status="ok" if remotive_hits else ("error" if remotive_errors else "empty"), hits=remotive_hits, errors=remotive_errors)
            perf_span.set_counts(found_count=remotive_hits, normalized_count=remotive_hits, error_count=len(remotive_errors))

    return CollectedVacancies(vacancies=vacancies, source_statuses=statuses)


def _collect_vacancies_compat(
    store: JobIntelStore | None = None,
    performance: RunPerformanceRecorder | None = None,
) -> CollectedVacancies:
    try:
        return _coerce_collected(_collect_vacancies(store, performance))
    except TypeError:
        return _coerce_collected(_collect_vacancies())


def _slack_webhook_enabled() -> bool:
    return bool(os.getenv("JOB_INTEL_SLACK_WEBHOOK_URL", "").strip())


def _ensure_hermes_env_loaded_for_outbound_delivery() -> None:
    env_file = (
        os.getenv("JOB_INTEL_ENV_FILE", "").strip()
        or "/etc/job-intel/job-intel.env"
    )
    if not env_file or not os.path.exists(env_file):
        return None
    required = ("SLACK_BOT_TOKEN", "SLACK_APP_TOKEN", "HERMES_HOME")
    if all(os.getenv(name, "").strip() for name in required):
        return None
    try:
        with open(env_file, "r", encoding="utf-8") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip("'").strip('"')
                if key and not os.getenv(key):
                    os.environ[key] = value
    except OSError:
        return None
    return None


_LOGICAL_SLACK_CHANNEL_ALIASES: dict[str, str] = {
    "executive_search_report": "C0B4MM6D52A",
}


def _resolve_logical_slack_channel_id(channel: str | None) -> str | None:
    if not channel:
        return None
    if channel.startswith(("C", "G", "D")):
        return channel
    return _LOGICAL_SLACK_CHANNEL_ALIASES.get(channel)


def _logical_aliases_for_channel_id(channel_id: str | None) -> list[str]:
    return [alias for alias, cid in _LOGICAL_SLACK_CHANNEL_ALIASES.items() if channel_id and cid == channel_id]



def _deliver_to_slack(
    message: str,
    channel: str | None = None,
    *,
    retries: int = 3,
    prefer_gateway: bool = False,
    thread_ts: str | None = None,
) -> SlackDeliveryResult:
    webhook = os.getenv("JOB_INTEL_SLACK_WEBHOOK_URL", "").strip()
    if message == "[SILENT]":
        return SlackDeliveryResult(success=True, attempts=0, error=None, status="sent")

    if thread_ts:
        prefer_gateway = True

    if prefer_gateway or not webhook:
        target_channel = _resolve_logical_slack_channel_id(channel) or channel
        if target_channel and thread_ts:
            target = f"slack:{target_channel}:{thread_ts}"
        elif target_channel:
            target = f"slack:{target_channel}"
        else:
            target = "slack"
        try:
            _ensure_hermes_env_loaded_for_outbound_delivery()
            raw = send_message_tool({"target": target, "message": message})
            payload = json.loads(raw) if raw else {}
        except Exception as exc:
            return SlackDeliveryResult(success=False, attempts=1, error=f"live adapter delivery error: {exc}", status="failed")
        if payload.get("error"):
            return SlackDeliveryResult(success=False, attempts=1, error=str(payload.get("error")), status="failed")
        if payload.get("success"):
            ts = payload.get("ts") or payload.get("message_ts") or payload.get("message_id") or payload.get("platform_message_id") or payload.get("id")
            channel_id = payload.get("chat_id") or payload.get("channel_id") or payload.get("channel")
            platform_message_id = payload.get("message_id") or payload.get("platform_message_id") or payload.get("id")
            return SlackDeliveryResult(
                success=True,
                attempts=1,
                error=None,
                status="sent",
                message_ts=str(ts) if ts else None,
                channel_id=str(channel_id) if channel_id else None,
                platform_message_id=str(platform_message_id) if platform_message_id else None,
            )
        return SlackDeliveryResult(success=False, attempts=1, error=f"unexpected live adapter response: {payload}", status="failed")

    payload: dict[str, str] = {"text": message}
    if channel:
        payload["channel"] = channel
    if thread_ts:
        payload["thread_ts"] = thread_ts

    def _send_once() -> None:
        response = requests.post(webhook, json=payload, timeout=20)
        response.raise_for_status()

    attempts = 0
    last_error: Exception | None = None
    for attempt in range(1, max(retries, 1) + 1):
        attempts = attempt
        try:
            _send_once()
            return SlackDeliveryResult(success=True, attempts=attempts, status="sent")
        except Exception as exc:
            last_error = exc
            if attempt < retries:
                continue
    return SlackDeliveryResult(success=False, attempts=attempts, error=str(last_error) if last_error else None, status="failed")


def _resolve_delivery_channel_id(channel: str | None, delivery: SlackDeliveryResult) -> str | None:
    if delivery.channel_id:
        return str(delivery.channel_id)
    return _resolve_logical_slack_channel_id(channel)


def _delivery_tracking_identity(channel: str | None, delivery: SlackDeliveryResult) -> tuple[str | None, str | None]:
    """Real Slack (channel_id, message_ts) for reaction tracking.

    Reaction events carry the real message identity; a guessed channel alias or a
    locally fabricated timestamp makes the card permanently untrackable, so this
    returns None instead of inventing values.
    """
    channel_id = _resolve_delivery_channel_id(channel, delivery)
    if channel_id and not channel_id.startswith(("C", "G", "D")):
        channel_id = None
    message_ts = str(delivery.message_ts) if delivery.message_ts else None
    return channel_id, message_ts


def _notification_payload_json(row: dict[str, Any] | None) -> dict[str, Any]:
    if not row:
        return {}
    try:
        return json.loads(row.get("payload_json") or "{}")
    except json.JSONDecodeError:
        return {}


def _chunk_slack_report(text: str, *, max_chars: int = 35000) -> list[str]:
    body = (text or "").strip()
    if not body:
        return [""]
    if len(body) <= max_chars:
        return [body]
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for block in body.split("\n\n"):
        piece = block if not current else f"\n\n{block}"
        if current and current_len + len(piece) > max_chars:
            chunks.append("".join(current).strip())
            current = [block]
            current_len = len(block)
            continue
        current.append(piece if current else block)
        current_len += len(piece if len(current) > 1 else block)
    if current:
        chunks.append("".join(current).strip())
    return [chunk for chunk in chunks if chunk]


def _deliver_threaded_slack_report(
    *,
    message: str,
    channel: str,
    parent_thread_ts: str | None,
) -> tuple[SlackDeliveryResult, list[str], str | None]:
    chunks = _chunk_slack_report(message)
    first_delivery = _deliver_to_slack(
        chunks[0],
        channel,
        prefer_gateway=True,
        thread_ts=parent_thread_ts,
    )
    delivered_message_ids: list[str] = []
    if first_delivery.message_ts:
        delivered_message_ids.append(str(first_delivery.message_ts))
    if not first_delivery.success:
        return first_delivery, delivered_message_ids, parent_thread_ts

    root_thread_ts = parent_thread_ts or first_delivery.message_ts
    if not root_thread_ts:
        return first_delivery, delivered_message_ids, None

    for chunk in chunks[1:]:
        extra_delivery = _deliver_to_slack(
            chunk,
            channel,
            prefer_gateway=True,
            thread_ts=root_thread_ts,
        )
        if extra_delivery.message_ts:
            delivered_message_ids.append(str(extra_delivery.message_ts))
        if not extra_delivery.success:
            return (
                SlackDeliveryResult(
                    success=False,
                    attempts=first_delivery.attempts + extra_delivery.attempts,
                    error=extra_delivery.error,
                    status="failed",
                    message_ts=first_delivery.message_ts,
                    channel_id=first_delivery.channel_id or extra_delivery.channel_id,
                    platform_message_id=first_delivery.platform_message_id,
                ),
                delivered_message_ids,
                root_thread_ts,
            )
    return first_delivery, delivered_message_ids, root_thread_ts


def _source_counts_from_historical_run(
    store: JobIntelStore,
    run_id: int,
    source_statuses: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    source_counts: dict[str, dict[str, Any]] = {}
    for row in store.fetch_source_kpi_rows(run_id):
        source = str(row.get("source") or "unknown")
        source_counts[source] = {
            "found_count": int(row.get("found_count") or 0),
            "raw_found_count": int(row.get("found_count") or 0),
            "scored_count": int(row.get("scored_count") or 0),
            "accepted_count": int(row.get("accepted_count") or 0),
            "vacancies_deduped": int(row.get("vacancies_deduped") or 0),
            "rejected_count": int(row.get("rejected_count") or 0),
            "executive_detected_count": int(row.get("executive_detected_count") or 0),
            "notified_count": int(row.get("notified_count") or 0),
            "strong_fit_count": 0,
            "potential_fit_count": 0,
            "needs_review_count": 0,
            "near_miss_count": 0,
        }
        status = source_statuses.setdefault(source, {})
        if row.get("source_status") and not status.get("status"):
            status["status"] = row.get("source_status")
        if row.get("runtime_seconds") is not None and status.get("runtime_seconds") in {None, 0, 0.0, ""}:
            status["runtime_seconds"] = row.get("runtime_seconds")
        for field in ("pages_fetched", "login_walls", "auth_redirects", "anti_bot_events"):
            if row.get(field) is not None and field not in status:
                status[field] = row.get(field)
        status.setdefault("errors", [])

    with store.connect(read_only=True) as conn:
        rows = conn.execute(
            """
            SELECT source, recommendation, COUNT(*)
            FROM vacancy_observability
            WHERE run_id = ? AND is_duplicate = 0
            GROUP BY source, recommendation
            """,
            (run_id,),
        ).fetchall()
    for source, recommendation, count in rows:
        key = _normalize_source_notification_key(str(source or "unknown"))
        stats = source_counts.setdefault(
            key,
            {
                "found_count": 0,
                "raw_found_count": 0,
                "scored_count": 0,
                "accepted_count": 0,
                "vacancies_deduped": 0,
                "rejected_count": 0,
                "executive_detected_count": 0,
                "notified_count": 0,
                "strong_fit_count": 0,
                "potential_fit_count": 0,
                "needs_review_count": 0,
                "near_miss_count": 0,
            },
        )
        bucket = str(recommendation or "reject")
        if bucket == "strong_fit":
            stats["strong_fit_count"] += int(count or 0)
        elif bucket in {"potential_fit", "possible_fit"}:
            stats["potential_fit_count"] += int(count or 0)
        elif bucket == "needs_review":
            stats["needs_review_count"] += int(count or 0)
        elif bucket == "near_miss":
            stats["near_miss_count"] += int(count or 0)
    return source_counts


def _build_historical_performance_report(
    store: JobIntelStore,
    run_id: int,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[str], int]:
    run_row = store.get_run(run_id)
    if not run_row:
        raise ValueError(f"run_id={run_id} not found")
    metadata = _json_loads(run_row.get("metadata_json"), {})
    source_statuses = dict(metadata.get("source_statuses") or {})
    spans = store.fetch_performance_spans(run_id)
    source_counts = _source_counts_from_historical_run(store, run_id, source_statuses)
    source_rows = build_source_value_rows(
        source_counts=source_counts,
        source_statuses=source_statuses,
        card_decisions=store.fetch_card_decisions(run_id),
    )
    total_runtime_ms = 0
    for span in spans:
        if str(span.get("span_name") or "") == "daily_run_total":
            total_runtime_ms = int(span.get("duration_ms") or 0)
            break
    recent_runtime_ms = store.recent_daily_run_span_durations("daily_run_total", limit=5, exclude_run_id=run_id)
    reasons = performance_trigger_reason(
        total_runtime_ms=total_runtime_ms,
        recent_runtime_ms=recent_runtime_ms,
        source_rows=source_rows,
    )
    report = format_detailed_performance_report(
        run_id=run_id,
        total_runtime_ms=total_runtime_ms,
        phase_rows=spans,
        source_rows=source_rows,
        reasons=reasons,
    )
    return run_row, source_rows, reasons, total_runtime_ms


def _send_performance_report_for_run(run_id: int, *, force: bool = False) -> str:
    assert_runtime_contract()
    store = _store()
    store.bootstrap()
    run_row, source_rows, reasons, total_runtime_ms = _build_historical_performance_report(store, run_id)
    if not reasons and not force:
        return f"run_id={run_id}: no performance-report trigger fired"

    notifications = store.fetch_notifications_for_run(run_id, message_type="daily_summary")
    summary_notification = notifications[-1] if notifications else None
    summary_payload = _notification_payload_json(summary_notification)
    channel = (
        (summary_notification or {}).get("channel")
        or summary_payload.get("slack_channel_id")
        or _search_report_channel(load_config_bundle() or DEFAULT_CONFIG)
    )
    parent_thread_ts = str(summary_payload.get("slack_message_ts") or "").strip() or None
    report = format_detailed_performance_report(
        run_id=run_id,
        total_runtime_ms=total_runtime_ms,
        phase_rows=store.fetch_performance_spans(run_id),
        source_rows=source_rows,
        reasons=reasons,
    )
    payload = {
        "run_id": run_id,
        "reasons": reasons,
        "forced": bool(force),
        "delivery_method": "thread_reply" if parent_thread_ts else "channel_message",
        "parent_notification_id": summary_notification.get("id") if summary_notification else None,
        "thread_ts": parent_thread_ts,
    }
    notification_id = store.create_notification(
        run_id,
        channel,
        "performance_report",
        report,
        notification_kind="performance_report",
        delivery_status="pending",
        payload=payload,
    )
    delivery, message_ids, effective_thread_ts = _deliver_threaded_slack_report(
        message=report,
        channel=channel,
        parent_thread_ts=parent_thread_ts,
    )
    store.mark_notification_delivery(
        notification_id,
        _delivery_db_status(delivery),
        attempts=delivery.attempts,
        delivery_error=delivery.error,
    )
    payload.update(
        {
            "slack_channel_id": _resolve_delivery_channel_id(channel, delivery),
            "slack_message_ts": delivery.message_ts,
            "platform_message_id": delivery.platform_message_id,
            "thread_ts": effective_thread_ts,
            "message_ids": message_ids,
            "chunk_count": len(message_ids) or 1,
            "delivery": delivery.__dict__,
        }
    )
    store.update_notification_payload(notification_id, payload)
    if not delivery.success:
        logger.warning(
            "performance_report_delivery_failed run_id=%s notification_id=%s channel=%s thread_ts=%s error=%s",
            run_id,
            notification_id,
            channel,
            effective_thread_ts,
            delivery.error,
        )
        return json.dumps(
            {
                "status": "failed",
                "run_id": run_id,
                "notification_id": notification_id,
                "channel": channel,
                "thread_ts": effective_thread_ts,
                "error": delivery.error,
            },
            ensure_ascii=False,
        )
    return json.dumps(
        {
            "status": "sent",
            "run_id": run_id,
            "notification_id": notification_id,
            "channel": channel,
            "thread_ts": effective_thread_ts,
            "message_ids": message_ids,
            "reasons": reasons,
            "forced": force,
        },
        ensure_ascii=False,
    )


def _source_footer(source_statuses: dict[str, dict[str, Any]]) -> str | None:
    issues: list[str] = []
    for name, status in source_statuses.items():
        if status.get("status") not in {"ok", "empty", "skipped"}:
            message = status.get("status", "unknown")
            if status.get("errors"):
                message = f"{message}: {status['errors'][-1]}"
            issues.append(f"{name}={message}")
    if not issues:
        return None
    return f"Operator note: source issues detected — {', '.join(issues)}"



def _search_report_channel(cfg: dict[str, Any]) -> str:
    runtime = cfg.get("runtime", {}).get("slack", {})
    return str(runtime.get("search_report_channel") or runtime.get("channel") or "executive_search_report")



def _bool_text(value: Any) -> str:
    return "yes" if bool(value) else "no"



def _search_technical_report(source_statuses: dict[str, dict[str, Any]], *, channel: str | None = None) -> str:
    cfg = load_config_bundle() or DEFAULT_CONFIG
    resolved_channel = channel or _search_report_channel(cfg)
    lines = ["*Technical search report*", ""]
    lines.append(
        f"Runtime: user={runtime_user()} | environment={resolve_environment_name()} | workdir={resolve_workdir()} | db={resolve_db_path()}"
    )
    lines.append(f"Slack channel: {resolved_channel}")
    lines.append("")
    lines.append("*Acquisition details*")
    for source, status in sorted(source_statuses.items()):
        session = status.get("session_health") or {}
        browser_profile = str(session.get("browser_profile") or "n/a")
        login_detected = bool(session.get("auth_attempted") or session.get("login_walls") or session.get("auth_redirects"))
        email_challenge_attempted = bool(session.get("email_challenge_attempted"))
        email_challenge_resolved = bool(session.get("email_challenge_resolved"))
        acquisition = str(status.get("acquisition") or status.get("source") or "n/a")
        lines.append(
            "- "
            f"{source}: acquisition={acquisition}, status={status.get('status', 'unknown')}, profile={browser_profile}, "
            f"login={_bool_text(login_detected)}, email_challenge={_bool_text(email_challenge_attempted)}"
            + (f" (resolved={_bool_text(email_challenge_resolved)})" if email_challenge_attempted else "")
            + f", pages_fetched={session.get('pages_fetched', 0)}, login_walls={session.get('login_walls', 0)}, auth_redirects={session.get('auth_redirects', 0)}, session_status={session.get('status', 'n/a')}"
        )
    return "\n".join(lines).rstrip()



def _search_report_payload(source_statuses: dict[str, dict[str, Any]]) -> str:
    return _search_technical_report(source_statuses)



def _feedback_hint_block() -> str:
    return "👍 Interesting\n👎 Not Interesting\n🔥 Exceptional\n🚀 Applied\n👀 Save for later"


def _format_vacancy_feedback_message(vacancy: Vacancy, evaluation: Any) -> str:
    base = format_vacancy_summary(vacancy, evaluation)
    parts = [base, "", "*Feedback*", _feedback_hint_block()]
    return "\n".join(parts).strip()


def _deliver_vacancy_messages(
    store: JobIntelStore,
    run_id: int,
    channel: str,
    items: list[tuple[Vacancy, Any, int]],
) -> list[dict[str, Any]]:
    deliveries: list[dict[str, Any]] = []
    for vacancy, evaluation, vacancy_id in items:
        body = _format_vacancy_feedback_message(vacancy, evaluation)
        payload = _notification_payload(vacancy, evaluation, vacancy_id)
        notification_id = store.create_notification(
            run_id,
            channel,
            "vacancy_card",
            body,
            vacancy_id=vacancy_id,
            card_key=payload.get("card_key"),
            notification_kind="vacancy_card",
            payload=payload,
            delivery_status="pending",
            delivery_attempts=0,
        )
        delivery = _deliver_to_slack(body, channel)
        store.mark_notification_delivery(
            notification_id,
            _delivery_db_status(delivery),
            attempts=delivery.attempts,
            delivery_error=delivery.error,
        )
        if delivery.success:
            tracked_channel_id, tracked_message_ts = _delivery_tracking_identity(channel, delivery)
            if not (tracked_channel_id and tracked_message_ts):
                logger.warning(
                    "vacancy_card_tracking_untrackable vacancy_id=%s channel=%r delivery_ts=%r delivery_channel_id=%r",
                    vacancy_id, channel, delivery.message_ts, delivery.channel_id,
                )
            else:
                store.insert_vacancy_slack_message(
                    vacancy_id=vacancy_id,
                    run_id=run_id,
                    vacancy_key=payload.get("vacancy_key"),
                    canonical_url=payload.get("canonical_url"),
                    card_key=payload.get("card_key"),
                    notification_id=notification_id,
                    slack_channel=tracked_channel_id,
                    slack_message_ts=tracked_message_ts,
                    message_type="vacancy_card",
                    company=vacancy.company,
                    title=vacancy.title,
                    score=int(getattr(evaluation, "score", 0) or 0),
                    recommendation=str(getattr(evaluation, "recommendation", "reject") or "reject"),
                    url=vacancy.url,
                    sent_at=datetime.now(timezone.utc).isoformat(),
                )
            store.set_vacancy_status(vacancy_id, "notified")
        else:
            store.set_vacancy_status(vacancy_id, "active")
        deliveries.append({
            "vacancy_id": vacancy_id,
            "notification_id": notification_id,
            "delivery": delivery.__dict__,
        })
    return deliveries


def _notification_payload(vacancy: Vacancy, evaluation: Any, vacancy_id: int) -> dict[str, Any]:
    canonical_url = canonical_job_url(vacancy.url, vacancy.source)
    vacancy_key = canonical_vacancy_key(vacancy)
    card_key = canonical_url or vacancy_key
    summary_body = format_vacancy_summary(vacancy, evaluation)
    return {
        "vacancy_id": vacancy_id,
        "vacancy_key": vacancy_key,
        "canonical_url": canonical_url,
        "card_key": card_key,
        "summary_hash": sha256_text(summary_body),
        "score": evaluation.score,
        "tier": evaluation.tier,
        "recommendation": evaluation.recommendation,
        "salary": vacancy.salary,
        "description": vacancy.description,
        "description_hash": sha256_text(vacancy.description),
        "title": vacancy.title,
        "company": vacancy.company,
        "location": vacancy.location,
    }


def _card_key_for_vacancy(vacancy: Vacancy) -> str:
    return canonical_job_url(vacancy.url, vacancy.source) or canonical_vacancy_key(vacancy)


def _recommendation_rank(value: str | None) -> int:
    return RECOMMENDATION_ORDER.get(str(value or "reject"), 0)


def _candidate_priority_for_recommendation(value: str | None) -> int | None:
    recommendation = str(value or "")
    if recommendation in {"strong_fit", "exceptional_fit"}:
        return 0
    if recommendation in {"potential_fit", "possible_fit", "needs_review"}:
        return 1
    if recommendation == "near_miss":
        return 2
    return None


def _candidate_freshness_timestamp(vacancy: Vacancy) -> float:
    for raw in (
        getattr(vacancy, "scraped_at", None),
        getattr(vacancy, "posted_at", None),
    ):
        parsed = parse_iso_datetime(raw) if raw else None
        if parsed:
            return parsed.timestamp()
    return 0.0


def _material_card_change(
    latest: dict[str, Any] | None,
    vacancy: Vacancy,
    evaluation: Any,
    *,
    score_delta: int,
) -> bool:
    if not latest:
        return True
    try:
        payload = json.loads(latest.get("payload_json") or "{}")
    except json.JSONDecodeError:
        payload = {}
    current_body = format_vacancy_summary(vacancy, evaluation)
    current_summary_hash = sha256_text(current_body)
    current_description_hash = sha256_text(vacancy.description)
    previous_recommendation = payload.get("recommendation") or payload.get("tier") or "reject"
    if _recommendation_rank(getattr(evaluation, "recommendation", "reject")) > _recommendation_rank(previous_recommendation):
        return True
    previous_score = int(payload.get("score") or 0)
    current_score = int(getattr(evaluation, "score", 0) or 0)
    if current_score >= previous_score + score_delta:
        return True
    if payload.get("description_hash") and payload.get("description_hash") != current_description_hash:
        return True
    previous_description = payload.get("description", "")
    if previous_description and description_similarity(vacancy.description, previous_description) < 0.9:
        return True
    if vacancy.salary and vacancy.salary != payload.get("salary"):
        return True
    previous_summary_hash = payload.get("summary_hash")
    if previous_summary_hash and previous_summary_hash != current_summary_hash:
        return True
    return False
def _card_decision_plan(
    store: JobIntelStore,
    vacancy: Vacancy,
    evaluation: Any,
    repost_window_days: int,
) -> CardDecisionPlan:
    score_delta = int(os.getenv("JOB_INTEL_CARD_RESEND_SCORE_DELTA", "10") or "10")
    card_key = _card_key_for_vacancy(vacancy)
    latest = store.latest_notification_for_card(
        card_key,
        delivery_status="sent",
        message_types=("vacancy_card", "vacancy_opportunity", "daily_digest"),
    )
    if not latest:
        return CardDecisionPlan(True, "sent", None, None, None, None, False, None, None, False, False, False)

    feedback_types = set(store.active_feedback_for_card(card_key))
    try:
        payload = json.loads(latest.get("payload_json") or "{}")
    except json.JSONDecodeError:
        payload = {}
    previous_score = int(payload.get("score") or 0)
    current_score = int(getattr(evaluation, "score", 0) or 0)
    previous_recommendation = str(payload.get("recommendation") or payload.get("tier") or "reject")
    current_recommendation = str(getattr(evaluation, "recommendation", "reject") or "reject")
    recommendation_changed = current_recommendation != previous_recommendation
    recommendation_improved = _recommendation_rank(current_recommendation) > _recommendation_rank(previous_recommendation)
    current_body = format_vacancy_summary(vacancy, evaluation)
    current_summary_hash = sha256_text(current_body)
    current_description_hash = sha256_text(vacancy.description)
    content_hash_changed = bool(payload.get("summary_hash")) and payload.get("summary_hash") != current_summary_hash
    description_hash_changed = bool(payload.get("description_hash")) and payload.get("description_hash") != current_description_hash
    material_change = _material_card_change(latest, vacancy, evaluation, score_delta=score_delta)
    notified_at = parse_iso_datetime(latest.get("sent_at"))
    next_allowed_at = None
    cooldown_elapsed = False
    if notified_at:
        next_allowed = notified_at + timedelta(days=repost_window_days)
        next_allowed_at = next_allowed.isoformat()
        cooldown_elapsed = datetime.now(timezone.utc) >= next_allowed

    previous_feedback_label = ",".join(sorted(feedback_types)) if feedback_types else None
    score_delta_since_last_sent = current_score - previous_score
    feedback_state_active = bool(feedback_types)
    previous_sent_run_id = int(latest.get("run_id")) if latest.get("run_id") is not None else None
    previous_sent_at = latest.get("sent_at")

    if "applied" in feedback_types:
        return CardDecisionPlan(False, "suppressed", "feedback_applied", previous_sent_run_id, previous_sent_at, previous_feedback_label, feedback_state_active, next_allowed_at, score_delta_since_last_sent, recommendation_changed, content_hash_changed, description_hash_changed)
    if "exceptional" in feedback_types:
        return CardDecisionPlan(False, "suppressed", "feedback_exceptional_follow_up", previous_sent_run_id, previous_sent_at, previous_feedback_label, feedback_state_active, next_allowed_at, score_delta_since_last_sent, recommendation_changed, content_hash_changed, description_hash_changed)
    if "not_interesting" in feedback_types and not material_change:
        return CardDecisionPlan(False, "suppressed", "feedback_not_interesting", previous_sent_run_id, previous_sent_at, previous_feedback_label, feedback_state_active, next_allowed_at, score_delta_since_last_sent, recommendation_changed, content_hash_changed, description_hash_changed)
    if ("interesting" in feedback_types or "save_for_later" in feedback_types) and not (material_change or cooldown_elapsed):
        return CardDecisionPlan(False, "suppressed", "feedback_cooldown_active", previous_sent_run_id, previous_sent_at, previous_feedback_label, feedback_state_active, next_allowed_at, score_delta_since_last_sent, recommendation_changed, content_hash_changed, description_hash_changed)
    if not feedback_types and not (material_change or cooldown_elapsed):
        return CardDecisionPlan(False, "suppressed", "already_sent_cooldown_active", previous_sent_run_id, previous_sent_at, None, False, next_allowed_at, score_delta_since_last_sent, recommendation_changed, content_hash_changed, description_hash_changed)

    if recommendation_improved:
        reason = "recommendation_improved"
    elif score_delta_since_last_sent >= score_delta:
        reason = "score_increased_materially"
    elif description_hash_changed:
        reason = "description_changed_materially"
    elif content_hash_changed:
        reason = "content_changed_materially"
    elif cooldown_elapsed:
        reason = "cooldown_elapsed"
    else:
        reason = "material_change"
    return CardDecisionPlan(True, "sent", reason, previous_sent_run_id, previous_sent_at, previous_feedback_label, feedback_state_active, next_allowed_at, score_delta_since_last_sent, recommendation_changed, content_hash_changed, description_hash_changed)


def _should_notify_vacancy(store: JobIntelStore, vacancy_id: int, vacancy: Vacancy, evaluation: Any, repost_window_days: int) -> bool:
    return _card_decision_plan(store, vacancy, evaluation, repost_window_days).should_notify


def _canonical_actionable_candidates(
    scored_rows: list[tuple[Vacancy, Any, dict[str, Any], int, bool]],
) -> list[tuple[int, int, float, int, Vacancy, Any, int]]:
    grouped: dict[str, list[tuple[int, int, float, int, Vacancy, Any, int, bool]]] = {}
    for vacancy, evaluation, _classification, vacancy_id, duplicate in scored_rows:
        priority = _candidate_priority_for_recommendation(getattr(evaluation, "recommendation", None))
        if priority is None:
            continue
        card_key = _card_key_for_vacancy(vacancy)
        grouped.setdefault(card_key, []).append(
            (
                priority,
                -int(getattr(evaluation, "score", 0) or 0),
                -_candidate_freshness_timestamp(vacancy),
                int(vacancy_id),
                vacancy,
                evaluation,
                int(vacancy_id),
                bool(duplicate),
            )
        )

    canonical: list[tuple[int, int, float, int, Vacancy, Any, int]] = []
    for rows in grouped.values():
        # If the same card identity produced duplicate-linked rows in the run,
        # keep it out of human-facing candidate accounting until lineage is unambiguous.
        if any(bool(row[7]) for row in rows):
            continue
        canonical_rows = [row for row in rows if not bool(row[7])]
        if not canonical_rows:
            continue
        canonical_rows.sort(key=lambda row: (row[0], row[1], row[2], row[3]))
        best = canonical_rows[0]
        canonical.append(best[:7])

    canonical.sort(key=lambda row: (row[0], row[1], row[2], row[3]))
    return canonical


def _dual_score_rollout_enabled(store: JobIntelStore, run_id: int) -> bool:
    """Enable dual-score reporting for the first 7 production daily runs after switching to v2.

    Implementation note:
    - Prefers `runs.metadata_json` (written in store.start_run and preserved/merged on finish_run)
      because `runs.notes` may be overwritten with end-of-run summaries.
    - Uses LIKE rather than sqlite JSON functions to keep the dependency surface small.
    """
    if (os.getenv("SCORING_MODEL_VERSION", "v1") or "v1").strip().lower() != "v2":
        return False
    if (os.getenv("JOB_INTEL_RUN_TYPE", "production") or "production").strip().lower() != "production":
        return False
    try:
        with store.connect(read_only=True) as conn:
            row = conn.execute(
                """
                SELECT COUNT(*)
                FROM runs
                WHERE id <= ?
                  AND run_type = 'production'
                  AND mode = 'daily'
                  AND (
                        notes LIKE '%scoring_model_version=v2%'
                     OR metadata_json LIKE '%\"scoring_model_version\": \"v2\"%'
                  )
                """,
                (run_id,),
            ).fetchone()
            n = int(row[0] or 0) if row else 0
            return n > 0 and n <= 7
    except Exception:
        # Never fail the run due to reporting logic.
        return False





def _delivery_db_status(delivery: SlackDeliveryResult) -> str:
    if delivery.success:
        return "sent"
    return delivery.status if delivery.status in {"failed", "skipped"} else "failed"

def _prepare_notifications(
    store: JobIntelStore,
    run_id: int,
    channel: str,
    message_type: str,
    items: list[tuple[Vacancy, Any, int]],
) -> list[int]:
    notification_ids: list[int] = []
    for vacancy, evaluation, vacancy_id in items:
        body = format_vacancy_summary(vacancy, evaluation)
        payload = _notification_payload(vacancy, evaluation, vacancy_id)
        notification_ids.append(
            store.create_notification(
                run_id,
                channel,
                message_type,
                body,
                vacancy_id=vacancy_id,
                card_key=payload.get("card_key"),
                notification_kind=message_type,
                payload=payload,
                delivery_status="pending",
                delivery_attempts=0,
            )
        )
    return notification_ids



def _finalize_notifications(store: JobIntelStore, notification_ids: list[int], delivery: SlackDeliveryResult) -> None:
    status = _delivery_db_status(delivery)
    error = None if delivery.success else delivery.error
    for notification_id in notification_ids:
        store.mark_notification_delivery(notification_id, status, attempts=delivery.attempts, delivery_error=error)


def _deliver_vacancy_notifications(
    store: JobIntelStore,
    run_id: int,
    channel: str,
    items: list[tuple[Vacancy, Any, int]],
) -> list[dict[str, Any]]:
    deliveries: list[dict[str, Any]] = []
    crm = CRMService.from_store(store)
    for vacancy, evaluation, vacancy_id in items:
        body = _format_vacancy_feedback_message(vacancy, evaluation)
        payload = _notification_payload(vacancy, evaluation, vacancy_id)
        notification_id = store.create_notification(
            run_id,
            channel,
            "vacancy_card",
            body,
            vacancy_id=vacancy_id,
            card_key=payload.get("card_key"),
            notification_kind="vacancy_card",
            payload=payload,
            delivery_status="pending",
            delivery_attempts=0,
        )
        delivery = _deliver_to_slack(body, channel, prefer_gateway=True)
        _finalize_notifications(store, [notification_id], delivery)
        resolved_channel_id, tracked_message_ts = _delivery_tracking_identity(channel, delivery)
        if delivery.success:
            if not (resolved_channel_id and tracked_message_ts):
                logger.warning(
                    "vacancy_card_tracking_untrackable vacancy_id=%s channel=%r delivery_ts=%r delivery_channel_id=%r",
                    vacancy_id, channel, delivery.message_ts, delivery.channel_id,
                )
            else:
                store.record_vacancy_slack_message(
                    vacancy_id=vacancy_id,
                    run_id=run_id,
                    vacancy_key=payload.get("vacancy_key"),
                    canonical_url=payload.get("canonical_url"),
                    card_key=payload.get("card_key"),
                    notification_id=notification_id,
                    slack_channel=resolved_channel_id,
                    slack_message_ts=tracked_message_ts,
                    message_type="vacancy_card",
                    company=vacancy.company,
                    title=vacancy.title,
                    score=int(getattr(evaluation, "score", 0) or 0),
                    recommendation=str(getattr(evaluation, "recommendation", "reject") or "reject"),
                    url=vacancy.url,
                    sent_at=datetime.now(timezone.utc).isoformat(),
                )
            store.set_vacancy_status(vacancy_id, "notified")
            if delivery.message_ts and resolved_channel_id:
                try:
                    opportunity = crm.ensure_opportunity_for_vacancy(vacancy=vacancy, vacancy_id=vacancy_id)
                    crm.link_slack_message_to_opportunity(
                        opportunity_id=int(opportunity["id"]),
                        slack_channel_id=resolved_channel_id,
                        slack_message_ts=str(delivery.message_ts),
                        slack_thread_ts=str(delivery.message_ts),
                    )
                    crm.transition_for_delivery(int(opportunity["id"]))
                    logger.info(
                        "crm_delivery_mapping_ok vacancy_id=%s opportunity_id=%s channel=%s ts=%s",
                        vacancy_id,
                        opportunity["id"],
                        resolved_channel_id,
                        delivery.message_ts,
                    )
                except Exception:
                    logger.exception(
                        "crm_delivery_mapping_failed vacancy_id=%s channel=%s ts=%s",
                        vacancy_id,
                        resolved_channel_id,
                        delivery.message_ts,
                    )
        else:
            store.set_vacancy_status(vacancy_id, "active")
        deliveries.append(
            {
                "vacancy_id": vacancy_id,
                "notification_id": notification_id,
                "card_key": payload.get("card_key"),
                "delivery": delivery,
                "message_ts": tracked_message_ts if delivery.success else None,
            }
        )
    return deliveries


def _normalize_source_notification_key(source: str) -> str:
    return "target_companies" if source == "target-company" else source



def _source_search_notification_payload(
    source: str,
    source_status: dict[str, Any],
    *,
    stats: dict[str, int],
    accepted_rows: list[tuple[Vacancy, Any, int]],
) -> dict[str, Any]:
    ranked_rows = sorted(accepted_rows, key=lambda item: item[1].score, reverse=True)
    top_matches = [
        {
            "vacancy_id": vacancy_id,
            "company": vacancy.company,
            "title": vacancy.title,
            "location": vacancy.location,
            "score": evaluation.score,
            "tier": evaluation.tier,
            "recommendation": evaluation.recommendation,
        }
        for vacancy, evaluation, vacancy_id in ranked_rows[:3]
    ]
    return {
        "source": source,
        "status": source_status.get("status", "unknown"),
        "hits": int(source_status.get("hits") or 0),
        "errors": list(source_status.get("errors") or []),
        "acquisition": source_status.get("acquisition"),
        "found": stats.get("found", 0),
        "executive_matches": stats.get("executive_matches", 0),
        "accepted": stats.get("accepted", 0),
        "rejected": stats.get("rejected", 0),
        "top_matches": top_matches,
    }



def _format_source_search_notification(
    source: str,
    source_status: dict[str, Any],
    *,
    stats: dict[str, int],
    accepted_rows: list[tuple[Vacancy, Any, int]],
    channel: str,
) -> str:
    lines = [f"*Search source update* — {source}", ""]
    lines.append(f"Channel: {channel}")
    lines.append(f"Status: {source_status.get('status', 'unknown')}")
    acquisition = source_status.get("acquisition")
    if acquisition:
        lines.append(f"Acquisition: {acquisition}")
    if source_status.get("hits") is not None:
        lines.append(f"Hits: {int(source_status.get('hits') or 0)}")
    lines.append(
        "Found: "
        f"{stats.get('found', 0)} | accepted: {stats.get('accepted', 0)} | rejected: {stats.get('rejected', 0)} | executive_matches: {stats.get('executive_matches', 0)}"
    )
    if source_status.get("errors"):
        lines.append(f"Errors: {', '.join(str(error) for error in source_status['errors'])}")
    session = source_status.get("session_health") or {}
    if session:
        browser_profile = session.get("browser_profile") or "n/a"
        lines.append(
            f"Session: profile={browser_profile} | pages_fetched={session.get('pages_fetched', 0)} | login_walls={session.get('login_walls', 0)} | auth_redirects={session.get('auth_redirects', 0)}"
        )
    lines.append("")
    if accepted_rows:
        lines.append("*Top accepted matches*")
        ranked_rows = sorted(accepted_rows, key=lambda item: item[1].score, reverse=True)
        for idx, (vacancy, evaluation, vacancy_id) in enumerate(ranked_rows[:3], start=1):
            lines.append(
                f"{idx}. {vacancy.company} — {vacancy.title} | {vacancy.location} | score={evaluation.score} | tier={evaluation.tier} | vacancy_id={vacancy_id}"
            )
    else:
        lines.append("Top accepted matches: none")
    return "\n".join(lines).rstrip()



def _deliver_source_notifications(
    store: JobIntelStore,
    run_id: int,
    channel: str,
    source_statuses: dict[str, dict[str, Any]],
    source_counts: dict[str, dict[str, int]],
    accepted_by_source: dict[str, list[tuple[Vacancy, Any, int]]],
) -> list[dict[str, Any]]:
    if os.getenv("JOB_INTEL_SEND_SOURCE_SEARCH_UPDATES", "0").strip() != "1":
        return []
    deliveries: list[dict[str, Any]] = []
    for source in sorted(source_statuses):
        source_status = source_statuses[source]
        stats = source_counts.get(source, {"found": 0, "executive_matches": 0, "accepted": 0, "rejected": 0})
        accepted_rows = accepted_by_source.get(source, [])
        body = _format_source_search_notification(
            source,
            source_status,
            stats=stats,
            accepted_rows=accepted_rows,
            channel=channel,
        )
        notification_id = store.create_notification(
            run_id,
            channel,
            "source_status",
            body,
            notification_kind="source_status",
            payload=_source_search_notification_payload(source, source_status, stats=stats, accepted_rows=accepted_rows),
            delivery_status="pending",
        )
        delivery = _deliver_to_slack(body, channel)
        store.mark_notification_delivery(
            notification_id,
            _delivery_db_status(delivery),
            attempts=delivery.attempts,
            delivery_error=delivery.error,
        )
        deliveries.append({"source": source, "notification_id": notification_id, "delivery": delivery.__dict__})
    return deliveries



def _vacancy_evaluation_from_row(row: dict[str, Any]) -> tuple[Vacancy, Evaluation, int]:
    vacancy = Vacancy(
        source=str(row.get("source") or ""),
        source_id=str(row.get("source_id") or ""),
        company=str(row.get("company") or "Unknown"),
        title=str(row.get("title") or "Vacancy"),
        location=str(row.get("location") or "Unknown"),
        url=str(row.get("url") or ""),
        description=str(row.get("description") or ""),
        posted_at=row.get("posted_at"),
        scraped_at=row.get("scraped_at"),
        salary=row.get("salary"),
        company_url=row.get("company_url"),
        metadata=json.loads(row.get("metadata_json") or "{}") if row.get("metadata_json") else {},
    )
    evaluation = Evaluation(
        score=int(row.get("score") or 0),
        tier=str(row.get("tier") or "reject"),
        recommendation=str(row.get("recommendation") or "reject"),
        salary_tier=row.get("salary_tier"),
        matched_signals=json.loads(row.get("matched_signals_json") or "[]") if row.get("matched_signals_json") else [],
        concerns=json.loads(row.get("concerns_json") or "[]") if row.get("concerns_json") else [],
        reasons=json.loads(row.get("reasons_json") or "[]") if row.get("reasons_json") else [],
        raw_breakdown=json.loads(row.get("raw_breakdown_json") or "{}") if row.get("raw_breakdown_json") else {},
    )
    vacancy_id = int(row.get("vacancy_id") or row.get("id") or 0)
    return vacancy, evaluation, vacancy_id



def run_daily() -> str:
    assert_runtime_contract()
    store = _store()
    store.bootstrap()
    run_id = store.start_run("daily")
    performance = RunPerformanceRecorder(run_id)
    run_started_perf = perf_counter()
    run_status = "ok"
    digest = "[SILENT]"
    source_statuses: dict[str, dict[str, Any]] = {}
    source_counts: dict[str, dict[str, Any]] = {}
    source_notifications: list[dict[str, Any]] = []
    vacancies: list[Vacancy] = []
    accepted: list[tuple[Vacancy, Any, int]] = []
    strategy_count = 0
    run_notes = ""
    run_metadata: dict[str, Any] = {}
    try:
        with performance.span("bootstrap_config", parent_span_name="daily_run_total"):
            cfg = load_config_bundle() or DEFAULT_CONFIG
            dedup_cfg = cfg["deduplication"]
            similarity_threshold = dedup_cfg["secondary_similarity"]["description_similarity_threshold"]
            repost_window_days = dedup_cfg["repost_detection"]["repost_window_days"]

        with performance.span("source_acquisition_total", parent_span_name="daily_run_total"):
            collected = _collect_vacancies_compat(store, performance)
            vacancies = collected.vacancies
            source_statuses = collected.source_statuses

        scored_rows: list[tuple[Vacancy, Any, dict[str, Any], int, bool]] = []
        canonical_rows: list[Vacancy] = []
        seen_keys: set[str] = set()
        accepted_by_source: dict[str, list[tuple[Vacancy, Any, int]]] = {}

        def _count_source(vacancy: Vacancy, classification: dict[str, Any], evaluation: Any) -> None:
            source_key = _normalize_source_notification_key(vacancy.source)
            stats = source_counts.setdefault(
                source_key,
                {
                    "raw_found_count": 0,
                    "found": 0,
                    "executive_matches": 0,
                    "accepted": 0,
                    "rejected": 0,
                    "found_count": 0,
                    "executive_detected_count": 0,
                    "scored_count": 0,
                    "strong_fit_count": 0,
                    "potential_fit_count": 0,
                    "needs_review_count": 0,
                    "near_miss_count": 0,
                    "reject_count": 0,
                    "accepted_count": 0,
                    "notified_count": 0,
                    "rejected_count": 0,
                    "vacancies_deduped": 0,
                    "score_list": [],
                    "accepted_score_list": [],
                    "company_known": 0,
                    "location_known": 0,
                    "salary_known": 0,
                    "seniority_confident": 0,
                },
            )
            stats["found_count"] += 1
            stats["found"] += 1
            stats["scored_count"] += 1
            if getattr(evaluation, "tier", None) in {"exceptional_fit", "strong_fit"}:
                stats["executive_matches"] += 1
            if classification.get("executive_detected"):
                stats["executive_detected_count"] += 1
            score = int(getattr(evaluation, "score", 0) or 0)
            stats["score_list"].append(score)
            if vacancy.company and vacancy.company != "Unknown":
                stats["company_known"] += 1
            if vacancy.location and vacancy.location != "Unknown":
                stats["location_known"] += 1
            if vacancy.salary:
                stats["salary_known"] += 1
            reco = str(getattr(evaluation, "recommendation", "reject") or "reject")
            if reco == "strong_fit":
                stats["strong_fit_count"] += 1
                stats["accepted_count"] += 1
                stats["accepted"] += 1
                stats["accepted_score_list"].append(score)
            elif reco in {"potential_fit", "possible_fit"}:
                stats["potential_fit_count"] += 1
                stats["accepted_count"] += 1
                stats["accepted"] += 1
                stats["accepted_score_list"].append(score)
            elif reco == "needs_review":
                stats["needs_review_count"] += 1
                stats["rejected_count"] += 1
                stats["rejected"] += 1
            elif reco == "near_miss":
                stats["near_miss_count"] += 1
                stats["rejected_count"] += 1
                stats["rejected"] += 1
            else:
                stats["reject_count"] += 1
                stats["rejected_count"] += 1
                stats["rejected"] += 1

        def _is_linkedin_discovery_url(url: str) -> bool:
            lowered = (url or "").strip().lower()
            if "linkedin.com" not in lowered:
                return False
            return any(token in lowered for token in ("/jobs/collections/", "/jobs/search", "/jobs/recommended", "recommended_jobs"))

        def _normalize_job_url(url: str) -> str:
            try:
                from urllib.parse import urlsplit, urlunsplit
                parsed = urlsplit((url or "").strip())
                if not parsed.scheme or not parsed.netloc:
                    return (url or "").strip().rstrip("/")
                normalized = urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))
                return normalized.rstrip("/")
            except Exception:
                return (url or "").strip().rstrip("/")

        seen_urls: dict[str, str] = {}
        dual_score_enabled = _dual_score_rollout_enabled(store, run_id)
        dual_scores_by_url: dict[str, dict[str, object]] = {} if dual_score_enabled else {}
        scoring_model_version = (os.getenv("SCORING_MODEL_VERSION", "v1") or "v1").strip().lower()
        effective_scoring_version = scoring_model_version if scoring_model_version in {"v1", "v2", "v3"} else "v1"
        v3_shadow_enabled = (os.getenv("SCORING_V3_SHADOW_ENABLED", "1") or "1").strip().lower() not in {"0", "false", "no"}
        v3_shadow_counts: Counter[str] = Counter()
        v3_nr_shadow_counts: Counter[str] = Counter()
        normalization_ms = 0.0
        scoring_ms = 0.0
        dedup_stats: dict[str, Any] = {
            "input_count": 0,
            "output_count": 0,
            "duplicate_count": 0,
            "comparisons_count": 0,
            "db_query_count": 0,
            "cache_hit_count": 0,
        }
        dedup_timings_ms: dict[str, float] = {
            "url_canonicalization": 0.0,
            "exact_key_lookup": 0.0,
            "content_hashing": 0.0,
            "description_hashing": 0.0,
            "fuzzy_matching": 0.0,
            "db_lookup": 0.0,
            "lineage_persistence": 0.0,
        }

        for vacancy in vacancies:
            started = perf_counter()
            raw_url = str(getattr(vacancy, "url", "") or "")
            if (getattr(vacancy, "source", "") or "").strip().lower() == "linkedin" and _is_linkedin_discovery_url(raw_url):
                continue
            dedup_stats["input_count"] += 1
            dedup_started = perf_counter()
            vacancy.url = _normalize_job_url(vacancy.url)
            dedup_timings_ms["url_canonicalization"] += (perf_counter() - dedup_started) * 1000.0
            vacancy_key = canonical_vacancy_key(vacancy)
            vacancy_id = store.upsert_vacancy(vacancy, vacancy_key)
            classification = classify_vacancy(vacancy)
            normalization_ms += (perf_counter() - started) * 1000.0

            started = perf_counter()
            if dual_score_enabled:
                ev1 = score_vacancy_with_version(vacancy, "v1")
                ev2 = score_vacancy_with_version(vacancy, "v2")
                evaluation = score_vacancy_with_version(vacancy, effective_scoring_version)
                dual_scores_by_url[vacancy.url] = {
                    "score_v1": int(getattr(ev1, "score", 0) or 0),
                    "rec_v1": str(getattr(ev1, "recommendation", "reject") or "reject"),
                    "score_v2": int(getattr(ev2, "score", 0) or 0),
                    "rec_v2": str(getattr(ev2, "recommendation", "reject") or "reject"),
                }
            else:
                evaluation = score_vacancy(vacancy)
            scoring_ms += (perf_counter() - started) * 1000.0

            if v3_shadow_enabled:
                ev2_shadow = score_vacancy_with_version(vacancy, "v2")
                ev3_shadow = score_vacancy_v3_shadow(vacancy)
                rec_v3 = str(ev3_shadow.get("recommendation") or "reject")
                rec_v3_nr = rec_v3
                store.upsert_vacancy_scoring_shadow(
                    run_id=run_id,
                    vacancy_key=vacancy_key,
                    source=vacancy.source,
                    score_v2=int(getattr(ev2_shadow, "score", 0) or 0),
                    recommendation_v2=str(getattr(ev2_shadow, "recommendation", "reject") or "reject"),
                    score_v3=int(ev3_shadow.get("score") or 0),
                    recommendation_v3=rec_v3,
                    score_v3_nr=int(ev3_shadow.get("score") or 0),
                    recommendation_v3_nr=rec_v3_nr,
                    gates_v3=ev3_shadow.get("gates") or {},
                    function_class_v3=str(ev3_shadow.get("function_class") or ""),
                )
                v3_shadow_counts[rec_v3] += 1
                v3_nr_shadow_counts[rec_v3_nr] += 1

            store.save_evaluation(vacancy_key, evaluation, run_id=run_id)
            _count_source(vacancy, classification, evaluation)

            started = perf_counter()
            hash_started = perf_counter()
            _content_hash_probe = sha256_text(f"{vacancy.company}|{vacancy.title}|{vacancy.url}")
            dedup_timings_ms["content_hashing"] += (perf_counter() - hash_started) * 1000.0
            hash_started = perf_counter()
            _description_hash_probe = sha256_text(vacancy.description or "")
            dedup_timings_ms["description_hashing"] += (perf_counter() - hash_started) * 1000.0
            lookup_started = perf_counter()
            is_dup = vacancy_key in seen_keys
            if not is_dup and vacancy.url:
                url_key = vacancy.url
                canonical_for_url = seen_urls.get(url_key)
                if canonical_for_url is not None:
                    is_dup = True
                    dedup_stats["cache_hit_count"] += 1
                    persist_started = perf_counter()
                    store.save_duplicate(canonical_for_url, vacancy_key, "url match", 1.0)
                    dedup_timings_ms["lineage_persistence"] += (perf_counter() - persist_started) * 1000.0
                else:
                    seen_urls[url_key] = vacancy_key
            dedup_timings_ms["exact_key_lookup"] += (perf_counter() - lookup_started) * 1000.0
            if not is_dup:
                fuzzy_started = perf_counter()
                for existing in canonical_rows:
                    dedup_stats["comparisons_count"] += 1
                    if is_duplicate(vacancy, existing, similarity_threshold=similarity_threshold, repost_window_days=repost_window_days):
                        is_dup = True
                        persist_started = perf_counter()
                        store.save_duplicate(canonical_vacancy_key(existing), vacancy_key, "semantic/repost match", description_similarity(vacancy.description, existing.description))
                        dedup_timings_ms["lineage_persistence"] += (perf_counter() - persist_started) * 1000.0
                        break
                dedup_timings_ms["fuzzy_matching"] += (perf_counter() - fuzzy_started) * 1000.0
            dedup_timings_ms["db_lookup"] += 0.0
            _ = _content_hash_probe, _description_hash_probe
            if is_dup:
                dedup_stats["duplicate_count"] += 1
                store.set_vacancy_status(vacancy_id, "duplicate")
                scored_rows.append((vacancy, evaluation, classification, vacancy_id, True))
                continue

            source_counts[_normalize_source_notification_key(vacancy.source)]["vacancies_deduped"] += 1
            dedup_stats["output_count"] += 1
            if evaluation.recommendation == "reject":
                store.set_vacancy_status(vacancy_id, "rejected")
                scored_rows.append((vacancy, evaluation, classification, vacancy_id, False))
                continue
            if evaluation.recommendation == "near_miss":
                store.set_vacancy_status(vacancy_id, "near_miss")
                scored_rows.append((vacancy, evaluation, classification, vacancy_id, False))
                canonical_rows.append(vacancy)
                seen_keys.add(vacancy_key)
                continue
            if _should_notify_vacancy(store, vacancy_id, vacancy, evaluation, repost_window_days):
                accepted.append((vacancy, evaluation, vacancy_id))
                accepted_by_source.setdefault(_normalize_source_notification_key(vacancy.source), []).append((vacancy, evaluation, vacancy_id))
            else:
                store.set_vacancy_status(vacancy_id, "notified")
            scored_rows.append((vacancy, evaluation, classification, vacancy_id, False))
            canonical_rows.append(vacancy)
            seen_keys.add(vacancy_key)

        performance.record_completed("normalization_total", parent_span_name="daily_run_total", duration_ms=int(round(normalization_ms)))
        performance.record_completed("scoring_total", parent_span_name="daily_run_total", duration_ms=int(round(scoring_ms)))
        dedup_total_duration = int(round(sum(dedup_timings_ms.values())))
        performance.record_completed(
            "dedup.url_canonicalization",
            parent_span_name="dedup_total",
            duration_ms=int(round(dedup_timings_ms["url_canonicalization"])),
            found_count=int(dedup_stats["input_count"]),
            normalized_count=int(dedup_stats["output_count"]),
            duplicate_count=int(dedup_stats["duplicate_count"]),
        )
        performance.record_completed(
            "dedup.exact_key_lookup",
            parent_span_name="dedup_total",
            duration_ms=int(round(dedup_timings_ms["exact_key_lookup"])),
            found_count=int(dedup_stats["input_count"]),
            normalized_count=int(dedup_stats["output_count"]),
            duplicate_count=int(dedup_stats["duplicate_count"]),
            metadata={"cache_hit_count": int(dedup_stats["cache_hit_count"])},
        )
        performance.record_completed(
            "dedup.content_hashing",
            parent_span_name="dedup_total",
            duration_ms=int(round(dedup_timings_ms["content_hashing"])),
            found_count=int(dedup_stats["input_count"]),
        )
        performance.record_completed(
            "dedup.description_hashing",
            parent_span_name="dedup_total",
            duration_ms=int(round(dedup_timings_ms["description_hashing"])),
            found_count=int(dedup_stats["input_count"]),
        )
        performance.record_completed(
            "dedup.fuzzy_matching",
            parent_span_name="dedup_total",
            duration_ms=int(round(dedup_timings_ms["fuzzy_matching"])),
            found_count=int(dedup_stats["input_count"]),
            normalized_count=int(dedup_stats["output_count"]),
            duplicate_count=int(dedup_stats["duplicate_count"]),
            metadata={"comparisons_count": int(dedup_stats["comparisons_count"])},
        )
        performance.record_completed(
            "dedup.db_lookup",
            parent_span_name="dedup_total",
            duration_ms=int(round(dedup_timings_ms["db_lookup"])),
            metadata={"db_query_count": int(dedup_stats["db_query_count"])},
        )
        performance.record_completed(
            "dedup.lineage_persistence",
            parent_span_name="dedup_total",
            duration_ms=int(round(dedup_timings_ms["lineage_persistence"])),
            duplicate_count=int(dedup_stats["duplicate_count"]),
        )
        performance.record_completed(
            "dedup.summary",
            parent_span_name="dedup_total",
            duration_ms=0,
            found_count=int(dedup_stats["input_count"]),
            normalized_count=int(dedup_stats["output_count"]),
            duplicate_count=int(dedup_stats["duplicate_count"]),
            metadata={
                "comparisons_count": int(dedup_stats["comparisons_count"]),
                "db_query_count": int(dedup_stats["db_query_count"]),
                "cache_hit_count": int(dedup_stats["cache_hit_count"]),
            },
        )
        performance.record_completed(
            "dedup_total",
            parent_span_name="daily_run_total",
            duration_ms=dedup_total_duration,
            found_count=int(dedup_stats["input_count"]),
            normalized_count=int(dedup_stats["output_count"]),
            duplicate_count=int(dedup_stats["duplicate_count"]),
            metadata={
                "comparisons_count": int(dedup_stats["comparisons_count"]),
                "db_query_count": int(dedup_stats["db_query_count"]),
                "cache_hit_count": int(dedup_stats["cache_hit_count"]),
            },
        )

        accepted.sort(key=lambda item: item[1].score, reverse=True)
        operator_footer = _source_footer(source_statuses)
        search_channel = _search_report_channel(cfg)
        target_company_hits = int((source_statuses.get("target_companies") or {}).get("hits") or 0)

        for source, stats in source_counts.items():
            existing = dict(source_statuses.get(source, {"source": source, "status": "unknown"}))
            session = existing.get("session_health") or {}
            hits = int(existing.get("hits") or stats["found"])
            stats["raw_found_count"] = hits
            errors = list(existing.get("errors") or [])
            anti_bot_failures = 1 if existing.get("status") == "blocked" else (len(errors) if errors else 0)
            metrics = metrics_from_counts(
                source=source,
                found=stats["found"],
                executive_matches=stats["executive_matches"],
                accepted=stats["accepted"],
                rejected=stats["rejected"],
                extraction_successes=hits,
                extraction_attempts=max(hits + len(errors), 1),
                anti_bot_failures=anti_bot_failures,
                detail_pages_opened=int(session.get("detail_pages_opened") or 0),
                target_company_hits=target_company_hits,
            )
            existing["metrics"] = metrics.__dict__
            source_statuses[source] = existing

        if len(source_statuses) > 2:
            source_notifications = _deliver_source_notifications(store, run_id, search_channel, source_statuses, source_counts, accepted_by_source)

        max_cards_per_run = int(os.getenv("JOB_INTEL_MAX_VACANCY_CARDS_PER_RUN", "10") or "10")
        nondup_scored = [(v, e, c, vid, dup) for (v, e, c, vid, dup) in scored_rows if not dup]
        nondup_scored.sort(key=lambda row: int(getattr(row[1], "score", 0) or 0), reverse=True)
        actionable_candidates = _canonical_actionable_candidates(scored_rows)

        with performance.span("card_decision_total", parent_span_name="daily_run_total") as span:
            surfaced_rows: list[tuple[Vacancy, Any, int]] = []
            top_scored = [(vacancy, evaluation) for _, _, _, _, vacancy, evaluation, _ in actionable_candidates[:20]]
            near_miss_scored = [
                (vacancy, evaluation)
                for _, _, _, _, vacancy, evaluation, _ in actionable_candidates
                if str(getattr(evaluation, "recommendation", None) or "") == "near_miss"
            ]
            for candidate_rank, (_priority, _neg_score, _neg_freshness, _vacancy_sort_id, vacancy, evaluation, vacancy_id) in enumerate(actionable_candidates, start=1):
                decision_plan = _card_decision_plan(store, vacancy, evaluation, repost_window_days)
                vacancy_key = canonical_vacancy_key(vacancy)
                canonical_url = canonical_job_url(vacancy.url, vacancy.source)
                card_key = canonical_url or vacancy_key
                if len(surfaced_rows) >= max_cards_per_run:
                    store.record_vacancy_card_decision(
                        run_id=run_id, vacancy_id=vacancy_id, vacancy_key=vacancy_key, canonical_url=canonical_url, card_key=card_key,
                        source=vacancy.source, company=vacancy.company, title=vacancy.title,
                        score=int(getattr(evaluation, "score", 0) or 0), recommendation=str(getattr(evaluation, "recommendation", "reject") or "reject"),
                        candidate_rank=candidate_rank, decision="skipped_limit", suppression_reason="max_cards_per_run",
                        previous_sent_run_id=decision_plan.previous_sent_run_id, previous_sent_at=decision_plan.previous_sent_at,
                        previous_feedback_label=decision_plan.previous_feedback_label, feedback_state_active=decision_plan.feedback_state_active,
                        next_allowed_send_at=decision_plan.next_allowed_send_at, score_delta_since_last_sent=decision_plan.score_delta_since_last_sent,
                        recommendation_changed_since_last_sent=decision_plan.recommendation_changed_since_last_sent,
                        content_hash_changed=decision_plan.content_hash_changed, description_hash_changed=decision_plan.description_hash_changed,
                    )
                    continue
                if decision_plan.should_notify:
                    surfaced_rows.append((vacancy, evaluation, vacancy_id))
                    decision = "sent"
                else:
                    store.set_vacancy_status(vacancy_id, "notified")
                    decision = "suppressed"
                store.record_vacancy_card_decision(
                    run_id=run_id, vacancy_id=vacancy_id, vacancy_key=vacancy_key, canonical_url=canonical_url, card_key=card_key,
                    source=vacancy.source, company=vacancy.company, title=vacancy.title,
                    score=int(getattr(evaluation, "score", 0) or 0), recommendation=str(getattr(evaluation, "recommendation", "reject") or "reject"),
                    candidate_rank=candidate_rank, decision=decision, suppression_reason=decision_plan.suppression_reason,
                    previous_sent_run_id=decision_plan.previous_sent_run_id, previous_sent_at=decision_plan.previous_sent_at,
                    previous_feedback_label=decision_plan.previous_feedback_label, feedback_state_active=decision_plan.feedback_state_active,
                    next_allowed_send_at=decision_plan.next_allowed_send_at, score_delta_since_last_sent=decision_plan.score_delta_since_last_sent,
                    recommendation_changed_since_last_sent=decision_plan.recommendation_changed_since_last_sent,
                    content_hash_changed=decision_plan.content_hash_changed, description_hash_changed=decision_plan.description_hash_changed,
                )
            span.set_counts(found_count=len(actionable_candidates), cards_sent=len(surfaced_rows))

        planned_notified_by_source: dict[str, int] = {}
        for vacancy, _, _ in surfaced_rows:
            key = _normalize_source_notification_key(vacancy.source)
            planned_notified_by_source[key] = planned_notified_by_source.get(key, 0) + 1

        per_source_funnel: list[dict[str, object]] = []
        for source in sorted(source_counts.keys()):
            stats = source_counts.get(source) or {}
            stats["notified_count"] = int(planned_notified_by_source.get(source, 0) or 0)
            stats["notified"] = int(planned_notified_by_source.get(source, 0) or 0)
            per_source_funnel.append(
                {
                    "source": source,
                    "found": int(stats.get("found_count") or 0),
                    "exec_detected": int(stats.get("executive_detected_count") or 0),
                    "scored": int(stats.get("scored_count") or 0),
                    "accepted": int(stats.get("accepted_count") or 0),
                    "notified": int(stats.get("notified_count") or 0),
                }
            )

        decision_counts = Counter()
        for v, e, _, _, _ in nondup_scored:
            decision_counts[str(getattr(e, "recommendation", "reject") or "reject")] += 1

        rejected_reason_counts: dict[str, int] = {}
        for v, e, _, _, dup in scored_rows:
            if dup:
                bucket = reject_reason_bucket(v, e, duplicate=True)
                rejected_reason_counts[bucket] = rejected_reason_counts.get(bucket, 0) + 1
                continue
            if getattr(e, "recommendation", None) == "reject":
                bucket = reject_reason_bucket(v, e, duplicate=False)
                rejected_reason_counts[bucket] = rejected_reason_counts.get(bucket, 0) + 1

        def _country(loc: str) -> str:
            text = (loc or "").strip()
            if not text or text == "Unknown":
                return "Unknown"
            if text.lower() == "remote":
                return "Remote"
            if "," in text:
                tail = text.rsplit(",", 1)[-1].strip()
                return tail or text
            return text

        title_counts = Counter()
        country_counts = Counter()
        company_counts = Counter()
        for v, _, _, _, dup in scored_rows:
            if dup:
                continue
            title_counts[(v.title or "").strip() or "Unknown"] += 1
            company_counts[(v.company or "").strip() or "Unknown"] += 1
            country_counts[_country((v.location or "").strip() or "Unknown")] += 1

        market_titles = [(k, int(v)) for k, v in title_counts.most_common(8)]
        market_countries = [(k, int(v)) for k, v in country_counts.most_common(8)]
        market_companies = [(k, int(v)) for k, v in company_counts.most_common(10)]
        rejected_scored = [(v, e) for (v, e, _, _, dup) in scored_rows if (not dup and getattr(e, "recommendation", None) == "reject")]
        rejected_scored.sort(key=lambda row: int(getattr(row[1], "score", 0) or 0), reverse=True)
        card_decision_counts = store.card_decision_counts(run_id)
        summary_card_candidates = sum(card_decision_counts.values())
        summary_card_sent = int(card_decision_counts.get("sent", 0))
        summary_card_suppressed = int(card_decision_counts.get("suppressed", 0)) + int(card_decision_counts.get("skipped_limit", 0))

        source_value_rows = build_source_value_rows(source_counts=source_counts, source_statuses=source_statuses, card_decisions=store.fetch_card_decisions(run_id))

        performance_block = ""
        performance_reasons: list[str] = []
        detailed_performance_report = ""
        with performance.span("digest_generation_total", parent_span_name="daily_run_total"):
            runtime_so_far_ms = int(round((perf_counter() - run_started_perf) * 1000))
            recent_runtime_ms = store.recent_daily_run_span_durations("daily_run_total", limit=5, exclude_run_id=run_id)
            performance_reasons = performance_trigger_reason(
                total_runtime_ms=runtime_so_far_ms,
                recent_runtime_ms=recent_runtime_ms,
                source_rows=source_value_rows,
            )
            performance_block = format_compact_performance_block(
                total_runtime_ms=runtime_so_far_ms,
                source_rows=source_value_rows,
                reasons=performance_reasons,
            )
            digest = format_executive_opportunity_report(
                run_id=run_id,
                title="Daily executive opportunity report",
                per_source_funnel=per_source_funnel,
                top_scored=top_scored,
                top_rejected=rejected_scored[:20],
                rejected_reason_counts=rejected_reason_counts,
                market_titles=market_titles,
                market_countries=market_countries,
                market_companies=market_companies,
                decision_counts=dict(decision_counts),
                top_near_miss=near_miss_scored,
                scoring_model_version=effective_scoring_version,
                vacancy_card_candidates=summary_card_candidates,
                vacancy_card_sent=summary_card_sent,
                vacancy_card_suppressed=summary_card_suppressed,
                operator_footer=operator_footer,
                dual_scores=dual_scores_by_url if dual_score_enabled else None,
                performance_block=performance_block or None,
            )
            if v3_shadow_enabled:
                v3_block = (
                    "*V3 Shadow Buckets*\\n"
                    f"- strong_fit: {int(v3_nr_shadow_counts.get('strong_fit', 0))}\\n"
                    f"- needs_review: {int(v3_nr_shadow_counts.get('needs_review', 0))}\\n"
                    f"- near_miss: {int(v3_nr_shadow_counts.get('near_miss', 0))}\\n"
                    f"- reject: {int(v3_nr_shadow_counts.get('reject', 0))}"
                )
                digest = (digest + "\\n\\n" + v3_block).rstrip()
            detailed_performance_report = format_detailed_performance_report(
                run_id=run_id,
                total_runtime_ms=runtime_so_far_ms,
                phase_rows=[span.__dict__ for span in performance.spans()],
                source_rows=source_value_rows,
                reasons=performance_reasons,
            )

        with performance.span("slack_delivery_total", parent_span_name="daily_run_total") as slack_span:
            delivery_report = _deliver_to_slack(digest, search_channel)
            summary_payload = {
                "run_id": run_id,
                "found": len(vacancies),
                "accepted": len(accepted),
                "strong_fit": int(decision_counts.get("strong_fit", 0)),
                "potential_fit": int(decision_counts.get("potential_fit", 0)),
                "needs_review": int(decision_counts.get("needs_review", 0)),
                "near_miss": int(decision_counts.get("near_miss", 0)),
                "rejected": int(decision_counts.get("reject", 0)),
                "vacancy_card_candidates": summary_card_candidates,
                "vacancy_card_sent": summary_card_sent,
                "vacancy_card_suppressed": summary_card_suppressed,
            }
            summary_notification_id = store.create_notification(
                run_id, search_channel, "daily_summary", digest,
                notification_kind="daily_summary", payload=summary_payload,
                delivery_status="pending", delivery_attempts=0,
            )
            store.mark_notification_delivery(
                summary_notification_id,
                _delivery_db_status(delivery_report),
                attempts=delivery_report.attempts,
                delivery_error=None if delivery_report.success else delivery_report.error,
            )
            summary_payload.update(
                {
                    "delivery": delivery_report.__dict__,
                    "slack_channel_id": _resolve_delivery_channel_id(search_channel, delivery_report),
                    "slack_message_ts": delivery_report.message_ts,
                    "platform_message_id": delivery_report.platform_message_id,
                }
            )
            store.update_notification_payload(summary_notification_id, summary_payload)
            vacancy_deliveries = _deliver_vacancy_notifications(store, run_id, search_channel, surfaced_rows)
            vacancy_message_deliveries = vacancy_deliveries
            performance_delivery: SlackDeliveryResult | None = None
            if performance_reasons or (os.getenv("JOB_INTEL_ATTACH_PERFORMANCE_REPORT", "0") or "0").strip() == "1":
                performance_payload = {
                    "run_id": run_id,
                    "reasons": performance_reasons,
                    "forced": (os.getenv("JOB_INTEL_ATTACH_PERFORMANCE_REPORT", "0") or "0").strip() == "1",
                    "parent_notification_id": summary_notification_id,
                    "thread_ts": delivery_report.message_ts,
                    "delivery_method": "thread_reply" if delivery_report.message_ts else "channel_message",
                }
                performance_notification_id = store.create_notification(
                    run_id,
                    search_channel,
                    "performance_report",
                    detailed_performance_report,
                    notification_kind="performance_report",
                    delivery_status="pending",
                    payload=performance_payload,
                )
                performance_delivery, performance_message_ids, effective_thread_ts = _deliver_threaded_slack_report(
                    message=detailed_performance_report,
                    channel=search_channel,
                    parent_thread_ts=delivery_report.message_ts,
                )
                store.mark_notification_delivery(
                    performance_notification_id,
                    _delivery_db_status(performance_delivery),
                    attempts=performance_delivery.attempts,
                    delivery_error=performance_delivery.error,
                )
                performance_payload.update(
                    {
                        "thread_ts": effective_thread_ts,
                        "slack_channel_id": _resolve_delivery_channel_id(search_channel, performance_delivery),
                        "slack_message_ts": performance_delivery.message_ts,
                        "platform_message_id": performance_delivery.platform_message_id,
                        "message_ids": performance_message_ids,
                        "chunk_count": len(performance_message_ids) or 1,
                        "delivery": performance_delivery.__dict__,
                    }
                )
                store.update_notification_payload(performance_notification_id, performance_payload)
                if not performance_delivery.success:
                    logger.warning(
                        "performance_report_delivery_failed run_id=%s notification_id=%s parent_notification_id=%s error=%s",
                        run_id,
                        performance_notification_id,
                        summary_notification_id,
                        performance_delivery.error,
                    )
                run_metadata["performance_delivery"] = performance_delivery.__dict__
            slack_span.set_counts(cards_sent=sum(1 for item in vacancy_deliveries if item["delivery"].success))
            slack_span.set_status(
                _slack_delivery_phase_status(
                    summary_delivery=delivery_report,
                    vacancy_deliveries=vacancy_deliveries,
                    performance_delivery=performance_delivery,
                )
            )

        if delivery_report.success:
            for source, cnt in planned_notified_by_source.items():
                stats = source_counts.setdefault(source, {})
                stats["notified_count"] = int(cnt or 0)
                stats["notified"] = int(cnt or 0)
        else:
            for source in list(planned_notified_by_source.keys()):
                stats = source_counts.setdefault(source, {})
                stats["notified_count"] = 0
                stats["notified"] = 0

        notified_vacancy_ids = {int(item["vacancy_id"]) for item in vacancy_deliveries if item["delivery"].success}
        accepted_vacancy_ids = {vacancy_id for _, _, vacancy_id in accepted}

        with performance.span("observability_persistence_total", parent_span_name="daily_run_total"):
            for vacancy, evaluation, _classification, _vacancy_id, duplicate in scored_rows:
                if duplicate:
                    continue
                rec = str(getattr(evaluation, "recommendation", "reject") or "reject")
                if rec in {"strong_fit", "potential_fit", "needs_review", "near_miss"}:
                    store.upsert_user_feedback_unseen(canonical_vacancy_key(vacancy), run_id=run_id)
            record_daily_observability(
                store,
                run_id,
                scored_rows,
                accepted_vacancy_ids=accepted_vacancy_ids,
                notified_vacancy_ids=notified_vacancy_ids,
                dual_scores_by_url=dual_scores_by_url if dual_score_enabled else None,
                active_scoring_version=effective_scoring_version,
                active_recommendation_version=effective_scoring_version,
            )

            scored_registry_counts: dict[tuple[str, str, str], int] = {}
            for vacancy, _, _, _, _ in scored_rows:
                md = dict(getattr(vacancy, "metadata", {}) or {})
                if md.get("acquisition_path") != "registry":
                    continue
                key = (
                    str(md.get("registry_company_name") or "").strip(),
                    str(md.get("registry_ats_vendor") or "").strip(),
                    str(md.get("registry_ats_slug") or "").strip(),
                )
                if not key[0]:
                    continue
                scored_registry_counts[key] = scored_registry_counts.get(key, 0) + 1

            for source, src_status in source_statuses.items():
                rows = list(src_status.get("registry_companies") or [])
                for item in rows:
                    company_name = str(item.get("company_name") or "").strip()
                    vendor = str(item.get("ats_vendor") or source).strip()
                    slug = str(item.get("ats_slug") or "").strip()
                    if not company_name:
                        continue
                    key = (company_name, vendor, slug)
                    scored_count = int(scored_registry_counts.get(key, 0))
                    item["vacancies_scored"] = scored_count
                    item["vacancies_stored"] = scored_count
                    store.upsert_registry_company_run(run_id, source, item)

        def _pctl(values: list[int], p: float) -> int | None:
            if not values:
                return None
            xs = sorted(values)
            if len(xs) == 1:
                return int(xs[0])
            k = int(round((p / 100.0) * (len(xs) - 1)))
            k = max(0, min(len(xs) - 1, k))
            return int(xs[k])

        with performance.span("source_kpi_persistence_total", parent_span_name="daily_run_total"):
            for source, src_status in source_statuses.items():
                stats = source_counts.get(source) or {}
                found = int(stats.get("found_count") or 0)
                scores = list(stats.get("score_list") or [])
                accepted_scores = list(stats.get("accepted_score_list") or [])
                avg_score = (sum(scores) / len(scores)) if scores else None
                session = (src_status.get("session_health") or {})
                pages_fetched = session.get("pages_fetched")
                login_walls = session.get("login_walls")
                auth_redirects = session.get("auth_redirects")
                anti_bot_events = session.get("anti_bot_events")
                extraction_failures = session.get("extraction_failures")
                denom = found if found else 0
                pct_company_known = (float(stats.get("company_known") or 0) / denom) if denom else None
                pct_location_known = (float(stats.get("location_known") or 0) / denom) if denom else None
                pct_salary_known = (float(stats.get("salary_known") or 0) / denom) if denom else None
                store.upsert_source_kpi_run(
                    run_id,
                    source,
                    {
                        "source_status": src_status.get("status"),
                        "skip_reason": derive_source_reason(src_status),
                        "enabled": 0 if str(src_status.get("status") or "") == "skipped" else 1,
                        "acquisition_mode": src_status.get("acquisition"),
                        "runtime_seconds": float(src_status.get("runtime_seconds") or 0.0),
                        "attempts": None,
                        "pages_fetched": pages_fetched,
                        "login_walls": login_walls,
                        "auth_redirects": auth_redirects,
                        "anti_bot_events": anti_bot_events,
                        "extraction_failures": extraction_failures,
                        "found_count": found,
                        "executive_detected_count": int(stats.get("executive_detected_count") or 0),
                        "scored_count": int(stats.get("scored_count") or 0),
                        "accepted_count": int(stats.get("accepted_count") or 0),
                        "notified_count": int(stats.get("notified_count") or 0),
                        "vacancies_deduped": int(stats.get("vacancies_deduped") or 0),
                        "rejected_count": int(stats.get("rejected_count") or 0),
                        "avg_vacancy_score": avg_score,
                        "vacancy_score_p50": _pctl(scores, 50),
                        "vacancy_score_p90": _pctl(scores, 90),
                        "accepted_score_p50": _pctl(accepted_scores, 50),
                        "pct_company_known": pct_company_known,
                        "pct_location_known": pct_location_known,
                        "pct_salary_known": pct_salary_known,
                        "pct_seniority_confident": None,
                        "company_score_avg": None,
                        "company_score_p90": None,
                        "industry_fit_avg": None,
                        "tier1_company_count": None,
                        "tier2_company_count": None,
                        "interview_generated_count": None,
                        "error_class": None,
                        "error_fingerprint": None,
                        "error_message_truncated": None,
                    },
                )

        run_row = store.get_run(run_id) or {}
        started_at = run_row.get("started_at")
        started_dt = parse_iso_datetime(started_at) if started_at else None
        finished_dt = datetime.now(timezone.utc)
        runtime_seconds = (finished_dt - started_dt).total_seconds() if started_dt else None
        total_collected = sum(int((source_counts.get(s) or {}).get("found_count") or 0) for s in source_statuses.keys())
        total_unique = sum(int((source_counts.get(s) or {}).get("vacancies_deduped") or 0) for s in source_statuses.keys())
        duplicate_rate = (1.0 - (float(total_unique) / float(total_collected))) if total_collected else 0.0
        company_known = sum(int((source_counts.get(s) or {}).get("company_known") or 0) for s in source_statuses.keys())
        unknown_company_rate = (1.0 - (float(company_known) / float(total_collected))) if total_collected else 0.0
        login_walls_total = sum(int(((source_statuses.get(s) or {}).get("session_health") or {}).get("login_walls") or 0) for s in source_statuses.keys())
        anti_bot_total = sum(int(((source_statuses.get(s) or {}).get("session_health") or {}).get("anti_bot_events") or 0) for s in source_statuses.keys())
        auth_redirects_total = sum(int(((source_statuses.get(s) or {}).get("session_health") or {}).get("auth_redirects") or 0) for s in source_statuses.keys())
        source_failures = {s: (source_statuses.get(s) or {}).get("status") for s in source_statuses.keys() if (source_statuses.get(s) or {}).get("status") not in {"ok", "empty", "skipped"}}
        source_runtimes = {str(s): float((source_statuses.get(s) or {}).get("runtime_seconds") or 0.0) for s in source_statuses.keys() if (source_statuses.get(s) or {}).get("runtime_seconds") is not None}
        slowest_source = max(source_runtimes, key=source_runtimes.get) if source_runtimes else None
        feedback_metrics = store.feedback_metrics_for_run(run_id)
        store.upsert_production_observation_daily(
            run_id,
            {
                "run_started_at": started_at,
                "run_finished_at": finished_dt.isoformat(),
                "runtime_seconds": runtime_seconds,
                "total_collected": total_collected,
                "total_unique": total_unique,
                "duplicate_rate": duplicate_rate,
                "unknown_company_rate": unknown_company_rate,
                "strong_fit_count": int(decision_counts.get("strong_fit", 0)),
                "needs_review_count": int(decision_counts.get("needs_review", 0)),
                "near_miss_count": int(decision_counts.get("near_miss", 0)),
                "login_walls": login_walls_total,
                "anti_bot_events": anti_bot_total,
                "auth_redirects": auth_redirects_total,
                "source_failures": source_failures,
                "source_runtimes": source_runtimes,
                "slowest_source": slowest_source,
                "vacancies_sent": int(feedback_metrics.get("vacancies_sent") or 0),
                "vacancies_reacted": int(feedback_metrics.get("vacancies_reacted") or 0),
                "reaction_rate": feedback_metrics.get("reaction_rate"),
                "positive_rate": feedback_metrics.get("positive_rate"),
                "applied_rate": feedback_metrics.get("applied_rate"),
            },
        )

        with performance.span("strategic_predictions_total", parent_span_name="daily_run_total") as span:
            strategic = update_strategic_layer(store, persist=True)
            strategy_count = len(strategic.predictions)
            span.set_counts(found_count=strategy_count)

        run_notes = f"found={len(vacancies)} accepted={len(accepted)} source_failures={sum(1 for s in source_statuses.values() if s.get('status') not in {'ok', 'empty', 'skipped'})} strategic_predictions={strategy_count}"
        run_metadata.update(
            {
                "source_statuses": source_statuses,
                "delivery": delivery_report.__dict__,
                "strategic_predictions": strategy_count,
                "source_notifications": source_notifications,
                "vacancy_message_deliveries": vacancy_message_deliveries,
                "performance_triggers": performance_reasons,
                "performance_block_included": bool(performance_block),
            }
        )
        if v3_shadow_enabled:
            run_metadata["v3_shadow"] = {
                "enabled": True,
                "counts": dict(v3_shadow_counts),
                "counts_v3_plus_needs_review": dict(v3_nr_shadow_counts),
                "active_pipeline_model": scoring_model_version,
            }
    except Exception as exc:
        run_status = "error"
        run_notes = f"error={exc}"
        run_metadata.setdefault("error", str(exc))
        raise
    finally:
        final_total_status = _daily_run_total_status(run_status, performance.spans())
        performance.record_completed(
            "cleanup_finalize",
            parent_span_name="daily_run_total",
            duration_ms=0,
            status=final_total_status,
        )
        performance.record_completed(
            "daily_run_total",
            duration_ms=int(round((perf_counter() - run_started_perf) * 1000)),
            status=final_total_status,
        )
        store.replace_performance_spans(run_id, [span.__dict__ for span in performance.spans()])
        store.finish_run(run_id, status=run_status, notes=run_notes or None, metadata=run_metadata or None)
    return digest





def run_weekly_kpi_report() -> str:
    """Compact weekly source quality report: last 7 days of daily runs, opportunity-ranked."""
    assert_runtime_contract()
    store = _store()
    store.bootstrap()
    run_id = store.start_run("weekly_kpi")
    cfg = load_config_bundle() or DEFAULT_CONFIG
    channel = _search_report_channel(cfg)

    with store.connect(read_only=True) as conn:
        db_rows = conn.execute("""
            SELECT
                skr.source,
                SUM(skr.found_count) as found,
                SUM(skr.executive_detected_count) as exec_detected,
                SUM(CASE WHEN vo.recommendation IN ('strong_fit','exceptional_fit') THEN 1 ELSE 0 END) as strong_fit,
                SUM(CASE WHEN vo.recommendation = 'potential_fit' THEN 1 ELSE 0 END) as potential_fit,
                SUM(CASE WHEN vo.recommendation = 'near_miss' THEN 1 ELSE 0 END) as near_miss,
                SUM(skr.accepted_count) as accepted,
                SUM(skr.notified_count) as notified,
                SUM(CASE WHEN skr.source_status = 'ok' THEN 1 ELSE 0 END) as runs_ok,
                COUNT(*) as runs_total,
                MAX(COALESCE(skr.enabled, 1)) as enabled,
                MAX(skr.source_status) as last_status
            FROM source_kpi_run skr
            JOIN runs r ON r.id = skr.run_id
            LEFT JOIN vacancy_observability vo
                ON vo.run_id = skr.run_id AND vo.source = skr.source AND vo.is_duplicate = 0
            WHERE r.mode = 'daily'
              AND r.run_type = 'production'
              AND r.started_at >= datetime('now', '-7 days')
            GROUP BY skr.source
            ORDER BY found DESC
        """).fetchall()

    cols = ["source", "found", "exec_detected", "strong_fit", "potential_fit", "near_miss",
            "accepted", "notified", "runs_ok", "runs_total", "enabled", "last_status"]
    data = [dict(zip(cols, row)) for row in db_rows]

    message = format_weekly_source_quality(data)

    # Delivery is best-effort. A delivery failure must not flip run.status to error.
    notification_id = store.create_notification(run_id, channel, "weekly_source_quality", message, notification_kind="weekly_source_quality", delivery_status="pending")
    delivery = _deliver_to_slack(message, channel)
    store.mark_notification_delivery(
        notification_id,
        _delivery_db_status(delivery),
        attempts=delivery.attempts,
        delivery_error=delivery.error,
    )

    store.finish_run(
        run_id,
        status="ok",
        notes=f"sources={len(data)}",
        metadata={
            "delivery": delivery.__dict__,
            "source_rows": len(data),
        },
    )
    return message


def run_performance_report(last: int = 7) -> str:
    assert_runtime_contract()
    store = _store()
    store.bootstrap()
    runs, spans_by_run = store.recent_daily_runs_with_spans(limit=max(1, int(last or 7)))
    return build_cli_performance_report(runs=runs, spans_by_run=spans_by_run)


def send_performance_report(run_id: int, *, force: bool = False) -> str:
    return _send_performance_report_for_run(int(run_id), force=bool(force))



def run_enrichment() -> str:
    assert_runtime_contract()
    store = _store()
    store.bootstrap()
    run_id = store.start_run("enrichment")
    cfg = load_config_bundle() or DEFAULT_CONFIG
    channel = _search_report_channel(cfg)
    memory = store.get_memory()
    questions = detect_high_value_questions(memory)
    digest = format_enrichment_questions(questions)
    if digest != "[SILENT]":
        notification_id = store.create_notification(run_id, channel, "enrichment_questions", digest, delivery_status="pending")
        delivery = _deliver_to_slack(digest, channel)
        store.mark_notification_delivery(notification_id, _delivery_db_status(delivery), attempts=delivery.attempts, delivery_error=delivery.error)
    store.finish_run(run_id, status="ok", notes=f"questions={len(questions)}", metadata={"questions": questions})
    return digest



def run_market_report() -> str:
    assert_runtime_contract()
    store = _store()
    store.bootstrap()
    run_id = store.start_run("market")
    monitor_target_companies(store)
    digest = build_market_report(store)
    cfg = load_config_bundle() or DEFAULT_CONFIG
    channel = _search_report_channel(cfg)
    technical_footer = _search_technical_report(store.source_adapter_status_from_latest_run(), channel=channel)
    message = digest if digest != "[SILENT]" else ""
    if technical_footer:
        message = f"{message}\n\n{technical_footer}".strip() if message else technical_footer
    if message and message != "[SILENT]":
        notification_id = store.create_notification(run_id, channel, "market_report", message, delivery_status="pending")
        delivery = _deliver_to_slack(message, channel)
        store.mark_notification_delivery(notification_id, _delivery_db_status(delivery), attempts=delivery.attempts, delivery_error=delivery.error)
        store.finish_run(run_id, status="ok", notes=f"report_length={len(message)}", metadata={"delivery": delivery.__dict__})
    else:
        store.finish_run(run_id, status="ok", notes="report=silent", metadata={"report": "silent"})
    return message or "[SILENT]"



def run_strategic_report() -> str:
    assert_runtime_contract()
    store = _store()
    store.bootstrap()
    run_id = store.start_run("strategic")
    digest = build_strategic_report(store)
    cfg = load_config_bundle() or DEFAULT_CONFIG
    channel = _search_report_channel(cfg)
    technical_footer = _search_technical_report(store.source_adapter_status_from_latest_run(), channel=channel)
    message = digest if digest != "[SILENT]" else ""
    if technical_footer:
        message = f"{message}\n\n{technical_footer}".strip() if message else technical_footer
    if message and message != "[SILENT]":
        notification_id = store.create_notification(run_id, channel, "strategic_report", message, delivery_status="pending")
        delivery = _deliver_to_slack(message, channel)
        store.mark_notification_delivery(notification_id, _delivery_db_status(delivery), attempts=delivery.attempts, delivery_error=delivery.error)
        store.finish_run(run_id, status="ok", notes=f"report_length={len(message)}", metadata={"delivery": delivery.__dict__})
    else:
        store.finish_run(run_id, status="ok", notes="report=silent", metadata={"report": "silent"})
    return message or "[SILENT]"



def run_alert_scan() -> str:
    assert_runtime_contract()
    store = _store()
    store.bootstrap()
    run_id = store.start_run("alert")
    cfg = load_config_bundle() or DEFAULT_CONFIG
    exceptional: list[tuple[Vacancy, Evaluation, int]] = []
    seen_keys: set[str] = set()
    rows = store.fetch_top_evaluations(min_score=cfg["scoring"]["thresholds"]["exceptional_fit"], limit=10)
    repost_window_days = cfg["deduplication"]["repost_detection"]["repost_window_days"]

    for row in rows:
        vacancy, evaluation, vacancy_id = _vacancy_evaluation_from_row(row)
        vacancy_key = canonical_vacancy_key(vacancy)
        if vacancy_key in seen_keys or evaluation.tier != "exceptional_fit":
            continue
        if not _should_notify_vacancy(store, vacancy_id, vacancy, evaluation, repost_window_days):
            store.set_vacancy_status(vacancy_id, "notified")
            continue
        exceptional.append((vacancy, evaluation, vacancy_id))
        seen_keys.add(vacancy_key)

    exceptional.sort(key=lambda item: item[1].score, reverse=True)
    digest_items = exceptional[:3]
    if not digest_items and not delivery_report.success:
        store.finish_run(run_id, status="ok", notes="exceptional=0", metadata={"alert_mode": "persisted_inventory", "delivery": {"success": True, "attempts": 0}})
        return "[SILENT]"

    digest = format_daily_digest(
        [(vacancy, evaluation) for vacancy, evaluation, _ in digest_items],
        title="Exceptional executive job alert",
        operator_footer="Operator note: alert scan uses persisted inventory; source acquisition runs on the twice-daily daily job.",
    )
    notification_ids = _prepare_notifications(store, run_id, cfg["runtime"]["slack"]["alerts_channel"], "alert", digest_items)
    delivery = _deliver_to_slack(digest, cfg["runtime"]["slack"]["alerts_channel"])
    _finalize_notifications(store, notification_ids, delivery)
    for vacancy, evaluation, vacancy_id in digest_items:
        if delivery.success:
            store.set_vacancy_status(vacancy_id, "notified")
        else:
            store.set_vacancy_status(vacancy_id, "active")
    store.finish_run(run_id, status="ok", notes=f"exceptional={len(exceptional)}", metadata={"alert_mode": "persisted_inventory", "delivery": delivery.__dict__})
    return digest



def _recent_runs_by_mode(store: JobIntelStore, mode: str, *, limit: int = 2) -> list[dict[str, Any]]:
    with store.connect() as conn:
        rows = conn.execute("SELECT * FROM runs WHERE mode = ? ORDER BY id DESC LIMIT ?", (mode, limit)).fetchall()
    return [dict(row) for row in rows]



def _run_evaluations_for_summary(store: JobIntelStore, run_id: int) -> list[dict[str, Any]]:
    with store.connect() as conn:
        rows = conn.execute(
            """
            SELECT
                e.id AS evaluation_id,
                e.vacancy_key,
                v.id AS vacancy_id,
                v.source,
                v.source_id,
                v.company,
                v.title,
                v.location,
                v.url,
                v.description,
                v.posted_at,
                v.scraped_at,
                v.salary,
                v.company_url,
                v.metadata_json,
                v.status,
                v.repost_count,
                e.score,
                e.tier,
                e.recommendation,
                e.salary_tier,
                e.matched_signals_json,
                e.concerns_json,
                e.reasons_json,
                e.raw_breakdown_json
            FROM vacancy_evaluations e
            JOIN vacancies v ON v.vacancy_key = e.vacancy_key
            WHERE e.run_id = ?
            ORDER BY e.score DESC, v.last_seen_at DESC, e.id DESC
            """,
            (run_id,),
        ).fetchall()
    return [dict(row) for row in rows]



def _json_loads(value: Any, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default



def _looks_like_allowed_geo(location: str) -> bool:
    text = (location or "").lower().strip()
    if not text or text == "unknown":
        return False
    if "remote" in text:
        return True

    # Tier 1: Remote, Europe, GCC, APAC + explicit countries.
    tier1_tokens = (
        "europe",
        "gcc",
        "apac",
        "singapore",
        "uae",
        "united arab emirates",
        "saudi",
        "saudi arabia",
        "uk",
        "united kingdom",
        "germany",
        "netherlands",
        "indonesia",
        "malaysia",
        "thailand",
        "dubai",
        "abu dhabi",
    )

    # Tier 2
    tier2_tokens = (
        "kazakhstan",
        "kz",
        "almaty",
        "astana",
        "poland",
        "australia",
        "sydney",
        "melbourne",
    )

    blocked_tokens = (
        "russia",
        "belarus",
        "iran",
        "north korea",
        "syria",
        "crimea",
        "donetsk",
        "luhansk",
    )

    if any(tok in text for tok in blocked_tokens):
        return False

    if any(tok in text for tok in tier1_tokens):
        return True
    if any(tok in text for tok in tier2_tokens):
        return True

    return False



def _role_bucket(title: str) -> str | None:
    text = (title or "").lower()
    if re.search(r"\b(vp|vice president)\b", text):
        return "vp_roles"
    if "chief product officer" in text or re.search(r"\bcpo\b", text):
        return "cpo_roles"
    if "director" in text:
        return "director_roles"
    if "head of" in text or text.startswith("head "):
        return "head_roles"
    if any(token in text for token in ("growth", "monetization", "strategy", "platform", "ecosystem", "digital products")):
        return "growth_strategy_roles"
    if "product manager" in text or "manager" in text or re.search(r"\bpm\b", text):
        return "manager_roles" if "manager" in text else "generic_pm_roles"
    return None



def _industry_buckets(text: str) -> list[str]:
    lowered = (text or "").lower()
    buckets: list[str] = []
    for bucket, tokens in {
        "fintech_ratio": ("fintech", "payments", "wallet", "bank", "lending", "credit"),
        "telecom_ratio": ("telecom", "telco", "carrier", "network operator"),
        "saas_ratio": ("saas", "subscription", "software as a service"),
        "ai_ratio": ("ai", "artificial intelligence", "machine learning", "llm", "ml"),
        "ecosystem_ratio": ("ecosystem", "platform", "marketplace", "super app"),
    }.items():
        if any(token in lowered for token in tokens):
            buckets.append(bucket)
    return buckets



def _health_summary_for_run(store: JobIntelStore, run: dict[str, Any]) -> dict[str, Any]:
    metadata = _json_loads(run.get("metadata_json"), {})
    source_statuses = metadata.get("source_statuses") or {}
    rows = _run_evaluations_for_summary(store, int(run["id"]))

    per_source: dict[str, dict[str, Any]] = {}
    overall_roles = Counter()
    overall_industries = Counter()
    accepted_rows: list[dict[str, Any]] = []
    sample_rows_by_source: dict[str, list[dict[str, Any]]] = {"linkedin": [], "headhunter": []}
    normalization_failures = 0
    blocked_geo_hits = 0
    allowed_geo_hits = 0
    generic_remote_roles_rejected = 0

    for row in rows:
        source = str(row.get("source") or "unknown")
        stats = per_source.setdefault(
            source,
            {
                "source": source,
                "vacancies_found": 0,
                "vacancies_normalized": 0,
                "normalization_failures": 0,
                "duplicate_count": 0,
                "new_unique_vacancies": 0,
                "reposts_detected": 0,
                "detail_pages_opened": 0,
                "executive_roles_detected": 0,
                "accepted_count": 0,
                "rejected_count": 0,
                "source_status": "unknown",
                "session_health": {},
                "metrics": {},
                "accepted_roles_by_source": 0,
            },
        )
        metadata = _json_loads(row.get("metadata_json"), {})
        vacancy = Vacancy(
            source=source,
            source_id=str(row.get("source_id") or ""),
            company=str(row.get("company") or "Unknown"),
            title=str(row.get("title") or "Vacancy"),
            location=str(row.get("location") or "Unknown"),
            url=str(row.get("url") or ""),
            description=str(row.get("description") or ""),
            posted_at=row.get("posted_at"),
            scraped_at=row.get("scraped_at"),
            salary=row.get("salary"),
            company_url=row.get("company_url"),
            metadata=metadata if isinstance(metadata, dict) else {},
        )
        classification = classify_vacancy(vacancy)
        stats["vacancies_found"] += 1
        stats["vacancies_normalized"] += 1
        stats["accepted_roles_by_source"] += 1 if row.get("recommendation") in {"strong_fit", "potential_fit"} else 0
        stats["executive_roles_detected"] += 1 if classification["executive_detected"] else 0
        overall_roles[str(classification["classification"] or "other")] += 1
        if row.get("status") == "duplicate":
            stats["duplicate_count"] += 1
            stats["reposts_detected"] += 1
        else:
            stats["new_unique_vacancies"] += 1
        # Industry/geography stats should describe the market, not only accepted rows.
        # Count them for all non-duplicate vacancies.
        if row.get("status") != "duplicate":
            for bucket in _industry_buckets(f"{row.get('title') or ''} {row.get('description') or ''} {row.get('company') or ''}"):
                overall_industries[bucket] += 1
            if _looks_like_allowed_geo(str(row.get("location") or "")):
                allowed_geo_hits += 1
            else:
                blocked_geo_hits += 1

        if row.get("recommendation") in {"reject", "near_miss"}:
            stats["rejected_count"] += 1
            title_text = f"{row.get('title') or ''} {row.get('location') or ''} {row.get('description') or ''}"
            if any(token in title_text.lower() for token in ("remote", "product manager", "project manager", "pm ")):
                generic_remote_roles_rejected += 1
        else:
            stats["accepted_count"] += 1
            accepted_rows.append(row)
        if source in sample_rows_by_source and len(sample_rows_by_source[source]) < 20:
            sample_rows_by_source[source].append(
                {
                    "raw_title": classification["raw_title"],
                    "normalized_title": classification["normalized_title"],
                    "classification": classification["classification"],
                    "executive_detected": classification["executive_detected"],
                    "company": row.get("company"),
                    "location": row.get("location"),
                }
            )

    for source, status in sorted(source_statuses.items()):
        stats = per_source.setdefault(
            source,
            {
                "source": source,
                "vacancies_found": 0,
                "vacancies_normalized": 0,
                "normalization_failures": 0,
                "duplicate_count": 0,
                "new_unique_vacancies": 0,
                "reposts_detected": 0,
                "detail_pages_opened": 0,
                "executive_roles_detected": 0,
                "accepted_count": 0,
                "rejected_count": 0,
                "source_status": "unknown",
                "session_health": {},
                "metrics": {},
                "accepted_roles_by_source": 0,
            },
        )
        stats["source_status"] = str(status.get("status") or "unknown")
        session = status.get("session_health") or {}
        metrics = status.get("metrics") or {}
        stats["session_health"] = session
        stats["metrics"] = metrics
        stats["detail_pages_opened"] = int(session.get("detail_pages_opened") or stats["detail_pages_opened"])
        stats["normalization_failures"] = int(status.get("normalization_failures") or 0)
        stats["vacancies_found"] = int(stats["vacancies_found"])
        stats["vacancies_normalized"] = int(stats["vacancies_normalized"])
        stats["duplicate_rate"] = round(stats["duplicate_count"] / stats["vacancies_found"], 3) if stats["vacancies_found"] else 0.0
        stats["extraction_success_rate"] = round((session.get("successful_extractions") or 0) / (session.get("pages_fetched") or 1), 3) if session else 0.0

    for stats in per_source.values():
        stats.setdefault("duplicate_rate", round(stats["duplicate_count"] / stats["vacancies_found"], 3) if stats["vacancies_found"] else 0.0)
        stats.setdefault("extraction_success_rate", 0.0)
        stats.setdefault("pages_fetched", 0)
        stats.setdefault("successful_extractions", 0)
        stats.setdefault("failed_extractions", 0)
        stats.setdefault("pagination_depth_reached", 0)
        stats.setdefault("avg_page_load_time", 0.0)
        stats.setdefault("anti_bot_events", 0)
        stats.setdefault("login_walls", 0)
        stats.setdefault("auth_redirects", 0)
        stats.setdefault("session_status", stats.get("source_status", "unknown"))
        stats.setdefault("session_age_hours", 0.0)
        stats.setdefault("last_successful_authenticated_request", None)
        stats.setdefault("login_wall_frequency", 0.0)

    total_found = sum(stats["vacancies_found"] for stats in per_source.values())
    total_accepted = sum(stats["accepted_count"] for stats in per_source.values())
    total_rejected = sum(stats["rejected_count"] for stats in per_source.values())
    total_duplicates = sum(stats["duplicate_count"] for stats in per_source.values())
    total_exec = sum(stats["executive_roles_detected"] for stats in per_source.values())
    source_quality_scores: list[tuple[float, int]] = []
    source_reliability_scores: list[tuple[float, int]] = []
    normalization_scores: list[tuple[float, int]] = []
    for source, stats in per_source.items():
        session = stats["session_health"] or {}
        metrics = stats["metrics"] or {}
        found = stats["vacancies_found"]
        accepted = stats["accepted_count"]
        rejected = stats["rejected_count"]
        exec_matches = stats["executive_roles_detected"]
        extraction_successes = int(session.get("successful_extractions") or found or 0)
        extraction_attempts = int(session.get("pages_fetched") or max(found, 1))
        anti_bot_failures = int(session.get("anti_bot_events") or (int(session.get("login_walls") or 0) + int(session.get("auth_redirects") or 0)))
        if metrics:
            quality = float(metrics.get("acquisition_quality_score") or 0.0)
            reliability = float(metrics.get("source_reliability") or 0.0)
            norm_quality = float(metrics.get("normalization_quality") or 0.0)
        else:
            computed = metrics_from_counts(
                source=source,
                found=found,
                executive_matches=exec_matches,
                accepted=accepted,
                rejected=rejected,
                extraction_successes=extraction_successes,
                extraction_attempts=extraction_attempts,
                anti_bot_failures=anti_bot_failures,
                normalization_quality=1.0,
                detail_pages_opened=int(session.get("detail_pages_opened") or 0),
                target_company_hits=int((source_statuses.get("target_companies") or {}).get("hits") or 0),
            )
            quality = computed.acquisition_quality_score
            reliability = computed.source_reliability
            norm_quality = computed.normalization_quality
            stats["metrics"] = computed.__dict__
        source_quality_scores.append((quality, max(found, 1)))
        source_reliability_scores.append((reliability, max(found, 1)))
        normalization_scores.append((norm_quality, max(found, 1)))
        session_status = str(session.get("status") or stats["source_status"] or "unknown")
        stats["session_status"] = session_status
        stats["session_age_hours"] = float(session.get("session_age_hours") or 0.0)
        stats["last_successful_authenticated_request"] = session.get("last_successful_authenticated_request") or session.get("last_url")
        stats["login_wall_frequency"] = round((int(session.get("login_walls") or 0) / (int(session.get("pages_fetched") or 1))), 3) if session else 0.0
        stats["auth_redirects"] = int(session.get("auth_redirects") or 0)
        stats["login_walls"] = int(session.get("login_walls") or 0)
        stats["pages_fetched"] = int(session.get("pages_fetched") or 0)
        stats["successful_extractions"] = int(session.get("successful_extractions") or 0)
        stats["failed_extractions"] = int(session.get("failed_extractions") or 0)
        stats["extraction_success_rate"] = round(stats["successful_extractions"] / stats["pages_fetched"], 3) if stats["pages_fetched"] else 0.0
        stats["pagination_depth_reached"] = int(session.get("pagination_depth_reached") or stats["pages_fetched"] or 0)
        stats["avg_page_load_time"] = round(float(session.get("avg_page_load_time_seconds") or 0.0), 3)
        stats["anti_bot_events"] = int(session.get("anti_bot_events") or 0)

    overall_quality = round(sum(score * weight for score, weight in source_quality_scores) / sum(weight for _, weight in source_quality_scores), 4) if source_quality_scores else 0.0
    overall_reliability = round(sum(score * weight for score, weight in source_reliability_scores) / sum(weight for _, weight in source_reliability_scores), 4) if source_reliability_scores else 0.0
    accepted_ratio = round(total_accepted / total_found, 3) if total_found else 0.0
    duplicate_rate = round(total_duplicates / total_found, 3) if total_found else 0.0
    normalization_quality = round(sum(score * weight for score, weight in normalization_scores) / sum(weight for _, weight in normalization_scores), 4) if normalization_scores else 0.0
    executive_density = round(total_exec / total_found, 3) if total_found else 0.0
    signal_noise_ratio = round(total_accepted / total_rejected, 3) if total_rejected else float(total_accepted) if total_accepted else 0.0
    target_company_hits = int((source_statuses.get("target_companies") or {}).get("hits") or 0)
    delivery_window_start = run.get("started_at") or ""
    with store.connect() as conn:
        notification_rows = conn.execute(
            "SELECT message_type, delivery_status, COUNT(*) AS count FROM notifications WHERE sent_at >= ? GROUP BY message_type, delivery_status",
            (delivery_window_start,),
        ).fetchall()
        digest_rows = conn.execute(
            "SELECT COUNT(*) FROM notifications WHERE sent_at >= ? AND message_type IN ('daily_summary','daily_digest') AND delivery_status = 'sent'",
            (delivery_window_start,),
        ).fetchone()
        alert_rows = conn.execute(
            "SELECT COUNT(*) FROM notifications WHERE sent_at >= ? AND message_type = 'alert' AND delivery_status = 'sent'",
            (delivery_window_start,),
        ).fetchone()
    delivery_counts = {"alerts_sent": int(alert_rows[0] or 0), "digests_sent": int(digest_rows[0] or 0), "delivery_failures": 0, "digest_empty_days": 0}
    for row in notification_rows:
        if row["delivery_status"] == "failed":
            delivery_counts["delivery_failures"] += int(row["count"] or 0)
    delivery_counts["digest_empty_days"] = 1 if delivery_counts["digests_sent"] == 0 else 0

    top_accepted = [
        {
            "company": row.get("company"),
            "title": row.get("title"),
            "location": row.get("location"),
            "source": row.get("source"),
            "score": int(row.get("score") or 0),
            "tier": row.get("tier"),
            "recommendation": row.get("recommendation"),
        }
        for row in rows
        if row.get("recommendation") != "reject"
    ][:5]

    return {
        "run": run,
        "rows": rows,
        "source_statuses": source_statuses,
        "per_source": per_source,
        "overall": {
            "vacancies_found": total_found,
            "vacancies_normalized": total_found,
            "normalization_failures": normalization_failures,
            "duplicate_rate": duplicate_rate,
            "new_unique_vacancies": total_found - total_duplicates,
            "reposts_detected": total_duplicates,
            "detail_pages_opened": sum(stats["detail_pages_opened"] for stats in per_source.values()),
            "executive_roles_detected": total_exec,
            "executive_density": executive_density,
            "signal_noise_ratio": signal_noise_ratio,
            "normalization_quality": normalization_quality,
            "acquisition_quality_score": overall_quality,
            "source_reliability": overall_reliability,
            "accepted_ratio": accepted_ratio,
            "exceptional_fit_count": sum(1 for row in rows if row.get("tier") == "exceptional_fit"),
            "strong_fit_count": sum(1 for row in rows if row.get("tier") == "strong_fit"),
            "possible_fit_count": sum(1 for row in rows if row.get("tier") == "possible_fit"),
            "reject_count": total_rejected,
            "generic_remote_roles_rejected": generic_remote_roles_rejected,
            "target_company_hits": target_company_hits,
            "allowed_geography_ratio": round(allowed_geo_hits / (allowed_geo_hits + blocked_geo_hits), 3) if (allowed_geo_hits + blocked_geo_hits) else 0.0,
            "blocked_geography_hits": blocked_geo_hits,
        },
        "roles": {
            "chief_product_officer": overall_roles.get("chief_product_officer", 0),
            "vp_product": overall_roles.get("vp_product", 0),
            "director_product": overall_roles.get("director_product", 0),
            "head_product": overall_roles.get("head_product", 0),
            "product_lead": overall_roles.get("product_lead", 0),
            "consumer_product_lead": overall_roles.get("consumer_product_lead", 0),
            "growth_product_lead": overall_roles.get("growth_product_lead", 0),
            "monetization_product_lead": overall_roles.get("monetization_product_lead", 0),
            "platform_ecosystem_product_lead": overall_roles.get("platform_ecosystem_product_lead", 0),
            "product_strategy_lead": overall_roles.get("product_strategy_lead", 0),
            "product_strategy_and_growth": overall_roles.get("product_strategy_and_growth", 0),
            "executive_product_leadership": overall_roles.get("executive_product_leadership", 0),
            "generic_pm_roles": overall_roles.get("generic_pm_roles", 0),
            "other": overall_roles.get("other", 0),
        },
        "samples": sample_rows_by_source,
        "industries": {
            "fintech_ratio": round(overall_industries.get("fintech_ratio", 0) / total_found, 3) if total_found else 0.0,
            "telecom_ratio": round(overall_industries.get("telecom_ratio", 0) / total_found, 3) if total_found else 0.0,
            "saas_ratio": round(overall_industries.get("saas_ratio", 0) / total_found, 3) if total_found else 0.0,
            "ai_ratio": round(overall_industries.get("ai_ratio", 0) / total_found, 3) if total_found else 0.0,
            "ecosystem_ratio": round(overall_industries.get("ecosystem_ratio", 0) / total_found, 3) if total_found else 0.0,
        },
        "delivery": delivery_counts,
        "top_accepted": top_accepted,
    }



def _runtime_provenance_summary(run: dict[str, Any]) -> dict[str, Any] | None:
    provenance = _json_loads(run.get("provenance_json"), {})
    if not provenance:
        return None
    browser_profiles = provenance.get("browser_profile_paths") or {}
    mirror_paths = provenance.get("runtime_mirror_paths") or {}
    contract = provenance.get("runtime_contract") or {}
    return {
        "whoami": provenance.get("whoami"),
        "hostname": provenance.get("hostname"),
        "pwd": provenance.get("pwd"),
        "effective_workdir": provenance.get("effective_workdir"),
        "git_commit_hash": provenance.get("git_commit_hash"),
        "python_executable": provenance.get("python_executable"),
        "db_path": provenance.get("db_path"),
        "state_dir": provenance.get("state_dir"),
        "browser_profile_dir": provenance.get("browser_profile_dir"),
        "browser_profile_paths": browser_profiles,
        "browser_python": contract.get("browser_python"),
        "expected_git_commit": contract.get("expected_git_commit"),
        "actual_git_commit": contract.get("actual_git_commit"),
        "runtime_contract_issues": list(contract.get("issues") or []),
        "runtime_mirror_paths": mirror_paths,
        "env_overrides_count": len(provenance.get("env_overrides") or {}),
        "imported_modules": sorted((provenance.get("imported_module_locations") or {}).keys()),
    }



def _format_delta(curr: float | int | None, prev: float | int | None) -> str:
    if curr is None or prev is None:
        return "n/a"
    diff = curr - prev
    if isinstance(diff, float):
        return f"{diff:+.3f}".rstrip("0").rstrip(".")
    return f"{diff:+d}"



def _format_health_report(curr: dict[str, Any], prev: dict[str, Any] | None) -> str:
    run = curr["run"]
    previous_run = (prev or {}).get("run") or {}
    lines = ["*Nightly Executive Intelligence Health Report*", ""]
    lines.append(f"Latest daily run: #{run.get('id')} {run.get('started_at')} ({run.get('status')})")
    if previous_run:
        lines.append(f"Previous daily run: #{previous_run.get('id')} {previous_run.get('started_at')} ({previous_run.get('status')})")
    latest_started = parse_iso_datetime(run.get("started_at"))
    if latest_started:
        age = datetime.now(timezone.utc) - latest_started
        lines.append(f"Latest daily run age: {age.days}d {age.seconds // 3600:02d}h {(age.seconds % 3600) // 60:02d}m")
    provenance_summary = _runtime_provenance_summary(run)
    if provenance_summary:
        browser_profiles = provenance_summary.get("browser_profile_paths") or {}
        mirror_paths = provenance_summary.get("runtime_mirror_paths") or {}
        lines.append(
            "Runtime provenance: "
            f"whoami={provenance_summary.get('whoami')} | hostname={provenance_summary.get('hostname')} | pwd={provenance_summary.get('pwd')} | "
            f"effective_workdir={provenance_summary.get('effective_workdir')} | git={provenance_summary.get('git_commit_hash')} | "
            f"expected_git={provenance_summary.get('expected_git_commit') or 'n/a'} | actual_git={provenance_summary.get('actual_git_commit') or 'n/a'} | "
            f"python={provenance_summary.get('python_executable')} | browser_python={provenance_summary.get('browser_python') or 'n/a'} | db={provenance_summary.get('db_path')} | state_dir={provenance_summary.get('state_dir')} | "
            f"browser_profile_dir={provenance_summary.get('browser_profile_dir') or 'n/a'} | browser_profiles={', '.join(f'{key}:{value}' for key, value in sorted(browser_profiles.items())) or 'n/a'} | "
            f"mirror_scripts={mirror_paths.get('resolved_scripts_dir') or 'n/a'} | env_overrides={provenance_summary.get('env_overrides_count', 0)} | "
            f"imported_modules={', '.join(provenance_summary.get('imported_modules') or []) or 'n/a'} | contract_issues={len(provenance_summary.get('runtime_contract_issues') or [])}"
        )
    lines.append("")

    lines.append("*Source health summary*")
    for source, stats in sorted(curr["per_source"].items(), key=lambda item: (item[1]["vacancies_found"], item[0]), reverse=True):
        lines.append(
            f"- {source}: source_status={stats['source_status']}, pages_fetched={stats['pages_fetched']}, successful_extractions={stats['successful_extractions']}, failed_extractions={stats['failed_extractions']}, extraction_success_rate={stats['extraction_success_rate']}, pagination_depth_reached={stats['pagination_depth_reached']}, avg_page_load_time={stats['avg_page_load_time']}s, anti_bot_events={stats['anti_bot_events']}, login_wall_hits={stats['login_walls']}, auth_redirects={stats['auth_redirects']}"
        )
    lines.append("")

    lines.append("*Session/auth health summary*")
    for source, stats in sorted(curr["per_source"].items(), key=lambda item: item[0]):
        if not stats.get("session_health"):
            continue
        lines.append(
            f"- {source}: session_status={stats['session_status']}, session_age_hours={stats['session_age_hours']}, last_successful_authenticated_request={stats['last_successful_authenticated_request'] or 'n/a'}, login_wall_frequency={stats['login_wall_frequency']}"
        )
    lines.append("")

    lines.append("*Acquisition summary*")
    for source, stats in sorted(curr["per_source"].items(), key=lambda item: (item[1]["vacancies_found"], item[0]), reverse=True):
        lines.append(
            f"- {source}: vacancies_found={stats['vacancies_found']}, vacancies_normalized={stats['vacancies_normalized']}, normalization_failures={stats['normalization_failures']}, duplicate_rate={stats['duplicate_rate']}, new_unique_vacancies={stats['new_unique_vacancies']}, reposts_detected={stats['reposts_detected']}, detail_pages_opened={stats['detail_pages_opened']}, executive_roles_detected={stats['executive_roles_detected']}"
        )
    lines.append("")

    lines.append("*Signal quality summary*")
    lines.append(
        f"- executive_density={curr['overall']['executive_density']}, signal_noise_ratio={curr['overall']['signal_noise_ratio']}, normalization_quality={curr['overall']['normalization_quality']}, acquisition_quality_score={curr['overall']['acquisition_quality_score']}"
    )
    lines.append(
        f"- roles: chief_product_officer={curr['roles']['chief_product_officer']}, vp_product={curr['roles']['vp_product']}, director_product={curr['roles']['director_product']}, head_product={curr['roles']['head_product']}, product_lead={curr['roles']['product_lead']}, consumer_product_lead={curr['roles']['consumer_product_lead']}, growth_product_lead={curr['roles']['growth_product_lead']}, monetization_product_lead={curr['roles']['monetization_product_lead']}, platform_ecosystem_product_lead={curr['roles']['platform_ecosystem_product_lead']}, product_strategy_lead={curr['roles']['product_strategy_lead']}, product_strategy_and_growth={curr['roles']['product_strategy_and_growth']}, executive_product_leadership={curr['roles']['executive_product_leadership']}, generic_pm_roles={curr['roles']['generic_pm_roles']}, other={curr['roles']['other']}"
    )
    lines.append(
        f"- industry: fintech_ratio={curr['industries']['fintech_ratio']}, telecom_ratio={curr['industries']['telecom_ratio']}, saas_ratio={curr['industries']['saas_ratio']}, ai_ratio={curr['industries']['ai_ratio']}, ecosystem_ratio={curr['industries']['ecosystem_ratio']}"
    )
    lines.append(
        f"- geography: allowed_geography_ratio={curr['overall']['allowed_geography_ratio']}, blocked_geography_hits={curr['overall']['blocked_geography_hits']}"
    )
    lines.append("")

    lines.append("*Normalized vacancy samples*")
    for source in ("linkedin", "headhunter"):
        source_samples = (curr.get("samples") or {}).get(source) or []
        lines.append(f"- {source}: {len(source_samples)} sample(s)")
        for idx, sample in enumerate(source_samples[:20], start=1):
            lines.append(
                f"  {idx}. raw={sample.get('raw_title')} | normalized={sample.get('normalized_title')} | class={sample.get('classification')} | exec={sample.get('executive_detected')} | company={sample.get('company')} | location={sample.get('location')}"
            )
    lines.append("")

    lines.append("*Pipeline metrics*")
    lines.append(
        f"- exceptional_fit_count={curr['overall']['exceptional_fit_count']}, strong_fit_count={curr['overall']['strong_fit_count']}, possible_fit_count={curr['overall']['possible_fit_count']}, reject_count={curr['overall']['reject_count']}, accepted_ratio={curr['overall']['accepted_ratio']}, generic_remote_roles_rejected={curr['overall']['generic_remote_roles_rejected']}, target_company_hits={curr['overall']['target_company_hits']}"
    )
    accepted_by_source = ", ".join(f"{source}:{stats['accepted_count']}" for source, stats in sorted(curr['per_source'].items()) if stats["accepted_count"])
    lines.append(f"- accepted_roles_by_source={accepted_by_source or 'none'}")
    lines.append("")

    lines.append("*Delivery metrics*")
    lines.append(
        f"- alerts_sent={curr['delivery']['alerts_sent']}, digests_sent={curr['delivery']['digests_sent']}, delivery_failures={curr['delivery']['delivery_failures']}, digest_empty_days={curr['delivery']['digest_empty_days']}"
    )
    lines.append("")

    lines.append("*Top accepted opportunities*")
    if curr["top_accepted"]:
        for idx, row in enumerate(curr["top_accepted"], 1):
            lines.append(
                f"{idx}. {row['company']} — {row['title']} | {row['location']} | source={row['source']} | score={row['score']} | tier={row['tier']}"
            )
    else:
        lines.append("- none in the latest daily run")
    lines.append("")

    warnings: list[str] = []
    for source, stats in curr["per_source"].items():
        if stats["source_status"] not in {"ok", "empty"}:
            warnings.append(f"{source}={stats['source_status']}")
        if stats["pages_fetched"] and stats["extraction_success_rate"] < 0.5:
            warnings.append(f"{source} extraction_success_rate={stats['extraction_success_rate']}")
    if curr["delivery"]["delivery_failures"]:
        warnings.append(f"delivery_failures={curr['delivery']['delivery_failures']}")
    if curr["delivery"]["digest_empty_days"]:
        warnings.append("digest_empty_days=1")
    lines.append("*Failures / warnings*")
    lines.append("- " + (", ".join(warnings) if warnings else "none detected"))
    lines.append("")

    if prev:
        prev_overall = prev["overall"]
        lines.append("*Metric deltas vs previous daily run*")
        for key in ["vacancies_found", "accepted_ratio", "duplicate_rate", "executive_density", "acquisition_quality_score", "target_company_hits"]:
            lines.append(f"- {key}: {curr['overall'][key]} (Δ {_format_delta(curr['overall'][key], prev_overall.get(key))})")
        for source in sorted(set(curr["per_source"]) & set(prev["per_source"])):
            curr_stats = curr["per_source"][source]
            prev_stats = prev["per_source"][source]
            lines.append(
                f"- {source}: status {prev_stats['source_status']} → {curr_stats['source_status']}, pages_fetched Δ {_format_delta(curr_stats['pages_fetched'], prev_stats['pages_fetched'])}, extraction_success_rate Δ {_format_delta(curr_stats['extraction_success_rate'], prev_stats['extraction_success_rate'])}, login_walls Δ {_format_delta(curr_stats['login_walls'], prev_stats['login_walls'])}"
            )
    else:
        lines.append("*Metric deltas vs previous daily run*\n- n/a")
    return "\n".join(lines).rstrip()



def _check_health_conditions(store: "JobIntelStore") -> list[str]:
    """Returns list of health problem descriptions. Empty list = healthy."""
    problems = []
    with store.connect(read_only=True) as conn:
        # 1. No successful daily run in last 26h
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=26)).isoformat()
        row = conn.execute(
            "SELECT started_at FROM runs WHERE mode='daily' AND status='ok' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if not row or row[0] < cutoff:
            last = row[0] if row else "never"
            problems.append(f"No successful daily run since {last}")

        # 2. Last daily run failed
        row = conn.execute(
            "SELECT id, status, notes FROM runs WHERE mode='daily' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if row and row[1] not in ("ok", "partial"):
            problems.append(f"Last daily run {row[0]} status={row[1]}: {row[2] or ''}")

        # 3. Tier-1 source failure in last daily run
        last_run_id = (conn.execute(
            "SELECT MAX(id) FROM runs WHERE mode='daily'"
        ).fetchone() or [None])[0]
        if last_run_id:
            tier1_failures = conn.execute(
                "SELECT source, source_status FROM source_kpi_run "
                "WHERE run_id=? AND source IN ('linkedin','headhunter','target_companies') "
                "AND source_status NOT IN ('ok','empty','skipped') "
                "AND COALESCE(enabled, 1) = 1",
                (last_run_id,)
            ).fetchall()
            for src, status in tier1_failures:
                problems.append(f"Tier-1 source {src}: {status}")

            # 4. Login wall on Tier-1
            login_walls = conn.execute(
                "SELECT source, login_walls FROM source_kpi_run "
                "WHERE run_id=? AND source IN ('linkedin','headhunter','target_companies') "
                "AND login_walls > 0",
                (last_run_id,)
            ).fetchall()
            enabled_sources_env_raw = os.getenv("JOB_INTEL_ENABLED_SOURCES", "").strip().lower()
            def _disabled_by_env(src: str) -> bool:
                return bool(enabled_sources_env_raw) and src.strip().lower() not in enabled_sources_env_raw

            for src, walls in login_walls:
                if _disabled_by_env(src):
                    continue
                problems.append(f"Login wall: {src} ({walls} events)")

            # 4b. LinkedIn session lost: login wall on every one of the last 3
            # daily runs means the browser profile needs manual re-auth.
            # Intentionally disabled (retired) linkedin must stay silent.
            linkedin_latest = conn.execute(
                "SELECT COALESCE(k.enabled, 1), k.skip_reason FROM source_kpi_run k "
                "JOIN runs r ON r.id = k.run_id "
                "WHERE k.source='linkedin' AND r.mode='daily' "
                "ORDER BY k.run_id DESC LIMIT 1",
            ).fetchone()
            linkedin_disabled = _disabled_by_env("linkedin") or bool(
                linkedin_latest
                and (int(linkedin_latest[0] or 0) == 0 or str(linkedin_latest[1] or "") == "disabled_by_config")
            )
            recent_walls = conn.execute(
                "SELECT k.login_walls FROM source_kpi_run k "
                "JOIN runs r ON r.id = k.run_id "
                "WHERE k.source='linkedin' AND r.mode='daily' "
                "ORDER BY k.run_id DESC LIMIT 3",
            ).fetchall()
            if not linkedin_disabled and len(recent_walls) >= 3 and all(int(r[0] or 0) > 0 for r in recent_walls):
                problems.append(
                    "LinkedIn re-auth required: login wall on 3+ consecutive daily runs "
                    "(browser profile session lost). Runbook: docs/runbooks/linkedin-reauth.md"
                )

            # 5. Daily digest delivery failure
            fail = conn.execute(
                "SELECT delivery_error FROM notifications "
                "WHERE run_id=? AND message_type IN ('daily_summary','daily_digest') AND delivery_status='failed' LIMIT 1",
                (last_run_id,)
            ).fetchone()
            if fail:
                problems.append(f"Daily digest delivery failed: {fail[0] or 'unknown error'}")

        # 6. ATS source empty for 5+ consecutive runs
        ats_sources = ["greenhouse", "lever", "ashby", "teamtailor", "smartrecruiters", "personio", "recruitee"]
        for src in ats_sources:
            recent = conn.execute(
                "SELECT source_status FROM source_kpi_run WHERE source=? ORDER BY run_id DESC LIMIT 5",
                (src,)
            ).fetchall()
            if len(recent) >= 5 and all(r[0] == "empty" for r in recent):
                problems.append(f"ATS source {src}: empty for 5+ consecutive runs")

    return problems


def run_health_report() -> str:
    runtime_contract = build_runtime_contract()
    store = _store()
    store.bootstrap()
    run_id = store.start_run("health")
    daily_runs = _recent_runs_by_mode(store, "daily", limit=2)
    if not daily_runs:
        store.finish_run(run_id, status="ok", notes="report=silent", metadata={"report": "silent"})
        return "[SILENT]"
    current = _health_summary_for_run(store, daily_runs[0])
    previous = _health_summary_for_run(store, daily_runs[1]) if len(daily_runs) > 1 else None
    if runtime_contract.get("issues"):
        current.setdefault("runtime", {})
        current["runtime"]["contract_issues"] = runtime_contract["issues"]
    problems = _check_health_conditions(store)
    send_ok = os.getenv("JOB_INTEL_SEND_HEALTH_OK_REPORTS", "0").strip() == "1"

    if not problems and not send_ok:
        logger.info("Health check passed — all systems healthy. Skipping Slack (set JOB_INTEL_SEND_HEALTH_OK_REPORTS=1 to send anyway).")
        store.finish_run(run_id, status="ok", notes="report=skipped_healthy", metadata={"report_type": "health", "skipped": True})
        return "[SILENT]"

    if problems:
        digest = format_health_warning(problems)
    else:
        digest = _format_health_report(current, previous)
    cfg = load_config_bundle() or DEFAULT_CONFIG
    channel = _search_report_channel(cfg)
    notification_id = store.create_notification(run_id, channel, "health_warning", digest, notification_kind="health_warning", delivery_status="pending")
    delivery = _deliver_to_slack(digest, channel)
    store.mark_notification_delivery(notification_id, _delivery_db_status(delivery), attempts=delivery.attempts, delivery_error=delivery.error)
    store.finish_run(run_id, status="ok", notes=f"report_length={len(digest)}", metadata={"report_type": "health", "latest_daily_run_id": daily_runs[0].get('id'), "previous_daily_run_id": daily_runs[1].get('id') if len(daily_runs) > 1 else None, "delivery": delivery.__dict__, "problems_found": len(problems)})
    return digest



def retire_stale_vacancies(days: int | None = None) -> dict[str, int]:
    cfg = load_config_bundle() or DEFAULT_CONFIG
    resolved_days = days if days is not None else int(os.getenv("JOB_INTEL_STALE_DAYS", cfg["runtime"].get("stale_after_days", 30)))
    store = _store()
    store.bootstrap()
    return store.retire_stale(days=resolved_days)



def _cron_script_paths() -> list[Path]:
    scripts_dir = resolve_scripts_dir()
    names = ["job_intel_daily.sh", "job_intel_alert.sh", "job_intel_enrichment.sh", "job_intel_browser_health.sh"]
    if scripts_dir and scripts_dir.exists():
        return [scripts_dir / name for name in names]
    return [runtime_home() / ".hermes" / "scripts" / name for name in names]



def _browser_desktop_base_dir() -> Path:
    browser_python = os.getenv("JOB_INTEL_BROWSER_PYTHON", "").strip()
    if browser_python:
        browser_python_path = Path(browser_python)
        try:
            if browser_python_path.name == "python" and browser_python_path.parent.name == "bin" and browser_python_path.parent.parent.name == "playwright-venv":
                return browser_python_path.parent.parent.parent
        except IndexError:
            pass
    return Path(os.getenv("BROWSER_DESKTOP_BASE_DIR", "/var/lib/browser-desktop"))



def _browser_desktop_health() -> dict[str, Any]:
    base_dir = _browser_desktop_base_dir()
    venv_python = Path(os.getenv("JOB_INTEL_BROWSER_PYTHON", "").strip() or (base_dir / "playwright-venv" / "bin" / "python"))
    scripts_dir = resolve_scripts_dir()
    helper_candidates = [
        (scripts_dir / "browser-desktop-ensure-playwright.sh") if scripts_dir else None,
        runtime_home() / ".hermes" / "scripts" / "browser-desktop-ensure-playwright.sh",
    ]
    helper_path = next((candidate for candidate in helper_candidates if candidate and candidate.exists()), helper_candidates[-1])

    result: dict[str, Any] = {
        "status": "healthy",
        "base_dir": str(base_dir),
        "helper_script": str(helper_path),
        "playwright_venv_python": str(venv_python),
        "chromium_executable": None,
        "issues": [],
        "checks": {},
    }

    def mark(name: str, ok: bool, detail: str | None = None) -> None:
        result["checks"][name] = {"ok": ok}
        if detail is not None:
            result["checks"][name]["detail"] = detail
        if not ok and detail:
            result["issues"].append(detail)
        elif not ok:
            result["issues"].append(name)

    mark("base_dir", base_dir.exists(), None if base_dir.exists() else f"{base_dir} missing")
    mark("helper_script", helper_path.exists() and os.access(helper_path, os.X_OK), None if helper_path.exists() and os.access(helper_path, os.X_OK) else f"{helper_path} missing or not executable")
    mark("playwright_venv_python", venv_python.exists() and os.access(venv_python, os.X_OK), None if venv_python.exists() and os.access(venv_python, os.X_OK) else f"{venv_python} missing or not executable")

    browser_env = os.environ.copy()
    browser_env.setdefault("HOME", str(base_dir))
    browser_env.setdefault("XDG_CACHE_HOME", str(base_dir / ".cache"))
    browser_env.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(Path(browser_env["XDG_CACHE_HOME"]) / "ms-playwright"))

    chromium_executable: str | None = None
    if result["checks"]["playwright_venv_python"]["ok"]:
        try:
            probe = subprocess.run(
                [str(venv_python), "-c", "from playwright.sync_api import sync_playwright\nwith sync_playwright() as p:\n    print(p.chromium.executable_path)"],
                capture_output=True,
                text=True,
                timeout=45,
                check=False,
                env=browser_env,
            )
            probe_output = (probe.stdout or "").strip()
            chromium_executable = probe_output or None
            if probe.returncode == 0 and chromium_executable:
                mark("playwright_import", True, chromium_executable)
            else:
                stderr = (probe.stderr or probe.stdout or "Playwright import probe failed").strip()
                mark("playwright_import", False, stderr)
        except subprocess.TimeoutExpired:
            mark("playwright_import", False, "Playwright import probe timed out after 45s")

    def worker_probe(source: str) -> tuple[bool, str]:
        try:
            probe = subprocess.run(
                [str(venv_python), "-m", "job_intel.browser_worker", "probe", source],
                capture_output=True,
                text=True,
                timeout=180,
                check=False,
                env=browser_env,
            )
        except subprocess.TimeoutExpired:
            return False, f"{source} CDP probe timed out after 180s"
        payload = None
        stdout = (probe.stdout or "").strip()
        for line in reversed(stdout.splitlines()):
            line = line.strip()
            if not (line.startswith("{") and line.endswith("}")):
                continue
            try:
                payload = json.loads(line)
                break
            except json.JSONDecodeError:
                continue
        if isinstance(payload, dict) and payload.get("ok"):
            return True, str(payload.get("session_health") or source)
        detail = payload.get("error") if isinstance(payload, dict) else None
        return False, str(detail or probe.stderr or stdout or f"{source} CDP probe failed")

    cdp_ports = {"linkedin": 9222, "headhunter": 9223}
    for source in ("linkedin", "headhunter"):
        ok, detail = worker_probe(source)
        mark(f"cdp_probe_{source}", ok, detail)
        port = cdp_ports[source]
        try:
            response = requests.get(f"http://127.0.0.1:{port}/json/version", timeout=5)
            response.raise_for_status()
            payload = response.json()
            browser = payload.get("Browser") or payload.get("browser") or f"127.0.0.1:{port}"
            mark(f"cdp_endpoint_{source}", True, str(browser))
        except Exception as exc:
            mark(f"cdp_endpoint_{source}", False, f"CDP endpoint {port} unavailable: {exc}")

    for profile_name in ("linkedin", "hh"):
        profile_dir = base_dir / "profiles" / profile_name
        profile_ok = profile_dir.exists() and profile_dir.is_dir()
        mark(
            f"profile_{profile_name}",
            profile_ok,
            None if profile_ok else f"{profile_dir} missing",
        )

    result["chromium_executable"] = chromium_executable
    result["status"] = "healthy" if not result["issues"] else "degraded"
    return result


def _format_browser_desktop_health(health: dict[str, Any]) -> str:
    lines = ["*Browser desktop health*", ""]
    lines.append(f"Status: {health['status']}")
    lines.append(f"Base dir: {health['base_dir']}")
    lines.append(f"Helper script: {health['helper_script']}")
    lines.append(f"Playwright venv: {health['playwright_venv_python']}")
    lines.append(f"Chromium executable: {health['chromium_executable'] or 'n/a'}")
    lines.append("Checks:")
    for name, check in health["checks"].items():
        detail = f" ({check['detail']})" if check.get("detail") else ""
        lines.append(f"- {name}: {'ok' if check['ok'] else 'fail'}{detail}")
    if health["issues"]:
        lines.append("Issues:")
        for issue in health["issues"]:
            lines.append(f"- {issue}")
    else:
        lines.append("Issues: none")
    return "\n".join(lines)



def _source_statuses_for_doctor(store: JobIntelStore) -> dict[str, dict[str, Any]]:
    try:
        latest = store.source_adapter_status_from_latest_run()
    except Exception as exc:
        return {"database": {"source": "database", "status": "error", "errors": [str(exc)]}}
    if latest:
        return latest
    if os.getenv("JOB_INTEL_DOCTOR_SKIP_LIVE_COLLECTION", "").strip().lower() in {"1", "true", "yes", "on"}:
        return {
            "diagnostics": {
                "source": "diagnostics",
                "status": "skipped",
                "errors": ["live source collection disabled for fast doctor mode"],
            }
        }
    try:
        return _collect_vacancies_compat(store).source_statuses
    except Exception as exc:
        return {"headhunter": {"source": "headhunter", "status": "error", "errors": [str(exc)]}}



def _collect_source_statuses(store: JobIntelStore) -> dict[str, dict[str, Any]]:
    return _source_statuses_for_doctor(store)



def doctor_report() -> str:
    contract = assert_runtime_contract()
    paths = {
        "Current user": runtime_user(),
        "Configured service user": contract.get("service_user"),
        "Home directory": str(runtime_home()),
        "Environment": resolve_environment_name(),
        "Workdir": str(resolve_workdir()),
        "DB path": str(resolve_db_path()),
        "State dir": contract.get("state_dir"),
        "Browser profile dir": contract.get("browser_profile_dir"),
        "Browser python": contract.get("browser_python"),
        "Expected git commit": contract.get("expected_git_commit"),
        "Actual git commit": contract.get("actual_git_commit"),
        "Slack delivery": "webhook" if _slack_webhook_enabled() else "disabled",
    }
    store = _store()
    bootstrap_error: str | None = None
    try:
        store.bootstrap()
    except Exception as exc:
        bootstrap_error = str(exc)
    db_flags = file_access_flags(resolve_db_path())
    workdir_flags = file_access_flags(resolve_workdir())
    source_statuses = _collect_source_statuses(store)
    try:
        latest = store.latest_run() or {}
    except Exception as exc:
        latest = {}
        bootstrap_error = (bootstrap_error + "; " if bootstrap_error else "") + f"latest_run_failed: {exc}"
    latest_statuses: dict[str, dict[str, Any]] = {}
    metadata_json = latest.get("metadata_json") if isinstance(latest, dict) else None
    if metadata_json:
        try:
            payload = json.loads(metadata_json)
        except json.JSONDecodeError:
            payload = {}
        if isinstance(payload, dict):
            latest_statuses = payload.get("source_statuses") or {}
    scripts = _cron_script_paths()

    lines = ["*Job-intel doctor*", ""]
    for label, value in paths.items():
        lines.append(f"{label}: {value}")

    def _ownership(path: Path) -> str:
        try:
            stat = path.stat()
        except FileNotFoundError:
            return "missing"
        return f"uid={stat.st_uid}, gid={stat.st_gid}, owned={'yes' if stat.st_uid == os.getuid() else 'no'}"

    lines.append(f"Runtime UID: {os.getuid()}")
    lines.append(f"DB ownership: {_ownership(resolve_db_path())}")
    lines.append(f"Workdir ownership: {_ownership(resolve_workdir())}")
    lines.append(f"DB readable: {'yes' if db_flags['readable'] else 'no'}")
    lines.append(f"DB writable: {'yes' if db_flags['writable'] else 'no'}")
    lines.append(f"Workdir readable: {'yes' if workdir_flags['readable'] else 'no'}")
    lines.append(f"Workdir writable: {'yes' if workdir_flags['writable'] else 'no'}")
    lines.append("Cron scripts:")
    for script in scripts:
        flags = file_access_flags(script)
        status = "present" if flags["exists"] else "missing"
        lines.append(
            f"- {script.name}: {status}, readable={'yes' if flags['readable'] else 'no'}, writable={'yes' if flags['writable'] else 'no'}"
        )
    lines.append("Browser desktop:")
    browser_health = _browser_desktop_health()
    lines.append(f"- status: {browser_health['status']}")
    lines.append(f"- helper_script: {browser_health['helper_script']}")
    lines.append(f"- playwright_venv: {browser_health['playwright_venv_python']}")
    lines.append(f"- chromium_executable: {browser_health['chromium_executable'] or 'n/a'}")
    if browser_health["issues"]:
        for issue in browser_health["issues"]:
            lines.append(f"- issue: {issue}")
    lines.append("Source adapters:")
    for name, status in sorted(source_statuses.items()):
        merged = dict(latest_statuses.get(name) or {})
        merged.update(status)
        status = merged
        extras = []
        if status.get("hits") is not None:
            extras.append(f"hits={status['hits']}")
        if status.get("errors"):
            extras.append(f"error={status['errors'][-1]}")
        metrics = status.get("metrics") or {}
        if metrics:
            extras.append(f"exec_fit={metrics.get('executive_fit_ratio', 'n/a')}")
            extras.append(f"reliability={metrics.get('source_reliability', 'n/a')}")
            extras.append(f"quality={metrics.get('status', 'n/a')}")
            extras.append(f"quality_score={metrics.get('acquisition_quality_score', 'n/a')}")
        if status.get("session_health"):
            health = status["session_health"]
            extras.append(f"session={health.get('status', 'n/a')}")
            extras.append(f"login_walls={health.get('login_walls', 0)}")
        extra_text = f" ({', '.join(extras)})" if extras else ""
        lines.append(f"- {name}: {status.get('status', 'unknown')}{extra_text}")
    lines.append(f"Last run: {latest.get('status', 'none')} ({latest.get('mode', 'n/a')})")
    if latest.get("notes"):
        lines.append(f"Last run notes: {latest['notes']}")
    return "\n".join(lines)



def send_test_message(channel: str) -> str:
    paths = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "runtime_user": runtime_user(),
        "db_path": str(resolve_db_path()),
        "environment": resolve_environment_name(),
    }
    message = "\n".join(
        [
            "*Job-intel test message*",
            f"Timestamp: {paths['timestamp']}",
            f"Runtime user: {paths['runtime_user']}",
            f"DB path: {paths['db_path']}",
            f"Environment: {paths['environment']}",
        ]
    )
    delivery = _deliver_to_slack(message, channel)
    success = delivery.success if hasattr(delivery, "success") else bool(delivery)
    error = getattr(delivery, "error", None)
    return "sent" if success else f"failed: {error or 'delivery failed'}"




def _map_feedback_reaction(raw_reaction: str) -> str | None:
    reaction = (raw_reaction or "").strip().lower()
    mapping = {
        "+1": "interesting",
        "thumbsup": "interesting",
        "thumbs_up": "interesting",
        "-1": "not_interesting",
        "thumbsdown": "not_interesting",
        "thumbs_down": "not_interesting",
        "star": "exceptional",
        "fire": "exceptional",
        "rocket": "applied",
        "eyes": "save_for_later",
        "bookmark": "save_for_later",
    }
    return mapping.get(reaction)


def run_feedback_event(payload: dict[str, Any]) -> str:
    event_type = str(payload.get("type") or "").strip()
    if event_type not in {"reaction_added", "reaction_removed"}:
        return json.dumps({"status": "ignored", "reason": "unsupported_event_type", "event_type": event_type})

    item = payload.get("item") or {}
    channel = str(item.get("channel") or payload.get("item_channel") or "").strip()
    message_ts = str(item.get("ts") or payload.get("item_ts") or "").strip()
    user_id = str(payload.get("user") or payload.get("user_id") or "unknown").strip() or "unknown"
    reaction = str(payload.get("reaction") or "").strip()
    feedback_type = _map_feedback_reaction(reaction)
    if not feedback_type:
        return json.dumps({"status": "ignored", "reason": "unsupported_reaction", "reaction": reaction, "event_type": event_type})
    if not channel or not message_ts:
        return json.dumps({"status": "ignored", "reason": "missing_message_reference", "event_type": event_type})

    event_timestamp = str(payload.get("event_ts") or payload.get("event_timestamp") or datetime.now(timezone.utc).isoformat())
    store = _store()
    message = store.find_vacancy_message(slack_channel=channel, slack_message_ts=message_ts)
    if not message:
        for alias in _logical_aliases_for_channel_id(channel):
            message = store.find_vacancy_message(slack_channel=alias, slack_message_ts=message_ts)
            if message:
                break
    if not message:
        return json.dumps({"status": "ignored", "reason": "message_not_tracked", "channel": channel, "slack_message_ts": message_ts})

    vacancy_id = int(message.get("vacancy_id"))
    store.record_vacancy_feedback_event(
        vacancy_id=vacancy_id,
        run_id=int(message.get("run_id")) if message.get("run_id") is not None else None,
        notification_id=int(message.get("notification_id")) if message.get("notification_id") is not None else None,
        vacancy_key=message.get("vacancy_key"),
        canonical_url=message.get("canonical_url"),
        card_key=message.get("card_key"),
        slack_channel=channel,
        slack_message_ts=message_ts,
        feedback_type=feedback_type,
        event_type=event_type,
        event_timestamp=event_timestamp,
        user_id=user_id,
        raw_event_json=payload,
    )

    feedback_prompt: dict[str, Any] | None = None
    if feedback_type == "not_interesting" and event_type == "reaction_added":
        try:
            service = _feedback_loop_service(store)
            feedback_prompt = service.handle_negative_reaction(
                slack_channel_id=channel,
                slack_message_ts=message_ts,
                user_id=user_id,
                reaction=reaction,
                vacancy_id=vacancy_id,
                raw_payload=payload,
            )
        except Exception as exc:
            logger.exception("negative feedback prompt failed: %s", exc)
            feedback_prompt = {"status": "error", "error": str(exc)}

    return json.dumps(
        {
            "status": "ok",
            "vacancy_id": vacancy_id,
            "feedback_type": feedback_type,
            "event_type": event_type,
            "channel": channel,
            "slack_message_ts": message_ts,
            "user_id": user_id,
            "feedback_prompt": feedback_prompt,
        },
        ensure_ascii=False,
    )


def _feedback_loop_service(store: JobIntelStore | None = None) -> "FeedbackLoopService":
    from job_intel.crm_service import CRMService
    from job_intel.feedback_service import FeedbackLoopService

    store = store or _store()

    def _deliver(message: str, channel: str, thread_ts: str | None) -> str | None:
        delivery = _deliver_to_slack(message, channel, prefer_gateway=True, thread_ts=thread_ts)
        if not delivery.success:
            logger.warning("feedback loop delivery failed: %s", delivery.error)
            return None
        return delivery.message_ts or thread_ts

    return FeedbackLoopService(store=store, crm=CRMService.from_store(store), deliver=_deliver)


def run_feedback_thread_reply(payload: dict[str, Any]) -> str:
    """Route a Slack thread message into the negative feedback loop.

    Called by the Slack adapter for ordinary messages that reply in a
    thread. Returns ``not_feedback_thread`` when the thread is not an open
    feedback prompt, so the caller can fall through to normal handling.
    """
    channel = str(payload.get("channel") or "").strip()
    thread_ts = str(payload.get("thread_ts") or "").strip()
    user_id = str(payload.get("user") or payload.get("user_id") or "unknown").strip() or "unknown"
    text = str(payload.get("text") or "")
    if not channel or not thread_ts:
        return json.dumps({"status": "not_feedback_thread"})
    try:
        service = _feedback_loop_service()
        result = service.handle_thread_reply(
            slack_channel_id=channel,
            slack_thread_ts=thread_ts,
            user_id=user_id,
            text=text,
        )
    except Exception as exc:
        logger.exception("feedback thread reply handling failed: %s", exc)
        return json.dumps({"status": "error", "error": str(exc)})
    result.pop("classification", None)
    return json.dumps(result, ensure_ascii=False, default=str)


def run_crm_reconcile(days: int, dry_run: bool, apply: bool, vacancy_id: int | None, limit: int | None) -> str:
    reconciler = CRMReconciler(_store())
    result = reconciler.run(days=days, dry_run=dry_run, apply=apply, vacancy_id=vacancy_id, limit=limit)
    return json.dumps(result, ensure_ascii=False, indent=2)

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="job-intel")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("bootstrap")
    sub.add_parser("daily")
    sub.add_parser("alert")
    sub.add_parser("enrichment")
    sub.add_parser("market")
    sub.add_parser("strategic")
    sub.add_parser("health")
    sub.add_parser("weekly-kpi")
    universe_parser = sub.add_parser("universe-discovery")
    universe_parser.add_argument("--no-deliver", action="store_true")
    performance_report = sub.add_parser("performance-report")
    performance_report.add_argument("--last", type=int, default=7)
    performance_report.set_defaults(cmd="performance-report")
    send_performance_report_parser = sub.add_parser("send-performance-report")
    send_performance_report_parser.add_argument("--run-id", type=int, required=True)
    send_performance_report_parser.add_argument("--force", action="store_true")
    send_performance_report_parser.set_defaults(cmd="send-performance-report")
    metrics_exporter = sub.add_parser("metrics-exporter")
    metrics_exporter.add_argument("--host", default="0.0.0.0")
    metrics_exporter.add_argument("--port", type=int, default=9899)
    metrics_exporter.set_defaults(cmd="metrics-exporter")
    doctor = sub.add_parser("doctor")
    doctor.set_defaults(cmd="doctor")

    browser_health = sub.add_parser("browser-health")
    browser_health.set_defaults(cmd="browser-health")

    send_test = sub.add_parser("send-test")
    send_test.add_argument("--channel", required=True)
    send_test.set_defaults(cmd="send-test")

    feedback_event = sub.add_parser("feedback-event")
    feedback_event.add_argument("--payload-file", default="-", help="JSON payload file, or - for stdin")
    feedback_event.set_defaults(cmd="feedback-event")

    crm_reconcile = sub.add_parser("crm-reconcile")
    crm_reconcile.add_argument("--days", type=int, default=14)
    mode_group = crm_reconcile.add_mutually_exclusive_group(required=True)
    mode_group.add_argument("--dry-run", action="store_true")
    mode_group.add_argument("--apply", action="store_true")
    crm_reconcile.add_argument("--vacancy-id", type=int, default=None)
    crm_reconcile.add_argument("--limit", type=int, default=None)
    crm_reconcile.set_defaults(cmd="crm-reconcile")

    retire = sub.add_parser("retire-stale")
    retire.add_argument("--days", type=int, default=None)
    retire.set_defaults(cmd="retire-stale")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.cmd == "metrics-exporter":
        exporter = JobIntelObservabilityExporter(_store())
        exporter.serve(host=args.host, port=args.port)
        return 0
    assert_runtime_contract()

    if args.cmd == "bootstrap":
        store = _store()
        store.bootstrap()
        print(f"bootstrapped {resolve_db_path()}")
        return 0
    if args.cmd == "daily":
        print(run_daily())
        return 0
    if args.cmd == "alert":
        print(run_alert_scan())
        return 0
    if args.cmd == "enrichment":
        print(run_enrichment())
        return 0
    if args.cmd == "market":
        print(run_market_report())
        return 0
    if args.cmd == "strategic":
        print(run_strategic_report())
        return 0
    if args.cmd == "health":
        print(run_health_report())
        return 0
    if args.cmd == "weekly-kpi":
        print(run_weekly_kpi_report())
        return 0
    if args.cmd == "universe-discovery":
        from job_intel.universe import run_universe_discovery
        print(run_universe_discovery(deliver=not args.no_deliver))
        return 0
    if args.cmd == "performance-report":
        print(run_performance_report(args.last))
        return 0
    if args.cmd == "send-performance-report":
        print(send_performance_report(args.run_id, force=args.force))
        return 0
    if args.cmd == "doctor":
        print(doctor_report())
        return 0
    if args.cmd == "browser-health":
        health = _browser_desktop_health()
        print(_format_browser_desktop_health(health))
        return 0 if health["status"] == "healthy" else 1
    if args.cmd == "send-test":
        print(send_test_message(args.channel))
        return 0
    if args.cmd == "feedback-event":
        if args.payload_file == "-":
            raw = sys.stdin.read()
        else:
            raw = Path(args.payload_file).read_text(encoding="utf-8")
        payload = json.loads(raw or "{}")
        print(run_feedback_event(payload))
        return 0
    if args.cmd == "crm-reconcile":
        print(run_crm_reconcile(args.days, args.dry_run, args.apply, args.vacancy_id, args.limit))
        return 0
    if args.cmd == "retire-stale":
        result = retire_stale_vacancies(args.days)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
