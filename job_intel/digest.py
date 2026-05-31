from __future__ import annotations

from collections import Counter
import re

from .models import Evaluation, Vacancy


def format_vacancy_summary(vacancy: Vacancy, evaluation: Evaluation, *, source_label: str | None = None) -> str:
    label = source_label or vacancy.source
    lines = [
        f"*{vacancy.company}* — {vacancy.title}",
        f"Location: {vacancy.location} | Source: {label} | Score: {evaluation.score} ({evaluation.tier}) | Recommendation: {evaluation.recommendation}",
        f"Why matched: {', '.join(evaluation.matched_signals) or 'High-level strategic fit'}",
    ]
    if evaluation.concerns:
        lines.append(f"Concerns: {', '.join(evaluation.concerns)}")
    if vacancy.salary:
        lines.append(f"Compensation signal: {vacancy.salary}")
    lines.append(f"URL: {vacancy.url}")
    return "\n".join(lines)


def _executive_confidence(vacancy: Vacancy) -> str:
    title = (vacancy.title or "").lower()
    if any(token in title for token in ("chief", "cpo", "vp", "vice president")):
        return "high"
    if any(token in title for token in ("director", "head of", "general manager", "gm")):
        return "medium"
    return "low"


_BREAKDOWN_LABELS = {
    "executive_visibility": "executive title / leadership",
    "growth_signal": "growth/strategy leadership",
    "product_ownership": "product ownership / strategy",
    "monetization_responsibility": "monetization",
    "PnL_ownership": "P&L ownership",
    "B2C_platform": "B2C/platform/subscription",
    "mobile_product": "mobile/app product",
    "fintech_or_telecom": "fintech/telecom adjacency",
    "org_transformation": "org/product transformation",
    "international_team": "international scope",
    "remote_friendly": "remote-friendly",
    "AI_or_modern_tech": "AI/modern tech",
    "target_company": "target company",
    "career_page_signal": "career page activity",
    "outsourcing_company": "outsourcing/outstaffing",
    "delivery_only": "delivery-only culture",
    "pure_project_management": "PM/Scrum track",
    "enterprise_bureaucracy": "bureaucracy",
    "low_autonomy": "low autonomy",
    "weak_product_culture": "weak product culture",
    "generic_remote_noise": "generic remote board noise",
}


def _format_breakdown(evaluation: Evaluation, *, limit: int = 6) -> list[str]:
    items: list[tuple[int, str]] = []
    for key, value in (evaluation.raw_breakdown or {}).items():
        try:
            points = int(value)
        except Exception:
            continue
        if points == 0:
            continue
        label = _BREAKDOWN_LABELS.get(key, key)
        items.append((points, label))
    items.sort(key=lambda x: abs(x[0]), reverse=True)
    out: list[str] = []
    for points, label in items[: max(0, int(limit))]:
        sign = "+" if points > 0 else "-"
        out.append(f"{sign} {label} ({points})")
    return out


def reject_reason_bucket(vacancy: Vacancy, evaluation: Evaluation, *, duplicate: bool) -> str:
    if duplicate:
        return "duplicate"
    title = (vacancy.title or "").lower()
    reasons = " ".join(evaluation.reasons or []).lower()
    concerns = " ".join(evaluation.concerns or []).lower()
    breakdown = evaluation.raw_breakdown or {}

    if "restricted geography" in concerns or "restricted geography" in reasons or "sanctions" in reasons:
        return "geography mismatch"
    if "role title is outside executive product path" in reasons or any(tok in title for tok in ("scrum master", "project manager", "delivery manager")):
        return "title mismatch"
    if any(tok in concerns for tok in ("generic remote noise", "delivery/pm title")):
        return "title mismatch"

    # Seniority.
    if not any(tok in title for tok in ("vp", "vice president", "director", "head of", "chief", "cpo", "gm", "general manager")):
        if "product manager" in title or "product owner" in title:
            return "insufficient seniority"
        if evaluation.score < 50:
            return "insufficient seniority"

    # Industry mismatch proxy: missing sector signals + low score.
    if int(breakdown.get("fintech_or_telecom", 0) or 0) == 0 and int(breakdown.get("B2C_platform", 0) or 0) == 0 and int(breakdown.get("AI_or_modern_tech", 0) or 0) == 0 and evaluation.score < 55:
        return "industry mismatch"

    # Missing core executive evidence buckets (heuristics; we do not change scoring here).
    if int(breakdown.get("product_ownership", 0) or 0) == 0 and evaluation.score < 70:
        return "no product ownership"
    if int(breakdown.get("PnL_ownership", 0) or 0) == 0 and evaluation.score < 80:
        return "no P&L"

    if "insufficient fit" in reasons or evaluation.score < 60:
        return "low confidence"
    return "other"




