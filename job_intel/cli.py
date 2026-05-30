from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests

from tools.send_message_tool import send_message_tool

from .browser_sourcing import metrics_from_counts
from .ats_sources import (
    fetch_ashby,
    fetch_greenhouse,
    fetch_lever,
    fetch_personio,
    fetch_recruitee,
    fetch_smartrecruiters,
    fetch_teamtailor,
    status_from_hits_errors,
)
from .company_intel import build_market_report, monitor_target_companies
from .config import DEFAULT_CONFIG, load_config_bundle
from .strategic import build_strategic_report, update_strategic_layer
from .dedup import canonical_vacancy_key, description_similarity, is_duplicate
from .digest import (
    format_daily_digest,
    format_enrichment_questions,
    format_executive_opportunity_report,
    format_vacancy_summary,
    reject_reason_bucket,
)
from .evaluator import classify_vacancy, score_vacancy
from .enrichment import detect_high_value_questions
from .observability import JobIntelObservabilityExporter, record_daily_observability
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
TIER_ORDER = {"reject": 0, "weak_fit": 1, "possible_fit": 2, "strong_fit": 3, "exceptional_fit": 4}


@dataclass(frozen=True)
class SlackDeliveryResult:
    success: bool
    attempts: int
    error: str | None = None
    status: str = "sent"


@dataclass(frozen=True)
class CollectedVacancies:
    vacancies: list[Vacancy]
    source_statuses: dict[str, dict[str, Any]]



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
    payload = _source_status_template(source, status="skipped", hits=0, errors=[], acquisition=acquisition or source)
    payload["reason"] = "disabled by JOB_INTEL_ENABLED_SOURCES"
    return payload



