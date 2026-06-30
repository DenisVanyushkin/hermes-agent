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
from .recruiter_application_materials_flow import (
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
    repo_root: str | Path | None = None,
    private_context_status: str = "PRIVATE_CONTEXT_NOT_INSPECTED",
    allow_provider_execution: bool = False,
    executor_factory: Callable[[], Any] | None = None,
) -> RecruiterDryRunReport:
    base_report = RecruiterDryRunReport(
        status=RecruiterDryRunStatus.POSITIONING_INPUT_BLOCKED,
        context_status="POSITIONING_INPUT_REQUIRED",
        input={
            "repo_root": str(repo_root) if repo_root is not None else None,
            "private_context_status": private_context_status,
            "allow_provider_execution": allow_provider_execution,
        },
        readiness={"ready": False, "reason": "positioning_input_not_ready"},
        context_packet=None,
        evaluation_flow=None,
        evaluation_result=_sanitize_result(evaluation_packet or {}) if isinstance(evaluation_packet, dict) else None,
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

    base_report.context_status = "READY"
    base_report.readiness = {"ready": True, "reason": "positioning_input_ready"}
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
            skill_input=_build_positioning_input(
                evaluation_packet=dict(evaluation_packet or {}),
                repo_root=repo_root,
                private_context_status=private_context_status,
            ),
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


def run_recruiter_application_materials_flow_dry_run(
    *,
    positioning_packet: dict[str, Any] | None,
    repo_root: str | Path | None = None,
    private_context_status: str = "PRIVATE_CONTEXT_NOT_INSPECTED",
    allow_provider_execution: bool = False,
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
        },
        readiness={"ready": False, "reason": "application_materials_input_not_ready"},
        context_packet=None,
        evaluation_flow=None,
        evaluation_result=None,
        positioning_result=_sanitize_result(positioning_packet or {}) if isinstance(positioning_packet, dict) else None,
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
    input_error = _validate_application_materials_input_gate(positioning_packet, private_context_status)
    if input_error is not None:
        base_report.errors = [input_error]
        base_report.readiness["reason"] = input_error
        return base_report

    base_report.context_status = "READY"
    base_report.readiness = {"ready": True, "reason": "application_materials_input_ready"}
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
    repo_root: str | Path | None,
    private_context_status: str,
) -> dict[str, Any]:
    return {
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


def _sanitize_result(raw_result: dict[str, Any]) -> dict[str, Any]:
    payload = dict(raw_result)
    payload["provenance"] = dict(payload.get("provenance") or {})
    return payload


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
    if not isinstance(positioning_packet, dict):
        return "positioning_packet_missing"
    missing_fields = [field for field in REQUIRED_POSITIONING_PACKET_FIELDS if field not in positioning_packet]
    if missing_fields:
        return f"missing_required_positioning_output_fields:{','.join(missing_fields)}"
    if positioning_packet.get("schema_version") != POSITIONING_PACKET_SCHEMA_VERSION:
        return "positioning_packet_schema_version_invalid"
    if positioning_packet.get("skill_id") != POSITIONING_SKILL_ID:
        return "positioning_packet_skill_id_invalid"
    if private_context_status != "PRIVATE_CONTEXT_AVAILABLE":
        return "private_context_not_ready_for_application_materials"
    if positioning_packet.get("status") != "POSITIONING_READY":
        return "positioning_packet_status_not_ready"
    if positioning_packet.get("next_step") != "POSITIONING_READY_FOR_DOCUMENTS":
        return "positioning_packet_next_step_invalid"
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
