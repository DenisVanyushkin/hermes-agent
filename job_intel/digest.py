from __future__ import annotations

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


def format_daily_digest(
    items: list[tuple[Vacancy, Evaluation]],
    *,
    title: str = "Daily executive job digest",
    operator_footer: str | None = None,
) -> str:
    if not items and not operator_footer:
        return "[SILENT]"
    lines = [f"*{title}*", f"Matches: {len(items)}", ""]
    for idx, (vacancy, evaluation) in enumerate(items, 1):
        lines.append(f"{idx}. {format_vacancy_summary(vacancy, evaluation)}")
        lines.append("")
    if operator_footer:
        lines.append("—")
        lines.append(operator_footer)
    return "\n".join(lines).rstrip()


def format_enrichment_questions(questions: list[str]) -> str:
    if not questions:
        return "[SILENT]"
    lines = ["*Candidate enrichment questions*", ""]
    for idx, q in enumerate(questions, 1):
        lines.append(f"{idx}. {q}")
    return "\n".join(lines)
