from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from statistics import median
from typing import Any

from .store import JobIntelStore

HIGH_PRIORITY_PREDICTIONS = (
    "vp_product_hiring_3_6_months",
    "product_transformation_phase",
    "monetization_function_scaling",
    "new_market_executive_opportunity",
    "executive_hiring_pattern_activation",
)


@dataclass(frozen=True)
class StrategicSignalRecord:
    company: str
    signal_type: str
    signal_strength: str
    confidence: float
    horizon_days: int | None
    probability: float | None
    rationale: str
    evidence: dict[str, Any] = field(default_factory=dict)
    source: str = "strategic"


@dataclass(frozen=True)
class StrategicPredictionRecord:
    company: str
    prediction_type: str
    signal_strength: str
    probability: float
    horizon_days: int
    rationale: str
    evidence: dict[str, Any] = field(default_factory=dict)
    source: str = "strategic"
    observed_openings: int = 0
    resolution_status: str = "open"
    outcome_text: str | None = None


@dataclass(frozen=True)
class StrategicAnalysisResult:
    signals: list[StrategicSignalRecord] = field(default_factory=list)
    predictions: list[StrategicPredictionRecord] = field(default_factory=list)
    watchlist_companies: list[dict[str, Any]] = field(default_factory=list)
    actionable_opportunities: list[dict[str, Any]] = field(default_factory=list)
    top_companies: list[dict[str, Any]] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)



def _safe_json(value: Any, default: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return default
    if value is None:
        return default
    return value



def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value).strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)



def _days_since(value: Any) -> int | None:
    parsed = _parse_datetime(value)
    if parsed is None:
        return None
    delta = datetime.now(timezone.utc) - parsed
    return max(delta.days, 0)



def _company_payload(row: dict[str, Any]) -> dict[str, Any]:
    signals = _safe_json(row.get("signals_json"), {}) or {}
    risk_flags = _safe_json(row.get("risk_flags_json"), []) or []
    career_urls = _safe_json(row.get("career_urls_json"), []) or []
    sources = _safe_json(row.get("source_json"), {}) or {}
    return {
        "company": row.get("company") or "",
        "summary": row.get("summary") or "",
        "target_category": row.get("target_category") or "",
        "website": row.get("website") or "",
        "signals": signals,
        "signal_list": signals.get("signals") or [],
        "risk_flags": risk_flags,
        "career_urls": career_urls,
        "opening_count": int(row.get("opening_count") or 0),
        "last_scanned_at": row.get("last_scanned_at") or "",
        "last_signal_at": row.get("last_signal_at") or "",
        "source": sources.get("source") or "system",
        "raw": row,
    }



