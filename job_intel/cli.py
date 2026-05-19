from __future__ import annotations

import argparse
import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests

from .browser_sourcing import metrics_from_counts
from .company_intel import build_market_report, monitor_target_companies
from .config import DEFAULT_CONFIG, load_config_bundle
from .strategic import build_strategic_report, update_strategic_layer
from .dedup import canonical_vacancy_key, description_similarity, is_duplicate
from .digest import format_daily_digest, format_enrichment_questions, format_vacancy_summary
from .evaluator import score_vacancy
from .enrichment import detect_high_value_questions
from .models import Evaluation, Vacancy
from .runtime import (
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



def _collect_vacancies(store: JobIntelStore | None = None) -> CollectedVacancies:
    cfg = load_config_bundle() or DEFAULT_CONFIG
    store = store or _store()
    store.bootstrap()
    vacancies: list[Vacancy] = []
    statuses: dict[str, dict[str, Any]] = {}

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

    linkedin_queries = [
        "VP Product monetization B2C platform",
        "Head of Product fintech telecom",
    ]
    linkedin_hits = 0
    linkedin_errors: list[str] = []
    for query in linkedin_queries:
        try:
            results = fetch_linkedin_vacancies(query, max_pages=1)
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

    hh_queries = [
        "VP Product monetization B2C platform",
        "Head of Product fintech telecom",
        "Director of Product ecosystem growth",
    ]
    hh_hits = 0
    hh_errors: list[str] = []
    for query in hh_queries:
        try:
            results = fetch_headhunter_vacancies(query, per_page=10)
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
    elif ddg_errors:
        ddg_status = "error"
    else:
        ddg_status = "empty"
    statuses["duckduckgo"] = _source_status_template("duckduckgo", status=ddg_status, hits=ddg_hits, errors=ddg_errors)

    remoteok_hits = 0
    remoteok_errors: list[str] = []
    try:
        remoteok_vacancies = search_remoteok_jobs(max_results=25)
        vacancies.extend(remoteok_vacancies)
        remoteok_hits = len(remoteok_vacancies)
    except Exception as exc:
        remoteok_errors.append(str(exc))
    statuses["remoteok"] = _source_status_template("remoteok", status="ok" if remoteok_hits else ("error" if remoteok_errors else "empty"), hits=remoteok_hits, errors=remoteok_errors)

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
    if not webhook:
        return SlackDeliveryResult(success=False, attempts=0, error="JOB_INTEL_SLACK_WEBHOOK_URL is not set")
    if message == "[SILENT]":
        return SlackDeliveryResult(success=True, attempts=0, error=None)

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
            return SlackDeliveryResult(success=True, attempts=attempts)
        except Exception as exc:
            last_error = exc
            if attempt < retries:
                continue
    return SlackDeliveryResult(success=False, attempts=attempts, error=str(last_error) if last_error else None)



def _source_footer(source_statuses: dict[str, dict[str, Any]]) -> str | None:
    issues: list[str] = []
    for name, status in source_statuses.items():
        if status.get("status") not in {"ok", "empty"}:
            message = status.get("status", "unknown")
            if status.get("errors"):
                message = f"{message}: {status['errors'][-1]}"
            issues.append(f"{name}={message}")
    if not issues:
        return None
    return f"Operator note: source issues detected — {', '.join(issues)}"



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
    status = "sent" if delivery.success else "failed"
    error = None if delivery.success else delivery.error
    for notification_id in notification_ids:
        store.mark_notification_delivery(notification_id, status, attempts=delivery.attempts, delivery_error=error)



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
    canonical_rows: list[Vacancy] = []
    seen_keys: set[str] = set()
    source_counts: dict[str, dict[str, int]] = {}

    def _count_source(vacancy: Vacancy, evaluation: Any) -> None:
        stats = source_counts.setdefault(vacancy.source, {"found": 0, "executive_matches": 0, "accepted": 0, "rejected": 0})
        stats["found"] += 1
        if evaluation.tier in {"exceptional_fit", "strong_fit"}:
            stats["executive_matches"] += 1
        if evaluation.recommendation == "reject":
            stats["rejected"] += 1
        else:
            stats["accepted"] += 1

    for vacancy in vacancies:
        vacancy_key = canonical_vacancy_key(vacancy)
        vacancy_id = store.upsert_vacancy(vacancy, vacancy_key)
        evaluation = score_vacancy(vacancy)
        store.save_evaluation(vacancy_key, evaluation, run_id=run_id)
        _count_source(vacancy, evaluation)

        is_dup = vacancy_key in seen_keys
        if not is_dup:
            for existing in canonical_rows:
                if is_duplicate(vacancy, existing, similarity_threshold=similarity_threshold, repost_window_days=repost_window_days):
                    is_dup = True
                    store.save_duplicate(canonical_vacancy_key(existing), vacancy_key, "semantic/repost match", description_similarity(vacancy.description, existing.description))
                    break

        if is_dup:
            store.set_vacancy_status(vacancy_id, "duplicate")
            continue

        if evaluation.recommendation == "reject":
            store.set_vacancy_status(vacancy_id, "rejected")
            continue

        if _should_notify_vacancy(store, vacancy_id, vacancy, evaluation, repost_window_days):
            accepted.append((vacancy, evaluation, vacancy_id))
        else:
            store.set_vacancy_status(vacancy_id, "notified")

        canonical_rows.append(vacancy)
        seen_keys.add(vacancy_key)

    accepted.sort(key=lambda item: item[1].score, reverse=True)
    batch_size = cfg["runtime"]["slack"]["batch_size"]
    digest_items = accepted[:batch_size]
    operator_footer = _source_footer(source_statuses)
    digest = format_daily_digest([(vacancy, evaluation) for vacancy, evaluation, _ in digest_items], operator_footer=operator_footer)

    for source, stats in source_counts.items():
        existing = dict(source_statuses.get(source, {"source": source, "status": "unknown"}))
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
        )
        existing["metrics"] = metrics.__dict__
        source_statuses[source] = existing

    notification_ids = _prepare_notifications(store, run_id, cfg["runtime"]["slack"]["channel"], "daily_digest", digest_items)
    delivery = _deliver_to_slack(digest, cfg["runtime"]["slack"]["channel"])
    _finalize_notifications(store, notification_ids, delivery)

    for vacancy, evaluation, vacancy_id in digest_items:
        if delivery.success:
            store.set_vacancy_status(vacancy_id, "notified")
        else:
            store.set_vacancy_status(vacancy_id, "active")

    strategic = update_strategic_layer(store, persist=True)
    strategy_count = len(strategic.predictions)

    run_notes = f"found={len(vacancies)} accepted={len(accepted)} source_failures={sum(1 for s in source_statuses.values() if s.get('status') not in {'ok', 'empty'})} strategic_predictions={strategy_count}"
    store.finish_run(run_id, status="ok", notes=run_notes, metadata={"source_statuses": source_statuses, "delivery": delivery.__dict__, "strategic_predictions": strategy_count})
    return digest



