from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
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
        source_authority_bytes={"gate_a": b"gate-a:v1"},
    )


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


def test_assembled_artifact_hash_covers_runtime_and_uses_legacy_manifest_shape(
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
        lambda _: b"pydantic==2.11.7\n",
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
    assert runtime_v1._artifact_tree_hash(
        assembled.root,
        excluded=frozenset({"runtime-manifest.json", "runtime-manifest.sha256"}),
    ) == assembled.artifact_tree_sha256
    tampered = assembled.root / "python-runtime/venv/bin/python"
    tampered.chmod(0o600)
    tampered.write_bytes(b"tampered")
    assert runtime_v1._artifact_tree_hash(
        assembled.root,
        excluded=frozenset({"runtime-manifest.json", "runtime-manifest.sha256"}),
    ) != assembled.artifact_tree_sha256


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
    probe = subprocess.check_output(
        [
            str(runtime.python_executable),
            "-c",
            "import job_intel,sqlite3,sys; print(job_intel.__file__); print(sys.version_info[:2]); print(sqlite3.__name__); print(sqlite3.sqlite_version)",
        ],
        cwd=runtime.root,
        text=True,
    ).splitlines()
    assert str(runtime.root / "lib") in probe[0]
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
