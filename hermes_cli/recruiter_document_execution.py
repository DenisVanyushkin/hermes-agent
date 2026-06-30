from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Protocol

from .recruiter_document_inputs import (
    RecruiterDocumentInputPacket,
    RecruiterDocumentInputStatus,
    build_recruiter_document_writer_input_packet,
)
from .recruiter_skill_execution import RecruiterSkillExecutionReport


DOCUMENT_WRITER_SKILL_ID = "document-writer"
DOCUMENT_REVIEWER_SKILL_ID = "document-reviewer"
DOCUMENT_PACKET_SCHEMA_VERSION = "recruiter_document_packet_v1"
DOCUMENT_REVIEWER_VERDICTS = {"APPROVE", "CHANGES_REQUESTED", "BLOCKED"}
def _document_writer_expected_schema(requested_document_type: str) -> dict[str, Any]:
    return {
        "schema_version": DOCUMENT_PACKET_SCHEMA_VERSION,
        "document_type": [requested_document_type],
        "status": ["DRAFT_READY"],
        "draft": {
            "format": ["text"],
            "content": "required",
            "notes": "required_list",
        },
    }

FORBIDDEN_ACTIONS = [
    "call_provider_model",
    "send_outbound_message",
    "apply_to_job",
    "write_crm",
    "write_job_intel_db",
    "create_gmail_draft",
    "send_gmail",
    "read_private_file_contents",
    "mutate_live_config",
    "restart_gateway",
]


class RecruiterDocumentExecutionStatus(str, Enum):
    DOCUMENT_EXECUTION_BLOCKED = "DOCUMENT_EXECUTION_BLOCKED"
    DOCUMENT_EXECUTOR_NOT_WIRED = "DOCUMENT_EXECUTOR_NOT_WIRED"
    DOCUMENT_INPUT_NOT_READY = "DOCUMENT_INPUT_NOT_READY"
    DOCUMENT_OUTPUT_INVALID = "DOCUMENT_OUTPUT_INVALID"
    DOCUMENT_REVIEW_INVALID = "DOCUMENT_REVIEW_INVALID"
    DOCUMENT_REVIEW_CHANGES_REQUESTED = "DOCUMENT_REVIEW_CHANGES_REQUESTED"
    DOCUMENT_REVIEW_APPROVED = "DOCUMENT_REVIEW_APPROVED"


