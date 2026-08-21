"""Task 4 source-artifact and frozen-runtime binding.

The builder has one path by design: archive the exact clean Git commit, copy
that source into a fresh non-editable venv, materialize the reviewed SQLite
shim, and hash the bytes that the resulting interpreter can actually import.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import sysconfig
import tarfile
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from job_intel.product_search.gate_b_evidence_runner_v1 import (
    AuthorityIdentity,
    EvidenceManifest,
    EvidenceManifestRow,
    Limits,
    RuntimeIdentity,
)


SHA256_PATTERN = r"^[0-9a-f]{64}$"
COMMIT_PATTERN = r"^[0-9a-f]{40}$"
SHIM_NAME = "00-pysqlite3-shim.pth"


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ArtifactBuildError(ValueError):
    """A source, runtime, or evidence identity failed closed."""


class AuthorityInputs(_StrictFrozenModel):
    model_bytes: bytes
    prompt_bytes: bytes
    response_schema_bytes: bytes
    profile_bytes: bytes
    policy_bytes: bytes
    decision_v2_bytes: bytes
    source_authority_bytes: dict[str, bytes]


class SourceArtifact(_StrictFrozenModel):
    commit: str = Field(pattern=COMMIT_PATTERN)
    source_root: Path
    archive_sha256: str = Field(pattern=SHA256_PATTERN)
    artifact_sha256: str = Field(pattern=SHA256_PATTERN)


class RuntimeParity(_StrictFrozenModel):
    python_version: str
    sqlite_module: Literal["pysqlite3"]
    sqlite_version: str


class FrozenRuntime(_StrictFrozenModel):
    root: Path
    python_executable: Path
    runtime_identity: RuntimeIdentity
    parity: RuntimeParity
    shim_sha256: str = Field(pattern=SHA256_PATTERN)
    reproducibility: Literal["frozen_non_editable"]


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _tree_hash(root: Path) -> str:
    digest = hashlib.sha256()
    if not root.exists():
        return digest.hexdigest()
    paths = sorted(path for path in root.rglob("*") if path.is_file())
    for path in paths:
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _artifact_tree_hash(root: Path) -> str:
    """Hash every entry in the materialized runtime, including symlink targets."""
    digest = hashlib.sha256()
    if not root.exists() or root.is_symlink():
        return digest.hexdigest()
    paths = sorted(root.rglob("*"), key=lambda path: path.relative_to(root).as_posix())
    for path in paths:
        reference = path.relative_to(root).as_posix().encode("utf-8")
        if path.is_symlink():
            digest.update(b"L\0" + reference + b"\0")
            digest.update(os.readlink(path).encode("utf-8"))
            digest.update(b"\0")
        elif path.is_dir():
            digest.update(b"D\0" + reference + b"\0")
        elif path.is_file():
            digest.update(b"F\0" + reference + b"\0")
            digest.update(path.read_bytes())
            digest.update(b"\0")
        else:
            raise ArtifactBuildError("artifact_tree_entry_invalid")
    return digest.hexdigest()


def _inventory_hash(root: Path, *, suffixes: frozenset[str] | None = None) -> str:
    digest = hashlib.sha256()
    if not root.exists():
        return digest.hexdigest()
    paths = sorted(path for path in root.rglob("*") if path.is_file())
    if suffixes is not None:
        paths = [path for path in paths if path.suffix in suffixes]
    for path in paths:
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _distribution_inventory_hash(root: Path) -> str:
    digest = hashlib.sha256()
    if not root.exists():
        return digest.hexdigest()
    paths = sorted(
        path
        for path in root.rglob("*")
        if path.is_file()
        and any(
            parent.name.endswith((".dist-info", ".egg-info"))
            for parent in path.parents
        )
    )
    for path in paths:
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _run(*args: str, cwd: Path) -> str:
    try:
        return subprocess.check_output(args, cwd=cwd, text=True, stderr=subprocess.STDOUT).strip()
    except subprocess.CalledProcessError as exc:
        raise ArtifactBuildError(f"command_failed:{args[0]}") from exc


def build_source_artifact(
    *,
    repo_root: Path,
    commit: str,
    destination: Path,
) -> SourceArtifact:
    """Archive one exact clean commit into a temporary source artifact."""
    status = _run(
        "git",
        "status",
        "--porcelain",
        "--untracked-files=all",
        cwd=repo_root,
    )
    if status:
        raise ArtifactBuildError("source_tree_dirty")
    resolved_commit = _run("git", "rev-parse", f"{commit}^{{commit}}", cwd=repo_root)
    if resolved_commit != commit:
        raise ArtifactBuildError("source_commit_mismatch")
    destination.mkdir(parents=True, exist_ok=False)
    archive_path = destination / "source.tar"
    try:
        archive_bytes = subprocess.check_output(
            ["git", "archive", "--format=tar", commit],
            cwd=repo_root,
        )
    except subprocess.CalledProcessError as exc:
        raise ArtifactBuildError("source_archive_failed") from exc
    archive_path.write_bytes(archive_bytes)
    source_root = destination / "source"
    source_root.mkdir()
    with tarfile.open(fileobj=archive_path.open("rb"), mode="r:") as archive:
        for member in archive.getmembers():
            target = (source_root / member.name).resolve()
            if source_root.resolve() not in target.parents and target != source_root.resolve():
                raise ArtifactBuildError("source_archive_path_escape")
        archive.extractall(source_root, filter="data")
    return SourceArtifact(
        commit=commit,
        source_root=source_root,
        archive_sha256=_sha256_bytes(archive_bytes),
        artifact_sha256=_tree_hash(source_root),
    )


def _site_packages(python_executable: Path) -> Path:
    value = subprocess.check_output(
        [
            str(python_executable),
            "-c",
            "import sysconfig; print(sysconfig.get_paths()['purelib'])",
        ],
        text=True,
    ).strip()
    return Path(value)


def _copy_entries(source: Path, destination: Path) -> None:
    for entry in sorted(source.iterdir(), key=lambda item: item.name):
        # The gateway environment is an editable checkout. Carrying its
        # finder or virtualenv .pth files into the artifact would reintroduce
        # absolute source paths and make the purportedly frozen runtime
        # import from the mutable worktree.
        if entry.name.startswith(("__editable__", "_virtualenv")):
            continue
        if entry.name.startswith("hermes_agent-") and entry.name.endswith(
            (".dist-info", ".egg-info")
        ):
            continue
        if entry.suffix == ".pth":
            try:
                pth_text = entry.read_text(encoding="utf-8")
            except UnicodeDecodeError as exc:
                raise ArtifactBuildError("gateway_pth_not_utf8") from exc
            if "__editable__" in pth_text or "_virtualenv" in pth_text:
                continue
        target = destination / entry.name
        if entry.is_dir() and not entry.is_symlink():
            shutil.copytree(entry, target, symlinks=True, dirs_exist_ok=True)
        elif entry.is_file() or entry.is_symlink():
            if target.exists() or target.is_symlink():
                target.unlink()
            shutil.copy2(entry, target, follow_symlinks=False)


def _runtime_probe(
    python_executable: Path, *, cwd: Path
) -> tuple[str, str, str, tuple[str, ...]]:
    payload = subprocess.check_output(
        [
            str(python_executable),
            "-c",
            (
                "import json,sqlite3,sys; "
                "print(json.dumps({'version': '.'.join(map(str, sys.version_info[:3])), "
                "'sqlite_module': sqlite3.__name__, 'sqlite_version': sqlite3.sqlite_version, "
                "'sys_path': list(sys.path)}))"
            ),
        ],
        cwd=cwd,
        text=True,
    )
    observed = json.loads(payload)
    runtime_root = cwd.resolve()
    normalized_sys_path: list[str] = []
    for item in observed["sys_path"]:
        if not item:
            normalized_sys_path.append("")
            continue
        candidate = Path(item)
        try:
            relative = candidate.resolve().relative_to(runtime_root)
        except ValueError:
            normalized_sys_path.append(str(candidate))
        else:
            normalized_sys_path.append(f"$RUNTIME_ROOT/{relative.as_posix()}")
    return (
        str(observed["version"]),
        str(observed["sqlite_module"]),
        str(observed["sqlite_version"]),
        tuple(normalized_sys_path),
    )


def build_frozen_runtime(
    *,
    artifact: SourceArtifact,
    gateway_venv: Path,
    destination: Path,
    python_executable: Path | None = None,
) -> FrozenRuntime:
    """Create a non-editable venv and copy the declared runtime inputs into it."""
    if not artifact.source_root.is_dir():
        raise ArtifactBuildError("source_artifact_missing")
    gateway_python = gateway_venv / "bin" / "python"
    if not gateway_python.is_file():
        gateway_python = python_executable or Path(sys.executable)
    gateway_site = _site_packages(gateway_python)
    shim = gateway_site / SHIM_NAME
    if not shim.is_file():
        raise ArtifactBuildError("shim_runtime_input_missing")
    destination.mkdir(parents=True, exist_ok=False)
    builder_python = python_executable or gateway_python
    try:
        subprocess.run(
            [str(builder_python), "-m", "venv", str(destination)],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        raise ArtifactBuildError("frozen_venv_create_failed") from exc
    target_python = destination / "bin" / "python"
    target_site = _site_packages(target_python)
    target_site.mkdir(parents=True, exist_ok=True)
    _copy_entries(gateway_site, target_site)
    # Never merge an older editable/source tree with the archived commit:
    # stale modules would remain importable and fall outside artifact_sha256.
    for entry in artifact.source_root.iterdir():
        target = target_site / entry.name
        if target.exists() or target.is_symlink():
            if target.is_dir() and not target.is_symlink():
                shutil.rmtree(target)
            else:
                target.unlink()
    _copy_entries(artifact.source_root, target_site)
    if not (target_site / SHIM_NAME).is_file():
        raise ArtifactBuildError("shim_materialization_failed")

    version, sqlite_module, sqlite_version, sys_path = _runtime_probe(
        target_python, cwd=destination
    )
    if sqlite_module != "pysqlite3" or tuple(int(part) for part in sqlite_version.split(".")[:2]) < (3, 53):
        raise ArtifactBuildError("runtime_parity_mismatch")
    stdlib_root = Path(
        subprocess.check_output(
            [str(target_python), "-c", "import sysconfig; print(sysconfig.get_paths()['stdlib'])"],
            text=True,
        ).strip()
    )
    native_suffixes = frozenset({".so", ".dylib", ".dll"})
    shim_sha256 = _sha256_bytes((target_site / SHIM_NAME).read_bytes())
    artifact_tree_sha256 = _artifact_tree_hash(destination)
    runtime_identity = RuntimeIdentity(
        artifact_sha256=artifact.artifact_sha256,
        artifact_tree_sha256=artifact_tree_sha256,
        shim_sha256=shim_sha256,
        interpreter_sha256=_sha256_bytes(target_python.read_bytes()),
        stdlib_inventory_sha256=_tree_hash(stdlib_root),
        installed_distributions_sha256=_distribution_inventory_hash(target_site),
        installed_files_sha256=_tree_hash(target_site),
        sys_path_sha256=_sha256_bytes("\n".join(sys_path).encode("utf-8")),
        native_extensions_sha256=_inventory_hash(target_site, suffixes=native_suffixes),
        shared_libraries_sha256=_inventory_hash(stdlib_root, suffixes=native_suffixes),
    )
    return FrozenRuntime(
        root=destination,
        python_executable=target_python,
        runtime_identity=runtime_identity,
        parity=RuntimeParity(
            python_version=version,
            sqlite_module=sqlite_module,
            sqlite_version=sqlite_version,
        ),
        shim_sha256=shim_sha256,
        reproducibility="frozen_non_editable",
    )


def _authority_identity(authorities: AuthorityInputs) -> AuthorityIdentity:
    return AuthorityIdentity(
        model_sha256=_sha256_bytes(authorities.model_bytes),
        prompt_sha256=_sha256_bytes(authorities.prompt_bytes),
        response_schema_sha256=_sha256_bytes(authorities.response_schema_bytes),
        profile_sha256=_sha256_bytes(authorities.profile_bytes),
        policy_sha256=_sha256_bytes(authorities.policy_bytes),
        decision_v2_sha256=_sha256_bytes(authorities.decision_v2_bytes),
        pricing_sha256=_sha256_bytes(b"pricing:v1"),
        source_authority_sha256s={
            key: _sha256_bytes(value)
            for key, value in sorted(authorities.source_authority_bytes.items())
        },
    )


def build_evidence_manifest(
    *,
    run_id: str,
    created_at: datetime,
    source_artifact: SourceArtifact,
    runtime: FrozenRuntime,
    rows: tuple[EvidenceManifestRow, ...],
    authorities: AuthorityInputs,
) -> EvidenceManifest:
    if runtime.runtime_identity.artifact_sha256 != source_artifact.artifact_sha256:
        raise ArtifactBuildError("artifact_hash_mismatch")
    if tuple(row.ordinal for row in rows) != tuple(range(48)):
        raise ArtifactBuildError("corpus_order_mismatch")
    identity = _authority_identity(authorities)
    payload: dict[str, object] = {
        "schema_version": "gate-b-evidence-manifest-v1",
        "run_id": run_id,
        "created_at": created_at.isoformat().replace("+00:00", "Z"),
        "benchmark_kind": "gate_b_description_evidence",
        "row_count": 48,
        "rows": [row.model_dump(mode="json") for row in rows],
        "runtime": runtime.runtime_identity.model_dump(mode="json"),
        "authorities": identity.model_dump(mode="json"),
        "limits": Limits(
            ordered_call_cap=48,
            per_call_maximum_usd="0.01",
            aggregate_maximum_usd="0.48",
        ).model_dump(mode="json"),
    }
    identity_body = dict(payload)
    identity_body.pop("created_at")
    payload["manifest_sha256"] = _sha256_bytes(_canonical_bytes(identity_body))
    return EvidenceManifest.model_validate(payload)


def verify_manifest_binding(
    manifest: EvidenceManifest,
    *,
    source_artifact: SourceArtifact,
    runtime: FrozenRuntime,
    rows: tuple[EvidenceManifestRow, ...],
    authorities: AuthorityInputs,
) -> None:
    if rows != manifest.rows:
        raise ArtifactBuildError("corpus_order_mismatch")
    if source_artifact.artifact_sha256 != manifest.runtime.artifact_sha256:
        raise ArtifactBuildError("artifact_hash_mismatch")
    if runtime.runtime_identity != manifest.runtime:
        raise ArtifactBuildError("runtime_identity_mismatch")
    expected = _authority_identity(authorities)
    if expected.policy_sha256 != manifest.authorities.policy_sha256:
        raise ArtifactBuildError("policy_hash_mismatch")
    if expected.prompt_sha256 != manifest.authorities.prompt_sha256:
        raise ArtifactBuildError("prompt_hash_mismatch")
    if expected != manifest.authorities:
        raise ArtifactBuildError("authority_hash_mismatch")