def _event_map(events: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    bucket: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        company = str(event.get("company") or "").strip().lower()
        if not company:
            continue
        bucket.setdefault(company, []).append(event)
    return bucket



def _lower_set(items: Any) -> set[str]:
    if isinstance(items, dict):
        return {str(key).lower() for key in items.keys()}
    if isinstance(items, list):
        return {str(item).lower() for item in items}
    return set()



def _signal_strength_from_score(score: float) -> str:
    if score >= 0.68:
        return "strong_signal"
    if score >= 0.42:
        return "moderate_signal"
    return "weak_signal"



def _tier_probability(signal_strength: str) -> float:
    return {
        "strong_signal": 0.78,
        "moderate_signal": 0.54,
        "weak_signal": 0.28,
    }.get(signal_strength, 0.28)



def _company_prediction_score(payload: dict[str, Any], events: list[dict[str, Any]]) -> tuple[float, list[str], dict[str, Any]]:
    signals = _lower_set(payload.get("signal_list") or [])
    text = f"{payload.get('summary', '')} {' '.join(payload.get('career_urls') or [])} {' '.join(payload.get('risk_flags') or [])}".lower()
    category = str(payload.get("target_category") or "").lower()
    opening_count = int(payload.get("opening_count") or 0)

    score = 0.18
    reasons: list[str] = []

    if opening_count >= 3:
        score += 0.20
        reasons.append("multiple live openings")
    elif opening_count > 0:
        score += 0.14
        reasons.append("current opening")
    else:
        score -= 0.16
        reasons.append("no live openings")

    if "leadership_change" in signals:
        score += 0.18
        reasons.append("leadership change signal")
    if "org_transformation" in signals:
        score += 0.18
        reasons.append("org transformation signal")
    if "funding_signal" in signals:
        score += 0.13
        reasons.append("funding signal")
    if "hiring_activity" in signals:
        score += 0.12
        reasons.append("hiring activity signal")
    if "growth_signal" in signals:
        score += 0.10
        reasons.append("growth signal")

    event_types = {str(event.get("event_type") or "").lower() for event in events}
    event_ages = [age for age in (_days_since(event.get("seen_at")) for event in events) if age is not None]
    recent_event = bool(event_types & {"leadership_change", "org_transformation", "funding_signal"})
    if recent_event:
        score += 0.10
        reasons.append("recent company event")
    if event_ages:
        freshest_event_age = min(event_ages)
        if freshest_event_age <= 14:
            score += 0.06
            reasons.append("fresh company event")
        elif freshest_event_age >= 120:
            score -= 0.05
            reasons.append("stale company event")
    else:
        freshest_event_age = None

    if any(term in text for term in ("new market", "expansion", "new geography", "launch", "platform", "ecosystem", "monetization", "pricing", "revenue")):
        score += 0.08
        reasons.append("strategic expansion or monetization language")
    if any(term in category for term in ("fintech", "subscription", "platform", "superapp", "saas", "ecosystem")):
        score += 0.05
        reasons.append("strategic target category")
    if any(term in text for term in ("ai", "artificial intelligence", "machine learning")):
        score += 0.03
        reasons.append("ai initiative language")

    last_signal_age = _days_since(payload.get("last_signal_at"))
    if last_signal_age is not None:
        if last_signal_age <= 14:
            score += 0.10
            reasons.append("fresh signal")
        elif last_signal_age <= 45:
            score += 0.05
            reasons.append("recent signal")
        elif last_signal_age >= 120:
            score -= 0.08
            reasons.append("stale signal")

    last_scan_age = _days_since(payload.get("last_scanned_at"))
    if last_scan_age is not None and last_scan_age >= 60:
        score -= 0.04
        reasons.append("stale scan")

    if opening_count == 0:
        score = min(score, 0.68)
        if not recent_event and not signals:
            score = min(score, 0.30)

    score = max(0.0, min(score, 1.0))
    details = {
        "signals": sorted(signals),
        "events": [str(event.get("event_type") or "") for event in events[:5]],
        "opening_count": opening_count,
        "target_category": category,
        "career_urls": payload.get("career_urls") or [],
        "last_signal_age_days": last_signal_age,
        "last_event_age_days": freshest_event_age,
    }
    return score, reasons, details



def _prediction_candidates(payload: dict[str, Any], events: list[dict[str, Any]]) -> list[StrategicPredictionRecord]:
    score, reasons, details = _company_prediction_score(payload, events)
    opening_count = int(payload.get("opening_count") or 0)
    if score < 0.52:
        return []

    signals = _lower_set(payload.get("signal_list") or [])
    text = f"{payload.get('summary', '')} {' '.join(payload.get('career_urls') or [])}".lower()
    category = str(payload.get("target_category") or "").lower()
    company = str(payload.get("company") or "")
    signal_strength = _signal_strength_from_score(score)
    probability = _tier_probability(signal_strength)
    evidence = {**details, "reasons": reasons, "signal_strength": signal_strength}

    predictions: list[StrategicPredictionRecord] = []

    if opening_count > 0 and score >= 0.60 and ("leadership_change" in signals or "org_transformation" in signals or "hiring_activity" in signals):
        predictions.append(
            StrategicPredictionRecord(
                company=company,
                prediction_type="vp_product_hiring_3_6_months",
                signal_strength=signal_strength,
                probability=probability,
                horizon_days=120,
                rationale="Strong signal cluster around leadership change, org change, and a live opening; keep monitoring for a VP Product or equivalent executive opening.",
                evidence=evidence,
                observed_openings=opening_count,
                resolution_status="open",
            )
        )

    if score >= 0.58 and ("funding_signal" in signals or "growth_signal" in signals or "launch" in text):
        predictions.append(
            StrategicPredictionRecord(
                company=company,
                prediction_type="product_transformation_phase",
                signal_strength=signal_strength,
                probability=probability,
                horizon_days=180,
                rationale="The company is in a visible product transformation phase; treat this as a watch signal, not a forecast with fake precision.",
                evidence=evidence,
                observed_openings=opening_count,
                resolution_status="open",
            )
        )

    if opening_count > 0 and score >= 0.56 and any(term in category for term in ("fintech", "subscription", "platform", "superapp", "saas")) and any(
        term in text for term in ("monetization", "pricing", "revenue", "subscription", "platform")
    ):
        predictions.append(
            StrategicPredictionRecord(
                company=company,
                prediction_type="monetization_function_scaling",
                signal_strength=signal_strength,
                probability=probability,
                horizon_days=150,
                rationale="Live openings plus monetization language suggest scaling of pricing, growth, or business-ownership functions.",
                evidence=evidence,
                observed_openings=opening_count,
                resolution_status="open",
            )
        )

    if score >= 0.55 and any(term in text for term in ("new market", "expansion", "new geography", "international", "regional")):
        predictions.append(
            StrategicPredictionRecord(
                company=company,
                prediction_type="new_market_executive_opportunity",
                signal_strength=signal_strength,
                probability=probability,
                horizon_days=180,
                rationale="Expansion signals are consistent with a future executive opportunity around market entry or scale-up.",
                evidence=evidence,
                observed_openings=opening_count,
                resolution_status="open",
            )
        )

    if opening_count > 0 and score >= 0.56 and ("hiring_activity" in signals or "leadership_change" in signals):
        predictions.append(
            StrategicPredictionRecord(
                company=company,
                prediction_type="executive_hiring_pattern_activation",
                signal_strength=signal_strength,
                probability=probability,
                horizon_days=90,
                rationale="Hiring and leadership signals indicate executive-search behavior is active enough to monitor closely.",
                evidence=evidence,
                observed_openings=opening_count,
                resolution_status="open",
            )
        )

    return predictions



def _bucket_company(payload: dict[str, Any], score: float, reasons: list[str], details: dict[str, Any]) -> dict[str, Any]:
    opening_count = int(payload.get("opening_count") or 0)
    signal_strength = _signal_strength_from_score(score)
    fresh_event_age = details.get("last_event_age_days")
    last_signal_age = details.get("last_signal_age_days")

    actionable = opening_count > 0 and score >= 0.38
    watchlist = score >= 0.42 and not actionable

    if actionable:
        bucket = "actionable opportunity"
        action = "Prioritize outreach, reopen the role against Denis's fit, and watch for leadership-triggered expansion in adjacent functions."
    elif watchlist:
        bucket = "interesting company"
        action = "Keep on watchlist; no outreach trigger yet, but monitor for a live opening or a leadership change that could create one."
    else:
        bucket = "background"
        action = "Monitor quietly; signal density is too low for either outreach or strategic watchlist priority."

    if opening_count == 0 and score >= 0.55:
        action = "Keep on watchlist; strong signal cluster exists, but zero openings means this is not yet an actionable opportunity."

    if opening_count > 0 and score < 0.38:
        action = "There is a live opening, but the company signal is weak; verify fit before outreach."
        bucket = "actionable opportunity"

    return {
        "company": payload.get("company") or "",
        "score": round(score, 2),
        "signal_strength": signal_strength,
        "bucket": bucket,
        "openings": opening_count,
        "category": payload.get("target_category") or "",
        "website": payload.get("website") or "",
        "career_urls": payload.get("career_urls") or [],
        "signals": sorted(_lower_set(payload.get("signal_list") or [])),
        "reasons": reasons,
        "action": action,
        "last_signal_age_days": last_signal_age,
        "last_event_age_days": fresh_event_age,
    }



def _validation_metrics(insights: list[dict[str, Any]], predictions: list[StrategicPredictionRecord]) -> dict[str, Any]:
    if not insights:
        return {
            "companies_scored": 0,
            "actionable_opportunities": 0,
            "watchlist_companies": 0,
            "signal_to_opening_conversion_proxy": "n/a",
            "prediction_resolution_coverage": "n/a",
            "prediction_hit_rate": "n/a",
            "median_days_since_signal_on_open_companies": "n/a",
            "watchlist_freshness_proxy": "n/a",
        }

    with_signals = [item for item in insights if item.get("signals")]
    with_openings = [item for item in insights if int(item.get("openings") or 0) > 0]
    actionable = [item for item in insights if item.get("bucket") == "actionable opportunity"]
    watchlist = [item for item in insights if item.get("bucket") == "interesting company"]
    fresh_watchlist = [item for item in watchlist if item.get("last_signal_age_days") is not None and int(item["last_signal_age_days"]) <= 45]
    opening_signal_ages = [int(item["last_signal_age_days"]) for item in with_openings if item.get("last_signal_age_days") is not None]
    resolved_predictions = [prediction for prediction in predictions if prediction.resolution_status != "open"]
    resolved_hits = [prediction for prediction in resolved_predictions if prediction.outcome_text and any(term in prediction.outcome_text.lower() for term in ("open", "opening", "hiring", "role"))]

    if with_signals:
        signal_to_opening = f"{len([item for item in with_signals if int(item.get('openings') or 0) > 0])}/{len(with_signals)}"
    else:
        signal_to_opening = "n/a"

    if predictions:
        prediction_resolution_coverage = f"{len(resolved_predictions)}/{len(predictions)}"
    else:
        prediction_resolution_coverage = "n/a"

    if resolved_predictions:
        prediction_hit_rate = f"{len(resolved_hits)}/{len(resolved_predictions)}"
    else:
        prediction_hit_rate = "n/a"

    if opening_signal_ages:
        opening_signal_median = f"{int(median(opening_signal_ages))}d"
    else:
        opening_signal_median = "n/a"

    if watchlist:
        watchlist_freshness = f"{len(fresh_watchlist)}/{len(watchlist)}"
    else:
        watchlist_freshness = "n/a"

    return {
        "companies_scored": len(insights),
        "companies_with_signals": len(with_signals),
        "companies_with_openings": len(with_openings),
        "actionable_opportunities": len(actionable),
        "watchlist_companies": len(watchlist),
        "signal_to_opening_conversion_proxy": signal_to_opening,
        "prediction_resolution_coverage": prediction_resolution_coverage,
        "prediction_hit_rate": prediction_hit_rate,
        "median_days_since_signal_on_open_companies": opening_signal_median,
        "watchlist_freshness_proxy": watchlist_freshness,
        "current_opportunity_yield_proxy": f"{len(actionable)}/{len(insights)}",
    }



def update_strategic_layer(store: JobIntelStore, *, persist: bool = True, limit: int = 50) -> StrategicAnalysisResult:
    companies = store.fetch_company_intelligence(limit=limit)
    events_by_company = _event_map(store.fetch_company_events(limit=200))
    signal_records: list[StrategicSignalRecord] = []
    prediction_records: list[StrategicPredictionRecord] = []
    company_insights: list[dict[str, Any]] = []
    analysis_date = datetime.now(timezone.utc).date().isoformat()

    for row in companies:
        payload = _company_payload(row)
        company_key = payload["company"].lower()
        if not company_key:
            continue
        events = events_by_company.get(company_key, [])
        signals = _lower_set(payload.get("signal_list") or [])
        score, reasons, details = _company_prediction_score(payload, events)
        if not score and not signals:
            continue

        insight = _bucket_company(payload, score, reasons, details)
        company_insights.append(insight)

        if persist and signals:
            signal_strength = _signal_strength_from_score(score)
            confidence = min(0.98, 0.50 + score * 0.30)
            probability = _tier_probability(signal_strength)
            for signal_type in sorted(signals):
                signal_record = StrategicSignalRecord(
                    company=payload["company"],
                    signal_type=signal_type,
                    signal_strength=signal_strength,
                    confidence=confidence,
                    horizon_days=90 if signal_type in {"hiring_activity", "leadership_change"} else 180,
                    probability=probability,
                    rationale=f"Company emitted {signal_type.replace('_', ' ')} signals; signal_strength={signal_strength}.",
                    evidence={
                        "signals": sorted(signals),
                        "reasons": reasons,
                        "opening_count": payload.get("opening_count", 0),
                        "career_urls": payload.get("career_urls") or [],
                        "signal_strength": signal_strength,
                    },
                    source="strategic",
                )
                signal_records.append(signal_record)
                store.record_strategic_signal(
                    payload["company"],
                    signal_type,
                    confidence=signal_record.confidence,
                    horizon_days=signal_record.horizon_days,
                    probability=signal_record.probability,
                    rationale=signal_record.rationale,
                    evidence=signal_record.evidence,
                    source="strategic",
                    observed_at=analysis_date,
                )

        predictions = _prediction_candidates(payload, events)
        prediction_records.extend(predictions)
        if persist:
            for prediction in predictions:
                store.record_strategic_prediction(
                    prediction.company,
                    prediction.prediction_type,
                    probability=prediction.probability,
                    horizon_days=prediction.horizon_days,
                    rationale=prediction.rationale,
                    evidence=prediction.evidence,
                    source=prediction.source,
                    observed_openings=prediction.observed_openings,
                    resolution_status=prediction.resolution_status,
                    outcome_text=prediction.outcome_text,
                    created_at=analysis_date,
                )

    company_insights.sort(key=lambda item: (item["bucket"] == "actionable opportunity", item["score"], item["openings"], -(item["last_signal_age_days"] or 9999)), reverse=True)
    actionable_opportunities = [item for item in company_insights if item["bucket"] == "actionable opportunity"]
    watchlist_companies = [item for item in company_insights if item["bucket"] == "interesting company"]
    top_companies = (actionable_opportunities + watchlist_companies)[:limit]
    metrics = _validation_metrics(company_insights, prediction_records)

    return StrategicAnalysisResult(
        signals=signal_records,
        predictions=prediction_records,
        watchlist_companies=watchlist_companies[:limit],
        actionable_opportunities=actionable_opportunities[:limit],
        top_companies=top_companies,
        metrics=metrics,
    )



def _format_item(item: dict[str, Any], *, label: str) -> list[str]:
    lines = [
        f"- *{item['company']}* — {label} | signal_strength={item['signal_strength']} | openings={item['openings']} | category={item['category'] or 'n/a'}",
        f"  Action: {item['action']}",
    ]
    if item.get("reasons"):
        lines.append(f"  Why it matters: {', '.join(item['reasons'][:3])}")
    if item.get("signals"):
        lines.append(f"  Evidence: {', '.join(item['signals'][:4])}")
    if item.get("career_urls"):
        lines.append(f"  Career pages: {', '.join(item['career_urls'][:2])}")
    return lines



def build_strategic_report(store: JobIntelStore, *, limit: int = 5) -> str:
    analysis = update_strategic_layer(store, persist=True, limit=500)
    if not analysis.actionable_opportunities and not analysis.watchlist_companies:
        return "[SILENT]"

    actionable = analysis.actionable_opportunities[: min(3, limit)]
    remaining_slots = max(limit - len(actionable), 0)
    watchlist = analysis.watchlist_companies[:remaining_slots]

    lines = ["*Executive strategic intelligence brief*", ""]
    lines.append("*Executive summary*")
    lines.append(
        f"- Showing {len(actionable)} actionable opportunities and {len(watchlist)} interesting companies from {analysis.metrics.get('companies_scored', 0)} scored companies."
    )
    lines.append(
        f"- {analysis.metrics.get('companies_with_openings', 0)} companies in the monitored set currently have live openings; signal tiers are weak_signal, moderate_signal, and strong_signal."
    )
    lines.append("")

    if actionable:
        lines.append("*Actionable opportunities*")
        for item in actionable:
            lines.extend(_format_item(item, label="actionable opportunity"))
        lines.append("")

    if watchlist:
        lines.append("*Interesting companies to watch*")
        for item in watchlist:
            lines.extend(_format_item(item, label="interesting company"))
        lines.append("")

    lines.append("*Validation metrics*")
    for key in (
        "companies_scored",
        "companies_with_signals",
        "companies_with_openings",
        "actionable_opportunities",
        "watchlist_companies",
        "signal_to_opening_conversion_proxy",
        "prediction_resolution_coverage",
        "prediction_hit_rate",
        "median_days_since_signal_on_open_companies",
        "watchlist_freshness_proxy",
        "current_opportunity_yield_proxy",
    ):
        value = analysis.metrics.get(key, "n/a")
        pretty_key = key.replace("_", " ")
        lines.append(f"- {pretty_key}: {value}")

    lines.append("")
    lines.append("*What to do next*")
    if actionable:
        lines.append("- Start outreach with the strongest actionable opportunity and anchor the message on the live opening plus the most recent leadership or hiring change.")
    else:
        lines.append("- No company cleared the action threshold; keep the watchlist warm and wait for a live opening before treating it as outreach-ready.")
    if watchlist:
        lines.append("- Use the watchlist to monitor for a role announcement, leadership move, or hiring acceleration that turns an interesting company into an actionable opportunity.")
    else:
        lines.append("- The monitored set is currently thin on interesting-but-not-yet-actionable companies; expand source coverage if this persists.")
    lines.append("- Treat the validation metrics as calibration scaffolding; keep measuring conversion and hit rate before trusting future predictions.")

    return "\n".join(lines).rstrip()