def _collect_vacancies(store: JobIntelStore | None = None) -> CollectedVacancies:
    cfg = load_config_bundle() or DEFAULT_CONFIG
    store = store or _store()
    store.bootstrap()
    vacancies: list[Vacancy] = []
    statuses: dict[str, dict[str, Any]] = {}
    enabled_sources = _enabled_sources()

    if _source_enabled(enabled_sources, "target_companies"):
        target_result = monitor_target_companies(store)
        vacancies.extend(target_result.vacancies)
        company_ok = any(status.get("status") == "ok" for status in target_result.company_statuses.values())
        statuses["target_companies"] = _source_status_template(
            "target-companies",
            status="ok" if company_ok or target_result.vacancies else ("error" if target_result.company_statuses else "empty"),
            hits=len(target_result.vacancies),
            companies=len(target_result.company_statuses),
            company_statuses=target_result.company_statuses,
        )
    else:
        statuses["target_companies"] = _skipped_source_status("target-companies", acquisition="target-companies")

    if not _source_enabled(enabled_sources, "linkedin"):
        statuses["linkedin"] = _skipped_source_status("linkedin", acquisition="browser-native")
    else:
        linkedin_queries = rotating_source_queries("linkedin", limit=6)
        linkedin_hits = 0
        linkedin_errors: list[str] = []
        for query in linkedin_queries:
            try:
                results = fetch_linkedin_vacancies(query, max_pages=2)
                linkedin_hits += len(results)
                vacancies.extend(results)
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
        linkedin_source_status = _source_status_template("linkedin", status=linkedin_status, hits=linkedin_hits, errors=linkedin_errors, acquisition="browser-native")
        linkedin_health = getattr(fetch_linkedin_vacancies, "last_health", None)
        if linkedin_health:
            linkedin_source_status["session_health"] = linkedin_health
        statuses["linkedin"] = linkedin_source_status

    if not _source_enabled(enabled_sources, "headhunter"):
        statuses["headhunter"] = _skipped_source_status("headhunter", acquisition="browser-native-first")
    else:
        hh_query_limit = max(1, int(os.getenv("JOB_INTEL_HEADHUNTER_QUERY_LIMIT", "6")))
        hh_per_page = max(1, int(os.getenv("JOB_INTEL_HEADHUNTER_PER_PAGE", "10")))
        hh_queries = rotating_source_queries("headhunter", limit=hh_query_limit)
        hh_hits = 0
        hh_errors: list[str] = []
        for query in hh_queries:
            try:
                results = fetch_headhunter_vacancies(query, per_page=hh_per_page)
                hh_hits += len(results)
                vacancies.extend(results)
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
        hh_source_status = _source_status_template("headhunter", status=hh_status, hits=hh_hits, errors=hh_errors, acquisition="browser-native-first")
        hh_health = getattr(fetch_headhunter_vacancies, "last_health", None)
        if hh_health:
            hh_source_status["session_health"] = hh_health
        statuses["headhunter"] = hh_source_status

    # ATS Wave 1 (production sources). These must not fail the whole run.
    def _collect_ats(source: str, *, fetcher, acquisition: str) -> None:
        if not _source_enabled(enabled_sources, source):
            statuses[source] = _skipped_source_status(source, acquisition=acquisition)
            return
        queries = rotating_source_queries(source, limit=6)
        try:
            result = fetcher(queries)
            vacancies.extend(result.vacancies)
            hits = len(result.vacancies)
            errors = list(result.errors or [])
            status = status_from_hits_errors(hits, errors)
            payload = _source_status_template(source, status=status, hits=hits, errors=errors, acquisition=acquisition)
            payload["discovered_companies"] = int(result.discovered_companies or 0)
            payload["pages_fetched"] = int(result.pages_fetched or 0)
            statuses[source] = payload
        except Exception as exc:
            statuses[source] = _source_status_template(source, status="error", hits=0, errors=[str(exc)], acquisition=acquisition)

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

    if not _source_enabled(enabled_sources, "remoteok"):
        statuses["remoteok"] = _skipped_source_status("remoteok")
    else:
        remoteok_hits = 0
        remoteok_errors: list[str] = []
        try:
            remoteok_vacancies = search_remoteok_jobs(max_results=25)
            vacancies.extend(remoteok_vacancies)
            remoteok_hits = len(remoteok_vacancies)
        except Exception as exc:
            remoteok_errors.append(str(exc))
        statuses["remoteok"] = _source_status_template("remoteok", status="ok" if remoteok_hits else ("error" if remoteok_errors else "empty"), hits=remoteok_hits, errors=remoteok_errors)

    if not _source_enabled(enabled_sources, "remotive"):
        statuses["remotive"] = _skipped_source_status("remotive")
    else:
        remotive_hits = 0
        remotive_errors: list[str] = []
        try:
            remotive_vacancies = search_remotive_jobs(max_results=25)
            vacancies.extend(remotive_vacancies)
            remotive_hits = len(remotive_vacancies)
        except Exception as exc:
            remotive_errors.append(str(exc))
        statuses["remotive"] = _source_status_template("remotive", status="ok" if remotive_hits else ("error" if remotive_errors else "empty"), hits=remotive_hits, errors=remotive_errors)

    return CollectedVacancies(vacancies=vacancies, source_statuses=statuses)


def _collect_vacancies_compat(store: JobIntelStore | None = None) -> CollectedVacancies:
    try:
        return _coerce_collected(_collect_vacancies(store))
    except TypeError:
        return _coerce_collected(_collect_vacancies())


def _slack_webhook_enabled() -> bool:
    return bool(os.getenv("JOB_INTEL_SLACK_WEBHOOK_URL", "").strip())



