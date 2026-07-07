import subprocess
from pathlib import Path

from hermes_cli import baseline_doctor_service as svc


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


def test_classify_action():
    assert svc.classify_action("inbox_tray") == "commit"
    assert svc.classify_action("see_no_evil") == "gitignore"
    assert svc.classify_action("package") == "stash"
    assert svc.classify_action("smile") is None


def test_is_operator(monkeypatch):
    monkeypatch.setenv("HERMES_OPERATOR_SLACK_UID", "U123")
    assert svc.is_operator("U123") is True
    assert svc.is_operator("U999") is False
    monkeypatch.delenv("HERMES_OPERATOR_SLACK_UID", raising=False)
    assert svc.is_operator("U123") is False


def test_is_block_message():
    assert svc.is_block_message("...\nfinal_verdict: autonomous_preflight_blocked\n...") is True
    assert svc.is_block_message("hello") is False


def test_pending_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    svc.record_pending("111.222", [{"path": "a.py", "category": "untracked"}])
    assert svc.pop_pending("111.222") == [{"path": "a.py", "category": "untracked"}]
    assert svc.pop_pending("111.222") is None  # popped


def test_apply_gitignore(tmp_path):
    repo = _seed(tmp_path)
    (repo / "new.py").write_text("x\n")
    result = svc.apply_action(repo, "gitignore", [{"path": "new.py", "category": "untracked"}])
    assert result["ok"] is True
    assert "new.py" in (repo / ".gitignore").read_text()


def test_apply_stash(tmp_path):
    repo = _seed(tmp_path)
    (repo / "new.py").write_text("x\n")
    result = svc.apply_action(repo, "stash", [{"path": "new.py", "category": "untracked"}])
    assert result["ok"] is True
    assert not (repo / "new.py").exists()  # parked


def test_apply_commit(tmp_path):
    repo = _seed(tmp_path)
    (repo / "new.py").write_text("x\n")
    result = svc.apply_action(repo, "commit", [{"path": "new.py", "category": "untracked"}])
    assert result["ok"] is True
    log = subprocess.run(["git", "log", "--oneline", "-1"], cwd=repo,
                         capture_output=True, text=True).stdout
    assert "baseline-doctor" in log
