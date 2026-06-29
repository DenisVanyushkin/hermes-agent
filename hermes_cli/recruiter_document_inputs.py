from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any

from .recruiter_skill_execution import RecruiterSkillExecutionReport


ALLOWED_DOCUMENT_TYPES = [
    "cover_letter",
    "recruiter_message",
    "linkedin_dm",
    "follow_up",
    "cv_tailoring_notes",
    "application_answer",
    "executive_bio",
]
REQUIRED_POSITIONING_FIELDS = [
    "positioning_summary",
    "evidence_map",
    "proven_facts",
    "derived_positioning",
    "gaps",
    "risks_and_mitigations",
]
_READY_EXECUTION_STATUSES = {"EXECUTION_READY", "SUCCESS", "READY"}
_READY_POSITIONING_STATUSES = {"SUCCESS", "READY", "POSITIONING_AVAILABLE"}
_POSITIONING_REQUIRED_REASON = "document-writer requires positioning-and-evidence output packet"
_SOT_REFERENCES = [
    "docs/hermes-recruiter-action-plan.md",
    "docs/hermes-recruiter-skill-package-architecture-sot.md",
    "docs/career-search-source-of-truth.md",
]
_SKILL_REFERENCES = [
    "role-packages/recruiter/skills/document-writer/SKILL.md",
    "role-packages/recruiter/skills/document-reviewer/SKILL.md",
]
_FORBIDDEN_ACTIONS = [
    "call_provider_model",
    "execute_document_writer",
    "generate_final_draft",
    "send_outbound_message",
    "apply_to_job",
    "write_crm",
    "write_job_intel_db",
    "read_private_file_contents",
    "mutate_live_config",
    "restart_gateway",
]


class RecruiterDocumentInputStatus(str, Enum):
    READY = "READY"
    POSITIONING_REQUIRED = "POSITIONING_REQUIRED"
    BLOCKED_INVALID_EXECUTION_REPORT = "BLOCKED_INVALID_EXECUTION_REPORT"
    BLOCKED_POSITIONING_RESULT_INVALID = "BLOCKED_POSITIONING_RESULT_INVALID"
    BLOCKED_UNSUPPORTED_DOCUMENT_TYPE = "BLOCKED_UNSUPPORTED_DOCUMENT_TYPE"
    BLOCKED_MISSING_VACANCY_CONTEXT = "BLOCKED_MISSING_VACANCY_CONTEXT"


@dataclass(slots=True)
class RecruiterDocumentInputPacket:
    status: RecruiterDocumentInputStatus
    document_writer_input: dict[str, Any] | None
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    provenance: dict[str, Any] = field(default_factory=dict)
    forbidden_actions: list[str] = field(default_factory=lambda: list(_FORBIDDEN_ACTIONS))
    downstream_gates: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        return data