def _unknown_fields(vacancy: Vacancy) -> list[str]:
    text = (vacancy.description or '').lower()
    out: list[str] = []
    loc = (vacancy.location or '').strip().lower()
    if not loc or loc == 'unknown':
        out.append('location_unknown')
    if not vacancy.salary:
        out.append('salary_unknown')
    if 'p&l' not in text and 'profit and loss' not in text:
        out.append('pnl_unknown')
    return out

def _country_from_location(location: str) -> str:
    text = (location or "").strip()
    if not text or text.lower() == "unknown":
        return "Unknown"
    if text.lower() == "remote":
        return "Remote"
    if "," in text:
        tail = text.rsplit(",", 1)[-1].strip()
        return tail or text
    return text


BLOCKER_REASONS = {
    "non_product_role", "low_seniority", "blocked_geography",
    "onsite_requirement_mismatch", "duplicate", "sales_role",
    "marketing_role", "business_development_role", "analyst_role",
    "low_company_tier",
}

UNKNOWN_REASONS = {
    "salary_unknown", "pnl_unknown", "company_score_unknown",
    "hiring_likelihood_unknown", "location_unknown",
}

# Recommendation values considered "strong" (green indicator).
_STRONG_RECS = {"exceptional_fit", "strong_fit"}
# Recommendation values considered "potential" (blue indicator).
_POTENTIAL_RECS = {"potential_fit", "possible_fit"}
# Recommendation values considered "near miss".
_NEAR_MISS_RECS = {"near_miss"}


def format_executive_opportunity_report(
    *,
    run_id: int,
    title: str,
    per_source_funnel: list[dict[str, object]],
    top_scored: list[tuple[Vacancy, Evaluation]],
    top_rejected: list[tuple[Vacancy, Evaluation]],
    rejected_reason_counts: dict[str, int],
    market_titles: list[tuple[str, int]],
    market_countries: list[tuple[str, int]],
    market_companies: list[tuple[str, int]],
    decision_counts: dict[str, int],
    top_near_miss: list[tuple[Vacancy, Evaluation]],
    operator_footer: str | None = None,
    dual_scores: dict[str, dict[str, object]] | None = None,
) -> str:
    try:
        return _format_executive_opportunity_report_inner(
            run_id=run_id,
            title=title,
            per_source_funnel=per_source_funnel,
            top_scored=top_scored,
            top_rejected=top_rejected,
            rejected_reason_counts=rejected_reason_counts,
            market_titles=market_titles,
            market_countries=market_countries,
            market_companies=market_companies,
            decision_counts=decision_counts,
            top_near_miss=top_near_miss,
            operator_footer=operator_footer,
            dual_scores=dual_scores,
        )
    except Exception as exc:
        return f"*Daily Executive Review* — report generation error: {exc}"


