from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from .recruiter_context import (
    RecruiterContextRequest,
    RecruiterContextStatus,
    build_recruiter_context,
)
from .recruiter_evaluation_flow import (
    RecruiterEvaluationFlowRequest,
    RecruiterEvaluationFlowStatus,
    build_recruiter_evaluation_flow,
)


_FORBIDDEN_ACTIONS = [
    "call_provider_model",
    "execute_recruiter_skill",
    "send_outbound_message",
    "apply_to_job",
    "write_crm",
    "write_job_intel_db",
    "read_private_file_contents",
    "mutate_live_config",
    "restart_gateway",
]


class RecruiterDryRunStatus(str, Enum):
    READY_FOR_RECRUITER_SKILL_INPUT = "READY_FOR_RECRUITER_SKILL_INPUT"
    CONTEXT_SOURCE_REQUIRED = "CONTEXT_SOURCE_REQUIRED"
    CONTEXT_NOT_FOUND = "CONTEXT_NOT_FOUND"
    CONTEXT_PACKAGE_ERROR = "CONTEXT_PACKAGE_ERROR"
    CONTEXT_FACADE_ERROR = "CONTEXT_FACADE_ERROR"
    CONTEXT_INVALID_REQUEST = "CONTEXT_INVALID_REQUEST"


@dataclass(slots=True)
class RecruiterDryRunRequest:
    vacancy_id: int | None = None
    vacancy_url: str | None = None
    opportunity_id: int | None = None
    job_intel_db_path: str | Path | None = None
    private_career_dir: str | Path | None = None
    repo_root: str | Path | None = None
    stale_after_days: int = 14

    def to_context_request(self) -> RecruiterContextRequest:
        return RecruiterContextRequest(
            vacancy_id=self.vacancy_id,
            vacancy_url=self.vacancy_url,
            opportunity_id=self.opportunity_id,
            job_intel_db_path=self.job_intel_db_path,
            private_career_dir=self.private_career_dir,
            repo_root=self.repo_root,
            stale_after_days=self.stale_after_days,
        )

    def to_dict(self) -> dict[str, Any]:
        return self.to_context_request().to_dict()


@dataclass(slots=True)
class RecruiterDryRunReport:
    status: RecruiterDryRunStatus
    context_status: str
    input: dict[str, Any]
    readiness: dict[str, Any]
    context_packet: dict[str, Any] | None
    evaluation_flow: dict[str, Any] | None = None
    missing_requirements: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    provenance: dict[str, Any] = field(default_factory=dict)
    next_allowed_actions: list[str] = field(default_factory=list)
    forbidden_actions: list[str] = field(default_factory=lambda: list(_FORBIDDEN_ACTIONS))

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        return data


def run_recruiter_context_dry_run(request: RecruiterDryRunRequest) -> RecruiterDryRunReport:
    try:
        packet = build_recruiter_context(request.to_context_request())
    except ValueError as exc:
        return RecruiterDryRunReport(
            status=RecruiterDryRunStatus.CONTEXT_INVALID_REQUEST,
            context_status=RecruiterContextStatus.SOURCE_REQUIRED.value,
            input=request.to_dict(),
            readiness={"ready": False, "reason": "request_validation_failed"},
            context_packet=None,
            evaluation_flow=None,
            errors=[str(exc)],
            missing_requirements=["request_source_identifier"],
            provenance={"writes_performed": False},
            next_allowed_actions=["request_missing_source"],
        )

    return _report_from_packet(request, packet.to_dict())


