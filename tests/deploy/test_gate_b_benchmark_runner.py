from __future__ import annotations

from pathlib import Path
import os
import shutil
import subprocess
import sys
import sysconfig


ROOT = Path(__file__).resolve().parents[2]
EXPORTER = ROOT / "scripts/export_job_intel_gate_b_benchmark.sh"
RUNNER = ROOT / "scripts/job_intel_gate_b_benchmark.sh"
SUPERVISED_RUNNER = ROOT / "scripts/job_intel_gate_b_supervised.sh"
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
    assert 'PYTHONHOME="$artifact_root/python-runtime/venv"' in runner
    assert 'LD_LIBRARY_PATH="$artifact_root/python-runtime/venv/lib' in runner
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


def test_runner_module_has_a_fail_closed_cli() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "job_intel.product_search.gate_b_evidence_runner_v1",
            "not-a-command",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    assert result.returncode != 0
    assert "usage" in result.stderr.lower()


def test_installer_preserves_executable_artifact_files() -> None:
    installer = INSTALLER.read_text(encoding="utf-8")
    assert "chmod u=rwX,g=rX,o=" in installer
    assert "artifact contains symlink" in installer
    assert 'excluded = {"runtime-manifest.sha256"}' in installer


def test_wrapper_reaches_cli_and_fails_closed_without_collection_config(
    tmp_path: Path,
) -> None:
    artifact_parent = tmp_path / "artifacts"
    artifact_root = artifact_parent / ("a" * 64)
    runtime_source = artifact_root / "runtime"
    shutil.copytree(ROOT / "job_intel", runtime_source / "job_intel")
    (runtime_source / "config/product_search").mkdir(parents=True)
    shutil.copy2(
        ROOT / "config/product_search/gate_b_benchmark.v3.yaml",
        runtime_source / "config/product_search/gate_b_benchmark.v3.yaml",
    )
    shutil.copy2(
        ROOT / "config/product_search/evidence_synthesis.v1.yaml",
        runtime_source / "config/product_search/evidence_synthesis.v1.yaml",
    )
    shutil.copy2(
        ROOT / "config/product_search/decision_contract.v2.yaml",
        runtime_source / "config/product_search/decision_contract.v2.yaml",
    )
    shutil.copy2(
        ROOT / "config/product_search/career_profile.v2.yaml",
        runtime_source / "config/product_search/career_profile.v2.yaml",
    )
    semantic_target = runtime_source / "job_intel/vacancy_understanding/semantic"
    semantic_target.mkdir(parents=True, exist_ok=True)
    shutil.copy2(
        ROOT / "job_intel/vacancy_understanding/semantic/semantic-fact-contract.yaml",
        semantic_target / "semantic-fact-contract.yaml",
    )
    shutil.copy2(
        ROOT / "config/product_search/career_profile.v2.yaml",
        runtime_source / "config/product_search/career_profile.v2.yaml",
    )
    semantic_target = runtime_source / "job_intel/vacancy_understanding/semantic"
    semantic_target.mkdir(parents=True, exist_ok=True)
    shutil.copy2(
        ROOT / "job_intel/vacancy_understanding/semantic/semantic-fact-contract.yaml",
        semantic_target / "semantic-fact-contract.yaml",
    )
    (runtime_source / "scripts").mkdir()
    shutil.copy2(RUNNER, runtime_source / "scripts/runner.sh")
    fake_python = artifact_root / "python-runtime/venv/bin/python"
    fake_python.parent.mkdir(parents=True)
    fake_python.write_text(
        f"#!/usr/bin/env bash\nexec {sys.executable!s} \"$@\"\n",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)
    shutil.copytree(
        Path(sysconfig.get_paths()["stdlib"]),
        artifact_root / "python-runtime/venv/lib" / f"python{sys.version_info.major}.{sys.version_info.minor}",
        symlinks=False,
        dirs_exist_ok=True,
    )
    wrapper = runtime_source / "scripts/runner.sh"
    wrapper.write_text(
        wrapper.read_text(encoding="utf-8").replace(
            "/var/lib/job-intel-gate-b-artifacts", str(artifact_parent)
        ),
        encoding="utf-8",
    )
    state = tmp_path / "state"
    state.mkdir()

    result = subprocess.run(
        ["bash", str(wrapper), "run-description-evidence"],
        env={**os.environ, "STATE_DIRECTORY": str(state)},
        text=True,
        capture_output=True,
    )

    assert result.returncode != 0
    assert "requires config" in result.stderr


