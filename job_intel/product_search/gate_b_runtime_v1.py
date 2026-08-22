"""Task 4 source-artifact and frozen-runtime binding.

The builder has one path by design: archive the exact clean Git commit, copy
that source into a fresh non-editable venv, materialize the reviewed SQLite
shim, and hash the bytes that the resulting interpreter can actually import.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import sysconfig
import tarfile
import tempfile
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


class AssembledArtifact(_StrictFrozenModel):
    """The complete tree consumed by the content-addressed installer."""

    root: Path
    source_artifact: SourceArtifact
    runtime: FrozenRuntime
    artifact_tree_sha256: str = Field(pattern=SHA256_PATTERN)
    manifest_path: Path
    manifest_sha256: str = Field(pattern=SHA256_PATTERN)


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


def _artifact_tree_hash(
    root: Path,
    *,
    excluded: frozenset[str] = frozenset(),
) -> str:
    """Hash every materialized byte, with only the checksum sidecar excluded.

    ``runtime-manifest.json`` is included after removing its self-referential
    ``artifact_tree_sha256`` field.  Symlinks are refused: hashing a target
    pathname would attest metadata rather than the bytes the process executes.
    """
    if "runtime-manifest.json" in excluded:
        raise ArtifactBuildError("runtime_manifest_must_be_anchored")
    digest = hashlib.sha256()
    if not root.exists() or root.is_symlink():
        return digest.hexdigest()
    paths = sorted(root.rglob("*"), key=lambda path: path.relative_to(root).as_posix())
    for path in paths:
        if path.relative_to(root).as_posix() in excluded:
            continue
        reference = path.relative_to(root).as_posix().encode("utf-8")
        if path.is_symlink():
            raise ArtifactBuildError("artifact_tree_symlink")
        elif path.is_dir():
            digest.update(b"D\0" + reference + b"\0")
        elif path.is_file():
            digest.update(b"F\0" + reference + b"\0")
            if path.relative_to(root).as_posix() == "runtime-manifest.json":
                payload = json.loads(path.read_bytes())
                payload.pop("artifact_tree_sha256", None)
                data = _canonical_bytes(payload)
            else:
                data = path.read_bytes()
            digest.update(data)
            digest.update(b"\0")
        else:
            raise ArtifactBuildError("artifact_tree_entry_invalid")
    return digest.hexdigest()


def _write_json(path: Path, payload: Mapping[str, object]) -> str:
    data = _canonical_bytes(payload)
    path.write_bytes(data)
    return _sha256_bytes(data)


def _contained_python_env(python_executable: Path, python_home: Path | None = None) -> dict[str, str]:
    env = os.environ.copy()
    if python_home is not None:
        env["PYTHONHOME"] = str(python_home)
        library_path = python_home / "lib"
        env["LD_LIBRARY_PATH"] = str(library_path) + (
            os.pathsep + env["LD_LIBRARY_PATH"]
            if env.get("LD_LIBRARY_PATH")
            else ""
        )
    env["PYTHONNOUSERSITE"] = "1"
    return env


def _linked_library_paths(python_executable: Path) -> tuple[Path, ...]:
    try:
        output = subprocess.check_output(
            ["ldd", str(python_executable)], text=True, stderr=subprocess.STDOUT
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ArtifactBuildError("runtime_shared_library_probe_failed") from exc
    paths: list[Path] = []
    for line in output.splitlines():
        match = re.search(r"=>\s+(\S+)\s+\(", line)
        if match is None:
            continue
        path = Path(match.group(1))
        if path.name.startswith("ld-linux"):
            continue
        paths.append(path)
    return tuple(sorted(set(paths)))


def _materialize_linked_libraries(
    python_executable: Path, destination: Path
) -> dict[str, str]:
    library_root = destination / "lib"
    library_root.mkdir(parents=True, exist_ok=True)
    provenance: dict[str, str] = {}
    for soname in _linked_library_paths(python_executable):
        try:
            resolved = soname.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise ArtifactBuildError("runtime_shared_library_unavailable") from exc
        # A soname symlink is acceptable at the source boundary, but only when
        # it resolves within its library directory.  The artifact itself gets
        # regular bytes under the soname, never a link or an escaped target.
        if resolved.parent != soname.parent.resolve():
            raise ArtifactBuildError("runtime_shared_library_escape")
        if not resolved.is_file() or resolved.is_symlink():
            raise ArtifactBuildError("runtime_shared_library_unavailable")
        target = library_root / soname.name
        previous = provenance.get(soname.name)
        if previous is not None and previous != resolved.name:
            raise ArtifactBuildError("runtime_shared_library_name_collision")
        shutil.copy2(resolved, target)
        provenance[soname.name] = resolved.name
    return provenance


def _installed_distribution_lines(
    python_executable: Path, *, python_home: Path | None = None
) -> bytes:
    payload = subprocess.check_output(
        [
            str(python_executable),
            "-c",
            (
                "import importlib.metadata; "
                "items = sorted((d.metadata['Name'], d.version) "
                "for d in importlib.metadata.distributions() "
                "if d.metadata.get('Name')); "
                "print(''.join(f'{name}=={version}\\n' for name, version in items), end='')"
            ),
        ],
        text=True,
        env=_contained_python_env(python_executable, python_home),
    )
    return payload.encode("utf-8")


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


def _site_packages(
    python_executable: Path, *, python_home: Path | None = None
) -> Path:
    value = subprocess.check_output(
        [
            str(python_executable),
            "-c",
            "import sysconfig; print(sysconfig.get_paths()['purelib'])",
        ],
        text=True,
        env=_contained_python_env(python_executable, python_home),
    ).strip()
    return Path(value)


def _copy_entries(source: Path, destination: Path) -> None:
    for entry in sorted(source.iterdir(), key=lambda item: item.name):
        if entry.is_symlink():
            raise ArtifactBuildError(f"runtime_input_symlink:{entry.name}")
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
        if entry.is_dir():
            shutil.copytree(entry, target, symlinks=False, dirs_exist_ok=True)
        elif entry.is_file():
            if target.exists() or target.is_symlink():
                target.unlink()
            shutil.copy2(entry, target)


def _runtime_probe(
    python_executable: Path, *, cwd: Path, python_home: Path | None = None
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
        env=_contained_python_env(python_executable, python_home),
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
            [str(builder_python), "-m", "venv", "--copies", str(destination)],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        raise ArtifactBuildError("frozen_venv_create_failed") from exc
    target_python = destination / "bin" / "python"
    if target_python.is_symlink() or not target_python.is_file():
        raise ArtifactBuildError("frozen_interpreter_not_materialized")
    # CPython's venv creator emits ``lib64 -> lib`` even with ``--copies``.
    # The artifact contract rejects symlinks, so remove this redundant alias
    # before any bytes are hashed.
    lib64 = destination / "lib64"
    if lib64.is_symlink():
        lib64.unlink()
    shared_library_provenance = _materialize_linked_libraries(
        builder_python, destination
    )
    builder_stdlib = Path(
        subprocess.check_output(
            [
                str(builder_python),
                "-c",
                "import sysconfig; print(sysconfig.get_paths()['stdlib'])",
            ],
            text=True,
        ).strip()
    )
    contained_stdlib = destination / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}"
    shutil.copytree(builder_stdlib, contained_stdlib, symlinks=False, dirs_exist_ok=True)
    target_site = _site_packages(target_python, python_home=destination)
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
        target_python, cwd=destination, python_home=destination
    )
    if sqlite_module != "pysqlite3" or tuple(int(part) for part in sqlite_version.split(".")[:2]) < (3, 53):
        raise ArtifactBuildError("runtime_parity_mismatch")
    stdlib_root = contained_stdlib
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
        shared_libraries_sha256=_tree_hash(destination / "lib"),
        shared_library_provenance=shared_library_provenance,
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


def build_assembled_artifact(
    *,
    repo_root: Path,
    commit: str,
    gateway_venv: Path,
    destination: Path,
    python_executable: Path | None = None,
) -> AssembledArtifact:
    """Materialize the exact tree that the Task 9 installer publishes.

    The source archive and frozen interpreter are built by the neutral runtime
    module. The resulting tree is hashed only after all executable inputs and
    the manifest body have been assembled; only the checksum sidecar is
    excluded because it records that external hash.
    """
    destination = destination.resolve()
    if destination.exists():
        raise ArtifactBuildError("assembled_artifact_destination_exists")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=".gate-b-source-", dir=str(destination.parent)
    ) as temporary:
        source_artifact = build_source_artifact(
            repo_root=repo_root,
            commit=commit,
            destination=Path(temporary) / "source-artifact",
        )
        if any(path.is_symlink() for path in source_artifact.source_root.rglob("*")):
            raise ArtifactBuildError("source_artifact_symlink")
        runtime_root = destination / "runtime"
        shutil.copytree(source_artifact.source_root, runtime_root, symlinks=True)
        frozen_runtime = build_frozen_runtime(
            artifact=source_artifact,
            gateway_venv=gateway_venv,
            destination=destination / "python-runtime" / "venv",
            python_executable=python_executable,
        )

    distributions_path = destination / "python-runtime" / "installed-distributions.txt"
    distributions_bytes = _installed_distribution_lines(
        frozen_runtime.python_executable, python_home=frozen_runtime.root
    )
    distributions_path.write_bytes(distributions_bytes)

    dependency_lock = runtime_root / "uv.lock"
    if not dependency_lock.is_file() or dependency_lock.is_symlink():
        raise ArtifactBuildError("dependency_lock_missing")

    # The manifest body is part of the external anchor, except for the one
    # field that records that anchor.  Its checksum sidecar is excluded.
    artifact_tree_sha256 = "0" * 64
    runtime_identity = frozen_runtime.runtime_identity.model_copy(
        update={"artifact_tree_sha256": artifact_tree_sha256}
    )
    manifest_payload: dict[str, object] = {
        # Keep the accepted v3 field shape for the installer and for existing
        # inspection tooling.  The value names the measurement, not the
        # retired launch protocol.
        "schema_version": "3.0.0",
        "runtime_kind": "gate_b_description_evidence",
        "artifact_sha256": source_artifact.artifact_sha256,
        "artifact_tree_sha256": artifact_tree_sha256,
        "candidate_commit": source_artifact.commit,
        "python_version": frozen_runtime.parity.python_version,
        "runtime_tree_sha256": _tree_hash(runtime_root),
        "python_executable_sha256": _sha256_bytes(
            frozen_runtime.python_executable.read_bytes()
        ),
        "shim_sha256": runtime_identity.shim_sha256,
        "stdlib_tree_sha256": runtime_identity.stdlib_inventory_sha256,
        "dependency_lock_sha256": _sha256_bytes(dependency_lock.read_bytes()),
        "installed_distributions_sha256": _sha256_bytes(distributions_bytes),
        "installed_files_sha256": runtime_identity.installed_files_sha256,
        "sys_path_sha256": runtime_identity.sys_path_sha256,
        "native_extensions_sha256": runtime_identity.native_extensions_sha256,
        "shared_libraries_sha256": runtime_identity.shared_libraries_sha256,
        "shared_library_provenance": runtime_identity.shared_library_provenance,
        "editable_installs": [],
    }
    manifest_path = destination / "runtime-manifest.json"
    _write_json(manifest_path, manifest_payload)
    artifact_tree_sha256 = _artifact_tree_hash(
        destination,
        excluded=frozenset({"runtime-manifest.sha256"}),
    )
    manifest_payload["artifact_tree_sha256"] = artifact_tree_sha256
    manifest_sha256 = _write_json(manifest_path, manifest_payload)
    (destination / "runtime-manifest.sha256").write_text(
        manifest_sha256 + "\n", encoding="ascii"
    )

    # Recompute the canonical view as a guard against accidentally writing any
    # other bytes after binding.  The manifest field itself is elided by the
    # hash function, so this is not a self-referential fixed point.
    observed = _artifact_tree_hash(
        destination,
        excluded=frozenset({"runtime-manifest.sha256"}),
    )
    if observed != artifact_tree_sha256:
        raise ArtifactBuildError("assembled_artifact_changed_after_binding")
    runtime = frozen_runtime.model_copy(
        update={"runtime_identity": runtime_identity}
    )
    for path in (destination / "runtime", destination / "python-runtime"):
        for entry in path.rglob("*"):
            if entry.is_file() and not entry.is_symlink():
                entry.chmod(entry.stat().st_mode & ~0o222)
    manifest_path.chmod(manifest_path.stat().st_mode & ~0o222)
    (destination / "runtime-manifest.sha256").chmod(
        (destination / "runtime-manifest.sha256").stat().st_mode & ~0o222
    )
    return AssembledArtifact(
        root=destination,
        source_artifact=source_artifact,
        runtime=runtime,
        artifact_tree_sha256=artifact_tree_sha256,
        manifest_path=manifest_path,
        manifest_sha256=manifest_sha256,
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
    # The deployed ``python -m`` entrypoint can load the evidence runner once
    # as ``__main__`` and once under its qualified name.  Compare the
    # manifest-bound value, not Pydantic class identity across those modules.
    if runtime.runtime_identity.model_dump(mode="json") != manifest.runtime.model_dump(
        mode="json"
    ):
        raise ArtifactBuildError("runtime_identity_mismatch")
    expected = _authority_identity(authorities)
    if expected.policy_sha256 != manifest.authorities.policy_sha256:
        raise ArtifactBuildError("policy_hash_mismatch")
    if expected.prompt_sha256 != manifest.authorities.prompt_sha256:
        raise ArtifactBuildError("prompt_hash_mismatch")
    # See the runtime identity comparison above: the ``-m`` entrypoint may
    # hold the manifest model under ``__main__`` while this module owns a
    # qualified copy.  The authority bytes are the contract, not class
    # identity across those import names.
    if expected.model_dump(mode="json") != manifest.authorities.model_dump(
        mode="json"
    ):
        raise ArtifactBuildError("authority_hash_mismatch")


def _main(arguments: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="gate_b_runtime_v1")
    subparsers = parser.add_subparsers(dest="command", required=True)
    build = subparsers.add_parser("build-artifact")
    build.add_argument("repo_root", type=Path)
    build.add_argument("commit")
    build.add_argument("gateway_venv", type=Path)
    build.add_argument("destination", type=Path)
    build.add_argument("python_executable", type=Path)
    args = parser.parse_args(arguments)
    if args.command == "build-artifact":
        artifact = build_assembled_artifact(
            repo_root=args.repo_root,
            commit=args.commit,
            gateway_venv=args.gateway_venv,
            destination=args.destination,
            python_executable=args.python_executable,
        )
        print(
            json.dumps(
                {
                    "artifact_tree_sha256": artifact.artifact_tree_sha256,
                    "artifact_sha256": artifact.source_artifact.artifact_sha256,
                    "manifest_sha256": artifact.manifest_sha256,
                    "root": str(artifact.root),
                },
                sort_keys=True,
            )
        )
        return 0
    raise AssertionError(f"unhandled command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
