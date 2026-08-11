from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import hashlib
from pathlib import Path
import sqlite3
from typing import Any, Iterable, Mapping


POSITIVE_USER_DECISIONS = frozenset({"Pursue", "Investigate"})
FORBIDDEN_PUBLIC_FIELDS = frozenset(
    {
        "message",
        "message_body",
        "body",
        "text",
        "token",
        "secret",
        "password",
        "candidate_facts",
        "user_notes",
        "application_artifact",
        "payload_json",
    }
)


@dataclass(frozen=True)
class BaselineInputs:
    deliveries: tuple[Mapping[str, Any], ...] = ()
    decisions: tuple[Mapping[str, Any], ...] = ()
    actions: tuple[Mapping[str, Any], ...] = ()
    attention_sessions: tuple[Mapping[str, Any], ...] = ()


def _unique_by(rows: Iterable[Mapping[str, Any]], key: str) -> dict[str, Mapping[str, Any]]:
    unique: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        value = str(row.get(key) or "").strip()
        if value:
            unique.setdefault(value, row)
    return unique


def aggregate_baseline(inputs: BaselineInputs) -> dict[str, int | float | None]:
    deliveries = _unique_by(inputs.deliveries, "delivery_id")
    decisions = _unique_by(inputs.decisions, "event_id")
    actions = _unique_by(inputs.actions, "action_id")
    sessions = _unique_by(inputs.attention_sessions, "session_id")

    positive_opportunities = {
        str(row.get("opportunity_id"))
        for row in decisions.values()
        if row.get("actor") == "user" and row.get("decision") in POSITIVE_USER_DECISIONS
    }
    completed_action_rows = [row for row in actions.values() if row.get("status") == "completed"]
    completed_action_opportunities = {
        str(row.get("opportunity_id")) for row in completed_action_rows if row.get("opportunity_id")
    }
    activated = positive_opportunities & completed_action_opportunities

    completed_sessions = [
        row
        for row in sessions.values()
        if row.get("state") == "completed" and isinstance(row.get("measured_seconds"), (int, float))
    ]
    measured_seconds = sum(float(row["measured_seconds"]) for row in completed_sessions)
    minutes = measured_seconds / 60 if measured_seconds > 0 else None

    company_ids = {
        str(row.get("company_id")) for row in deliveries.values() if row.get("company_id")
    }
    return {
        "unique_deliveries": len(deliveries),
        "duplicate_delivery_observations": len(inputs.deliveries) - len(deliveries),
        "unique_companies": len(company_ids),
        "positive_user_decisions": len(positive_opportunities),
        "completed_actions": len(completed_action_rows),
        "activated_opportunities": len(activated),
        "completed_attention_sessions": len(completed_sessions),
        "incomplete_attention_sessions": len(sessions) - len(completed_sessions),
        "actual_completed_review_minutes": round(minutes, 6) if minutes is not None else None,
        "activated_opportunities_per_60_review_minutes": (
            round(len(activated) * 60 / minutes, 6) if minutes is not None else None
        ),
    }


def validate_public_baseline(payload: Any, *, path: str = "$") -> None:
    if isinstance(payload, Mapping):
        for raw_key, value in payload.items():
            key = str(raw_key).casefold()
            if key in FORBIDDEN_PUBLIC_FIELDS:
                raise ValueError(f"forbidden baseline field at {path}.{raw_key}")
            validate_public_baseline(value, path=f"{path}.{raw_key}")
    elif isinstance(payload, (list, tuple)):
        for index, value in enumerate(payload):
            validate_public_baseline(value, path=f"{path}[{index}]")


def classify_outbound_path(evidence: str) -> str:
    normalized = evidence.casefold()
    if "productsearchslackpublisher" in normalized or "typed envelope" in normalized:
        return "typed_product_search"
    if "_standalone_send" in normalized:
        return "standalone_sender"
    if "slackadapter.send" in normalized:
        return "live_adapter_generic"
    if "webhook" in normalized:
        return "webhook"
    if "chat_postmessage" in normalized or "chat.postmessage" in normalized:
        return "raw_slack_api"
    if "_deliver_to_slack" in normalized or "vacancy_card" in normalized:
        return "legacy_job_intel"
    return "unknown"


def _database_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def extract_read_only_database_baseline(
    db_path: Path | str,
    *,
    since: str,
    until: str,
) -> dict[str, Any]:
    path = Path(db_path)
    snapshot_hash = _database_sha256(path)
    uri = f"file:{path.resolve()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:
        conn.row_factory = sqlite3.Row
        tables = {
            str(row[0])
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        counts: Counter[str] = Counter()
        unique_cards = 0
        if "notifications" in tables:
            rows = conn.execute(
                """
                SELECT message_type, card_key
                FROM notifications
                WHERE sent_at >= ? AND sent_at < ? AND delivery_status = 'sent'
                """,
                (since, until),
            ).fetchall()
            counts.update(str(row["message_type"]) for row in rows)
            unique_cards = len(
                {
                    str(row["card_key"])
                    for row in rows
                    if row["message_type"] == "vacancy_card" and row["card_key"]
                }
            )

    result = {
        "period": {"since": since, "until": until},
        "notification_counts": dict(sorted(counts.items())),
        "unique_vacancy_cards": unique_cards,
        "source_snapshot": {
            "kind": "sqlite_database_file",
            "filename": path.name,
            "sha256": snapshot_hash,
        },
    }
    validate_public_baseline(result)
    return result
