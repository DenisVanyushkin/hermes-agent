"""S3: current export issues, privacy and lifecycle."""
import json
import sqlite3
import types
from datetime import datetime

import pytest

from fam import cal, cli, extcal, gate, health, maint
from fam import db as famdb


WRITE_URL = "https://caldav.icloud.com/1/calendars/hermes/"
TEST_NOW = "2037-07-15T00:00:00+00:00"


def _cfg(**over):
    cfg = dict(gate.CONFIG_DEFAULTS)
    cfg.update({
        "extcal_enabled": True,
        "extcal_username": "amina@example.com",
        "extcal_read_calendars": [],
        "extcal_write_calendar": WRITE_URL,
        "extcal_horizon_weeks": 8,
        "extcal_fail_streak_threshold": 1,
    })
    cfg.update(over)
    return cfg


def _event(conn, title="Fixture secret title", start="2037-07-20T13:00:00+00:00"):
    event = cal.add(conn, title, start)
    conn.commit()
    return event


def _fail_request(method, url, **kwargs):
    return extcal.Response(500, b"SECRET ICS BODY", {})


def _ok_request(method, url, **kwargs):
    return extcal.Response(201, b"", {"ETag": '"e1"'})


def _insert_issue(conn, event_id, action="put", kind="error",
                  reason_code="export_error", status=500,
                  first="2037-07-15T00:00:00.000000+00:00",
                  last="2037-07-15T00:00:00.000000+00:00"):
    conn.execute(
        "INSERT INTO extcal_export_issues("
        "target,event_id,action,kind,http_status,reason_code,"
        "first_seen_utc,last_seen_utc) VALUES(?,?,?,?,?,?,?,?)",
        ("hermes", event_id, action, kind, status, reason_code, first, last),
    )
    conn.commit()


def _issue(conn, event_id):
    row = conn.execute(
        "SELECT * FROM extcal_export_issues WHERE target='hermes' AND event_id=?",
        (event_id,),
    ).fetchone()
    return dict(row) if row else None


def test_failed_put_creates_one_issue_and_repeat_keeps_first_seen(db):
    event = _event(db)
    href = f"{WRITE_URL}fam-{event['id']}@hermes-home.ics"

    first_counts = extcal.export_own(
        db, _cfg(), request=_fail_request, now_utc=TEST_NOW)
    first = _issue(db, event["id"])
    second_counts = extcal.export_own(
        db, _cfg(), request=_fail_request, now_utc=TEST_NOW)
    second = _issue(db, event["id"])

    assert len(first_counts["errors"]) == len(second_counts["errors"]) == 1
    assert first["target"] == "hermes"
    assert first["event_id"] == event["id"]
    assert first["action"] == second["action"] == "put"
    assert first["kind"] == second["kind"] == "error"
    assert first["reason_code"] == second["reason_code"] == "export_error"
    assert first["http_status"] == second["http_status"] == 500
    assert first["first_seen_utc"] == second["first_seen_utc"]
    assert second["last_seen_utc"] >= first["last_seen_utc"]
    assert href not in json.dumps(second, ensure_ascii=False)
    assert "Fixture secret title" not in json.dumps(second, ensure_ascii=False)
    assert "SECRET ICS BODY" not in json.dumps(second, ensure_ascii=False)


def test_success_and_retained_transition_remove_issue(db):
    event = _event(db)
    extcal.export_own(db, _cfg(), request=_fail_request, now_utc=TEST_NOW)
    assert _issue(db, event["id"]) is not None

    extcal.export_own(db, _cfg(), request=_ok_request, now_utc=TEST_NOW)
    assert _issue(db, event["id"]) is None

    past = _event(db, "Past fixture", "2037-07-10T13:00:00+00:00")
    href = f"{WRITE_URL}fam-{past['id']}@hermes-home.ics"
    db.execute(
        "INSERT INTO ext_exports(event_id,href,etag,body_hash,synced_at) "
        "VALUES(?,?,?,?,?)",
        (past["id"], href, '"e0"', "hash", TEST_NOW),
    )
    db.commit()
    _insert_issue(db, past["id"])

    calls = []
    extcal.export_own(
        db, _cfg(), request=lambda *a, **k: calls.append(a),
        now_utc=TEST_NOW)
    assert calls == []
    assert _issue(db, past["id"]) is None


def test_issue_db_constraints_include_delete_restrict_and_all_enum_checks(db):
    event = _event(db)
    invalids = [
        ("bad-target", event["id"], "put", "error", 500, "export_error"),
        ("hermes", event["id"], "bad-action", "error", 500, "export_error"),
        ("hermes", event["id"], "put", "bad-kind", 500, "export_error"),
        ("hermes", event["id"], "put", "error", 500, "raw exception with href"),
    ]
    for values in invalids:
        row = values + (TEST_NOW, TEST_NOW)
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                "INSERT INTO extcal_export_issues("
                "target,event_id,action,kind,http_status,reason_code,"
                "first_seen_utc,last_seen_utc) VALUES(?,?,?,?,?,?,?,?)", row)
        db.rollback()

    _insert_issue(db, event["id"])
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("DELETE FROM events WHERE id=?", (event["id"],))
    db.rollback()
    assert _issue(db, event["id"]) is not None


