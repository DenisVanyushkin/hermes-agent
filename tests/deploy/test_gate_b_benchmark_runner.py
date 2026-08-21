from __future__ import annotations

from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
EXPORTER = ROOT / "scripts/export_job_intel_gate_b_benchmark.sh"
RUNNER = ROOT / "scripts/job_intel_gate_b_benchmark.sh"
INSTALLER = ROOT / "scripts/install_job_intel_gate_b_benchmark_unit.sh"
SERVICE = ROOT / "deploy/systemd/experiments/job-intel-gate-b-benchmark@.service"


def test_runtime_export_wrapper_uses_neutral_runtime_builder_and_rejects_dirty_source(
    tmp_path: Path,
) -> None:
    assert EXPORTER.exists()
    script = EXPORTER.read_text(encoding="utf-8")
    assert "gate_b_runtime_v1" in script
    assert "gate_b_benchmark_v3" not in script

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
    result = subprocess.run(
        [
            "bash",
            str(EXPORTER),
            str(repo),
            commit,
            str(tmp_path / "export"),
            sys.executable,
        ],
        text=True,
        capture_output=True,
    )
    assert result.returncode != 0
    assert "source_tree_dirty" in result.stderr


def test_one_shot_unit_has_no_restart_or_slack_authority() -> None:
    assert RUNNER.exists()
    assert SERVICE.exists()
    completed = subprocess.run(
        ["systemd-analyze", "verify", str(SERVICE)],
        text=True,
        capture_output=True,
    )
    assert completed.returncode == 0, completed.stderr
    unit = SERVICE.read_text(encoding="utf-8")
    assert "Type=oneshot" in unit
    assert "User=hermes" in unit
    assert "Restart=no" in unit
    assert "ExecStartPre=+" not in unit
    assert "StateDirectory=job-intel-gate-b-description-evidence" in unit
    assert "ExecStartPre=/usr/bin/test -d ${STATE_DIRECTORY}" in unit
    assert "ReadWritePaths=" not in unit
    assert "gate-b-at-most-once" not in unit
    assert "/var/lib/job-intel-gate-b-artifacts/%i/" in unit
    exec_start = next(
        line for line in unit.splitlines() if line.startswith("ExecStart=")
    )
    assert "gate-b-at-most-once" not in exec_start
    assert exec_start.startswith(
        "ExecStart=/usr/bin/env /var/lib/job-intel-gate-b-artifacts/%i/"
    )
    assert "launch.pending.json" not in unit
    assert "SLACK" not in unit.upper()
    assert "job_intel.sqlite3" in unit
    syntax = subprocess.run(
        ["bash", "-n", str(RUNNER)],
        text=True,
        capture_output=True,
    )
    assert syntax.returncode == 0, syntax.stderr


def test_state_directory_replaces_root_preflight_namespace_setup() -> None:
    unit = SERVICE.read_text(encoding="utf-8")
    assert "StateDirectory=job-intel-gate-b-description-evidence" in unit
    assert "ReadWritePaths=" not in unit
    runner = RUNNER.read_text(encoding="utf-8")
    assert "prepare-output-root" not in runner
    assert "run-description-evidence" in runner
    assert INSTALLER.exists()
    installer = INSTALLER.read_text(encoding="utf-8")
    assert "prepare-output-root" not in installer
    assert "/usr/bin/install" in installer
    assert "/usr/bin/systemctl daemon-reload" in installer
    assert "systemctl start" not in installer
    assert "launch.pending.json" not in installer


def test_runner_rejects_extra_arguments_before_python(tmp_path: Path) -> None:
    artifact_parent = tmp_path / "artifacts"
    artifact_root = artifact_parent / ("a" * 64)
    runtime_source = artifact_root / "runtime"
    fake_python = artifact_root / "python-runtime/venv/bin/python"
    marker = tmp_path / "python-invoked"
    (runtime_source / "scripts").mkdir(parents=True)
    fake_python.parent.mkdir(parents=True)
    fake_python.write_text(
        f"#!/usr/bin/env bash\ntouch {marker!s}\n",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)
    wrapper = runtime_source / "scripts/runner.sh"
    wrapper.write_text(
        RUNNER.read_text(encoding="utf-8").replace(
            "/var/lib/job-intel-gate-b-artifacts", str(artifact_parent)
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        ["bash", str(wrapper), "run-description-evidence", "unexpected"],
        text=True,
        capture_output=True,
    )

    assert result.returncode == 77
    assert "no arguments" in result.stderr
    assert not marker.exists()
