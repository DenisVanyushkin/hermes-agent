from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
import re
from typing import Any

from .recruiter_routing import (
    APPLICATION_MATERIALS_BUNDLE_ID,
    DECISION_SUPPORT_BUNDLE_ID,
    RECRUITER_ROLE_ID,
    build_recruiter_handoff_metadata,
)


_EVALUATE_VACANCY_FLOW_ID = "evaluate-vacancy"
_SUPPORTED_EVALUATION_BUNDLES = {_EVALUATE_VACANCY_FLOW_ID, DECISION_SUPPORT_BUNDLE_ID}
_URL_PATTERN = re.compile(r"https?://\S+", re.IGNORECASE)
_TEXT_SOURCE_PATTERNS = (
    re.compile(r"\b(company|responsibilities|requirements|title)\s*:", re.IGNORECASE),
    re.compile(r"\b(head|director|vp|chief)\s+of\s+product\b", re.IGNORECASE),
    re.compile(r"\babout the role\b", re.IGNORECASE),
    re.compile(r"\bwhat you'll do\b", re.IGNORECASE),
    re.compile(r"\bwhat you will do\b", re.IGNORECASE),
    re.compile(r"\bqualifications\b", re.IGNORECASE),
    re.compile(r"\brequirements\b", re.IGNORECASE),
    re.compile(r"\bresponsibilities\b", re.IGNORECASE),
    re.compile(r"\bexperience\b", re.IGNORECASE),
    re.compile(r"\bcandidate profile\b", re.IGNORECASE),
    re.compile(r"\brole overview\b", re.IGNORECASE),
)
_READY_PRIVATE_CONTEXT_STATUSES = {"PRIVATE_CONTEXT_AVAILABLE", "PARTIAL", "PRIVATE_CONTEXT_NOT_INSPECTED"}
_FORBIDDEN_ACTIONS = [
    "call_provider_model",
    "fetch_vacancy_url",
    "open_browser",
    "read_private_file_contents",
    "write_job_intel_db",
    "write_crm",
    "send_outbound_message",
    "restart_gateway",
    "mutate_live_config",
    "execute_recruiter_document_with_provider",
]
_DEFAULT_REQUIRED_INPUTS = ["vacancy_url_or_text"]
_DEFAULT_AVAILABLE_INPUTS = ["prompt_text", "role_context"]
_READY_NEXT_ALLOWED_ACTIONS = [
    "build_vacancy_evaluation_input",
    "build_positioning_evidence_input",
    "produce_recruiter_positioning_packet_v1",
]


class RecruiterEvaluationFlowStatus(str, Enum):
    READY = "READY"
    BLOCKED_NOT_RECRUITER = "BLOCKED_NOT_RECRUITER"
    BLOCKED_UNSUPPORTED_BUNDLE = "BLOCKED_UNSUPPORTED_BUNDLE"
    BLOCKED_SOURCE_REQUIRED = "BLOCKED_SOURCE_REQUIRED"
    BLOCKED_PRIVATE_CONTEXT_MISSING = "BLOCKED_PRIVATE_CONTEXT_MISSING"


@dataclass(slots=True)
class RecruiterEvaluationFlowRequest:
    prompt: str
    repo_root: str | Path | None = None
    private_context_status: str = "PRIVATE_CONTEXT_NOT_INSPECTED"


@dataclass(slots=True)
class RecruiterEvaluationFlowReport:
    status: RecruiterEvaluationFlowStatus
    schema_version: str = "recruiter_evaluation_flow_v1"
    role_context_schema_version: str | None = None
    selected_role_id: str | None = None
    selected_bundle: str | None = None
    flow_id: str = _EVALUATE_VACANCY_FLOW_ID
    status_reason: str = ""
    vacancy_source_status: str = "MISSING"
    private_context_status: str = "PRIVATE_CONTEXT_NOT_INSPECTED"
    provider_execution_enabled: bool = False
    document_provider_execution_enabled: bool = False
    outbound_enabled: bool = False
    db_write_enabled: bool = False
    next_allowed_actions: list[str] = field(default_factory=list)
    forbidden_actions: list[str] = field(default_factory=lambda: list(_FORBIDDEN_ACTIONS))
    required_inputs: list[str] = field(default_factory=lambda: list(_DEFAULT_REQUIRED_INPUTS))
    available_inputs: list[str] = field(default_factory=lambda: list(_DEFAULT_AVAILABLE_INPUTS))
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        return data


