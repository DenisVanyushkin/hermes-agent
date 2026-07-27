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
# Every value that means "run baseline-doctor". Slack delivers bare emoji names,
# Telegram/WhatsApp deliver the unicode glyph. 🧹 is a premium emoji on WhatsApp
# and cannot be sent there, so free alternatives are accepted too.
TRIGGER_REACTIONS = {
    "broom", "\U0001F9F9",
    "recycle", "\u267B\uFE0F", "\u267B",
    "ok_hand", "\U0001F44C",
}
_COMMANDS = {
    "run": {"/baseline-doctor", "baseline-doctor", "\u043f\u043e\u0447\u0438\u0441\u0442\u0438"},
    "commit": {"\u0437\u0430\u043a\u043e\u043c\u043c\u0438\u0442\u044c \u0432\u0441\u0451", "\u0437\u0430\u043a\u043e\u043c\u043c\u0438\u0442\u044c \u0432\u0441\u0435", "commit"},
    "gitignore": {"gitignore", "\u0432 gitignore"},
    "stash": {"stash", "\u0432 stash"},
}


def _normalize_reaction(value: str | None) -> str:
    return (value or "").strip().strip(":").lower()


def is_trigger_reaction(value: str | None) -> bool:
    """True when the reaction means "run baseline-doctor" on any platform."""
    return _normalize_reaction(value) in TRIGGER_REACTIONS


def parse_doctor_command(text: str | None) -> str | None:
    """Map an exact operator text command to an action, else None.

    Exact-match only: a bare "\u043f\u043e\u0447\u0438\u0441\u0442\u0438" is the trigger, but "\u043f\u043e\u0447\u0438\u0441\u0442\u0438 \u0431\u0430\u0437\u0443" is
    an ordinary request and must reach the agent untouched.
    """
    normalized = (text or "").strip().lower().rstrip("!.")
    if not normalized:
        return None
    for action, phrases in _COMMANDS.items():
        if normalized in phrases:
            return action
    return None
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
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, encoding="utf-8")


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


# Pending key for the text-command path, which has no Slack message ts.
TEXT_PENDING_KEY = "text"


def run_doctor(repo: Path | None = None) -> dict:
    """Run scripts/baseline_doctor.py against the agent repo."""
    import importlib.util

    target = repo or Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location(
        "baseline_doctor", target / "scripts" / "baseline_doctor.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.run_doctor(target)


def render_report(result: dict, *, reactions: bool = True) -> str:
    lines = ["\U0001F9F9 Baseline doctor"]
    if result["fixed"]:
        lines.append(f"\u2705 Fixed: chowned {len(result['fixed'])} root-owned file(s)")
    if result["clean"]:
        lines.append("\u2705 Baseline clean \u2014 retry the request.")
        return "\n".join(lines)
    lines.append("\u26a0\ufe0f Remaining (blocks run):")
    for r in result["remaining"]:
        lines.append(f"  \u2022 {r['path']} [{r['category']}] \u2014 {r.get('hint','')}")
    if reactions:
        lines.append("React:  \U0001F4E5 commit all \u00b7 \U0001F648 gitignore all \u00b7 \U0001F4E6 stash all")
    else:
        lines.append("Reply:  commit \u00b7 gitignore \u00b7 stash")
    return "\n".join(lines)
