#!/usr/bin/env python3
"""Measure the Gate B deployed composition root without changing production code.

The harness builds a fresh content-addressed artifact from a temporary commit,
installs it into a temporary artifact parent, prepares the 48-row fake-provider
fixture, and invokes the published wrapper. It stops at the first blocker rather
than injecting missing production inputs or continuing after a failed gate.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time

REPO = Path(__file__).resolve().parents[1]
GATEWAY_VENV = Path(
    os.environ.get(
        "GATEWAY_VENV", "/home/hermes/.hermes/hermes-agent/venv"
    )
)
GATEWAY_PYTHON = GATEWAY_VENV / "bin" / "python"


def canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def run(command: list[str], *, cwd: Path, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd, env=env, text=True, capture_output=True, check=True)


def make_source_commit(root: Path) -> str:
    root.mkdir()
    archive = root.parent / "source.tar"
    with archive.open("wb") as stream:
        subprocess.run(["git", "archive", "HEAD"], cwd=REPO, stdout=stream, check=True)
    with tarfile.open(archive, "r:") as tar:
        tar.extractall(root, filter="data")
    working_diff = subprocess.run(
        ["git", "diff", "--binary", "HEAD"],
        cwd=REPO,
        check=True,
        capture_output=True,
    ).stdout
    if working_diff:
        subprocess.run(
            ["git", "apply", "--whitespace=nowarn"],
            cwd=root,
            input=working_diff,
            check=True,
        )
    shutil.copy2(
        REPO / "tests/product_search/gate_b_cli_smoke_fixture.py",
        root / "gate_b_cli_smoke_fixture.py",
    )
    run(["git", "init", "-q"], cwd=root)
    run(["git", "config", "user.email", "gate-b-smoke@example.invalid"], cwd=root)
    run(["git", "config", "user.name", "Gate B smoke harness"], cwd=root)
    run(["git", "add", "-A"], cwd=root)
    run(["git", "commit", "-qm", "composition smoke fixture"], cwd=root)
    return run(["git", "rev-parse", "HEAD"], cwd=root).stdout.strip()


def bind_manifest_runtime(manifest_path: Path, runtime_manifest_path: Path) -> str:
    manifest = json.loads(manifest_path.read_bytes())
    runtime = json.loads(runtime_manifest_path.read_bytes())
    manifest["runtime"] = {
        "artifact_sha256": runtime["artifact_sha256"],
        "artifact_tree_sha256": runtime["artifact_tree_sha256"],
        "shim_sha256": runtime["shim_sha256"],
        "interpreter_sha256": runtime["python_executable_sha256"],
        "stdlib_inventory_sha256": runtime["stdlib_tree_sha256"],
        "installed_distributions_sha256": runtime["installed_distributions_sha256"],
        "installed_files_sha256": runtime["installed_files_sha256"],
        "sys_path_sha256": runtime["sys_path_sha256"],
        "native_extensions_sha256": runtime["native_extensions_sha256"],
        "shared_libraries_sha256": runtime["shared_libraries_sha256"],
        "shared_library_provenance": {},
    }
    identity = dict(manifest)
    identity.pop("manifest_sha256")
    identity.pop("created_at")
    manifest["manifest_sha256"] = sha256(canonical(identity))
    manifest_path.write_bytes(canonical(manifest))
    return manifest["manifest_sha256"]


def main() -> int:
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="gate-b-composition-smoke-") as temporary:
        root = Path(temporary)
        source_root = root / "source"
        commit = make_source_commit(source_root)
        fixture_root = root / "fixture"
        fixture_artifact = root / ("f" * 64)
        sys.path.insert(0, str(REPO))
        from tests.product_search import gate_b_cli_smoke_fixture as fixture

        manifest_path, config_path, _ = fixture.prepare(
            root=fixture_root,
            artifact_root=fixture_artifact,
            repo_root=REPO,
        )
        stage = root / "built-artifact"
        build_started = time.perf_counter()
        build = run(
            [
                str(GATEWAY_PYTHON),
                "-m",
                "job_intel.product_search.gate_b_runtime_v1",
                "build-artifact",
                str(source_root),
                commit,
                str(GATEWAY_VENV),
                str(stage),
                str(GATEWAY_PYTHON),
            ],
            cwd=REPO,
            env={**os.environ, "PYTHONPATH": str(REPO)},
        )
        build_seconds = time.perf_counter() - build_started
        build_result = json.loads(build.stdout)
        artifact_hash = build_result["artifact_tree_sha256"]
        install_parent = root / "installed-artifacts"
        install_root = install_parent / artifact_hash
        install_started = time.perf_counter()
        shutil.copytree(stage, install_root, symlinks=True)
        install_seconds = time.perf_counter() - install_started
        manifest_sha = bind_manifest_runtime(
            manifest_path, install_root / "runtime-manifest.json"
        )
        wrapper = install_root / "runtime/scripts/job_intel_gate_b_benchmark.sh"
        wrapper_copy = install_root / "runtime/scripts/.composition-smoke.sh"
        wrapper_copy.write_text(
            wrapper.read_text(encoding="utf-8").replace(
                "/var/lib/job-intel-gate-b-artifacts", str(install_parent)
            ),
            encoding="utf-8",
        )
        wrapper_copy.chmod(0o755)
        state = root / "state"
        state.mkdir()
        artifact_python = install_root / "python-runtime/venv/bin/python"
        target_env = os.environ.copy()
        target_env["PYTHONHOME"] = str(install_root / "python-runtime/venv")
        target_env["LD_LIBRARY_PATH"] = str(install_root / "python-runtime/venv/lib")
        target_env["PYTHONNOUSERSITE"] = "1"
        target_env["PYTHONPATH"] = str(install_root / "runtime")
        target_started = time.perf_counter()
        target_attempt = subprocess.run(
            [
                str(artifact_python),
                "-m",
                "job_intel.product_search.gate_b_evidence_runner_v1",
                "run-supervised",
                "--corpus",
                str(fixture_root / "corpus-rows.json"),
                "--manifest",
                str(manifest_path),
                "--manifest-sha256",
                manifest_sha,
                "--output",
                str(state),
                "--provider-factory",
                "gate_b_cli_smoke_fixture:provider_factory",
                "--decision-request-factory",
                "gate_b_cli_smoke_fixture:decision_request_factory",
            ],
            cwd=install_root / "runtime",
            env=target_env,
            text=True,
            capture_output=True,
        )
        target_seconds = time.perf_counter() - target_started
        target_expected = "invariant supervised_collection_spine_v1 is unsatisfied"
        if target_attempt.returncode == 0 or target_expected not in target_attempt.stderr:
            raise RuntimeError(
                "target supervised entrypoint did not fail closed on the named spine invariant"
            )

        old_env = os.environ.copy()
        old_env.pop("GATE_B_COLLECTION_CONFIG", None)
        old_env.pop("GATE_B_EVIDENCE_MANIFEST", None)
        old_env.pop("GATE_B_MANIFEST_SHA256", None)
        old_env["STATE_DIRECTORY"] = str(state)
        old_started = time.perf_counter()
        old_attempt = subprocess.run(
            [str(wrapper_copy), "run-description-evidence"],
            cwd=install_root / "runtime",
            env=old_env,
            text=True,
            capture_output=True,
        )
        old_seconds = time.perf_counter() - old_started
        old_expected = "requires config, manifest and STATE_DIRECTORY"
        if old_attempt.returncode != 2 or old_expected not in old_attempt.stderr:
            raise RuntimeError(
                "old unit wrapper was not inert: expected exit 2 input validation"
            )
        report = {
            "head": run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO).stdout.strip(),
            "artifact_build_seconds": round(build_seconds, 3),
            "artifact_install_seconds": round(install_seconds, 3),
            "target_attempt_seconds": round(target_seconds, 3),
            "old_unit_attempt_seconds": round(old_seconds, 3),
            "harness_lines": len(Path(__file__).read_text(encoding="utf-8").splitlines()),
            "artifact_tree_sha256": artifact_hash,
            "manifest_sha256": manifest_sha,
            "provider_fixture": str(config_path),
            "target_returncode": target_attempt.returncode,
            "target_stdout": target_attempt.stdout,
            "target_stderr": target_attempt.stderr,
            "old_unit_returncode": old_attempt.returncode,
            "old_unit_stdout": old_attempt.stdout,
            "old_unit_stderr": old_attempt.stderr,
            "first_stop": "target supervised entrypoint named-invariant guard",
            "durable_artifacts_at_stop": sorted(
                path.relative_to(state).as_posix() for path in state.rglob("*")
            ),
        }
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
