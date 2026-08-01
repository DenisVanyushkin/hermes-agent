import json
from datetime import datetime, timezone

from fam import audit, cli, diag


def test_audit_tick_error_records_exception_type(db):
    cli._audit_tick_error("reminders", KeyError("No item with that key"))
    row = db.execute(
        "SELECT payload FROM audit_log WHERE kind='tick.error'").fetchone()
    payload = json.loads(row["payload"])
    assert payload["where"] == "reminders"
    assert payload["exc_type"] == "KeyError"


def test_audit_tick_error_accepts_plain_string(db):
    # cli.py:1245 passes a joined string, not an exception -- exc_type is
    # None there rather than "str", which would be meaningless noise.
    cli._audit_tick_error("offsite", "backup failed; disk full")
    payload = json.loads(db.execute(
        "SELECT payload FROM audit_log WHERE kind='tick.error'").fetchone()["payload"])
    assert payload["exc_type"] is None
    assert "disk full" in payload["error"]


def test_normalize_signature_collapses_numbers_and_hex():
    assert diag.normalize_signature("row 4211 of abcdef1234567890") == \
        "row <n> of <hex>"


def test_identical_errors_collapse_into_one_finding(db):
    for _ in range(113):
        audit.log(db, "tick.error",
                  {"where": "meds_row", "intake_id": 10, "exc_type": "KeyError",
                   "error": "No item with that key"}, actor="tick")
    db.commit()
    findings = diag.collect_errors(db, "1970-01-01T00:00:00+00:00")
    assert len(findings) == 1
    assert findings[0]["count"] == 113
    assert findings[0]["kind"] == "tick.error"
    assert findings[0]["p_where"] == "meds_row"
    assert findings[0]["p_exc_type"] == "KeyError"
    assert findings[0]["context"]["intake_id"] == [10]


def test_same_defect_on_several_doses_stays_one_finding(db):
    for intake in (10, 11, 12):
        audit.log(db, "tick.error",
                  {"where": "meds_row", "intake_id": intake, "exc_type": "KeyError",
                   "error": "No item with that key"}, actor="tick")
    db.commit()
    findings = diag.collect_errors(db, "1970-01-01T00:00:00+00:00")
    assert len(findings) == 1, "intake_id must not split the signature"
    assert findings[0]["context"]["intake_id"] == [10, 11, 12]


def test_road_error_without_error_text_is_not_lost(db):
    # road.py writes {"event_id": ...} with no "error" key at all.
    audit.log(db, "road.error", {"event_id": 7})
    db.commit()
    findings = diag.collect_errors(db, "1970-01-01T00:00:00+00:00")
    assert len(findings) == 1
    assert findings[0]["kind"] == "road.error"
    assert findings[0]["context"]["event_id"] == [7]


def test_gate_error_never_exposes_message_text(db):
    audit.log(db, "gate.error",
              {"kind": "reminder", "attempt": 2,
               "raw": {"text": "прими лекарство"}, "final": "Прими лекарство"})
    db.commit()
    findings = diag.collect_errors(db, "1970-01-01T00:00:00+00:00")
    blob = json.dumps(findings, ensure_ascii=False)
    assert "лекарство" not in blob
    assert "final" not in blob and "raw" not in blob
    assert findings[0]["kind"] == "gate.error"
    assert findings[0]["p_kind"] == "reminder"


def test_unparseable_json_payload_still_yields_finding(db):
    # A malformed error row (non-JSON) must produce a finding, not silently
    # disappear -- it's exactly the kind of breakage this digest must surface.
    db.execute(
        "INSERT INTO audit_log (ts_utc, kind, payload, actor) "
        "VALUES (datetime('now'), 'tick.error', 'not json', 'test')")
    db.commit()
    findings = diag.collect_errors(db, "1970-01-01T00:00:00+00:00")
    assert len(findings) == 1
    assert findings[0]["kind"] == "tick.error"
    assert findings[0]["count"] == 1


def test_mail_error_event_id_in_context_and_text_in_signature(db):
    audit.log(db, "mail.error",
              {"event_id": 42, "error": "DNS resolution failed"}, actor="mail")
    db.commit()
    findings = diag.collect_errors(db, "1970-01-01T00:00:00+00:00")
    assert len(findings) == 1
    assert findings[0]["kind"] == "mail.error"
    assert findings[0]["context"]["event_id"] == [42]
    assert "DNS resolution failed" in findings[0]["examples"]
