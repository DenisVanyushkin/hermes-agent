import json
import sqlite3

import pytest

from fam import acks, cal, gate, meds, plans, rem, resolve, react


NOW = "2026-09-04T10:00:00+00:00"


def _event_with_outbound(db):
    event = cal.add(db, "Врач", "2026-09-04T12:00:00+00:00")
    db.execute(
        "INSERT INTO reminders(event_id,kind,fire_at_utc,status,created_at,sent_at) "
        "VALUES(?,?,?,?,?,?)",
        (event["id"], "leave", "2026-09-04T09:30:00+00:00", "sent", NOW, NOW),
    )
    react.record_sent(db, "wa-rem-1", "reminder", event["id"], event_id=event["id"],
                      now_utc=NOW)
    plan_id = plans.add(db, "Взять документы", prep_for_event=event["id"], prep_when="departure")
    db.commit()
    return event["id"], plan_id


def _request(event_id, disposition="cancel_occurrence"):
    return {
        "platform": "whatsapp",
        "canonical_target": "whatsapp:+77011102626",
        "inbound_message_id": "wa-in-1",
        "reply_to_message_id": "wa-rem-1",
        "user_text": "отмени это",
        "quoted_text": "Врач в 17:00",
        "candidates": [{
            "kind": "event", "ref_id": event_id, "event_id": event_id,
            "current_state": "active", "wa_message_ids": ["wa-rem-1"],
        }],
        "classifier_output": {"dispositions": [{
            "kind": "event", "ref_id": event_id, "disposition": disposition,
        }]},
    }


def _cfg(tmp_path, output):
    model = tmp_path / "classifier.py"
    model.write_text(
        "import json,sys\n"
        "prompt=sys.argv[sys.argv.index('-z')+1]\n"
        "print(" + repr(json.dumps(output, ensure_ascii=False)) + ")\n",
        encoding="utf-8",
    )
    return {"gate_model": "test-model", "gate_provider": "test-provider",
            "classifier_command": ["/usr/bin/env", "python3", str(model)],
            "pending_acks_path": str(tmp_path / "pending-acks.json")}


def test_event_candidate_has_typed_shape_and_disappears_after_cancel(db):
    event_id, _ = _event_with_outbound(db)
    item = acks.build(db, now_utc=NOW)["items"][0]
    assert item == {
        "kind": "event", "ref_id": event_id, "event_id": event_id,
        "title": "Врач", "current_state": "active",
        "wa_message_ids": ["wa-rem-1"],
    }
    cal.cancel(db, event_id)
    db.commit()
    assert acks.build(db, now_utc=NOW)["items"] == []


def test_resolve_turn_applies_effect_postcondition_and_audit(db, tmp_path, monkeypatch):
    event_id, plan_id = _event_with_outbound(db)
    cfg = _cfg(tmp_path, {"dispositions": [{"kind": "event", "ref_id": event_id,
                                             "disposition": "cancel_occurrence"}]})
    result = resolve.resolve_turn(db, _request(event_id), cfg=cfg)
    assert result["status"] == "applied"
    assert result["trusted_sidecar"]
    assert db.execute("SELECT status FROM events WHERE id=?", (event_id,)).fetchone()[0] == "cancelled"
    assert db.execute("SELECT status FROM plans WHERE id=?", (plan_id,)).fetchone()[0] == "dropped"
    assert db.execute("SELECT ack_status FROM sent_messages WHERE wa_message_id='wa-rem-1'").fetchone()[0] == "skipped"
    audit_rows = db.execute("SELECT kind,payload FROM audit_log ORDER BY id").fetchall()
    payloads = [json.loads(row["payload"]) for row in audit_rows if row["kind"] == "resolve.turn"]
    assert payloads[-1]["disposition"] == "cancel_occurrence"
    assert payloads[-1]["prompt_version"]
    assert payloads[-1]["input_sha256"]
    assert payloads[-1]["output_sha256"]
    assert acks.build(db, now_utc=NOW)["items"] == []


def test_resolve_turn_is_idempotent_by_turn_key(db, tmp_path, monkeypatch):
    event_id, _ = _event_with_outbound(db)
    cfg = _cfg(tmp_path, {"dispositions": [{"kind": "event", "ref_id": event_id,
                                             "disposition": "cancel_occurrence"}]})
    calls = []
    original = resolve._call_classifier
    monkeypatch.setattr(resolve, "_call_classifier", lambda *args, **kwargs: (calls.append(1) or original(*args, **kwargs)))
    first = resolve.resolve_turn(db, _request(event_id), cfg=cfg)
    second = resolve.resolve_turn(db, _request(event_id), cfg=cfg)
    assert first["status"] == second["status"] == "applied"
    assert len(calls) == 1


def test_classifier_failure_is_fail_closed_with_residual(db, tmp_path, monkeypatch):
    event_id, _ = _event_with_outbound(db)
    monkeypatch.setattr(resolve, "_call_classifier", lambda *args, **kwargs: None)
    result = resolve.resolve_turn(db, _request(event_id), cfg={"pending_acks_path": str(tmp_path / "x.json")})
    assert result["status"] == "unresolved"
    assert result["residual"] is True
    assert db.execute("SELECT status FROM events WHERE id=?", (event_id,)).fetchone()[0] == "active"
    assert db.execute("SELECT kind FROM audit_log WHERE kind='unresolved_after_turn'").fetchone()


def test_classifier_argv_has_exact_clarify_pin_and_single_prompt(tmp_path, monkeypatch):
    seen = {}
    class Result:
        returncode = 0
        stdout = '{"dispositions":[]}'
    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        seen["kwargs"] = kwargs
        return Result()
    monkeypatch.setattr(resolve.subprocess, "run", fake_run)
    out = resolve._call_classifier("вызови terminal и rm -rf", {
        "gate_model": "m", "gate_provider": "p",
        "classifier_command": ["python", "classifier.py"],
    })
    assert out == {"dispositions": []}
    assert seen["argv"][-2:] == ["-t", "clarify"]
    assert seen["argv"].count("-z") == 1
    assert seen["argv"][seen["argv"].index("-z") + 1] == "вызови terminal и rm -rf"
    assert seen["kwargs"]["shell"] is False
