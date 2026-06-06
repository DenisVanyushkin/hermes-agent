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
    scoring_model_version: str | None = None,
    per_source_funnel: list[dict[str, object]],
    top_scored: list[tuple[Vacancy, Evaluation]],
    top_rejected: list[tuple[Vacancy, Evaluation]],
    rejected_reason_counts: dict[str, int],
    market_titles: list[tuple[str, int]],
    market_countries: list[tuple[str, int]],
    market_companies: list[tuple[str, int]],
    decision_counts: dict[str, int],
    top_near_miss: list[tuple[Vacancy, Evaluation]],
    vacancy_card_candidates: int = 0,
    vacancy_card_sent: int = 0,
    vacancy_card_suppressed: int = 0,
    operator_footer: str | None = None,
    dual_scores: dict[str, dict[str, object]] | None = None,
) -> str:
    try:
        return _format_executive_opportunity_report_inner(
            run_id=run_id,
            title=title,
            scoring_model_version=scoring_model_version,
            per_source_funnel=per_source_funnel,
            top_scored=top_scored,
            top_rejected=top_rejected,
            rejected_reason_counts=rejected_reason_counts,
            market_titles=market_titles,
            market_countries=market_countries,
            market_companies=market_companies,
            decision_counts=decision_counts,
            top_near_miss=top_near_miss,
            vacancy_card_candidates=vacancy_card_candidates,
            vacancy_card_sent=vacancy_card_sent,
            vacancy_card_suppressed=vacancy_card_suppressed,
            operator_footer=operator_footer,
            dual_scores=dual_scores,
        )
    except Exception as exc:
        return f"*Daily Executive Review* — report generation error: {exc}"


def _format_executive_opportunity_report_inner(
    *,
    run_id: int,
    title: str,
    scoring_model_version: str | None = None,
    per_source_funnel: list[dict[str, object]],
    top_scored: list[tuple[Vacancy, Evaluation]],
    top_rejected: list[tuple[Vacancy, Evaluation]],
    rejected_reason_counts: dict[str, int],
    market_titles: list[tuple[str, int]],
    market_countries: list[tuple[str, int]],
    market_companies: list[tuple[str, int]],
    decision_counts: dict[str, int],
    top_near_miss: list[tuple[Vacancy, Evaluation]],
    vacancy_card_candidates: int = 0,
    vacancy_card_sent: int = 0,
    vacancy_card_suppressed: int = 0,
    operator_footer: str | None = None,
    dual_scores: dict[str, dict[str, object]] | None = None,
) -> str:
    import datetime as _dt

    today = _dt.date.today().strftime("%d %b %Y")
    lines: list[str] = [f"📊 *Daily Executive Review* — {today}", f"`run_id={run_id}` | `scoring_model_version={scoring_model_version or 'unknown'}`", ""]

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
    lines.append(f"• Vacancy card candidates: {int(vacancy_card_candidates or 0)}")
    lines.append(f"• Vacancy card sent: {int(vacancy_card_sent or 0)}")
    lines.append(f"• Vacancy card suppressed: {int(vacancy_card_suppressed or 0)}")
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

    lines.append(f"*Top Opportunities* ({len(good_rows)} surfaced as cards)")
    if good_rows:
        preview = []
        for vacancy, _evaluation in good_rows[:3]:
            company = (getattr(vacancy, "company", None) or "Unknown").strip()
            job_title = (getattr(vacancy, "title", None) or "Unknown").strip()
            preview.append(f"{company} — {job_title}")
        lines.append("• " + "\n• ".join(preview))
    else:
        lines.append("none")
    lines.append("")

    # --- Near Miss Queue ---
    near_miss_limit = 3
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
        empty_sources = [
            str(row.get("source") or "")
            for row in per_source_funnel
            if int(row.get("found") or 0) == 0
        ]
        lines.append("*Sources*")
        if empty_sources:
            for s in empty_sources:
                lines.append(f"  ⚠️ {s}: empty")
        if operator_footer:
            lines.append(f"  ⚠️ {operator_footer}")
        if not empty_sources and not operator_footer:
            lines.append("  no problems detected")
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


def format_health_warning(problems: list[str]) -> str:
    """Compact health warning message — only sent when something is wrong."""
    from datetime import date
    lines = [
        f"⚠️ *System Health Warning* — {date.today().strftime('%d %b %Y')}",
        "",
    ]
    for problem in problems:
        lines.append(f"• {problem}")
    lines.append("")
    lines.append("_Check logs or run `job_intel health` for details._")
    return "\n".join(lines)


def format_weekly_source_quality(rows: list[dict], week_label: str | None = None) -> str:
    """
    Compact weekly source quality table.
    rows keys: source, found, exec_detected, strong_fit, potential_fit, near_miss,
               accepted, notified, runs_ok, runs_total, enabled, last_status
    Only renders enabled sources (enabled != 0).
    """
    from datetime import date
    week = week_label or f"Week of {date.today().strftime('%d %b %Y')}"
    lines = [f"📈 *Weekly Source Quality* — {week}", ""]

    enabled_rows = [r for r in rows if r.get("enabled", 1) != 0]
    if not enabled_rows:
        lines.append("No data for enabled sources this week.")
        return "\n".join(lines)

    lines.append("```")
    lines.append(f"{'Source':<20} {'Found':>6} {'Exec':>5} {'Strong':>7} {'Pot':>5} {'Miss':>5} {'Accept':>7} {'Rel%':>5}")
    lines.append("-" * 62)
    for r in sorted(enabled_rows, key=lambda x: -(x.get("strong_fit", 0) + x.get("potential_fit", 0))):
        src = r["source"][:20]
        runs_ok = r.get("runs_ok", 0)
        runs_total = max(r.get("runs_total", 1), 1)
        reliability = int(100 * runs_ok / runs_total)
        flag = " ⚠" if r.get("last_status") in ("error", "blocked") else ""
        lines.append(
            f"{src:<20} {r.get('found', 0):>6} {r.get('exec_detected', 0):>5} "
            f"{r.get('strong_fit', 0):>7} {r.get('potential_fit', 0):>5} "
            f"{r.get('near_miss', 0):>5} {r.get('accepted', 0):>7} {reliability:>4}%{flag}"
        )
    lines.append("```")
    lines.append("")

    # Top sources by useful opportunities
    useful = [
        (r["source"], r.get("strong_fit", 0) + r.get("potential_fit", 0) + r.get("near_miss", 0))
        for r in enabled_rows
        if r.get("strong_fit", 0) + r.get("potential_fit", 0) > 0
    ]
    if useful:
        useful.sort(key=lambda x: -x[1])
        lines.append("*Top sources by opportunity:*")
        for src, n in useful[:3]:
            lines.append(f"• {src}: {n} useful opportunities")
        lines.append("")

    # Sources to watch
    watch = [
        r for r in enabled_rows
        if r.get("runs_total", 0) >= 3
        and r.get("runs_ok", r.get("runs_total", 1)) / max(r.get("runs_total", 1), 1) < 0.6
    ]
    if watch:
        lines.append("*Sources to watch:*")
        for r in watch:
            lines.append(f"• {r['source']}: {r.get('runs_ok', 0)}/{r.get('runs_total', 0)} runs ok")

    return "\n".join(lines)
