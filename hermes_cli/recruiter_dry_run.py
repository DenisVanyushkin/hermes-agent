from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable

from .recruiter_context import (
    RecruiterContextRequest,
    RecruiterContextStatus,
    build_recruiter_context,
)
from .recruiter_candidate_facts import validate_candidate_facts_ready_for_positioning
from .recruiter_application_materials_flow import (
    APPLICATION_MATERIAL_TARGETS,
    APPLICATION_MATERIALS_PACKET_SCHEMA_VERSION,
    run_recruiter_application_materials_flow,
)
from .recruiter_evaluation_provider_executor import (
    REQUIRED_VACANCY_EVALUATION_PACKET_FIELDS,
    VACANCY_EVALUATION_PACKET_SCHEMA_VERSION,
    VACANCY_EVALUATION_SKILL_ID,
    vacancy_evaluation_expected_schema,
)
from .recruiter_evaluation_flow import (
    RecruiterEvaluationFlowRequest,
    RecruiterEvaluationFlowStatus,
    build_recruiter_evaluation_flow,
)
from .recruiter_positioning_provider_executor import (
    POSITIONING_PACKET_SCHEMA_VERSION,
    POSITIONING_SKILL_ID,
    REQUIRED_POSITIONING_PACKET_FIELDS,
    positioning_expected_schema,
)
from .recruiter_skill_inputs import build_recruiter_skill_input_packets
from .recruiter_candidate_facts import detect_unsafe_content


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
    PROVIDER_EXECUTION_BLOCKED = "PROVIDER_EXECUTION_BLOCKED"
    EVALUATION_FLOW_BLOCKED = "EVALUATION_FLOW_BLOCKED"
    EVALUATION_READY = "EVALUATION_READY"
    EVALUATION_OUTPUT_INVALID = "EVALUATION_OUTPUT_INVALID"
    PROVIDER_EXECUTION_FAILED = "PROVIDER_EXECUTION_FAILED"
    POSITIONING_INPUT_BLOCKED = "POSITIONING_INPUT_BLOCKED"
    POSITIONING_READY = "POSITIONING_READY"
    POSITIONING_OUTPUT_INVALID = "POSITIONING_OUTPUT_INVALID"
    POSITIONING_PROVIDER_EXECUTION_FAILED = "POSITIONING_PROVIDER_EXECUTION_FAILED"
    APPLICATION_MATERIALS_INPUT_BLOCKED = "APPLICATION_MATERIALS_INPUT_BLOCKED"
    APPLICATION_MATERIALS_PROVIDER_EXECUTION_BLOCKED = "APPLICATION_MATERIALS_PROVIDER_EXECUTION_BLOCKED"
    APPLICATION_MATERIALS_OUTPUT_INVALID = "APPLICATION_MATERIALS_OUTPUT_INVALID"
    APPLICATION_MATERIALS_PROVIDER_EXECUTION_FAILED = "APPLICATION_MATERIALS_PROVIDER_EXECUTION_FAILED"
    APPLICATION_MATERIALS_REVIEW_BLOCKED = "APPLICATION_MATERIALS_REVIEW_BLOCKED"
    APPLICATION_MATERIALS_READY = "APPLICATION_MATERIALS_READY"


class RecruiterPositioningSmokeStatus(str, Enum):
    READY_PROVIDER_BLOCKED = "POSITIONING_SMOKE_READY_PROVIDER_BLOCKED"
    INPUT_BLOCKED = "POSITIONING_SMOKE_INPUT_BLOCKED"
    OUTPUT_INVALID = "POSITIONING_SMOKE_OUTPUT_INVALID"
    READY = "POSITIONING_SMOKE_READY"


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
    evaluation_result: dict[str, Any] | None = None
    positioning_result: dict[str, Any] | None = None
    application_materials_result: dict[str, Any] | None = None
    missing_requirements: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    provenance: dict[str, Any] = field(default_factory=dict)
    next_allowed_actions: list[str] = field(default_factory=list)
    forbidden_actions: list[str] = field(default_factory=lambda: list(_FORBIDDEN_ACTIONS))
    provider_called: bool = False
    provider_execution_enabled: bool = False
    executor_called: bool = False
    downstream_gates: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        return data


