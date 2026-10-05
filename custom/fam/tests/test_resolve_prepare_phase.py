"""Incident 2026-10-05: "Я уже тут" quoted on a prepare-stage reminder.

The classifier picked ack_chain_prepare after the prepare stages were already
sent; rem.ack_chain(scope="prepare") touched 0 rows, the vacuous postcondition
reported "applied", and both leave-stage reminders still went out.
"""
import json

from fam import cal, react, resolve


NOW = "2026-10-05T04:20:47+00:00"


def _event(db, *, prepare_status):
    event = cal.add(db, "Тренировка в Invictus", "2099-10-05T05:00:00+00:00")
    rows = (
        ("prepare", "2026-10-05T04:09:00+00:00", "sent"),
        ("prepare", "2099-10-05T04:14:00+00:00", prepare_status),
        ("leave", "2099-10-05T04:31:00+00:00", "pending"),
        ("leave", "2099-10-05T04:46:00+00:00", "pending"),
    )
    for kind, fire_at, status in rows:
        db.execute(
            "INSERT INTO reminders(event_id,kind,fire_at_utc,status,created_at,sent_at) "
            "VALUES(?,?,?,?,?,?)",
            (event["id"], kind, fire_at, status, NOW,
             NOW if status == "sent" else None),
        )
    react.record_sent(db, "wa-prep", "reminder", event["id"],
                      event_id=event["id"], now_utc=NOW)
    db.commit()
    return event["id"]


def _cfg(tmp_path, event_id, disposition):
    classifier = tmp_path / "classifier.py"
    payload = json.dumps({"dispositions": [
        {"kind": "event", "ref_id": event_id, "disposition": disposition},
    ]})
    classifier.write_text(f"print({payload!r})\n", encoding="utf-8")
    return {"gate_model": "test-model", "gate_provider": "test-provider",
            "classifier_command": ["/usr/bin/env", "python3", str(classifier)],
            "pending_acks_path": str(tmp_path / "pending-acks.json")}


def _request(event_id):
    return {
        "platform": "whatsapp",
        "canonical_target": "whatsapp:+77011102626",
        "inbound_message_id": "wa-in-here",
        "reply_to_message_id": "wa-prep",
        "user_text": "Я уже тут",
        "quoted_text": "Пора собираться на тренировку в Invictus. Считаю от машины.",
        "now_utc": NOW,
        "candidates": [{
            "kind": "event", "ref_id": event_id, "event_id": event_id,
            "current_state": "active", "wa_message_ids": ["wa-prep"],
        }],
    }


def _pending(db, event_id, kind):
    return db.execute(
        "SELECT COUNT(*) FROM reminders WHERE event_id=? AND kind=? AND status='pending'",
        (event_id, kind),
    ).fetchone()[0]


def test_prepare_ack_after_prepare_phase_is_unresolved_not_applied(db, tmp_path):
    event_id = _event(db, prepare_status="sent")

    result = resolve.resolve_turn(
        db, _request(event_id), cfg=_cfg(tmp_path, event_id, "ack_chain_prepare"))

    assert result["status"] == "unresolved"
    assert "trusted_sidecar" not in result
    assert result["unresolved_refs"][0]["reason"] == "prepare_phase_over"
    assert _pending(db, event_id, "leave") == 2
    assert db.execute(
        "SELECT ack_status FROM sent_messages WHERE wa_message_id='wa-prep'"
    ).fetchone()[0] == "none"


def test_prepare_ack_during_prepare_phase_still_applies(db, tmp_path):
    event_id = _event(db, prepare_status="pending")

    result = resolve.resolve_turn(
        db, _request(event_id), cfg=_cfg(tmp_path, event_id, "ack_chain_prepare"))

    assert result["status"] == "applied"
    assert _pending(db, event_id, "prepare") == 0
    assert _pending(db, event_id, "leave") == 2


def test_already_there_ack_all_stops_leave_stages(db, tmp_path):
    event_id = _event(db, prepare_status="sent")

    result = resolve.resolve_turn(
        db, _request(event_id), cfg=_cfg(tmp_path, event_id, "ack_chain_all"))

    assert result["status"] == "applied"
    assert _pending(db, event_id, "leave") == 0


def test_classifier_prompt_routes_already_there_to_ack_all():
    descriptions = resolve._EVENT_DISPOSITION_DESCRIPTIONS
    assert "уже тут" in descriptions["ack_chain_all"]
    assert "leave" in descriptions["ack_chain_prepare"].lower()
