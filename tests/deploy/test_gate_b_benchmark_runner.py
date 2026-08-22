from __future__ import annotations

from pathlib import Path
import os
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
EXPORTER = ROOT / "scripts/export_job_intel_gate_b_benchmark.sh"
SUPERVISED_RUNNER = ROOT / "scripts/job_intel_gate_b_supervised.sh"


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
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Gate B test"], check=True)
    (repo / "tracked.txt").write_text("reviewed\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "tracked.txt"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "test fixture"], check=True)
    commit = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()
    (repo / "tracked.txt").write_text("dirty checkout\n", encoding="utf-8")
    result = subprocess.run(
        ["bash", str(EXPORTER), str(repo), commit, str(tmp_path / "export"), sys.executable],
        text=True,
        capture_output=True,
    )
    assert result.returncode != 0
    assert "source_tree_dirty" in result.stderr


def test_supervised_wrapper_is_the_single_systemd_enforcement_root() -> None:
    assert SUPERVISED_RUNNER.exists()
    script = SUPERVISED_RUNNER.read_text(encoding="utf-8")
    assert "sudo -n systemd-run --wait --pipe --uid=hermes" in script
    assert 'protected_paths=(' in script
    assert '"${#protected_paths[@]}" -eq 5' in script
    assert 'namespace_properties+=(--property="InaccessiblePaths=${protected_path}")' in script
    for protected in (
        "/home/hermes/.hermes/state.db",
        "/var/lib/job-intel/state",
        "/home/hermes/.cache",
        "/var/lib/browser-desktop/profiles",
        "/home/hermes/.hermes/sessions",
    ):
        assert f'"{protected}"' in script
    for retired in (
        "/home/hermes/.hermes/job_intel/job_intel.sqlite3",
        "/home/hermes/.hermes/job_intel/job_intel.sqlite3-wal",
        "/home/hermes/.hermes/job_intel/job_intel.sqlite3-shm",
    ):
        assert f'"{retired}"' not in script
    assert ":/home/hermes/.hermes" not in script
    assert "state.db" in script
    assert "/var/lib/job-intel/state" in script
    assert "/home/hermes/.cache" in script
    assert "/var/lib/browser-desktop/profiles" in script
    assert "/home/hermes/.hermes/sessions" in script
    syntax = subprocess.run(["bash", "-n", str(SUPERVISED_RUNNER)], text=True, capture_output=True)
    assert syntax.returncode == 0, syntax.stderr


def test_supervised_wrapper_fails_if_one_protected_path_is_removed(tmp_path: Path) -> None:
    artifact_parent = tmp_path / "artifacts"
    artifact_root = artifact_parent / ("a" * 64)
    runtime_source = artifact_root / "runtime"
    runtime_source.mkdir(parents=True)
    fake_python = artifact_root / "python-runtime/venv/bin/python"
    fake_python.parent.mkdir(parents=True)
    fake_python.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    fake_python.chmod(0o755)
    source = SUPERVISED_RUNNER.read_text(encoding="utf-8").replace(
        "/var/lib/job-intel-gate-b-artifacts", str(artifact_parent)
    ).replace('  "/home/hermes/.cache"\n', "", 1)
    wrapper = runtime_source / "scripts/runner.sh"
    wrapper.parent.mkdir(parents=True)
    wrapper.write_text(source, encoding="utf-8")
    wrapper.chmod(0o755)
    result = subprocess.run(["bash", str(wrapper), "run-supervised"], text=True, capture_output=True)
    assert result.returncode == 66
    assert "protected path set is incomplete" in result.stderr


def test_supervised_wrapper_fails_closed_before_sudo_if_property_composition_breaks(
    tmp_path: Path,
) -> None:
    artifact_parent = tmp_path / "artifacts"
    artifact_root = artifact_parent / ("a" * 64)
    runtime_source = artifact_root / "runtime/scripts"
    runtime_source.mkdir(parents=True)
    fake_python = artifact_root / "python-runtime/venv/bin/python"
    fake_python.parent.mkdir(parents=True)
    fake_python.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    fake_python.chmod(0o755)
    fake_sudo = tmp_path / "bin/sudo"
    fake_sudo.parent.mkdir()
    marker = tmp_path / "sudo-invoked"
    fake_sudo.write_text(f"#!/usr/bin/env bash\ntouch {marker!s}\nexit 99\n", encoding="utf-8")
    fake_sudo.chmod(0o755)
    source = SUPERVISED_RUNNER.read_text(encoding="utf-8").replace(
        "/var/lib/job-intel-gate-b-artifacts", str(artifact_parent)
    ).replace("namespace_properties=()", "readonly -a namespace_properties=()", 1)
    wrapper = runtime_source / "runner.sh"
    wrapper.write_text(source, encoding="utf-8")
    wrapper.chmod(0o755)
    result = subprocess.run(
        ["bash", str(wrapper), "run-supervised"],
        env={**os.environ, "PATH": f"{fake_sudo.parent}:{os.environ['PATH']}"},
        text=True,
        capture_output=True,
    )
    assert result.returncode != 0
    assert not marker.exists()
