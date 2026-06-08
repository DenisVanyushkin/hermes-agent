"""Preview/write-only Scribe handoff layer for Hermes profile architecture.

This module is intentionally pure and import-light. It records durable handoff
artifacts without invoking runtime execution, subprocesses, or agent stacks.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
import json
import re

import yaml

from hermes_cli.profile_approval import (
    ApprovalPreview,
    classify_engineer_approval,
    decision_to_dict as approval_decision_to_dict,
)
from hermes_cli.profile_routing import (
    RouteDecision,
    RouteHop,
    route_task,
    decision_to_dict as route_decision_to_dict,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DOCS_ROOT = REPO_ROOT / "docs"
ALLOWED_DESTINATIONS = {"handoff", "current-operational-state", "open-questions"}
ALLOWED_TASK_EXECUTION_STATUSES = {
    "completed",
    "no_update_required",
    "blocked_pending_approval",
    "incomplete",
    "unknown",
}
ALLOWED_SCRIBE_FAILURE_REASONS = {
    None,
    "write_failed",
    "path_missing",
    "diff_not_verified",
    "hook_skipped",
    "insufficient_evidence",
    "approval_required",
}


class HandoffError(RuntimeError):
    """Raised when a handoff cannot be built safely."""


@dataclass
class ScribeHandoff:
    task_id: str
    timestamp_utc: str
    from_profile: str
    to_profile: str
    task_summary: str
    route_decision: dict[str, Any] | None = None
    approval_preview: dict[str, Any] | None = None
    evidence: list[str] = field(default_factory=list)
    changed_state: list[str] = field(default_factory=list)
    changed_files: list[str] = field(default_factory=list)
    decisions: list[str] = field(default_factory=list)
    open_followups: list[str] = field(default_factory=list)
    scribe_status: str = "handoff_incomplete"
    scribe_attempt_recorded: bool = False
    scribe_failure_reason: str | None = None
    scribe_changed_paths: list[str] = field(default_factory=list)
    scribe_verified_paths: list[str] = field(default_factory=list)
    no_update_required: bool = False
    no_update_rationale: str | None = None
    model_tier: str = "unknown"
    selected_model: str | None = None
    model_fallback_used: bool = False
    task_execution_status: str = "unknown"


@dataclass
class ScribeHandoffResult:
    handoff: ScribeHandoff
    artifact_path: str
    markdown: str
    write_performed: bool
    write_verified: bool
    write_error: str | None = None


def _now_timestamp_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _filename_timestamp(timestamp_utc: str) -> str:
    cleaned = timestamp_utc.strip().replace("-", "").replace(":", "")
    cleaned = cleaned.replace("T", "T").replace("Z", "Z")
    return cleaned or _now_timestamp_utc().replace("-", "").replace(":", "")


def _slugify(text: str, *, max_length: int = 72) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", text.strip().lower()).strip("-")
    slug = re.sub(r"-+", "-", slug)
    if not slug:
        slug = "task"
    return slug[:max_length].strip("-") or "task"


def _ensure_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, tuple):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, str):
        return [value] if value.strip() else []
    return [str(value)]


def _to_plain_object(value: Any) -> Any:
    if value is None:
        return None
    if is_dataclass(value):
        return {key: _to_plain_object(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {str(key): _to_plain_object(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_to_plain_object(item) for item in value]
    if isinstance(value, tuple):
        return [_to_plain_object(item) for item in value]
    return value


def _route_dict_to_dataclass(data: dict[str, Any]) -> RouteDecision:
    route_chain = []
    for raw_hop in data.get("route_chain", []) or []:
        if isinstance(raw_hop, RouteHop):
            route_chain.append(raw_hop)
            continue
        if not isinstance(raw_hop, dict):
            raise HandoffError("route_decision route_chain entries must be mappings")
        route_chain.append(
            RouteHop(
                profile_id=str(raw_hop.get("profile_id", "")),
                routing_reason=str(raw_hop.get("routing_reason", "")),
                model_tier=str(raw_hop.get("model_tier", "unknown")),
                provider=str(raw_hop.get("provider", "")),
                model=str(raw_hop.get("model", "")),
                escalation_reason=str(raw_hop.get("escalation_reason", "")),
                model_resolution_status=str(raw_hop.get("model_resolution_status", "unknown")),
                fallback_status=str(raw_hop.get("fallback_status", "unknown")),
            )
        )

    return RouteDecision(
        request_text=str(data.get("request_text", "")),
        coordinator_profile=str(data.get("coordinator_profile", "chief_hermes")),
        primary_profile=str(data.get("primary_profile", "unknown")),
        selected_profiles=[str(item) for item in data.get("selected_profiles", []) or []],
        route_chain=route_chain,
        route_reason=str(data.get("route_reason", "")),
        validation_status=str(data.get("validation_status", "unknown")),
        confidence=str(data.get("confidence", "unknown")),
        ambiguity_reasons=[str(item) for item in data.get("ambiguity_reasons", []) or []],
        max_chain_limit_applied=bool(data.get("max_chain_limit_applied", False)),
    )


def _coerce_route_decision(route_decision: RouteDecision | dict[str, Any] | None, task_summary: str) -> RouteDecision:
    if route_decision is None:
        return route_task(task_summary)
    if isinstance(route_decision, RouteDecision):
        return route_decision
    if isinstance(route_decision, dict):
        return _route_dict_to_dataclass(route_decision)
    raise HandoffError("route_decision must be a RouteDecision or mapping")


def _coerce_approval_preview(approval_preview: ApprovalPreview | dict[str, Any] | None) -> dict[str, Any] | None:
    if approval_preview is None:
        return None
    if isinstance(approval_preview, ApprovalPreview):
        return approval_decision_to_dict(approval_preview)
    if isinstance(approval_preview, dict):
        return {str(key): _to_plain_object(value) for key, value in approval_preview.items()}
    raise HandoffError("approval_preview must be an ApprovalPreview or mapping")


def _extract_primary_model(route_decision: RouteDecision) -> tuple[str, str, bool]:
    if not route_decision.route_chain:
        return "unknown", "unknown", False
    primary_hop = route_decision.route_chain[0]
    selected_model = f"{primary_hop.provider}/{primary_hop.model}" if primary_hop.provider and primary_hop.model else "unknown"
    model_tier = primary_hop.model_tier or "unknown"
    fallback_used = str(primary_hop.fallback_status).lower() in {"fallback_used", "used_fallback", "fallback"}
    return model_tier, selected_model, fallback_used


def _derive_task_execution_status(
    *,
    approval_preview: dict[str, Any] | None,
    no_update_required: bool,
    changed_state: list[str],
    changed_files: list[str],
    decisions: list[str],
    evidence: list[str],
    explicit: str | None = None,
) -> str:
    if explicit is not None:
        if explicit not in ALLOWED_TASK_EXECUTION_STATUSES:
            raise HandoffError(f"task_execution_status must be one of: {', '.join(sorted(ALLOWED_TASK_EXECUTION_STATUSES))}")
        return explicit
    if no_update_required:
        return "no_update_required"
    if approval_preview and bool(approval_preview.get("requires_approval")):
        return "blocked_pending_approval"
    if changed_state or changed_files or decisions or evidence:
        return "completed"
    return "unknown"


def build_scribe_handoff(
    task_summary: str,
    *,
    route_decision: RouteDecision | dict[str, Any] | None = None,
    approval_preview: ApprovalPreview | dict[str, Any] | None = None,
    evidence: Optional[list[str]] = None,
    changed_state: Optional[list[str]] = None,
    changed_files: Optional[list[str]] = None,
    decisions: Optional[list[str]] = None,
    open_followups: Optional[list[str]] = None,
    no_update_required: bool = False,
    no_update_rationale: Optional[str] = None,
    task_id: Optional[str] = None,
    timestamp_utc: Optional[str] = None,
    task_execution_status: Optional[str] = None,
) -> ScribeHandoff:
    if not isinstance(task_summary, str) or not task_summary.strip():
        raise HandoffError("task_summary must be a non-empty string")
    if no_update_required and not (isinstance(no_update_rationale, str) and no_update_rationale.strip()):
        raise HandoffError("no_update_required requires no_update_rationale")

    route_obj = _coerce_route_decision(route_decision, task_summary)
    approval_obj = _coerce_approval_preview(approval_preview)
    if approval_obj is None:
        approval_obj = _to_plain_object(
            classify_engineer_approval(task_summary, route_decision=route_obj, evidence_before=_ensure_list(evidence))
        )

    model_tier, selected_model, model_fallback_used = _extract_primary_model(route_obj)
    execution_status = _derive_task_execution_status(
        approval_preview=approval_obj,
        no_update_required=no_update_required,
        changed_state=_ensure_list(changed_state),
        changed_files=_ensure_list(changed_files),
        decisions=_ensure_list(decisions),
        evidence=_ensure_list(evidence),
        explicit=task_execution_status,
    )

    timestamp_value = (timestamp_utc or _now_timestamp_utc()).strip()
    if not timestamp_value:
        timestamp_value = _now_timestamp_utc()
    task_id_value = (task_id or f"{_filename_timestamp(timestamp_value)}-{_slugify(task_summary)}").strip()

    return ScribeHandoff(
        task_id=task_id_value,
        timestamp_utc=timestamp_value,
        from_profile=route_obj.primary_profile or "unknown",
        to_profile="scribe",
        task_summary=task_summary.strip(),
        route_decision=_to_plain_object(route_obj),
        approval_preview=approval_obj,
        evidence=_ensure_list(evidence),
        changed_state=_ensure_list(changed_state),
        changed_files=_ensure_list(changed_files),
        decisions=_ensure_list(decisions),
        open_followups=_ensure_list(open_followups),
        scribe_status="handoff_incomplete",
        scribe_attempt_recorded=False,
        scribe_failure_reason=None,
        scribe_changed_paths=_ensure_list(changed_state) + _ensure_list(changed_files),
        scribe_verified_paths=[],
        no_update_required=bool(no_update_required),
        no_update_rationale=no_update_rationale.strip() if isinstance(no_update_rationale, str) and no_update_rationale.strip() else None,
        model_tier=model_tier,
        selected_model=selected_model,
        model_fallback_used=model_fallback_used,
        task_execution_status=execution_status,
    )


def decision_to_dict(decision: ScribeHandoff) -> dict[str, Any]:
    if not isinstance(decision, ScribeHandoff):
        raise HandoffError("decision_to_dict expects a ScribeHandoff")
    return _to_plain_object(asdict(decision))


def decision_to_json(decision: ScribeHandoff) -> str:
    return json.dumps(decision_to_dict(decision), ensure_ascii=False, indent=2)


def _artifact_path_for(
    handoff: ScribeHandoff,
    *,
    output_root: Path | str | None = None,
    destination: str = "handoff",
) -> Path:
    if destination not in ALLOWED_DESTINATIONS:
        raise HandoffError(f"destination must be one of: {', '.join(sorted(ALLOWED_DESTINATIONS))}")

    base_root = Path(output_root) if output_root is not None else DEFAULT_DOCS_ROOT
    base_root = base_root.expanduser()
    if destination == "handoff":
        timestamp = _filename_timestamp(handoff.timestamp_utc)
        slug = _slugify(handoff.task_summary)
        return base_root / "profile-handoffs" / handoff.timestamp_utc[:10] / f"{timestamp}-{slug}.md"
    if destination == "current-operational-state":
        return base_root / "state" / "current-operational-state.md"
    return base_root / "open-questions.md"


def _metadata_for_front_matter(handoff: ScribeHandoff) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "task_id": handoff.task_id,
        "timestamp_utc": handoff.timestamp_utc,
        "from_profile": handoff.from_profile,
        "to_profile": handoff.to_profile,
        "scribe_status": handoff.scribe_status,
        "task_execution_status": handoff.task_execution_status,
        "no_update_required": handoff.no_update_required,
    }


def render_handoff_markdown(handoff: ScribeHandoff) -> str:
    if not isinstance(handoff, ScribeHandoff):
        raise HandoffError("render_handoff_markdown expects a ScribeHandoff")

    metadata = yaml.safe_dump(_metadata_for_front_matter(handoff), sort_keys=False, default_flow_style=False).strip()
    route_block = json.dumps(handoff.route_decision, ensure_ascii=False, indent=2) if handoff.route_decision is not None else "null"
    approval_block = json.dumps(handoff.approval_preview, ensure_ascii=False, indent=2) if handoff.approval_preview is not None else "null"

    def block_list(items: list[str]) -> str:
        if not items:
            return "- None"
        return "\n".join(f"- {item}" for item in items)

    status_lines = [
        f"- scribe_status: {handoff.scribe_status}",
        f"- scribe_attempt_recorded: {str(handoff.scribe_attempt_recorded).lower()}",
        f"- scribe_failure_reason: {handoff.scribe_failure_reason if handoff.scribe_failure_reason is not None else 'null'}",
        f"- scribe_changed_paths: {json.dumps(handoff.scribe_changed_paths, ensure_ascii=False)}",
        f"- scribe_verified_paths: {json.dumps(handoff.scribe_verified_paths, ensure_ascii=False)}",
        f"- no_update_required: {str(handoff.no_update_required).lower()}",
        f"- no_update_rationale: {handoff.no_update_rationale if handoff.no_update_rationale is not None else 'null'}",
        f"- model_tier: {handoff.model_tier}",
        f"- selected_model: {handoff.selected_model if handoff.selected_model is not None else 'null'}",
        f"- model_fallback_used: {str(handoff.model_fallback_used).lower()}",
        f"- task_execution_status: {handoff.task_execution_status}",
    ]

    parts = [
        "---",
        metadata,
        "---",
        "# Scribe Handoff",
        "",
        "## Summary",
        f"- task_id: {handoff.task_id}",
        f"- timestamp_utc: {handoff.timestamp_utc}",
        f"- from_profile: {handoff.from_profile}",
        f"- to_profile: {handoff.to_profile}",
        f"- task_summary: {handoff.task_summary}",
        "",
        "## Route Decision",
        "```json",
        route_block,
        "```",
        "",
        "## Approval Preview",
        "```json",
        approval_block,
        "```",
        "",
        "## Evidence",
        block_list(handoff.evidence),
        "",
        "## Changed State",
        block_list(handoff.changed_state),
        "",
        "## Changed Files",
        block_list(handoff.changed_files),
        "",
        "## Decisions",
        block_list(handoff.decisions),
        "",
        "## Open Follow-ups",
        block_list(handoff.open_followups),
        "",
        "## Scribe Status",
        *status_lines,
        "",
    ]
    return "\n".join(parts).rstrip() + "\n"


def _safe_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _validate_target_path(target_path: Path, output_root: Path) -> None:
    resolved_root = output_root.resolve(strict=False)
    resolved_target = target_path.resolve(strict=False)
    try:
        resolved_target.relative_to(resolved_root)
    except ValueError as exc:
        raise HandoffError("target path escapes the allowed output root") from exc


def _write_result(
    handoff: ScribeHandoff,
    *,
    output_root: Path | str | None = None,
    destination: str = "handoff",
) -> ScribeHandoffResult:
    artifact_path = _artifact_path_for(handoff, output_root=output_root, destination=destination)
    docs_root = Path(output_root) if output_root is not None else DEFAULT_DOCS_ROOT
    docs_root = docs_root.expanduser()
    markdown = render_handoff_markdown(handoff)

    result = ScribeHandoffResult(
        handoff=handoff,
        artifact_path=str(artifact_path),
        markdown=markdown,
        write_performed=True,
        write_verified=False,
        write_error=None,
    )

    try:
        if artifact_path.exists():
            raise HandoffError("artifact already exists; refusing to overwrite silently")
        _validate_target_path(artifact_path, docs_root)
        _safe_write_text(artifact_path, markdown)
        written_back = artifact_path.read_text(encoding="utf-8")
        if written_back != markdown:
            raise HandoffError("written artifact did not round-trip exactly")

        handoff.scribe_status = "complete"
        handoff.scribe_attempt_recorded = True
        handoff.scribe_failure_reason = None
        handoff.scribe_verified_paths = [str(artifact_path)]
        handoff.scribe_changed_paths = sorted(set(handoff.scribe_changed_paths + [str(artifact_path)]))
        result.write_verified = True
    except FileNotFoundError as exc:
        handoff.scribe_status = "handoff_incomplete"
        handoff.scribe_attempt_recorded = True
        handoff.scribe_failure_reason = "path_missing"
        result.write_error = str(exc)
    except OSError as exc:
        handoff.scribe_status = "handoff_incomplete"
        handoff.scribe_attempt_recorded = True
        handoff.scribe_failure_reason = "write_failed"
        result.write_error = str(exc)
    except HandoffError as exc:
        handoff.scribe_status = "handoff_incomplete"
        handoff.scribe_attempt_recorded = True
        message = str(exc)
        if "round-trip" in message:
            handoff.scribe_failure_reason = "diff_not_verified"
        elif "overwrite" in message:
            handoff.scribe_failure_reason = "write_failed"
        elif "escapes" in message:
            handoff.scribe_failure_reason = "write_failed"
        else:
            handoff.scribe_failure_reason = "write_failed"
        result.write_error = message
    return result


def preview_scribe_handoff(
    task_summary: str,
    *,
    route_decision: RouteDecision | dict[str, Any] | None = None,
    approval_preview: ApprovalPreview | dict[str, Any] | None = None,
    evidence: Optional[list[str]] = None,
    changed_state: Optional[list[str]] = None,
    changed_files: Optional[list[str]] = None,
    decisions: Optional[list[str]] = None,
    open_followups: Optional[list[str]] = None,
    no_update_required: bool = False,
    no_update_rationale: Optional[str] = None,
    task_id: Optional[str] = None,
    timestamp_utc: Optional[str] = None,
    task_execution_status: Optional[str] = None,
    output_root: Path | str | None = None,
    destination: str = "handoff",
    write: bool = False,
) -> ScribeHandoffResult:
    handoff = build_scribe_handoff(
        task_summary,
        route_decision=route_decision,
        approval_preview=approval_preview,
        evidence=evidence,
        changed_state=changed_state,
        changed_files=changed_files,
        decisions=decisions,
        open_followups=open_followups,
        no_update_required=no_update_required,
        no_update_rationale=no_update_rationale,
        task_id=task_id,
        timestamp_utc=timestamp_utc,
        task_execution_status=task_execution_status,
    )

    if not write:
        handoff.scribe_attempt_recorded = False
        handoff.scribe_status = "handoff_incomplete"
        handoff.scribe_failure_reason = "hook_skipped"
        return ScribeHandoffResult(
            handoff=handoff,
            artifact_path=str(_artifact_path_for(handoff, output_root=output_root, destination=destination)),
            markdown=render_handoff_markdown(handoff),
            write_performed=False,
            write_verified=False,
            write_error=None,
        )

    return _write_result(handoff, output_root=output_root, destination=destination)


def write_scribe_handoff(
    task_summary: str,
    *,
    route_decision: RouteDecision | dict[str, Any] | None = None,
    approval_preview: ApprovalPreview | dict[str, Any] | None = None,
    evidence: Optional[list[str]] = None,
    changed_state: Optional[list[str]] = None,
    changed_files: Optional[list[str]] = None,
    decisions: Optional[list[str]] = None,
    open_followups: Optional[list[str]] = None,
    no_update_required: bool = False,
    no_update_rationale: Optional[str] = None,
    task_id: Optional[str] = None,
    timestamp_utc: Optional[str] = None,
    task_execution_status: Optional[str] = None,
    output_root: Path | str | None = None,
    destination: str = "handoff",
) -> ScribeHandoffResult:
    handoff = build_scribe_handoff(
        task_summary,
        route_decision=route_decision,
        approval_preview=approval_preview,
        evidence=evidence,
        changed_state=changed_state,
        changed_files=changed_files,
        decisions=decisions,
        open_followups=open_followups,
        no_update_required=no_update_required,
        no_update_rationale=no_update_rationale,
        task_id=task_id,
        timestamp_utc=timestamp_utc,
        task_execution_status=task_execution_status,
    )
    return _write_result(handoff, output_root=output_root, destination=destination)


def result_to_dict(result: ScribeHandoffResult) -> dict[str, Any]:
    if not isinstance(result, ScribeHandoffResult):
        raise HandoffError("result_to_dict expects a ScribeHandoffResult")
    return {
        "artifact_path": result.artifact_path,
        "write_performed": result.write_performed,
        "write_verified": result.write_verified,
        "write_error": result.write_error,
        "handoff": decision_to_dict(result.handoff),
    }


def result_to_json(result: ScribeHandoffResult) -> str:
    return json.dumps(result_to_dict(result), ensure_ascii=False, indent=2)
