from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Protocol

from .recruiter_document_execution import (
    DOCUMENT_REVIEWER_SKILL_ID,
    RecruiterDocumentExecutionStatus,
    run_recruiter_document_execution,
)
from .recruiter_document_inputs import RecruiterDocumentInputStatus, build_recruiter_document_writer_input_packet
from .recruiter_outward_drafts import compose_deterministic_outward_draft, is_deterministic_outward_document_type


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
        audience = "Hiring manager" if document_type == "cover_letter" else "Recruiter"
        if is_deterministic_outward_document_type(document_type):
            review_report = _run_deterministic_outward_document_review(
                execution_report=execution_report,
                document_type=document_type,
                audience=audience,
                purpose="Draft application materials for user review",
                executor=executor,
            )
            runs[output_key] = dict(review_report)
            writer_called = writer_called or bool(review_report["writer_called"])
            reviewer_called = reviewer_called or bool(review_report["reviewer_called"])
            provider_called = provider_called or bool(review_report["provider_called"])
            warnings.extend(list(review_report["warnings"]))
            if review_report["status"] != RecruiterDocumentExecutionStatus.DOCUMENT_REVIEW_APPROVED.value:
                return RecruiterApplicationMaterialsReport(
                    status=RecruiterApplicationMaterialsStatus.REVIEW_BLOCKED.value
                    if review_report["status"] == RecruiterDocumentExecutionStatus.DOCUMENT_REVIEW_CHANGES_REQUESTED.value
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
                    errors=list(review_report["errors"]),
                )
            draft = dict((review_report["document_packet"] or {}).get("draft") or {})
            materials[output_key] = {
                "document_type": document_type,
                "content": draft.get("content"),
                "notes": list(draft.get("notes") or []),
            }
            review_verdicts.append(str((review_report["review_result"] or {}).get("verdict") or "UNKNOWN"))
            continue
        report = run_recruiter_document_execution(
            execution_report,
            document_type=document_type,
            audience=audience,
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



def _run_deterministic_outward_document_review(
    *,
    execution_report: dict[str, Any],
    document_type: str,
    audience: str,
    purpose: str,
    executor: RecruiterApplicationMaterialsExecutor,
) -> dict[str, Any]:
    input_packet = build_recruiter_document_writer_input_packet(
        execution_report,
        document_type=document_type,
        audience=audience,
        purpose=purpose,
    )
    if input_packet.status is not RecruiterDocumentInputStatus.READY:
        return {
            "status": RecruiterDocumentExecutionStatus.DOCUMENT_INPUT_NOT_READY.value,
            "document_type": document_type,
            "writer_input_status": input_packet.status.value,
            "execution_status": "blocked_by_document_writer_input",
            "writer_called": False,
            "reviewer_called": False,
            "provider_called": False,
            "document_writer_input_packet": input_packet.to_dict(),
            "document_packet": None,
            "review_result": None,
            "downstream_gates": _deterministic_outward_downstream_gates("DOCUMENT_INPUT_NOT_READY"),
            "warnings": list(input_packet.warnings),
            "errors": list(input_packet.errors),
            "provenance": {"writes_performed": False, "builder": "recruiter_application_materials_flow"},
        }

    document_packet = compose_deterministic_outward_draft(dict(input_packet.document_writer_input or {}))
    provider_called = bool(getattr(executor, "provider_backed", False))
    reviewer_input = {
        "skill_id": DOCUMENT_REVIEWER_SKILL_ID,
        "status": "REVIEW_REQUIRED",
        "document_packet": document_packet,
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
        return {
            "status": RecruiterDocumentExecutionStatus.DOCUMENT_REVIEW_INVALID.value,
            "document_type": document_type,
            "writer_input_status": input_packet.status.value,
            "execution_status": "document_reviewer_execution_failed",
            "writer_called": False,
            "reviewer_called": True,
            "provider_called": provider_called,
            "document_writer_input_packet": input_packet.to_dict(),
            "document_packet": document_packet,
            "review_result": None,
            "downstream_gates": _deterministic_outward_downstream_gates("REVIEW_INVALID"),
            "warnings": list(input_packet.warnings),
            "errors": [f"document_reviewer_execution_failed:{type(exc).__name__}"],
            "provenance": {"writes_performed": False, "builder": "recruiter_application_materials_flow"},
        }

    review_errors = _validate_reviewer_result(reviewer_result)
    if review_errors:
        return {
            "status": RecruiterDocumentExecutionStatus.DOCUMENT_REVIEW_INVALID.value,
            "document_type": document_type,
            "writer_input_status": input_packet.status.value,
            "execution_status": "document_reviewer_output_invalid",
            "writer_called": False,
            "reviewer_called": True,
            "provider_called": provider_called,
            "document_writer_input_packet": input_packet.to_dict(),
            "document_packet": document_packet,
            "review_result": {"invalid_payload": reviewer_result},
            "downstream_gates": _deterministic_outward_downstream_gates("REVIEW_INVALID"),
            "warnings": _dedupe([*input_packet.warnings, *_string_list(reviewer_result.get("warnings"))]),
            "errors": review_errors,
            "provenance": {"writes_performed": False, "builder": "recruiter_application_materials_flow"},
        }

    verdict = str(reviewer_result.get("verdict") or "")
    status = (
        RecruiterDocumentExecutionStatus.DOCUMENT_REVIEW_APPROVED.value
        if verdict == "APPROVE"
        else RecruiterDocumentExecutionStatus.DOCUMENT_REVIEW_CHANGES_REQUESTED.value
    )
    return {
        "status": status,
        "document_type": document_type,
        "writer_input_status": input_packet.status.value,
        "execution_status": "document_review_approved" if verdict == "APPROVE" else "document_review_changes_requested",
        "writer_called": False,
        "reviewer_called": True,
        "provider_called": provider_called,
        "document_writer_input_packet": input_packet.to_dict(),
        "document_packet": document_packet,
        "review_result": reviewer_result,
        "downstream_gates": _deterministic_outward_downstream_gates("APPROVED" if verdict == "APPROVE" else "REVIEW_CHANGES_REQUESTED"),
        "warnings": _dedupe([*input_packet.warnings, *_string_list(reviewer_result.get("warnings"))]),
        "errors": list(input_packet.errors),
        "provenance": {"writes_performed": False, "builder": "recruiter_application_materials_flow"},
    }


def _validate_reviewer_result(payload: dict[str, Any]) -> list[str]:
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
    errors = [f"reviewer_missing_field:{field_name}" for field_name in required_fields if field_name not in payload]
    if payload.get("skill_id") != DOCUMENT_REVIEWER_SKILL_ID:
        errors.append("reviewer_skill_id_invalid")
    if str(payload.get("verdict") or "") not in {"APPROVE", "CHANGES_REQUESTED", "BLOCKED"}:
        errors.append("reviewer_verdict_invalid")
    return errors


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _deterministic_outward_downstream_gates(review_status: str) -> dict[str, Any]:
    document_review_status = "REVIEW_REQUIRED"
    if review_status == "APPROVED":
        document_review_status = "APPROVED"
    elif review_status in {"REVIEW_INVALID", "REVIEW_CHANGES_REQUESTED"}:
        document_review_status = review_status
    return {
        "document_writer": {
            "skill_id": "document-writer",
            "status": "SKIPPED_DETERMINISTIC_OUTWARD_COMPOSER",
        },
        "document_review": {
            "skill_id": DOCUMENT_REVIEWER_SKILL_ID,
            "status": document_review_status,
        },
        "outbound_delivery": {
            "status": "BLOCKED_USER_REVIEW_REQUIRED",
        },
        "crm_writeback": {
            "status": "BLOCKED_OUT_OF_SCOPE",
        },
    }


def _build_synthetic_execution_report(positioning_packet: dict[str, Any]) -> dict[str, Any]:
    allowed_claims = [
        {
            "claim_id": str(item.get("claim_id") or ""),
            "claim_text": str(item.get("claim_text") or ""),
            "source_fact_ids": [str(ref) for ref in item.get("source_fact_ids") or [] if isinstance(ref, str)],
            "support_level": str(item.get("support_level") or ""),
        }
        for item in positioning_packet.get("allowed_claims") or []
        if isinstance(item, dict)
    ]
    evidence_items = [
        {
            "claim_text": str(item.get("claim_text") or ""),
            "source_fact_ids": [str(ref) for ref in item.get("source_fact_ids") or [] if isinstance(ref, str)],
            "source_ref_ids": [str(ref) for ref in item.get("source_ref_ids") or [] if isinstance(ref, str)],
            "support_level": str(item.get("support_level") or ""),
            "category": str(item.get("category") or ""),
            "safe_summary": str(item.get("safe_summary") or ""),
        }
        for item in positioning_packet.get("evidence_items") or []
        if isinstance(item, dict)
    ]
    source_references = [
        {
            "source_ref_id": str(item.get("source_ref_id") or ""),
            "source_label": str(item.get("source_label") or ""),
            "source_id_hash": str(item.get("source_id_hash") or ""),
            "section_label": str(item.get("section_label") or ""),
            "support_level": str(item.get("support_level") or ""),
            "category": str(item.get("category") or ""),
        }
        for item in positioning_packet.get("source_references") or []
        if isinstance(item, dict)
    ]
    synthetic_positioning_result = {
        "status": "SUCCESS",
        "skill_id": "positioning-and-evidence",
        "positioning_summary": str(positioning_packet.get("positioning_summary") or ""),
        "evidence_map": {
            "positioning_evidence": [item["safe_summary"] for item in evidence_items if item.get("safe_summary")],
            "source_references": source_references,
        },
        "proven_facts": [item["claim_text"] for item in allowed_claims if item.get("claim_text")],
        "derived_positioning": [str(positioning_packet.get("recommended_angle") or "")] if positioning_packet.get("recommended_angle") else [],
        "gaps": list(positioning_packet.get("gaps") or []),
        "risks_and_mitigations": list(positioning_packet.get("risks_and_mitigations") or []),
        "allowed_claims": allowed_claims,
        "evidence_items": evidence_items,
        "source_references": source_references,
        "claims_to_avoid": list(positioning_packet.get("claims_to_avoid") or []),
        "support_summary": dict(positioning_packet.get("support_summary") or {}),
        "privacy_notes": list(positioning_packet.get("privacy_notes") or []),
        "generation_mode": str(positioning_packet.get("generation_mode") or ""),
        "source_kind": str(positioning_packet.get("source_kind") or ""),
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