def test_extcal_failures_is_pure_safe_and_registered(db):
    event = _event(db, "Title secret", "2037-07-20T13:00:00+00:00")
    href = f"{WRITE_URL}fam-{event['id']}@hermes-home.ics"
    _insert_issue(db, event["id"])
    famdb.meta_set(db, "extcal_fail_streak:__import_apply__", "2")
    famdb.meta_set(db, "extcal_fail_streak:__export__", "3")
    db.commit()
    before = dict(db.execute(
        "SELECT key, value FROM meta").fetchall())
    result = health.extcal_failures(
        db, _cfg(), now_utc=TEST_NOW)

    full_text = json.dumps(result, ensure_ascii=False, sort_keys=True)
    assert result["status"] == "degraded"
    assert str(event["id"]) in full_text
    assert "put" in full_text
    assert href not in full_text
    assert "Title secret" not in full_text
    assert "SECRET ICS BODY" not in full_text
    assert dict(db.execute(
        "SELECT key, value FROM meta").fetchall()) == before
    assert "extcal_failures" in {
        probe["name"] for probe in health.all_probes(db, _cfg(), TEST_NOW)
    }


def test_issue_audit_payload_is_safe_and_no_gate_delivery_or_amina_message(
        db, monkeypatch):
    event = _event(db, "Audit title secret")
    calls = []
    monkeypatch.setattr(gate, "deliver",
                        lambda *a, **k: calls.append(("deliver", a, k)))
    counts = extcal.export_own(
        db, _cfg(), request=_fail_request, now_utc=TEST_NOW)
    rows = db.execute(
        "SELECT payload FROM audit_log WHERE kind='cal.ext.export_error'"
    ).fetchall()
    full_text = json.dumps([json.loads(row["payload"]) for row in rows],
                           ensure_ascii=False)
    assert counts["errors"]
    assert "Audit title secret" not in full_text
    assert WRITE_URL not in full_text
    assert "SECRET ICS BODY" not in full_text
    assert calls == []


def test_active_issue_holds_last_ok_until_clean_resolution(db, monkeypatch):
    event = _event(db, "Night secret", "2037-07-20T13:00:00+00:00")
    cfg = _cfg()
    monkeypatch.setattr(cli.gate, "load_config", lambda *a, **k: cfg)
    monkeypatch.setattr(cli.extcal, "discover", lambda cfg, request=None: [])
    monkeypatch.setattr(cli.gate, "deliver",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("no gate delivery")))
    mode = {"fail": True}

    def fake_open(req, timeout):
        if mode["fail"]:
            return extcal.Response(500, b"SECRET ICS BODY", {})
        return extcal.Response(201, b"", {"ETag": '"e2"'})

    monkeypatch.setattr(cli.extcal, "_default_open", fake_open)
    old_ok = "2037-07-14T00:00:00+00:00"
    famdb.meta_set(db, "extcal_last_ok", old_ok)
    db.commit()

    assert cli.cmd_tick_cal_ext(types.SimpleNamespace(now=TEST_NOW)) == 1
    assert famdb.meta_get(db, "extcal_last_ok") == old_ok
    assert _issue(db, event["id"]) is not None
    audit_text = json.dumps([
        json.loads(row["payload"])
        for row in db.execute(
            "SELECT payload FROM audit_log WHERE kind LIKE 'cal.ext.%'"
        ).fetchall()
    ], ensure_ascii=False)
    assert "Night secret" not in audit_text
    assert WRITE_URL not in audit_text
    assert "SECRET ICS BODY" not in audit_text

    assert cli.cmd_tick_cal_ext(types.SimpleNamespace(now=TEST_NOW)) == 0
    assert db.execute(
        "SELECT COUNT(*) FROM audit_log WHERE kind='tick.error'"
    ).fetchone()[0] == 1

    mode["fail"] = False
    assert cli.cmd_tick_cal_ext(types.SimpleNamespace(now=TEST_NOW)) == 0
    assert _issue(db, event["id"]) is None
    assert famdb.meta_get(db, "extcal_last_ok") == TEST_NOW


def test_nightly_problem_line_is_one_safe_aggregated_line(db, tmp_path, monkeypatch):
    secret = "https://secret.example/title-secret.ics SECRET ICS BODY"
    digest = {
        "generated_at": TEST_NOW,
        "sections": {
            "probes": [{
                "name": "extcal_failures",
                "status": "degraded",
                "detail": "экспорт iCloud залип: 2 события (7, 8), DELETE 412",
            }],
        },
        "section_errors": {},
    }
    monkeypatch.setattr(maint.diag, "build_digest",
                        lambda *a, **k: (digest, {}))
    monkeypatch.setattr(maint.diag, "write_digest",
                        lambda *a, **k: tmp_path / "digest.json")
    monkeypatch.setattr(maint.gate, "deliver",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("no gate delivery")))
    sent = []
    result = maint.problem_summary(
        {**_cfg(), "diagnostics_dir": str(tmp_path),
         "report_jobs_path": str(tmp_path / "missing-jobs.json")},
        now=datetime.fromisoformat(TEST_NOW),
        notify=lambda text: sent.append(text) or True)
    text = sent[0]
    assert result["fallback_sent"] is True
    assert text.count("экспорт iCloud залип:") == 1
    assert secret not in text
    assert "href" not in text and "ICS" not in text.upper()
