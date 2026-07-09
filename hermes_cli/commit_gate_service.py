"""Platform-independent core of the commit-gate approval flow: pending-commit
marker, reply parsing, operator auth, and the git commit/discard executor.
Kept out of the gateway/adapters so it is unit-testable (mirrors
baseline_doctor_service.py)."""
from __future__ import annotations
import json, os, subprocess
from pathlib import Path
from typing import Any

_GATE_SIGNATURE = "ЗАДАЧА ВЫПОЛНЕНА"  # substring of the success gate message; used by the reaction handler to recognize a gate message

def _pending_path() -> Path:
    home = Path(os.getenv("HERMES_HOME") or (Path.home() / ".hermes"))
    return home / "state" / "commit_gate_pending.json"

def is_operator(user_id: str) -> bool:
    expected = (os.getenv("HERMES_OPERATOR_SLACK_UID") or "").strip()
    return bool(expected) and (user_id or "").strip() == expected

def is_gate_message(text: str) -> bool:
    return _GATE_SIGNATURE in (text or "")

def record_pending(*, session_id: str, workspace_path: str, changed_files: list[str], commit_message: str) -> None:
    data = {
        "session_id": str(session_id or ""),
        "workspace_path": str(workspace_path or ""),
        "changed_files": [str(p) for p in (changed_files or []) if str(p).strip()],
        "commit_message": str(commit_message or "").strip() or "chore: controlled-pipeline change",
        "status": "awaiting_commit",
    }
    path = _pending_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))

def get_pending() -> dict[str, Any] | None:
    path = _pending_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except Exception:
        return None
    return data if isinstance(data, dict) and data.get("status") == "awaiting_commit" else None

def clear_pending() -> None:
    path = _pending_path()
    try:
        path.unlink()
    except FileNotFoundError:
        pass

def parse_commit_reply(text: str) -> str | None:
    """Narrow parser. Returns 'commit' | 'commit_push' | 'discard' | None.
    Returns None for anything that is not an unambiguous commit/discard reply,
    so ordinary messages never fire the intercept."""
    t = (text or "").strip().lower()
    if not t or len(t) > 40:
        return None
    push_words = ("запушь", "запуш", "push")
    commit_words = ("коммить", "коммит", "закоммить", "закоммит", "commit")
    discard_words = ("отмена", "отмени", "отменить", "cancel", "discard", "сбрось", "сбросить", "не коммить")
    if any(w in t for w in discard_words):
        return "discard"
    if any(w in t for w in commit_words):
        return "commit_push" if any(w in t for w in push_words) else "commit"
    return None

def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(repo), capture_output=True, text=True)

def apply_commit(*, repo: Path, changed_files: list[str], commit_message: str, push: bool = False) -> dict[str, Any]:
    paths = [p for p in (changed_files or []) if str(p).strip()]
    if not paths:
        return {"ok": False, "detail": "nothing to commit", "committed": False}
    add = _git(repo, "add", "--", *paths)
    if add.returncode != 0:
        return {"ok": False, "detail": (add.stderr or add.stdout).strip(), "committed": False}
    commit = _git(repo, "commit", "-m", commit_message)
    if commit.returncode != 0:
        return {"ok": False, "detail": (commit.stderr or commit.stdout).strip(), "committed": False}
    head = _git(repo, "rev-parse", "--short", "HEAD").stdout.strip()
    result: dict[str, Any] = {"ok": True, "committed": True, "sha": head, "paths": paths, "pushed": False}
    if push:
        pushed = _push(repo)
        result["pushed"] = pushed.get("ok", False)
        result["push_detail"] = pushed.get("detail", "")
    return result

def _push(repo: Path) -> dict[str, Any]:
    upstream = _git(repo, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    if upstream.returncode == 0 and upstream.stdout.strip():
        p = _git(repo, "push")
    else:
        branch = _git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
        if not branch or branch == "HEAD":
            return {"ok": False, "detail": "no branch to push"}
        p = _git(repo, "push", "-u", "origin", branch)
    return {"ok": p.returncode == 0, "detail": (p.stderr or p.stdout).strip()}

def apply_discard(*, repo: Path, changed_files: list[str]) -> dict[str, Any]:
    """Non-destructive discard: stash the pending files (recoverable) so the tree returns to baseline."""
    paths = [p for p in (changed_files or []) if str(p).strip()]
    if not paths:
        return {"ok": True, "detail": "nothing to discard"}
    proc = _git(repo, "stash", "push", "--include-untracked", "-m", "commit-gate: discarded pending deliverable", "--", *paths)
    return {"ok": proc.returncode == 0, "detail": (proc.stderr or proc.stdout).strip()}
