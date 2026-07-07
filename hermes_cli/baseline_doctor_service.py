"""Platform-independent pieces of the Slack baseline-doctor reaction flow.

Kept out of the Slack adapter so the emoji->action mapping, operator auth,
pending-state persistence, and git action application are unit-testable.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

TRIGGER_REACTION = "broom"
_ACTIONS = {"inbox_tray": "commit", "see_no_evil": "gitignore", "package": "stash"}
_BLOCK_SIGNATURE = "final_verdict: autonomous_preflight_blocked"
_MAX_PENDING = 50


def classify_action(reaction: str) -> str | None:
    return _ACTIONS.get((reaction or "").strip().lower())


def is_operator(user_id: str) -> bool:
    expected = (os.getenv("HERMES_OPERATOR_SLACK_UID") or "").strip()
    return bool(expected) and (user_id or "").strip() == expected


def is_block_message(text: str) -> bool:
    return _BLOCK_SIGNATURE in (text or "")


def _pending_path() -> Path:
    home = Path(os.getenv("HERMES_HOME") or (Path.home() / ".hermes"))
    return home / "state" / "baseline_doctor_pending.json"


def _load_pending() -> dict:
    path = _pending_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def _save_pending(data: dict) -> None:
    path = _pending_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # Cap size: keep the most recent entries by insertion order.
    if len(data) > _MAX_PENDING:
        data = dict(list(data.items())[-_MAX_PENDING:])
    path.write_text(json.dumps(data))


def record_pending(report_ts: str, remaining: list[dict]) -> None:
    data = _load_pending()
    data[report_ts] = remaining
    _save_pending(data)


def pop_pending(report_ts: str) -> list[dict] | None:
    data = _load_pending()
    value = data.pop(report_ts, None)
    if value is not None:
        _save_pending(data)
    return value


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True)


def apply_action(repo: Path, action: str, remaining: list[dict]) -> dict:
    paths = [r["path"] for r in remaining]
    if not paths:
        return {"applied": action, "paths": [], "ok": True, "detail": "nothing to do"}
    if action == "gitignore":
        gitignore = repo / ".gitignore"
        existing = gitignore.read_text() if gitignore.exists() else ""
        additions = [p for p in paths if p not in existing.splitlines()]
        with gitignore.open("a") as fh:
            if existing and not existing.endswith("\n"):
                fh.write("\n")
            for p in additions:
                fh.write(f"{p}\n")
        return {"applied": action, "paths": paths, "ok": True, "detail": "appended to .gitignore"}
    if action == "stash":
        proc = _git(
            repo, "stash", "push", "--include-untracked", "-m",
            "baseline-doctor parked", "--", *paths,
        )
        return {"applied": action, "paths": paths, "ok": proc.returncode == 0,
                "detail": (proc.stderr or proc.stdout).strip()}
    if action == "commit":
        add = _git(repo, "add", "--", *paths)
        if add.returncode != 0:
            return {"applied": action, "paths": paths, "ok": False, "detail": add.stderr.strip()}
        commit = _git(
            repo, "commit", "-m",
            "chore(baseline-doctor): commit operator-approved working-tree files",
        )
        return {"applied": action, "paths": paths, "ok": commit.returncode == 0,
                "detail": (commit.stderr or commit.stdout).strip()}
    return {"applied": action, "paths": paths, "ok": False, "detail": f"unknown action {action}"}