@dataclass(slots=True)
class RecruiterPositioningSmokeReport:
    schema_version: str
    status: RecruiterPositioningSmokeStatus
    readiness_reason: str
    provider_allowed: bool
    provider_called: bool
    executor_called: bool
    input_validation: dict[str, Any]
    output_validation: dict[str, Any]
    positioning_packet_summary: dict[str, Any] | None = None
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    forbidden_actions: list[str] = field(default_factory=lambda: list(_FORBIDDEN_ACTIONS))
    next_allowed_actions: list[str] = field(default_factory=list)
    provenance: dict[str, Any] = field(default_factory=dict)

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
    allow_provider_execution: bool = False,
    executor_factory: Callable[[], Any] | None = None,
) -> RecruiterDryRunReport:
    flow_report = build_recruiter_evaluation_flow(
        RecruiterEvaluationFlowRequest(
            prompt=prompt,
            repo_root=repo_root,
            private_context_status=private_context_status,
        )
    )
    ready = flow_report.status is RecruiterEvaluationFlowStatus.READY
    base_report = RecruiterDryRunReport(
        status=RecruiterDryRunStatus.READY_FOR_RECRUITER_SKILL_INPUT if ready else RecruiterDryRunStatus.EVALUATION_FLOW_BLOCKED,
        context_status=flow_report.status.value,
        input={
            "prompt": prompt,
            "repo_root": str(repo_root) if repo_root is not None else None,
            "private_context_status": private_context_status,
            "allow_provider_execution": allow_provider_execution,
        },
        readiness={"ready": ready, "reason": "evaluation_flow_ready" if ready else flow_report.status.value.casefold()},
        context_packet=None,
        evaluation_flow=flow_report.to_dict(),
        evaluation_result=None,
        positioning_result=None,
        missing_requirements=[] if ready else list(flow_report.required_inputs),
        warnings=list(flow_report.warnings),
        errors=[],
        provenance={"writes_performed": False, "dry_run": True, "flow": "evaluate-vacancy"},
        next_allowed_actions=list(flow_report.next_allowed_actions),
        forbidden_actions=list(flow_report.forbidden_actions),
        provider_called=False,
        provider_execution_enabled=allow_provider_execution,
        executor_called=False,
        downstream_gates=_evaluation_downstream_gates(),
    )
    if not ready:
        return base_report
    if not allow_provider_execution:
        base_report.status = RecruiterDryRunStatus.PROVIDER_EXECUTION_BLOCKED
        base_report.readiness["reason"] = "provider_execution_requires_explicit_opt_in"
        base_report.next_allowed_actions = _dedupe([*base_report.next_allowed_actions, "rerun_with_allow_provider_execution"])
        return base_report

    if private_context_status != "PRIVATE_CONTEXT_AVAILABLE":
        base_report.status = RecruiterDryRunStatus.EVALUATION_FLOW_BLOCKED
        base_report.readiness = {"ready": False, "reason": "provider_execution_requires_private_context_available"}
        base_report.errors = ["private_context_not_ready_for_provider_execution"]
        base_report.next_allowed_actions = ["provision_private_career_context"]
        return base_report

    evaluation_input = _build_prompt_evaluation_input(prompt=prompt, repo_root=repo_root)
    base_report.provenance["skill_input_builder"] = "recruiter_skill_inputs"
    base_report.provenance["skill_id"] = "vacancy-evaluation"

    if executor_factory is None:
        from .recruiter_evaluation_provider_executor import build_recruiter_evaluation_provider_executor

        executor_factory = build_recruiter_evaluation_provider_executor

    try:
        executor = executor_factory()
        base_report.executor_called = True
        base_report.provider_called = True
        raw_result = executor.execute(
            skill_input=evaluation_input,
            expected_schema=vacancy_evaluation_expected_schema(),
        )
    except ValueError as exc:
        base_report.status = RecruiterDryRunStatus.EVALUATION_OUTPUT_INVALID
        base_report.errors = [str(exc)]
        return base_report
    except Exception:
        base_report.status = RecruiterDryRunStatus.PROVIDER_EXECUTION_FAILED
        base_report.errors = ["provider_execution_failed"]
        return base_report

    missing_fields = [field for field in REQUIRED_VACANCY_EVALUATION_PACKET_FIELDS if field not in raw_result]
    if missing_fields:
        base_report.status = RecruiterDryRunStatus.EVALUATION_OUTPUT_INVALID
        base_report.evaluation_result = _sanitize_result(raw_result)
        base_report.errors = [f"missing_required_evaluation_output_fields:{','.join(missing_fields)}"]
        return base_report

    base_report.status = RecruiterDryRunStatus.EVALUATION_READY
    base_report.evaluation_result = _sanitize_result(raw_result)
    base_report.readiness = {"ready": True, "reason": "provider_evaluation_completed"}
    base_report.next_allowed_actions = ["review_evaluation_packet_manually"]
    return base_report


def run_recruiter_positioning_flow_dry_run(
    *,
    evaluation_packet: dict[str, Any] | None,
    candidate_facts_packet: dict[str, Any] | None = None,
    repo_root: str | Path | None = None,
    private_context_status: str = "PRIVATE_CONTEXT_NOT_INSPECTED",
    allow_provider_execution: bool = False,
    executor_factory: Callable[[], Any] | None = None,
    fake_positioning_result_factory: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
) -> RecruiterDryRunReport:
    base_report = RecruiterDryRunReport(
        status=RecruiterDryRunStatus.POSITIONING_INPUT_BLOCKED,
        context_status="POSITIONING_INPUT_REQUIRED",
        input=_build_positioning_report_input(
            repo_root=repo_root,
            private_context_status=private_context_status,
            allow_provider_execution=allow_provider_execution,
        ),
        readiness={"ready": False, "reason": "positioning_input_not_ready"},
        context_packet=None,
        evaluation_flow=None,
        evaluation_result=None,
        positioning_result=None,
        missing_requirements=[],
        warnings=[],
        errors=[],
        provenance={"writes_performed": False, "dry_run": True, "flow": "positioning-and-evidence"},
        next_allowed_actions=[],
        provider_called=False,
        provider_execution_enabled=allow_provider_execution,
        executor_called=False,
        downstream_gates=_evaluation_downstream_gates(),
    )
    evaluation_error = _validate_positioning_input_gate(evaluation_packet, private_context_status)
    if evaluation_error is not None:
        base_report.errors = [evaluation_error]
        base_report.readiness["reason"] = evaluation_error
        return base_report
    candidate_facts_error = _validate_candidate_facts_input_gate(candidate_facts_packet)
    if candidate_facts_error is not None:
        base_report.errors = [candidate_facts_error]
        base_report.readiness["reason"] = candidate_facts_error
        return base_report

    if isinstance(candidate_facts_packet, dict):
        base_report.input.update(
            _build_candidate_facts_report_fields(candidate_facts_packet)
        )
    base_report.context_status = "READY"
    base_report.readiness = {"ready": True, "reason": "positioning_input_ready"}
    base_report.evaluation_result = _build_evaluation_packet_report_fields(evaluation_packet)
    positioning_input = _build_positioning_input(
        evaluation_packet=dict(evaluation_packet or {}),
        candidate_facts_packet=dict(candidate_facts_packet or {}) if isinstance(candidate_facts_packet, dict) else None,
        repo_root=repo_root,
        private_context_status=private_context_status,
    )
    if fake_positioning_result_factory is not None:
        if not isinstance(candidate_facts_packet, dict):
            base_report.errors = ["candidate_facts_packet_missing"]
            base_report.readiness["reason"] = "candidate_facts_packet_missing"
            return base_report
        base_report.executor_called = True
        try:
            raw_result = fake_positioning_result_factory(positioning_input)
        except ValueError as exc:
            base_report.status = RecruiterDryRunStatus.POSITIONING_OUTPUT_INVALID
            base_report.errors = [str(exc)]
            return base_report
        output_error = _validate_fake_positioning_output(raw_result)
        if output_error is not None:
            base_report.status = RecruiterDryRunStatus.POSITIONING_OUTPUT_INVALID
            base_report.positioning_result = _sanitize_result(raw_result)
            base_report.errors = [output_error]
            return base_report
        base_report.status = RecruiterDryRunStatus.POSITIONING_READY
        base_report.positioning_result = _sanitize_result(raw_result)
        base_report.readiness = {"ready": True, "reason": "fake_positioning_completed"}
        base_report.next_allowed_actions = ["review_positioning_packet_manually"]
        return base_report

    if not allow_provider_execution:
        base_report.status = RecruiterDryRunStatus.PROVIDER_EXECUTION_BLOCKED
        base_report.readiness["reason"] = "provider_execution_requires_explicit_opt_in"
        base_report.next_allowed_actions = ["rerun_with_allow_provider_execution"]
        return base_report

    if executor_factory is None:
        from .recruiter_positioning_provider_executor import build_recruiter_positioning_provider_executor

        executor_factory = build_recruiter_positioning_provider_executor

    try:
        executor = executor_factory()
        base_report.executor_called = True
        base_report.provider_called = True
        raw_result = executor.execute(
            skill_input=positioning_input,
            expected_schema=positioning_expected_schema(),
        )
    except ValueError as exc:
        base_report.status = RecruiterDryRunStatus.POSITIONING_OUTPUT_INVALID
        base_report.errors = [str(exc)]
        return base_report
    except Exception:
        base_report.status = RecruiterDryRunStatus.POSITIONING_PROVIDER_EXECUTION_FAILED
        base_report.errors = ["positioning_provider_execution_failed"]
        return base_report

    output_error = _validate_positioning_output(raw_result)
    if output_error is not None:
        base_report.status = RecruiterDryRunStatus.POSITIONING_OUTPUT_INVALID
        base_report.positioning_result = _sanitize_result(raw_result)
        base_report.errors = [output_error]
        return base_report

    base_report.status = RecruiterDryRunStatus.POSITIONING_READY
    base_report.positioning_result = _sanitize_result(raw_result)
    base_report.readiness = {"ready": True, "reason": "provider_positioning_completed"}
    base_report.next_allowed_actions = ["review_positioning_packet_manually"]
    return base_report


