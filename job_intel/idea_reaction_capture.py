"""Capture Slack 👍 reactions on bot-authored idea posts into Google Docs.

This module is used by the Slack gateway and by the standalone CLI wrapper in
``scripts/slack_idea_reaction_capture.py``.

The workflow is intentionally tiny:

1. receive a Slack reaction event plus a small message payload
2. confirm the reaction is the configured trigger (default: 👍 / +1)
3. confirm the message came from one of the configured Slack channels
4. confirm the message was posted by the Hermes bot
5. append a compact bullet to the Google Doc "Идеи и улучшения"

The module keeps a lightweight state file to avoid duplicate appends when Slack
redelivers the same reaction event.
"""

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover - Python 3.8 fallback
    ZoneInfo = None  # type: ignore[assignment]


DEFAULT_DOC_ID = "13JgGfiTDJN0Vtyn7jjte6ELbnZY_4Mqu2bKFYs1dWo4"
DEFAULT_ALLOWED_CHANNELS = {"C0B55FPG5B7"}
DEFAULT_TRIGGER_REACTIONS = {"+1", "thumbsup", "thumbs_up"}


def _hermes_home() -> Path:
    raw = os.getenv("HERMES_HOME")
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".hermes"


def _state_file() -> Path:
    override = os.getenv("SLACK_IDEA_REACTION_STATE_FILE", "").strip()
    if override:
        return Path(override).expanduser()
    return _hermes_home() / "state" / "slack_idea_reactions.json"


def _load_state() -> dict[str, Any]:
    path = _state_file()
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"seen": []}


def _save_state(state: Mapping[str, Any]) -> None:
    path = _state_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(dict(state), ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def _split_csv(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [part.strip() for part in raw.split(",") if part.strip()]


def _normalise_reaction(raw: str | None) -> str:
    return (raw or "").strip().lower()


def _allowed_channels() -> set[str]:
    raw = os.getenv("SLACK_IDEA_REACTION_CHANNELS", "").strip()
    channels = set(_split_csv(raw))
    return channels or set(DEFAULT_ALLOWED_CHANNELS)


def _trigger_reactions() -> set[str]:
    raw = os.getenv("SLACK_IDEA_REACTION_EMOJIS", "").strip()
    reactions = set(_split_csv(raw))
    return reactions or set(DEFAULT_TRIGGER_REACTIONS)


def _doc_id() -> str:
    raw = os.getenv("SLACK_IDEA_DOC_ID", "").strip()
    return raw or DEFAULT_DOC_ID


def _is_bot_authored_message(message: Mapping[str, Any], bot_user_id: str | None) -> bool:
    if message.get("bot_id"):
        return True
    if message.get("subtype") == "bot_message":
        return True
    if bot_user_id and str(message.get("user") or "").strip() == bot_user_id:
        return True
    return False


def _extract_message_text(message: Mapping[str, Any]) -> str:
    text = str(message.get("text") or "").strip()
    if text:
        return " ".join(text.split())
    # Very small fallback: flatten block text if Slack delivered blocks but no
    # plain text field. This is intentionally conservative.
    blocks = message.get("blocks") or []
    parts: list[str] = []
    for block in blocks:
        if not isinstance(block, Mapping):
            continue
        if block.get("type") == "rich_text":
            for el in block.get("elements") or []:
                if not isinstance(el, Mapping):
                    continue
                if el.get("type") == "rich_text_section":
                    for child in el.get("elements") or []:
                        if not isinstance(child, Mapping):
                            continue
                        if child.get("type") == "text" and child.get("text"):
                            parts.append(str(child.get("text")))
    return " ".join(" ".join(parts).split())


def _current_almaty_timestamp() -> str:
    if ZoneInfo is None:
        return datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    return datetime.now(ZoneInfo("Asia/Almaty")).strftime("%Y-%m-%d %H:%M %Z")


def _append_to_doc(text: str) -> dict[str, Any]:
    doc_id = _doc_id()
    repo_script = Path(__file__).resolve().parents[1] / "skills" / "productivity" / "google-workspace" / "scripts" / "google_api.py"
    hermes_home = Path(os.getenv("HERMES_HOME", str(Path.home() / ".hermes"))).expanduser()
    home_script = hermes_home / "skills" / "productivity" / "google-workspace" / "scripts" / "google_api.py"
    script = repo_script if repo_script.exists() else home_script
    cmd = [os.environ.get("PYTHON", "python"), str(script), "docs", "append", doc_id, "--text", text]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "google docs append failed").strip())
    try:
        return json.loads(proc.stdout)
    except Exception:
        return {"raw": proc.stdout.strip()}


