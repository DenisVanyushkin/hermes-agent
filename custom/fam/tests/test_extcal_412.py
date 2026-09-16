"""S4 RED: conditional 412 handling and versioned export hashes."""
from fam import cal, extcal, gate


WRITE_URL = "https://caldav.icloud.com/1/calendars/hermes/"
NOW = "2037-07-15T00:00:00+00:00"


def _cfg(**over):
    cfg = dict(gate.CONFIG_DEFAULTS)
    cfg.update({
        "extcal_enabled": True,
        "extcal_username": "amina@example.com",
        "extcal_write_calendar": WRITE_URL,
        "extcal_horizon_weeks": 8,
    })
    cfg.update(over)
    return cfg


def _event(db, title="Йога", start="2037-07-20T13:00:00+00:00"):
    event = cal.add(db, title, start, end_utc="2037-07-20T14:00:00+00:00")
    db.commit()
    return event


def _ics(event_id, title="Йога", uid=None, location=""):
    uid = uid or f"fam-{event_id}@hermes-home"
    lines = [
        "BEGIN:VCALENDAR", "VERSION:2.0", "BEGIN:VEVENT",
        f"UID:{uid}", "DTSTAMP:20370715T000000Z",
        "DTSTART:20370720T130000Z", "DTEND:20370720T140000Z",
        f"SUMMARY:{title}",
    ]
    if location:
        lines.append(f"LOCATION:{location}")
    lines += ["END:VEVENT", "END:VCALENDAR"]
    return "\r\n".join(lines) + "\r\n"


def _seed_export(db, event, body_hash, etag='"e0"'):
    db.execute(
        "INSERT INTO ext_exports(event_id, href, etag, body_hash, synced_at) "
        "VALUES (?,?,?,?,?)",
        (event["id"], f"{WRITE_URL}fam-{event['id']}@hermes-home.ics",
         etag, body_hash, NOW),
    )
    db.commit()


def test_put_412_remote_equals_desired_records_without_second_put(db):
    event = _event(db)
    calls = []
    desired = _ics(event["id"])

    def request(method, url, **kwargs):
        calls.append(method)
        if method == "PUT":
            return extcal.Response(412, b"", {})
        return extcal.Response(200, desired.encode(), {"ETag": '"remote"'})

    counts = extcal.export_routes(db, _cfg(), request=request, now_utc=NOW)

    assert counts["errors"] == []
    assert counts["conflicts"] == []
    assert counts["exported"] == 1
    assert calls == ["PUT", "GET"]
    assert db.execute("SELECT etag FROM ext_exports WHERE event_id=?",
                      (event["id"],)).fetchone()["etag"] == '"remote"'


def test_put_412_remote_equals_last_retries_once_with_fresh_etag(db):
    event = _event(db, title="Старое")
    old_hash = extcal._export_body_hash(event, "", [])
    _seed_export(db, event, old_hash)
    db.execute("UPDATE events SET title=? WHERE id=?", ("Новое", event["id"]))
    db.commit()
    calls = []

    def request(method, url, **kwargs):
        calls.append(method)
        if method == "PUT" and calls.count("PUT") == 1:
            return extcal.Response(412, b"", {})
        if method == "GET":
            return extcal.Response(200, _ics(event["id"], "Старое").encode(),
                                   {"ETag": '"fresh"'})
        return extcal.Response(200, b"", {"ETag": '"new"'})

    counts = extcal.export_routes(db, _cfg(), request=request, now_utc=NOW)

    assert counts["updated"] == 1
    assert counts["conflicts"] == []
    assert calls == ["PUT", "GET", "PUT"]


def test_put_412_remote_differs_creates_conflict_without_second_put(db):
    event = _event(db)
    _seed_export(db, event, "v2:" + "0" * 64)
    calls = []

    def request(method, url, **kwargs):
        calls.append(method)
        if method == "PUT":
            return extcal.Response(412, b"", {})
        return extcal.Response(200, _ics(event["id"], "На телефоне").encode(),
                               {"ETag": '"phone"'})

    counts = extcal.export_routes(db, _cfg(), request=request, now_utc=NOW)

    assert calls == ["PUT", "GET"]
    assert counts["conflicts"] and counts["errors"] == []
    issue = db.execute(
        "SELECT kind, reason_code FROM extcal_export_issues "
        "WHERE event_id=?", (event["id"],)).fetchone()
    assert tuple(issue) == ("conflict", "conflict")


def test_conflict_quarantine_skips_network_on_next_tick(db):
    event = _event(db)
    _seed_export(db, event, "v2:" + "0" * 64)
    calls = []

    def request(method, url, **kwargs):
        calls.append(method)
        if method == "PUT":
            return extcal.Response(412, b"", {})
        return extcal.Response(200, _ics(event["id"], "На телефоне").encode(),
                               {"ETag": '"phone"'})

    first = extcal.export_routes(db, _cfg(), request=request, now_utc=NOW)
    assert first["conflicts"]
    calls_before = list(calls)

    second = extcal.export_routes(
        db, _cfg(), request=lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("quarantine must not call network")),
        now_utc=NOW,
    )
    assert calls == calls_before
    assert second["errors"] == []


def test_bare_v1_hash_rebaselines_locally_without_network(db):
    event = _event(db)
    bare_v1 = extcal._export_hash_for_version(event, "", [], "v1")
    _seed_export(db, event, bare_v1)

    counts = extcal.export_routes(
        db, _cfg(), request=lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("legacy equivalent must not call network")),
        now_utc=NOW,
    )

    assert counts["unchanged"] == 1
    stored = db.execute(
        "SELECT body_hash FROM ext_exports WHERE event_id=?", (event["id"],)
    ).fetchone()["body_hash"]
    assert stored.startswith("v2:")


def test_delete_412_requires_matching_uid_before_delete(db):
    event = _event(db)
    _seed_export(db, event, "v2:" + "0" * 64)
    db.execute("UPDATE events SET status='cancelled' WHERE id=?", (event["id"],))
    db.commit()
    calls = []

    def request(method, url, **kwargs):
        calls.append(method)
        if method == "DELETE":
            return extcal.Response(412, b"", {})
        return extcal.Response(200, _ics(event["id"], "foreign", uid="other@phone").encode(),
                               {"ETag": '"phone"'})

    counts = extcal.export_routes(db, _cfg(), request=request, now_utc=NOW)

    assert calls == ["DELETE", "GET"]
    assert len(counts["errors"]) == 1
    assert db.execute("SELECT 1 FROM ext_exports WHERE event_id=?",
                      (event["id"],)).fetchone() is not None
