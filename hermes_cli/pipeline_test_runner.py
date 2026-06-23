"""Controlled pytest-only validation boundary for engineering pipeline runs."""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import subprocess
import sys
import time
from typing import Any, Callable, Mapping, Sequence


ENGINEER_SUBAGENT_ID = "hermes_engineer_core"
ALLOWED_EXECUTABLES = {
    ("pytest",),
    ("venv/bin/pytest",),
    (".venv/bin/pytest",),
    ("python", "-m", "pytest"),
    ("python3", "-m", "pytest"),
}
MAX_TEST_COMMAND_COUNT = 3
MAX_ARGV_LENGTH = 12
MAX_ARG_LENGTH = 200
MAX_OUTPUT_CHARS = 800
DEFAULT_TIMEOUT_SECONDS = 30
_DISALLOWED_SHELL_MARKERS = ("&&", "||", ";", "|", ">", "<", "`", "$(", "${", "\n", "\r")
_SECRET_PATTERN = re.compile(r"(?i)\b(api[_-]?key|token|password|secret|credential)\b\s*[:=]\s*\S+")


@dataclass(frozen=True)
class TestCommandResult:
    command: list[str]
    cwd: str | None
    status: str
    exit_code: int | None = None
    duration_ms: float | None = None
    stdout_excerpt: str | None = None
    stderr_excerpt: str | None = None
    stdout_truncated: bool = False
    stderr_truncated: bool = False
    timed_out: bool = False
    reason: str | None = None
    denied_command_raw_sanitized: str | None = None
    denied_argv_sanitized: list[str] | None = None
    validator_reason: str | None = None

    def to_safe_dict(self) -> dict[str, Any]:
        return {
            "command": list(self.command),
            "cwd": self.cwd,
            "status": self.status,
            "exit_code": self.exit_code,
            "duration_ms": self.duration_ms,
            "stdout_excerpt": self.stdout_excerpt,
            "stderr_excerpt": self.stderr_excerpt,
            "stdout_truncated": self.stdout_truncated,
            "stderr_truncated": self.stderr_truncated,
            "timed_out": self.timed_out,
            "reason": self.reason,
            "denied_command_raw_sanitized": self.denied_command_raw_sanitized,
            "denied_argv_sanitized": list(self.denied_argv_sanitized) if self.denied_argv_sanitized is not None else None,
            "validator_reason": self.validator_reason,
        }


@dataclass(frozen=True)
class TestRunSummary:
    enabled: bool
    workspace: str | None
    status: str
    requested_count: int
    executed_count: int
    passed_count: int
    failed_count: int
    denied_count: int
    timeout_count: int
    blocked_reason: str | None = None
    results: list[dict[str, Any]] = field(default_factory=list)

    def to_safe_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "workspace": self.workspace,
            "status": self.status,
            "requested_count": self.requested_count,
            "executed_count": self.executed_count,
            "passed_count": self.passed_count,
            "failed_count": self.failed_count,
            "denied_count": self.denied_count,
            "timeout_count": self.timeout_count,
            "blocked_reason": self.blocked_reason,
            "summary": _summary_text(self),
            "results": [dict(item) for item in self.results],
        }


@dataclass(frozen=True)
class PytestInvocation:
    reported_command: list[str]
    execution_command: list[str]
    raw_command_sanitized: str | None = None
    denied_argv_sanitized: list[str] | None = None
    validator_reason: str | None = None


