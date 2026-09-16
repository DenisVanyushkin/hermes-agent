"""Gateway-side reader for fam's pending-ack snapshot.

The gateway injects a short note about unanswered questions (currently:
medication doses) into every turn's sidecar, so the context survives a
session reset. It deliberately knows nothing about fam -- it reads a small
JSON file fam writes, matches the target channel, and renders a note.

Regression: 2026-07-23, daily reset landed between the 09:00 dose reminder
and the 09:25 "Готово"; history=0, no tool call, dose stayed pending.
"""
import json

import pytest

from gateway.run import _pending_acks_note, _read_pending_acks

NOW = "2026-07-23T04:25:00+00:00"

SNAPSHOT = {
    "generated_at": "2026-07-23T04:24:00+00:00",
    "target": "whatsapp:+77011102626",
    "items": [{
        "kind": "med_intake",
        "id": 3,
        "name": "мисол",
        "dose": "1 таблетка",
        "plan_ts_utc": "2026-07-23T04:00:00+00:00",
        "due_local": "09:00",
        "ack_cmd": "fam med taken 3",
        "skip_cmd": "fam med skip 3",
    }],
}


def test_note_names_the_dose_and_both_commands():
    note = _pending_acks_note(SNAPSHOT, "whatsapp", "77011102626", now_utc=NOW)
    assert note is not None
    assert "мисол" in note
    assert "09:00" in note
    assert "fam med taken 3" in note
    assert "fam med skip 3" in note


def test_note_reaches_the_lid_addressed_turn(tmp_path, monkeypatch):
    """A phone-addressed snapshot must match the inbound WhatsApp LID."""
    mapping_dir = tmp_path / "whatsapp" / "session"
    mapping_dir.mkdir(parents=True)
    (mapping_dir / "lid-mapping-244882006364348_reverse.json").write_text(
        json.dumps("77011102626"), encoding="utf-8"
    )
    monkeypatch.setattr(
        "gateway.whatsapp_identity.get_hermes_dir",
        lambda *_args: mapping_dir,
    )

    note = _pending_acks_note(
        SNAPSHOT, "whatsapp", "244882006364348@lid", now_utc=NOW
    )

    assert note is not None
    assert "мисол" in note


def test_no_note_for_an_unresolved_lid(tmp_path, monkeypatch):
    """An unknown LID must not broaden into another WhatsApp identity."""
    mapping_dir = tmp_path / "whatsapp" / "session"
    mapping_dir.mkdir(parents=True)
    monkeypatch.setattr(
        "gateway.whatsapp_identity.get_hermes_dir",
        lambda *_args: mapping_dir,
    )

    assert _pending_acks_note(
        SNAPSHOT, "whatsapp", "999999999999999@lid", now_utc=NOW
    ) is None


def test_no_note_for_a_different_channel():
    """Denis's admin channel must not receive Amina's open questions."""
    assert _pending_acks_note(SNAPSHOT, "telegram", "79564752", now_utc=NOW) is None
    assert _pending_acks_note(SNAPSHOT, "whatsapp", "77012110625", now_utc=NOW) is None


def test_no_note_when_nothing_is_pending():
    snap = dict(SNAPSHOT, items=[])
    assert _pending_acks_note(snap, "whatsapp", "77011102626", now_utc=NOW) is None


def test_no_note_when_snapshot_is_stale():
    """If the tick died, silence beats nagging about a resolved dose."""
    snap = dict(SNAPSHOT, generated_at="2026-07-23T01:00:00+00:00")
    assert _pending_acks_note(snap, "whatsapp", "77011102626", now_utc=NOW) is None


def test_no_target_means_no_note():
    snap = dict(SNAPSHOT, target="")
    assert _pending_acks_note(snap, "whatsapp", "77011102626", now_utc=NOW) is None


def test_malformed_snapshot_is_ignored():
    for bad in (None, {}, {"items": "nope"}, {"target": 5, "items": [{}]}):
        assert _pending_acks_note(bad, "whatsapp", "77011102626", now_utc=NOW) is None


def test_multiple_items_are_all_listed():
    snap = dict(SNAPSHOT, items=SNAPSHOT["items"] + [{
        "kind": "med_intake", "id": 4, "name": "магний", "dose": "",
        "plan_ts_utc": "2026-07-23T04:00:00+00:00", "due_local": "09:00",
        "ack_cmd": "fam med taken 4", "skip_cmd": "fam med skip 4",
    }])
    note = _pending_acks_note(snap, "whatsapp", "77011102626", now_utc=NOW)
    assert "мисол" in note and "магний" in note


# ---- file reading ----

def test_read_returns_parsed_snapshot(tmp_path):
    path = tmp_path / "pending-acks.json"
    path.write_text(json.dumps(SNAPSHOT), encoding="utf-8")
    assert _read_pending_acks(str(path)) == SNAPSHOT


def test_read_missing_or_broken_file_is_none(tmp_path):
    assert _read_pending_acks(str(tmp_path / "nope.json")) is None
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    assert _read_pending_acks(str(broken)) is None
    assert _read_pending_acks("") is None