def test_published_wrapper_runs_positive_collection_with_anchored_fake_provider(
    tmp_path: Path,
) -> None:
    from tests.product_search import gate_b_cli_smoke_fixture as fixture

    artifact_parent = tmp_path / "artifacts"
    artifact_root = artifact_parent / ("b" * 64)
    runtime_source = artifact_root / "runtime"
    shutil.copytree(ROOT / "job_intel", runtime_source / "job_intel")
    (runtime_source / "config/product_search").mkdir(parents=True)
    shutil.copy2(
        ROOT / "config/product_search/gate_b_benchmark.v3.yaml",
        runtime_source / "config/product_search/gate_b_benchmark.v3.yaml",
    )
    shutil.copy2(
        ROOT / "config/product_search/evidence_synthesis.v1.yaml",
        runtime_source / "config/product_search/evidence_synthesis.v1.yaml",
    )
    shutil.copy2(
        ROOT / "config/product_search/decision_contract.v2.yaml",
        runtime_source / "config/product_search/decision_contract.v2.yaml",
    )
    shutil.copy2(
        ROOT / "config/product_search/career_profile.v2.yaml",
        runtime_source / "config/product_search/career_profile.v2.yaml",
    )
    semantic_target = runtime_source / "job_intel/vacancy_understanding/semantic"
    semantic_target.mkdir(parents=True, exist_ok=True)
    shutil.copy2(
        ROOT / "job_intel/vacancy_understanding/semantic/semantic-fact-contract.yaml",
        semantic_target / "semantic-fact-contract.yaml",
    )
    (runtime_source / "tests/product_search").mkdir(parents=True)
    shutil.copytree(
        ROOT / "tests/product_search/fixtures",
        runtime_source / "tests/product_search/fixtures",
    )
    for name in ("test_decision_v2.py", "test_gate_b_evidence_skeleton.py"):
        shutil.copy2(ROOT / "tests/product_search" / name, runtime_source / "tests/product_search" / name)
    shutil.copy2(
        ROOT / "tests/product_search/gate_b_cli_smoke_fixture.py",
        runtime_source / "gate_b_cli_smoke_fixture.py",
    )
    (runtime_source / "scripts").mkdir()
    shutil.copy2(RUNNER, runtime_source / "scripts/runner.sh")
    manifest_path, config_path, manifest_sha256 = fixture.prepare(
        root=tmp_path / "config",
        artifact_root=artifact_root,
        repo_root=ROOT,
    )
    fake_python = artifact_root / "python-runtime/venv/bin/python"
    shutil.copytree(
        Path(sysconfig.get_paths()["stdlib"]),
        artifact_root / "python-runtime/venv/lib" / f"python{sys.version_info.major}.{sys.version_info.minor}",
        symlinks=False,
        dirs_exist_ok=True,
    )
    # Resolve the path through the same runtime helper production uses, then
    # prove the direct write is still inside the temporary artifact.
    from job_intel.product_search.gate_b_runtime_v1 import (
        _site_packages,
        assert_artifact_destination_safe,
    )

    fixture_shim = artifact_root / "python-runtime/venv/lib/python3.12/site-packages/00-pysqlite3-shim.pth"
    resolved_shim = _site_packages(
        fake_python,
        python_home=artifact_root / "python-runtime/venv",
    ) / "00-pysqlite3-shim.pth"
    assert_artifact_destination_safe(resolved_shim)
    fixture_shim_source = tmp_path / "fixture-shim.pth"
    shutil.copy2(fixture_shim, fixture_shim_source)
    assert_artifact_destination_safe(resolved_shim)
    resolved_shim.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(fixture_shim_source, resolved_shim)
    wrapper = runtime_source / "scripts/runner.sh"
    wrapper.write_text(
        wrapper.read_text(encoding="utf-8").replace(
            "/var/lib/job-intel-gate-b-artifacts", str(artifact_parent)
        ),
        encoding="utf-8",
    )
    state = tmp_path / "state"
    state.mkdir()
    init = subprocess.run(
        [
            str(fake_python),
            "-m",
            "job_intel.product_search.gate_b_evidence_runner_v1",
            "init-run",
            "--manifest",
            str(manifest_path),
            "--state-directory",
            str(state),
            "--manifest-sha256",
            manifest_sha256,
        ],
        env={
            **os.environ,
            "PYTHONPATH": str(runtime_source),
            "STATE_DIRECTORY": str(state),
        },
        text=True,
        capture_output=True,
    )
    assert init.returncode == 0, init.stderr

    result = subprocess.run(
        ["bash", str(wrapper), "run-description-evidence"],
        env={
            **os.environ,
            "STATE_DIRECTORY": str(state),
            "GATE_B_COLLECTION_CONFIG": str(config_path),
            "GATE_B_EVIDENCE_MANIFEST": str(manifest_path),
            "GATE_B_MANIFEST_SHA256": manifest_sha256,
        },
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    assert manifest_path.is_file()
    report = state / "measurement-report.json"
    if not report.is_file():
        print("stdout:", result.stdout)
        print("stderr:", result.stderr)
    assert report.is_file()
    assert (state / "journal.jsonl").is_file()
    assert len(list((state / "recordings").glob("*.json"))) == 48
    assert len(list((state / "decisions").glob("*.json"))) == 48


def test_supervised_wrapper_is_the_single_systemd_enforcement_root() -> None:
    assert SUPERVISED_RUNNER.exists()
    script = SUPERVISED_RUNNER.read_text(encoding="utf-8")
    assert "sudo -n systemd-run --wait --pipe --uid=hermes" in script
    assert 'protected_paths=(' in script
    assert '"${#protected_paths[@]}" -eq 6' in script
    assert 'namespace_properties+=(--property="InaccessiblePaths=-${protected_path}")' in script
    assert ":/home/hermes/.hermes" not in script
    assert "state.db" in script
    assert "job_intel.sqlite3" in script
    assert "job_intel.sqlite3-wal" in script
    assert "job_intel.sqlite3-shm" in script
    assert "/home/hermes/.cache" in script
    assert "/var/lib/browser-desktop/profiles" in script
    assert "chown -R root:hermes" in INSTALLER.read_text(encoding="utf-8")
    syntax = subprocess.run(
        ["bash", "-n", str(SUPERVISED_RUNNER)],
        text=True,
        capture_output=True,
    )
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
    source = SUPERVISED_RUNNER.read_text(encoding="utf-8")
    source = source.replace(
        "/var/lib/job-intel-gate-b-artifacts", str(artifact_parent)
    )
    source = source.replace(
        '  "/home/hermes/.cache"\n',
        "",
        1,
    )
    wrapper = runtime_source / "scripts/runner.sh"
    wrapper.parent.mkdir(parents=True)
    wrapper.write_text(source, encoding="utf-8")
    wrapper.chmod(0o755)
    result = subprocess.run(
        ["bash", str(wrapper), "init-run"],
        text=True,
        capture_output=True,
    )
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
    fake_sudo.write_text(
        f"#!/usr/bin/env bash\ntouch {marker!s}\nexit 99\n",
        encoding="utf-8",
    )
    fake_sudo.chmod(0o755)
    source = SUPERVISED_RUNNER.read_text(encoding="utf-8")
    source = source.replace(
        "/var/lib/job-intel-gate-b-artifacts", str(artifact_parent)
    )
    source = source.replace(
        "namespace_properties=()",
        "readonly -a namespace_properties=()",
        1,
    )
    wrapper = runtime_source / "runner.sh"
    wrapper.write_text(source, encoding="utf-8")
    wrapper.chmod(0o755)
    result = subprocess.run(
        ["bash", str(wrapper), "init-run"],
        env={**os.environ, "PATH": f"{fake_sudo.parent}:{os.environ['PATH']}"},
        text=True,
        capture_output=True,
    )
    assert result.returncode != 0
    assert not marker.exists()
