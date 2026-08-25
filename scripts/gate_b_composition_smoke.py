#!/usr/bin/env python3
"""Measure the Gate B deployed composition root without changing production code.

The harness builds a fresh content-addressed artifact from a temporary commit,
installs it into a temporary artifact parent, prepares the 48-row fake-provider
fixture, and invokes the published wrapper. It stops at the first blocker rather
than injecting missing production inputs or continuing after a failed gate.
"""
from __future__ import annotations

from datetime import datetime
from contextlib import contextmanager
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

# Measured on hermes-agent after 3.1: artifact build is roughly two minutes;
# keep a generous bound while still failing a genuinely stuck build.
BUILD_TIMEOUT_SECONDS = 300
TARGET_TIMEOUT_SECONDS = 30
EVALUATION_TIMEOUT_SECONDS = 60


class SmokeFailure(RuntimeError):
    """Named, operator-facing failure from the composition smoke harness."""


def canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def run(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
    timeout_seconds: float | None = None,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            cwd=cwd,
            env=env,
            text=True,
            capture_output=True,
            check=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        phase = command[command.index("-m") + 1] if "-m" in command else command[0]
        raise SmokeFailure(
            f"smoke_phase_timeout:{phase}:>{timeout_seconds:g}s"
        ) from exc


def _named_target_failure(stderr: str) -> str:
    # Keep this allowlist to exact raised error tokens. Unknown failures remain
    # the honest ``smoke_target_failed`` fallback instead of guessing broadly.
    for prerequisite in (
        "spend_record_missing",
        "decision_policy_authority_mismatch",
        "company_evidence_root_required",
        "corpus_manifest_sha256_mismatch",
    ):
        if prerequisite in stderr:
            return f"smoke_precondition_failed:{prerequisite}"
    return "smoke_target_failed"


def _stderr_tail(stderr: str) -> str:
    lines = [line.strip() for line in stderr.splitlines() if line.strip()]
    for line in reversed(lines):
        if "Error:" in line or "error" in line.lower():
            return line
    return lines[-1] if lines else "no target diagnostics"


@contextmanager
def _smoke_tempdir():
    path = Path(tempfile.mkdtemp(prefix="gate-b-composition-smoke-"))
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)
        if path.exists():
            sys.stdout.flush()
            print(
                "SMOKE_CLEANUP_RETAINED: "
                f"{path} (root-owned files from the systemd target remain)",
                file=sys.stderr,
            )


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
    wrapper_source = REPO / "scripts/job_intel_gate_b_supervised.sh"
    wrapper_target = root / "scripts/job_intel_gate_b_supervised.sh"
    wrapper_target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(wrapper_source, wrapper_target)
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
        "shared_library_provenance": runtime["shared_library_provenance"],
    }
    identity = dict(manifest)
    identity.pop("manifest_sha256")
    identity.pop("created_at")
    manifest["manifest_sha256"] = sha256(canonical(identity))
    encoded = canonical(manifest)
    manifest_path.write_bytes(encoded)
    return sha256(encoded)


