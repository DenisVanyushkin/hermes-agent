from __future__ import annotations

import os
from pathlib import Path
import subprocess

import yaml

from job_intel.product_search.acquisition_probe import (
    build_experiment_manifest,
    validate_experiment_manifest,
)


ROOT = Path(__file__).resolve().parents[2]
WRAPPER = ROOT / "scripts/job_intel_product_search_experiment.sh"
SERVICE = ROOT / "deploy/systemd/experiments/job-intel-product-search-probe-experiment.service"
TIMER = ROOT / "deploy/systemd/experiments/job-intel-product-search-probe-experiment.timer"
EXPORTER = ROOT / "scripts/export_job_intel_product_search_experiment.sh"


def valid_manifest(tmp_path: Path) -> dict:
    root = tmp_path / "gate-a" / "commit"
    paths = {name: str(root / name) for name in ("runtime", "experiment.sqlite3", "raw-evidence", "logs", "locks", "browser-profile", "cache", "tmp")}
    return {
        "schema_version": "1.0.0",
        "gate": "gate-a",
        "environment_id": "product-search-gate-a",
        "commit": "a" * 40,
        "root": str(root),
        "paths": paths,
        "python": {
            "executable_path": str(root / "python-runtime/venv/bin/python"),
            "executable_sha256": "b" * 64,
            "version": "3.12.13",
            "implementation": "CPython",
            "stdlib_root": str(root / "python-runtime/stdlib"),
            "stdlib_tree_sha256": "c" * 64,
        },
        "environment": {
            "dependency_lock_sha256": "d" * 64,
            "installed_distributions_sha256": "e" * 64,
            "import_root": str(root / "runtime"),
            "sys_path_sha256": "f" * 64,
            "editable_installs": [],
        },
        "runtime_sha256": "1" * 64,
        "config_sha256": "2" * 64,
        "source_sha256": "3" * 64,
        "unit_sha256": "4" * 64,
    }


def test_manifest_requires_dedicated_runtime_python_and_disjoint_state(tmp_path: Path) -> None:
    manifest = valid_manifest(tmp_path)

    validate_experiment_manifest(manifest)

    manifest["environment"]["editable_installs"] = ["/workspace/live-hermes"]
    try:
        validate_experiment_manifest(manifest)
    except ValueError as exc:
        assert "editable installs" in str(exc)
    else:
        raise AssertionError("editable install accepted")


def test_manifest_builder_hashes_runtime_python_dependencies_and_paths(tmp_path: Path) -> None:
    root = tmp_path / "gate-a" / ("a" * 40)
    runtime = root / "runtime"
    python_runtime = root / "python-runtime"
    executable = python_runtime / "venv/bin/python"
    stdlib = python_runtime / "stdlib"
    for path, content in (
        (runtime / "job_intel/product_search/acquisition_probe.py", "probe"),
        (runtime / "config/product_search/search_contract.v1.yaml", "contract"),
        (runtime / "uv.lock", "lock"),
        (runtime / "deploy/systemd/experiments/probe.service", "unit"),
        (executable, "python"),
        (stdlib / "os.py", "stdlib"),
        (python_runtime / "installed-distributions.txt", "PyYAML==6.0.3\n"),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    manifest = build_experiment_manifest(
        root=root,
        commit="a" * 40,
        python_executable=executable,
        python_version="3.12.13",
        stdlib_root=stdlib,
        sys_path=(str(runtime), str(stdlib)),
    )

    validate_experiment_manifest(manifest)
    assert manifest["environment"]["editable_installs"] == []
    assert manifest["environment"]["import_root"] == str(runtime)
    assert manifest["paths"]["experiment.sqlite3"] == str(root / "experiment.sqlite3")
    assert len(manifest["runtime_sha256"]) == 64
    assert len(manifest["python"]["stdlib_tree_sha256"]) == 64


def test_manifest_rejects_shared_venv_and_production_paths(tmp_path: Path) -> None:
    manifest = valid_manifest(tmp_path)
    manifest["python"]["executable_path"] = "/home/hermes/.hermes/hermes-agent/venv/bin/python"
    try:
        validate_experiment_manifest(manifest)
    except ValueError as exc:
        assert "experiment-local" in str(exc)
    else:
        raise AssertionError("shared venv accepted")

    manifest = valid_manifest(tmp_path)
    manifest["paths"]["experiment.sqlite3"] = "/var/lib/job-intel/state/job_intel.sqlite3"
    try:
        validate_experiment_manifest(manifest)
    except ValueError as exc:
        assert "outside experiment root" in str(exc)
    else:
        raise AssertionError("production database accepted")


def test_wrapper_fails_closed_when_slack_credentials_are_present(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.yaml"
    manifest_path.write_text(yaml.safe_dump(valid_manifest(tmp_path)), encoding="utf-8")
    env = os.environ.copy()
    env["SLACK_BOT_TOKEN"] = "forbidden"

    result = subprocess.run(
        ["bash", str(WRAPPER), "preflight", str(manifest_path)],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode != 0
    assert "Slack credentials are forbidden" in result.stderr


def test_systemd_runner_is_temporary_hermes_user_and_overlap_safe() -> None:
    service = SERVICE.read_text(encoding="utf-8")
    timer = TIMER.read_text(encoding="utf-8")

    assert "User=hermes" in service
    assert "Environment=PYTHONNOUSERSITE=1" in service
    assert "Environment=PYTHONDONTWRITEBYTECODE=1" in service
    assert "job_intel_product_search_experiment.sh run" in service
    assert "Persistent=true" in timer
    assert "OnCalendar=" in timer
    assert "flock" in (ROOT / "scripts/job_intel_product_search_experiment.sh").read_text(encoding="utf-8")


def test_exporter_skips_project_build_and_pins_copied_python() -> None:
    exporter = EXPORTER.read_text(encoding="utf-8")

    assert "--no-install-project" in exporter
    assert '--python "$destination/python-runtime/venv/bin/python"' in exporter
    assert "--no-editable" not in exporter
