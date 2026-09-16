"""Focused correctness regressions from the external S1-S6 review."""

import json

import pytest

from fam import cal, extcal, gate, health, people, series

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


def event(db, subject_person_id=None, title="Local", start="2037-07-20T13:00:00+00:00"):
    row = cal.add(
        db, title, start, end_utc="2037-07-20T14:00:00+00:00",
        subject_person_id=subject_person_id,
    )
    db.commit()
    return row


def seed_taya(db):
    taya = people.add(db, "Тая", slug="taya")
    db.commit()
    return taya


def ics(event_id, title="Remote", start="20370720T130000Z",
        end="20370720T140000Z", uid=None, description=None, alarm=False):
    uid = uid or f"fam-{event_id}@hermes-home"
    lines = [
        "BEGIN:VCALENDAR", "VERSION:2.0", "BEGIN:VEVENT",
        f"UID:{uid}", "DTSTAMP:20370715T000000Z",
        f"DTSTART:{start}", f"DTEND:{end}", f"SUMMARY:{title}",
    ]
    if description is not None:
        lines.append(f"DESCRIPTION:{description}")
    if alarm:
        lines += [
            "BEGIN:VALARM", "ACTION:DISPLAY", "DESCRIPTION:ring",
            "TRIGGER:-PT10M", "END:VALARM",
        ]
    lines += ["END:VEVENT", "END:VCALENDAR"]
    return "\r\n".join(lines) + "\r\n"


def seed_journal(db, table, row, etag='"e0"', body_hash=None):
    body_hash = body_hash or extcal._export_body_hash(row, "", [])
    db.execute(
        f"INSERT INTO {table}(event_id, href, etag, body_hash, synced_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (row["id"], f"{HERMES if table == 'ext_exports' else TAYA}"
         f"fam-{row['id']}@hermes-home.ics", etag, body_hash, NOW),
    )
    db.commit()


def seed_issue(db, row, target="hermes", action="put"):
    db.execute(
        "INSERT INTO extcal_export_issues("
        "target,event_id,action,kind,http_status,reason_code,"
        "first_seen_utc,last_seen_utc) VALUES(?,?,?,?,?,?,?,?)",
        (target, row["id"], action, "conflict", 412, "conflict", NOW, NOW),
    )
    db.commit()


def test_success_without_etag_is_not_a_verified_put(db):
    row = event(db)
    calls = []

    def request(method, url, **kwargs):
        calls.append((method, kwargs.get("headers", {})))
        return extcal.Response(201, b"", {})

    counts = extcal.export_routes(db, cfg(), request=request, now_utc=NOW)

    assert counts["errors"]
    assert counts["exported"] == 0
    assert db.execute(
        "SELECT 1 FROM ext_exports WHERE event_id=?", (row["id"],)
    ).fetchone() is None


def test_existing_journal_without_etag_never_sends_put(db):
    row = event(db)
    seed_journal(db, "ext_exports", row, etag=None, body_hash="v2:" + "0" * 64)
    calls = []

    counts = extcal.export_routes(
        db, cfg(),
        request=lambda method, url, **kwargs: calls.append(method),
        now_utc=NOW,
    )

    assert counts["errors"]
    assert calls == []
    assert db.execute(
        "SELECT 1 FROM ext_exports WHERE event_id=?", (row["id"],)
    ).fetchone() is not None


def test_delete_without_etag_never_sends_unconditional_request(db):
    row = event(db)
    seed_journal(db, "ext_exports", row, etag=None)
    db.execute("UPDATE events SET status='cancelled' WHERE id=?", (row["id"],))
    db.commit()
    calls = []

    counts = extcal.export_routes(
        db, cfg(),
        request=lambda method, url, **kwargs: calls.append(method),
        now_utc=NOW,
    )

    assert counts["errors"]
    assert calls == []
    assert db.execute(
        "SELECT 1 FROM ext_exports WHERE event_id=?", (row["id"],)
    ).fetchone() is not None


def test_drop_valarm_reloads_body_before_retry_after_412(db):
    href = "https://caldav.icloud.com/1/calendars/personal/event.ics"
    calls = []
    old_body = ics(1, title="Old", alarm=True)
    fresh_body = ics(1, title="Phone edit", alarm=True,
                     start="20370720T140000Z", end="20370720T150000Z")

    def request(method, url, **kwargs):
        calls.append((method, kwargs.get("headers", {}), kwargs.get("body")))
        if method == "GET" and len([c for c in calls if c[0] == "GET"]) == 1:
            return extcal.Response(200, old_body, {"ETag": '"e1"'})
        if method == "PUT" and len([c for c in calls if c[0] == "PUT"]) == 1:
            return extcal.Response(412, b"", {})
        if method == "GET":
            return extcal.Response(200, fresh_body, {"ETag": '"e2"'})
        return extcal.Response(204, b"", {"ETag": '"e3"'})

    ok, _, detail = extcal.drop_valarm(cfg(), href, '"e1"', request=request)

    assert ok is True, detail
    put_calls = [call for call in calls if call[0] == "PUT"]
    assert len(put_calls) == 2
    second_body = put_calls[1][2]
    second_body = second_body.decode() if isinstance(second_body, bytes) else second_body
    assert "SUMMARY:Phone edit" in second_body
    assert put_calls[1][1]["If-Match"] == '"e2"'