def main() -> int:
    started = time.perf_counter()
    with _smoke_tempdir() as root:
        source_root = root / "source"
        commit = make_source_commit(source_root)
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
            timeout_seconds=BUILD_TIMEOUT_SECONDS,
        )
        build_seconds = time.perf_counter() - build_started
        build_result = json.loads(build.stdout)
        artifact_hash = build_result["artifact_tree_sha256"]
        install_parent = root / "installed-artifacts"
        install_root = install_parent / artifact_hash
        install_started = time.perf_counter()
        shutil.copytree(stage, install_root, symlinks=True)
        install_seconds = time.perf_counter() - install_started
        fixture_root = root / "fixture"
        fixture_artifact = root / ("f" * 64)
        sys.path.insert(0, str(REPO))
        from tests.product_search import gate_b_cli_smoke_fixture as fixture

        manifest_path, config_path, manifest_sha = fixture.prepare(
            root=fixture_root,
            artifact_root=fixture_artifact,
            repo_root=REPO,
            runtime_manifest_path=install_root / "runtime-manifest.json",
            corpus_authority_path=(
                REPO
                / "tests/product_search/fixtures/gate-b-corpus-authority.json"
            ),
        )
        manifest_payload = json.loads(manifest_path.read_bytes())
        spend_record_path = (
            fixture_root
            / "spend-records"
            / str(manifest_payload["manifest_sha256"])
            / "committed-budget.json"
        )
        # Fault injection for the 3.2 acceptance experiment; never normal mode.
        if os.environ.get("GATE_B_SMOKE_REMOVE_SPEND_RECORD") == "1":
            spend_record_path.unlink(missing_ok=True)
        if not spend_record_path.is_file():
            raise SmokeFailure(
                "smoke_precondition_failed:spend_record_missing; "
                f"artifact_build_seconds={build_seconds:.3f}; "
                f"artifact_install_seconds={install_seconds:.3f}"
            )
        supervised_wrapper = install_root / "runtime/scripts/job_intel_gate_b_supervised.sh"
        supervised_wrapper_copy = install_root / "runtime/scripts/.composition-smoke-supervised.sh"
        supervised_wrapper_copy.write_text(
            supervised_wrapper.read_text(encoding="utf-8").replace(
                "/var/lib/job-intel-gate-b-artifacts", str(install_parent)
            ).replace(
                "/var/lib/job-intel-gate-b-spend",
                str(fixture_root / "spend-records"),
            ),
            encoding="utf-8",
        )
        supervised_wrapper_copy.chmod(0o755)
        state = root / "state"
        state.mkdir()
        dispatch_probe_path = state / "dispatch-probe.json"
        decision_request_factory = (
            "gate_b_cli_smoke_fixture:broken_decision_request_factory"
            if os.environ.get("GATE_B_SMOKE_BREAK_DECISION_FACTORY") == "1"
            else "job_intel.product_search.gate_b_evidence_runner_v1:build_decision_request_from_context_v2"
        )
        target_args = [
            "--manifest",
            str(manifest_path),
            "--manifest-sha256",
            manifest_sha,
            "--output",
            str(state),
        ]
        init_attempt = None
        init_seconds = 0.0
        target_attempt = None
        target_seconds = 0.0
        evaluation_attempt = None
        evaluation_seconds = 0.0
        gate_decision = None
        probe_path = state / "isolation-probe.json"
        target_env = {
            **os.environ,
            "GATE_B_SMOKE_ISOLATION_PROBE": str(probe_path),
            "GATE_B_SMOKE_DISPATCH_LOG": str(dispatch_probe_path),
            "GATE_B_SMOKE_PROBE_CAP": "1",
        }
        target_started = time.perf_counter()
        try:
            target_attempt = subprocess.run(
                    [
                        str(supervised_wrapper_copy),
                        "run-supervised",
                        "--corpus",
                        str(fixture_root / "corpus-rows.json"),
                        *target_args,
                        "--reviewed-allowlist",
                        str(fixture_root / "reviewed-allowlist.json"),
                        "--decision-policy",
                        str(fixture_artifact / "authority/decision_contract.v2.yaml"),
                        "--authority-root",
                        str(fixture_artifact / "authority"),
                        "--company-evidence-root",
                        str(fixture_root / "company-evidence"),
                        "--provider-factory",
                        "gate_b_cli_smoke_fixture:provider_factory",
                        "--decision-request-factory",
                        decision_request_factory,
                    ],
                    cwd=install_root / "runtime",
                    env=target_env,
                    text=True,
                    capture_output=True,
                    timeout=TARGET_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired as exc:
            raise SmokeFailure(
                "smoke_phase_timeout:supervised_collection:"
                f">{TARGET_TIMEOUT_SECONDS}s; "
                f"artifact_build_seconds={build_seconds:.3f}; "
                f"artifact_install_seconds={install_seconds:.3f}"
            ) from exc
        target_seconds = time.perf_counter() - target_started
        if target_attempt is None or target_attempt.returncode != 0:
            detail = "no target attempt"
            if target_attempt is not None:
                detail = target_attempt.stderr.strip()
            raise SmokeFailure(
                f"{_named_target_failure(detail)}; "
                f"artifact_build_seconds={build_seconds:.3f}; "
                f"artifact_install_seconds={install_seconds:.3f}; "
                f"target_seconds={target_seconds:.3f}; "
                f"detail={_stderr_tail(detail)}"
            )
        if not dispatch_probe_path.is_file():
            raise RuntimeError("provider did not publish dispatch probe")
        dispatch_probe = json.loads(dispatch_probe_path.read_bytes())
        manifest_row_count = 48
        if dispatch_probe.get("dispatch_count") != manifest_row_count:
            raise RuntimeError("composition dispatch count exceeded the 48-row cap")
        if len(set(dispatch_probe.get("dispatch_inputs", []))) != manifest_row_count:
            raise RuntimeError("composition dispatched a row more than once")
        if dispatch_probe.get("extra_dispatch_refused") != "call_cap_exhausted":
            raise RuntimeError("composition did not refuse the 49th dispatch")
        from job_intel.product_search.gate_b_evidence_runner_v1 import (
            AdjudicationSet,
            AdjudicationVerdict,
            DecisionEvidenceStore,
            EvidenceManifest,
        )

        manifest = EvidenceManifest.model_validate(json.loads(manifest_path.read_bytes()))
        decision_store = DecisionEvidenceStore(state / "decisions")
        factory_call_count = 0
        verdicts_list: list[AdjudicationVerdict] = []
        for ordinal in range(manifest.row_count):
            row_ref = manifest.row_ref(ordinal)
            try:
                decision_ref = decision_store.find_for_manifest_ref(row_ref)
            except ValueError as exc:
                raise SmokeFailure(
                    "production_decision_request_factory_not_called"
                ) from exc
            factory_call_count += 1
            verdicts_list.append(
                AdjudicationVerdict(
                    manifest_ref=row_ref,
                    decision_sha256=decision_ref.decision_sha256,
                    correct=True,
                )
            )
        verdicts = tuple(verdicts_list)
        adjudication = AdjudicationSet.from_verdicts(verdicts)
        adjudication_path = state / "adjudication.json"
        adjudication_path.write_bytes(canonical(adjudication.model_dump(mode="json")))
        evaluation_started = time.perf_counter()
        try:
            evaluation_attempt = subprocess.run(
                [
                    str(supervised_wrapper_copy),
                    "evaluate-run",
                    "--manifest",
                    str(manifest_path),
                    "--manifest-sha256",
                    manifest_sha,
                    "--measurement-report",
                    str(state / "measurement-report.json"),
                    "--measurement-report-sha256",
                    sha256((state / "measurement-report.json").read_bytes()),
                    "--adjudication",
                    str(adjudication_path),
                    "--adjudication-sha256",
                    adjudication.adjudication_sha256,
                    "--gate-policy",
                    str(install_root / "runtime/config/product_search/gate_b_benchmark.v3.yaml"),
                    "--output",
                    str(state),
                ],
                cwd=install_root / "runtime",
                env=target_env,
                text=True,
                capture_output=True,
                timeout=EVALUATION_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired as exc:
            raise SmokeFailure(
                "smoke_phase_timeout:evaluate_run:"
                f">{EVALUATION_TIMEOUT_SECONDS}s"
            ) from exc
        evaluation_seconds = time.perf_counter() - evaluation_started
        if evaluation_attempt.returncode != 0:
            raise SmokeFailure(
                f"smoke_evaluation_failed: {evaluation_attempt.stderr.strip()}"
            )
        decision_path = state / "gate-decision.json"
        if not decision_path.is_file():
            raise RuntimeError("gate evaluator did not publish gate-decision.json")
        gate_decision = json.loads(decision_path.read_bytes())
        if not {
            "measurement_status",
            "decision",
            "violated_rules",
        } <= gate_decision.keys():
            raise RuntimeError("gate decision publication is incomplete")
        measurement_report = json.loads(
            (state / "measurement-report.json").read_bytes()
        )
        if measurement_report["terminal_unknown_count"] != 5:
            raise RuntimeError(
                "mixed-outcome fixture did not exercise five terminal-unknown rows"
            )
        if not 0 < measurement_report["deliverable_count"] < 43:
            raise RuntimeError(
                "mixed-outcome fixture did not publish a bounded deliverable count"
            )
        terminal_unknown_recordings = 0
        for recording_path in (state / "recordings").glob("*.json"):
            recording = json.loads(recording_path.read_bytes())
            if recording.get("outcome") == "terminal_unknown":
                terminal_unknown_recordings += 1
                if recording.get("response_b64") != "":
                    raise RuntimeError(
                        "terminal-unknown recording did not preserve an empty response"
                    )
        if terminal_unknown_recordings != 5:
            raise RuntimeError(
                "terminal-unknown recordings were not durably published"
            )
        isolation_probe_error = None
        if target_attempt is not None:
            if not probe_path.is_file():
                isolation_probe = {}
                isolation_probe_error = "provider did not publish isolation probe"
                raise RuntimeError(isolation_probe_error)
            isolation_probe = json.loads(probe_path.read_bytes())
            if any(item.get("reachable") for item in isolation_probe.values()):
                isolation_probe_error = "probe observed a reachable protected path"
                raise RuntimeError(isolation_probe_error)
        else:
            isolation_probe = {}
        first_stop = "gate decision publication"
        report = {
            "head": run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO).stdout.strip(),
            "artifact_build_seconds": round(build_seconds, 3),
            "artifact_install_seconds": round(install_seconds, 3),
            "init_attempt_seconds": round(init_seconds, 3),
            "target_attempt_seconds": round(target_seconds, 3),
            "evaluation_attempt_seconds": round(evaluation_seconds, 3),
            "harness_lines": len(Path(__file__).read_text(encoding="utf-8").splitlines()),
            "artifact_tree_sha256": artifact_hash,
            "manifest_sha256": manifest_sha,
            "provider_fixture": str(config_path),
            "init_returncode": None,
            "init_stdout": "",
            "init_stderr": "",
            "target_returncode": None if target_attempt is None else target_attempt.returncode,
            "target_stdout": "" if target_attempt is None else target_attempt.stdout,
            "target_stderr": "" if target_attempt is None else target_attempt.stderr,
            "evaluation_returncode": evaluation_attempt.returncode,
            "evaluation_stdout": evaluation_attempt.stdout,
            "evaluation_stderr": evaluation_attempt.stderr,
            "gate_decision": gate_decision,
            "measurement_report": measurement_report,
            "terminal_unknown_recordings": terminal_unknown_recordings,
            "isolation_observed": bool(isolation_probe) and not any(
                item.get("reachable") for item in isolation_probe.values()
            ),
            "isolation_probe": isolation_probe,
            "isolation_probe_error": isolation_probe_error,
            "dispatch_probe": dispatch_probe,
            "decision_request_factory_calls": factory_call_count,
            "first_stop": first_stop,
            "durable_artifacts_at_stop": sorted(
                path.relative_to(state).as_posix() for path in state.rglob("*")
            ),
        }
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SmokeFailure as exc:
        print(f"SMOKE_FAILURE: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
