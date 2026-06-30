from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Protocol

from .recruiter_document_execution import (
    RecruiterDocumentExecutionStatus,
    run_recruiter_document_execution,
)


APPLICATION_MATERIALS_PACKET_SCHEMA_VERSION = "recruiter_application_materials_packet_v1"
APPLICATION_MATERIALS_DOCUMENT_TYPES = {
    "cv_tailoring_notes": "cv_tailoring_notes",
    "cover_letter_draft": "cover_letter",
    "recruiter_message_draft": "recruiter_message",
}
APPLICATION_MATERIAL_TARGETS = tuple(APPLICATION_MATERIALS_DOCUMENT_TYPES)


class RecruiterApplicationMaterialsStatus(str, Enum):
    INPUT_BLOCKED = "APPLICATION_MATERIALS_INPUT_BLOCKED"
    PROVIDER_EXECUTION_BLOCKED = "APPLICATION_MATERIALS_PROVIDER_EXECUTION_BLOCKED"
    OUTPUT_INVALID = "APPLICATION_MATERIALS_OUTPUT_INVALID"
    PROVIDER_EXECUTION_FAILED = "APPLICATION_MATERIALS_PROVIDER_EXECUTION_FAILED"
    REVIEW_BLOCKED = "APPLICATION_MATERIALS_REVIEW_BLOCKED"
    READY = "APPLICATION_MATERIALS_READY"


