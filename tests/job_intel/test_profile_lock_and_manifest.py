"""Behavioral tests for the isolated profile lock and tree manifest."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import stat
import subprocess
import sys

import pytest


REPO_ROOT = Path(__file__).parents[2]
LOCK_SCRIPT = REPO_ROOT / "scripts/job_intel_profile_lock.sh"
MANIFEST_SCRIPT = REPO_ROOT / "scripts/job_intel_profile_manifest.py"


def _start_holder(lock_path: Path) -> subprocess.Popen[bytes]:
    try:
        return subprocess.Popen(
            [str(LOCK_SCRIPT), "--path", str(lock_path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        pytest.fail(f"profile lock implementation is missing: {exc}")


def _wait_for_acquisition(holder: subprocess.Popen[bytes]) -> None:
    assert holder.stdout is not None
    line = holder.stdout.readline().decode()
    assert "acquired" in line, (
        f"holder did not report acquisition: {line!r}; "
        f"stderr={holder.stderr.read().decode() if holder.stderr else ''!r}"
    )


def _stop_holder(holder: subprocess.Popen[bytes]) -> None:
    if holder.poll() is None:
        holder.terminate()
    try:
        holder.wait(timeout=3)
    except subprocess.TimeoutExpired:
        holder.kill()
        holder.wait(timeout=3)


def _run_lock(lock_path: Path) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [str(LOCK_SCRIPT), "--path", str(lock_path)],
        capture_output=True,
        timeout=3,
    )


def test_second_profile_lock_fails_while_the_holder_is_alive(tmp_path: Path) -> None:
    lock_path = tmp_path / "profile.lock"
    holder = _start_holder(lock_path)
    try:
        _wait_for_acquisition(holder)
        result = _run_lock(lock_path)
    finally:
        _stop_holder(holder)

    assert result.returncode != 0
    assert b"held" in result.stderr


def test_execstart_holder_must_stay_alive_after_acquiring_the_lock(
    tmp_path: Path,
) -> None:
    """A returning ExecStart process releases flock even if its unit stays active.

    This assertion deliberately treats that anti-pattern as a failure. The
    holder must remain alive with its descriptor open; a second process must not
    be allowed to acquire the lock merely because ExecStart returned.
    """
    lock_path = tmp_path / "profile.lock"
    holder = _start_holder(lock_path)
    try:
        _wait_for_acquisition(holder)
        assert holder.poll() is None, (
            "the ExecStart-style holder returned after acquiring flock; "
            "the unit could remain active while the lock was released"
        )
        result = _run_lock(lock_path)
    finally:
        _stop_holder(holder)

    assert result.returncode != 0


def test_stale_lock_file_does_not_block_a_new_holder(tmp_path: Path) -> None:
    lock_path = tmp_path / "profile.lock"
    lock_path.touch()

    holder = _start_holder(lock_path)
    try:
        _wait_for_acquisition(holder)
        assert holder.poll() is None
    finally:
        _stop_holder(holder)


def _manifest(profile: Path) -> bytes:
    result = subprocess.run(
        ["python3", str(MANIFEST_SCRIPT), str(profile)],
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr.decode()
    return result.stdout


def _load_manifest_module():
    spec = importlib.util.spec_from_file_location("profile_manifest", MANIFEST_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_ordered_tree(profile: Path) -> None:
    (profile / "zeta/deep").mkdir(parents=True)
    (profile / "alpha/inner").mkdir(parents=True)
    (profile / "zeta/deep/state").write_text("z\n", encoding="utf-8")
    (profile / "alpha/inner/state").write_text("a\n", encoding="utf-8")


def test_profile_manifest_is_deterministic_and_content_addressed(
    tmp_path: Path,
) -> None:
    profile = tmp_path / "profile"
    _write_ordered_tree(profile)

    first = _manifest(profile)
    second = _manifest(profile)

    assert first == second
    assert hashlib.sha256(first).hexdigest() == hashlib.sha256(second).hexdigest()
    payload = json.loads(first)
    assert [entry["path"] for entry in payload["entries"]] == sorted(
        entry["path"] for entry in payload["entries"]
    )


def test_profile_manifest_does_not_depend_on_scandir_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile = tmp_path / "profile"
    _write_ordered_tree(profile)
    expected = _manifest(profile)
    manifest_module = _load_manifest_module()
    original_scandir = manifest_module.os.scandir

    def reversed_scandir(path: Path):
        return list(reversed(list(original_scandir(path))))

    monkeypatch.setattr(manifest_module.os, "scandir", reversed_scandir)
    reordered = manifest_module.build_manifest(profile)
    actual = (
        json.dumps(reordered, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        .encode("utf-8")
        + b"\n"
    )

    assert actual == expected


def test_directory_replaced_by_symlink_changes_profile_manifest(tmp_path: Path) -> None:
    profile = tmp_path / "profile"
    directory = profile / "browser"
    directory.mkdir(parents=True)
    (directory / "state").write_text("inside\n", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "state").write_text("outside\n", encoding="utf-8")

    before = _manifest(profile)
    shutil.rmtree(directory)
    directory.symlink_to(outside, target_is_directory=True)

    after = _manifest(profile)

    assert before != after


def test_profile_manifest_records_directory_symlink_without_following_it(
    tmp_path: Path,
) -> None:
    profile = tmp_path / "profile"
    profile.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("outside\n", encoding="utf-8")
    link = profile / "external"
    link.symlink_to(outside, target_is_directory=True)

    payload = json.loads(_manifest(profile))
    entries = {entry["path"]: entry for entry in payload["entries"]}

    assert entries["external"]["type"] == "symlink"
    assert entries["external"]["target"] == str(outside)
    assert "external/secret.txt" not in entries


def test_file_content_change_changes_profile_manifest(tmp_path: Path) -> None:
    profile = tmp_path / "profile"
    profile.mkdir()
    file_path = profile / "preferences.json"
    file_path.write_text('{"theme":"dark"}\n', encoding="utf-8")

    before = _manifest(profile)
    file_path.write_text('{"theme":"light"}\n', encoding="utf-8")

    after = _manifest(profile)

    assert before != after


def test_mode_change_changes_profile_manifest_without_uid_gid_mutation(
    tmp_path: Path,
) -> None:
    """UID/GID mutation is not attempted: it needs privileges unavailable here."""
    profile = tmp_path / "profile"
    profile.mkdir()
    file_path = profile / "state"
    file_path.write_text("state\n", encoding="utf-8")

    before = _manifest(profile)
    file_path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    after = _manifest(profile)

    assert before != after


def test_profile_manifest_refuses_unreadable_entry_without_partial_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    profile = tmp_path / "profile"
    profile.mkdir()
    (profile / "unreadable.db").write_bytes(b"private\n")
    manifest_module = _load_manifest_module()

    def unreadable(_path: Path) -> str:
        raise PermissionError("Permission denied")

    monkeypatch.setattr(manifest_module, "_sha256_file", unreadable)
    monkeypatch.setattr(sys, "argv", [str(MANIFEST_SCRIPT), str(profile)])

    assert manifest_module.main() == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Permission denied" in captured.err
