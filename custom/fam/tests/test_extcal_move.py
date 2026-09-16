"""CalDAV MOVE route-transition regressions."""

import pytest

from fam import cal, extcal, gate, people

HERMES = "https://caldav.icloud.com/1/calendars/hermes/"
TAYA = "https://caldav.icloud.com/1/calendars/taya/"
NOW = "2037-07-15T00:00:00+00:00"


def cfg(**over):
    value = dict(gate.CONFIG_DEFAULTS)
    value.update({
        "extcal_enabled": True,
        "extcal_write_calendar": HERMES,
        "extcal_taya_calendar": TAYA,
        "extcal_horizon_weeks": 8,
    })
    value.update(over)
    return value


def make_event(db):
    taya = people.add(db, "Тая", slug="taya")
    db.commit()
    row = cal.add(
        db, "Taya event", "2037-07-20T13:00:00+00:00",
        end_utc="2037-07-20T14:00:00+00:00", subject_person_id=taya["id"],
    )
    db.commit()
    return row


def journal_source(db, row):
    db.execute(
        "INSERT INTO ext_exports(event_id,href,etag,body_hash,synced_at) "
        "VALUES(?,?,?,?,?)",
        (row["id"], f"{HERMES}fam-{row['id']}@hermes-home.ics", '"source"',
         extcal._export_body_hash(row, "", []), NOW),
    )
    db.commit()


def remote_ics(event_id):
    return (
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\n"
        f"UID:fam-{event_id}@hermes-home\r\n"
        "DTSTAMP:20370715T000000Z\r\n"
        "DTSTART:20370720T130000Z\r\n"
        "DTEND:20370720T140000Z\r\n"
        "SUMMARY:Taya event\r\n"
        "END:VEVENT\r\nEND:VCALENDAR\r\n"
    ).encode()


def test_route_transition_uses_move_get_verify_and_no_network_delete(db):
    row = make_event(db)
    journal_source(db, row)
    calls = []

    def request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        if method == "GET":
            assert url.startswith(TAYA)
            if len([call for call in calls if call[0] == "GET"]) == 1:
                return extcal.Response(404, b"", {})
            return extcal.Response(200, remote_ics(row["id"]), {"ETag": '"moved"'})
        if method == "MOVE":
            assert kwargs["headers"]["Destination"].startswith(TAYA)
            assert kwargs["headers"]["Overwrite"] == "F"
            return extcal.Response(201, b"", {})
        pytest.fail("route-transition must not use PUT or network DELETE")

    counts = extcal.export_routes(db, cfg(), request=request, now_utc=NOW)

    assert [method for method, _, _ in calls] == ["GET", "MOVE", "GET"]
    assert counts["exported"] == 1
    assert counts["deleted"] == 1
    assert db.execute("SELECT 1 FROM ext_exports WHERE event_id=?", (row["id"],)).fetchone() is None
    moved = db.execute(
        "SELECT href, etag FROM ext_exports_taya WHERE event_id=?", (row["id"],)
    ).fetchone()
    assert moved["href"].startswith(TAYA)
    assert moved["etag"] == '"moved"'


def test_route_transition_refuses_occupied_destination_without_move(db):
    row = make_event(db)
    journal_source(db, row)
    calls = []

    def request(method, url, **kwargs):
        calls.append((method, url))
        assert method == "GET"
        return extcal.Response(200, remote_ics(row["id"]), {"ETag": '"occupied"'})

    counts = extcal.export_routes(db, cfg(), request=request, now_utc=NOW)

    assert [method for method, _ in calls] == ["GET", "GET"]
    assert calls[0][1].startswith(TAYA)
    assert calls[1][1].startswith(HERMES)
    assert counts["errors"]
    assert db.execute("SELECT 1 FROM ext_exports WHERE event_id=?", (row["id"],)).fetchone() is not None
    assert db.execute("SELECT 1 FROM ext_exports_taya WHERE event_id=?", (row["id"],)).fetchone() is None


@pytest.mark.parametrize("status", [405, 501])
def test_route_transition_does_not_fallback_when_move_unsupported(db, status):
    row = make_event(db)
    journal_source(db, row)
    calls = []

    def request(method, url, **kwargs):
        calls.append(method)
        if method == "GET":
            return extcal.Response(404, b"", {})
        if method == "MOVE":
            return extcal.Response(status, b"", {})
        pytest.fail("unsupported MOVE must not fall back to PUT or DELETE")

    counts = extcal.export_routes(db, cfg(), request=request, now_utc=NOW)

    assert calls == ["GET", "MOVE"]
    assert counts["errors"]
    assert db.execute("SELECT 1 FROM ext_exports WHERE event_id=?", (row["id"],)).fetchone() is not None
    assert db.execute("SELECT 1 FROM ext_exports_taya WHERE event_id=?", (row["id"],)).fetchone() is None


def test_move_success_without_journal_commit_recovers_without_second_move(db, monkeypatch):
    row = make_event(db)
    journal_source(db, row)
    calls = []
    moved = {"value": False}
    original_record_success = extcal._export_record_success
    first_record = {"value": True}

    def record_success_once(*args, **kwargs):
        if first_record["value"]:
            first_record["value"] = False
            return
        return original_record_success(*args, **kwargs)

    monkeypatch.setattr(extcal, "_export_record_success", record_success_once)

    def request(method, url, **kwargs):
        calls.append((method, url))
        if method == "GET":
            if url.startswith(TAYA):
                if not moved["value"]:
                    return extcal.Response(404, b"", {})
                return extcal.Response(200, remote_ics(row["id"]), {"ETag": '"moved"'})
            if url.startswith(HERMES):
                return extcal.Response(404, b"", {})
        if method == "MOVE":
            moved["value"] = True
            return extcal.Response(201, b"", {})
        pytest.fail("recovery must not use PUT or network DELETE")

    extcal.export_routes(db, cfg(), request=request, now_utc=NOW)
    extcal.export_routes(db, cfg(), request=request, now_utc=NOW)

    assert [method for method, _ in calls].count("MOVE") == 1
    assert "DELETE" not in [method for method, _ in calls]
    assert db.execute("SELECT 1 FROM ext_exports WHERE event_id=?", (row["id"],)).fetchone() is None
    assert db.execute("SELECT 1 FROM ext_exports_taya WHERE event_id=?", (row["id"],)).fetchone() is not None
    assert db.execute("SELECT 1 FROM extcal_export_issues WHERE event_id=?", (row["id"],)).fetchone() is None