def _format_executive_opportunity_report_inner(
    *,
    run_id: int,
    title: str,
    per_source_funnel: list[dict[str, object]],
    top_scored: list[tuple[Vacancy, Evaluation]],
    top_rejected: list[tuple[Vacancy, Evaluation]],
    rejected_reason_counts: dict[str, int],
    market_titles: list[tuple[str, int]],
    market_countries: list[tuple[str, int]],
    market_companies: list[tuple[str, int]],
    decision_counts: dict[str, int],
    top_near_miss: list[tuple[Vacancy, Evaluation]],
    operator_footer: str | None = None,
    dual_scores: dict[str, dict[str, object]] | None = None,
) -> str:
    import datetime as _dt

    today = _dt.date.today().strftime("%d %b %Y")
    lines: list[str] = [f"📊 *Daily Executive Review* — {today}", ""]

    # --- Summary ---
    strong = int(decision_counts.get("strong_fit") or 0) + int(decision_counts.get("exceptional_fit") or 0)
    potential = int(decision_counts.get("potential_fit") or 0) + int(decision_counts.get("possible_fit") or 0)
    near = int(decision_counts.get("near_miss") or 0)
    rej = int(decision_counts.get("reject") or 0)
    total_found = sum(int(row.get("found") or 0) for row in (per_source_funnel or []))

    lines.append("*Summary*")
    lines.append(f"• Found: {total_found}")
    lines.append(f"• Strong fit: {strong}")
    lines.append(f"• Potential fit: {potential}")
    lines.append(f"• Near miss: {near} (review queue)")
    lines.append(f"• Rejected: {rej}")
    lines.append("")

    # --- Top Opportunities ---
    # Combine strong+potential from top_scored, then limit to 10.
    good_rows: list[tuple[Vacancy, Evaluation]] = []
    for v, e in (top_scored or []):
        rec = str(getattr(e, "recommendation", "") or "")
        if rec in _STRONG_RECS or rec in _POTENTIAL_RECS:
            good_rows.append((v, e))
        if len(good_rows) >= 10:
            break

    lines.append("*Top Opportunities*")
    if not good_rows:
        lines.append("none")
    else:
        for vacancy, evaluation in good_rows:
            rec = str(getattr(evaluation, "recommendation", "") or "")
            indicator = "🟢" if rec in _STRONG_RECS else "🔵"
            company = (getattr(vacancy, "company", None) or "Unknown").strip()
            job_title = (getattr(vacancy, "title", None) or "Unknown").strip()
            location = (getattr(vacancy, "location", None) or "Unknown").strip()
            source = (getattr(vacancy, "source", None) or "").strip()
            score = int(getattr(evaluation, "score", 0) or 0)
            url = getattr(vacancy, "url", None) or ""

            lines.append(f"{indicator} *{company}* — {job_title}")
            lines.append(f"   {location} | score {score} | {source}")

            signals = list(getattr(evaluation, "matched_signals", None) or [])
            if signals:
                lines.append(f"   ✓ {', '.join(signals[:3])}")

            concerns = list(getattr(evaluation, "concerns", None) or [])
            if concerns:
                lines.append(f"   ⚠ {concerns[0]}")

            if url:
                lines.append(f"   {url}")
            lines.append("")

    # --- Near Miss Queue ---
    near_miss_limit = 5 if good_rows else 10
    lines.append(f"*Near Miss Queue* ({near} total)")
    if not top_near_miss:
        lines.append("none")
    else:
        for vacancy, evaluation in top_near_miss[:near_miss_limit]:
            company = (getattr(vacancy, "company", None) or "Unknown").strip()
            job_title = (getattr(vacancy, "title", None) or "Unknown").strip()
            concerns = list(getattr(evaluation, "concerns", None) or [])
            reasons_list = list(getattr(evaluation, "reasons", None) or [])
            top_reason = concerns[0] if concerns else (reasons_list[0] if reasons_list else "review needed")
            lines.append(f"• {company} — {job_title} | {top_reason}")
    lines.append("")

    # --- Why Rejected (blockers only, top 6) ---
    blocker_counts = {
        r: c for r, c in (rejected_reason_counts or {}).items()
        if r in BLOCKER_REASONS
    }
    if blocker_counts:
        lines.append("*Why Rejected* (blockers)")
        for reason, cnt in sorted(blocker_counts.items(), key=lambda x: x[1], reverse=True)[:6]:
            lines.append(f"• {reason}: {cnt}")
        lines.append("")

    # --- Data Gaps ---
    unknown_counts = {
        r: c for r, c in (rejected_reason_counts or {}).items()
        if r in UNKNOWN_REASONS
    }
    total_unknowns = sum(unknown_counts.values())
    if total_unknowns > 0:
        top_unknowns = sorted(unknown_counts.items(), key=lambda x: x[1], reverse=True)[:3]
        unknowns_str = ", ".join(f"{r} {c}" for r, c in top_unknowns)
        lines.append(f"*Data Gaps* ({total_unknowns} unknowns): {unknowns_str}")
        lines.append("")

    # --- Sources ---
    if per_source_funnel:
        ok_sources = [
            str(row.get("source") or "")
            for row in per_source_funnel
            if int(row.get("found") or 0) > 0
        ]
        empty_sources = [
            str(row.get("source") or "")
            for row in per_source_funnel
            if int(row.get("found") or 0) == 0
        ]
        lines.append("*Sources*")
        if ok_sources:
            lines.append(f"  ok: {', '.join(ok_sources)}")
        if empty_sources:
            for s in empty_sources:
                lines.append(f"  ⚠️ {s}: empty")
        # Surface any operator-level issues (source errors/auth failures) from the footer.
        if operator_footer:
            lines.append(f"  ⚠️ {operator_footer}")
        lines.append("")

    return "\n".join(lines).rstrip()

def format_daily_digest(
    items: list[tuple[Vacancy, Evaluation]],
    *,
    title: str = "Daily executive job digest",
    operator_footer: str | None = None,
    technical_footer: str | None = None,
) -> str:
    if not items and not operator_footer and not technical_footer:
        return "[SILENT]"
    lines = [f"*{title}*", f"Matches: {len(items)}", ""]
    for idx, (vacancy, evaluation) in enumerate(items, 1):
        lines.append(f"{idx}. {format_vacancy_summary(vacancy, evaluation)}")
        lines.append("")
    if operator_footer:
        lines.append("—")
        lines.append(operator_footer)
    if technical_footer:
        if operator_footer:
            lines.append("")
        lines.append(technical_footer)
    return "\n".join(lines).rstrip()


def format_enrichment_questions(questions: list[str]) -> str:
    if not questions:
        return "[SILENT]"
    lines = ["*Candidate enrichment questions*", ""]
    for idx, q in enumerate(questions, 1):
        lines.append(f"{idx}. {q}")
    return "\n".join(lines)
