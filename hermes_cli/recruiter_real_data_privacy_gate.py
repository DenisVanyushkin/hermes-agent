from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from hashlib import sha256
from typing import Any


_GENERATED_SOURCE_KINDS = {"generated_draft", "previous_generated_material"}
_PRIVATE_FILE_SOURCE_KIND = "private_file"
_ALLOWED_OUTPUT_MODE = "draft_only"


class RealDataPrivacyGateStatus(str, Enum):
    BLOCKED = "REAL_DATA_PRIVACY_GATE_BLOCKED"
    READY = "REAL_DATA_PRIVACY_GATE_READY"


@dataclass(slots=True)
class CareerSourceApproval:
    source_id: str
    source_kind: str
    source_type: str
    approved: bool

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "CareerSourceApproval":
        return cls(
            source_id=str(payload.get("source_id") or "career-source"),
            source_kind=str(payload.get("source_kind") or ""),
            source_type=str(payload.get("source_type") or ""),
            approved=bool(payload.get("approved")),
        )


@dataclass(slots=True)
class RealDataPrivacyGateRequest:
    vacancy_source_type: str | None
    vacancy_source_approved: bool
    career_sources: list[CareerSourceApproval]
    permitted_source_types: list[str]
    output_mode: str
    outbound_enabled: bool
    crm_writes_enabled: bool
    job_intel_writes_enabled: bool
    browser_automation_enabled: bool
    private_file_access_requested: bool
    private_file_access_approved: bool
    draft_only: bool = True

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["career_sources"] = [asdict(item) for item in self.career_sources]
        return data


@dataclass(slots=True)
class RealDataPrivacyGateReport:
    status: RealDataPrivacyGateStatus
    ready: bool
    blocked_reason: str | None
    required_approvals: list[str]
    safe_to_retry_after_user_approval: bool
    approved_source_count: int
    approved_source_types: list[str]
    capability_flags: dict[str, bool]
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    provenance: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        return data


