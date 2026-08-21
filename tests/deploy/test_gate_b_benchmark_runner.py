from __future__ import annotations

import hashlib
import inspect
import json
import os
from pathlib import Path
import stat
import subprocess
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

import job_intel.product_search.gate_b_benchmark_v3 as gate_b_v3


ROOT = Path(__file__).resolve().parents[2]
EXPORTER = ROOT / "scripts/export_job_intel_gate_b_benchmark.sh"
RUNNER = ROOT / "scripts/job_intel_gate_b_benchmark.sh"
INSTALLER = ROOT / "scripts/install_job_intel_gate_b_benchmark_unit.sh"
SERVICE = ROOT / "deploy/systemd/experiments/job-intel-gate-b-benchmark@.service"


def _runtime_manifest_payload() -> dict[str, object]:
    return {
        "schema_version": "3.0.0",
        "runtime_kind": "gate_b_at_most_once",
        "artifact_sha256": "f" * 64,
        "artifact_tree_sha256": "0" * 64,
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
    target="${{FAKE_UV_PYTHON_TARGET:-${{destination%/venv}}/cpython/bin/python3.12}}"
    ln -s "$target" "$destination/bin/python"
    ln -s python "$destination/bin/python3"
    ln -s python "$destination/bin/python3.12"
    ;;
  "sync --project")
    venv="${{UV_PROJECT_ENVIRONMENT:?}}"
    for executable in python python3 python3.12; do
      [[ -L "$venv/bin/$executable" ]]
    done
    rm -- "$venv/bin/python" "$venv/bin/python3" "$venv/bin/python3.12"
    ln -s "${{venv%/venv}}/cpython/bin/python3.12" "$venv/bin/python"
    ln -s python "$venv/bin/python3"
    ln -s python "$venv/bin/python3.12"
    touch "${{FAKE_UV_SYNC_MARKER:?}}"
    ;;
  "pip freeze")
    venv="${{4%/bin/python}}"
    for executable in python python3 python3.12; do
      [[ -L "$venv/bin/$executable" ]]
    done
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
    sync_marker = tmp_path / "uv-sync-reached"
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env["FAKE_UV_SYNC_MARKER"] = str(sync_marker)

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
    exported_python = destination / "python-runtime/venv/bin/python"
    assert exported_python.is_file()
    assert not exported_python.is_symlink()
    assert exported_python.stat().st_nlink == 1
    assert (
        hashlib.sha256(exported_python.read_bytes()).hexdigest()
        == hashlib.sha256(fake_python.read_bytes()).hexdigest()
    )
    for alias in ("python3", "python3.12"):
        exported_alias = exported_python.with_name(alias)
        assert exported_alias.is_file()
        assert not exported_alias.is_symlink()
        assert exported_alias.stat().st_nlink == 1
        assert (
            hashlib.sha256(exported_alias.read_bytes()).hexdigest()
            == hashlib.sha256(fake_python.read_bytes()).hexdigest()
        )
    forbidden = tuple(destination.rglob("*.service")) + tuple(
        destination.rglob("launch.pending.json")
    )
    assert forbidden == ()
    assert (
        hashlib.sha256((destination / "runtime-manifest.json").read_bytes()).hexdigest()
        == (destination / "runtime-manifest.sha256").read_text().strip()
    )
    assert sync_marker.exists()

    unsafe_destination = tmp_path / "unsafe-export"
    unsafe_sync_marker = tmp_path / "unsafe-uv-sync-reached"
    unsafe_env = dict(env)
    unsafe_env["FAKE_UV_PYTHON_TARGET"] = str(fake_python)
    unsafe_env["FAKE_UV_SYNC_MARKER"] = str(unsafe_sync_marker)
    unsafe = subprocess.run(
        [
            "bash",
            str(EXPORTER),
            str(repo),
            commit,
            str(unsafe_destination),
            str(fake_python),
        ],
        cwd=tmp_path,
        env=unsafe_env,
        text=True,
        capture_output=True,
    )

    assert unsafe.returncode == 66
    assert "venv Python target is not the copied interpreter" in unsafe.stderr
    assert not unsafe_sync_marker.exists()
    assert not (unsafe_destination / "runtime-manifest.json").exists()
    assert not (unsafe_destination / "export-observation.txt").exists()


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def test_root_launcher_consumes_one_exact_expiring_receipt_before_user_run(
    tmp_path: Path,
) -> None:
    runtime_manifest = gate_b_v3.GateBRuntimeManifestV3.model_validate(
        _runtime_manifest_payload()
    )
    package_manifest = gate_b_v3.GateBPackageManifestV3(
        schema_version="3.0.0",
        package_id="gate-b-v3-test",
        created_at=datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc),
        ordered_input_sha256s=tuple(f"{index + 1:064x}" for index in range(48)),
        authority_sha256s=("a" * 64,),
    )
    package_sha256 = package_manifest.canonical_sha256
    launch = gate_b_v3.GateBLaunchBindingV3(
        schema_version="3.0.0",
        run_id=f"gate-b-at-most-once-{package_sha256[:16]}",
        candidate_commit=runtime_manifest.candidate_commit,
        runtime_manifest_sha256=runtime_manifest.canonical_sha256,
        package_manifest_sha256=package_sha256,
        ordered_input_sha256s=package_manifest.ordered_input_sha256s,
        ordered_projection_sha256s=tuple(f"{index + 101:064x}" for index in range(48)),
        source_authority_sha256s={"fixture": "b" * 64},
        model_id="openai/gpt-5-mini",
        maximum_output_tokens=2_000,
        ordered_call_cap=48,
        per_call_maximum_usd=Decimal("0.01"),
        aggregate_maximum_usd=Decimal("0.48"),
    )
    checkpoint = gate_b_v3.GateBOwnerCheckpointManifestV3(
        schema_version="3.0.0",
        checkpoint_kind="gate_b_at_most_once_owner_approval",
        approved_at=datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc),
        launch_identity=launch,
    )
    receipt = gate_b_v3.GateBOneTimeLaunchReceiptV3(
        schema_version="3.0.0",
        receipt_kind="gate_b_at_most_once_launch",
        launch_kind="initial",
        benchmark_run_id=launch.run_id,
        launch_attempt_id=f"{launch.run_id}-{'c' * 64}",
        issued_at=datetime(2026, 8, 20, 12, 1, tzinfo=timezone.utc),
        expires_at=datetime(2026, 8, 20, 12, 31, tzinfo=timezone.utc),
        nonce="c" * 64,
        checkpoint_manifest_sha256=checkpoint.canonical_sha256,
        launch_identity_sha256=launch.canonical_sha256,
        candidate_commit=launch.candidate_commit,
        runtime_manifest_sha256=launch.runtime_manifest_sha256,
        package_manifest_sha256=launch.package_manifest_sha256,
        ordered_call_cap=48,
        per_call_maximum_usd=Decimal("0.01"),
        aggregate_maximum_usd=Decimal("0.48"),
    )
    pending_root = tmp_path / "etc"
    consumed_root = tmp_path / "run"
    package_parent = tmp_path / "packages"
    runtime_root = tmp_path / "runtime"
    pending_dir = pending_root / receipt.launch_attempt_id
    package_dir = package_parent / package_sha256
    pending_dir.mkdir(parents=True, mode=0o700)
    package_dir.mkdir(parents=True)
    runtime_root.mkdir()
    pending_path = pending_dir / "launch.pending.json"
    checkpoint_path = pending_dir / "owner-checkpoint.json"
    recovery_key_path = pending_dir / "owner-recovery-public-key.bin"
    pending_path.write_bytes(_canonical_bytes(receipt.model_dump(mode="json")))
    checkpoint_path.write_bytes(_canonical_bytes(checkpoint.model_dump(mode="json")))
    recovery_key_path.write_bytes(bytes.fromhex("22" * 32))
    for path in (pending_path, checkpoint_path, recovery_key_path):
        path.chmod(0o400)
    (package_dir / "package-manifest.json").write_bytes(
        _canonical_bytes(package_manifest.model_dump(mode="json"))
    )
    (runtime_root / "runtime-manifest.json").write_bytes(
        _canonical_bytes(runtime_manifest.model_dump(mode="json"))
    )

    consumed = gate_b_v3.consume_gate_b_launch_receipt_v3(
        pending_root=pending_root,
        consumed_root=consumed_root,
        package_parent=package_parent,
        runtime_export_root=runtime_root,
        now=datetime(2026, 8, 20, 12, 10, tzinfo=timezone.utc),
        expected_root_uid=os.geteuid(),
        expected_root_gid=os.getegid(),
        expected_hermes_uid=os.geteuid(),
        expected_hermes_gid=os.getegid(),
    )

    assert consumed == (
        consumed_root / receipt.launch_attempt_id / "launch.consumed.json"
    )
    assert not pending_path.exists()
    assert consumed.read_bytes() == _canonical_bytes(receipt.model_dump(mode="json"))
    assert stat.S_IMODE(consumed.stat().st_mode) == 0o440
    assert (consumed.parent / "owner-checkpoint.json").read_bytes() == (
        _canonical_bytes(checkpoint.model_dump(mode="json"))
    )
    assert (consumed.parent / "owner-recovery-public-key.bin").read_bytes() == (
        bytes.fromhex("22" * 32)
    )
    claim_directory = consumed.parent / "launch-claim"
    claim_metadata = claim_directory.stat()
    assert stat.S_IMODE(claim_metadata.st_mode) == 0o700
    assert claim_metadata.st_uid == os.geteuid()
    assert claim_metadata.st_gid == os.getegid()
    with pytest.raises(ValueError, match="pending_receipt"):
        gate_b_v3.consume_gate_b_launch_receipt_v3(
            pending_root=pending_root,
            consumed_root=consumed_root,
            package_parent=package_parent,
            runtime_export_root=runtime_root,
            now=datetime(2026, 8, 20, 12, 11, tzinfo=timezone.utc),
            expected_root_uid=os.geteuid(),
            expected_root_gid=os.getegid(),
            expected_hermes_uid=os.geteuid(),
            expected_hermes_gid=os.getegid(),
        )


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


