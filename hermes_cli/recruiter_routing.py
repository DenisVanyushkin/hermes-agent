from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
import re
from typing import Any

from .recruiter_decision_modules import DECISION_BUNDLE_ID, parse_requested_outputs
from .role_packages import build_repo_role_package_skill_context


RECRUITER_ROLE_ID = "hermes_recruiter"
EVALUATE_VACANCY_BUNDLE_ID = "evaluate-vacancy"
DECISION_SUPPORT_BUNDLE_ID = DECISION_BUNDLE_ID
DEFAULT_BUNDLE_ID = DECISION_SUPPORT_BUNDLE_ID
APPLICATION_MATERIALS_BUNDLE_ID = "application-materials"
_RECRUITER_PACKAGE_DIR = Path("role-packages") / "recruiter"
_MANUAL_HANDOFF_REASON = "controlled_recruiter_core_exists_but_provider_execution_stays_disabled"
_DEFAULT_NEXT_ALLOWED_ACTIONS = [
    "recruiter-context dry-run",
    "recruiter-skill execute",
    "recruiter-decision run",
]
_APPLICATION_MATERIALS_NEXT_ALLOWED_ACTIONS = [
    "recruiter-context dry-run",
    "recruiter-skill execute",
    "recruiter-document execute",
]
_APPLICATION_MATERIALS_POSITIONING_WARNING = (
    "application-materials requires POSITIONING_REQUIRED clearance via RecruiterPositioningPacket before document writing"
)
_FORBIDDEN_ACTIONS = [
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
    "execute_recruiter_skill_with_provider",
    "execute_recruiter_document_with_provider",
]

_APPLICATION_PATTERNS = (
    r"\bcv\b",
    r"\bresume\b",
    r"\bcover\s+letter\b",
    r"\bapplication\s+materials?\b",
    r"\bfollow[\s-]?up\b",
    r"\blinkedin\b",
    r"\brecruiter\s+message\b",
    r"\brecruiter\s+dm\b",
    r"\bmessage\s+recruiter\b",
    r"\bnapishi\s+rekruteru\b",
    r"сопровод",
    r"резюме",
    r"материал",
    r"отклик",
    r"напиши рекрутер",
    r"подготовь\s+cv",
    r"подготовь\s+материал",
)
_EVALUATION_PATTERNS = (
    r"\bevaluate\b",
    r"\bvacanc(y|ies)\b",
    r"\bjob\b",
    r"\bfit\b",
    r"\bshould\s+i\s+apply\b",
    r"\bworth\s+applying\b",
    r"\blook\s+at\s+this\s+job\b",
    r"\bjob\s+posting\b",
    r"ваканси",
    r"податься",
    r"стоит\s+ли\s+податься",
    r"посмотри\s+ваканси",
    r"оцени\s+ваканси",
    r"оцен[иь]\s+джоб",
)
_RECRUITER_GENERAL_PATTERNS = (
    r"\brecruiter\b",
    r"\bcareer\b",
    r"\bjob\s+search\b",
    r"\bapplication\b",
    r"\bvacanc(y|ies)\b",
    r"\bjob\b",
    r"\bcover\s+letter\b",
    r"\bcv\b",
    r"\bresume\b",
    r"\blinkedin\b",
    r"ваканси",
    r"карьер",
    r"резюме",
    r"сопровод",
    r"рекрутер",
    r"отклик",
    r"податься",
)
_ENGINEERING_PATTERNS = (
    r"hermes_cli/",
    r"tests/",
    r"\bpython\b",
    r"\bpytest\b",
    r"\bruff\b",
    r"\bbandit\b",
    r"\bdebug\b",
    r"\bfix\b",
    r"\bbug\b",
    r"\bcode\b",
    r"\brouter\b",
    r"\bpipeline\b",
    r"\bgateway\b",
    r"\binfrastructure\b",
    r"\bnetwork(ing)?\b",
    r"\bportfolio\b",
    r"\btrading\b",
    r"\bcalendar\b",
    r"\breminder\b",
    r"\bhome\s+lab\b",
    r"код",
    r"тест",
    r"баг",
    r"пайплайн",
    r"роутер",
    r"гейтвей",
    r"инфраструкт",
    r"календар",
    r"напоминан",
    r"портфел",
    r"трейдинг",
    r"хоумлаб",
)
_JOB_URL_PATTERN = re.compile(r"https?://\S+", re.IGNORECASE)


