from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Protocol

from .recruiter_context import RecruiterContextRequest, build_recruiter_context
from .recruiter_skill_inputs import build_recruiter_skill_input_packets


FLOW_EVALUATE_AND_POSITION = "evaluate-and-position"
VACANCY_EVALUATION_SKILL_ID = "vacancy-evaluation"
POSITIONING_EVIDENCE_SKILL_ID = "positioning-and-evidence"
PLANNED_FLOW = [VACANCY_EVALUATION_SKILL_ID, POSITIONING_EVIDENCE_SKILL_ID]
REAL_PROVIDER_EXECUTOR_NOT_WIRED_ERROR = "REAL_PROVIDER_EXECUTOR_NOT_WIRED"

REQUIRED_VACANCY_EVALUATION_FIELDS = [
    "vacancy_evaluation_summary",
    "fit_interpretation",
    "evidence_gaps",
    "recommendation_for_next_step",
]
REQUIRED_POSITIONING_FIELDS = [
    "positioning_summary",
    "evidence_map",
    "proven_facts",
    "derived_positioning",
    "gaps",
    "risks_and_mitigations",
]
BASE_FORBIDDEN_ACTIONS = [
    "send_outbound_message",
    "apply_to_job",
    "write_crm",
    "write_job_intel_db",
    "read_private_file_contents",
    "mutate_live_config",
    "restart_gateway",
]
DEFAULT_FORBIDDEN_ACTIONS = [
    "call_provider_model",
    "execute_recruiter_skill",
    *BASE_FORBIDDEN_ACTIONS,
]
_SKILL_ID_TO_PATH = {
    VACANCY_EVALUATION_SKILL_ID: "role-packages/recruiter/skills/vacancy-evaluation/SKILL.md",
    POSITIONING_EVIDENCE_SKILL_ID: "role-packages/recruiter/skills/positioning-and-evidence/SKILL.md",
}


class RecruiterSkillExecutionStatus(str, Enum):
    PROVIDER_EXECUTION_BLOCKED = "PROVIDER_EXECUTION_BLOCKED"
    REAL_PROVIDER_EXECUTOR_NOT_WIRED = "REAL_PROVIDER_EXECUTOR_NOT_WIRED"
    CONTEXT_OR_INPUT_BLOCKED = "CONTEXT_OR_INPUT_BLOCKED"
    SKILL_OUTPUT_INVALID = "SKILL_OUTPUT_INVALID"
    EXECUTION_READY = "EXECUTION_READY"
    INVALID_REQUEST = "INVALID_REQUEST"


@dataclass(slots=True)
class RecruiterSkillExecutionRequest:
    vacancy_id: int | None = None
    vacancy_url: str | None = None
    opportunity_id: int | None = None
    job_intel_db_path: str | Path | None = None
    private_career_dir: str | Path | None = None
    repo_root: str | Path | None = None
    stale_after_days: int = 14
    flow: str = FLOW_EVALUATE_AND_POSITION
    allow_provider_execution: bool = False

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


@dataclass(slots=True)
class SkillExecutionResult:
    status: str
    skill_id: str
    output: dict[str, Any]
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    provenance: dict[str, Any] = field(default_factory=dict)
    provider_called: bool = False


class RecruiterSkillExecutor(Protocol):
    def execute(
        self,
        *,
        skill_id: str,
        skill_input: dict[str, Any],
        skill_markdown_path: str,
        expected_schema: list[str],
    ) -> SkillExecutionResult: ...


@dataclass(slots=True)
class RecruiterSkillExecutionReport:
    status: RecruiterSkillExecutionStatus
    flow_id: str
    context_status: str
    skill_input_status: str
    execution_status: str
    provider_called: bool
    executor_called: bool
    vacancy_evaluation_result: dict[str, Any] | None
    positioning_evidence_result: dict[str, Any] | None
    downstream_gates: dict[str, Any]
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    provenance: dict[str, Any] = field(default_factory=dict)
    forbidden_actions: list[str] = field(default_factory=lambda: list(DEFAULT_FORBIDDEN_ACTIONS))
    planned_flow: list[str] = field(default_factory=lambda: list(PLANNED_FLOW))

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        return data


ContextBuilder = Callable[[RecruiterContextRequest], Any]