def test_state_directory_replaces_root_preflight_namespace_setup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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


def test_output_root_preflight_repairs_existing_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    experiment_root = tmp_path / "gate-b-at-most-once"
    experiment_root.mkdir(mode=0o700)
    runs_root = experiment_root / "runs"
    runs_root.mkdir(mode=0o755)
    monkeypatch.setattr(
        gate_b_v3,
        "_GATE_B_PACKAGE_PARENT_V3",
        experiment_root,
    )

    observed = gate_b_v3.prepare_gate_b_runner_output_root_v3(
        expected_root_uid=os.geteuid(),
        expected_hermes_uid=os.geteuid(),
        expected_hermes_gid=os.getegid(),
    )

    assert observed == runs_root
    assert stat.S_IMODE(runs_root.lstat().st_mode) == 0o700


@pytest.mark.parametrize("unsafe_kind", ["file", "symlink"])
def test_output_root_preflight_rejects_unsafe_existing_entry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    unsafe_kind: str,
) -> None:
    experiment_root = tmp_path / "gate-b-at-most-once"
    experiment_root.mkdir(mode=0o700)
    runs_root = experiment_root / "runs"
    if unsafe_kind == "file":
        runs_root.write_bytes(b"unsafe")
    else:
        target = tmp_path / "outside"
        target.mkdir()
        runs_root.symlink_to(target, target_is_directory=True)
    monkeypatch.setattr(
        gate_b_v3,
        "_GATE_B_PACKAGE_PARENT_V3",
        experiment_root,
    )

    with pytest.raises(ValueError, match="runner_output_root_prepare_failed"):
        gate_b_v3.prepare_gate_b_runner_output_root_v3(
            expected_root_uid=os.geteuid(),
            expected_hermes_uid=os.geteuid(),
            expected_hermes_gid=os.getegid(),
        )