class RecruiterRoutingStatus(str, Enum):
    SELECTED = "selected"
    NOT_SELECTED = "not_selected"


@dataclass(slots=True)
class RecruiterRoutingDecision:
    status: RecruiterRoutingStatus
    selected_role_id: str | None
    selected_bundle: str | None
    reasoning_summary: str
    matched_signals: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    next_allowed_actions: list[str] = field(default_factory=list)
    forbidden_actions: list[str] = field(default_factory=lambda: list(_FORBIDDEN_ACTIONS))
    execution_mode: str = "observe"
    provider_execution_enabled: bool = False
    document_provider_execution_enabled: bool = False
    role_package_context: dict[str, Any] = field(default_factory=dict)
    requested_outputs: list[str] = field(default_factory=list)
    requested_outputs_preset: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        return data


def route_recruiter_prompt(prompt: str, *, context: dict[str, Any] | None = None) -> RecruiterRoutingDecision:
    text = " ".join(prompt.split())
    lowered = text.casefold()
    engineering_signals = _match_patterns(lowered, _ENGINEERING_PATTERNS)
    recruiter_signals = _match_patterns(lowered, _RECRUITER_GENERAL_PATTERNS)

    if engineering_signals:
        return RecruiterRoutingDecision(
            status=RecruiterRoutingStatus.NOT_SELECTED,
            selected_role_id=None,
            selected_bundle=None,
            reasoning_summary="Engineering or infrastructure signals take priority over recruiter routing.",
            matched_signals=engineering_signals,
        )

    if not recruiter_signals:
        return RecruiterRoutingDecision(
            status=RecruiterRoutingStatus.NOT_SELECTED,
            selected_role_id=None,
            selected_bundle=None,
            reasoning_summary="Prompt does not look recruiter-specific.",
            matched_signals=[],
        )

    application_signals = _match_patterns(lowered, _APPLICATION_PATTERNS)
    evaluation_signals = _match_patterns(lowered, _EVALUATION_PATTERNS)
    if _JOB_URL_PATTERN.search(text):
        evaluation_signals.append("job_url")
        recruiter_signals.append("job_url")

    warnings: list[str] = []
    requested_outputs: list[str] = []
    requested_outputs_preset: str | None = None
    if application_signals:
        bundle_id = APPLICATION_MATERIALS_BUNDLE_ID
        reasoning = "Prompt asks for recruiter-facing application materials."
        warnings.append(_APPLICATION_MATERIALS_POSITIONING_WARNING)
        next_actions = list(_APPLICATION_MATERIALS_NEXT_ALLOWED_ACTIONS)
    else:
        bundle_id = DECISION_SUPPORT_BUNDLE_ID
        next_actions = list(_DEFAULT_NEXT_ALLOWED_ACTIONS)
        parsed = parse_requested_outputs(text, context=context)
        requested_outputs = list(parsed.requested)
        requested_outputs_preset = parsed.preset_id
        warnings.extend(parsed.warnings)
        if evaluation_signals:
            reasoning = "Prompt asks for vacancy/company decision support."
        else:
            reasoning = (
                "Prompt is recruiter-related but ambiguous, so the safe default is the "
                "decision-support bundle with a targeted module subset."
            )
            warnings.append(
                "ambiguous recruiter request; defaulted to decision-support modules "
                f"{requested_outputs_preset or 'quick_vacancy_screen'}"
            )

    role_package_context = _build_role_package_context(context)
    return RecruiterRoutingDecision(
        status=RecruiterRoutingStatus.SELECTED,
        selected_role_id=RECRUITER_ROLE_ID,
        selected_bundle=bundle_id,
        reasoning_summary=reasoning,
        matched_signals=_dedupe([*recruiter_signals, *application_signals, *evaluation_signals]),
        warnings=warnings,
        next_allowed_actions=next_actions,
        role_package_context=role_package_context,
        requested_outputs=requested_outputs,
        requested_outputs_preset=requested_outputs_preset,
    )