def run_recruiter_positioning_smoke_harness(
    *,
    evaluation_packet: dict[str, Any] | None,
    candidate_facts_packet: dict[str, Any] | None,
    repo_root: str | Path | None = None,
    private_context_status: str = "PRIVATE_CONTEXT_NOT_INSPECTED",
    allow_provider_execution: bool = False,
    executor_factory: Callable[[], Any] | None = None,
) -> RecruiterPositioningSmokeReport:
    input_errors: list[str] = []
    evaluation_error = _validate_positioning_smoke_evaluation_packet(evaluation_packet)
    if evaluation_error is not None:
        input_errors.append(evaluation_error)
    candidate_facts_error = _validate_candidate_facts_ready_for_positioning(candidate_facts_packet)
    if candidate_facts_error is not None:
        input_errors.append(candidate_facts_error)

    base_report = RecruiterPositioningSmokeReport(
        schema_version="recruiter_positioning_smoke_report_v1",
        status=RecruiterPositioningSmokeStatus.INPUT_BLOCKED,
        readiness_reason="positioning_smoke_input_not_ready",
        provider_allowed=allow_provider_execution,
        provider_called=False,
        executor_called=False,
        input_validation={
            "ready": not input_errors,
            "evaluation_packet_ready": evaluation_error is None,
            "candidate_facts_packet_ready": candidate_facts_error is None,
            "errors": list(input_errors),
        },
        output_validation={
            "ready": False,
            "status": "not_run",
            "errors": [],
        },
        warnings=[],
        errors=list(input_errors),
        next_allowed_actions=[],
        provenance={"writes_performed": False, "dry_run": True, "flow": "positioning-smoke"},
    )
    if input_errors:
        base_report.readiness_reason = input_errors[0]
        return base_report

    base_report.status = RecruiterPositioningSmokeStatus.READY_PROVIDER_BLOCKED
    base_report.readiness_reason = "provider_execution_requires_explicit_opt_in"
    base_report.next_allowed_actions = ["rerun_with_allow_provider_execution"]
    if not allow_provider_execution:
        return base_report

    positioning_input = _build_positioning_input(
        evaluation_packet=dict(evaluation_packet or {}),
        candidate_facts_packet=dict(candidate_facts_packet or {}),
        repo_root=repo_root,
        private_context_status=private_context_status,
    )
    if executor_factory is None:
        from .recruiter_positioning_provider_executor import build_recruiter_positioning_provider_executor

        executor_factory = build_recruiter_positioning_provider_executor

    try:
        executor = executor_factory()
        base_report.executor_called = True
        base_report.provider_called = bool(getattr(executor, "provider_backed", True))
        raw_result = executor.execute(
            skill_input=positioning_input,
            expected_schema=positioning_expected_schema(),
        )
    except ValueError as exc:
        base_report.status = RecruiterPositioningSmokeStatus.OUTPUT_INVALID
        base_report.readiness_reason = str(exc)
        base_report.errors = [str(exc)]
        base_report.output_validation = {"ready": False, "status": "invalid", "errors": [str(exc)]}
        return base_report
    except Exception:
        base_report.status = RecruiterPositioningSmokeStatus.OUTPUT_INVALID
        base_report.readiness_reason = "positioning_executor_failed"
        base_report.errors = ["positioning_executor_failed"]
        base_report.output_validation = {"ready": False, "status": "invalid", "errors": ["positioning_executor_failed"]}
        return base_report

    output_error = _validate_positioning_packet_contract(raw_result)
    base_report.positioning_packet_summary = _build_positioning_packet_report_fields(raw_result)
    if output_error is not None:
        base_report.status = RecruiterPositioningSmokeStatus.OUTPUT_INVALID
        base_report.readiness_reason = output_error
        base_report.errors = [output_error]
        base_report.output_validation = {"ready": False, "status": "invalid", "errors": [output_error]}
        return base_report

    base_report.status = RecruiterPositioningSmokeStatus.READY
    base_report.readiness_reason = "positioning_smoke_ready"
    base_report.errors = []
    base_report.output_validation = {"ready": True, "status": "valid", "errors": []}
    base_report.next_allowed_actions = ["review_positioning_packet_manually"]
    return base_report


