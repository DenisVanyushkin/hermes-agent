"""Gateway execution bridge for the recruiter decision-support pipeline.

Mirrors the engineering bounded-rework-loop helper contract: takes the raw
user message, runs the decision-support flow (provider-backed when the real
provider fuse is open), and returns a helper payload whose
``report.final_response.text`` is the user-facing terminal response.
"""

from __future__ import annotations

import re
from typing import Any, Mapping

from hermes_cli.config import cfg_get
from hermes_cli.recruiter_career_facts import CareerFactsBundle, load_career_facts
from hermes_cli.recruiter_decision_flow import (
    DecisionSupportReport,
    DecisionSupportRequest,
    run_recruiter_decision_support_flow,
)
from hermes_cli.recruiter_decision_modules import DecisionBundleStatus, DecisionModuleStatus

RECRUITER_PIPELINE_ID = "recruiter_decision_support_pipeline"
RECRUITER_DECISION_HELPER = "recruiter_decision_support_flow"
RECRUITER_PRIMARY_SUBAGENT_ID = "general_operator"

_URL_PATTERN = re.compile(r"https?://[^\s<>()\"']+")

_MODULE_TITLES = {
    "vacancy_assessment": "Vacancy assessment",
    "company_assessment": "Company assessment",
    "company_risk_register": "Company risk register",
    "recommendation": "Recommendation",
    "positioning_summary": "Positioning summary",
    "evidence_backed_supporting_claims": "Evidence-backed supporting claims",
    "claims_to_avoid": "Claims to avoid",
    "questions_to_ask": "Questions to ask",
    "manual_review_warnings": "Manual review warnings",
}


def build_decision_request_from_message(
    user_message: str,
    career_facts: CareerFactsBundle | None = None,
    conversation_context: str | None = None,
) -> DecisionSupportRequest:
    """Build a safe draft-only decision request from a raw chat message.

    A bare follow-up ("оцени вакансию" replying under a job alert) carries no
    URL itself, so the vacancy link falls back to the most recent URL in the
    thread context, and the trimmed context is exposed to modules as
    role_context.
    """
    text = str(user_message or "").strip()
    context = str(conversation_context or "").strip()
    urls = _URL_PATTERN.findall(text)
    if not urls and context:
        # Most recent thread URL first.
        urls = list(reversed(_URL_PATTERN.findall(context)))
    vacancy_source = None
    if urls:
        vacancy_source = {
            "source_type": "vacancy_url",
            "source_id": urls[0].rstrip(".,;)"),
            "approved": True,
        }
    bundle = career_facts if career_facts is not None else CareerFactsBundle()
    return DecisionSupportRequest(
        prompt=text,
        vacancy_source=vacancy_source,
        career_fact_sources=list(bundle.sources),
        career_facts=bundle.facts,
        candidate_preferences=bundle.preferences,
        role_context=(f"Recent conversation context:\n{context[-2500:]}" if context else None),
    )


