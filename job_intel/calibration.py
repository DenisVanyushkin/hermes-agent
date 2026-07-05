"""Feedback analytics and scoring calibration (negative feedback loop PRD).

Covers slices 7-11: aggregation over feedback_events, the weekly digest,
scoring calibration proposals with evidence thresholds, dry-runs against
historical evaluations, and manual apply/reject/rollback with audit trail.

Core principle: feedback never mutates scoring directly. Everything here
produces *proposals*; `apply_proposal` runs only on explicit user command,
requires a prior dry-run, and refuses high-risk proposals without --force.
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from typing import Any

import yaml

from .config import SEED_DIR, load_config_bundle
from .feedback_taxonomy import (
    DEFAULT_HARD_BLOCKER_CODES,
    NO_PREFERENCE_PENALTY_CODES,
    REASON_TO_SCORING_FEATURE,
)

logger = logging.getLogger(__name__)

SCORING_SEED_PATH = SEED_DIR / "scoring.yaml"

# PRD 14.3 minimum evidence thresholds.
MIN_SAME_DETAIL_CODE_30D = 5
MIN_SAME_HARD_BLOCKER_14D = 3
MIN_WEEKLY_EVENTS_FOR_FULL_DIGEST = 5

# Proposal sizing: bump the |weight| by 25%, at least 2 points.
PROPOSAL_RELATIVE_STEP = 0.25
PROPOSAL_MIN_STEP = 2

POSITIVE_OPPORTUNITY_STATUSES = {
    "watchlist",
    "evaluation_requested",
    "evaluated",
    "artifact_requested",
    "artifacts_ready",
    "application_planned",
    "applied",
    "application_confirmed",
    "outreach_planned",
    "outreach_sent",
    "recruiter_replied",
    "interviewing",
    "assessment_received",
    "offer_process",
}

POSITIVE_FEEDBACK_TYPES = {"interesting", "exceptional", "applied", "save_for_later"}

MATERIAL_DROP_POINTS = 10


# --- Slice 7: analytics -----------------------------------------------------


def aggregate_feedback(store: Any, *, days: int = 30, company: str | None = None) -> dict[str, Any]:
    events = store.fetch_feedback_events(days=days, company=company, limit=5000)
    negative = [event for event in events if event.get("polarity") == "negative"]
    classified = [event for event in negative if event.get("status") == "classified"]
    unclassified = [event for event in negative if event.get("status") != "classified"]

    detail_counter: Counter[str] = Counter()
    category_counter: Counter[str] = Counter()
    attribution_counter: Counter[str] = Counter()
    company_counter: Counter[str] = Counter()
    company_level_rejects: Counter[str] = Counter()
    preference_events = 0
    data_quality_events = 0

    for event in classified:
        details = event.get("reason_detail_codes") or []
        detail_counter.update(details)
        category_counter.update(event.get("reason_category_codes") or [])
        attribution_counter.update(event.get("attribution_targets") or [])
        company_name = event.get("company")
        if company_name:
            company_counter[company_name] += 1
            if event.get("applies_to_company"):
                company_level_rejects[company_name] += 1
        if details and all(code in NO_PREFERENCE_PENALTY_CODES for code in details):
            data_quality_events += 1
        else:
            preference_events += 1

    return {
        "window_days": days,
        "total_negative_events": len(negative),
        "classified_events": len(classified),
        "unclassified_events": len(unclassified),
        "preference_events": preference_events,
        "data_quality_events": data_quality_events,
        "top_reason_detail_codes": detail_counter.most_common(15),
        "top_reason_categories": category_counter.most_common(10),
        "top_attribution_targets": attribution_counter.most_common(10),
        "negative_by_company": company_counter.most_common(15),
        "company_level_rejects": company_level_rejects.most_common(15),
    }


# --- Slice 8: weekly digest --------------------------------------------------


def build_weekly_digest(store: Any, *, days: int = 7) -> dict[str, Any]:
    summary = aggregate_feedback(store, days=days)
    total = summary["total_negative_events"]
    if total < MIN_WEEKLY_EVENTS_FOR_FULL_DIGEST:
        return {
            "mode": "short",
            "text": (
                f"Negative feedback: за последние {days} дн. событий мало ({total}) — "
                "полный разбор пропускаю."
            ),
            "summary": summary,
        }

    lines = [
        f"Negative feedback review — last {days} days",
        "",
        f"Negative feedback events: {total}",
        f"Classified: {summary['classified_events']}",
        f"Unclassified: {summary['unclassified_events']}",
        "",
        "Top reasons:",
    ]
    for code, count in summary["top_reason_detail_codes"][:5]:
        lines.append(f"- {code}: {count}")
    lines.append("")
    lines.append(
        f"Preference vs data quality: {summary['preference_events']} preference / "
        f"{summary['data_quality_events']} data-quality (data-quality не влияет на скоринг)."
    )
    if summary["top_attribution_targets"]:
        targets = ", ".join(f"{target} ({count})" for target, count in summary["top_attribution_targets"][:5])
        lines.append(f"Attribution: {targets}.")
    if summary["company_level_rejects"]:
        companies = ", ".join(name for name, _ in summary["company_level_rejects"][:5])
        lines.append(f"Company-level rejects: {companies}.")
    patterns = detect_patterns(store, window_days=30)
    lines.append("")
    if patterns:
        lines.append("Есть повторяющиеся паттерны — можно запустить: propose scoring calibration.")
    else:
        lines.append("Порог для scoring-калибровки пока не достигнут.")
    return {"mode": "full", "text": "\n".join(lines), "summary": summary}


# --- Slice 9: proposal generation --------------------------------------------


def detect_patterns(store: Any, *, window_days: int = 30) -> list[dict[str, Any]]:
    """Repeated preference patterns above PRD evidence thresholds."""
    monthly = aggregate_feedback(store, days=window_days)
    biweekly = aggregate_feedback(store, days=14)
    biweekly_counts = dict(biweekly["top_reason_detail_codes"])

    patterns: list[dict[str, Any]] = []
    for code, count in monthly["top_reason_detail_codes"]:
        if code in NO_PREFERENCE_PENALTY_CODES:
            continue  # PRD 22.3: data quality is never a preference signal
        is_hard_blocker = code in DEFAULT_HARD_BLOCKER_CODES
        qualifies = count >= MIN_SAME_DETAIL_CODE_30D or (
            is_hard_blocker and biweekly_counts.get(code, 0) >= MIN_SAME_HARD_BLOCKER_14D
        )
        if not qualifies:
            continue
        mapping = REASON_TO_SCORING_FEATURE.get(code)
        patterns.append(
            {
                "reason_code": code,
                "count": count,
                "count_14d": biweekly_counts.get(code, 0),
                "hard_blocker": is_hard_blocker,
                "scoring_feature": mapping["feature"] if mapping else None,
                "attribution": mapping["attribution"] if mapping else None,
            }
        )
    return patterns


def _current_scoring_config() -> dict[str, Any]:
    # Prefer the seed yaml (the file apply/rollback patches) so tests can
    # redirect SCORING_SEED_PATH; fall back to the merged config bundle.
    document = _load_scoring_yaml()
    scoring = document.get("scoring") or {}
    if scoring:
        return scoring
    return load_config_bundle().get("scoring") or {}


def _locate_feature(scoring: dict[str, Any], feature: str) -> tuple[str, int] | None:
    for section in ("positive_signals", "negative_signals"):
        values = scoring.get(section) or {}
        if feature in values:
            return section, int(values[feature])
    return None


def generate_proposal(store: Any, *, window_days: int = 30) -> dict[str, Any]:
    """Generate (and persist) a scoring calibration proposal, if evidence allows."""
    patterns = detect_patterns(store, window_days=window_days)
    summary = aggregate_feedback(store, days=window_days)
    if not patterns:
        return {"status": "no_proposal", "reason": "no_patterns_above_threshold"}

    scoring = _current_scoring_config()
    proposed_changes: list[dict[str, Any]] = []
    unmapped: list[dict[str, Any]] = []
    for pattern in patterns:
        feature = pattern["scoring_feature"]
        located = _locate_feature(scoring, feature) if feature else None
        if not located:
            unmapped.append(pattern)
            continue
        section, current = located
        step = max(PROPOSAL_MIN_STEP, round(abs(current) * PROPOSAL_RELATIVE_STEP))
        proposed = current + step if current >= 0 else current - step
        if any(change["scoring_feature"] == feature for change in proposed_changes):
            continue
        proposed_changes.append(
            {
                "section": section,
                "scoring_feature": feature,
                "current_value": current,
                "proposed_value": proposed,
                "rationale": (
                    f"{pattern['count']} negative feedback events with reason "
                    f"'{pattern['reason_code']}' in the last {window_days} days."
                ),
                "evidence_reason_code": pattern["reason_code"],
            }
        )

    if not proposed_changes:
        return {
            "status": "no_proposal",
            "reason": "patterns_found_but_no_mapped_scoring_features",
            "unmapped_patterns": unmapped,
        }

    evidence = {
        "total_negative_events": summary["total_negative_events"],
        "matched_patterns": patterns,
        "unmapped_patterns": unmapped,
    }
    proposal_id = store.create_scoring_proposal(
        evidence_window_days=window_days,
        evidence=evidence,
        proposed_changes=proposed_changes,
        risk_level="unknown",
    )
    store.add_scoring_calibration_event(
        proposal_id=proposal_id, event_type="proposed", payload={"patterns": len(patterns)}
    )
    return {"status": "proposed", "proposal_id": proposal_id, "proposed_changes": proposed_changes}


# --- Slice 10: dry-run --------------------------------------------------------


def _sample_evaluations(store: Any, *, limit: int = 100, days: int = 30) -> list[dict[str, Any]]:
    """Recent delivered vacancies + positively-marked opportunities.

    Uses the latest evaluation per vacancy_key, joined with delivery and
    positive-signal markers (positive reactions or advanced CRM status).
    """
    query = """
        SELECT ve.vacancy_key,
               ve.score,
               ve.raw_breakdown_json,
               v.company,
               v.title,
               MAX(CASE WHEN vsm.id IS NOT NULL THEN 1 ELSE 0 END) AS delivered,
               MAX(CASE WHEN vfs.feedback_type IN ('interesting','exceptional','applied','save_for_later')
                             AND vfs.active=1 THEN 1 ELSE 0 END) AS positive_reaction,
               MAX(CASE WHEN o.status IN ({positive_statuses}) THEN 1 ELSE 0 END) AS positive_status
        FROM vacancy_evaluations ve
        JOIN (
            SELECT vacancy_key, MAX(id) AS max_id FROM vacancy_evaluations GROUP BY vacancy_key
        ) latest ON latest.max_id = ve.id
        LEFT JOIN vacancies v ON v.vacancy_key = ve.vacancy_key
        LEFT JOIN vacancy_slack_messages vsm ON vsm.vacancy_id = v.id
        LEFT JOIN vacancy_feedback_state vfs ON vfs.vacancy_id = v.id
        LEFT JOIN opportunities o ON o.vacancy_id = v.id
        WHERE ve.created_at >= datetime('now', ?)
        GROUP BY ve.vacancy_key
        ORDER BY ve.id DESC
        LIMIT ?
    """.format(positive_statuses=",".join(f"'{status}'" for status in POSITIVE_OPPORTUNITY_STATUSES))
    with store.connect(read_only=True) as conn:
        rows = conn.execute(query, (f"-{days} days", limit)).fetchall()
    samples = []
    for row in rows:
        record = dict(row)
        try:
            record["breakdown"] = json.loads(record.get("raw_breakdown_json") or "{}")
        except (TypeError, ValueError):
            record["breakdown"] = {}
        record["positive"] = bool(record.get("positive_reaction")) or bool(record.get("positive_status"))
        samples.append(record)
    return samples


def _rescored(score: int, breakdown: dict[str, Any], changes: list[dict[str, Any]]) -> int:
    """Rescale per-signal contributions for changed weights.

    The stored breakdown holds each signal's contribution; scaling by
    new_weight/current_weight reproduces what the scorer would emit without
    re-parsing vacancy text.
    """
    new_score = float(score)
    for change in changes:
        feature = change["scoring_feature"]
        current = change["current_value"]
        proposed = change["proposed_value"]
        contribution = breakdown.get(feature)
        if not contribution or not current:
            continue
        new_score += float(contribution) * (float(proposed) / float(current)) - float(contribution)
    return round(new_score)


def dry_run_proposal(store: Any, proposal_id: int, *, sample_limit: int = 100, sample_days: int = 30) -> dict[str, Any]:
    proposal = store.get_scoring_proposal(proposal_id)
    if not proposal:
        return {"status": "not_found", "proposal_id": proposal_id}
    changes = proposal["proposed_changes"]
    thresholds = _current_scoring_config().get("thresholds") or {}
    delivery_threshold = int(thresholds.get("possible_fit", 60))

    samples = _sample_evaluations(store, limit=sample_limit, days=sample_days)
    scores_changed = 0
    would_drop = 0
    would_rise = 0
    harmed_positive: list[dict[str, Any]] = []
    examples: list[dict[str, Any]] = []

    for sample in samples:
        old_score = int(sample["score"])
        new_score = _rescored(old_score, sample["breakdown"], changes)
        if new_score == old_score:
            continue
        scores_changed += 1
        crossed_down = old_score >= delivery_threshold > new_score
        crossed_up = old_score < delivery_threshold <= new_score
        if crossed_down:
            would_drop += 1
        if crossed_up:
            would_rise += 1
        materially_down = old_score - new_score >= MATERIAL_DROP_POINTS
        if sample["positive"] and (crossed_down or materially_down):
            harmed_positive.append(
                {
                    "vacancy_key": sample["vacancy_key"],
                    "company": sample.get("company"),
                    "title": sample.get("title"),
                    "old_score": old_score,
                    "new_score": new_score,
                }
            )
        if len(examples) < 10:
            examples.append(
                {
                    "vacancy_key": sample["vacancy_key"],
                    "company": sample.get("company"),
                    "title": sample.get("title"),
                    "old_score": old_score,
                    "new_score": new_score,
                }
            )

    risk_level = "low"
    if would_drop or would_rise:
        risk_level = "medium"
    if harmed_positive:
        risk_level = "high"  # PRD 15.3 safety rule

    result = {
        "status": "ok",
        "proposal_id": proposal_id,
        "sample_size": len(samples),
        "scores_changed": scores_changed,
        "would_drop_below_threshold": would_drop,
        "would_rise_above_threshold": would_rise,
        "previously_positive_opportunities_harmed": len(harmed_positive),
        "harmed_examples": harmed_positive[:10],
        "examples": examples,
        "delivery_threshold": delivery_threshold,
        "risk_level": risk_level,
    }
    store.update_scoring_proposal(proposal_id, dry_run_result_json=result, risk_level=risk_level)
    store.add_scoring_calibration_event(
        proposal_id=proposal_id,
        event_type="dry_run",
        payload={"risk_level": risk_level, "sample_size": len(samples)},
    )
    return result


# --- Slice 11: apply / reject / rollback --------------------------------------


def _utcnow() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def _load_scoring_yaml() -> dict[str, Any]:
    if not SCORING_SEED_PATH.exists():
        return {"scoring": _current_scoring_config()}
    with SCORING_SEED_PATH.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def _write_scoring_yaml(document: dict[str, Any]) -> None:
    with SCORING_SEED_PATH.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(document, handle, allow_unicode=True, sort_keys=False)


def _apply_changes_to_yaml(changes: list[dict[str, Any]], *, reverse: bool = False) -> dict[str, Any]:
    document = _load_scoring_yaml()
    scoring = document.setdefault("scoring", {})
    previous: dict[str, Any] = {}
    for change in changes:
        section = scoring.setdefault(change["section"], {})
        feature = change["scoring_feature"]
        target_value = change["current_value"] if reverse else change["proposed_value"]
        previous[f"{change['section']}.{feature}"] = section.get(feature)
        section[feature] = target_value
    _write_scoring_yaml(document)
    return previous


def apply_proposal(store: Any, proposal_id: int, *, actor: str | None = None, force: bool = False) -> dict[str, Any]:
    proposal = store.get_scoring_proposal(proposal_id)
    if not proposal:
        return {"status": "not_found", "proposal_id": proposal_id}
    if proposal["status"] not in {"proposed", "dry_run_completed"}:
        return {"status": "invalid_status", "current_status": proposal["status"]}
    dry_run = proposal.get("dry_run_result")
    if not dry_run:
        return {"status": "dry_run_required", "proposal_id": proposal_id}
    if proposal.get("risk_level") == "high" and not force:
        return {
            "status": "blocked_high_risk",
            "proposal_id": proposal_id,
            "hint": "high-risk proposal: previously liked/applied opportunities would be harmed; "
            "re-run with explicit force confirmation",
        }

    previous = _apply_changes_to_yaml(proposal["proposed_changes"])
    now = _utcnow()
    store.update_scoring_proposal(
        proposal_id,
        status="applied",
        approved_at=now,
        applied_at=now,
        rollback_ref=json.dumps(previous, ensure_ascii=False),
    )
    store.add_scoring_calibration_event(
        proposal_id=proposal_id, event_type="applied", actor=actor, payload={"previous_values": previous}
    )
    return {"status": "applied", "proposal_id": proposal_id, "previous_values": previous}


def reject_proposal(store: Any, proposal_id: int, *, actor: str | None = None) -> dict[str, Any]:
    proposal = store.get_scoring_proposal(proposal_id)
    if not proposal:
        return {"status": "not_found", "proposal_id": proposal_id}
    if proposal["status"] == "applied":
        return {"status": "invalid_status", "current_status": "applied", "hint": "use rollback"}
    store.update_scoring_proposal(proposal_id, status="rejected", rejected_at=_utcnow())
    store.add_scoring_calibration_event(proposal_id=proposal_id, event_type="rejected", actor=actor)
    return {"status": "rejected", "proposal_id": proposal_id}


def rollback_proposal(store: Any, proposal_id: int, *, actor: str | None = None) -> dict[str, Any]:
    proposal = store.get_scoring_proposal(proposal_id)
    if not proposal:
        return {"status": "not_found", "proposal_id": proposal_id}
    if proposal["status"] != "applied":
        return {"status": "invalid_status", "current_status": proposal["status"]}
    _apply_changes_to_yaml(proposal["proposed_changes"], reverse=True)
    store.update_scoring_proposal(proposal_id, status="rolled_back")
    store.add_scoring_calibration_event(proposal_id=proposal_id, event_type="rolled_back", actor=actor)
    return {"status": "rolled_back", "proposal_id": proposal_id}
