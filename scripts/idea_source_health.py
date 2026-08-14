"""Inspect, suspend, or reactivate idea-signal sources with an audit trail.

This tool deliberately does *not* edit the checked-in registry.  The registry
answers what a source is; the runtime health record answers whether it has been
automatically or manually admitted. Every manual transition requires a reason
and appends an immutable JSONL event.
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import yaml

try:
    from idea_signal_state import state_lock
except ModuleNotFoundError:  # Imported as scripts.idea_source_health in tests.
    from scripts.idea_signal_state import state_lock


ALLOWED_MANUAL_ACTIONS = frozenset({"suspend", "reactivate"})


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def default_hermes_home() -> Path:
    return Path(os.environ.get("HERMES_HOME") or (Path.home() / ".hermes"))


def default_state_dir() -> Path:
    return default_hermes_home() / "state"


def default_registry_path() -> Path:
    script_dir = Path(__file__).resolve().parent
    sibling = script_dir / "idea_sources.yaml"
    if sibling.is_file():
        return sibling
    return script_dir.parent / "config" / "idea_sources.yaml"


def load_state(state_dir: Path) -> dict:
    try:
        data = json.loads((state_dir / "idea_source_health.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def load_registry_source_states(registry_path: Path) -> dict[str, dict]:
    """Seed manual actions from reviewed registry entries before first run."""
    try:
        registry = yaml.safe_load(registry_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"cannot load registry: {exc}") from exc
    sources = registry.get("sources") if isinstance(registry, dict) else None
    if not isinstance(sources, list):
        raise ValueError("registry must contain a sources list")
    seeded: dict[str, dict] = {}
    for source in sources:
        if not isinstance(source, dict) or not str(source.get("id") or "").strip():
            continue
        source_id = str(source["id"])
        reviewed_status = str(source.get("status") or "candidate")
        seeded[source_id] = {
            # Registry admission is authoritative.  Keeping it in runtime state
            # prevents a manual suspend/reactivate toggle from promoting a
            # reviewed candidate to probation behind code review's back.
            "reviewed_status": reviewed_status,
            "effective_status": reviewed_status,
            "runs": 0,
            "successful_runs": 0,
            "items_seen": 0,
            "valid_date_items": 0,
            "accepted_items": 0,
            "duplicate_items": 0,
            "recent_results": [],
        }
    return seeded


def merge_registry_and_runtime_state(registry_state: dict, runtime_state: dict) -> dict:
    """Hydrate persisted health records with authoritative registry admission."""
    merged = {source_id: dict(record) for source_id, record in registry_state.items()}
    for source_id, saved_record in runtime_state.items():
        if source_id not in merged or not isinstance(saved_record, dict):
            merged[source_id] = saved_record
            continue
        if saved_record.get("reviewed_status") != registry_state[source_id]["reviewed_status"]:
            # A reviewed admission change (including active -> candidate)
            # invalidates old trial counters and effective admission.
            continue
        record = merged[source_id]
        record.update(saved_record)
        # Do not trust stale/tampered runtime state to rewrite reviewed status.
        record["reviewed_status"] = registry_state[source_id]["reviewed_status"]
    return merged


def apply_transition(
    state: dict,
    source_id: str,
    action: str,
    reason: str,
    *,
    now: datetime | None = None,
) -> tuple[dict, dict]:
    """Apply one explicit lifecycle action and return updated state + event."""
    reason = reason.strip()
    if not reason:
        raise ValueError("reason is required")
    if source_id not in state:
        raise ValueError(f"unknown source: {source_id}")
    if action not in ALLOWED_MANUAL_ACTIONS:
        raise ValueError(f"unsupported action: {action}")
    now = now or utc_now()
    updated = {key: dict(value) for key, value in state.items()}
    record = updated[source_id]
    previous_status = str(record.get("effective_status") or "probation")
    if action == "suspend":
        record["effective_status"] = "suspended"
        event_name = "suspended"
    else:
        if previous_status != "suspended":
            raise ValueError("only suspended sources can be reactivated")
        if record.get("reviewed_status") == "candidate":
            raise ValueError("candidate source must be reviewed into probation before reactivation")
        reviewed_status = record.get("reviewed_status")
        # Reactivation starts a fresh probation evaluation. Historical
        # transitions remain in idea_source_events.jsonl; counters are trial
        # metrics rather than the permanent source-history ledger.
        record = {
            "effective_status": "probation",
            "runs": 0,
            "successful_runs": 0,
            "items_seen": 0,
            "valid_date_items": 0,
            "accepted_items": 0,
            "duplicate_items": 0,
            "recent_results": [],
        }
        if reviewed_status is not None:
            record["reviewed_status"] = str(reviewed_status)
        updated[source_id] = record
        event_name = "reactivated"
    event = {
        "source_id": source_id,
        "event": event_name,
        "previous_status": previous_status,
        "reason": reason,
        "observed_at": now.isoformat(),
    }
    return updated, event


def persist_transition(state_dir: Path, state: dict, event: dict) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    health_path = state_dir / "idea_source_health.json"
    tmp_path = health_path.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp_path.replace(health_path)
    with (state_dir / "idea_source_events.jsonl").open("a", encoding="utf-8") as event_file:
        event_file.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, default=default_state_dir())
    parser.add_argument("--registry", type=Path, default=default_registry_path())
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("status", help="Print current source health JSON")
    for command in ("suspend", "reactivate"):
        transition = subparsers.add_parser(command)
        transition.add_argument("source_id")
        transition.add_argument("--reason", required=True)
    args = parser.parse_args(argv)
    with state_lock(args.state_dir):
        registry_state = load_registry_source_states(args.registry)
        state = merge_registry_and_runtime_state(registry_state, load_state(args.state_dir))
        if args.command == "status":
            print(json.dumps(state, ensure_ascii=False, indent=2))
            return 0
        try:
            updated, event = apply_transition(state, args.source_id, args.command, args.reason)
        except ValueError as exc:
            parser.error(str(exc))
        persist_transition(args.state_dir, updated, event)
        print(json.dumps(event, ensure_ascii=False, indent=2))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
