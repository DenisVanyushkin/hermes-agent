"""Durable, independently verifiable artifacts for controlled Git changes.

The controlled pipeline runs in a linked worktree.  A report that only says
"files changed" is not a recovery artifact: the worktree can be swept and a
reviewer cannot reconstruct the exact change.  This module captures commits,
tracked working-tree edits and bounded copies of untracked files before a run
is allowed to claim completion.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import tempfile
from typing import Any, Iterable, Mapping, Sequence


CHANGE_ARTIFACT_SCHEMA_VERSION = "change-artifact.v1"
CHANGE_ARTIFACT_FILENAME = "change-artifact.json"
DEFAULT_MAX_ARTIFACT_BYTES = 8 * 1024 * 1024
DEFAULT_MAX_UNTRACKED_FILE_BYTES = 2 * 1024 * 1024


class _ArtifactFailure(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


def persist_change_artifacts(
    *,
    repo_path: Path | str,
    canonical_repo_path: Path | str | None,
    durable_run_root: Path | str,
    baseline_head_sha: str | None,
    run_head_sha: str | None,
    branch: str | None,
    changed_files: Sequence[str] = (),
    tracked_changed_files: Sequence[str] = (),
    untracked_files: Sequence[str] = (),
    staged_files: Sequence[str] = (),
    unstaged_files: Sequence[str] = (),
    material_changes_present: bool,
    max_artifact_bytes: int = DEFAULT_MAX_ARTIFACT_BYTES,
    max_untracked_file_bytes: int = DEFAULT_MAX_UNTRACKED_FILE_BYTES,
) -> dict[str, Any]:
    """Persist and verify the changes in ``repo_path``.

    The returned mapping contains an internal ``metadata_path`` for callers
    that need to inspect the durable artifact.  User-facing payloads must use
    :func:`safe_change_artifact_metadata`, which deliberately omits paths and
    file contents.
    """
    durable_root = Path(durable_run_root).expanduser()
    if not material_changes_present:
        return {
            "schema_version": CHANGE_ARTIFACT_SCHEMA_VERSION,
            "status": "not_required",
            "verified": True,
            "artifact_type": None,
            "artifact_count": 0,
            "content_sha256": None,
            "reason_code": None,
            "metadata_path": None,
        }

    metadata_path = durable_root / CHANGE_ARTIFACT_FILENAME
    payload: dict[str, Any] = {
        "schema_version": CHANGE_ARTIFACT_SCHEMA_VERSION,
        "status": "artifact_not_persisted",
        "verified": False,
        "reason_code": "artifact_not_persisted",
        "baseline_head_sha": baseline_head_sha,
        "run_head_sha": run_head_sha,
        "branch": str(branch or "") or None,
        "changed_files": sorted({str(item) for item in changed_files if str(item)}),
        "artifacts": [],
    }
    try:
        repo = Path(repo_path).expanduser().resolve()
        canonical = Path(canonical_repo_path).expanduser().resolve() if canonical_repo_path else None
        if not repo.is_dir() or canonical is None or not canonical.is_dir():
            raise _ArtifactFailure("canonical_repo_unavailable")
        if not _workspace_is_linked_to_canonical(repo, canonical):
            raise _ArtifactFailure("workspace_not_linked_to_canonical")
        if max_artifact_bytes <= 0 or max_untracked_file_bytes <= 0:
            raise _ArtifactFailure("invalid_artifact_limit")
        for relative in changed_files:
            _safe_relative_path(str(relative), reason_code="unsafe_changed_path")
        for relative in tracked_changed_files:
            _safe_relative_path(str(relative), reason_code="unsafe_changed_path")
        for relative in untracked_files:
            _safe_relative_path(str(relative), reason_code="unsafe_untracked_path")

        current_head = run_head_sha or _git_stdout(repo, "rev-parse", "HEAD")
        baseline = str(baseline_head_sha or "").strip()
        if not baseline:
            raise _ArtifactFailure("baseline_sha_missing")
        durable_root.mkdir(parents=True, exist_ok=True)
        records: list[dict[str, Any]] = []
        total_bytes = 0

        if baseline != current_head:
            bundle = durable_root / "committed.bundle"
            temporary_ref = f"refs/hermes-change-artifacts/{hashlib.sha256(f'{baseline}:{current_head}'.encode()).hexdigest()}"
            _git(repo, "update-ref", temporary_ref, current_head)
            try:
                _git(repo, "bundle", "create", str(bundle), temporary_ref, f"^{baseline}")
            finally:
                _git(repo, "update-ref", "-d", temporary_ref)
            if not bundle.is_file():
                raise _ArtifactFailure("bundle_missing")
            _git(repo, "bundle", "verify", str(bundle))
            _git(canonical, "cat-file", "-e", f"{baseline}^{{commit}}")
            _git(canonical, "cat-file", "-e", f"{current_head}^{{commit}}")
            total_bytes = _record_file(
                records,
                root=durable_root,
                path=bundle,
                relative_path="committed.bundle",
                kind="git_bundle",
                total_bytes=total_bytes,
                max_artifact_bytes=max_artifact_bytes,
            )

        patch = _git_bytes(repo, "diff", "--binary", "HEAD", "--")
        if patch:
            if total_bytes + len(patch) > max_artifact_bytes:
                raise _ArtifactFailure("artifact_too_large")
            patch_path = durable_root / "working-tree.patch"
            _atomic_write_bytes(patch_path, patch)
            total_bytes = _record_file(
                records,
                root=durable_root,
                path=patch_path,
                relative_path="working-tree.patch",
                kind="binary_patch",
                total_bytes=total_bytes,
                max_artifact_bytes=max_artifact_bytes,
            )

        manifest_files: list[dict[str, Any]] = []
        for relative in sorted({str(item) for item in untracked_files if str(item)}):
            source = _safe_repo_file(repo, relative, reason_code="unsafe_untracked_path")
            size = source.stat().st_size
            if size > max_untracked_file_bytes or total_bytes + size > max_artifact_bytes:
                raise _ArtifactFailure("artifact_too_large")
            destination = durable_root / "untracked" / PurePosixPath(relative)
            destination.parent.mkdir(parents=True, exist_ok=True)
            _ensure_artifact_destination(durable_root, destination)
            shutil.copyfile(source, destination)
            total_bytes = _record_file(
                records,
                root=durable_root,
                path=destination,
                relative_path=f"untracked/{relative}",
                kind="untracked_copy",
                total_bytes=total_bytes,
                max_artifact_bytes=max_artifact_bytes,
            )
            manifest_files.append(
                {
                    "path": relative,
                    "bytes": size,
                    "sha256": _sha256_file(source),
                }
            )

        if manifest_files:
            manifest_path = durable_root / "untracked-manifest.json"
            _atomic_write_json(manifest_path, {"schema_version": "untracked-manifest.v1", "files": manifest_files})
            total_bytes = _record_file(
                records,
                root=durable_root,
                path=manifest_path,
                relative_path="untracked-manifest.json",
                kind="untracked_manifest",
                total_bytes=total_bytes,
                max_artifact_bytes=max_artifact_bytes,
            )

        if not records:
            raise _ArtifactFailure("no_artifacts_captured")
        content_sha256 = _combined_content_sha256(durable_root, records)
        payload.update(
            {
                "status": "verified",
                "verified": True,
                "reason_code": None,
                "run_head_sha": current_head,
                "artifacts": records,
                "artifact_type": "+".join(sorted({str(item["kind"]) for item in records})),
                "artifact_count": len(records),
                "bytes": total_bytes,
                "content_sha256": content_sha256,
            }
        )
        _atomic_write_json(metadata_path, payload)
        verified, reason = verify_change_artifact(
            metadata_path=metadata_path,
            repo_path=repo,
            canonical_repo_path=canonical,
        )
        if not verified:
            raise _ArtifactFailure(reason or "artifact_verification_failed")
        return _result_from_payload(payload, metadata_path)
    except _ArtifactFailure as exc:
        payload["status"] = "artifact_not_persisted"
        payload["verified"] = False
        payload["reason_code"] = exc.reason_code
    except (OSError, subprocess.SubprocessError, ValueError):
        payload["status"] = "artifact_not_persisted"
        payload["verified"] = False
        payload["reason_code"] = "artifact_persistence_error"

    try:
        durable_root.mkdir(parents=True, exist_ok=True)
        _atomic_write_json(metadata_path, payload)
        metadata_path_value: str | None = str(metadata_path)
    except OSError:
        metadata_path_value = None
    payload["metadata_path"] = metadata_path_value
    payload.setdefault("artifact_type", None)
    payload.setdefault("artifact_count", len(payload.get("artifacts") or []))
    payload.setdefault("content_sha256", None)
    return payload


def verify_change_artifact(
    *,
    metadata_path: Path | str,
    repo_path: Path | str,
    canonical_repo_path: Path | str | None,
) -> tuple[bool, str | None]:
    """Recompute hashes and verify Git bundle prerequisites."""
    path = Path(metadata_path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("schema_version") != CHANGE_ARTIFACT_SCHEMA_VERSION:
            return False, "schema_version_mismatch"
        if payload.get("status") != "verified":
            return False, "artifact_not_verified"
        root = path.parent.resolve()
        repo = Path(repo_path).resolve()
        canonical = Path(canonical_repo_path).resolve() if canonical_repo_path else None
        if canonical is None or not _workspace_is_linked_to_canonical(repo, canonical):
            return False, "workspace_not_linked_to_canonical"
        records = payload.get("artifacts")
        if not isinstance(records, list) or not records:
            return False, "artifacts_missing"
        for item in records:
            if not isinstance(item, Mapping):
                return False, "artifact_record_invalid"
            relative = str(item.get("path") or "")
            _safe_relative_path(relative, reason_code="artifact_path_invalid")
            artifact_path = (root / relative).resolve()
            if root not in artifact_path.parents or not artifact_path.is_file() or artifact_path.is_symlink():
                return False, "artifact_path_invalid"
            if _sha256_file(artifact_path) != str(item.get("sha256") or ""):
                return False, "artifact_hash_mismatch"
            if item.get("kind") == "git_bundle":
                _git(repo, "bundle", "verify", str(artifact_path))
        if _combined_content_sha256(root, [dict(item) for item in records]) != payload.get("content_sha256"):
            return False, "content_hash_mismatch"
        return True, None
    except _ArtifactFailure as exc:
        return False, exc.reason_code
    except (OSError, ValueError, subprocess.SubprocessError, json.JSONDecodeError):
        return False, "artifact_verification_failed"


def safe_change_artifact_metadata(result: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return only bounded, path-free fields suitable for logs/reports."""
    payload = dict(result or {})
    digest = str(payload.get("content_sha256") or "")
    return {
        "schema_version": str(payload.get("schema_version") or CHANGE_ARTIFACT_SCHEMA_VERSION),
        "status": str(payload.get("status") or "artifact_not_persisted"),
        "verified": bool(payload.get("verified")),
        "artifact_type": payload.get("artifact_type"),
        "artifact_count": int(payload.get("artifact_count") or 0),
        "bytes": int(payload.get("bytes") or 0),
        "content_sha256_prefix": digest[:16] if digest else None,
        "reason_code": payload.get("reason_code"),
    }