def process_event(
    event: Mapping[str, Any],
    *,
    message: Mapping[str, Any] | None = None,
    bot_user_id: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Process one Slack reaction event.

    Returns a small JSON-serialisable result dictionary.
    """

    event_type = str(event.get("type") or "").strip()
    if event_type != "reaction_added":
        return {"status": "ignored", "reason": "unsupported_event_type", "event_type": event_type}

    channel = str(((event.get("item") or {}).get("channel") or event.get("item_channel") or "")).strip()
    message_ts = str(((event.get("item") or {}).get("ts") or event.get("item_ts") or "")).strip()
    reaction = _normalise_reaction(event.get("reaction"))

    if not channel or not message_ts:
        return {"status": "ignored", "reason": "missing_message_reference", "channel": channel, "message_ts": message_ts}

    if channel not in _allowed_channels():
        return {"status": "ignored", "reason": "channel_not_allowed", "channel": channel}

    if reaction not in _trigger_reactions():
        return {"status": "ignored", "reason": "unsupported_reaction", "reaction": reaction}

    message = dict(message or {})
    if not message:
        return {"status": "ignored", "reason": "missing_message_payload", "channel": channel, "message_ts": message_ts}

    if not _is_bot_authored_message(message, bot_user_id):
        return {"status": "ignored", "reason": "not_bot_authored", "channel": channel, "message_ts": message_ts}

    state = _load_state()
    seen = set(str(item) for item in state.get("seen", []) if item)
    dedupe_key = f"{channel}:{message_ts}:{reaction}"
    if dedupe_key in seen:
        return {"status": "ignored", "reason": "duplicate", "dedupe_key": dedupe_key}

    text = _extract_message_text(message)
    if not text:
        return {"status": "ignored", "reason": "empty_message_text", "channel": channel, "message_ts": message_ts}

    source_bits = [f"channel {channel}", f"ts {message_ts}"]
    if message.get("user"):
        source_bits.append(f"user {message.get('user')}")
    if message.get("username"):
        source_bits.append(f"username {message.get('username')}")

    bullet = f"- [{_current_almaty_timestamp()}] {text} — source: {', '.join(source_bits)}"

    if dry_run:
        return {"status": "dry_run", "doc_id": _doc_id(), "append_text": bullet, "dedupe_key": dedupe_key}

    result = _append_to_doc(bullet + "\n")
    seen.add(dedupe_key)
    state["seen"] = sorted(seen)
    _save_state(state)
    return {"status": "ok", "doc_id": _doc_id(), "dedupe_key": dedupe_key, "doc_result": result}


def process_event_json(raw_json: str, *, bot_user_id: str | None = None, dry_run: bool = False) -> dict[str, Any]:
    data = json.loads(raw_json)
    message = data.get("message") if isinstance(data, dict) else None
    if message is not None and not isinstance(message, Mapping):
        raise TypeError("message must be a mapping when provided")
    if not isinstance(data, Mapping):
        raise TypeError("event payload must be a JSON object")
    return process_event(data, message=message, bot_user_id=bot_user_id, dry_run=dry_run)


def main(argv: list[str] | None = None) -> int:
    import argparse
    import sys

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event-file", help="Read the Slack event JSON from this file instead of stdin")
    parser.add_argument("--dry-run", action="store_true", help="Do not append to Google Docs")
    parser.add_argument("--bot-user-id", default=os.getenv("SLACK_BOT_USER_ID", "").strip() or None)
    args = parser.parse_args(argv)

    raw = Path(args.event_file).read_text(encoding="utf-8") if args.event_file else sys.stdin.read()
    result = process_event_json(raw, bot_user_id=args.bot_user_id, dry_run=args.dry_run)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entrypoint
    raise SystemExit(main())