def run_recruiter_skill_execution(
    request: RecruiterSkillExecutionRequest,
    *,
    context_builder: ContextBuilder = build_recruiter_context,
    executor: RecruiterSkillExecutor | None = None,
) -> RecruiterSkillExecutionReport:
    if request.flow != FLOW_EVALUATE_AND_POSITION:
        return RecruiterSkillExecutionReport(
            status=RecruiterSkillExecutionStatus.INVALID_REQUEST,
            flow_id=request.flow,
            context_status="NOT_REQUESTED",
            skill_input_status="NOT_REQUESTED",
            execution_status="invalid_request",
            provider_called=False,
            executor_called=False,
            vacancy_evaluation_result=None,
            positioning_evidence_result=None,
            downstream_gates=_document_writer_gate("POSITIONING_REQUIRED"),
            errors=[f"unsupported_flow:{request.flow}"],
            provenance={"writes_performed": False},
        )

    try:
        context_packet = context_builder(request.to_context_request())
    except ValueError as exc:
        return RecruiterSkillExecutionReport(
            status=RecruiterSkillExecutionStatus.INVALID_REQUEST,
            flow_id=request.flow,
            context_status="INVALID_REQUEST",
            skill_input_status="NOT_REQUESTED",
            execution_status="invalid_request",
            provider_called=False,
            executor_called=False,
            vacancy_evaluation_result=None,
            positioning_evidence_result=None,
            downstream_gates=_document_writer_gate("POSITIONING_REQUIRED"),
            errors=[str(exc)],
            provenance={"writes_performed": False},
        )

    packet = context_packet.to_dict() if hasattr(context_packet, "to_dict") else dict(context_packet)
    skill_input_packet = build_recruiter_skill_input_packets(packet)
    skill_input = skill_input_packet.to_dict()

    context_status = str(packet.get("status") or "UNKNOWN")
    skill_input_status = skill_input_packet.status.value
    warnings = _dedupe([*list(packet.get("warnings") or []), *skill_input.get("warnings", [])])
    errors = _dedupe([*list(packet.get("errors") or []), *skill_input.get("errors", [])])
    provenance = {
        **dict(packet.get("provenance") or {}),
        **dict(skill_input.get("provenance") or {}),
        "writes_performed": False,
        "flow_id": request.flow,
    }

    if context_status != "READY" or skill_input.get("vacancy_evaluation_input") is None:
        return RecruiterSkillExecutionReport(
            status=RecruiterSkillExecutionStatus.CONTEXT_OR_INPUT_BLOCKED,
            flow_id=request.flow,
            context_status=context_status,
            skill_input_status=skill_input_status,
            execution_status="blocked_by_context_or_skill_inputs",
            provider_called=False,
            executor_called=False,
            vacancy_evaluation_result=None,
            positioning_evidence_result=None,
            downstream_gates=skill_input["downstream_gates"],
            warnings=warnings,
            errors=errors,
            provenance=provenance,
        )

    positioning_gate_error = _positioning_input_gate_error(skill_input)
    if positioning_gate_error is not None:
        return RecruiterSkillExecutionReport(
            status=RecruiterSkillExecutionStatus.CONTEXT_OR_INPUT_BLOCKED,
            flow_id=request.flow,
            context_status=context_status,
            skill_input_status=skill_input_status,
            execution_status="blocked_by_skill_input",
            provider_called=False,
            executor_called=False,
            vacancy_evaluation_result=None,
            positioning_evidence_result=None,
            downstream_gates=skill_input["downstream_gates"],
            warnings=warnings,
            errors=_dedupe([*errors, positioning_gate_error]),
            provenance=provenance,
        )

    if not request.allow_provider_execution:
        return RecruiterSkillExecutionReport(
            status=RecruiterSkillExecutionStatus.PROVIDER_EXECUTION_BLOCKED,
            flow_id=request.flow,
            context_status=context_status,
            skill_input_status=skill_input_status,
            execution_status="blocked_by_provider_fuse",
            provider_called=False,
            executor_called=False,
            vacancy_evaluation_result=None,
            positioning_evidence_result=None,
            downstream_gates=skill_input["downstream_gates"],
            warnings=warnings,
            errors=errors,
            provenance=provenance,
        )

    if executor is None:
        return RecruiterSkillExecutionReport(
            status=RecruiterSkillExecutionStatus.REAL_PROVIDER_EXECUTOR_NOT_WIRED,
            flow_id=request.flow,
            context_status=context_status,
            skill_input_status=skill_input_status,
            execution_status="provider_enabled_but_executor_unavailable",
            provider_called=False,
            executor_called=False,
            vacancy_evaluation_result=None,
            positioning_evidence_result=None,
            downstream_gates=skill_input["downstream_gates"],
            warnings=warnings,
            errors=_dedupe([*errors, REAL_PROVIDER_EXECUTOR_NOT_WIRED_ERROR]),
            provenance=provenance,
            forbidden_actions=list(BASE_FORBIDDEN_ACTIONS),
        )

    vacancy_input = dict(skill_input["vacancy_evaluation_input"])
    vacancy_result = executor.execute(
        skill_id=VACANCY_EVALUATION_SKILL_ID,
        skill_input=vacancy_input,
        skill_markdown_path=_skill_path_or_raise(VACANCY_EVALUATION_SKILL_ID),
        expected_schema=list(REQUIRED_VACANCY_EVALUATION_FIELDS),
    )
    vacancy_payload = _normalize_skill_result(vacancy_result)
    provider_called = vacancy_result.provider_called
    executor_called = True

    missing_vacancy_fields = _missing_fields(vacancy_result.output, REQUIRED_VACANCY_EVALUATION_FIELDS)
    if missing_vacancy_fields:
        return _invalid_output_report(
            request=request,
            context_status=context_status,
            skill_input_status=skill_input_status,
            warnings=warnings,
            errors=errors,
            provenance=provenance,
            provider_called=provider_called,
            executor_called=executor_called,
            vacancy_result=vacancy_payload,
            positioning_result=None,
            missing_fields=missing_vacancy_fields,
        )

    positioning_input = dict(skill_input["positioning_evidence_input"])

    positioning_result = executor.execute(
        skill_id=POSITIONING_EVIDENCE_SKILL_ID,
        skill_input=positioning_input,
        skill_markdown_path=_skill_path_or_raise(POSITIONING_EVIDENCE_SKILL_ID),
        expected_schema=list(REQUIRED_POSITIONING_FIELDS),
    )
    provider_called = provider_called or positioning_result.provider_called
    positioning_payload = _normalize_skill_result(positioning_result)

    missing_positioning_fields = _missing_fields(positioning_result.output, REQUIRED_POSITIONING_FIELDS)
    if missing_positioning_fields:
        return _invalid_output_report(
            request=request,
            context_status=context_status,
            skill_input_status=skill_input_status,
            warnings=warnings,
            errors=errors,
            provenance=provenance,
            provider_called=provider_called,
            executor_called=executor_called,
            vacancy_result=vacancy_payload,
            positioning_result=positioning_payload,
            missing_fields=missing_positioning_fields,
        )

    return RecruiterSkillExecutionReport(
        status=RecruiterSkillExecutionStatus.EXECUTION_READY,
        flow_id=request.flow,
        context_status=context_status,
        skill_input_status=skill_input_status,
        execution_status="completed",
        provider_called=provider_called,
        executor_called=executor_called,
        vacancy_evaluation_result=vacancy_payload,
        positioning_evidence_result=positioning_payload,
        downstream_gates=_document_writer_gate("POSITIONING_AVAILABLE"),
        warnings=_dedupe([*warnings, *vacancy_result.warnings, *positioning_result.warnings]),
        errors=_dedupe([*errors, *vacancy_result.errors, *positioning_result.errors]),
        provenance=provenance,
        forbidden_actions=list(BASE_FORBIDDEN_ACTIONS),
    )

