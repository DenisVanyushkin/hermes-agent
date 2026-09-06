"""Focused regressions for external-calendar review round two."""

import pytest

from fam import audit, cal, extcal, gate, people, plans, series

HERMES = "https://caldav.icloud.com/1/calendars/hermes/"
TAYA = "https://caldav.icloud.com/1/calendars/taya/"
NOW = "2037-07-15T00:00:00+00:00"


def cfg(**over):
    value = dict(gate.CONFIG_DEFAULTS)
    value.update({
        "extcal_enabled": True,
        "extcal_username": "amina@example.com",
        "extcal_write_calendar": HERMES,
        "extcal_taya_calendar": TAYA,
        "extcal_horizon_weeks": 8,
    })
    value.update(over)
    return value


def event(db, subject=None, title="Local", start="2037-07-20T13:00:00+00:00"):
    row = cal.add(db, title, start, end_utc="2037-07-20T14:00:00+00:00",
                  subject_person_id=subject)
    db.commit()
    return row


def taya(db):
    row = people.add(db, "Тая", slug="taya")
    db.commit()
    return row


def journal(db, table, row, body_hash=None, etag='"e0"', url=None):
    body_hash = body_hash or extcal._export_body_hash(row, "", [])
    url = url or (TAYA if table == "ext_exports_taya" else HERMES)
    db.execute(
        f"INSERT INTO {table}(event_id, href, etag, body_hash, synced_at) "
        "VALUES(?,?,?,?,?)",
        (row["id"], f"{url}fam-{row['id']}@hermes-home.ics", etag,
         body_hash, NOW),
    )
    db.commit()


def ics(event_id, title="Local", start="20370720T130000Z",
        end="20370720T140000Z"):
    return ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\n"
            f"UID:fam-{event_id}@hermes-home\r\n"
            "DTSTAMP:20370715T000000Z\r\n"
            f"DTSTART:{start}\r\nDTEND:{end}\r\nSUMMARY:{title}\r\n"
            "END:VEVENT\r\nEND:VCALENDAR\r\n").encode()


def issue(db, row, target="hermes", action="put", kind="error"):
    reason = "conflict" if kind == "conflict" else "export_error"
    status = 412 if kind == "conflict" else 500
    db.execute(
        "INSERT INTO extcal_export_issues(target,event_id,action,kind,"
        "http_status,reason_code,first_seen_utc,last_seen_utc) "
        "VALUES(?,?,?,?,?,?,?,?)",
        (target, row["id"], action, kind, status, reason, NOW, NOW),
    )
    db.commit()


def test_initial_put_without_etag_uses_create_precondition_on_both_ticks(db):
    row = event(db)
    puts = []

    def request(method, url, **kwargs):
        if method == "PUT":
            puts.append(kwargs["headers"])
            if len(puts) == 1:
                return extcal.Response(201, b"", {})
            return extcal.Response(412, b"", {})
        return extcal.Response(404, b"", {})

    extcal.export_routes(db, cfg(), request=request, now_utc=NOW)
    extcal.export_routes(db, cfg(), request=request, now_utc=NOW)

    assert len(puts) == 2
    assert all(headers.get("If-None-Match") == "*" for headers in puts)
    assert db.execute(
        "SELECT 1 FROM ext_exports WHERE event_id=?", (row["id"],)
    ).fetchone() is None


def test_route_transition_does_not_delete_source_after_destination_recreate_fails(db):
    subject = taya(db)
    row = event(db, subject=subject["id"])
    journal(db, "ext_exports", row)
    journal(db, "ext_exports_taya", row)
    before_source = [tuple(r) for r in db.execute(
        "SELECT * FROM ext_exports ORDER BY event_id"
    ).fetchall()]
    calls = []

    def request(method, url, **kwargs):
        calls.append(method)
        if method == "GET":
            return extcal.Response(404, b"", {})
        if method == "MOVE":
            return extcal.Response(500, b"", {})
        pytest.fail("source DELETE or PUT must not follow failed MOVE")

    counts = extcal.export_routes(db, cfg(), request=request, now_utc=NOW)

    assert calls == ["GET", "GET", "MOVE"]
    assert counts["deleted"] == 0
    assert [tuple(r) for r in db.execute(
        "SELECT * FROM ext_exports ORDER BY event_id"
    ).fetchall()] == before_source
    assert db.execute("SELECT COUNT(*) FROM ext_exports_taya").fetchone()[0] == 0



def test_route_transition_detects_remote_destination_edit_before_source_delete(db):
    subject = taya(db)
    row = event(db, subject=subject["id"])
    body_hash = extcal._export_body_hash(row, "", [])
    journal(db, "ext_exports", row, body_hash=body_hash)
    journal(db, "ext_exports_taya", row, body_hash=body_hash)
    calls = []

    def request(method, url, **kwargs):
        calls.append(method)
        if method == "GET":
            return extcal.Response(200, ics(row["id"], title="Phone edit"),
                                   {"ETag": '"phone"'})
        pytest.fail("remote destination edit must quarantine before DELETE")

    counts = extcal.export_routes(db, cfg(), request=request, now_utc=NOW)

    assert calls == ["GET"]
    assert counts["deleted"] == 0
    assert counts["conflicts"]
    assert db.execute(
        "SELECT 1 FROM ext_exports WHERE event_id=?", (row["id"],)
    ).fetchone() is not None