def test_keep_remote_rolls_back_when_road_hook_reports_failure(db, monkeypatch):
    row = event(db, title="Local")
    seed_journal(db, "ext_exports", row)
    seed_issue(db, row)
    before = dict(db.execute("SELECT * FROM events WHERE id=?", (row["id"],)).fetchone())

    def request(method, url, **kwargs):
        return extcal.Response(
            200, ics(row["id"], title="Phone", start="20370720T140000Z",
                     end="20370720T150000Z").encode(), {"ETag": '"phone"'},
        )

    monkeypatch.setattr(
        extcal.cal, "recompute_road",
        lambda *args, **kwargs: {"minutes": None, "reason": "error"},
    )

    with pytest.raises(extcal._ExportFailure, match="road"):
        extcal.resolve_conflict(
            db, row["id"], decision="keep-remote", cfg=cfg(),
            request=request, now_utc=NOW,
        )

    assert dict(db.execute("SELECT * FROM events WHERE id=?", (row["id"],)).fetchone()) == before
    assert db.execute(
        "SELECT 1 FROM extcal_export_issues WHERE event_id=?", (row["id"],)
    ).fetchone() is not None


def test_participant_name_collision_fails_closed(db):
    people.add(db, "А, Б")
    people.add(db, "А")
    people.add(db, "Б")
    db.commit()
    row = event(db)
    seed_journal(db, "ext_exports", row)
    seed_issue(db, row)
    before = dict(db.execute("SELECT * FROM event_participants WHERE event_id=?", (row["id"],)).fetchone() or {})

    def request(method, url, **kwargs):
        return extcal.Response(
            200,
            ics(row["id"], description="Участники: А\\, Б").encode(),
            {"ETag": '"phone"'},
        )

    with pytest.raises(extcal._ExportFailure, match="participant"):
        extcal.resolve_conflict(
            db, row["id"], decision="keep-remote", cfg=cfg(),
            request=request, now_utc=NOW,
        )

    after = dict(db.execute("SELECT * FROM event_participants WHERE event_id=?", (row["id"],)).fetchone() or {})
    assert after == before
    assert db.execute(
        "SELECT 1 FROM extcal_export_issues WHERE event_id=?", (row["id"],)
    ).fetchone() is not None


def test_existing_destination_journal_requires_remote_proof_before_source_delete(db):
    taya = seed_taya(db)
    row = event(db, taya["id"])
    seed_journal(db, "ext_exports", row)
    seed_journal(db, "ext_exports_taya", row)
    body = ics(row["id"], title="Local")
    calls = []

    def request(method, url, **kwargs):
        calls.append((method, url))
        if method == "GET":
            if len([call for call in calls if call[0] == "GET"]) < 3:
                return extcal.Response(404, b"", {})
            return extcal.Response(200, body.encode(), {"ETag": '"new-taya"'})
        if method == "MOVE":
            return extcal.Response(201, b"", {})
        pytest.fail("route transition must not PUT or network DELETE")

    counts = extcal.export_routes(db, cfg(), request=request, now_utc=NOW)

    assert counts["exported"] == 1
    assert counts["deleted"] == 1
    assert [method for method, _ in calls] == ["GET", "GET", "MOVE", "GET"]



def test_existing_destination_without_remote_etag_creates_issue(db):
    taya = seed_taya(db)
    row = event(db, subject_person_id=taya["id"])
    seed_journal(db, "ext_exports", row)
    seed_journal(db, "ext_exports_taya", row)

    def request(method, url, **kwargs):
        assert method == "GET"
        return extcal.Response(200, ics(row["id"]).encode(), {})

    counts = extcal.export_routes(db, cfg(), request=request, now_utc=NOW)

    assert counts["errors"]
    assert db.execute(
        "SELECT 1 FROM ext_exports WHERE event_id=?", (row["id"],)
    ).fetchone() is not None
    issue = db.execute(
        "SELECT target, action, reason_code FROM extcal_export_issues "
        "WHERE event_id=?", (row["id"],)
    ).fetchone()
    assert tuple(issue) == ("taya", "put", "invalid_response")


def test_cancelled_series_keeps_exported_occurrence_until_cleanup(db):
    s = series.add(db, "Тренировка", "mon", "10:00")
    db.commit()
    series.generate(db, now_utc=NOW)
    db.commit()
    occurrence = db.execute(
        "SELECT * FROM events WHERE series_id=? AND status='active' ORDER BY start_utc LIMIT 1",
        (s["id"],),
    ).fetchone()
    assert occurrence is not None
    row = dict(occurrence)
    seed_journal(db, "ext_exports", row)

    series.cancel(db, s["id"], now_utc=NOW)

    assert db.execute(
        "SELECT status FROM events WHERE id=?", (row["id"],)
    ).fetchone()["status"] == "cancelled"
    assert db.execute(
        "SELECT 1 FROM ext_exports WHERE event_id=?", (row["id"],)
    ).fetchone() is not None


