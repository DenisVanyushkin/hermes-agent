from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess

import pytest
from pydantic import ValidationError

import job_intel.product_search.gate_b_benchmark_v3 as gate_b_v3


ROOT = Path(__file__).resolve().parents[2]
EXPORTER = ROOT / "scripts/export_job_intel_gate_b_benchmark.sh"


def _runtime_manifest_payload() -> dict[str, object]:
    return {
        "schema_version": "3.0.0",
        "runtime_kind": "gate_b_at_most_once",
        "candidate_commit": "a" * 40,
        "python_version": "3.12.13",
        "runtime_tree_sha256": "1" * 64,
        "python_executable_sha256": "2" * 64,
        "stdlib_tree_sha256": "3" * 64,
        "dependency_lock_sha256": "4" * 64,
        "installed_distributions_sha256": "5" * 64,
        "sys_path_sha256": "6" * 64,
        "editable_installs": [],
    }


def test_runtime_manifest_requires_python_3_12_13_and_no_editable_installs() -> None:
    manifest_type = getattr(gate_b_v3, "GateBRuntimeManifestV3")
    payload = _runtime_manifest_payload()

    manifest = manifest_type.model_validate(payload)

    assert manifest.python_version == "3.12.13"
    assert len(manifest.canonical_sha256) == 64
    payload["python_version"] = "3.12.14"
    with pytest.raises(ValidationError):
        manifest_type.model_validate(payload)
    payload = _runtime_manifest_payload()
    payload["editable_installs"] = ["/mutable/checkout"]
    with pytest.raises(ValidationError):
        manifest_type.model_validate(payload)


def test_runtime_export_uses_only_reviewed_commit_and_never_runs_benchmark(
    tmp_path: Path,
) -> None:
    assert EXPORTER.exists()
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.email", "test@example.invalid"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.name", "Gate B test"],
        check=True,
    )
    (repo / "tracked.txt").write_text("reviewed\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "tracked.txt"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-qm", "test fixture"], check=True
    )
    commit = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()
    (repo / "tracked.txt").write_text("dirty checkout\n", encoding="utf-8")
    (repo / "untracked.txt").write_text("must not export\n", encoding="utf-8")

    toolchain = tmp_path / "toolchain"
    python_prefix = toolchain / "cpython"
    fake_python = python_prefix / "bin/python3.12"
    fake_bin = toolchain / "bin"
    fake_bin.mkdir(parents=True)
    fake_python.parent.mkdir(parents=True)
    python_script = f"""#!/usr/bin/env bash
set -euo pipefail
if [[ "${{1:-}}" == "-c" ]]; then
  case "${{2:-}}" in
    *sys.prefix*) printf '%s\\n' {str(python_prefix)!r} ;;
    *sys.version_info*) printf '3.12.13\\n' ;;
    *) exit 65 ;;
  esac
  exit 0
fi
if [[ "${{1:-}}" == "-m" && "${{2:-}}" == "job_intel.product_search.gate_b_benchmark_v3" ]]; then
  [[ "${{PYTHONNOUSERSITE:-}}" == "1" ]]
  [[ "${{PYTHONDONTWRITEBYTECODE:-}}" == "1" ]]
  root="${{4}}"
  commit_arg="${{5}}"
  mkdir -p "$root/runtime-identity"
  printf '{{"candidate_commit":"%s"}}' "$commit_arg" > "$root/runtime-manifest.json"
  printf 'manifest-created\\n' > "$root/export-observation.txt"
  exit 0
fi
exit 66
"""
    fake_python.write_text(python_script, encoding="utf-8")
    fake_python.chmod(0o755)
    fake_uv = fake_bin / "uv"
    fake_uv.write_text(
        f"""#!/usr/bin/env bash
set -euo pipefail
case "$1 $2" in
  "venv "*)
    destination="$2"
    mkdir -p "$destination/bin"
    cp {str(fake_python)!r} "$destination/bin/python"
    ;;
  "sync --project")
    ;;
  "pip freeze")
    printf 'pydantic==2.11.7\\n'
    ;;
  *)
    if [[ "$1" == "sync" ]]; then exit 0; fi
    exit 67
    ;;
esac
""",
        encoding="utf-8",
    )
    fake_uv.chmod(0o755)
    destination = tmp_path / "export"
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"

    result = subprocess.run(
        [
            "bash",
            str(EXPORTER),
            str(repo),
            commit,
            str(destination),
            str(fake_python),
        ],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    assert (destination / "runtime/tracked.txt").read_text() == "reviewed\n"
    assert not (destination / "runtime/untracked.txt").exists()
    assert not os.access(destination / "runtime/tracked.txt", os.W_OK)
    assert (destination / "runtime-manifest.json").exists()
    assert (destination / "export-observation.txt").read_text() == "manifest-created\n"
    forbidden = tuple(destination.rglob("*.service")) + tuple(
        destination.rglob("launch.pending.json")
    )
    assert forbidden == ()
    assert hashlib.sha256(
        (destination / "runtime-manifest.json").read_bytes()
    ).hexdigest() == (destination / "runtime-manifest.sha256").read_text().strip()
