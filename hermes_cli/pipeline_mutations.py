"""Controlled mutation boundary for engineering subagents."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
from pathlib import Path, PurePosixPath
from typing import Any


ALLOWED_MUTATION_OPERATION = "write_text"
MAX_MUTATION_COUNT = 10
MAX_CONTENT_BYTES = 100_000
_DENIED_PATH_PARTS = {".git"}
_DENIED_FILENAMES = {
    ".env",
    "auth.json",
    "config.yml",
    "config.yaml",
    "id_rsa",
    "id_ed25519",
}
_DENIED_NAME_TOKENS = ("secret", "token", "private", "key")


@dataclass(frozen=True)
class MutationRequest:
    operation: str
    path: str
    content: str
    size_limit_exempt: bool = False


@dataclass(frozen=True)
class MutationResult:
    operation: str
    path: str
    status: str
    reason: str | None = None
    content_sha256: str | None = None
    bytes_written: int | None = None

    def to_safe_dict(self) -> dict[str, Any]:
        payload = {"operation": self.operation, "path": self.path, "status": self.status}
        if self.reason is not None:
            payload["reason"] = self.reason
        if self.content_sha256 is not None:
            payload["content_sha256"] = self.content_sha256
        if self.bytes_written is not None:
            payload["bytes_written"] = self.bytes_written
        return payload


@dataclass(frozen=True)
class MutationDenied(Exception):
    reason: str
    operation: str
    path: str

    def to_result(self) -> MutationResult:
        return MutationResult(
            operation=self.operation,
            path=self.path,
            status="denied",
            reason=self.reason,
        )


@dataclass(frozen=True)
class MutationSummary:
    enabled: bool
    workspace: str | None
    attempted_count: int
    applied_count: int
    denied_count: int
    results: list[dict[str, Any]] = field(default_factory=list)

    def to_safe_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "workspace": self.workspace,
            "attempted_count": self.attempted_count,
            "applied_count": self.applied_count,
            "denied_count": self.denied_count,
            "results": [dict(item) for item in self.results],
        }


@dataclass(frozen=True)
class MutationExecutor:
    workspace: Path

    def apply(self, mutations: list[MutationRequest]) -> MutationSummary:
        plans = [self._plan_one(mutation) for mutation in mutations]
        results = [self._apply_planned(plan) for plan in plans]
        return MutationSummary(
            enabled=True,
            workspace=self.workspace.name,
            attempted_count=len(mutations),
            applied_count=len(results),
            denied_count=0,
            results=[item.to_safe_dict() for item in results],
        )

    def _plan_one(self, mutation: MutationRequest) -> tuple[MutationRequest, PurePosixPath, Path]:
        if mutation.operation != ALLOWED_MUTATION_OPERATION:
            raise MutationDenied("unsupported_operation", mutation.operation, mutation.path)
        relative_path = _validate_relative_path(mutation.path)
        _validate_content(
            mutation.content,
            mutation.operation,
            mutation.path,
            size_limit_exempt=mutation.size_limit_exempt,
        )
        _validate_sensitive_path(relative_path, mutation.operation, mutation.path)

        destination = self.workspace / relative_path
        _validate_destination_within_workspace(self.workspace, destination, mutation.operation, mutation.path)
        if destination.exists() and destination.is_symlink():
            raise MutationDenied("symlink_target_denied", mutation.operation, mutation.path)
        return mutation, relative_path, destination

    def _apply_planned(self, plan: tuple[MutationRequest, PurePosixPath, Path]) -> MutationResult:
        mutation, relative_path, destination = plan
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists() and destination.is_symlink():
            raise MutationDenied("symlink_target_denied", mutation.operation, mutation.path)

        content_bytes = mutation.content.encode("utf-8")
        destination.write_text(mutation.content, encoding="utf-8")
        return MutationResult(
            operation=mutation.operation,
            path=relative_path.as_posix(),
            status="applied",
            content_sha256=hashlib.sha256(content_bytes).hexdigest(),
            bytes_written=len(content_bytes),
        )


def apply_controlled_mutations(
    *,
    allow_mutations: bool,
    mutation_workspace: str | Path | None,
    mutations_payload: Any,
) -> MutationSummary:
    requests = _coerce_mutations(mutations_payload)
    workspace_name = Path(mutation_workspace).name if mutation_workspace else None
    if not requests:
        return MutationSummary(
            enabled=bool(allow_mutations),
            workspace=workspace_name,
            attempted_count=0,
            applied_count=0,
            denied_count=0,
            results=[],
        )
    if not allow_mutations:
        return _deny_all("mutation_gate_disabled", requests, workspace_name)
    workspace = _validate_workspace(mutation_workspace)
    try:
        return MutationExecutor(workspace=workspace).apply(requests)
    except MutationDenied as exc:
        return MutationSummary(
            enabled=True,
            workspace=workspace.name,
            attempted_count=len(requests),
            applied_count=0,
            denied_count=1,
            results=[exc.to_result().to_safe_dict()],
        )


def _deny_all(reason: str, requests: list[MutationRequest], workspace_name: str | None) -> MutationSummary:
    return MutationSummary(
        enabled=False,
        workspace=workspace_name,
        attempted_count=len(requests),
        applied_count=0,
        denied_count=len(requests),
        results=[
            MutationResult(
                operation=request.operation,
                path=request.path,
                status="denied",
                reason=reason,
            ).to_safe_dict()
            for request in requests
        ],
    )


def _coerce_mutations(payload: Any) -> list[MutationRequest]:
    if payload is None:
        return []
    if not isinstance(payload, list):
        raise MutationDenied("invalid_mutations_payload", "unknown", "<invalid>")
    if len(payload) > MAX_MUTATION_COUNT:
        raise MutationDenied("mutation_count_exceeded", "unknown", "<batch>")
    requests: list[MutationRequest] = []
    for item in payload:
        if not isinstance(item, dict):
            raise MutationDenied("invalid_mutation_entry", "unknown", "<invalid>")
        operation = str(item.get("operation") or "")
        path = str(item.get("path") or "")
        content = item.get("content")
        if not isinstance(content, str):
            raise MutationDenied("invalid_mutation_content", operation or "unknown", path or "<invalid>")
        requests.append(
            MutationRequest(
                operation=operation,
                path=path,
                content=content,
                size_limit_exempt=bool(item.get("size_limit_exempt")),
            )
        )
    return requests


def _validate_workspace(workspace: str | Path | None) -> Path:
    if workspace is None:
        raise MutationDenied("missing_mutation_workspace", "unknown", "<workspace>")
    root = Path(workspace)
    if not root.exists() or not root.is_dir():
        raise MutationDenied("invalid_mutation_workspace", "unknown", str(workspace))
    if not (root / ".git").exists():
        raise MutationDenied("workspace_not_git_repo", "unknown", str(workspace))
    return root.resolve()


def _validate_relative_path(path_value: str) -> PurePosixPath:
    if not path_value:
        raise MutationDenied("missing_path", ALLOWED_MUTATION_OPERATION, path_value)
    raw = PurePosixPath(path_value)
    if raw.is_absolute():
        raise MutationDenied("absolute_path_denied", ALLOWED_MUTATION_OPERATION, path_value)
    if ".." in raw.parts:
        raise MutationDenied("path_outside_workspace", ALLOWED_MUTATION_OPERATION, path_value)
    return raw


def _validate_content(content: str, operation: str, path: str, *, size_limit_exempt: bool = False) -> None:
    content_bytes = content.encode("utf-8")
    if not size_limit_exempt and len(content_bytes) > MAX_CONTENT_BYTES:
        raise MutationDenied("content_too_large", operation, path)
    if "\x00" in content:
        raise MutationDenied("binary_content_denied", operation, path)


def _validate_sensitive_path(relative_path: PurePosixPath, operation: str, path: str) -> None:
    lowered_parts = [part.lower() for part in relative_path.parts]
    if any(part in _DENIED_PATH_PARTS for part in lowered_parts):
        raise MutationDenied("sensitive_path_denied", operation, path)
    name = relative_path.name.lower()
    if name in _DENIED_FILENAMES:
        raise MutationDenied("sensitive_path_denied", operation, path)
    if any(token in name for token in _DENIED_NAME_TOKENS):
        raise MutationDenied("sensitive_path_denied", operation, path)


def _validate_destination_within_workspace(workspace: Path, destination: Path, operation: str, path: str) -> None:
    resolved_workspace = workspace.resolve()
    current = workspace
    for part in destination.relative_to(workspace).parts[:-1]:
        current = current / part
        if current.exists() and current.is_symlink():
            raise MutationDenied("symlink_target_denied", operation, path)
        resolved_current = current.resolve(strict=False)
        try:
            resolved_current.relative_to(resolved_workspace)
        except ValueError as exc:
            raise MutationDenied("path_outside_workspace", operation, path) from exc