def evaluate_real_data_application_materials_privacy_gate(
    request: RealDataPrivacyGateRequest,
) -> RealDataPrivacyGateReport:
    capability_flags = {
        "draft_only": request.output_mode == _ALLOWED_OUTPUT_MODE and request.draft_only,
        "outbound_disabled": not request.outbound_enabled,
        "crm_writes_disabled": not request.crm_writes_enabled,
        "job_intel_writes_disabled": not request.job_intel_writes_enabled,
        "browser_automation_disabled": not request.browser_automation_enabled,
        "private_file_access_disabled": not request.private_file_access_requested,
    }
    approved_source_types: set[str] = set()
    approved_source_count = 0
    if request.vacancy_source_approved and request.vacancy_source_type:
        approved_source_types.add(request.vacancy_source_type)
        approved_source_count += 1

    approved_career_sources = [item for item in request.career_sources if item.approved]
    for source in approved_career_sources:
        approved_source_types.add(source.source_type)
        approved_source_count += 1

    if not request.vacancy_source_approved or not request.vacancy_source_type:
        return _blocked_report(
            blocked_reason="vacancy_source_not_approved",
            required_approvals=["vacancy_input_source_approval"],
            approved_source_count=approved_source_count,
            approved_source_types=approved_source_types,
            capability_flags=capability_flags,
        )
    if not approved_career_sources:
        return _blocked_report(
            blocked_reason="career_sources_not_approved",
            required_approvals=["career_fact_source_approval"],
            approved_source_count=approved_source_count,
            approved_source_types=approved_source_types,
            capability_flags=capability_flags,
        )
    if any(source.source_kind in _GENERATED_SOURCE_KINDS for source in approved_career_sources):
        return _blocked_report(
            blocked_reason="generated_materials_not_allowed_as_career_facts",
            required_approvals=["career_fact_source_approval"],
            approved_source_count=approved_source_count,
            approved_source_types=approved_source_types,
            capability_flags=capability_flags,
        )
    if any(
        source.source_kind == _PRIVATE_FILE_SOURCE_KIND for source in approved_career_sources
    ) and not (request.private_file_access_requested and request.private_file_access_approved):
        return _blocked_report(
            blocked_reason="private_file_access_not_approved",
            required_approvals=["private_file_access_approval"],
            approved_source_count=approved_source_count,
            approved_source_types=approved_source_types,
            capability_flags=capability_flags,
        )

    missing_permitted_types = {
        source_type
        for source_type in approved_source_types
        if source_type not in set(request.permitted_source_types)
    }
    if missing_permitted_types:
        return _blocked_report(
            blocked_reason="source_type_not_permitted",
            required_approvals=["permitted_source_types_approval"],
            approved_source_count=approved_source_count,
            approved_source_types=approved_source_types,
            capability_flags=capability_flags,
            errors=[f"unapproved_source_type:{source_type}" for source_type in sorted(missing_permitted_types)],
        )
    if request.output_mode != _ALLOWED_OUTPUT_MODE or not request.draft_only:
        return _blocked_report(
            blocked_reason="draft_only_output_required",
            required_approvals=["draft_only_output_mode"],
            approved_source_count=approved_source_count,
            approved_source_types=approved_source_types,
            capability_flags=capability_flags,
        )
    if request.outbound_enabled:
        return _blocked_report(
            blocked_reason="outbound_actions_must_be_disabled",
            required_approvals=["outbound_disabled"],
            approved_source_count=approved_source_count,
            approved_source_types=approved_source_types,
            capability_flags=capability_flags,
        )
    if request.crm_writes_enabled:
        return _blocked_report(
            blocked_reason="crm_writes_must_be_disabled",
            required_approvals=["crm_writes_disabled"],
            approved_source_count=approved_source_count,
            approved_source_types=approved_source_types,
            capability_flags=capability_flags,
        )
    if request.job_intel_writes_enabled:
        return _blocked_report(
            blocked_reason="job_intel_writes_must_be_disabled",
            required_approvals=["job_intel_writes_disabled"],
            approved_source_count=approved_source_count,
            approved_source_types=approved_source_types,
            capability_flags=capability_flags,
        )
    if request.browser_automation_enabled:
        return _blocked_report(
            blocked_reason="browser_automation_must_be_disabled",
            required_approvals=["browser_automation_disabled"],
            approved_source_count=approved_source_count,
            approved_source_types=approved_source_types,
            capability_flags=capability_flags,
        )
    warnings = ["metadata_only_preflight"]
    if request.private_file_access_requested and request.private_file_access_approved:
        capability_flags["private_file_access_disabled"] = False
        warnings.append("private_file_access_metadata_only_no_content_read")
    return RealDataPrivacyGateReport(
        status=RealDataPrivacyGateStatus.READY,
        ready=True,
        blocked_reason=None,
        required_approvals=[],
        safe_to_retry_after_user_approval=False,
        approved_source_count=approved_source_count,
        approved_source_types=sorted(approved_source_types),
        capability_flags=capability_flags,
        warnings=warnings,
        errors=[],
        provenance={
            "writes_performed": False,
            "flow": "application-materials-real-data-preflight",
            "career_source_refs": [_safe_source_ref(source.source_id) for source in approved_career_sources],
        },
    )


def build_invalid_career_source_spec_report(reason: str) -> RealDataPrivacyGateReport:
    return _blocked_report(
        blocked_reason=reason,
        required_approvals=["career_fact_source_approval"],
        approved_source_count=0,
        approved_source_types=set(),
        capability_flags={
            "draft_only": False,
            "outbound_disabled": True,
            "crm_writes_disabled": True,
            "job_intel_writes_disabled": True,
            "browser_automation_disabled": True,
            "private_file_access_disabled": True,
        },
        safe_to_retry_after_user_approval=False,
    )


def _blocked_report(
    *,
    blocked_reason: str,
    required_approvals: list[str],
    approved_source_count: int,
    approved_source_types: set[str],
    capability_flags: dict[str, bool],
    errors: list[str] | None = None,
    safe_to_retry_after_user_approval: bool = True,
) -> RealDataPrivacyGateReport:
    return RealDataPrivacyGateReport(
        status=RealDataPrivacyGateStatus.BLOCKED,
        ready=False,
        blocked_reason=blocked_reason,
        required_approvals=list(required_approvals),
        safe_to_retry_after_user_approval=safe_to_retry_after_user_approval,
        approved_source_count=approved_source_count,
        approved_source_types=sorted(approved_source_types),
        capability_flags=dict(capability_flags),
        warnings=["metadata_only_preflight"],
        errors=list(errors or []),
        provenance={
            "writes_performed": False,
            "flow": "application-materials-real-data-preflight",
        },
    )


def _safe_source_ref(source_id: str) -> str:
    digest = sha256(source_id.encode("utf-8")).hexdigest()[:12]
    return f"career-source-{digest}"