@dataclass(frozen=True)
class ControlledTestRunner:
    workspace: Path
    subprocess_runner: Callable[..., Any] = subprocess.run
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS

    def run(self, invocations: list[PytestInvocation]) -> TestRunSummary:
        results: list[TestCommandResult] = []
        for invocation in invocations:
            start = time.perf_counter()
            try:
                completed = self.subprocess_runner(
                    invocation.execution_command,
                    cwd=str(self.workspace),
                    shell=False,
                    timeout=self.timeout_seconds,
                    text=True,
                    capture_output=True,
                    env={"PATH": os.environ.get("PATH", "/usr/bin:/bin")},
                )
            except subprocess.TimeoutExpired as exc:
                stdout_excerpt, stdout_truncated = _sanitize_output(getattr(exc, "stdout", None))
                stderr_excerpt, stderr_truncated = _sanitize_output(getattr(exc, "stderr", None))
                result = TestCommandResult(
                    command=list(invocation.reported_command),
                    cwd=self.workspace.name,
                    status="timeout",
                    duration_ms=_duration_ms(start),
                    stdout_excerpt=stdout_excerpt,
                    stderr_excerpt=stderr_excerpt,
                    stdout_truncated=stdout_truncated,
                    stderr_truncated=stderr_truncated,
                    timed_out=True,
                    reason="test_command_timeout",
                    denied_command_raw_sanitized=invocation.raw_command_sanitized,
                    denied_argv_sanitized=list(invocation.denied_argv_sanitized) if invocation.denied_argv_sanitized is not None else None,
                    validator_reason=invocation.validator_reason,
                )
                return _summary_from_results(self.workspace, invocations, results + [result], "test_command_timeout")
            except FileNotFoundError:
                result = TestCommandResult(
                    command=list(invocation.reported_command),
                    cwd=self.workspace.name,
                    status="blocked",
                    duration_ms=_duration_ms(start),
                    reason="test_command_start_failed",
                    denied_command_raw_sanitized=invocation.raw_command_sanitized,
                    denied_argv_sanitized=list(invocation.denied_argv_sanitized) if invocation.denied_argv_sanitized is not None else None,
                    validator_reason=invocation.validator_reason,
                )
                return _summary_from_results(self.workspace, invocations, results + [result], "test_command_start_failed")
            except Exception:
                result = TestCommandResult(
                    command=list(invocation.reported_command),
                    cwd=self.workspace.name,
                    status="error",
                    duration_ms=_duration_ms(start),
                    reason="test_command_failed",
                    denied_command_raw_sanitized=invocation.raw_command_sanitized,
                    denied_argv_sanitized=list(invocation.denied_argv_sanitized) if invocation.denied_argv_sanitized is not None else None,
                    validator_reason=invocation.validator_reason,
                )
                return _summary_from_results(self.workspace, invocations, results + [result], "test_command_failed")

            stdout_excerpt, stdout_truncated = _sanitize_output(completed.stdout)
            stderr_excerpt, stderr_truncated = _sanitize_output(completed.stderr)
            status = "passed" if completed.returncode == 0 else "failed"
            result = TestCommandResult(
                command=list(invocation.reported_command),
                cwd=self.workspace.name,
                status=status,
                exit_code=int(completed.returncode),
                duration_ms=_duration_ms(start),
                stdout_excerpt=stdout_excerpt,
                stderr_excerpt=stderr_excerpt,
                stdout_truncated=stdout_truncated,
                stderr_truncated=stderr_truncated,
                reason=None if status == "passed" else "test_command_failed",
                denied_command_raw_sanitized=invocation.raw_command_sanitized,
                denied_argv_sanitized=list(invocation.denied_argv_sanitized) if invocation.denied_argv_sanitized is not None else None,
                validator_reason=invocation.validator_reason,
            )
            results.append(result)
            if completed.returncode != 0:
                return _summary_from_results(self.workspace, invocations, results, "test_command_failed")
        return _summary_from_results(self.workspace, invocations, results, None)


def run_controlled_tests(
    *,
    allow_test_commands: bool,
    test_workspace: str | Path | None,
    tests_payload: Any,
    step_kind: str,
    step_subagent_id: str,
    subprocess_runner: Callable[..., Any] = subprocess.run,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
) -> TestRunSummary:
    workspace_name = Path(test_workspace).name if test_workspace else None
    try:
        requests = _coerce_test_requests(tests_payload)
    except ValueError:
        requested_count = len(tests_payload) if isinstance(tests_payload, list) else 1
        return TestRunSummary(
            enabled=bool(allow_test_commands),
            workspace=workspace_name,
            status="blocked",
            requested_count=requested_count,
            executed_count=0,
            passed_count=0,
            failed_count=0,
            denied_count=requested_count,
            timeout_count=0,
            blocked_reason="test_command_denied",
            results=[],
        )
    if not requests:
        return TestRunSummary(
            enabled=bool(allow_test_commands),
            workspace=workspace_name,
            status="not_requested",
            requested_count=0,
            executed_count=0,
            passed_count=0,
            failed_count=0,
            denied_count=0,
            timeout_count=0,
            results=[],
        )
    if step_kind != "engineer" or step_subagent_id != ENGINEER_SUBAGENT_ID:
        return _denied_summary(workspace_name, requests, "test_command_role_not_permitted", enabled=bool(allow_test_commands))
    if not allow_test_commands:
        return _denied_summary(workspace_name, requests, "test_command_gate_disabled", enabled=False)
    workspace = _validate_workspace(test_workspace)
    try:
        invocations = [_normalize_invocation(request, workspace) for request in requests]
    except ValueError as exc:
        return _denied_summary(workspace.name, requests, "test_command_denied", enabled=True, validator_reason=str(exc) or "test_command_denied")
    return ControlledTestRunner(
        workspace=workspace,
        subprocess_runner=subprocess_runner,
        timeout_seconds=timeout_seconds,
    ).run(invocations)