def build_recruiter_handoff_metadata(
    prompt: str,
    *,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    decision = route_recruiter_prompt(prompt, context=context)
    payload = decision.to_dict()
    if decision.status is RecruiterRoutingStatus.SELECTED:
        payload["manual_handoff_reason"] = _MANUAL_HANDOFF_REASON
        payload["role_context"] = _build_role_context(decision)
    else:
        payload["manual_handoff_reason"] = None
        payload["role_context"] = None
    return payload


def _build_role_context(decision: RecruiterRoutingDecision) -> dict[str, Any]:
    role_package_context = dict(decision.role_package_context)
    bundle_id = decision.selected_bundle
    bundle_ids = set(role_package_context.get("bundle_ids") or [])
    bundle_payload = dict((role_package_context.get("bundles") or {}).get(bundle_id) or {})
    bundle_skill_ids = list(bundle_payload.get("skills") or [])

    bundle_required_inputs: list[str] = []
    bundle_expected_outputs: list[str] = []
    expected_output = bundle_payload.get("expected_output")
    if bundle_id == APPLICATION_MATERIALS_BUNDLE_ID:
        bundle_required_inputs = ["RecruiterPositioningPacket", "POSITIONING_REQUIRED"]
    elif expected_output:
        bundle_required_inputs = [str(expected_output)]
    if expected_output:
        bundle_expected_outputs = [str(expected_output)]

    return {
        "schema_version": "recruiter_role_context_v1",
        "selected_role_id": decision.selected_role_id,
        "selected_bundle": bundle_id,
        "execution_mode": decision.execution_mode,
        "provider_execution_enabled": decision.provider_execution_enabled,
        "document_provider_execution_enabled": decision.document_provider_execution_enabled,
        "next_allowed_actions": list(decision.next_allowed_actions),
        "forbidden_actions": list(decision.forbidden_actions),
        "warnings": list(decision.warnings),
        "matched_signals": list(decision.matched_signals),
        "reasoning_summary": decision.reasoning_summary,
        "manual_handoff_reason": _MANUAL_HANDOFF_REASON,
        "role_package_available": bool(role_package_context),
        "selected_bundle_available": bundle_id in bundle_ids if bundle_id is not None else False,
        "bundle_skill_ids": bundle_skill_ids,
        "bundle_required_inputs": bundle_required_inputs,
        "bundle_expected_outputs": bundle_expected_outputs,
        "requested_outputs": list(decision.requested_outputs),
        "requested_outputs_preset": decision.requested_outputs_preset,
    }


def _build_role_package_context(context: dict[str, Any] | None) -> dict[str, Any]:
    repo_root = _context_repo_root(context)
    payload = build_repo_role_package_skill_context(repo_root / _RECRUITER_PACKAGE_DIR, repo_root=repo_root)
    bundles = {
        bundle["id"]: {
            "skills": list(bundle.get("skills") or []),
            "expected_output": bundle.get("expected_output"),
        }
        for bundle in payload["bundles"]
    }
    return {
        "package_id": payload["package_id"],
        "role_id": payload["role_id"],
        "bundle_ids": [bundle["id"] for bundle in payload["bundles"]],
        "skills": [skill["id"] for skill in payload["skills"]],
        "bundles": bundles,
    }


def _context_repo_root(context: dict[str, Any] | None) -> Path:
    repo_root = (context or {}).get("repo_root")
    if repo_root is None:
        return Path.cwd()
    return Path(repo_root)


def _match_patterns(text: str, patterns: tuple[str, ...]) -> list[str]:
    matches: list[str] = []
    for pattern in patterns:
        if re.search(pattern, text, re.IGNORECASE):
            matches.append(pattern)
    return matches


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result
