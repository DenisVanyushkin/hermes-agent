from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any

from .recruiter_context import RecruiterContextPacket, RecruiterContextStatus


_REQUIRED_ROLE_ID = "hermes_recruiter"
_REQUIRED_PACKAGE_ID = "hermes-recruiter"
_VACANCY_SKILL_ID = "vacancy-evaluation"
_POSITIONING_SKILL_ID = "positioning-and-evidence"
_DOCUMENT_WRITER_SKILL_ID = "document-writer"
_PRIVATE_SOURCE_PATHS = [
    "~/.hermes/private/career/denis_vanyushkin_structured_resume_v1_1.json",
    "~/.hermes/private/career/opportunity-thesis.md",
    "~/.hermes/private/career/company_intelligence_architecture.md",
    "~/.hermes/private/career/scoring_v3.md",
]
_CAREER_SOT_PATH = "docs/career-search-source-of-truth.md"
_VACANCY_SKILL_PATH = "role-packages/recruiter/skills/vacancy-evaluation/SKILL.md"
_POSITIONING_SKILL_PATH = "role-packages/recruiter/skills/positioning-and-evidence/SKILL.md"
_DOCUMENT_WRITER_SKILL_PATH = "role-packages/recruiter/skills/document-writer/SKILL.md"
_FORBIDDEN_ACTIONS = [
    "call_provider_model",
    "execute_recruiter_skill",
    "read_private_file_contents",
    "write_job_intel_db",
    "write_crm",
    "send_outbound_message",
    "apply_to_job",
    "restart_gateway",
    "modify_router",
    "modify_gateway_config",
]


class RecruiterSkillInputStatus(str, Enum):
    READY = "READY"
    PARTIAL = "PARTIAL"
    BLOCKED_CONTEXT_NOT_READY = "BLOCKED_CONTEXT_NOT_READY"
    BLOCKED_MISSING_VACANCY = "BLOCKED_MISSING_VACANCY"
    BLOCKED_ROLE_PACKAGE_CONTEXT = "BLOCKED_ROLE_PACKAGE_CONTEXT"
    BLOCKED_INVALID_PACKET = "BLOCKED_INVALID_PACKET"
    BLOCKED_PRIVATE_CONTEXT_MISSING = "BLOCKED_PRIVATE_CONTEXT_MISSING"
    BLOCKED_SOURCE_MISSING = "BLOCKED_SOURCE_MISSING"


@dataclass(slots=True)
class RecruiterSkillInputPacket:
    status: RecruiterSkillInputStatus
    request: dict[str, Any]
    vacancy_evaluation_input: dict[str, Any] | None
    positioning_evidence_input: dict[str, Any] | None
    downstream_gates: dict[str, Any]
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    provenance: dict[str, Any] = field(default_factory=dict)
    forbidden_actions: list[str] = field(default_factory=lambda: list(_FORBIDDEN_ACTIONS))

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        return data