def is_verified_change_artifact(*, durable_root: Path | str, repo_path: Path | str, canonical_repo_path: Path | str | None) -> bool:
    metadata = Path(durable_root) / CHANGE_ARTIFACT_FILENAME
    if not metadata.exists():
        return False
    verified, _reason = verify_change_artifact(
        metadata_path=metadata,
        repo_path=repo_path,
        canonical_repo_path=canonical_repo_path,
    )
    return verified


def _result_from_payload(payload: Mapping[str, Any], metadata_path: Path) -> dict[str, Any]:
    result = dict(payload)
    result["metadata_path"] = str(metadata_path)
    return result


def _workspace_is_linked_to_canonical(repo: Path, canonical: Path) -> bool:
    common = _git_stdout(repo, "rev-parse", "--path-format=absolute", "--git-common-dir")
    expected = (canonical / ".git").resolve()
    return Path(common).resolve() == expected


def _safe_relative_path(value: str, *, reason_code: str) -> PurePosixPath:
    if not value or "\x00" in value:
        raise _ArtifactFailure(reason_code)
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts) or "\\" in value:
        raise _ArtifactFailure(reason_code)
    return path


def _safe_repo_file(repo: Path, relative: str, *, reason_code: str) -> Path:
    path = _safe_relative_path(relative, reason_code=reason_code)
    target = repo.joinpath(*path.parts)
    try:
        stat = target.lstat()
    except OSError as exc:
        raise _ArtifactFailure(reason_code) from exc
    if target.is_symlink() or not target.is_file() or not os.path.isfile(target):
        raise _ArtifactFailure(reason_code)
    resolved = target.resolve()
    if repo not in resolved.parents:
        raise _ArtifactFailure(reason_code)
    if not stat:
        raise _ArtifactFailure(reason_code)
    return target