def execute_recruiter_decision_support_helper(
    *,
    config: Mapping[str, Any] | None = None,
    user_message: str = "",
    executor_factory: Any = None,
    conversation_context: str | None = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Registered controller helper for the recruiter pipeline."""
    try:
        facts_bundle = load_career_facts()
    except Exception as exc:
        facts_bundle = CareerFactsBundle(warnings=[f"career facts loader failed: {type(exc).__name__}"])
    request = build_decision_request_from_message(
        user_message,
        career_facts=facts_bundle,
        conversation_context=conversation_context,
    )
    fetch_warnings = _enrich_vacancy_source(request)

    module_executor = None
    executor_error: str | None = None
    if _real_provider_execution_allowed(config):
        try:
            if executor_factory is not None:
                module_executor = executor_factory()
            else:
                from hermes_cli.recruiter_decision_provider_executor import (
                    build_recruiter_decision_provider_executor,
                )

                module_executor = build_recruiter_decision_provider_executor()
        except Exception as exc:
            executor_error = f"{type(exc).__name__}: {exc}"

    report = run_recruiter_decision_support_flow(request, module_executor=module_executor)
    text = format_decision_report_text(
        report,
        executor_error=executor_error,
        extra_notes=list(facts_bundle.warnings) + fetch_warnings,
    )

    status = "executed"
    blocked_reason = None
    if report.status is DecisionBundleStatus.BLOCKED:
        blocked_reason = _first_block_reason(report)

    return {
        "status": status,
        "blocked_reason": blocked_reason,
        "completion_allowed": True,
        "candidate_complete": True,
        "user_action_required": False,
        "subagent_runs": _subagent_runs(module_executor),
        "usage_summary": _usage_summary(module_executor),
        "report": {
            "status": report.status.value,
            "routing": {
                "selected_pipeline_id": RECRUITER_PIPELINE_ID,
                "router_status": "selected",
            },
            "completion": {
                "final_verdict": report.status.value,
                "candidate_complete": True,
                "completion_allowed": True,
                "blocked_reason": blocked_reason,
            },
            "final_response": {"text": text},
            "decision_support": report.to_dict(),
            "subagent_runs": _subagent_runs(module_executor),
        },
    }


def _enrich_vacancy_source(request: DecisionSupportRequest) -> list[str]:
    """Fetch the actual posting content into the vacancy source (read-only)."""
    source = request.vacancy_source
    if not isinstance(source, dict) or not source.get("source_id"):
        return []
    try:
        from hermes_cli.recruiter_vacancy_fetch import fetch_vacancy_details

        details = fetch_vacancy_details(str(source["source_id"]))
    except Exception as exc:
        return [f"vacancy page could not be fetched ({type(exc).__name__}); assessment is limited"]
    status = str(details.get("fetch_status") or "")
    if status.startswith("ok"):
        for key, value in details.items():
            if key != "fetch_status":
                source.setdefault(key, value)
        return []
    return ["vacancy page could not be fetched; assessment is limited to the link and thread context"]


def format_decision_report_text(
    report: DecisionSupportReport,
    *,
    executor_error: str | None = None,
    extra_notes: list[str] | None = None,
) -> str:
    lines: list[str] = []
    lines.append(f"*Company & Vacancy Decision Support* — {_status_label(report.status)}")
    if report.preset_id:
        lines.append(f"_Requested scope: {report.preset_id}_")
    lines.append("")

    for module_id in report.requested_outputs:
        result = report.modules.get(module_id)
        if result is None:
            continue
        title = _MODULE_TITLES.get(module_id, module_id.replace("_", " "))
        if result.status is DecisionModuleStatus.READY:
            lines.append(f"*{title}*")
            lines.extend(_render_payload(result.payload))
        elif result.status is DecisionModuleStatus.INCONCLUSIVE:
            lines.append(f"*{title}* — inconclusive (low confidence)")
            lines.extend(_render_payload(result.payload))
        else:
            reason = result.block_reason or "missing required inputs"
            lines.append(f"*{title}* — unavailable: {_humanize(reason)}")
        for warning in _sanitize_notes(result.warnings):
            lines.append(f"  ⚠ {warning}")
        lines.append("")

    if executor_error:
        lines.append("⚠ Automated analysis was unavailable for this run; results are limited.")
    for warning in _sanitize_notes(list(report.warnings) + list(extra_notes or [])):
        lines.append(f"⚠ {warning}")
    for error in _sanitize_notes(report.errors):
        lines.append(f"✖ {error}")

    lines.append("")
    lines.append("_Draft for manual review only — nothing was sent, submitted, or saved anywhere._")
    return "\n".join(line for line in lines if line is not None).strip()


def _render_payload(payload: Mapping[str, Any] | None, *, indent: str = "") -> list[str]:
    if not isinstance(payload, Mapping) or not payload:
        return []
    lines: list[str] = []
    for key, value in payload.items():
        label = _humanize(str(key))
        if isinstance(value, Mapping):
            lines.append(f"{indent}• {label}:")
            lines.extend(_render_payload(value, indent=indent + "    "))
        elif isinstance(value, (list, tuple)):
            if not value:
                continue
            if all(isinstance(item, str) for item in value):
                items = _sanitize_notes(list(value))[:12]
                if not items:
                    continue
                lines.append(f"{indent}• {label}:")
                lines.extend(f"{indent}    - {item}" for item in items)
            else:
                lines.append(f"{indent}• {label}:")
                for item in value[:8]:
                    if isinstance(item, Mapping):
                        rendered = "; ".join(f"{_humanize(str(k))}: {v}" for k, v in item.items() if not isinstance(v, (Mapping, list)))
                        lines.append(f"{indent}    - {rendered}")
                    else:
                        lines.append(f"{indent}    - {item}")
        elif value not in (None, ""):
            if isinstance(value, str) and any(term in value.lower() for term in _INTERNAL_NOTE_TERMS):
                continue
            lines.append(f"{indent}• {label}: {value}")
    return lines


def _humanize(value: str) -> str:
    return value.replace("_", " ").strip()


_INTERNAL_NOTE_TERMS = ("provider", "executor", "preflight", "pipeline", "subagent", "packet", "module_executor")
_GENERIC_LIMITATION_NOTE = "some analysis steps could not run automatically; treat gaps as unknowns"


def _sanitize_notes(notes: list[str]) -> list[str]:
    """Keep user-safe notes verbatim; collapse internal-machinery notes into one generic line."""
    cleaned: list[str] = []
    internal_seen = False
    for note in notes:
        lowered = str(note).lower()
        if any(term in lowered for term in _INTERNAL_NOTE_TERMS):
            internal_seen = True
            continue
        cleaned.append(_humanize(str(note)))
    if internal_seen and _GENERIC_LIMITATION_NOTE not in cleaned:
        cleaned.append(_GENERIC_LIMITATION_NOTE)
    return cleaned


def _status_label(status: DecisionBundleStatus) -> str:
    return {
        DecisionBundleStatus.READY: "ready",
        DecisionBundleStatus.BLOCKED: "blocked",
        DecisionBundleStatus.INCONCLUSIVE: "inconclusive",
    }.get(status, status.value)


def _first_block_reason(report: DecisionSupportReport) -> str | None:
    for module_id in report.requested_outputs:
        result = report.modules.get(module_id)
        if result is not None and result.block_reason:
            return result.block_reason
    return None


def _real_provider_execution_allowed(config: Mapping[str, Any] | None) -> bool:
    return bool(cfg_get(config, "pipelines", "execution", "allow_real_provider_execution", default=False))


def _subagent_runs(module_executor: Any) -> list[dict[str, Any]]:
    if module_executor is None:
        return []
    return [
        {
            "subagent_id": RECRUITER_PRIMARY_SUBAGENT_ID,
            "runtime_mode": "real_provider",
            "provider_policy_status": "allowed",
            "real_provider_allowed": True,
        }
    ]


def _usage_summary(module_executor: Any) -> dict[str, Any]:
    executed = 1 if module_executor is not None else 0
    return {
        "total_input_tokens": 0,
        "total_output_tokens": 0,
        "total_tokens": 0,
        "token_sources": [],
        "cache_sources": [],
        "planned_subagent_count": 1,
        "executed_subagent_count": executed,
        "subagent_run_instance_count": executed,
        "execution_round_count": 1,
        "subagent_count": 1,
        "models_used": [],
        "providers_used": [],
    }