def build_recruiter_evaluation_flow(request: RecruiterEvaluationFlowRequest) -> RecruiterEvaluationFlowReport:
    metadata = build_recruiter_handoff_metadata(
        request.prompt,
        context={"repo_root": _resolve_repo_root(request.repo_root)},
    )
    role_context = metadata.get("role_context") if isinstance(metadata, dict) else None

    if metadata.get("status") != "selected" or not isinstance(role_context, dict):
        return RecruiterEvaluationFlowReport(
            status=RecruiterEvaluationFlowStatus.BLOCKED_NOT_RECRUITER,
            status_reason="recruiter routing not selected",
            private_context_status=request.private_context_status,
        )

    selected_role_id = role_context.get("selected_role_id")
    selected_bundle = role_context.get("selected_bundle")
    vacancy_source_status = _detect_vacancy_source_status(request.prompt)
    warnings = list(role_context.get("warnings") or [])
    report = RecruiterEvaluationFlowReport(
        status=RecruiterEvaluationFlowStatus.READY,
        role_context_schema_version=role_context.get("schema_version"),
        selected_role_id=selected_role_id,
        selected_bundle=selected_bundle,
        vacancy_source_status=vacancy_source_status,
        private_context_status=request.private_context_status,
        provider_execution_enabled=False,
        document_provider_execution_enabled=False,
        outbound_enabled=False,
        db_write_enabled=False,
        next_allowed_actions=list(_READY_NEXT_ALLOWED_ACTIONS),
        forbidden_actions=list(_FORBIDDEN_ACTIONS),
        warnings=warnings,
    )
    if selected_role_id != RECRUITER_ROLE_ID:
        report.status = RecruiterEvaluationFlowStatus.BLOCKED_NOT_RECRUITER
        report.status_reason = "selected role is not recruiter"
        report.next_allowed_actions = []
        return report
    if selected_bundle not in _SUPPORTED_EVALUATION_BUNDLES:
        report.status = RecruiterEvaluationFlowStatus.BLOCKED_UNSUPPORTED_BUNDLE
        report.status_reason = "selected recruiter bundle is not supported by evaluate-vacancy flow"
        report.next_allowed_actions = []
        if selected_bundle == APPLICATION_MATERIALS_BUNDLE_ID:
            report.warnings = _dedupe(
                [
                    *report.warnings,
                    "application-materials remains blocked until POSITIONING_REQUIRED is cleared by recruiter_positioning_packet_v1",
                ]
            )
        return report
    if vacancy_source_status == "MISSING":
        report.status = RecruiterEvaluationFlowStatus.BLOCKED_SOURCE_REQUIRED
        report.status_reason = "vacancy source missing from recruiter prompt"
        report.next_allowed_actions = []
        return report
    if request.private_context_status not in _READY_PRIVATE_CONTEXT_STATUSES:
        report.status = RecruiterEvaluationFlowStatus.BLOCKED_PRIVATE_CONTEXT_MISSING
        report.status_reason = "private career context readiness is not available"
        report.next_allowed_actions = []
        return report

    report.status_reason = "evaluate-vacancy flow is ready in observe/dry-run mode"
    return report


def _detect_vacancy_source_status(prompt: str) -> str:
    if _URL_PATTERN.search(prompt):
        return "AVAILABLE_URL"
    if any(pattern.search(prompt) for pattern in _TEXT_SOURCE_PATTERNS):
        return "AVAILABLE_TEXT"
    return "MISSING"


def _resolve_repo_root(repo_root: str | Path | None) -> Path:
    if repo_root is None:
        # The gateway's cwd is ~/.hermes, not the source repo — resolve from this file.
        current = Path(__file__).resolve()
        for candidate in current.parents:
            if (candidate / ".git").exists() or (candidate / "role-packages").is_dir():
                return candidate
        return current.parents[1]
    return Path(repo_root)


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result