def test_output_root_preflight_rejects_non_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(gate_b_v3.os, "geteuid", lambda: 12345)

    with pytest.raises(ValueError, match="root_installer_required"):
        gate_b_v3.prepare_gate_b_runner_output_root_v3()


def test_runner_rejects_extra_arguments_before_python(tmp_path: Path) -> None:
    artifact_parent = tmp_path / "artifacts"
    artifact_root = artifact_parent / ("a" * 64)
    runtime_root = artifact_root
    runtime_source = runtime_root / "runtime"
    fake_python = runtime_root / "python-runtime/venv/bin/python"
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


def test_consumed_loader_ignores_started_attempt_and_selects_new_recovery(
    tmp_path: Path,
) -> None:
    consumed_root = tmp_path / "consumed"
    consumed_root.mkdir()
    benchmark_run_id = "gate-b-at-most-once-" + "1" * 16
    old_attempt = f"{benchmark_run_id}-{'2' * 64}"
    recovery_attempt = f"{benchmark_run_id}-{'3' * 64}"
    old_directory = consumed_root / old_attempt
    recovery_directory = consumed_root / recovery_attempt
    old_directory.mkdir()
    recovery_directory.mkdir()
    (old_directory / "launch.consumed.json").write_bytes(b"old")
    old_claim_directory = old_directory / "launch-claim"
    old_claim_directory.mkdir(mode=0o700)
    (old_claim_directory / "launch.started.json").write_bytes(b"started")
    (recovery_directory / "launch-claim").mkdir(mode=0o700)
    expected = recovery_directory / "launch.consumed.json"
    expected.write_bytes(b"recovery")

    selected = gate_b_v3._one_consumed_receipt_path_v3(consumed_root)

    assert selected == expected