def _report_from_packet(request: RecruiterDryRunRequest, packet: dict[str, Any]) -> RecruiterDryRunReport:
    context_status = str(packet.get("status") or RecruiterContextStatus.FACADE_ERROR.value)
    status, ready, reason = _map_status(context_status)
    missing_requirements: list[str] = []
    next_allowed_actions: list[str] = []
    warnings = list(packet.get("warnings") or [])
    errors = list(packet.get("errors") or [])

    private_status = str((packet.get("private_context") or {}).get("status") or "")
    if private_status in {RecruiterContextStatus.PRIVATE_CONTEXT_MISSING.value, "PARTIAL"}:
        requirement = "private_career_context_missing" if private_status == RecruiterContextStatus.PRIVATE_CONTEXT_MISSING.value else "private_career_context_partial"
        missing_requirements.append(requirement)
        next_allowed_actions.append("provision_private_career_context")

    machine_score_status = str((packet.get("machine_score") or {}).get("status") or "")
    if machine_score_status == RecruiterContextStatus.MACHINE_SCORE_UNAVAILABLE.value:
        missing_requirements.append("machine_score_unavailable")
        next_allowed_actions.append("run_or_inspect_job_intel_evaluation")

    if context_status in {
        RecruiterContextStatus.VACANCY_NOT_FOUND.value,
        RecruiterContextStatus.OPPORTUNITY_NOT_FOUND.value,
    }:
        missing_requirements.append("requested_context_not_found")
        next_allowed_actions.append("request_missing_source")
    elif context_status == RecruiterContextStatus.PACKAGE_CONTEXT_ERROR.value:
        missing_requirements.append("role_package_context_unavailable")
    elif context_status == RecruiterContextStatus.FACADE_ERROR.value:
        missing_requirements.append("job_intel_read_facade_unavailable")

    if ready:
        next_allowed_actions.append("run_recruiter_vacancy_evaluation_skill_later")
    report = RecruiterDryRunReport(
        status=status,
        context_status=context_status,
        input=request.to_dict(),
        readiness={"ready": ready, "reason": reason},
        context_packet=packet,
        evaluation_flow=None,
        missing_requirements=_dedupe(missing_requirements),
        warnings=_dedupe(warnings),
        errors=_dedupe(errors),
        provenance={**(packet.get("provenance") or {}), "writes_performed": False, "dry_run": True},
        next_allowed_actions=_dedupe(next_allowed_actions),
    )
    return report


def run_recruiter_evaluation_flow_dry_run(
    *,
    prompt: str,
    repo_root: str | Path | None = None,
    private_context_status: str = "PRIVATE_CONTEXT_NOT_INSPECTED",
) -> RecruiterDryRunReport:
    flow_report = build_recruiter_evaluation_flow(
        RecruiterEvaluationFlowRequest(
            prompt=prompt,
            repo_root=repo_root,
            private_context_status=private_context_status,
        )
    )
    ready = flow_report.status is RecruiterEvaluationFlowStatus.READY
    status = RecruiterDryRunStatus.READY_FOR_RECRUITER_SKILL_INPUT if ready else RecruiterDryRunStatus.CONTEXT_SOURCE_REQUIRED
    reason = "evaluation_flow_ready" if ready else flow_report.status.value.casefold()
    return RecruiterDryRunReport(
        status=status,
        context_status=flow_report.status.value,
        input={
            "prompt": prompt,
            "repo_root": str(repo_root) if repo_root is not None else None,
            "private_context_status": private_context_status,
        },
        readiness={"ready": ready, "reason": reason},
        context_packet=None,
        evaluation_flow=flow_report.to_dict(),
        missing_requirements=[] if ready else list(flow_report.required_inputs),
        warnings=list(flow_report.warnings),
        errors=[],
        provenance={"writes_performed": False, "dry_run": True, "flow": "evaluate-vacancy"},
        next_allowed_actions=list(flow_report.next_allowed_actions),
        forbidden_actions=list(flow_report.forbidden_actions),
    )


def _map_status(context_status: str) -> tuple[RecruiterDryRunStatus, bool, str]:
    if context_status == RecruiterContextStatus.READY.value:
        return RecruiterDryRunStatus.READY_FOR_RECRUITER_SKILL_INPUT, True, "context_ready"
    if context_status in {
        RecruiterContextStatus.VACANCY_NOT_FOUND.value,
        RecruiterContextStatus.OPPORTUNITY_NOT_FOUND.value,
    }:
        return RecruiterDryRunStatus.CONTEXT_NOT_FOUND, False, "requested_context_not_found"
    if context_status == RecruiterContextStatus.PACKAGE_CONTEXT_ERROR.value:
        return RecruiterDryRunStatus.CONTEXT_PACKAGE_ERROR, False, "role_package_context_error"
    if context_status == RecruiterContextStatus.FACADE_ERROR.value:
        return RecruiterDryRunStatus.CONTEXT_FACADE_ERROR, False, "recruiter_read_facade_error"
    if context_status == RecruiterContextStatus.SOURCE_REQUIRED.value:
        return RecruiterDryRunStatus.CONTEXT_SOURCE_REQUIRED, False, "request_source_required"
    return RecruiterDryRunStatus.CONTEXT_INVALID_REQUEST, False, "invalid_context_request"


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result