def build_recruiter_skill_input_packets(
    context_packet: dict[str, Any] | RecruiterContextPacket,
) -> RecruiterSkillInputPacket:
    if isinstance(context_packet, RecruiterContextPacket):
        packet = context_packet.to_dict()
    elif isinstance(context_packet, dict):
        packet = dict(context_packet)
    else:
        return _blocked_packet(
            RecruiterSkillInputStatus.BLOCKED_INVALID_PACKET,
            errors=["invalid_context_packet_type"],
        )

    request = _dict(packet.get("request"))
    warnings = _dedupe(_string_list(packet.get("warnings")))
    errors = _dedupe(_string_list(packet.get("errors")))
    provenance = {**_dict(packet.get("provenance")), "writes_performed": False, "builder": "recruiter_skill_inputs"}

    context_status = str(packet.get("status") or "")
    if context_status != RecruiterContextStatus.READY.value:
        return _blocked_packet(
            RecruiterSkillInputStatus.BLOCKED_CONTEXT_NOT_READY,
            request=request,
            warnings=warnings,
            errors=errors or [f"context_not_ready:{context_status or 'UNKNOWN'}"],
            provenance=provenance,
        )

    vacancy = packet.get("vacancy")
    if not isinstance(vacancy, dict):
        return _blocked_packet(
            RecruiterSkillInputStatus.BLOCKED_MISSING_VACANCY,
            request=request,
            warnings=warnings,
            errors=errors or ["vacancy_missing"],
            provenance=provenance,
        )

    role_package_context = _dict(packet.get("role_package_context"))
    if not _valid_role_package_context(role_package_context):
        return _blocked_packet(
            RecruiterSkillInputStatus.BLOCKED_ROLE_PACKAGE_CONTEXT,
            request=request,
            warnings=warnings,
            errors=_dedupe([*errors, "role_package_context_invalid_for_recruiter"]),
            provenance=provenance,
        )

    private_context = _dict(packet.get("private_context"))
    private_status = str(private_context.get("status") or "")
    missing_files = _missing_private_files(private_context)

    vacancy_input = _build_vacancy_evaluation_input(packet, vacancy, role_package_context)
    positioning_input = _build_positioning_input(packet, vacancy, private_context, private_status, missing_files)

    if private_status == RecruiterContextStatus.PRIVATE_CONTEXT_MISSING.value and "private_context_missing" not in warnings:
        warnings.append("private_context_missing")
    if private_status == "PARTIAL" and "private_context_partial" not in warnings:
        warnings.append("private_context_partial")

    top_status = RecruiterSkillInputStatus.READY
    if private_status in {RecruiterContextStatus.PRIVATE_CONTEXT_MISSING.value, "PARTIAL"}:
        top_status = RecruiterSkillInputStatus.PARTIAL
    elif positioning_input.get("status") != RecruiterSkillInputStatus.READY.value:
        top_status = RecruiterSkillInputStatus.PARTIAL

    return RecruiterSkillInputPacket(
        status=top_status,
        request=request,
        vacancy_evaluation_input=vacancy_input,
        positioning_evidence_input=positioning_input,
        downstream_gates={
            "document_writer": {
                "skill_id": _DOCUMENT_WRITER_SKILL_ID,
                "status": "READY" if positioning_input.get("status") == RecruiterSkillInputStatus.READY.value else "POSITIONING_REQUIRED",
                "requires": ["positioning-and-evidence"],
                "references": [_DOCUMENT_WRITER_SKILL_PATH],
            }
        },
        warnings=_dedupe(warnings),
        errors=_dedupe(errors),
        provenance=provenance,
    )


def _build_vacancy_evaluation_input(
    packet: dict[str, Any],
    vacancy: dict[str, Any],
    role_package_context: dict[str, Any],
) -> dict[str, Any]:
    return {
        "skill_id": _VACANCY_SKILL_ID,
        "status": RecruiterSkillInputStatus.READY.value,
        "vacancy": _vacancy_facts(vacancy),
        "machine_score": _dict(packet.get("machine_score")),
        "opportunity": _dict(packet.get("opportunity")) or None,
        "company_context": list(packet.get("company_context") or []),
        "application_history": _dict(packet.get("application_history")),
        "source_of_truth_references": [
            _CAREER_SOT_PATH,
            _VACANCY_SKILL_PATH,
        ],
        "boundaries": {
            "do_not_rescore": True,
            "use_machine_score_as_evidence": True,
            "no_outbound": True,
            "no_crm_write": True,
            "no_private_file_content_read": True,
            "no_db_reads": True,
            "no_gateway_router_config": True,
        },
        "expected_output": [
            "vacancy_evaluation_summary",
            "fit_interpretation",
            "evidence_gaps",
            "recommendation_for_next_step",
        ],
        "provenance": {
            "context_status": packet.get("status"),
            "role_package_id": role_package_context.get("package_id"),
            "role_id": role_package_context.get("role_id"),
            "vacancy_source": _dict(vacancy.get("provenance")),
        },
    }


