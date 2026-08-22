from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

import job_intel.product_search.gate_b_runtime_v1 as runtime_v1
from job_intel.product_search.gate_b_evidence_runner_v1 import EvidenceManifestRow
from job_intel.product_search.gate_b_runtime_v1 import (
    ArtifactBuildError,
    AuthorityInputs,
    FrozenRuntime,
    RuntimeIdentity,
    SourceArtifact,
    AssembledArtifact,
    build_assembled_artifact,
    build_evidence_manifest,
    build_frozen_runtime,
    build_source_artifact,
    verify_manifest_binding,
    _authority_identity,
)


def _git_fixture(root: Path) -> str:
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "Test"], check=True)
    (root / "job_intel").mkdir()
    (root / "job_intel" / "__init__.py").write_text("", encoding="utf-8")
    (root / "job_intel" / "marker.py").write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-q", "-m", "fixture"], check=True)
    return subprocess.check_output(
        ["git", "-C", str(root), "rev-parse", "HEAD"], text=True
    ).strip()


def _authority_inputs() -> AuthorityInputs:
    return AuthorityInputs(
        model_bytes=b"model:gpt-test",
        prompt_bytes=b"prompt:v1",
        response_schema_bytes=b'{"schema":"response-v1"}',
        profile_bytes=b"profile:v1",
        policy_bytes=b"policy:v1",
        decision_v2_bytes=b"decision:v2",
        pricing_bytes=b"pricing:v1",
        source_authority_bytes={"gate_a": b"gate-a:v1"},
    )


def test_pricing_authority_bytes_change_identity() -> None:
    first = _authority_identity(_authority_inputs())
    second = _authority_identity(
        _authority_inputs().model_copy(update={"pricing_bytes": b"pricing:v2"})
    )
    assert first.pricing_sha256 != second.pricing_sha256


def _rows() -> tuple[EvidenceManifestRow, ...]:
    return tuple(
        EvidenceManifestRow(
            ordinal=index,
            corpus_key=f"row-{index}",
            raw_sha256=f"{index + 1:064x}",
            input_sha256=f"{index + 101:064x}",
            projection_sha256=f"{index + 201:064x}",
        )
        for index in range(48)
    )