def _ensure_artifact_destination(root: Path, destination: Path) -> None:
    resolved_root = root.resolve()
    resolved_parent = destination.parent.resolve()
    if resolved_parent != resolved_root and resolved_root not in resolved_parent.parents:
        raise _ArtifactFailure("artifact_path_invalid")
    cursor = destination.parent
    while cursor != root:
        if cursor.is_symlink():
            raise _ArtifactFailure("artifact_path_invalid")
        cursor = cursor.parent
    if destination.is_symlink():
        raise _ArtifactFailure("artifact_path_invalid")


def _record_file(
    records: list[dict[str, Any]],
    *,
    root: Path,
    path: Path,
    relative_path: str,
    kind: str,
    total_bytes: int,
    max_artifact_bytes: int,
) -> int:
    size = path.stat().st_size
    if total_bytes > max_artifact_bytes or size > max_artifact_bytes - total_bytes:
        raise _ArtifactFailure("artifact_too_large")
    relative = path.resolve().relative_to(root.resolve()).as_posix()
    if relative != relative_path:
        raise _ArtifactFailure("artifact_path_invalid")
    records.append(
        {
            "kind": kind,
            "path": relative,
            "bytes": size,
            "sha256": _sha256_file(path),
        }
    )
    return total_bytes + size


def _combined_content_sha256(root: Path, records: Iterable[Mapping[str, Any]]) -> str:
    digest = hashlib.sha256()
    for item in records:
        relative = str(item.get("path") or "")
        path = (root / relative).resolve()
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_stdout(repo: Path, *args: str) -> str:
    result = _git(repo, *args)
    return result.stdout.strip()


def _git_bytes(repo: Path, *args: str) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, check=False
    )
    if result.returncode != 0:
        raise subprocess.CalledProcessError(result.returncode, result.args, result.stdout, result.stderr)
    return bytes(result.stdout or b"")


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise subprocess.CalledProcessError(result.returncode, result.args, result.stdout, result.stderr)
    return result


def _atomic_write_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    _atomic_write_bytes(path, (json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"))