def _deliver_to_slack(message: str, channel: str | None = None, *, retries: int = 3) -> SlackDeliveryResult:
    webhook = os.getenv("JOB_INTEL_SLACK_WEBHOOK_URL", "").strip()
    if message == "[SILENT]":
        return SlackDeliveryResult(success=True, attempts=0, error=None, status="sent")

    if not webhook:
        target = (f"slack:{channel}" if (channel and channel.startswith("C")) else ("slack:C0B4MM6D52A" if channel == "executive_search_report" else (f"slack:{channel}" if channel else "slack")))
        try:
            raw = send_message_tool({"target": target, "message": message})
            payload = json.loads(raw) if raw else {}
        except Exception as exc:
            return SlackDeliveryResult(success=False, attempts=1, error=f"live adapter delivery error: {exc}", status="failed")
        if payload.get("error"):
            return SlackDeliveryResult(success=False, attempts=1, error=str(payload.get("error")), status="failed")
        if payload.get("success"):
            return SlackDeliveryResult(success=True, attempts=1, error=None, status="sent")
        return SlackDeliveryResult(success=False, attempts=1, error=f"unexpected live adapter response: {payload}", status="failed")

    payload: dict[str, str] = {"text": message}
    if channel:
        payload["channel"] = channel

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



def _notification_payload(vacancy: Vacancy, evaluation: Any, vacancy_id: int) -> dict[str, Any]:
    return {
        "vacancy_id": vacancy_id,
        "vacancy_key": canonical_vacancy_key(vacancy),
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



def _should_notify_vacancy(store: JobIntelStore, vacancy_id: int, vacancy: Vacancy, evaluation: Any, repost_window_days: int) -> bool:
    latest = store.latest_notification_for_vacancy(vacancy_id, delivery_status="sent")
    if not latest:
        return True
    try:
        payload = json.loads(latest.get("payload_json") or "{}")
    except json.JSONDecodeError:
        payload = {}
    current_body = format_vacancy_summary(vacancy, evaluation)
    current_description_hash = sha256_text(vacancy.description)
    if payload.get("description_hash") and payload.get("description_hash") != current_description_hash:
        return True
    if latest.get("content_hash") == sha256_text(current_body):
        return False
    previous_tier = payload.get("tier", "reject")
    if TIER_ORDER.get(evaluation.tier, 0) > TIER_ORDER.get(previous_tier, 0):
        return True
    if vacancy.salary and vacancy.salary != payload.get("salary"):
        return True
    previous_description = payload.get("description", "")
    if previous_description and description_similarity(vacancy.description, previous_description) < 0.9:
        return True
    notified_at = parse_iso_datetime(latest.get("sent_at"))
    if notified_at and datetime.now(timezone.utc) - notified_at >= timedelta(days=repost_window_days):
        return True
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
            "source_search",
            body,
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
    cfg = load_config_bundle() or DEFAULT_CONFIG
    dedup_cfg = cfg["deduplication"]
    similarity_threshold = dedup_cfg["secondary_similarity"]["description_similarity_threshold"]
    repost_window_days = dedup_cfg["repost_detection"]["repost_window_days"]

    collected = _collect_vacancies_compat(store)
    vacancies = collected.vacancies
    source_statuses = collected.source_statuses
    accepted: list[tuple[Vacancy, Any, int]] = []
    scored_rows: list[tuple[Vacancy, Any, dict[str, Any], int, bool]] = []  # (vacancy, evaluation, classification, vacancy_id, duplicate)
    canonical_rows: list[Vacancy] = []
    seen_keys: set[str] = set()
    source_counts: dict[str, dict[str, Any]] = {}
    accepted_by_source: dict[str, list[tuple[Vacancy, Any, int]]] = {}

    def _count_source(vacancy: Vacancy, classification: dict[str, Any], evaluation: Any) -> None:
        source_key = _normalize_source_notification_key(vacancy.source)
        stats = source_counts.setdefault(
            source_key,
            {
                "found": 0,
                "executive_matches": 0,
                "accepted": 0,
                "rejected": 0,
                "found_count": 0,
                "executive_detected_count": 0,
                "scored_count": 0,
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
        # Placeholder: seniority confidence is not computed yet.
        if evaluation.recommendation == "reject":
            stats["rejected_count"] += 1
            stats["rejected"] += 1
        else:
            stats["accepted_count"] += 1
            stats["accepted"] += 1
            stats["accepted_score_list"].append(score)

    def _normalize_job_url(url: str) -> str:
        """Normalize URLs for within-run dedup.

        Drops query/fragment (tracking params) and trims trailing slash.
        """
        try:
            from urllib.parse import urlsplit, urlunsplit

            parsed = urlsplit((url or "").strip())
            if not parsed.scheme or not parsed.netloc:
                return (url or "").strip().rstrip("/")
            normalized = urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))
            return normalized.rstrip("/")
        except Exception:
            return (url or "").strip().rstrip("/")

    seen_urls: dict[str, str] = {}  # normalized_url -> canonical_vacancy_key of first seen

    for vacancy in vacancies:
        vacancy.url = _normalize_job_url(vacancy.url)
        vacancy_key = canonical_vacancy_key(vacancy)
        vacancy_id = store.upsert_vacancy(vacancy, vacancy_key)
        classification = classify_vacancy(vacancy)
        evaluation = score_vacancy(vacancy)
        store.save_evaluation(vacancy_key, evaluation, run_id=run_id)
        _count_source(vacancy, classification, evaluation)

        is_dup = vacancy_key in seen_keys
        if not is_dup and vacancy.url:
            url_key = vacancy.url
            canonical_for_url = seen_urls.get(url_key)
            if canonical_for_url is not None:
                is_dup = True
                store.save_duplicate(canonical_for_url, vacancy_key, "url match", 1.0)
            else:
                seen_urls[url_key] = vacancy_key
        if not is_dup:
            for existing in canonical_rows:
                if is_duplicate(vacancy, existing, similarity_threshold=similarity_threshold, repost_window_days=repost_window_days):
                    is_dup = True
                    store.save_duplicate(canonical_vacancy_key(existing), vacancy_key, "semantic/repost match", description_similarity(vacancy.description, existing.description))
                    break

        if is_dup:
            store.set_vacancy_status(vacancy_id, "duplicate")
            scored_rows.append((vacancy, evaluation, classification, vacancy_id, True))
            continue

        # Survived global dedup for this run.
        source_counts[_normalize_source_notification_key(vacancy.source)]["vacancies_deduped"] += 1

        if evaluation.recommendation == "reject":
            store.set_vacancy_status(vacancy_id, "rejected")
            scored_rows.append((vacancy, evaluation, classification, vacancy_id, False))
            continue

        if _should_notify_vacancy(store, vacancy_id, vacancy, evaluation, repost_window_days):
            accepted.append((vacancy, evaluation, vacancy_id))
            accepted_by_source.setdefault(_normalize_source_notification_key(vacancy.source), []).append((vacancy, evaluation, vacancy_id))
        else:
            store.set_vacancy_status(vacancy_id, "notified")

        scored_rows.append((vacancy, evaluation, classification, vacancy_id, False))
        canonical_rows.append(vacancy)
        seen_keys.add(vacancy_key)
    accepted.sort(key=lambda item: item[1].score, reverse=True)
    batch_size = cfg["runtime"]["slack"]["batch_size"]
    digest_items = accepted[:batch_size]
    operator_footer = _source_footer(source_statuses)
    search_channel = _search_report_channel(cfg)
    technical_footer = _search_technical_report(source_statuses, channel=search_channel)
    digest = ""

    target_company_hits = int((source_statuses.get("target_companies") or {}).get("hits") or 0)

    for source, stats in source_counts.items():
        existing = dict(source_statuses.get(source, {"source": source, "status": "unknown"}))
        session = existing.get("session_health") or {}
        hits = int(existing.get("hits") or stats["found"])
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

    source_notifications: list[dict[str, Any]] = []
    if len(source_statuses) > 2:
        source_notifications = _deliver_source_notifications(
            store,
            run_id,
            search_channel,
            source_statuses,
            source_counts,
            accepted_by_source,
        )
    # Compute "planned notified" counts (actual becomes 0 if delivery fails).
    planned_notified_by_source: dict[str, int] = {}
    for vacancy, _, _ in digest_items:
        key = _normalize_source_notification_key(vacancy.source)
        planned_notified_by_source[key] = planned_notified_by_source.get(key, 0) + 1

    # Executive Opportunity Report (user-facing).
    per_source_funnel: list[dict[str, object]] = []
    # Populate notified_count with planned values for reporting pre-delivery.
    for source, cnt in planned_notified_by_source.items():
        stats = source_counts.setdefault(source, {})
        stats["notified_count"] = int(cnt or 0)
        stats["notified"] = int(cnt or 0)

    for source in sorted(source_counts.keys()):
        stats = source_counts.get(source) or {}
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

    # Top scored opportunities (include rejected; exclude duplicates).
    nondup_scored = [(v, e, c, vid, dup) for (v, e, c, vid, dup) in scored_rows if not dup]
    nondup_scored.sort(key=lambda row: int(getattr(row[1], "score", 0) or 0), reverse=True)
    top_scored = [(v, e) for (v, e, _, _, _) in nondup_scored[:20]]

    # Rejection intelligence buckets.
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

    # Market intelligence (deduped, non-duplicate rows).
    title_counts = Counter()
    country_counts = Counter()
    company_counts = Counter()
    for v, _, _, _, dup in scored_rows:
        if dup:
            continue
        title = (v.title or "").strip() or "Unknown"
        company = (v.company or "").strip() or "Unknown"
        location = (v.location or "").strip() or "Unknown"
        title_counts[title] += 1
        company_counts[company] += 1
        country_counts[_country(location)] += 1

    market_titles = [(k, int(v)) for k, v in title_counts.most_common(8)]
    market_countries = [(k, int(v)) for k, v in country_counts.most_common(8)]
    market_companies = [(k, int(v)) for k, v in company_counts.most_common(10)]

    # Scoring calibration: top rejected by score (exclude duplicates).
    rejected_scored = [(v, e) for (v, e, _, _, dup) in scored_rows if (not dup and getattr(e, "recommendation", None) == "reject")]
    rejected_scored.sort(key=lambda row: int(getattr(row[1], "score", 0) or 0), reverse=True)
    top_rejected_high_score = rejected_scored[:10] if not accepted else rejected_scored[:0]

    digest = format_daily_digest(
        [(vacancy, evaluation) for vacancy, evaluation, _ in digest_items],
        title="Daily executive job digest",
        operator_footer=operator_footer if digest_items else None,
        technical_footer=technical_footer if digest_items else None,
    )

    delivery_report = _deliver_to_slack(digest, search_channel)

    notification_ids = _prepare_notifications(store, run_id, search_channel, "daily_digest", digest_items)
    _finalize_notifications(store, notification_ids, delivery_report)

    for vacancy, evaluation, vacancy_id in digest_items:
        if delivery_report.success:
            store.set_vacancy_status(vacancy_id, "notified")
        else:
            store.set_vacancy_status(vacancy_id, "active")

    # Update per-source notified_count based on actual delivery outcome.
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

    notified_vacancy_ids = {vacancy_id for _, _, vacancy_id in digest_items} if delivery_report.success else set()
    accepted_vacancy_ids = {vacancy_id for _, _, vacancy_id in accepted}
    record_daily_observability(
        store,
        run_id,
        scored_rows,
        accepted_vacancy_ids=accepted_vacancy_ids,
        notified_vacancy_ids=notified_vacancy_ids,
    )

    # Return user-facing report for CLI output.
    def _pctl(values: list[int], p: float) -> int | None:
        if not values:
            return None
        xs = sorted(values)
        if len(xs) == 1:
            return int(xs[0])
        k = int(round((p / 100.0) * (len(xs) - 1)))
        k = max(0, min(len(xs) - 1, k))
        return int(xs[k])

    for source, src_status in source_statuses.items():
        stats = source_counts.get(source) or {}
        found = int(stats.get("found_count") or 0)
        scores = list(stats.get("score_list") or [])
        accepted_scores = list(stats.get("accepted_score_list") or [])
        avg_score = (sum(scores) / len(scores)) if scores else None
        session = (src_status.get("session_health") or {})
        # Prefer BrowserSessionHealth keys when present; otherwise store NULLs.
        pages_fetched = session.get("pages_fetched")
        login_walls = session.get("login_walls")
        auth_redirects = session.get("auth_redirects")
        anti_bot_events = session.get("anti_bot_events")
        extraction_failures = session.get("extraction_failures")

        denom = found if found else 0
        pct_company_known = (float(stats.get("company_known") or 0) / denom) if denom else None
        pct_location_known = (float(stats.get("location_known") or 0) / denom) if denom else None
        pct_salary_known = (float(stats.get("salary_known") or 0) / denom) if denom else None
        pct_seniority_confident = None  # reserved

        store.upsert_source_kpi_run(
            run_id,
            source,
            {
                "source_status": src_status.get("status"),
                "acquisition_mode": src_status.get("acquisition"),
                "runtime_seconds": None,
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
                "pct_seniority_confident": pct_seniority_confident,
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

    strategic = update_strategic_layer(store, persist=True)
    strategy_count = len(strategic.predictions)

    run_notes = f"found={len(vacancies)} accepted={len(accepted)} source_failures={sum(1 for s in source_statuses.values() if s.get('status') not in {'ok', 'empty', 'skipped'})} strategic_predictions={strategy_count}"
    store.finish_run(run_id, status="ok", notes=run_notes, metadata={"source_statuses": source_statuses, "delivery": delivery_report.__dict__, "strategic_predictions": strategy_count, "source_notifications": source_notifications})
    return digest





def run_weekly_kpi_report() -> str:
    """Compute weekly KPI rollup from source_kpi_run and deliver it (delivery failures must not fail the run)."""
    assert_runtime_contract()
    store = _store()
    store.bootstrap()
    run_id = store.start_run("weekly_kpi")
    cfg = load_config_bundle() or DEFAULT_CONFIG
    channel = _search_report_channel(cfg)

    now = datetime.now(timezone.utc)
    # Last fully completed week [week_start, week_end)
    this_week_start = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    week_end = this_week_start
    week_start = week_end - timedelta(days=7)

    sql = """
    SELECT
      k.source AS source,
      COUNT(*) AS rows,
      SUM(CASE WHEN k.source_status IN ('ok','empty','error') THEN 1 ELSE 0 END) AS attempts,
      SUM(CASE WHEN k.source_status IN ('ok','empty') THEN 1 ELSE 0 END) AS ok,
      SUM(CASE WHEN k.source_status = 'error' THEN 1 ELSE 0 END) AS error,
      SUM(COALESCE(k.found_count, 0)) AS found,
      SUM(COALESCE(k.executive_detected_count, 0)) AS exec_detected,
      SUM(COALESCE(k.scored_count, 0)) AS scored,
      SUM(COALESCE(k.accepted_count, 0)) AS accepted,
      SUM(COALESCE(k.notified_count, 0)) AS notified,
      CASE WHEN SUM(COALESCE(k.found_count, 0)) > 0
        THEN SUM(COALESCE(k.pct_company_known, 0.0) * COALESCE(k.found_count, 0)) / SUM(COALESCE(k.found_count, 0))
        ELSE NULL END AS pct_company_known_w,
      CASE WHEN SUM(COALESCE(k.found_count, 0)) > 0
        THEN SUM(COALESCE(k.pct_location_known, 0.0) * COALESCE(k.found_count, 0)) / SUM(COALESCE(k.found_count, 0))
        ELSE NULL END AS pct_location_known_w
    FROM source_kpi_run k
    JOIN runs r ON r.id = k.run_id
    WHERE r.run_type = 'production'
      AND k.source_status != 'skipped'
      AND datetime(k.created_at) >= datetime(?)
      AND datetime(k.created_at) < datetime(?)
    GROUP BY k.source
    ORDER BY exec_detected DESC, found DESC
    """

    with store.connect() as conn:
        rows = conn.execute(sql, (week_start.isoformat(), week_end.isoformat())).fetchall()

    header = "*Job-intel weekly source KPIs*\n" + f"Week: {week_start.date().isoformat()} -> {week_end.date().isoformat()} (UTC)"
    if not rows:
        message = header + "\nNo KPI rows found for this period."
    else:
        out = [header, "", "source | ok_rate | exec | accepted | accept_rate | notified | found | company_known | location_known"]
        for row in rows:
            attempts = int(row["attempts"] or 0)
            ok = int(row["ok"] or 0)
            ok_rate = (ok / attempts) if attempts else 0.0
            exec_detected = int(row["exec_detected"] or 0)
            accepted = int(row["accepted"] or 0)
            accept_rate = (accepted / exec_detected) if exec_detected else 0.0
            ck = row["pct_company_known_w"]
            lk = row["pct_location_known_w"]
            ck_s = f"{ck:.2f}" if ck is not None else "n/a"
            lk_s = f"{lk:.2f}" if lk is not None else "n/a"
            out.append(
                f"{row['source']} | {ok_rate:.2f} | {exec_detected} | {accepted} | {accept_rate:.2f} | {int(row['notified'] or 0)} | {int(row['found'] or 0)} | {ck_s} | {lk_s}"
            )
        message = "\n".join(out)

    # Delivery is best-effort. A delivery failure must not flip run.status to error.
    notification_id = store.create_notification(run_id, channel, "weekly_kpi", message, delivery_status="pending")
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
        notes=f"sources={len(rows) if rows else 0}",
        metadata={
            "delivery": delivery.__dict__,
            "week_start": week_start.isoformat(),
            "week_end": week_end.isoformat(),
            "source_rows": len(rows) if rows else 0,
        },
    )
    return message



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
    if not digest_items:
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
    text = (location or "").lower()
    return any(
        token in text
        for token in (
            "remote",
            "almaty",
            "astana",
            "kazakhstan",
            "kz",
            "central asia",
            "emea",
            "europe",
            "mena",
            "apac",
            "asia",
            "singapore",
            "uae",
            "dubai",
        )
    )



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
        stats["accepted_roles_by_source"] += 1 if row.get("recommendation") != "reject" else 0
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

        if row.get("recommendation") == "reject":
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
            "SELECT COUNT(*) FROM notifications WHERE sent_at >= ? AND message_type = 'daily_digest' AND delivery_status = 'sent'",
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
    digest = _format_health_report(current, previous)
    cfg = load_config_bundle() or DEFAULT_CONFIG
    channel = _search_report_channel(cfg)
    notification_id = store.create_notification(run_id, channel, "health_report", digest, delivery_status="pending")
    delivery = _deliver_to_slack(digest, channel)
    store.mark_notification_delivery(notification_id, _delivery_db_status(delivery), attempts=delivery.attempts, delivery_error=delivery.error)
    store.finish_run(run_id, status="ok", notes=f"report_length={len(digest)}", metadata={"report_type": "health", "latest_daily_run_id": daily_runs[0].get('id'), "previous_daily_run_id": daily_runs[1].get('id') if len(daily_runs) > 1 else None, "delivery": delivery.__dict__})
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
    latest = store.source_adapter_status_from_latest_run()
    if latest:
        return latest
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
    latest = store.latest_run() or {}
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
    if args.cmd == "retire-stale":
        result = retire_stale_vacancies(args.days)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    return 1