class RecruiterApplicationMaterialsExecutor(Protocol):
    provider_backed: bool

    def execute(
        self,
        *,
        skill_id: str,
        skill_input: dict[str, Any],
        expected_schema: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...


@dataclass(slots=True)
class RecruiterApplicationMaterialsReport:
    status: str
    schema_version: str
    writer_called: bool
    reviewer_called: bool
    provider_called: bool
    structured_output_validated: bool
    materials: dict[str, Any]
    review: dict[str, Any]
    next_step: str
    provenance: dict[str, Any]
    downstream_gates: dict[str, Any]
    document_runs: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def run_recruiter_application_materials_flow(
    *,
    positioning_packet: dict[str, Any],
    allow_document_execution: bool = False,
    document_target: str | None = None,
    executor: RecruiterApplicationMaterialsExecutor | None = None,
) -> RecruiterApplicationMaterialsReport:
    if not allow_document_execution:
        return RecruiterApplicationMaterialsReport(
            status=RecruiterApplicationMaterialsStatus.PROVIDER_EXECUTION_BLOCKED.value,
            schema_version=APPLICATION_MATERIALS_PACKET_SCHEMA_VERSION,
            writer_called=False,
            reviewer_called=False,
            provider_called=False,
            structured_output_validated=False,
            materials={},
            review={"verdict": "BLOCKED"},
            next_step="RERUN_WITH_ALLOW_PROVIDER_EXECUTION",
            provenance={"writes_performed": False, "builder": "recruiter_application_materials_flow"},
            downstream_gates=_downstream_gates(False),
            errors=["application_materials_provider_execution_blocked"],
        )

    if executor is None:
        raise ValueError("application_materials_executor_missing")

    execution_report = _build_synthetic_execution_report(positioning_packet)
    runs: dict[str, Any] = {}
    materials: dict[str, Any] = {}
    warnings: list[str] = []
    review_verdicts: list[str] = []
    writer_called = False
    reviewer_called = False
    provider_called = False

    target_keys = (document_target,) if document_target is not None else APPLICATION_MATERIAL_TARGETS
    for output_key in target_keys:
        document_type = APPLICATION_MATERIALS_DOCUMENT_TYPES[output_key]
        report = run_recruiter_document_execution(
            execution_report,
            document_type=document_type,
            audience="Hiring manager" if document_type == "cover_letter" else "Recruiter",
            purpose="Draft application materials for user review",
            allow_document_execution=True,
            executor=executor,
        )
        report_dict = report.to_dict()
        runs[output_key] = report_dict
        writer_called = writer_called or report.writer_called
        reviewer_called = reviewer_called or report.reviewer_called
        provider_called = provider_called or report.provider_called
        warnings.extend(report.warnings)
        if report.status is not RecruiterDocumentExecutionStatus.DOCUMENT_REVIEW_APPROVED:
            return RecruiterApplicationMaterialsReport(
                status=RecruiterApplicationMaterialsStatus.REVIEW_BLOCKED.value
                if report.status is RecruiterDocumentExecutionStatus.DOCUMENT_REVIEW_CHANGES_REQUESTED
                else RecruiterApplicationMaterialsStatus.OUTPUT_INVALID.value,
                schema_version=APPLICATION_MATERIALS_PACKET_SCHEMA_VERSION,
                writer_called=writer_called,
                reviewer_called=reviewer_called,
                provider_called=provider_called,
                structured_output_validated=False,
                materials=materials,
                review={"verdict": "BLOCKED", "document_type": document_type, "document_runs": runs},
                next_step="USER_REVIEW_REQUIRED",
                provenance={"writes_performed": False, "builder": "recruiter_application_materials_flow"},
                downstream_gates=_downstream_gates(True),
                document_runs=runs,
                warnings=_dedupe(warnings),
                errors=list(report.errors),
            )
        draft = dict((report.document_packet or {}).get("draft") or {})
        materials[output_key] = {
            "document_type": document_type,
            "content": draft.get("content"),
            "notes": list(draft.get("notes") or []),
        }
        review_verdicts.append(str((report.review_result or {}).get("verdict") or "UNKNOWN"))

    materials["application_summary"] = _build_application_summary(positioning_packet)
    return RecruiterApplicationMaterialsReport(
        status=RecruiterApplicationMaterialsStatus.READY.value,
        schema_version=APPLICATION_MATERIALS_PACKET_SCHEMA_VERSION,
        writer_called=writer_called,
        reviewer_called=reviewer_called,
        provider_called=provider_called,
        structured_output_validated=True,
        materials=materials,
        review={"verdict": "APPROVE", "document_review_verdicts": review_verdicts},
        next_step="USER_REVIEW_REQUIRED",
        provenance={"writes_performed": False, "builder": "recruiter_application_materials_flow"},
        downstream_gates=_downstream_gates(True),
        document_runs=runs,
        warnings=_dedupe(warnings),
        errors=[],
    )


def _build_synthetic_execution_report(positioning_packet: dict[str, Any]) -> dict[str, Any]:
    synthetic_positioning_result = {
        "status": "SUCCESS",
        "skill_id": "positioning-and-evidence",
        "positioning_summary": str(positioning_packet.get("positioning_summary") or ""),
        "evidence_map": {"positioning_evidence": list(positioning_packet.get("evidence") or [])},
        "proven_facts": list(positioning_packet.get("claims_to_use") or []),
        "derived_positioning": [str(positioning_packet.get("recommended_angle") or "")] if positioning_packet.get("recommended_angle") else [],
        "gaps": list(positioning_packet.get("gaps") or []),
        "risks_and_mitigations": list(positioning_packet.get("risks_and_mitigations") or []),
        "provenance": dict(positioning_packet.get("provenance") or {}),
    }
    return {
        "status": "EXECUTION_READY",
        "flow_id": "application-materials",
        "context_status": "READY",
        "skill_input_status": "READY",
        "execution_status": "completed",
        "provider_called": False,
        "executor_called": True,
        "vacancy_evaluation_result": {
            "status": "SUCCESS",
            "skill_id": "vacancy-evaluation",
            "vacancy_evaluation_summary": str(positioning_packet.get("target_narrative") or ""),
            "fit_interpretation": str(positioning_packet.get("positioning_summary") or ""),
            "evidence_gaps": list(positioning_packet.get("gaps") or []),
            "recommendation_for_next_step": "Proceed to draft preparation.",
            "provenance": dict(positioning_packet.get("provenance") or {}),
        },
        "positioning_evidence_result": synthetic_positioning_result,
        "downstream_gates": {
            "document_writer": {
                "skill_id": "document-writer",
                "status": "POSITIONING_AVAILABLE",
                "reason": "positioning packet available for downstream draft-only writer",
                "requires": ["positioning-and-evidence"],
                "references": ["role-packages/recruiter/skills/document-writer/SKILL.md"],
            }
        },
        "warnings": [],
        "errors": [],
        "provenance": {"writes_performed": False, "builder": "recruiter_application_materials_flow"},
        "forbidden_actions": [],
        "planned_flow": ["positioning-and-evidence", "document-writer", "document-reviewer"],
    }


def _build_application_summary(positioning_packet: dict[str, Any]) -> str:
    summary = str(positioning_packet.get("positioning_summary") or "").strip()
    angle = str(positioning_packet.get("recommended_angle") or "").strip()
    if summary and angle:
        return f"{summary} Angle: {angle}"
    return summary or angle


def _downstream_gates(controlled_document_dry_run_enabled: bool) -> dict[str, Any]:
    return {
        "outbound": {"enabled": False},
        "db_write": {"enabled": False},
        "crm_write": {"enabled": False},
        "document_generation": {"enabled": False},
        "gmail_draft": {"enabled": False},
        "linkedin_send": {"enabled": False},
        "controlled_document_dry_run": {"enabled": controlled_document_dry_run_enabled},
    }


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            ordered.append(value)
    return ordered