def test_resolve_supports_taya_and_delete_action(db):
    taya = seed_taya(db)
    row = event(db, taya["id"], title="Local")
    seed_journal(db, "ext_exports_taya", row)
    seed_issue(db, row, target="taya", action="delete")
    calls = []

    def request(method, url, **kwargs):
        calls.append((method, url, kwargs.get("headers", {})))
        if method == "GET":
            return extcal.Response(200, ics(row["id"], title="Phone").encode(), {"ETag": '"phone"'})
        if method == "DELETE":
            return extcal.Response(204, b"", {})
        pytest.fail("DELETE conflict force-push must not PUT")

    result = extcal.resolve_conflict(
        db, row["id"], target="taya", decision="force-push",
        cfg=cfg(), request=request, now_utc=NOW,
    )

    assert result["target"] == "taya"
    assert [method for method, _, _ in calls] == ["GET", "DELETE"]
    assert calls[1][2]["If-Match"] == '"phone"'
    assert db.execute(
        "SELECT 1 FROM extcal_export_issues WHERE event_id=?", (row["id"],)
    ).fetchone() is None


def test_health_surfaces_taya_issue(db):
    row = event(db)
    db.execute(
        "INSERT INTO extcal_export_issues("
        "target,event_id,action,kind,http_status,reason_code,"
        "first_seen_utc,last_seen_utc) VALUES(?,?,?,?,?,?,?,?)",
        ("taya", row["id"], "put", "error", 412, "export_error", NOW, NOW),
    )
    db.commit()

    result = health.extcal_failures(db, cfg(), now_utc=NOW)

    assert result["status"] == "degraded"
    assert str(row["id"]) in result["detail"]
    assert "TAYA" in result["detail"]
    assert result["issues"][0]["target"] == "taya"


def test_configuration_error_creates_issue(db):
    taya = seed_taya(db)
    row = event(db, taya["id"])
    counts = extcal.export_routes(
        db, cfg(extcal_taya_calendar=""),
        request=lambda *args, **kwargs: pytest.fail("configuration error must not use transport"),
        now_utc=NOW,
    )
    assert counts["errors"]
    issue = db.execute(
        "SELECT target, action, kind, reason_code FROM extcal_export_issues WHERE event_id=?",
        (row["id"],),
    ).fetchone()
    assert tuple(issue) == ("taya", "put", "error", "invalid_response")


def test_destination_unverified_creates_issue_and_keeps_source_after_move(db, monkeypatch):
    taya = seed_taya(db)
    row = event(db, subject_person_id=taya["id"])
    seed_journal(db, "ext_exports", row)
    monkeypatch.setattr(extcal, "_export_record", lambda *args, **kwargs: None)
    calls = []

    def request(method, url, **kwargs):
        calls.append(method)
        if method == "GET":
            if calls.count("GET") == 1:
                return extcal.Response(404, b"", {})
            return extcal.Response(200, ics(row["id"], title="Local").encode(), {"ETag": '"destination"'})
        if method == "MOVE":
            return extcal.Response(201, b"", {})
        pytest.fail("destination verification must not use PUT or network DELETE")

    counts = extcal.export_routes(db, cfg(), request=request, now_utc=NOW)

    assert counts["errors"]
    assert calls == ["GET", "MOVE", "GET"]
    assert db.execute(
        "SELECT 1 FROM ext_exports WHERE event_id=?", (row["id"],)
    ).fetchone() is not None
    assert db.execute(
        "SELECT 1 FROM ext_exports_taya WHERE event_id=?", (row["id"],)
    ).fetchone() is None
    issue = db.execute(
        "SELECT target, action, kind, reason_code FROM extcal_export_issues "
        "WHERE event_id=?", (row["id"],)
    ).fetchone()
    assert tuple(issue) == ("taya", "put", "error", "export_error")



def test_successful_item_isolation_covers_post_remote_bookkeeping(db, monkeypatch):
    first = event(db, title="first")
    second = event(db, title="second")
    failed = set()
    original_resolve = extcal._export_issue_resolve

    def fail_first(conn, event_id, target="hermes"):
        if event_id == first["id"] and event_id not in failed:
            failed.add(event_id)
            raise RuntimeError("audit bookkeeping failed")
        return original_resolve(conn, event_id, target)

    monkeypatch.setattr(extcal, "_export_issue_resolve", fail_first)
    counts = extcal.export_routes(
        db, cfg(),
        request=lambda *args, **kwargs: extcal.Response(201, b"", {"ETag": '"e1"'}),
        now_utc=NOW,
    )

    assert counts["exported"] == 1
    assert len(counts["errors"]) == 1
    assert db.execute(
        "SELECT 1 FROM ext_exports WHERE event_id=?", (second["id"],)
    ).fetchone() is not None
    payload = json.loads(db.execute(
        "SELECT payload FROM audit_log WHERE kind='cal.ext.export_error' ORDER BY id DESC LIMIT 1"
    ).fetchone()["payload"])
    assert payload["exception_type"] == "RuntimeError"
