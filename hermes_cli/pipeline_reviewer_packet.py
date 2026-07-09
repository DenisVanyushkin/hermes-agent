"""Safe reviewer packet builder for git material-delta based review handoff."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import shlex
from typing import Any, Mapping

from hermes_cli.pipeline_git_delta import GitMaterialChangeResult, GitSnapshot


_SCHEMA_VERSION = "reviewer_packet.v1"
_MAX_TEXT_LENGTH = 2000
_MAX_UNTRACKED_FILE_BYTES = 4096
_MAX_UNTRACKED_FILE_LINES = 120
_BLOCKING_GIT_STATUSES = {
    "baseline_invalid",
    "post_snapshot_invalid",
    "invalid_repo",
    "git_unavailable",
}
_BLOCKING_ENGINEER_STATUSES = {"failed", "blocked"}
_DIFF_MARKERS = ("diff --git", "@@", "+++", "---")
_SENSITIVE_PARTS = ("api_key", "token", "password", "secret", "credential", "env")
MACHINE_CAPTURED_TEST_STATUSES = {"passed", "failed", "blocked", "timeout", "invalid"}
_VALID_TEST_STATUSES = {
    "not_run",
    "not_requested",
    "requested_not_executed",
    *MACHINE_CAPTURED_TEST_STATUSES,
    "unknown",
}


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
    engineer_output_valid: bool
    engineer_output_validation_status: str
    engineer_output_evaluation_status: str | None
    engineer_output_warning: str | None
    engineer_validation_errors: list[dict[str, str]]
    engineer_sanitized_output: dict[str, Any]
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
            "engineer_output_valid": self.engineer_output_valid,
            "engineer_output_validation_status": self.engineer_output_validation_status,
            "engineer_output_evaluation_status": self.engineer_output_evaluation_status,
            "engineer_output_warning": self.engineer_output_warning,
            "engineer_validation_errors": [dict(item) for item in self.engineer_validation_errors],
            "engineer_sanitized_output": dict(self.engineer_sanitized_output),
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
    engineer_evaluation_status: str | None = None,
    risk_flags: list[str] | None = None,
    artifacts: list[dict[str, Any]] | None = None,
    tracked_diff: str | None = None,
) -> ReviewerPacket:
    engineer = summarize_engineer_output(engineer_output)
    tests = normalize_test_summary(test_summary)
    git_summary = packet_from_git_delta(
        baseline_snapshot=baseline_snapshot,
        post_snapshot=post_snapshot,
        git_result=git_result,
        tracked_diff=tracked_diff,
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
        engineer_output_valid=not engineer["invalid"],
        engineer_output_validation_status=engineer["validation_status"],
        engineer_output_evaluation_status=_clean_optional_text(engineer_evaluation_status, max_length=128),
        engineer_output_warning=engineer["warning"],
        engineer_validation_errors=engineer["validation_errors"],
        engineer_sanitized_output=dict(engineer["sanitized_output"]),
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
        "warning": _engineer_output_warning(validation_status),
        "requires_review": bool(requires_review) if isinstance(requires_review, bool) else None,
        "failed": status in _BLOCKING_ENGINEER_STATUSES,
        "invalid": validation_status != "valid",
        "artifacts": _sanitize_artifacts(payload.get("artifacts")),
        "sanitized_output": _sanitize_engineer_output_payload(payload),
    }


def normalize_test_summary(test_summary: Any) -> dict[str, Any]:
    payload = test_summary if isinstance(test_summary, Mapping) else {}
    status = str(payload.get("status") or "unknown")
    if status not in _VALID_TEST_STATUSES:
        status = "unknown"
    requested_command = _clean_test_command_text(payload.get("requested_command") or payload.get("command"), max_length=512)
    executed_command = _clean_test_command_text(payload.get("executed_command") or payload.get("command"), max_length=512)
    command_relation, command_relation_reason = _classify_test_command_relation(
        requested_command=requested_command,
        executed_command=executed_command,
        exit_code=payload.get("exit_code"),
    )
    normalized = {
        "status": status,
        "command": executed_command or requested_command,
        "requested_command": requested_command,
        "executed_command": executed_command,
        "command_relation": command_relation,
        "command_relation_reason": command_relation_reason,
        "exit_code": payload.get("exit_code") if isinstance(payload.get("exit_code"), int) else None,
        "summary": _clean_optional_text(payload.get("summary")),
        "source": _clean_optional_text(payload.get("source"), max_length=64),
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
    tracked_diff: str | None = None,
) -> dict[str, Any]:
    repo_path = post_snapshot.repo_path or baseline_snapshot.repo_path
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
        "untracked_file_details": _collect_untracked_file_details(
            repo_path=repo_path,
            untracked_files=git_result.untracked_files,
        ),
        "tracked_diff": tracked_diff or "",
        "tracked_diff_available": bool(tracked_diff),
    }


def _collect_untracked_file_details(*, repo_path: str | None, untracked_files: list[str]) -> list[dict[str, Any]]:
    repo_root = Path(repo_path) if repo_path else None
    details: list[dict[str, Any]] = []
    for relative_path in _sorted_strings(untracked_files)[:8]:
        detail = _read_untracked_file_detail(repo_root=repo_root, relative_path=relative_path)
        if detail is not None:
            details.append(detail)
    return details


def _read_untracked_file_detail(*, repo_root: Path | None, relative_path: str) -> dict[str, Any] | None:
    detail: dict[str, Any] = {
        "path": relative_path,
        "content_available": False,
        "content_excerpt": None,
        "size_bytes": None,
        "truncated": False,
        "binary": False,
        "omission_reason": None,
    }
    if repo_root is None:
        detail["omission_reason"] = "repo_path_unavailable"
        return detail
    candidate = (repo_root / relative_path).resolve()
    try:
        candidate.relative_to(repo_root.resolve())
    except ValueError:
        detail["omission_reason"] = "outside_repo"
        return detail
    if not candidate.exists() or not candidate.is_file():
        detail["omission_reason"] = "file_missing"
        return detail
    try:
        raw = candidate.read_bytes()
    except OSError:
        detail["omission_reason"] = "read_failed"
        return detail
    detail["size_bytes"] = len(raw)
    if b"\x00" in raw:
        detail["binary"] = True
        detail["omission_reason"] = "binary_file"
        return detail
    excerpt = raw[:_MAX_UNTRACKED_FILE_BYTES]
    truncated = len(raw) > len(excerpt)
    try:
        text = excerpt.decode("utf-8")
    except UnicodeDecodeError:
        detail["binary"] = True
        detail["omission_reason"] = "non_utf8_file"
        return detail
    lines = text.splitlines()
    if len(lines) > _MAX_UNTRACKED_FILE_LINES:
        text = "\n".join(lines[:_MAX_UNTRACKED_FILE_LINES])
        truncated = True
    safe_excerpt = _clean_file_excerpt(text)
    if safe_excerpt is None:
        detail["omission_reason"] = "content_redacted"
        return detail
    detail["content_available"] = True
    detail["content_excerpt"] = safe_excerpt
    detail["truncated"] = truncated
    return detail


def _clean_file_excerpt(value: str) -> str | None:
    lines: list[str] = []
    for raw_line in value.splitlines():
        lower = raw_line.lower()
        if any(part in lower for part in _SENSITIVE_PARTS):
            continue
        lines.append(raw_line.rstrip())
    cleaned = "\n".join(lines).strip()
    if not cleaned:
        return None
    return cleaned


def _classify_test_command_relation(
    *,
    requested_command: str | None,
    executed_command: str | None,
    exit_code: Any,
) -> tuple[str, str | None]:
    if not requested_command and not executed_command:
        return "unknown", None
    if requested_command and executed_command and requested_command == executed_command:
        return "same", "Requested and executed commands match exactly."
    if not requested_command or not executed_command:
        return "unknown", "Only one command form is available."
    requested = _parse_pytest_command(requested_command)
    executed = _parse_pytest_command(executed_command)
    if requested is None or executed is None:
        return "unknown", "Could not normalize one or both pytest commands."
    if requested["targets"] != executed["targets"]:
        if requested["targets"] and executed["targets"]:
            if set(executed["targets"]).issubset(set(requested["targets"])):
                return "narrower", "Executed command covers only a subset of the requested pytest targets."
            if set(requested["targets"]).issubset(set(executed["targets"])):
                return "broader", "Executed command covers the requested pytest targets and additional ones."
            return "different", "Requested and executed commands target different pytest paths."
        return "unknown", "Target coverage could not be compared."
    if exit_code == 0:
        return "equivalent", "Executed command ran the same pytest target with a semantically sufficient invocation."
    return "equivalent", "Executed command targets the same pytest path after normalization."


def _parse_pytest_command(command: str) -> dict[str, Any] | None:
    try:
        tokens = shlex.split(command)
    except ValueError:
        return None
    if not tokens:
        return None
    normalized_tokens = list(tokens)
    tool = Path(normalized_tokens[0]).name
    if tool.startswith("python") and len(normalized_tokens) >= 3 and normalized_tokens[1] == "-m" and normalized_tokens[2] == "pytest":
        normalized_tokens = ["pytest", *normalized_tokens[3:]]
    else:
        normalized_tokens[0] = tool
    if normalized_tokens[0] not in {"pytest", "py.test"}:
        return None
    targets = [token for token in normalized_tokens[1:] if not token.startswith("-")]
    return {"tokens": normalized_tokens, "targets": targets}


def _blocked_reason(*, git_result: GitMaterialChangeResult, engineer: dict[str, Any]) -> str | None:
    if git_result.status in _BLOCKING_GIT_STATUSES:
        return git_result.blocked_reason or git_result.status
    if git_result.baseline_dirty:
        return git_result.blocked_reason or "baseline_dirty"
    if engineer["invalid"]:
        if git_result.material_changes_present:
            return None
        return "invalid_engineer_output"
    if engineer["failed"]:
        if git_result.material_changes_present:
            return None
        return "engineer_failed"
    return None


def _blocked_reason_detail(*, git_result: GitMaterialChangeResult, engineer: dict[str, Any]) -> str | None:
    if git_result.status in _BLOCKING_GIT_STATUSES or git_result.baseline_dirty:
        return None
    if engineer["invalid"]:
        if git_result.material_changes_present:
            return None
        if engineer.get("validation_status") == "missing_structured_output":
            for item in engineer.get("validation_errors") or []:
                message = _clean_optional_text(item.get("message"), max_length=128) if isinstance(item, Mapping) else None
                if message == "engineer_max_iterations_without_structured_output":
                    return message
            return "missing_structured_output"
        return "invalid_engineer_structured_output"
    return None


def _engineer_output_warning(validation_status: str) -> str | None:
    if validation_status == "valid":
        return None
    if validation_status == "missing_structured_output":
        return "engineer structured output missing; reviewer must rely on observed repo state and sanitized raw output."
    return "engineer structured output invalid; reviewer must treat engineer metadata as best-effort evidence."


def _sanitize_engineer_output_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    safe_payload = {
        "status": _clean_optional_text(payload.get("status"), max_length=64),
        "summary": _clean_optional_text(payload.get("summary")),
        "validation_status": _clean_optional_text(payload.get("validation_status"), max_length=128),
        "requires_review": payload.get("requires_review") if isinstance(payload.get("requires_review"), bool) else None,
        "next_action": _clean_optional_text(payload.get("next_action"), max_length=128),
        "blockers": _sorted_strings(payload.get("blockers")) if isinstance(payload.get("blockers"), list) else [],
        "changes": _sanitize_changes(payload.get("changes")),
        "findings": _sanitize_findings(payload.get("findings")),
        "validation_errors": _sanitize_validation_errors(payload.get("validation_errors")),
    }
    return {key: value for key, value in safe_payload.items() if value not in (None, [], {})}


def _sanitize_findings(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    sanitized: list[dict[str, str]] = []
    for item in value[:12]:
        if not isinstance(item, Mapping):
            continue
        safe_item: dict[str, str] = {}
        code = _clean_optional_text(item.get("code"), max_length=128)
        summary = _clean_optional_text(item.get("summary"))
        detail = _clean_optional_text(item.get("detail"))
        severity = _clean_optional_text(item.get("severity"), max_length=32)
        if code:
            safe_item["code"] = code
        if summary:
            safe_item["summary"] = summary
        if detail:
            safe_item["detail"] = detail
        if severity:
            safe_item["severity"] = severity
        if safe_item.get("summary") or safe_item.get("detail"):
            sanitized.append(safe_item)
    return sanitized


def _sanitize_changes(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    sanitized: list[dict[str, str]] = []
    for item in value[:8]:
        if not isinstance(item, Mapping):
            continue
        path = _clean_optional_text(item.get("path"), max_length=256)
        kind = _clean_optional_text(item.get("kind"), max_length=64)
        if path and kind:
            sanitized.append({"path": path, "kind": kind})
    return sanitized


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
            "exit_code": item.get("exit_code") if isinstance(item.get("exit_code"), int) else None,
            "cwd": _clean_optional_text(item.get("cwd"), max_length=128),
            "stdout_excerpt": _clean_optional_text(item.get("stdout_excerpt")),
            "stderr_excerpt": _clean_optional_text(item.get("stderr_excerpt")),
            "denied_command_raw_sanitized": _clean_optional_text(item.get("denied_command_raw_sanitized")),
            "validator_reason": _clean_optional_text(item.get("validator_reason"), max_length=128),
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


def _clean_test_command_text(value: Any, *, max_length: int = 512) -> str | None:
    if value is None:
        return None
    lines: list[str] = []
    for raw_line in str(value).splitlines():
        line = raw_line.strip()
        if any(marker in line for marker in _DIFF_MARKERS):
            continue
        if not line:
            continue
        lines.append(line)
    cleaned = " ".join(lines).strip()
    if not cleaned:
        return None
    if len(cleaned) <= max_length:
        return cleaned
    return cleaned[: max_length - 12].rstrip() + " [truncated]"