def run_recruiter_application_materials_flow_dry_run(
    *,
    positioning_packet: dict[str, Any] | None,
    repo_root: str | Path | None = None,
    private_context_status: str = "PRIVATE_CONTEXT_NOT_INSPECTED",
    allow_provider_execution: bool = False,
    document_target: str | None = None,
    executor_factory: Callable[[], Any] | None = None,
) -> RecruiterDryRunReport:
    downstream_gates = _application_materials_downstream_gates(controlled_document_dry_run_enabled=False)
    base_report = RecruiterDryRunReport(
        status=RecruiterDryRunStatus.APPLICATION_MATERIALS_INPUT_BLOCKED,
        context_status="APPLICATION_MATERIALS_INPUT_REQUIRED",
        input={
            "repo_root": str(repo_root) if repo_root is not None else None,
            "private_context_status": private_context_status,
            "allow_provider_execution": allow_provider_execution,
            "document_target": document_target,
        },
        readiness={"ready": False, "reason": "application_materials_input_not_ready"},
        context_packet=None,
        evaluation_flow=None,
        evaluation_result=None,
        positioning_result=None,
        application_materials_result=None,
        missing_requirements=[],
        warnings=[],
        errors=[],
        provenance={"writes_performed": False, "dry_run": True, "flow": "application-materials"},
        next_allowed_actions=[],
        provider_called=False,
        provider_execution_enabled=allow_provider_execution,
        executor_called=False,
        downstream_gates=downstream_gates,
    )
    if document_target is not None and document_target not in APPLICATION_MATERIAL_TARGETS:
        base_report.errors = ["invalid_document_target"]
        base_report.readiness["reason"] = "invalid_document_target"
        return base_report
    input_error = _validate_application_materials_input_gate(positioning_packet, private_context_status)
    if input_error is not None:
        base_report.errors = [input_error]
        base_report.readiness["reason"] = input_error
        return base_report

    base_report.context_status = "READY"
    base_report.readiness = {"ready": True, "reason": "application_materials_input_ready"}
    base_report.positioning_result = _build_positioning_packet_report_fields(positioning_packet)
    if not allow_provider_execution:
        base_report.status = RecruiterDryRunStatus.APPLICATION_MATERIALS_PROVIDER_EXECUTION_BLOCKED
        base_report.readiness["reason"] = "provider_execution_requires_explicit_opt_in"
        base_report.next_allowed_actions = ["rerun_with_allow_provider_execution"]
        return base_report

    if executor_factory is None:
        from .recruiter_document_provider_executor import build_recruiter_document_provider_executor

        executor_factory = build_recruiter_document_provider_executor

    try:
        executor = executor_factory()
        base_report.executor_called = True
        base_report.provider_called = True
        flow_report = run_recruiter_application_materials_flow(
            positioning_packet=dict(positioning_packet or {}),
            allow_document_execution=True,
            document_target=document_target,
            executor=executor,
        )
    except ValueError as exc:
        base_report.status = RecruiterDryRunStatus.APPLICATION_MATERIALS_OUTPUT_INVALID
        base_report.errors = [str(exc)]
        return base_report
    except Exception:
        base_report.status = RecruiterDryRunStatus.APPLICATION_MATERIALS_PROVIDER_EXECUTION_FAILED
        base_report.errors = ["application_materials_provider_execution_failed"]
        return base_report

    base_report.application_materials_result = flow_report.to_dict()
    base_report.downstream_gates = dict(flow_report.downstream_gates)
    if flow_report.status == "APPLICATION_MATERIALS_READY":
        base_report.status = RecruiterDryRunStatus.APPLICATION_MATERIALS_READY
        base_report.readiness = {"ready": True, "reason": "application_materials_ready"}
        base_report.next_allowed_actions = ["review_application_materials_packet_manually"]
        return base_report
    if flow_report.status == "APPLICATION_MATERIALS_REVIEW_BLOCKED":
        base_report.status = RecruiterDryRunStatus.APPLICATION_MATERIALS_REVIEW_BLOCKED
        base_report.readiness = {"ready": False, "reason": "application_materials_review_blocked"}
        base_report.errors = list(flow_report.errors)
        return base_report
    base_report.status = RecruiterDryRunStatus.APPLICATION_MATERIALS_OUTPUT_INVALID
    base_report.readiness = {"ready": False, "reason": "application_materials_output_invalid"}
    base_report.errors = list(flow_report.errors)
    return base_report


def _build_prompt_evaluation_input(*, prompt: str, repo_root: str | Path | None) -> dict[str, Any]:
    synthetic_context = {
        "status": RecruiterContextStatus.READY.value,
        "request": {"prompt": prompt, "repo_root": str(repo_root) if repo_root is not None else None},
        "vacancy": {
            "vacancy_id": None,
            "vacancy_key": None,
            "source_url": None,
            "url": None,
            "title": None,
            "company": None,
            "location": None,
            "source_kind": "prompt_text",
            "provenance": {"source": "recruiter_evaluation_flow_prompt"},
        },
        "opportunity": None,
        "company_context": [],
        "application_history": {"status": "not_requested", "history": [], "artifacts": [], "feedback": []},
        "machine_score": {},
        "role_package_context": {"package_id": "hermes-recruiter", "role_id": "hermes_recruiter"},
        "private_context": {"status": "PRIVATE_CONTEXT_AVAILABLE", "dir": "", "files": {}},
        "warnings": [],
        "errors": [],
        "provenance": {"writes_performed": False, "input_mode": "prompt_dry_run"},
    }
    packet = build_recruiter_skill_input_packets(synthetic_context).to_dict()
    vacancy_input = dict(packet.get("vacancy_evaluation_input") or {})
    vacancy_input["prompt_text"] = prompt
    vacancy_input["expected_schema_version"] = vacancy_evaluation_expected_schema()["schema_version"]
    return vacancy_input