class RecruiterDocumentExecutor(Protocol):
    provider_backed: bool

    def execute(
        self,
        *,
        skill_id: str,
        skill_input: dict[str, Any],
        expected_schema: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...


@dataclass(slots=True)
class RecruiterDocumentExecutionReport:
    status: RecruiterDocumentExecutionStatus
    document_type: str
    writer_input_status: str
    execution_status: str
    writer_called: bool
    reviewer_called: bool
    provider_called: bool
    document_writer_input_packet: dict[str, Any] | None
    document_packet: dict[str, Any] | None
    review_result: dict[str, Any] | None
    downstream_gates: dict[str, Any]
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    provenance: dict[str, Any] = field(default_factory=dict)
    forbidden_actions: list[str] = field(default_factory=lambda: list(FORBIDDEN_ACTIONS))

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        return data


def run_recruiter_document_execution(
    execution_report: dict[str, Any] | RecruiterSkillExecutionReport,
    document_type: str,
    *,
    audience: str | None = None,
    purpose: str | None = None,
    allow_document_execution: bool = False,
    executor: RecruiterDocumentExecutor | None = None,
) -> RecruiterDocumentExecutionReport:
    input_packet = build_recruiter_document_writer_input_packet(
        execution_report,
        document_type,
        audience=audience,
        purpose=purpose,
    )
    base_provenance = {
        **dict(input_packet.provenance),
        "builder": "recruiter_document_execution",
        "provider_called": False,
        "writes_performed": False,
    }
    base_report = RecruiterDocumentExecutionReport(
        status=RecruiterDocumentExecutionStatus.DOCUMENT_INPUT_NOT_READY,
        document_type=document_type,
        writer_input_status=input_packet.status.value,
        execution_status="blocked_by_document_writer_input",
        writer_called=False,
        reviewer_called=False,
        provider_called=False,
        document_writer_input_packet=input_packet.to_dict(),
        document_packet=None,
        review_result=None,
        downstream_gates=_build_downstream_gates(input_packet.status.value),
        warnings=list(input_packet.warnings),
        errors=list(input_packet.errors),
        provenance=base_provenance,
    )
    if input_packet.status is not RecruiterDocumentInputStatus.READY:
        return base_report

    if not allow_document_execution:
        base_report.status = RecruiterDocumentExecutionStatus.DOCUMENT_EXECUTION_BLOCKED
        base_report.execution_status = "blocked_by_document_execution_fuse"
        return base_report

    if executor is None:
        base_report.status = RecruiterDocumentExecutionStatus.DOCUMENT_EXECUTOR_NOT_WIRED
        base_report.execution_status = "document_execution_enabled_but_executor_unavailable"
        return base_report

    provider_called = bool(getattr(executor, "provider_backed", False))
    writer_input = dict(input_packet.document_writer_input or {})
    requested_document_type = str(writer_input.get("requested_document_type") or document_type)
    try:
        writer_result = executor.execute(
            skill_id=DOCUMENT_WRITER_SKILL_ID,
            skill_input=writer_input,
            expected_schema=_document_writer_expected_schema(requested_document_type),
        )
    except Exception as exc:
        return RecruiterDocumentExecutionReport(
            status=RecruiterDocumentExecutionStatus.DOCUMENT_OUTPUT_INVALID,
            document_type=document_type,
            writer_input_status=input_packet.status.value,
            execution_status="document_writer_execution_failed",
            writer_called=True,
            reviewer_called=False,
            provider_called=provider_called,
            document_writer_input_packet=input_packet.to_dict(),
            document_packet=None,
            review_result=None,
            downstream_gates=_build_downstream_gates("WRITER_OUTPUT_INVALID"),
            warnings=list(input_packet.warnings),
            errors=_dedupe([*input_packet.errors, f"document_writer_execution_failed:{type(exc).__name__}"]),
            provenance={**base_provenance, "provider_called": provider_called},
        )
    writer_errors = _validate_writer_result(writer_result, requested_document_type=requested_document_type)
    writer_warnings = _dedupe([*input_packet.warnings, *_string_list(writer_result.get("warnings"))])
    writer_provenance = {
        **base_provenance,
        "provider_called": provider_called,
        "writer_result_provenance": _dict(writer_result.get("provenance")),
    }
    if writer_errors:
        return RecruiterDocumentExecutionReport(
            status=RecruiterDocumentExecutionStatus.DOCUMENT_OUTPUT_INVALID,
            document_type=document_type,
            writer_input_status=input_packet.status.value,
            execution_status="document_writer_output_invalid",
            writer_called=True,
            reviewer_called=False,
            provider_called=provider_called,
            document_writer_input_packet=input_packet.to_dict(),
            document_packet={"invalid_payload": writer_result},
            review_result=None,
            downstream_gates=_build_downstream_gates("WRITER_OUTPUT_INVALID"),
            warnings=writer_warnings,
            errors=_dedupe([*input_packet.errors, *writer_errors]),
            provenance=writer_provenance,
        )

    reviewer_input = {
        "skill_id": DOCUMENT_REVIEWER_SKILL_ID,
        "status": "REVIEW_REQUIRED",
        "document_packet": writer_result,
        "document_type": document_type,
        "audience": audience,
        "purpose": purpose,
        "boundaries": {
            "draft_only": True,
            "user_review_required": True,
            "no_outbound": True,
            "no_crm_write": True,
            "no_job_intel_db_write": True,
            "provider_execution_enabled_for_manual_cli": provider_called,
        },
    }
    try:
        reviewer_result = executor.execute(
            skill_id=DOCUMENT_REVIEWER_SKILL_ID,
            skill_input=reviewer_input,
            expected_schema={"verdict": ["APPROVE", "CHANGES_REQUESTED", "BLOCKED"]},
        )
    except Exception as exc:
        return RecruiterDocumentExecutionReport(
            status=RecruiterDocumentExecutionStatus.DOCUMENT_REVIEW_INVALID,
            document_type=document_type,
            writer_input_status=input_packet.status.value,
            execution_status="document_reviewer_execution_failed",
            writer_called=True,
            reviewer_called=True,
            provider_called=provider_called,
            document_writer_input_packet=input_packet.to_dict(),
            document_packet=writer_result,
            review_result=None,
            downstream_gates=_build_downstream_gates("REVIEW_INVALID"),
            warnings=writer_warnings,
            errors=_dedupe([*input_packet.errors, f"document_reviewer_execution_failed:{type(exc).__name__}"]),
            provenance=writer_provenance,
        )
    review_errors = _validate_reviewer_result(reviewer_result)
    combined_warnings = _dedupe([*writer_warnings, *_string_list(reviewer_result.get("warnings"))])
    combined_provenance = {
        **writer_provenance,
        "review_result_provenance": _dict(reviewer_result.get("provenance")),
    }
    if review_errors:
        return RecruiterDocumentExecutionReport(
            status=RecruiterDocumentExecutionStatus.DOCUMENT_REVIEW_INVALID,
            document_type=document_type,
            writer_input_status=input_packet.status.value,
            execution_status="document_reviewer_output_invalid",
            writer_called=True,
            reviewer_called=True,
            provider_called=provider_called,
            document_writer_input_packet=input_packet.to_dict(),
            document_packet=writer_result,
            review_result={"invalid_payload": reviewer_result},
            downstream_gates=_build_downstream_gates("REVIEW_INVALID"),
            warnings=combined_warnings,
            errors=_dedupe([*input_packet.errors, *review_errors]),
            provenance=combined_provenance,
        )

    verdict = str(reviewer_result.get("verdict") or "")
    if verdict != "APPROVE":
        return RecruiterDocumentExecutionReport(
            status=RecruiterDocumentExecutionStatus.DOCUMENT_REVIEW_CHANGES_REQUESTED,
            document_type=document_type,
            writer_input_status=input_packet.status.value,
            execution_status="document_review_changes_requested",
            writer_called=True,
            reviewer_called=True,
            provider_called=provider_called,
            document_writer_input_packet=input_packet.to_dict(),
            document_packet=writer_result,
            review_result=reviewer_result,
            downstream_gates=_build_downstream_gates("REVIEW_CHANGES_REQUESTED"),
            warnings=combined_warnings,
            errors=list(input_packet.errors),
            provenance=combined_provenance,
        )

    return RecruiterDocumentExecutionReport(
        status=RecruiterDocumentExecutionStatus.DOCUMENT_REVIEW_APPROVED,
        document_type=document_type,
        writer_input_status=input_packet.status.value,
        execution_status="document_review_approved",
        writer_called=True,
        reviewer_called=True,
        provider_called=provider_called,
        document_writer_input_packet=input_packet.to_dict(),
        document_packet=writer_result,
        review_result=reviewer_result,
        downstream_gates=_build_downstream_gates("APPROVED"),
        warnings=combined_warnings,
        errors=list(input_packet.errors),
        provenance=combined_provenance,
    )


def _validate_writer_result(payload: dict[str, Any], *, requested_document_type: str) -> list[str]:
    errors: list[str] = []
    if payload.get("schema_version") != DOCUMENT_PACKET_SCHEMA_VERSION:
        errors.append("writer_schema_version_invalid")
    if payload.get("document_type") != requested_document_type:
        errors.append("writer_document_type_mismatch")
    if str(payload.get("status") or "") not in {"READY", "DRAFT_READY", "SUCCESS"}:
        errors.append("writer_status_not_ready")
    if not _has_source_provenance(payload):
        errors.append("writer_source_provenance_missing")
    draft = payload.get("draft")
    if not isinstance(draft, dict) or not isinstance(draft.get("content"), str):
        errors.append("writer_draft_missing_or_invalid")
    if any(payload.get(flag) for flag in ("send_outbound_message", "apply_to_job", "write_crm", "write_job_intel_db")):
        errors.append("writer_forbidden_action_flag_set")
    return errors


def _validate_reviewer_result(payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    required_fields = [
        "status",
        "skill_id",
        "verdict",
        "hallucination_risk",
        "unsupported_claims",
        "genericness_assessment",
        "tone_seniority_assessment",
        "missing_source_references",
        "required_changes",
        "warnings",
        "errors",
        "provenance",
    ]
    for field_name in required_fields:
        if field_name not in payload:
            errors.append(f"reviewer_missing_field:{field_name}")
    if payload.get("skill_id") != DOCUMENT_REVIEWER_SKILL_ID:
        errors.append("reviewer_skill_id_invalid")
    if str(payload.get("verdict") or "") not in DOCUMENT_REVIEWER_VERDICTS:
        errors.append("reviewer_verdict_invalid")
    return errors


def _build_downstream_gates(review_status: str) -> dict[str, Any]:
    document_review_status = "WRITER_OUTPUT_REQUIRED"
    outbound_status = "BLOCKED_USER_REVIEW_REQUIRED"
    if review_status == "APPROVED":
        document_review_status = "APPROVED"
    elif review_status in {"REVIEW_INVALID", "REVIEW_CHANGES_REQUESTED"}:
        document_review_status = review_status
    elif review_status == "READY":
        document_review_status = "REVIEW_REQUIRED"
    return {
        "document_writer": {
            "skill_id": DOCUMENT_WRITER_SKILL_ID,
            "status": "READY_FOR_INPUT" if review_status == "READY" else review_status,
        },
        "document_review": {
            "skill_id": DOCUMENT_REVIEWER_SKILL_ID,
            "status": document_review_status,
        },
        "outbound_delivery": {
            "status": outbound_status,
        },
        "crm_writeback": {
            "status": "BLOCKED_OUT_OF_SCOPE",
        },
    }


def _has_source_provenance(payload: dict[str, Any]) -> bool:
    return any(
        key in payload and payload.get(key)
        for key in ("source_positioning_packet_id", "source_positioning_packet_ref", "provenance")
    )


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            ordered.append(item)
    return ordered