def _coerce_test_requests(payload: Any) -> list[Any]:
    if payload is None:
        return []
    if not isinstance(payload, list):
        raise ValueError("tests payload must be a list")
    requests: list[Any] = []
    for item in payload:
        if item is None:
            continue
        if isinstance(item, Mapping):
            requests.append(dict(item))
            continue
        text = str(item).strip()
        if text:
            requests.append(text)
    if len(requests) > MAX_TEST_COMMAND_COUNT:
        raise ValueError("test command count exceeded")
    return requests


def _validate_workspace(workspace: str | Path | None) -> Path:
    if workspace is None:
        raise ValueError("test workspace missing")
    root = Path(workspace)
    if not root.exists() or not root.is_dir():
        raise ValueError("test workspace invalid")
    if not (root / ".git").exists():
        raise ValueError("test workspace must be a git repo")
    return root.resolve()


def _normalize_invocation(request: Any, workspace: Path) -> PytestInvocation:
    if isinstance(request, Mapping):
        return _normalize_structured_invocation(request, workspace)
    if isinstance(request, str):
        return _normalize_legacy_command(request, workspace)
    raise ValueError("test_command_denied")


def _normalize_legacy_command(raw_command: str, workspace: Path) -> PytestInvocation:
    if any(marker in raw_command for marker in _DISALLOWED_SHELL_MARKERS):
        raise ValueError("test_command_denied")
    argv = shlex.split(raw_command, posix=True)
    if not argv or len(argv) > MAX_ARGV_LENGTH or any(len(arg) > MAX_ARG_LENGTH for arg in argv):
        raise ValueError("test_command_denied")
    if tuple(argv[:1]) in ALLOWED_EXECUTABLES:
        path_args = argv[1:]
    elif tuple(argv[:3]) in ALLOWED_EXECUTABLES:
        path_args = argv[3:]
    else:
        raise ValueError("test_command_denied")
    if len(path_args) < 2 or path_args[0] != "-q":
        raise ValueError("test_command_denied")
    for test_path in path_args[1:]:
        _validate_test_path(test_path, workspace)
    return PytestInvocation(
        reported_command=list(argv),
        execution_command=_resolve_execution_command(argv),
        raw_command_sanitized=_sanitize_command_text(raw_command),
        denied_argv_sanitized=_safe_command_tokens(raw_command),
        validator_reason="legacy_pytest_command",
    )


def _normalize_structured_invocation(payload: Mapping[str, Any], workspace: Path) -> PytestInvocation:
    targets = payload.get("targets")
    if not isinstance(targets, list) or not targets:
        raise ValueError("structured_pytest_payload_missing_targets")
    if not bool(payload.get("quiet", False)):
        raise ValueError("structured_pytest_payload_quiet_required")
    argv = [sys.executable, "-m", "pytest", "-q"]
    maxfail = payload.get("maxfail")
    if maxfail is not None:
        try:
            coerced_maxfail = int(maxfail)
        except (TypeError, ValueError) as exc:
            raise ValueError("structured_pytest_payload_invalid_maxfail") from exc
        if coerced_maxfail <= 0:
            raise ValueError("structured_pytest_payload_invalid_maxfail")
        argv.append(f"--maxfail={coerced_maxfail}")
    for raw_target in targets:
        target = str(raw_target).strip()
        if not target:
            raise ValueError("structured_pytest_payload_empty_target")
        _validate_test_path(target, workspace)
        argv.append(target)
    return PytestInvocation(
        reported_command=list(argv),
        execution_command=list(argv),
        denied_argv_sanitized=list(argv),
        validator_reason="structured_pytest_payload",
    )