def test_effective_hermes_user_claims_only_the_dedicated_writable_directory(
    tmp_path: Path,
) -> None:
    benchmark_run_id = "gate-b-at-most-once-" + "1" * 16
    nonce = "2" * 64
    receipt = gate_b_v3.GateBOneTimeLaunchReceiptV3(
        schema_version="3.0.0",
        receipt_kind="gate_b_at_most_once_launch",
        launch_kind="initial",
        benchmark_run_id=benchmark_run_id,
        launch_attempt_id=f"{benchmark_run_id}-{nonce}",
        issued_at=datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc),
        expires_at=datetime(2026, 8, 20, 12, 30, tzinfo=timezone.utc),
        nonce=nonce,
        checkpoint_manifest_sha256="3" * 64,
        launch_identity_sha256="4" * 64,
        candidate_commit="5" * 40,
        runtime_manifest_sha256="6" * 64,
        package_manifest_sha256="7" * 64,
        ordered_call_cap=48,
        per_call_maximum_usd=Decimal("0.01"),
        aggregate_maximum_usd=Decimal("0.48"),
    )
    consumed_root = tmp_path / "consumed"
    consumed_root.mkdir()
    attempt_directory = consumed_root / receipt.launch_attempt_id
    attempt_directory.mkdir(mode=0o700)
    consumed_path = attempt_directory / "launch.consumed.json"
    consumed_bytes = _canonical_bytes(receipt.model_dump(mode="json"))
    consumed_path.write_bytes(consumed_bytes)
    consumed_path.chmod(0o440)
    claim_directory = attempt_directory / "launch-claim"
    claim_directory.mkdir(mode=0o700)
    attempt_directory.chmod(0o550)

    assert not os.access(attempt_directory, os.W_OK)
    with pytest.raises(PermissionError):
        (attempt_directory / "receipt-bypass").write_bytes(b"forbidden")

    selected_path = gate_b_v3._one_consumed_receipt_path_v3(consumed_root)
    marker_path = gate_b_v3._claim_consumed_receipt_v3(
        selected_path,
        receipt,
    )

    assert marker_path == claim_directory / "launch.started.json"
    assert stat.S_IMODE(marker_path.stat().st_mode) == 0o600
    assert consumed_path.read_bytes() == consumed_bytes
    with pytest.raises(ValueError, match="count_invalid"):
        gate_b_v3._one_consumed_receipt_path_v3(consumed_root)
    with pytest.raises(ValueError, match="already_started"):
        gate_b_v3._claim_consumed_receipt_v3(consumed_path, receipt)