def test_materialization_rejects_live_hermes_agent_paths(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    commit = _git_fixture(repo)
    live_destination = runtime_v1.HERMES_AGENT_ROOT / "venv" / "gate-b-test-artifact"

    with pytest.raises(ArtifactBuildError, match="artifact_destination_inside_hermes_agent"):
        build_source_artifact(
            repo_root=repo,
            commit=commit,
            destination=live_destination,
        )
    with pytest.raises(ArtifactBuildError, match="artifact_destination_inside_hermes_agent"):
        build_frozen_runtime(
            artifact=None,  # type: ignore[arg-type]
            gateway_venv=tmp_path / "gateway",
            destination=live_destination,
        )
    with pytest.raises(ArtifactBuildError, match="artifact_destination_inside_hermes_agent"):
        build_assembled_artifact(
            repo_root=repo,
            commit=commit,
            gateway_venv=tmp_path / "gateway",
            destination=live_destination,
        )

    from tests.product_search import gate_b_cli_smoke_fixture as fixture

    with pytest.raises(ArtifactBuildError, match="artifact_destination_inside_hermes_agent"):
        fixture.prepare(
            root=tmp_path / "fixture",
            artifact_root=live_destination,
            repo_root=repo,
        )


def test_source_artifact_rejects_dirty_worktree_before_archiving(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    commit = _git_fixture(repo)
    (repo / "untracked.py").write_text("DIRTY = True\n", encoding="utf-8")

    with pytest.raises(ArtifactBuildError, match="source_tree_dirty"):
        build_source_artifact(
            repo_root=repo,
            commit=commit,
            destination=tmp_path / "artifact",
        )


def test_assembled_artifact_hash_covers_runtime_and_manifest_body(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    commit = "a" * 40
    source_root = tmp_path / "source"
    source_root.mkdir()
    (source_root / "tracked.txt").write_text("reviewed\n", encoding="utf-8")
    (source_root / "uv.lock").write_text("lock\n", encoding="utf-8")
    source_hash = runtime_v1._tree_hash(source_root)
    source = SourceArtifact(
        commit=commit,
        source_root=source_root,
        archive_sha256="b" * 64,
        artifact_sha256=source_hash,
    )

    def fake_source_artifact(**_: object) -> SourceArtifact:
        return source

    def fake_frozen_runtime(*, artifact: SourceArtifact, destination: Path, **_: object) -> FrozenRuntime:
        python = destination / "bin" / "python"
        python.parent.mkdir(parents=True)
        python.write_bytes(b"interpreter-bytes")
        identity = RuntimeIdentity(
            artifact_sha256=artifact.artifact_sha256,
            artifact_tree_sha256="c" * 64,
            shim_sha256="d" * 64,
            interpreter_sha256=hashlib.sha256(python.read_bytes()).hexdigest(),
            stdlib_inventory_sha256="e" * 64,
            installed_distributions_sha256="f" * 64,
            installed_files_sha256="0" * 64,
            sys_path_sha256="1" * 64,
            native_extensions_sha256="2" * 64,
            shared_libraries_sha256="3" * 64,
        )
        return FrozenRuntime(
            root=destination,
            python_executable=python,
            runtime_identity=identity,
            parity=runtime_v1.RuntimeParity(
                python_version="3.12.3",
                sqlite_module="pysqlite3",
                sqlite_version="3.53.4",
            ),
            shim_sha256="d" * 64,
            reproducibility="frozen_non_editable",
        )

    monkeypatch.setattr(runtime_v1, "build_source_artifact", fake_source_artifact)
    monkeypatch.setattr(runtime_v1, "build_frozen_runtime", fake_frozen_runtime)
    monkeypatch.setattr(
        runtime_v1,
        "_installed_distribution_lines",
        lambda *_args, **_kwargs: b"pydantic==2.11.7\n",
    )
    assembled = build_assembled_artifact(
        repo_root=tmp_path / "repo",
        commit=commit,
        gateway_venv=tmp_path / "gateway",
        destination=tmp_path / "assembled",
        python_executable=Path(sys.executable),
    )

    assert isinstance(assembled, AssembledArtifact)
    assert (assembled.root / "runtime/tracked.txt").read_text() == "reviewed\n"
    assert (assembled.root / "python-runtime/venv/bin/python").read_bytes() == b"interpreter-bytes"
    payload = json.loads((assembled.root / "runtime-manifest.json").read_bytes())
    assert payload["runtime_kind"] == "gate_b_description_evidence"
    assert payload["artifact_tree_sha256"] == assembled.artifact_tree_sha256
    assert assembled.runtime.runtime_identity.artifact_tree_sha256 == assembled.artifact_tree_sha256
    assert assembled.runtime.runtime_identity.artifact_tree_sha256 != "0" * 64
    assert runtime_v1._artifact_tree_hash(
        assembled.root,
        excluded=frozenset({"runtime-manifest.sha256"}),
    ) == assembled.artifact_tree_sha256
    tampered = assembled.root / "python-runtime/venv/bin/python"
    tampered.chmod(0o600)
    tampered.write_bytes(b"tampered")
    assert runtime_v1._artifact_tree_hash(
        assembled.root,
        excluded=frozenset({"runtime-manifest.sha256"}),
    ) != assembled.artifact_tree_sha256


def test_artifact_tree_hash_rejects_symlink_entries(tmp_path: Path) -> None:
    root = tmp_path / "artifact"
    root.mkdir()
    (root / "target.txt").write_text("bytes\n", encoding="utf-8")
    (root / "link.txt").symlink_to("target.txt")

    with pytest.raises(ArtifactBuildError, match="artifact_tree_symlink"):
        runtime_v1._artifact_tree_hash(root)


def test_materialize_linked_libraries_resolves_versioned_soname(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_root = tmp_path / "system-lib"
    source_root.mkdir()
    target = source_root / "libz.so.1.3"
    target.write_bytes(b"versioned-library")
    soname = source_root / "libz.so.1"
    soname.symlink_to(target.name)
    monkeypatch.setattr(runtime_v1, "_linked_library_paths", lambda _: (soname,))

    provenance = runtime_v1._materialize_linked_libraries(
        Path("/usr/bin/python3"), tmp_path / "runtime"
    )

    materialized = tmp_path / "runtime" / "lib" / "libz.so.1"
    assert materialized.read_bytes() == target.read_bytes()
    assert not materialized.is_symlink()
    assert provenance == {"libz.so.1": "libz.so.1.3"}


def test_materialize_linked_libraries_rejects_soname_escape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_root = tmp_path / "system-lib"
    source_root.mkdir()
    outside = tmp_path / "outside.so"
    outside.write_bytes(b"outside")
    soname = source_root / "libescape.so.1"
    soname.symlink_to(outside)
    monkeypatch.setattr(runtime_v1, "_linked_library_paths", lambda _: (soname,))

    with pytest.raises(ArtifactBuildError, match="runtime_shared_library_escape"):
        runtime_v1._materialize_linked_libraries(
            Path("/usr/bin/python3"), tmp_path / "runtime"
        )


def test_manifest_body_is_covered_by_external_tree_anchor(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    commit = "a" * 40
    source_root = tmp_path / "source"
    source_root.mkdir()
    (source_root / "uv.lock").write_text("lock\n", encoding="utf-8")
    source = SourceArtifact(
        commit=commit,
        source_root=source_root,
        archive_sha256="b" * 64,
        artifact_sha256=runtime_v1._tree_hash(source_root),
    )

    def fake_source_artifact(**_: object) -> SourceArtifact:
        return source

    def fake_frozen_runtime(*, artifact: SourceArtifact, destination: Path, **_: object) -> FrozenRuntime:
        python = destination / "bin" / "python"
        python.parent.mkdir(parents=True)
        python.write_bytes(b"interpreter-bytes")
        identity = RuntimeIdentity(
            artifact_sha256=artifact.artifact_sha256,
            artifact_tree_sha256="c" * 64,
            shim_sha256="d" * 64,
            interpreter_sha256=hashlib.sha256(python.read_bytes()).hexdigest(),
            stdlib_inventory_sha256="e" * 64,
            installed_distributions_sha256="f" * 64,
            installed_files_sha256="0" * 64,
            sys_path_sha256="1" * 64,
            native_extensions_sha256="2" * 64,
            shared_libraries_sha256="3" * 64,
        )
        return FrozenRuntime(
            root=destination,
            python_executable=python,
            runtime_identity=identity,
            parity=runtime_v1.RuntimeParity(
                python_version="3.12.3",
                sqlite_module="pysqlite3",
                sqlite_version="3.53.4",
            ),
            shim_sha256="d" * 64,
            reproducibility="frozen_non_editable",
        )

    monkeypatch.setattr(runtime_v1, "build_source_artifact", fake_source_artifact)
    monkeypatch.setattr(runtime_v1, "build_frozen_runtime", fake_frozen_runtime)
    monkeypatch.setattr(
        runtime_v1,
        "_installed_distribution_lines",
        lambda *_args, **_kwargs: b"pydantic==2.11.7\n",
    )
    assembled = build_assembled_artifact(
        repo_root=tmp_path / "repo",
        commit=commit,
        gateway_venv=tmp_path / "gateway",
        destination=tmp_path / "assembled",
        python_executable=Path(sys.executable),
    )
    before = assembled.artifact_tree_sha256
    manifest_path = assembled.root / "runtime-manifest.json"
    payload = json.loads(manifest_path.read_bytes())
    payload["candidate_commit"] = "f" * 40
    manifest_path.chmod(0o600)
    manifest_path.write_bytes(runtime_v1._canonical_bytes(payload))
    assert runtime_v1._artifact_tree_hash(
        assembled.root,
        excluded=frozenset({"runtime-manifest.sha256"}),
    ) != before


def test_frozen_runtime_materializes_shim_and_matches_gateway_parity(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    commit = _git_fixture(repo)
    artifact = build_source_artifact(
        repo_root=repo,
        commit=commit,
        destination=tmp_path / "artifact",
    )

    gateway_site = (
        tmp_path / "gateway" / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}"
        / "site-packages"
    )
    gateway_site.mkdir(parents=True)
    gateway_python = tmp_path / "gateway" / "bin" / "python"
    gateway_python.parent.mkdir(parents=True, exist_ok=True)
    gateway_python.write_text(
        "#!/bin/sh\n"
        "if [ \"$1\" = \"-c\" ]; then printf '%s\n' "
        + repr(str(gateway_site))
        + "; else exit 1; fi\n",
        encoding="utf-8",
    )
    gateway_python.chmod(0o755)
    (gateway_site / "00-pysqlite3-shim.pth").write_text(
        "import sys, pysqlite3 as _p; sys.modules['sqlite3'] = _p\n",
        encoding="utf-8",
    )
    # The declared gateway venv is editable. These entries must not leak its
    # checkout path into the frozen runtime.
    (gateway_site / "__editable__.hermes_agent.pth").write_text(
        "import __editable___hermes_agent_finder\n",
        encoding="utf-8",
    )
    (gateway_site / "__editable___hermes_agent_finder.py").write_text(
        "MAPPING = {'job_intel': '/mutable/checkout/job_intel'}\n",
        encoding="utf-8",
    )
    pysqlite3 = gateway_site / "pysqlite3"
    pysqlite3.mkdir()
    (pysqlite3 / "__init__.py").write_text(
        "__version__ = '3.53.4'\nsqlite_version = '3.53.4'\n",
        encoding="utf-8",
    )

    runtime = build_frozen_runtime(
        artifact=artifact,
        gateway_venv=tmp_path / "gateway",
        destination=tmp_path / "frozen-runtime",
        python_executable=Path(sys.executable),
    )
    assert not runtime.python_executable.is_symlink()
    contained_stdlib = runtime.root / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}"
    assert (contained_stdlib / "os.py").is_file()
    contained_libraries = {
        path.name for path in (runtime.root / "lib").iterdir() if path.is_file()
    }
    assert any(name.startswith("libc.so") for name in contained_libraries)
    contained_env = {
        **os.environ,
        "PYTHONHOME": str(runtime.root),
        "PYTHONNOUSERSITE": "1",
        "LD_LIBRARY_PATH": str(runtime.root / "lib"),
    }
    probe = subprocess.check_output(
        [
            str(runtime.python_executable),
            "-c",
            "import job_intel,sqlite3,sys; print(job_intel.__file__); print(sys.version_info[:2]); print(sqlite3.__name__); print(sqlite3.sqlite_version)",
        ],
        cwd=runtime.root,
        text=True,
        env=contained_env,
    ).splitlines()
    assert Path(probe[0]).resolve().is_relative_to(runtime.root.resolve())
    stdlib_path = subprocess.check_output(
        [
            str(runtime.python_executable),
            "-c",
            "import sysconfig; print(sysconfig.get_paths()['stdlib'])",
        ],
        cwd=runtime.root,
        text=True,
        env=contained_env,
    ).strip()
    assert Path(stdlib_path).resolve().is_relative_to(runtime.root.resolve())
    ldd_output = subprocess.check_output(
        ["ldd", str(runtime.python_executable)],
        cwd=runtime.root,
        text=True,
        env=contained_env,
    )
    for line in ldd_output.splitlines():
        if "=>" not in line:
            continue
        resolved = Path(line.split("=>", 1)[1].split("(", 1)[0].strip())
        # The ELF interpreter path is absolute in the binary header and cannot be redirected by LD_LIBRARY_PATH.
        if resolved.name.startswith("ld-linux"):
            continue
        assert resolved.resolve().is_relative_to((runtime.root / "lib").resolve())
    assert probe[2:] == ["pysqlite3", "3.53.4"]
    sys_path = json.loads(
        subprocess.check_output(
            [str(runtime.python_executable), "-c", "import json,sys; print(json.dumps(sys.path))"],
            cwd=runtime.root,
            text=True,
        )
    )
    assert not any("__editable__" in item or "mutable/checkout" in item for item in sys_path)
    assert runtime.shim_sha256 == hashlib.sha256(
        (gateway_site / "00-pysqlite3-shim.pth").read_bytes()
    ).hexdigest()
    assert runtime.reproducibility == "frozen_non_editable"


def test_manifest_binding_rejects_reorder_policy_and_prompt_drift_distinctly(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    commit = _git_fixture(repo)
    artifact = build_source_artifact(
        repo_root=repo,
        commit=commit,
        destination=tmp_path / "artifact",
    )
    gateway_site = (
        tmp_path / "gateway" / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}"
        / "site-packages"
    )
    gateway_site.mkdir(parents=True)
    gateway_python = tmp_path / "gateway" / "bin" / "python"
    gateway_python.parent.mkdir(parents=True, exist_ok=True)
    gateway_python.write_text(
        "#!/bin/sh\n"
        "if [ \"$1\" = \"-c\" ]; then printf '%s\n' "
        + repr(str(gateway_site))
        + "; else exit 1; fi\n",
        encoding="utf-8",
    )
    gateway_python.chmod(0o755)
    (gateway_site / "00-pysqlite3-shim.pth").write_text(
        "import sys, pysqlite3 as _p; sys.modules['sqlite3'] = _p\n",
        encoding="utf-8",
    )
    # The declared gateway venv is editable. These entries must not leak its
    # checkout path into the frozen runtime.
    (gateway_site / "__editable__.hermes_agent.pth").write_text(
        "import __editable___hermes_agent_finder\n",
        encoding="utf-8",
    )
    (gateway_site / "__editable___hermes_agent_finder.py").write_text(
        "MAPPING = {'job_intel': '/mutable/checkout/job_intel'}\n",
        encoding="utf-8",
    )
    pysqlite3 = gateway_site / "pysqlite3"
    pysqlite3.mkdir()
    (pysqlite3 / "__init__.py").write_text(
        "__version__ = '3.53.4'\nsqlite_version = '3.53.4'\n",
        encoding="utf-8",
    )
    runtime = build_frozen_runtime(
        artifact=artifact,
        gateway_venv=tmp_path / "gateway",
        destination=tmp_path / "frozen-runtime",
        python_executable=Path(sys.executable),
    )
    authorities = _authority_inputs()
    rows = _rows()
    manifest = build_evidence_manifest(
        run_id="gate-b-evidence-v1-0123456789abcdef",
        created_at=datetime(2026, 8, 21, tzinfo=timezone.utc),
        source_artifact=artifact,
        runtime=runtime,
        rows=rows,
        authorities=authorities,
    )
    verify_manifest_binding(
        manifest,
        source_artifact=artifact,
        runtime=runtime,
        rows=rows,
        authorities=authorities,
    )

    with pytest.raises(ArtifactBuildError, match="corpus_order_mismatch"):
        verify_manifest_binding(
            manifest,
            source_artifact=artifact,
            runtime=runtime,
            rows=tuple(reversed(rows)),
            authorities=authorities,
        )
    with pytest.raises(ArtifactBuildError, match="policy_hash_mismatch"):
        verify_manifest_binding(
            manifest,
            source_artifact=artifact,
            runtime=runtime,
            rows=rows,
            authorities=AuthorityInputs(
                **{
                    **authorities.model_dump(),
                    "policy_bytes": b"policy:changed",
                }
            ),
        )
    with pytest.raises(ArtifactBuildError, match="prompt_hash_mismatch"):
        verify_manifest_binding(
            manifest,
            source_artifact=artifact,
            runtime=runtime,
            rows=rows,
            authorities=AuthorityInputs(
                **{
                    **authorities.model_dump(),
                    "prompt_bytes": b"prompt:changed",
                }
            ),
        )
