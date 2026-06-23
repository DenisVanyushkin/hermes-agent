"""Safe reviewer packet builder for git material-delta based review handoff."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from hermes_cli.pipeline_git_delta import GitMaterialChangeResult, GitSnapshot


_SCHEMA_VERSION = "reviewer_packet.v1"
_MAX_TEXT_LENGTH = 2000
_BLOCKING_GIT_STATUSES = {
    "baseline_invalid",
    "post_snapshot_invalid",
    "invalid_repo",
    "git_unavailable",
}
_BLOCKING_ENGINEER_STATUSES = {"failed", "blocked"}
_DIFF_MARKERS = ("diff --git", "@@", "+++", "---")
_SENSITIVE_PARTS = ("api_key", "token", "password", "secret", "credential", "env")
_VALID_TEST_STATUSES = {"not_run", "not_requested", "passed", "failed", "blocked", "unknown"}


@dataclass(frozen=True)
class ReviewerPacket:
    schema_version: str
    packet_kind: str
    pipeline_id: str
    session_id: str | None
    packet_status: str
    review_required: bool
    completion_allowed_without_review: bool
    user_action_required: bool
    blocked_reason: str | None
    blocked_reason_detail: str | None
    task_summary: str | None
    engineer_summary: str | None
    engineer_status: str
    git: dict[str, Any]
    tests: dict[str, Any]
    risk_flags: list[str] = field(default_factory=list)
    artifacts: list[dict[str, Any]] = field(default_factory=list)

    def to_safe_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "packet_kind": self.packet_kind,
            "pipeline_id": self.pipeline_id,
            "session_id": self.session_id,
            "packet_status": self.packet_status,
            "review_required": self.review_required,
            "completion_allowed_without_review": self.completion_allowed_without_review,
            "user_action_required": self.user_action_required,
            "blocked_reason": self.blocked_reason,
            "blocked_reason_detail": self.blocked_reason_detail,
            "task_summary": self.task_summary,
            "engineer_summary": self.engineer_summary,
            "engineer_status": self.engineer_status,
            "git": dict(self.git),
            "tests": dict(self.tests),
            "risk_flags": list(self.risk_flags),
            "artifacts": [dict(item) for item in self.artifacts],
        }


def build_reviewer_packet(
    *,
    pipeline_id: str,
    task_summary: str | None,
    engineer_output: Any,
    baseline_snapshot: GitSnapshot,
    post_snapshot: GitSnapshot,
    git_result: GitMaterialChangeResult,
    session_id: str | None = None,
    test_summary: Any = None,
    risk_flags: list[str] | None = None,
    artifacts: list[dict[str, Any]] | None = None,
) -> ReviewerPacket:
    engineer = summarize_engineer_output(engineer_output)
    tests = normalize_test_summary(test_summary)
    git_summary = packet_from_git_delta(
        baseline_snapshot=baseline_snapshot,
        post_snapshot=post_snapshot,
        git_result=git_result,
    )
    review_required = bool(
        git_result.review_required
        or git_result.material_changes_present
        or engineer["invalid"]
        or engineer["failed"]
    )
    blocked_reason = _blocked_reason(git_result=git_result, engineer=engineer)
    blocked_reason_detail = _blocked_reason_detail(git_result=git_result, engineer=engineer)
    user_action_required = blocked_reason is not None
    completion_allowed_without_review = not review_required and blocked_reason is None
    if blocked_reason is not None:
        packet_status = "blocked"
    elif review_required:
        packet_status = "ready_for_review"
    else:
        packet_status = "review_not_required"

    return ReviewerPacket(
        schema_version=_SCHEMA_VERSION,
        packet_kind="reviewer_packet",
        pipeline_id=str(pipeline_id),
        session_id=_clean_optional_text(session_id, max_length=256),
        packet_status=packet_status,
        review_required=review_required,
        completion_allowed_without_review=completion_allowed_without_review,
        user_action_required=user_action_required,
        blocked_reason=blocked_reason,
        blocked_reason_detail=blocked_reason_detail,
        task_summary=_clean_optional_text(task_summary),
        engineer_summary=engineer["summary"],
        engineer_status=engineer["status"],
        git=git_summary,
        tests=tests,
        risk_flags=_sorted_strings(risk_flags or []),
        artifacts=_sanitize_artifacts(artifacts or []) + engineer["artifacts"],
    )


def summarize_engineer_output(engineer_output: Any) -> dict[str, Any]:
    payload = engineer_output if isinstance(engineer_output, Mapping) else {}
    status = str(payload.get("status") or "unknown")
    validation_status = str(payload.get("validation_status") or "unknown")
    requires_review = payload.get("requires_review")
    return {
        "status": status,
        "summary": _clean_optional_text(payload.get("summary")),
        "validation_status": validation_status,
        "validation_errors": _sanitize_validation_errors(payload.get("validation_errors")),
        "requires_review": bool(requires_review) if isinstance(requires_review, bool) else None,
        "failed": status in _BLOCKING_ENGINEER_STATUSES,
        "invalid": validation_status != "valid",
        "artifacts": _sanitize_artifacts(payload.get("artifacts")),
    }


def normalize_test_summary(test_summary: Any) -> dict[str, Any]:
    payload = test_summary if isinstance(test_summary, Mapping) else {}
    status = str(payload.get("status") or "unknown")
    if status not in _VALID_TEST_STATUSES:
        status = "unknown"
    normalized = {
        "status": status,
        "command": _clean_optional_text(payload.get("command"), max_length=512),
        "summary": _clean_optional_text(payload.get("summary")),
        "blocked_reason": _clean_optional_text(payload.get("blocked_reason"), max_length=128),
    }
    results = payload.get("results")
    if isinstance(results, list):
        normalized["results"] = _sanitize_test_results(results)
    return normalized


def packet_from_git_delta(
    *,
    baseline_snapshot: GitSnapshot,
    post_snapshot: GitSnapshot,
    git_result: GitMaterialChangeResult,
) -> dict[str, Any]:
    return {
        "baseline_head_sha": git_result.baseline_head_sha or baseline_snapshot.head_sha,
        "post_head_sha": git_result.post_head_sha or post_snapshot.head_sha,
        "head_changed": bool(git_result.head_changed),
        "baseline_dirty": bool(git_result.baseline_dirty or baseline_snapshot.is_dirty),
        "material_changes_present": bool(git_result.material_changes_present),
        "material_change_status": git_result.status,
        "changed_files": _sorted_strings(git_result.changed_files),
        "untracked_files": _sorted_strings(git_result.untracked_files),
        "staged_files": _sorted_strings(git_result.staged_files),
        "unstaged_files": _sorted_strings(git_result.unstaged_files),
        "review_reason": git_result.blocked_reason or git_result.status,
    }


def _blocked_reason(*, git_result: GitMaterialChangeResult, engineer: dict[str, Any]) -> str | None:
    if git_result.status in _BLOCKING_GIT_STATUSES:
        return git_result.blocked_reason or git_result.status
    if git_result.baseline_dirty:
        return git_result.blocked_reason or "baseline_dirty"
    if engineer["invalid"]:
        return "invalid_engineer_output"
    if engineer["failed"]:
        return "engineer_failed"
    return None


def _blocked_reason_detail(*, git_result: GitMaterialChangeResult, engineer: dict[str, Any]) -> str | None:
    if git_result.status in _BLOCKING_GIT_STATUSES or git_result.baseline_dirty:
        return None
    if engineer["invalid"]:
        if engineer.get("validation_status") == "missing_structured_output":
            for item in engineer.get("validation_errors") or []:
                message = _clean_optional_text(item.get("message"), max_length=128) if isinstance(item, Mapping) else None
                if message == "engineer_max_iterations_without_structured_output":
                    return message
            return "missing_structured_output"
        return "invalid_engineer_structured_output"
    return None


def _sanitize_validation_errors(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    sanitized: list[dict[str, str]] = []
    for item in value[:4]:
        if not isinstance(item, Mapping):
            continue
        field_name = _clean_optional_text(item.get("field"), max_length=64)
        message = _clean_optional_text(item.get("message"), max_length=128)
        if field_name and message:
            sanitized.append({"field": field_name, "message": message})
    return sanitized


def _sanitize_artifacts(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    sanitized: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        artifact_id = _clean_optional_text(item.get("artifact_id"), max_length=128)
        kind = _clean_optional_text(item.get("kind"), max_length=128)
        if artifact_id and kind:
            sanitized.append(
                {
                    "artifact_id": artifact_id,
                    "kind": kind,
                    "redacted": bool(item.get("redacted", True)),
                }
            )
    sanitized.sort(key=lambda item: (item["kind"], item["artifact_id"]))
    return sanitized


def _sanitize_test_results(value: list[Any]) -> list[dict[str, Any]]:
    sanitized: list[dict[str, Any]] = []
    for item in value[:3]:
        if not isinstance(item, Mapping):
            continue
        command = item.get("command")
        safe_command = [str(token) for token in command[:12]] if isinstance(command, list) else None
        safe_item = {
            "command": safe_command,
            "status": _clean_optional_text(item.get("status"), max_length=64),
            "cwd": _clean_optional_text(item.get("cwd"), max_length=128),
            "stdout_excerpt": _clean_optional_text(item.get("stdout_excerpt")),
            "stderr_excerpt": _clean_optional_text(item.get("stderr_excerpt")),
        }
        sanitized.append({key: value for key, value in safe_item.items() if value is not None})
    return sanitized


def _sorted_strings(values: list[str] | tuple[str, ...]) -> list[str]:
    return sorted({str(item) for item in values if item})


def _clean_optional_text(value: Any, *, max_length: int = _MAX_TEXT_LENGTH) -> str | None:
    if value is None:
        return None
    lines: list[str] = []
    for raw_line in str(value).splitlines():
        line = raw_line.strip()
        lower = line.lower()
        if any(marker in line for marker in _DIFF_MARKERS):
            continue
        if any(part in lower for part in _SENSITIVE_PARTS):
            continue
        if line:
            lines.append(line)
    cleaned = " ".join(lines).strip()
    if not cleaned:
        return None
    if len(cleaned) <= max_length:
        return cleaned
    return cleaned[: max_length - 12].rstrip() + " [truncated]"