def _validate_test_path(path_value: str, workspace: Path) -> None:
    path = PurePosixPath(path_value)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("test_command_denied")
    if any(part.startswith(".") for part in path.parts):
        raise ValueError("test_command_denied")
    if not path.parts or path.parts[0] != "tests":
        raise ValueError("test_command_denied")
    destination = (workspace / path).resolve(strict=False)
    destination.relative_to(workspace)


def _denied_summary(workspace_name: str | None, requests: Sequence[Any], reason: str, *, enabled: bool, validator_reason: str | None = None) -> TestRunSummary:
    return TestRunSummary(
        enabled=enabled,
        workspace=workspace_name,
        status="blocked",
        requested_count=len(requests),
        executed_count=0,
        passed_count=0,
        failed_count=0,
        denied_count=len(requests),
        timeout_count=0,
        blocked_reason=reason,
        results=[
            TestCommandResult(
                command=_safe_request_tokens(request),
                cwd=workspace_name,
                status="denied",
                reason=reason,
                denied_command_raw_sanitized=_sanitize_request_text(request),
                denied_argv_sanitized=_safe_request_tokens(request),
                validator_reason=validator_reason or reason,
            ).to_safe_dict()
            for request in requests
        ],
    )


def _resolve_execution_command(command: Sequence[str]) -> list[str]:
    if tuple(command[:1]) == ("pytest",):
        return [sys.executable, "-m", "pytest", *command[1:]]
    if tuple(command[:3]) == ("python", "-m", "pytest"):
        return [sys.executable, *command[1:]]
    return list(command)


def _summary_from_results(workspace: Path, invocations: Sequence[PytestInvocation], results: list[TestCommandResult], blocked_reason: str | None) -> TestRunSummary:
    return TestRunSummary(
        enabled=True,
        workspace=workspace.name,
        status="passed" if blocked_reason is None else ("failed" if blocked_reason == "test_command_failed" else "blocked"),
        requested_count=len(invocations),
        executed_count=len(results),
        passed_count=sum(1 for item in results if item.status == "passed"),
        failed_count=sum(1 for item in results if item.status == "failed"),
        denied_count=sum(1 for item in results if item.status == "denied"),
        timeout_count=sum(1 for item in results if item.status == "timeout"),
        blocked_reason=blocked_reason,
        results=[item.to_safe_dict() for item in results],
    )


def _sanitize_output(value: Any) -> tuple[str | None, bool]:
    if value is None:
        return None, False
    text = _SECRET_PATTERN.sub(r"\1=[redacted]", str(value))
    truncated = len(text) > MAX_OUTPUT_CHARS
    if truncated:
        text = text[: MAX_OUTPUT_CHARS - 12].rstrip() + " [truncated]"
    return text or None, truncated


def _safe_command_tokens(raw_command: str) -> list[str]:
    try:
        tokens = shlex.split(raw_command, posix=True)
    except ValueError:
        return ["[denied]"]
    return [_SECRET_PATTERN.sub(r"\1=[redacted]", token) for token in tokens[:MAX_ARGV_LENGTH]] or ["[denied]"]


def _safe_request_tokens(request: Any) -> list[str]:
    if isinstance(request, Mapping):
        targets = request.get("targets")
        if not isinstance(targets, list):
            return ["[denied]"]
        return [str(item).strip() for item in targets[:MAX_ARGV_LENGTH] if str(item).strip()] or ["[denied]"]
    return _safe_command_tokens(str(request))


def _sanitize_request_text(request: Any) -> str | None:
    if isinstance(request, Mapping):
        return "targets=" + ",".join(_safe_request_tokens(request))
    return _sanitize_command_text(str(request))


def _sanitize_command_text(raw_command: str) -> str | None:
    sanitized = " ".join(_safe_command_tokens(raw_command))
    return sanitized or None


def _summary_text(summary: TestRunSummary) -> str | None:
    if summary.requested_count == 0:
        return None
    if summary.blocked_reason:
        return summary.blocked_reason
    return f"{summary.passed_count} test command passed"


def _duration_ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000, 3)