def _build_positioning_input(
    *,
    evaluation_packet: dict[str, Any],
    candidate_facts_packet: dict[str, Any] | None,
    repo_root: str | Path | None,
    private_context_status: str,
) -> dict[str, Any]:
    payload = {
        "skill_id": POSITIONING_SKILL_ID,
        "evaluation_packet": evaluation_packet,
        "private_context_status": private_context_status,
        "repo_root": str(repo_root) if repo_root is not None else None,
        "boundaries": {
            "no_outbound": True,
            "no_db_write": True,
            "no_crm_write": True,
            "no_document_generation": True,
            "no_private_file_content_read": True,
        },
    }
    if isinstance(candidate_facts_packet, dict):
        payload.update(_build_candidate_facts_positioning_fields(candidate_facts_packet))
    return payload


def _build_positioning_report_input(
    *,
    repo_root: str | Path | None,
    private_context_status: str,
    allow_provider_execution: bool,
) -> dict[str, Any]:
    return {
        "repo_root": str(repo_root) if repo_root is not None else None,
        "private_context_status": private_context_status,
        "allow_provider_execution": allow_provider_execution,
    }


def _build_candidate_facts_positioning_fields(candidate_facts_packet: dict[str, Any]) -> dict[str, Any]:
    facts = _dict_list(candidate_facts_packet.get("facts"))
    allowed_claims = _dict_list(candidate_facts_packet.get("allowed_claims"))
    source_references = _dict_list(candidate_facts_packet.get("source_references"))
    return {
        "candidate_facts_packet": candidate_facts_packet,
        "candidate_facts_status": str(candidate_facts_packet.get("status") or ""),
        "candidate_fact_summaries": [
            {
                "fact_id": str(fact.get("fact_id") or ""),
                "category": str(fact.get("category") or ""),
                "safe_summary": str(fact.get("safe_summary") or ""),
                "support_level": str(fact.get("support_level") or ""),
            }
            for fact in facts
            if isinstance(fact, dict)
        ],
        "allowed_claims": [
            str(claim.get("claim_text") or "")
            for claim in allowed_claims
            if isinstance(claim, dict) and str(claim.get("claim_text") or "").strip()
        ],
        "claims_to_avoid": [str(item) for item in _string_list(candidate_facts_packet.get("claims_to_avoid")) if str(item).strip()],
        "source_references": [
            {
                "source_ref_id": str(ref.get("source_ref_id") or ""),
                "source_type": str(ref.get("source_type") or ""),
                "source_label": str(ref.get("source_label") or ""),
                "source_id_hash": str(ref.get("source_id_hash") or ""),
                "section_label": str(ref.get("section_label") or ""),
                "content_hash": str(ref.get("content_hash") or ""),
                "sensitivity": str(ref.get("sensitivity") or ""),
            }
            for ref in source_references
            if isinstance(ref, dict)
        ],
        "candidate_facts_provider_visibility_status": str(candidate_facts_packet.get("provider_visibility_status") or ""),
    }


def _build_candidate_facts_report_fields(candidate_facts_packet: dict[str, Any]) -> dict[str, Any]:
    payload = _build_candidate_facts_positioning_fields(candidate_facts_packet)
    payload.pop("candidate_facts_packet", None)
    return payload


def _validate_candidate_facts_input_gate(candidate_facts_packet: dict[str, Any] | None) -> str | None:
    if candidate_facts_packet is None:
        return None
    return validate_candidate_facts_ready_for_positioning(candidate_facts_packet)


def _validate_candidate_facts_ready_for_positioning(candidate_facts_packet: dict[str, Any] | None) -> str | None:
    if not isinstance(candidate_facts_packet, dict):
        return "candidate_facts_packet_missing"
    return validate_candidate_facts_ready_for_positioning(candidate_facts_packet)


def _sanitize_result(raw_result: dict[str, Any]) -> dict[str, Any]:
    payload = dict(raw_result)
    payload["provenance"] = dict(payload.get("provenance") or {})
    return payload


