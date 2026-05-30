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


def format_executive_opportunity_report(
    *,
    run_id: int,
    title: str,
    per_source_funnel: list[dict[str, object]],
    top_scored: list[tuple[Vacancy, Evaluation]],
    rejected_reason_counts: dict[str, int],
    market_titles: list[tuple[str, int]],
    market_countries: list[tuple[str, int]],
    market_companies: list[tuple[str, int]],
    decision_counts: dict[str, int],
    top_near_miss: list[tuple[Vacancy, Evaluation]],
    operator_footer: str | None = None,
) -> str:
    lines: list[str] = [f"*{title}*", f"run_id: {run_id}", ""]

    lines.append('*Decision buckets*')
    strong = int(decision_counts.get('strong_fit') or 0)
    potential = int(decision_counts.get('potential_fit') or 0)
    near = int(decision_counts.get('near_miss') or 0)
    rej = int(decision_counts.get('reject') or 0)
    lines.append(f"strong_fit={strong} | potential_fit={potential} | near_miss={near} | reject={rej}")
    lines.append('')

    lines.append("*Per-source funnel*")
    lines.append("source | found | exec_detected | scored | accepted | notified")
    for row in per_source_funnel:
        lines.append(f"{row['source']} | {row['found']} | {row['exec_detected']} | {row['scored']} | {row['accepted']} | {row['notified']}")
    lines.append("")

    lines.append("*Top opportunities (top 20 by score)*")
    if not top_scored:
        lines.append("none")
        lines.append("")
    else:
        for idx, (vacancy, evaluation) in enumerate(top_scored[:20], 1):
            lines.append(
                f"{idx}. {vacancy.company} | {vacancy.title} | {vacancy.location} | {vacancy.source} | score={evaluation.score} | exec_conf={_executive_confidence(vacancy)}"
            )
            breakdown_lines = _format_breakdown(evaluation, limit=6)
            if breakdown_lines:
                lines.append("Breakdown:")
                for b in breakdown_lines:
                    lines.append(f"- {b}")
            if evaluation.concerns:
                lines.append(f"Concerns: {', '.join(evaluation.concerns)}")
            lines.append(f"URL: {vacancy.url}")
            lines.append("")

    lines.append("*Rejected opportunities by reason*")
    if not rejected_reason_counts:
        lines.append("none")
    else:
        for reason, cnt in sorted(rejected_reason_counts.items(), key=lambda x: x[1], reverse=True)[:10]:
            lines.append(f"- {reason}: {cnt}")
    lines.append("")

    lines.append("*Market intelligence (from this run)*")
    if market_titles:
        lines.append("Top titles:")
        for name, cnt in market_titles[:8]:
            lines.append(f"- {name}: {cnt}")
    if market_countries:
        lines.append("Top countries:")
        for name, cnt in market_countries[:8]:
            lines.append(f"- {name}: {cnt}")
    if market_companies:
        lines.append("Top companies:")
        for name, cnt in market_companies[:10]:
            lines.append(f"- {name}: {cnt}")
    lines.append("")
    if top_near_miss and (int(decision_counts.get('strong_fit') or 0) + int(decision_counts.get('potential_fit') or 0) == 0):
        lines.append('*Top near_miss (review queue)*')
        for idx, (vacancy, evaluation) in enumerate(top_near_miss[:10], 1):
            unknown = _unknown_fields(vacancy)
            blockers = list(evaluation.concerns or [])
            lines.append(
                f"{idx}. {vacancy.company} | {vacancy.title} | {vacancy.location} | {vacancy.source} | score={evaluation.score} | unknown={', '.join(unknown) or '-'} | blockers={', '.join(blockers) or '-'}"
            )
            breakdown_lines = _format_breakdown(evaluation, limit=6)
            if breakdown_lines:
                lines.append('Breakdown:')
                for b in breakdown_lines:
                    lines.append(f"- {b}")
            lines.append(f"URL: {vacancy.url}")
            lines.append('')

    if operator_footer:
        lines.append("—")
        lines.append(operator_footer)

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
