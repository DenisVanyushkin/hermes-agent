import importlib.util
import os
import subprocess
from pathlib import Path

os.environ["BASELINE_DOCTOR_ALLOW_ANY_REPO"] = "1"

_spec = importlib.util.spec_from_file_location(
    "baseline_doctor",
    Path(__file__).resolve().parent.parent / "scripts" / "baseline_doctor.py",
)
baseline_doctor = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(baseline_doctor)


def _git(repo, *a):
    subprocess.run(["git", *a], cwd=repo, check=True, capture_output=True)


def _seed(tmp_path):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@t")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "tracked.txt").write_text("base\n")
    _git(tmp_path, "add", "tracked.txt")
    _git(tmp_path, "commit", "-qm", "base")
    return tmp_path


def test_clean_repo(tmp_path):
    result = baseline_doctor.run_doctor(_seed(tmp_path))
    assert result == {"clean": True, "fixed": [], "remaining": []}


def test_untracked_reported_not_touched(tmp_path):
    repo = _seed(tmp_path)
    (repo / "scripts").mkdir()
    (repo / "scripts" / "x.py").write_text("y\n")
    result = baseline_doctor.run_doctor(repo)
    assert result["clean"] is False
    assert result["fixed"] == []
    assert result["remaining"][0]["path"] == "scripts/x.py"
    assert result["remaining"][0]["category"] == "untracked"
    assert "script" in result["remaining"][0]["hint"]
    # untouched
    assert (repo / "scripts" / "x.py").exists()


def test_root_owned_is_chowned_via_injected_callable(tmp_path):
    repo = _seed(tmp_path)
    (repo / "leftover.pyc").write_text("z\n")
    calls = []

    def fake_chown(path):
        calls.append(path)
        return True

    # Force the entry to look root-owned by monkeypatching classify.
    from hermes_cli import baseline_git

    orig = baseline_git._is_root_owned
    baseline_git._is_root_owned = lambda repo, p: p == "leftover.pyc"
    try:
        result = baseline_doctor.run_doctor(repo, chown=fake_chown)
    finally:
        baseline_git._is_root_owned = orig

    assert calls == ["leftover.pyc"]
    assert result["fixed"][0]["path"] == "leftover.pyc"
    assert result["fixed"][0]["category"] == "root_owned"
