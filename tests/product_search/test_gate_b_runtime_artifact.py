from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

from job_intel.product_search.gate_b_evidence_runner_v1 import EvidenceManifestRow
from job_intel.product_search.gate_b_runtime_v1 import (
    ArtifactBuildError,
    AuthorityInputs,
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