def _build_positioning_input(
    packet: dict[str, Any],
    vacancy: dict[str, Any],
    private_context: dict[str, Any],
    private_status: str,
    missing_files: list[str],
) -> dict[str, Any]:
    if private_status == RecruiterContextStatus.PRIVATE_CONTEXT_MISSING.value:
        status = RecruiterSkillInputStatus.BLOCKED_PRIVATE_CONTEXT_MISSING.value
    elif not vacancy:
        status = RecruiterSkillInputStatus.BLOCKED_MISSING_VACANCY.value
    else:
        status = RecruiterSkillInputStatus.READY.value

    return {
        "skill_id": _POSITIONING_SKILL_ID,
        "status": status,
        "vacancy": _vacancy_facts(vacancy),
        "machine_score": _dict(packet.get("machine_score")),
        "opportunity": _dict(packet.get("opportunity")) or None,
        "private_context": {
            "status": private_status,
            "private_career_dir": str(private_context.get("dir") or ""),
            "files": _dict(private_context.get("files")),
            "missing_files": missing_files,
        },
        "private_source_references": list(_PRIVATE_SOURCE_PATHS),
        "role_package_references": [_POSITIONING_SKILL_PATH],
        "boundaries": {
            "no_invented_facts": True,
            "proven_facts_only": True,
            "derived_positioning_must_be_labeled": True,
            "gaps_must_be_explicit": True,
            "no_outbound": True,
            "no_crm_write": True,
            "no_private_file_content_read": True,
            "no_db_reads": True,
            "no_gateway_router_config": True,
        },
        "expected_output": [
            "positioning_summary",
            "evidence_map",
            "proven_facts",
            "derived_positioning",
            "gaps",
        ],
    }


def _vacancy_facts(vacancy: dict[str, Any]) -> dict[str, Any]:
    return {
        "vacancy_id": vacancy.get("vacancy_id"),
        "vacancy_key": vacancy.get("vacancy_key"),
        "url": vacancy.get("source_url") or vacancy.get("url"),
        "title": vacancy.get("title"),
        "company": vacancy.get("company"),
        "location": vacancy.get("location"),
        "source": vacancy.get("source_kind"),
        "provenance": _dict(vacancy.get("provenance")),
    }


def _valid_role_package_context(role_package_context: dict[str, Any]) -> bool:
    return (
        role_package_context.get("package_id") == _REQUIRED_PACKAGE_ID
        and role_package_context.get("role_id") == _REQUIRED_ROLE_ID
    )


def _missing_private_files(private_context: dict[str, Any]) -> list[str]:
    files = private_context.get("files")
    if not isinstance(files, dict):
        return []
    missing = [name for name, meta in files.items() if not (isinstance(meta, dict) and meta.get("present") is True)]
    return sorted(missing)


def _blocked_packet(
    status: RecruiterSkillInputStatus,
    *,
    request: dict[str, Any] | None = None,
    warnings: list[str] | None = None,
    errors: list[str] | None = None,
    provenance: dict[str, Any] | None = None,
) -> RecruiterSkillInputPacket:
    return RecruiterSkillInputPacket(
        status=status,
        request=request or {},
        vacancy_evaluation_input=None,
        positioning_evidence_input=None,
        downstream_gates={
            "document_writer": {
                "skill_id": _DOCUMENT_WRITER_SKILL_ID,
                "status": "POSITIONING_REQUIRED",
                "requires": ["positioning-and-evidence"],
                "references": [_DOCUMENT_WRITER_SKILL_PATH],
            }
        },
        warnings=_dedupe(warnings or []),
        errors=_dedupe(errors or []),
        provenance={**(provenance or {}), "writes_performed": False, "builder": "recruiter_skill_inputs"},
    )


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result