def run_enrichment() -> str:
    store = _store()
    store.bootstrap()
    run_id = store.start_run("enrichment")
    memory = store.get_memory()
    questions = detect_high_value_questions(memory)
    digest = format_enrichment_questions(questions)
    if digest != "[SILENT]":
        notification_id = store.create_notification(run_id, "C0B42K4H4KV", "enrichment_questions", digest, delivery_status="pending")
        delivery = _deliver_to_slack(digest, "C0B42K4H4KV")
        store.mark_notification_delivery(notification_id, "sent" if delivery.success else "failed", attempts=delivery.attempts, delivery_error=delivery.error)
    store.finish_run(run_id, status="ok", notes=f"questions={len(questions)}", metadata={"questions": questions})
    return digest



def run_market_report() -> str:
    store = _store()
    store.bootstrap()
    run_id = store.start_run("market")
    monitor_target_companies(store)
    digest = build_market_report(store)
    if digest != "[SILENT]":
        channel = load_config_bundle().get("runtime", {}).get("slack", {}).get("market_channel", "C0B42K4H4KV")
        notification_id = store.create_notification(run_id, channel, "market_report", digest, delivery_status="pending")
        delivery = _deliver_to_slack(digest, channel)
        store.mark_notification_delivery(notification_id, "sent" if delivery.success else "failed", attempts=delivery.attempts, delivery_error=delivery.error)
        store.finish_run(run_id, status="ok", notes=f"report_length={len(digest)}", metadata={"delivery": delivery.__dict__})
    else:
        store.finish_run(run_id, status="ok", notes="report=silent", metadata={"report": "silent"})
    return digest



def run_strategic_report() -> str:
    store = _store()
    store.bootstrap()
    run_id = store.start_run("strategic")
    digest = build_strategic_report(store)
    if digest != "[SILENT]":
        channel = load_config_bundle().get("runtime", {}).get("slack", {}).get("strategic_channel", "C0B42K4H4KV")
        notification_id = store.create_notification(run_id, channel, "strategic_report", digest, delivery_status="pending")
        delivery = _deliver_to_slack(digest, channel)
        store.mark_notification_delivery(notification_id, "sent" if delivery.success else "failed", attempts=delivery.attempts, delivery_error=delivery.error)
        store.finish_run(run_id, status="ok", notes=f"report_length={len(digest)}", metadata={"delivery": delivery.__dict__})
    else:
        store.finish_run(run_id, status="ok", notes="report=silent", metadata={"report": "silent"})
    return digest



def run_alert_scan() -> str:
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



def retire_stale_vacancies(days: int | None = None) -> dict[str, int]:
    cfg = load_config_bundle() or DEFAULT_CONFIG
    resolved_days = days if days is not None else int(os.getenv("JOB_INTEL_STALE_DAYS", cfg["runtime"].get("stale_after_days", 30)))
    store = _store()
    store.bootstrap()
    return store.retire_stale(days=resolved_days)



def _cron_script_paths() -> list[Path]:
    scripts_dir = resolve_scripts_dir()
    names = ["job_intel_daily.sh", "job_intel_alert.sh", "job_intel_enrichment.sh"]
    if scripts_dir and scripts_dir.exists():
        return [scripts_dir / name for name in names]
    return [runtime_home() / ".hermes" / "scripts" / name for name in names]



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
    paths = {
        "Current user": runtime_user(),
        "Home directory": str(runtime_home()),
        "Environment": resolve_environment_name(),
        "Workdir": str(resolve_workdir()),
        "DB path": str(resolve_db_path()),
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

    doctor = sub.add_parser("doctor")
    doctor.set_defaults(cmd="doctor")

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
    if args.cmd == "doctor":
        print(doctor_report())
        return 0
    if args.cmd == "send-test":
        print(send_test_message(args.channel))
        return 0
    if args.cmd == "retire-stale":
        result = retire_stale_vacancies(args.days)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    return 1