def _normalize_skill_result(result: SkillExecutionResult) -> dict[str, Any]:
    return {
        "status": result.status,
        "skill_id": result.skill_id,
        **dict(result.output),
        "warnings": list(result.warnings),
        "errors": list(result.errors),
        "provenance": dict(result.provenance),
    }


def _missing_fields(payload: dict[str, Any], required_fields: list[str]) -> list[str]:
    return [field for field in required_fields if field not in payload]


def _invalid_output_report(
    *,
    request: RecruiterSkillExecutionRequest,
    context_status: str,
    skill_input_status: str,
    warnings: list[str],
    errors: list[str],
    provenance: dict[str, Any],
    provider_called: bool,
    executor_called: bool,
    vacancy_result: dict[str, Any] | None,
    positioning_result: dict[str, Any] | None,
    missing_fields: list[str],
) -> RecruiterSkillExecutionReport:
    return RecruiterSkillExecutionReport(
        status=RecruiterSkillExecutionStatus.SKILL_OUTPUT_INVALID,
        flow_id=request.flow,
        context_status=context_status,
        skill_input_status=skill_input_status,
        execution_status="invalid_skill_output",
        provider_called=provider_called,
        executor_called=executor_called,
        vacancy_evaluation_result=vacancy_result,
        positioning_evidence_result=positioning_result,
        downstream_gates=_document_writer_gate("POSITIONING_REQUIRED"),
        warnings=warnings,
        errors=_dedupe([*errors, f"missing_required_skill_output_fields:{','.join(missing_fields)}"]),
        provenance=provenance,
        forbidden_actions=list(BASE_FORBIDDEN_ACTIONS),
    )


def _document_writer_gate(status: str) -> dict[str, Any]:
    return {
        "document_writer": {
            "skill_id": "document-writer",
            "status": status,
            "reason": (
                "positioning packet available for downstream draft-only writer"
                if status == "POSITIONING_AVAILABLE"
                else "document-writer requires positioning-and-evidence output packet, not merely positioning input readiness"
            ),
            "requires": ["positioning-and-evidence"],
            "references": ["role-packages/recruiter/skills/document-writer/SKILL.md"],
        }
    }


def _positioning_input_gate_error(skill_input: dict[str, Any]) -> str | None:
    positioning_input = skill_input.get("positioning_evidence_input")
    if positioning_input is None:
        return "positioning-and-evidence input missing"
    if not isinstance(positioning_input, dict):
        return "positioning-and-evidence input invalid"
    positioning_status = str(positioning_input.get("status") or "")
    if positioning_status != "READY":
        return f"positioning-and-evidence input not ready:{positioning_status or 'UNKNOWN'}"
    return None


def _skill_path_or_raise(skill_id: str) -> str:
    path = _SKILL_ID_TO_PATH.get(skill_id)
    if path is None:
        raise ValueError(f"missing_skill_path:{skill_id}")
    return path


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result