def build_recruiter_document_writer_input_packet(
    execution_report: dict[str, Any] | RecruiterSkillExecutionReport,
    document_type: str,
    audience: str | None = None,
    purpose: str | None = None,
) -> RecruiterDocumentInputPacket:
    report = _report_dict(execution_report)
    if report is None:
        return _blocked_packet(
            RecruiterDocumentInputStatus.BLOCKED_INVALID_EXECUTION_REPORT,
            errors=["invalid_execution_report_type"],
        )

    warnings = _dedupe(_string_list(report.get("warnings")))
    errors = _dedupe(_string_list(report.get("errors")))
    provenance = {
        **_dict(report.get("provenance")),
        "writes_performed": False,
        "builder": "recruiter_document_inputs",
    }

    if document_type not in ALLOWED_DOCUMENT_TYPES:
        return _blocked_packet(
            RecruiterDocumentInputStatus.BLOCKED_UNSUPPORTED_DOCUMENT_TYPE,
            warnings=warnings,
            errors=[*errors, f"unsupported_document_type:{document_type}"],
            provenance=provenance,
        )

    execution_status = str(report.get("status") or "")
    vacancy_result = _dict(report.get("vacancy_evaluation_result"))
    positioning_result = _dict(report.get("positioning_evidence_result"))
    gate = _dict(_dict(report.get("downstream_gates")).get("document_writer"))
    gate_status = str(gate.get("status") or "")

    if execution_status not in _READY_EXECUTION_STATUSES:
        return _blocked_packet(
            RecruiterDocumentInputStatus.BLOCKED_INVALID_EXECUTION_REPORT,
            warnings=warnings,
            errors=[*errors, f"execution_report_not_ready:{execution_status or 'UNKNOWN'}"],
            provenance=provenance,
        )

    if audience is None:
        warnings.append("audience_missing")
    if purpose is None:
        warnings.append("purpose_missing")

    if not vacancy_result:
        return _blocked_packet(
            RecruiterDocumentInputStatus.BLOCKED_MISSING_VACANCY_CONTEXT,
            warnings=warnings,
            errors=[*errors, "vacancy_evaluation_result_missing"],
            provenance=provenance,
        )

    if not positioning_result or gate_status != "POSITIONING_AVAILABLE":
        return _blocked_packet(
            RecruiterDocumentInputStatus.POSITIONING_REQUIRED,
            warnings=warnings,
            errors=[*errors, _POSITIONING_REQUIRED_REASON],
            provenance=provenance,
        )

    positioning_status = str(positioning_result.get("status") or "")
    missing_fields = [field for field in REQUIRED_POSITIONING_FIELDS if field not in positioning_result]
    if positioning_status not in _READY_POSITIONING_STATUSES or missing_fields:
        invalid_errors = list(errors)
        if positioning_status not in _READY_POSITIONING_STATUSES:
            invalid_errors.append(f"positioning_result_not_ready:{positioning_status or 'UNKNOWN'}")
        if missing_fields:
            invalid_errors.append(f"missing_positioning_fields:{','.join(missing_fields)}")
        return _blocked_packet(
            RecruiterDocumentInputStatus.BLOCKED_POSITIONING_RESULT_INVALID,
            warnings=warnings,
            errors=invalid_errors,
            provenance=provenance,
        )

    return RecruiterDocumentInputPacket(
        status=RecruiterDocumentInputStatus.READY,
        document_writer_input={
            "skill_id": "document-writer",
            "status": "READY",
            "document_type": document_type,
            "audience": audience,
            "purpose": purpose,
            "source_execution_report_ref": {
                "flow_id": report.get("flow_id"),
                "execution_status": execution_status,
            },
            "source_positioning_packet_ref": {
                "skill_id": positioning_result.get("skill_id"),
                "status": positioning_status,
            },
            "vacancy_evaluation_result": vacancy_result,
            "positioning_evidence_result": positioning_result,
            "required_source_fields": list(REQUIRED_POSITIONING_FIELDS),
            "allowed_document_types": list(ALLOWED_DOCUMENT_TYPES),
            "skill_references": list(_SKILL_REFERENCES),
            "source_of_truth_references": list(_SOT_REFERENCES),
            "expected_future_output_schema": "recruiter_document_packet_v1",
            "expected_future_output_fields": [
                "schema_version",
                "document_type",
                "audience",
                "purpose",
                "source_positioning_packet_id",
                "draft",
                "review",
                "status",
            ],
            "boundaries": {
                "no_invented_facts": True,
                "use_only_positioning_evidence": True,
                "unsupported_claims_must_be_omitted_or_flagged": True,
                "draft_only": True,
                "user_review_required": True,
                "do_not_imply_application_submission": True,
                "no_outbound": True,
                "no_crm_write": True,
                "no_job_intel_db_write": True,
                "no_private_file_content_read": True,
                "no_provider_call_in_this_slice": True,
            },
            "provenance": {
                "execution_report": provenance,
                "document_writer_gate": gate,
            },
        },
        warnings=warnings,
        errors=errors,
        provenance=provenance,
        downstream_gates={
            "document_writer": {
                "skill_id": "document-writer",
                "status": "READY_FOR_INPUT",
                "reason": "document-writer input packet ready; writer has not executed",
                "requires": ["positioning-and-evidence"],
                "references": list(_SKILL_REFERENCES),
            }
        },
    )


def _blocked_packet(
    status: RecruiterDocumentInputStatus,
    *,
    warnings: list[str] | None = None,
    errors: list[str] | None = None,
    provenance: dict[str, Any] | None = None,
) -> RecruiterDocumentInputPacket:
    return RecruiterDocumentInputPacket(
        status=status,
        document_writer_input=None,
        warnings=_dedupe(warnings or []),
        errors=_dedupe(errors or []),
        provenance={**(provenance or {}), "writes_performed": False, "builder": "recruiter_document_inputs"},
        downstream_gates={
            "document_writer": {
                "skill_id": "document-writer",
                "status": "POSITIONING_REQUIRED",
                "reason": _POSITIONING_REQUIRED_REASON,
                "requires": ["positioning-and-evidence"],
                "references": list(_SKILL_REFERENCES),
            }
        },
    )


def _report_dict(value: dict[str, Any] | RecruiterSkillExecutionReport) -> dict[str, Any] | None:
    if isinstance(value, RecruiterSkillExecutionReport):
        return value.to_dict()
    if isinstance(value, dict):
        return dict(value)
    return None


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


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result
