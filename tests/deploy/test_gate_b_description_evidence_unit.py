from __future__ import annotations

from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[2]
SERVICE = ROOT / "deploy/systemd/experiments/job-intel-gate-b-benchmark@.service"
RUNNER = ROOT / "scripts/job_intel_gate_b_benchmark.sh"
INSTALLER = ROOT / "scripts/install_job_intel_gate_b_benchmark_unit.sh"


def test_simplified_unit_has_state_directory_and_independent_db_namespace_proof() -> None:
    completed = subprocess.run(
        ["systemd-analyze", "verify", str(SERVICE)],
        text=True,
        capture_output=True,
    )
    assert completed.returncode == 0, completed.stderr
    unit = SERVICE.read_text(encoding="utf-8")
    assert "Type=oneshot" in unit
    assert "User=hermes" in unit
    assert "Group=hermes" in unit
    assert "Restart=no" in unit
    assert "StateDirectory=job-intel-gate-b-description-evidence" in unit
    assert "StateDirectoryMode=0750" in unit
    assert "ExecStart=/usr/bin/env /var/lib/job-intel-gate-b-artifacts/%i/runtime/scripts/job_intel_gate_b_benchmark.sh run-description-evidence" in unit
    assert "ReadOnlyPaths=/var/lib/job-intel-gate-b-artifacts/%i" in unit
    assert "gate-b-at-most-once" not in unit
    assert "ExecStartPre=/usr/bin/test -d ${STATE_DIRECTORY}" in unit
    assert "ConditionPathIsDirectory=" not in unit
    assert "ExecStartPre=+" not in unit
    assert "ReadWritePaths=" not in unit
    assert "launch.pending" not in unit
    assert "launch.consumed" not in unit
    assert "receipt" not in unit.lower()
    assert "EnvironmentFile=" not in unit
    assert "JOB_INTEL_LLM_LIVE_APPROVED" not in unit
    for protected in (
        "/home/hermes/.hermes/job_intel/job_intel.sqlite3",
        "/home/hermes/.hermes/job_intel/job_intel.sqlite3-wal",
        "/home/hermes/.hermes/job_intel/job_intel.sqlite3-shm",
        "/home/hermes/.hermes/state.db",
        "/home/hermes/.hermes/job_intel/outbox",
    ):
        assert f"InaccessiblePaths={protected}" in unit


def test_runner_and_installer_have_no_retired_receipt_or_root_preparation_path() -> None:
    runner = RUNNER.read_text(encoding="utf-8")
    assert "run-description-evidence" in runner
    assert "run-collection" in runner
    assert "STATE_DIRECTORY" in runner
    assert "systemd StateDirectory is unavailable" in runner
    assert "consume-launch-receipt" not in runner
    assert "launch.pending" not in runner
    assert "receipt" not in runner.lower()
    assert "prepare-output-root" not in runner

    installer = INSTALLER.read_text(encoding="utf-8")
    assert "prepare-output-root" not in installer
    assert "receipt" not in installer.lower()
    assert "launch.pending" not in installer
    assert "systemctl start" not in installer
    assert "/usr/bin/install" in installer
    assert "/usr/bin/systemctl daemon-reload" in installer
    assert "gate-b-at-most-once" not in installer
    assert "verify_artifact_hash" in installer
    assert "artifact_tree_sha256" in installer
    assert "ARTIFACT_TREE_SHA256" in installer
    assert 'destination="$artifact_parent/$artifact_tree_sha256"' in installer
    assert "runtime-manifest.json" in installer
    assert "existing artifact path" in installer
    assert "mv -T -n" in installer
    assert "/var/lib/job-intel-gate-b-artifacts" in installer


def test_contract_verification_is_static_and_does_not_start_the_unit() -> None:
    unit = SERVICE.read_text(encoding="utf-8")
    assert "ExecStart=/usr/bin/env" in unit
    exec_start = next(
        line for line in unit.splitlines() if line.startswith("ExecStart=")
    )
    assert "gate-b-at-most-once" not in exec_start
    assert exec_start.startswith(
        "ExecStart=/usr/bin/env /var/lib/job-intel-gate-b-artifacts/%i/"
    )
    # This task proves the text and systemd parser only. No unit start, provider
    # construction, credential lookup, network call, or benchmark execution is
    # performed by this contract test.
