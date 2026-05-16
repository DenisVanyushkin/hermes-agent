from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
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
    top_companies: list[dict[str, Any]] = field(default_factory=list)



def _safe_json(value: Any, default: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return default
    if value is None:
        return default
    return value



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



def _company_prediction_score(payload: dict[str, Any], events: list[dict[str, Any]]) -> tuple[float, list[str]]:
    signals = _lower_set(payload.get("signal_list") or [])
    text = f"{payload.get('summary', '')} {' '.join(payload.get('career_urls') or [])} {' '.join(payload.get('risk_flags') or [])}".lower()
    category = str(payload.get("target_category") or "").lower()
    score = 0.15
    reasons: list[str] = []

    if payload.get("opening_count", 0) >= 3:
        score += 0.15
        reasons.append("multiple current openings")
    elif payload.get("opening_count", 0) > 0:
        score += 0.08
        reasons.append("current executive opening")

    if "leadership_change" in signals:
        score += 0.20
        reasons.append("leadership change")
    if "org_transformation" in signals:
        score += 0.20
        reasons.append("org transformation")
    if "growth_signal" in signals:
        score += 0.12
        reasons.append("growth signal")
    if "hiring_activity" in signals:
        score += 0.10
        reasons.append("hiring activity")
    if "funding_signal" in signals:
        score += 0.15
        reasons.append("funding signal")

    event_types = {str(event.get("event_type") or "").lower() for event in events}
    if {"leadership_change", "org_transformation"} & event_types:
        score += 0.10
        reasons.append("recent company event")
    if any(term in text for term in ("new market", "expansion", "new geography", "launch", "platform", "ecosystem", "monetization")):
        score += 0.10
        reasons.append("strategic expansion or monetization language")
    if any(term in category for term in ("fintech", "subscription", "platform", "superapp", "saas", "ecosystem")):
        score += 0.08
        reasons.append("strategic target category")
    if any(term in text for term in ("ai", "artificial intelligence", "machine learning")):
        score += 0.05
        reasons.append("AI initiative language")

    return min(score, 0.95), reasons



def _prediction_candidates(payload: dict[str, Any], events: list[dict[str, Any]]) -> list[StrategicPredictionRecord]:
    score, reasons = _company_prediction_score(payload, events)
    if score < 0.35:
        return []

    signals = _lower_set(payload.get("signal_list") or [])
    text = f"{payload.get('summary', '')} {' '.join(payload.get('career_urls') or [])}".lower()
    category = str(payload.get("target_category") or "").lower()
    company = str(payload.get("company") or "")
    evidence = {
        "signals": sorted(signals),
        "events": [str(event.get("event_type") or "") for event in events[:5]],
        "opening_count": payload.get("opening_count", 0),
        "target_category": category,
        "career_urls": payload.get("career_urls") or [],
    }

    predictions: list[StrategicPredictionRecord] = []

    if score >= 0.65 and ("leadership_change" in signals or "org_transformation" in signals or "hiring_activity" in signals):
        predictions.append(
            StrategicPredictionRecord(
                company=company,
                prediction_type="vp_product_hiring_3_6_months",
                probability=min(0.92, 0.45 + score * 0.45),
                horizon_days=120,
                rationale="High likelihood of VP Product hiring within 3-6 months due to leadership/operating-model change and strategic growth signals.",
                evidence={**evidence, "reasons": reasons},
                observed_openings=int(payload.get("opening_count") or 0),
                resolution_status="open",
            )
        )

    if score >= 0.58 and ("funding_signal" in signals or "growth_signal" in signals or "launch" in text):
        predictions.append(
            StrategicPredictionRecord(
                company=company,
                prediction_type="product_transformation_phase",
                probability=min(0.90, 0.42 + score * 0.50),
                horizon_days=180,
                rationale="Company appears to be entering or accelerating a product transformation phase.",
                evidence={**evidence, "reasons": reasons},
                observed_openings=int(payload.get("opening_count") or 0),
                resolution_status="open",
            )
        )

    if score >= 0.55 and any(term in category for term in ("fintech", "subscription", "platform", "superapp", "saas")) and any(
        term in text for term in ("monetization", "pricing", "revenue", "subscription", "platform")
    ):
        predictions.append(
            StrategicPredictionRecord(
                company=company,
                prediction_type="monetization_function_scaling",
                probability=min(0.88, 0.38 + score * 0.48),
                horizon_days=150,
                rationale="Organization is likely scaling monetization, pricing, or business-ownership functions.",
                evidence={**evidence, "reasons": reasons},
                observed_openings=int(payload.get("opening_count") or 0),
                resolution_status="open",
            )
        )

    if score >= 0.50 and any(term in text for term in ("new market", "expansion", "new geography", "international", "regional")):
        predictions.append(
            StrategicPredictionRecord(
                company=company,
                prediction_type="new_market_executive_opportunity",
                probability=min(0.82, 0.35 + score * 0.42),
                horizon_days=180,
                rationale="Expansion signals suggest a likely future executive opening tied to market entry or scale-up.",
                evidence={**evidence, "reasons": reasons},
                observed_openings=int(payload.get("opening_count") or 0),
                resolution_status="open",
            )
        )

    if score >= 0.55 and ("hiring_activity" in signals or "leadership_change" in signals):
        predictions.append(
            StrategicPredictionRecord(
                company=company,
                prediction_type="executive_hiring_pattern_activation",
                probability=min(0.80, 0.32 + score * 0.42),
                horizon_days=90,
                rationale="Observed hiring/leadership patterns indicate active executive search behavior may surface soon.",
                evidence={**evidence, "reasons": reasons},
                observed_openings=int(payload.get("opening_count") or 0),
                resolution_status="open",
            )
        )

    return predictions



def update_strategic_layer(store: JobIntelStore, *, persist: bool = True, limit: int = 50) -> StrategicAnalysisResult:
    companies = store.fetch_company_intelligence(limit=limit)
    events_by_company = _event_map(store.fetch_company_events(limit=200))
    signal_records: list[StrategicSignalRecord] = []
    prediction_records: list[StrategicPredictionRecord] = []
    top_companies: list[dict[str, Any]] = []
    analysis_date = datetime.now(timezone.utc).date().isoformat()

    for row in companies:
        payload = _company_payload(row)
        company_key = payload["company"].lower()
        if not company_key:
            continue
        events = events_by_company.get(company_key, [])
        signals = _lower_set(payload.get("signal_list") or [])
        score, reasons = _company_prediction_score(payload, events)
        if not score and not signals:
            continue

        if persist:
            if signals:
                for signal_type in sorted(signals):
                    signal_records.append(
                        StrategicSignalRecord(
                            company=payload["company"],
                            signal_type=signal_type,
                            confidence=min(0.98, 0.55 + score * 0.35),
                            horizon_days=90 if signal_type in {"hiring_activity", "leadership_change"} else 180,
                            probability=min(0.95, 0.45 + score * 0.40),
                            rationale=f"Company emitted {signal_type.replace('_', ' ')} signals and strategic score={score:.2f}.",
                            evidence={
                                "signals": sorted(signals),
                                "reasons": reasons,
                                "opening_count": payload.get("opening_count", 0),
                                "career_urls": payload.get("career_urls") or [],
                            },
                            source="strategic",
                        )
                    )
                    store.record_strategic_signal(
                        payload["company"],
                        signal_type,
                        confidence=min(0.98, 0.55 + score * 0.35),
                        horizon_days=90 if signal_type in {"hiring_activity", "leadership_change"} else 180,
                        probability=min(0.95, 0.45 + score * 0.40),
                        rationale=f"Company emitted {signal_type.replace('_', ' ')} signals and strategic score={score:.2f}.",
                        evidence={
                            "signals": sorted(signals),
                            "reasons": reasons,
                            "opening_count": payload.get("opening_count", 0),
                            "career_urls": payload.get("career_urls") or [],
                        },
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

        if score >= 0.45:
            top_companies.append(
                {
                    "company": payload["company"],
                    "score": round(score, 2),
                    "signals": sorted(signals),
                    "openings": payload.get("opening_count", 0),
                    "category": payload.get("target_category") or "",
                    "website": payload.get("website") or "",
                    "career_urls": payload.get("career_urls") or [],
                    "reasons": reasons,
                }
            )

    top_companies.sort(key=lambda item: (item["score"], item["openings"]), reverse=True)
    return StrategicAnalysisResult(signals=signal_records, predictions=prediction_records, top_companies=top_companies[:limit])



def build_strategic_report(store: JobIntelStore, *, limit: int = 10) -> str:
    analysis = update_strategic_layer(store, persist=True, limit=500)
    if not analysis.top_companies and not analysis.predictions:
        return "[SILENT]"

    lines = ["*Strategic opportunity report*", ""]
    if analysis.top_companies:
        lines.append("*Emerging target companies*")
        for idx, item in enumerate(analysis.top_companies[:limit], 1):
            lines.append(
                f"{idx}. *{item['company']}* — score={item['score']:.2f}, openings={item['openings']}, category={item['category']}"
            )
            if item.get("signals"):
                lines.append(f"   Signals: {', '.join(item['signals'])}")
            if item.get("reasons"):
                lines.append(f"   Why it matters: {', '.join(item['reasons'][:3])}")
            if item.get("career_urls"):
                lines.append(f"   Career pages: {', '.join(item['career_urls'][:2])}")
        lines.append("")

    if analysis.predictions:
        lines.append("*Likely future executive openings*")
        for idx, prediction in enumerate(sorted(analysis.predictions, key=lambda item: item.probability, reverse=True)[:limit], 1):
            horizon = f"{prediction.horizon_days // 30}-month" if prediction.horizon_days >= 60 else f"{prediction.horizon_days}-day"
            lines.append(
                f"{idx}. *{prediction.company}* — {prediction.prediction_type.replace('_', ' ')} | probability={prediction.probability:.0%} | horizon={horizon}"
            )
            lines.append(f"   {prediction.rationale}")
        lines.append("")

    lines.append("*Strategic recommendations*")
    if analysis.top_companies:
        top = analysis.top_companies[0]
        lines.append(
            f"- Prioritize {top['company']} as an active watchlist target; signals indicate {', '.join(top['signals'][:3]) or 'material change'} and potential executive opportunity creation."
        )
    if any(pred.prediction_type == "vp_product_hiring_3_6_months" for pred in analysis.predictions):
        lines.append("- Prepare tailored VP/Director product outreach for companies with product transformation and leadership change signals.")
    if any(pred.prediction_type == "monetization_function_scaling" for pred in analysis.predictions):
        lines.append("- Emphasize monetization and P&L ownership in outreach where growth and subscription/platform signals are present.")
    if any(pred.prediction_type == "new_market_executive_opportunity" for pred in analysis.predictions):
        lines.append("- Watch companies expanding into new markets for hidden executive openings before public posting.")
    if len(lines) == 4:
        lines.append("- No high-confidence strategic signals yet; continue monitoring target companies and events.")
    return "\n".join(lines).rstrip()