def test_series_cancel_tombstones_issue_only_occurrence_instead_of_fk_delete(db):
    sid = series.add(db, "series", "tue", "10:00", until_local="2037-07-21")
    series.generate(db, now_utc=NOW)
    row = db.execute(
        "SELECT * FROM events WHERE series_id=? AND status='active' "
        "ORDER BY start_utc LIMIT 1", (sid["id"],)
    ).fetchone()
    issue(db, row)

    removed = series.cancel(db, sid["id"], now_utc=NOW)

    assert removed == 1
    assert db.execute(
        "SELECT status FROM events WHERE id=?", (row["id"],)
    ).fetchone()[0] == "cancelled"
    assert db.execute(
        "SELECT 1 FROM extcal_export_issues WHERE event_id=?", (row["id"],)
    ).fetchone() is not None

    calls = []
    def request(method, url, **kwargs):
        calls.append(method)
        if method == "GET":
            return extcal.Response(200, ics(row["id"], title="series"),
                                   {"ETag": '"orphan"'})
        if method == "DELETE":
            return extcal.Response(204, b"", {})
        pytest.fail("cancelled issue cleanup must not write or probe another method")

    counts = extcal.export_routes(db, cfg(), request=request, now_utc=NOW)

    assert calls == ["GET", "DELETE"]
    assert counts["deleted"] == 1
    assert db.execute(
        "SELECT 1 FROM extcal_export_issues WHERE event_id=?", (row["id"],)
    ).fetchone() is None



def test_series_cancel_clears_issue_when_orphan_resource_is_already_gone(db):
    sid = series.add(db, "series", "tue", "10:00", until_local="2037-07-21")
    series.generate(db, now_utc=NOW)
    row = db.execute(
        "SELECT * FROM events WHERE series_id=? AND status='active' "
        "ORDER BY start_utc LIMIT 1", (sid["id"],)
    ).fetchone()
    issue(db, row)
    series.cancel(db, sid["id"], now_utc=NOW)

    calls = []
    def request(method, url, **kwargs):
        calls.append(method)
        assert method == "GET"
        return extcal.Response(404, b"", {})

    counts = extcal.export_routes(db, cfg(), request=request, now_utc=NOW)

    assert calls == ["GET"]
    assert counts["deleted"] == 1
    assert db.execute(
        "SELECT 1 FROM extcal_export_issues WHERE event_id=?", (row["id"],)
    ).fetchone() is None


def test_series_cancel_preserves_plan_links_for_tombstoned_occurrence(db):
    sid = series.add(db, "series", "tue", "10:00", until_local="2037-08-01")
    series.generate(db, now_utc=NOW)
    row = db.execute(
        "SELECT * FROM events WHERE series_id=? AND status='active' "
        "ORDER BY start_utc LIMIT 1", (sid["id"],)
    ).fetchone()
    journal(db, "ext_exports", dict(row))
    prep_id = plans.add(db, "prep", prep_for_event=row["id"],
                        prep_when="departure")
    attached_id = plans.add(db, "attached")
    db.execute("UPDATE plans SET attached_event_id=? WHERE id=?",
               (row["id"], attached_id))
    db.commit()

    series.cancel(db, sid["id"], now_utc=NOW)

    links = db.execute(
        "SELECT id, prep_for_event_id, attached_event_id FROM plans "
        "WHERE id IN (?,?) ORDER BY id", (prep_id, attached_id)
    ).fetchall()
    assert links[0]["prep_for_event_id"] == row["id"]
    assert links[1]["attached_event_id"] == row["id"]
    assert db.execute(
        "SELECT status FROM plans WHERE id=?", (prep_id,)
    ).fetchone()[0] == "dropped"


def test_delete_resolve_recovers_after_local_commit_failure(db, monkeypatch):
    row = event(db)
    journal(db, "ext_exports", row)
    issue(db, row, action="delete", kind="conflict")
    calls = []
    fail_audit = {"value": True}

    def request(method, url, **kwargs):
        calls.append(method)
        if method == "GET":
            if fail_audit["value"]:
                return extcal.Response(200, ics(row["id"]), {"ETag": '"e0"'})
            return extcal.Response(404, b"", {})
        return extcal.Response(204, b"", {})

    original_log = audit.log
    def log_once(conn, kind, payload):
        if fail_audit["value"] and kind == "cal.ext.resolve":
            raise RuntimeError("local commit window")
        return original_log(conn, kind, payload)

    monkeypatch.setattr(extcal.audit, "log", log_once)
    with pytest.raises(RuntimeError, match="local commit window"):
        extcal.resolve_conflict(db, row["id"], decision="force-push",
                                cfg=cfg(), request=request, now_utc=NOW)
    db.rollback()
    fail_audit["value"] = False

    result = extcal.resolve_conflict(db, row["id"], decision="force-push",
                                     cfg=cfg(), request=request, now_utc=NOW)

    assert result["resolved"] is True
    assert calls == ["GET", "DELETE", "GET"]
    assert db.execute(
        "SELECT 1 FROM ext_exports WHERE event_id=?", (row["id"],)
    ).fetchone() is None
    assert db.execute(
        "SELECT 1 FROM extcal_export_issues WHERE event_id=?", (row["id"],)
    ).fetchone() is None


def test_export_success_count_is_not_kept_when_post_write_commit_fails(db, monkeypatch):
    counts = {"exported": 0, "errors": []}

    def fail_audit(conn, kind, payload):
        raise RuntimeError("audit write failed")

    monkeypatch.setattr(extcal.audit, "log", fail_audit)
    extcal._export_commit_one(
        db, 1, "insert", "exported", counts, lambda: None, now_utc=NOW)

    assert counts["exported"] == 0
    assert len(counts["errors"]) == 1