def _build_evaluation_packet_report_fields(evaluation_packet: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(evaluation_packet, dict):
        return None
    return {
        "schema_version": str(evaluation_packet.get("schema_version") or ""),
        "status": str(evaluation_packet.get("status") or ""),
        "fit_assessment": str(evaluation_packet.get("fit_assessment") or ""),
        "recommendation": str(evaluation_packet.get("recommendation") or ""),
        "missing_information": [item for item in _string_list(evaluation_packet.get("missing_information")) if item.strip()],
        "next_step": str(evaluation_packet.get("next_step") or ""),
        "strengths": [item for item in _string_list(evaluation_packet.get("strengths")) if item.strip()],
        "risks": [item for item in _string_list(evaluation_packet.get("risks")) if item.strip()],
        "provenance": dict(evaluation_packet.get("provenance") or {}),
    }


def _build_positioning_packet_report_fields(positioning_packet: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(positioning_packet, dict):
        return None
    return {
        "schema_version": str(positioning_packet.get("schema_version") or ""),
        "status": str(positioning_packet.get("status") or ""),
        "generation_mode": str(positioning_packet.get("generation_mode") or ""),
        "source_kind": str(positioning_packet.get("source_kind") or ""),
        "positioning_summary": str(positioning_packet.get("positioning_summary") or ""),
        "recommended_angle": str(positioning_packet.get("recommended_angle") or ""),
        "allowed_claims": [
            {
                "claim_id": str(item.get("claim_id") or ""),
                "claim_text": str(item.get("claim_text") or ""),
                "source_fact_ids": [ref for ref in _string_list(item.get("source_fact_ids")) if ref.strip()],
                "support_level": str(item.get("support_level") or ""),
            }
            for item in _dict_list(positioning_packet.get("allowed_claims"))
        ],
        "evidence_items": [
            {
                "claim_text": str(item.get("claim_text") or ""),
                "source_fact_ids": [ref for ref in _string_list(item.get("source_fact_ids")) if ref.strip()],
                "source_ref_ids": [ref for ref in _string_list(item.get("source_ref_ids")) if ref.strip()],
                "support_level": str(item.get("support_level") or ""),
                "category": str(item.get("category") or ""),
                "safe_summary": str(item.get("safe_summary") or ""),
            }
            for item in _dict_list(positioning_packet.get("evidence_items"))
        ],
        "source_references": [
            {
                "source_ref_id": str(item.get("source_ref_id") or ""),
                "source_label": str(item.get("source_label") or ""),
                "source_id_hash": str(item.get("source_id_hash") or ""),
                "section_label": str(item.get("section_label") or ""),
                "support_level": str(item.get("support_level") or ""),
                "category": str(item.get("category") or ""),
            }
            for item in _dict_list(positioning_packet.get("source_references"))
        ],
        "claims_to_avoid": [item for item in _string_list(positioning_packet.get("claims_to_avoid")) if item.strip()],
        "support_summary": dict(positioning_packet.get("support_summary") or {}),
        "privacy_notes": [item for item in _string_list(positioning_packet.get("privacy_notes")) if item.strip()],
        "next_step": str(positioning_packet.get("next_step") or ""),
    }


def _dict_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if isinstance(item, str)]


def _validate_positioning_input_gate(
    evaluation_packet: dict[str, Any] | None,
    private_context_status: str,
) -> str | None:
    if not isinstance(evaluation_packet, dict):
        return "evaluation_packet_missing"
    missing_fields = [field for field in REQUIRED_VACANCY_EVALUATION_PACKET_FIELDS if field not in evaluation_packet]
    if missing_fields:
        return f"missing_required_evaluation_output_fields:{','.join(missing_fields)}"
    if evaluation_packet.get("schema_version") != VACANCY_EVALUATION_PACKET_SCHEMA_VERSION:
        return "evaluation_packet_schema_version_invalid"
    if evaluation_packet.get("skill_id") != VACANCY_EVALUATION_SKILL_ID:
        return "evaluation_packet_skill_id_invalid"
    if private_context_status != "PRIVATE_CONTEXT_AVAILABLE":
        return "private_context_not_ready_for_positioning"
    if evaluation_packet.get("recommendation") == "DO_NOT_APPLY" or evaluation_packet.get("next_step") == "DO_NOT_APPLY":
        return "evaluation_recommendation_blocks_positioning"
    if evaluation_packet.get("recommendation") == "NEED_MORE_INFO" or evaluation_packet.get("status") == "INSUFFICIENT_INPUT":
        return "evaluation_requires_more_information"
    if detect_unsafe_content(evaluation_packet):
        return "evaluation_packet_unsafe"
    return None


def _validate_positioning_output(raw_result: dict[str, Any]) -> str | None:
    missing_fields = [field for field in REQUIRED_POSITIONING_PACKET_FIELDS if field not in raw_result]
    if missing_fields:
        return f"missing_required_positioning_output_fields:{','.join(missing_fields)}"
    if raw_result.get("schema_version") != POSITIONING_PACKET_SCHEMA_VERSION:
        return "positioning_output_schema_version_invalid"
    if raw_result.get("skill_id") != POSITIONING_SKILL_ID:
        return "positioning_output_skill_id_invalid"
    return None


def _validate_positioning_smoke_evaluation_packet(evaluation_packet: dict[str, Any] | None) -> str | None:
    if not isinstance(evaluation_packet, dict):
        return "evaluation_packet_missing"
    missing_fields = [field for field in REQUIRED_VACANCY_EVALUATION_PACKET_FIELDS if field not in evaluation_packet]
    if missing_fields:
        return f"missing_required_evaluation_output_fields:{','.join(missing_fields)}"
    if evaluation_packet.get("schema_version") != VACANCY_EVALUATION_PACKET_SCHEMA_VERSION:
        return "evaluation_packet_schema_version_invalid"
    if evaluation_packet.get("skill_id") != VACANCY_EVALUATION_SKILL_ID:
        return "evaluation_packet_skill_id_invalid"
    if evaluation_packet.get("recommendation") == "DO_NOT_APPLY" or evaluation_packet.get("next_step") == "DO_NOT_APPLY":
        return "evaluation_recommendation_blocks_positioning"
    if evaluation_packet.get("recommendation") == "NEED_MORE_INFO" or evaluation_packet.get("status") == "INSUFFICIENT_INPUT":
        return "evaluation_requires_more_information"
    if detect_unsafe_content(evaluation_packet):
        return "evaluation_packet_unsafe"
    return None


def build_fake_positioning_packet_from_candidate_facts(skill_input: dict[str, Any]) -> dict[str, Any]:
    candidate_facts_packet = dict(skill_input.get("candidate_facts_packet") or {})
    evaluation_packet = dict(skill_input.get("evaluation_packet") or {})
    fact_items = _dict_list(candidate_facts_packet.get("facts"))
    source_reference_items = _dict_list(candidate_facts_packet.get("source_references"))
    claim_items = _dict_list(candidate_facts_packet.get("allowed_claims"))
    if not claim_items:
        raise ValueError("positioning_fake_output_unavailable")

    fact_by_id = {
        str(fact.get("fact_id") or ""): fact
        for fact in fact_items
        if str(fact.get("fact_id") or "").strip()
    }
    source_by_id = {
        str(ref.get("source_ref_id") or ""): ref
        for ref in source_reference_items
        if str(ref.get("source_ref_id") or "").strip()
    }
    evidence_items: list[dict[str, Any]] = []
    normalized_allowed_claims: list[dict[str, Any]] = []
    source_references: list[dict[str, Any]] = []
    seen_source_refs: set[str] = set()

    for claim in claim_items:
        claim_text = str(claim.get("claim_text") or "").strip()
        if not claim_text:
            continue
        source_fact_ids = [str(item).strip() for item in _string_list(claim.get("source_fact_ids")) if str(item).strip()]
        if not source_fact_ids:
            raise ValueError("positioning_claim_without_source_fact")
        fact_source_ref_ids: list[str] = []
        categories: list[str] = []
        support_levels: list[str] = []
        safe_summaries: list[str] = []
        for fact_id in source_fact_ids:
            fact = fact_by_id.get(fact_id)
            if fact is None:
                raise ValueError("positioning_claim_without_source_fact")
            categories.append(str(fact.get("category") or ""))
            support_levels.append(str(fact.get("support_level") or ""))
            safe_summary = str(fact.get("safe_summary") or "").strip()
            if safe_summary:
                safe_summaries.append(safe_summary)
            ref_ids = [str(item).strip() for item in _string_list(fact.get("source_ref_ids")) if str(item).strip()]
            if not ref_ids:
                raise ValueError("positioning_evidence_without_source")
            for ref_id in ref_ids:
                if ref_id not in source_by_id:
                    raise ValueError("positioning_evidence_without_source")
                if ref_id not in fact_source_ref_ids:
                    fact_source_ref_ids.append(ref_id)
                if ref_id not in seen_source_refs:
                    seen_source_refs.add(ref_id)
                    ref = source_by_id[ref_id]
                    source_references.append(
                        {
                            "source_ref_id": ref_id,
                            "source_label": str(ref.get("source_label") or ""),
                            "source_id_hash": str(ref.get("source_id_hash") or ""),
                            "section_label": str(ref.get("section_label") or ""),
                            "support_level": str(ref.get("support_level") or claim.get("support_level") or ""),
                            "category": str(ref.get("source_type") or ""),
                        }
                    )
        evidence_items.append(
            {
                "claim_text": claim_text,
                "source_fact_ids": source_fact_ids,
                "source_ref_ids": fact_source_ref_ids,
                "support_level": str(claim.get("support_level") or support_levels[0] or ""),
                "category": next((item for item in categories if item), ""),
                "safe_summary": next((item for item in safe_summaries if item), claim_text),
            }
        )
        normalized_allowed_claims.append(
            {
                "claim_id": str(claim.get("claim_id") or ""),
                "claim_text": claim_text,
                "source_fact_ids": source_fact_ids,
                "support_level": str(claim.get("support_level") or support_levels[0] or ""),
            }
        )

    unsupported_claims = [str(item).strip() for item in _string_list(candidate_facts_packet.get("unsupported_claims")) if str(item).strip()]
    unsupported_lower = {item.casefold() for item in unsupported_claims}
    normalized_allowed_claims = [
        claim for claim in normalized_allowed_claims if claim["claim_text"].casefold() not in unsupported_lower
    ]
    evidence_items = [
        item for item in evidence_items if item["claim_text"].casefold() not in unsupported_lower
    ]
    if not normalized_allowed_claims or not evidence_items:
        raise ValueError("positioning_fake_output_unavailable")

    evaluation_strengths = [str(item).strip() for item in _string_list(evaluation_packet.get("strengths")) if str(item).strip()]
    evaluation_risks = [str(item).strip() for item in _string_list(evaluation_packet.get("risks")) if str(item).strip()]
    fit_assessment = str(evaluation_packet.get("fit_assessment") or "").strip()
    positioning_summary_parts = [claim["claim_text"] for claim in normalized_allowed_claims[:2]]
    positioning_summary = " ".join(positioning_summary_parts) or "Use only source-backed candidate facts."
    if fit_assessment:
        positioning_summary = f"{positioning_summary} Fit assessment: {fit_assessment}"

    recommended_angle = normalized_allowed_claims[0]["claim_text"]
    support_summary = dict(candidate_facts_packet.get("support_summary") or {})
    claim_categories = [item.get("category") for item in evidence_items if item.get("category")]
    fake_packet = {
        "schema_version": POSITIONING_PACKET_SCHEMA_VERSION,
        "skill_id": POSITIONING_SKILL_ID,
        "status": "POSITIONING_READY",
        "positioning_summary": positioning_summary,
        "target_narrative": fit_assessment or recommended_angle,
        "evidence": [item["safe_summary"] for item in evidence_items],
        "gaps": [item for item in evaluation_packet.get("missing_information") or [] if isinstance(item, str)],
        "risks_and_mitigations": evaluation_risks,
        "recommended_angle": recommended_angle,
        "claims_to_use": [claim["claim_text"] for claim in normalized_allowed_claims],
        "claims_to_avoid": [str(item).strip() for item in _string_list(candidate_facts_packet.get("claims_to_avoid")) if str(item).strip()],
        "missing_information": [item for item in evaluation_packet.get("missing_information") or [] if isinstance(item, str)],
        "next_step": "POSITIONING_READY_FOR_DOCUMENTS",
        "candidate_ref": str(candidate_facts_packet.get("candidate_ref") or ""),
        "evidence_items": evidence_items,
        "allowed_claims": normalized_allowed_claims,
        "unsupported_claims": unsupported_claims,
        "source_references": source_references,
        "support_summary": support_summary,
        "privacy_notes": [str(item) for item in _string_list(candidate_facts_packet.get("privacy_notes")) if str(item).strip()],
        "generation_mode": "deterministic_fake",
        "source_kind": "fake_candidate_facts",
        "provider_called": False,
        "executor_called": False,
        "provenance": {
            "source": "candidate_facts_deterministic_fake",
            "provider_called": False,
            "executor_called": False,
            "generation_mode": "deterministic_fake",
            "source_kind": "fake_candidate_facts",
            "candidate_fact_count": len(fact_items),
            "allowed_claim_count": len(normalized_allowed_claims),
            "strength_count": len(evaluation_strengths),
            "category_counts": {category: claim_categories.count(category) for category in sorted(set(claim_categories))},
        },
    }
    unsafe_code = detect_unsafe_content(fake_packet)
    if unsafe_code:
        raise ValueError("positioning_unsafe_output_detected")
    return fake_packet


def _validate_fake_positioning_output(raw_result: dict[str, Any]) -> str | None:
    output_error = _validate_positioning_packet_contract(raw_result)
    if output_error is not None:
        if output_error == "positioning_packet_unsafe":
            return "positioning_unsafe_output_detected"
        return output_error
    if raw_result.get("generation_mode") != "deterministic_fake":
        return "positioning_fake_output_invalid"
    if raw_result.get("source_kind") != "fake_candidate_facts":
        return "positioning_fake_output_invalid"
    if raw_result.get("provider_called") is not False:
        return "positioning_fake_output_invalid"
    if raw_result.get("executor_called") is not False:
        return "positioning_fake_output_invalid"
    if not isinstance(raw_result.get("candidate_ref"), str) or not str(raw_result.get("candidate_ref") or "").strip():
        return "positioning_fake_output_invalid"
    if not isinstance(raw_result.get("allowed_claims"), list) or not raw_result["allowed_claims"]:
        return "positioning_fake_output_invalid"
    if not isinstance(raw_result.get("evidence_items"), list) or not raw_result["evidence_items"]:
        return "positioning_fake_output_invalid"
    if not isinstance(raw_result.get("source_references"), list) or not raw_result["source_references"]:
        return "positioning_fake_output_invalid"
    if not isinstance(raw_result.get("support_summary"), dict):
        return "positioning_fake_output_invalid"
    for claim in _dict_list(raw_result.get("allowed_claims")):
        source_fact_ids = [item for item in _string_list(claim.get("source_fact_ids")) if item.strip()]
        if not source_fact_ids:
            return "positioning_claim_without_source_fact"
    source_ref_ids = {
        str(ref.get("source_ref_id") or "").strip()
        for ref in _dict_list(raw_result.get("source_references"))
        if str(ref.get("source_ref_id") or "").strip()
    }
    for evidence in _dict_list(raw_result.get("evidence_items")):
        fact_ids = [item for item in _string_list(evidence.get("source_fact_ids")) if item.strip()]
        ref_ids = [item for item in _string_list(evidence.get("source_ref_ids")) if item.strip()]
        if not fact_ids:
            return "positioning_claim_without_source_fact"
        if not ref_ids:
            return "positioning_evidence_without_source"
        if not set(ref_ids).issubset(source_ref_ids):
            return "positioning_evidence_without_source"
    unsafe_code = detect_unsafe_content(raw_result)
    if unsafe_code:
        return "positioning_unsafe_output_detected"
    return None


def _evaluation_downstream_gates() -> dict[str, Any]:
    return {
        "outbound": {"enabled": False},
        "db_write": {"enabled": False},
        "crm_write": {"enabled": False},
        "document_generation": {"enabled": False},
    }


def _application_materials_downstream_gates(*, controlled_document_dry_run_enabled: bool) -> dict[str, Any]:
    return {
        "outbound": {"enabled": False},
        "db_write": {"enabled": False},
        "crm_write": {"enabled": False},
        "document_generation": {"enabled": False},
        "gmail_draft": {"enabled": False},
        "linkedin_send": {"enabled": False},
        "controlled_document_dry_run": {"enabled": controlled_document_dry_run_enabled},
    }


def _validate_application_materials_input_gate(
    positioning_packet: dict[str, Any] | None,
    private_context_status: str,
) -> str | None:
    if private_context_status != "PRIVATE_CONTEXT_AVAILABLE":
        return "private_context_not_ready_for_application_materials"
    return _validate_positioning_packet_contract(positioning_packet)


def _validate_positioning_packet_contract(positioning_packet: dict[str, Any] | None) -> str | None:
    if not isinstance(positioning_packet, dict):
        return "positioning_packet_missing"
    missing_fields = [field for field in REQUIRED_POSITIONING_PACKET_FIELDS if field not in positioning_packet]
    if missing_fields:
        return f"missing_required_positioning_output_fields:{','.join(missing_fields)}"
    if positioning_packet.get("schema_version") != POSITIONING_PACKET_SCHEMA_VERSION:
        return "positioning_packet_schema_version_invalid"
    if positioning_packet.get("skill_id") != POSITIONING_SKILL_ID:
        return "positioning_packet_skill_id_invalid"
    if positioning_packet.get("status") != "POSITIONING_READY":
        return "positioning_packet_status_not_ready"
    if positioning_packet.get("next_step") != "POSITIONING_READY_FOR_DOCUMENTS":
        return "positioning_packet_next_step_invalid"
    if not isinstance(positioning_packet.get("allowed_claims"), list) or not positioning_packet["allowed_claims"]:
        return "positioning_packet_invalid"
    if not isinstance(positioning_packet.get("evidence_items"), list) or not positioning_packet["evidence_items"]:
        return "positioning_packet_invalid"
    if not isinstance(positioning_packet.get("source_references"), list) or not positioning_packet["source_references"]:
        return "positioning_packet_invalid"
    if not isinstance(positioning_packet.get("claims_to_avoid"), list):
        return "positioning_packet_invalid"
    source_ref_ids = {
        str(ref.get("source_ref_id") or "").strip()
        for ref in _dict_list(positioning_packet.get("source_references"))
        if str(ref.get("source_ref_id") or "").strip()
    }
    if not source_ref_ids:
        return "positioning_packet_source_ref_invalid"
    unsupported_claims = {
        item.casefold()
        for item in _string_list(positioning_packet.get("unsupported_claims"))
        if item.strip()
    }
    for claim in _dict_list(positioning_packet.get("allowed_claims")):
        claim_text = str(claim.get("claim_text") or "").strip()
        source_fact_ids = [item for item in _string_list(claim.get("source_fact_ids")) if item.strip()]
        if not source_fact_ids:
            return "positioning_packet_claim_without_source"
        if claim_text and claim_text.casefold() in unsupported_claims:
            return "positioning_packet_invalid"
    for evidence in _dict_list(positioning_packet.get("evidence_items")):
        claim_text = str(evidence.get("claim_text") or "").strip()
        source_fact_ids = [item for item in _string_list(evidence.get("source_fact_ids")) if item.strip()]
        evidence_ref_ids = [item for item in _string_list(evidence.get("source_ref_ids")) if item.strip()]
        if not source_fact_ids:
            return "positioning_packet_evidence_without_source"
        if not evidence_ref_ids:
            return "positioning_packet_evidence_without_source"
        if not set(evidence_ref_ids).issubset(source_ref_ids):
            return "positioning_packet_source_ref_invalid"
        if claim_text and claim_text.casefold() in unsupported_claims:
            return "positioning_packet_invalid"
    unsafe_code = detect_unsafe_content(positioning_packet)
    if unsafe_code:
        return "positioning_packet_unsafe"
    return None


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
