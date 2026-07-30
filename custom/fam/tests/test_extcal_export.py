"""Task 7: reverse write -- `extcal.export_own` (PUT/DELETE owner='hermes'
events into the "Гермес" collection, without VALARM) plus its wiring into
`fam tick cal-ext` (`cli._cal_ext_sync`/`cli.cmd_tick_cal_ext`).

Only `extcal._default_open` is monkeypatched here (the module's own
lowest-level network seam -- see test_extcal_transport.py's identical
style): every test exercises the REAL `_request`/`_export_put`/
`_export_delete`/`export_own` code, including the host-guard, header
building, and 412-retry logic -- no test here ever touches the real
network. `cal.add`/`cal.cancel`/`places.add` run for real against the `db`
fixture's tmp sqlite file.
"""
import json
import types

from fam import cal, cli, extcal, gate, people, places
from fam import db as famdb


WRITE_URL = "https://caldav.icloud.com/1/calendars/hermes/"
TEST_NOW = "2037-07-15T00:00:00+00:00"


def _cfg(**over):
    base = dict(gate.CONFIG_DEFAULTS)
    base.update({
        "extcal_enabled": True,
        "extcal_username": "amina@example.com",
        "extcal_read_calendars": [],
        "extcal_write_calendar": "",
        "extcal_horizon_weeks": 8,
    })
    base.update(over)
    return base


def _hermes_event(conn, title="Йога", start="2037-07-20T13:00:00+00:00", **kw):
    return cal.add(conn, title, start, **kw)


def _seed_export_row(conn, event_id, href=None, etag='"e0"', body_hash="stale-hash"):
    href = href or f"{WRITE_URL}fam-{event_id}@hermes-home.ics"
    conn.execute(
        "INSERT INTO ext_exports(event_id, href, etag, body_hash, synced_at) "
        "VALUES (?,?,?,?,?)",
        (event_id, href, etag, body_hash, "2037-07-01T00:00:00+00:00"))
    conn.commit()
    return href


# ---------------------------------------------------------------------
# requirement #8: extcal_write_calendar unset -> hard no-op, zero network
# ---------------------------------------------------------------------

def test_write_calendar_unset_is_zero_network_noop(db, monkeypatch):
    def boom(req, timeout):
        raise AssertionError("must not touch the network when "
                              "extcal_write_calendar is unset")
    monkeypatch.setattr(extcal, "_default_open", boom)

    _hermes_event(db)
    db.commit()

    counts = extcal.export_own(db, _cfg(extcal_write_calendar=""), now_utc=TEST_NOW)
    assert counts == {"exported": 0, "updated": 0, "unchanged": 0,
                       "deleted": 0, "errors": []}
    assert db.execute("SELECT COUNT(*) AS n FROM ext_exports").fetchone()["n"] == 0


# ---------------------------------------------------------------------
# requirement #2/#3: generated VEVENT has NO VALARM, correct UID convention
# ---------------------------------------------------------------------

def test_exported_vevent_has_no_valarm_and_mail_uid_convention(db, monkeypatch):
    captured = {}

    def fake_open(req, timeout):
        if req.get_method() == "PUT":
            captured["body"] = req.data.decode("utf-8")
        return extcal.Response(201, b"", {"ETag": '"e1"'})
    monkeypatch.setattr(extcal, "_default_open", fake_open)

    event = _hermes_event(db, title="Йога", start="2037-07-20T13:00:00+00:00",
                           end_utc="2037-07-20T14:00:00+00:00")
    db.commit()

    counts = extcal.export_own(db, _cfg(extcal_write_calendar=WRITE_URL), now_utc=TEST_NOW)
    assert counts["exported"] == 1
    assert counts["errors"] == []

    body = captured["body"]
    assert "VALARM" not in body
    assert "BEGIN:VEVENT" in body and "END:VEVENT" in body
    # mail.py::build_ics's own convention, reused verbatim (task brief
    # requirement #3) -- and exactly what this module's own _HERMES_UID_RE
    # (anti-echo belt 2) is written to recognize.
    assert f"UID:fam-{event['id']}@hermes-home" in body
    assert "SUMMARY:Йога" in body
    assert "DTSTART:20370720T130000Z" in body
    assert "DTEND:20370720T140000Z" in body

    row = db.execute("SELECT * FROM ext_exports WHERE event_id=?", (event["id"],)).fetchone()
    assert row["href"] and row["etag"] == '"e1"' and row["body_hash"]


def test_exported_vevent_no_dtend_defaults_to_one_hour(db, monkeypatch):
    captured = {}

    def fake_open(req, timeout):
        if req.get_method() == "PUT":
            captured["body"] = req.data.decode("utf-8")
        return extcal.Response(201, b"", {})
    monkeypatch.setattr(extcal, "_default_open", fake_open)

    _hermes_event(db, title="Звонок", start="2037-07-20T09:00:00+00:00")
    db.commit()
    extcal.export_own(db, _cfg(extcal_write_calendar=WRITE_URL), now_utc=TEST_NOW)

    assert "DTSTART:20370720T090000Z" in captured["body"]
    assert "DTEND:20370720T100000Z" in captured["body"]


def test_exported_vevent_includes_resolved_place_name_as_location(db, monkeypatch):
    captured = {}

    def fake_open(req, timeout):
        if req.get_method() == "PUT":
            captured["body"] = req.data.decode("utf-8")
        return extcal.Response(201, b"", {})
    monkeypatch.setattr(extcal, "_default_open", fake_open)

    places.add(db, "Invictus")
    db.commit()
    _hermes_event(db, title="Тренировка", start="2037-07-20T09:00:00+00:00",
                  place="Invictus")
    db.commit()
    extcal.export_own(db, _cfg(extcal_write_calendar=WRITE_URL), now_utc=TEST_NOW)

    assert "LOCATION:Invictus" in captured["body"]


# ---------------------------------------------------------------------
# fix-round finding N1: participants -- exported (matching mail.py's own
# convention for the SAME event/UID), and folded into body_hash so a
# participant-set edit is not a permanent, undetected divergence
# ---------------------------------------------------------------------

def test_participants_are_exported_matching_mail_convention(db, monkeypatch):
    captured = {}

    def fake_open(req, timeout):
        if req.get_method() == "PUT":
            captured["body"] = req.data.decode("utf-8")
        return extcal.Response(201, b"", {})
    monkeypatch.setattr(extcal, "_default_open", fake_open)

    taya = people.add(db, "Таня")
    denis = people.add(db, "Денис")
    db.commit()
    event = _hermes_event(db, title="Ужин", start="2037-07-20T18:00:00+00:00",
                           participants=("Таня", "Денис"))
    db.commit()

    counts = extcal.export_own(db, _cfg(extcal_write_calendar=WRITE_URL), now_utc=TEST_NOW)
    assert counts["exported"] == 1

    body = captured["body"]
    # mail.py::build_ics's OWN convention for the SAME event/UID: property
    # name, "Участники: " prefix, and comma-join, all identical -- since
    # fix-round 2, both call the ONE shared `mail.participant_names`. The
    # comma in the join is itself RFC5545 TEXT-escaped (`\,`) by
    # `_export_escape_text`, same as mail.py's own `_escape_ics_text`
    # would do for the identical string.
    assert "DESCRIPTION:Участники: Денис\\, Таня" in body


def test_export_delegates_participant_join_to_shared_mail_function(db, monkeypatch):
    """Fix-round 2, finding R1: extcal.py must not keep its own
    independent copy of the participant-name join -- it has to call the
    ONE shared `mail.participant_names`. Monkeypatching that function and
    checking it is actually invoked (not just checking the output text,
    which a second, textually-identical implementation would also
    satisfy) pins the DELEGATION itself, not merely today's result."""
    from fam import mail
    calls = []

    def fake_join(participants):
        calls.append(list(participants or []))
        return "FAKE-JOIN-MARKER"
    monkeypatch.setattr(mail, "participant_names", fake_join)

    captured = {}

    def fake_open(req, timeout):
        if req.get_method() == "PUT":
            captured["body"] = req.data.decode("utf-8")
        return extcal.Response(201, b"", {})
    monkeypatch.setattr(extcal, "_default_open", fake_open)

    people.add(db, "Таня")
    db.commit()
    _hermes_event(db, title="Ужин", start="2037-07-20T18:00:00+00:00",
                   participants=("Таня",))
    db.commit()

    counts = extcal.export_own(db, _cfg(extcal_write_calendar=WRITE_URL), now_utc=TEST_NOW)
    assert counts["exported"] == 1
    assert len(calls) >= 1  # invoked at least once (body build + body_hash)
    assert "FAKE-JOIN-MARKER" in captured["body"]


def test_event_with_no_participants_has_no_description_line(db, monkeypatch):
    captured = {}

    def fake_open(req, timeout):
        if req.get_method() == "PUT":
            captured["body"] = req.data.decode("utf-8")
        return extcal.Response(201, b"", {})
    monkeypatch.setattr(extcal, "_default_open", fake_open)

    _hermes_event(db, title="Йога", start="2037-07-20T13:00:00+00:00")
    db.commit()
    extcal.export_own(db, _cfg(extcal_write_calendar=WRITE_URL), now_utc=TEST_NOW)

    assert "DESCRIPTION" not in captured["body"]


def test_export_participants_delegates_to_cal_get_not_a_raw_query(db, monkeypatch):
    """Fix-round 2, finding R2: `_export_participants` must not keep its
    own hand-copied `event_participants JOIN people` SQL -- it has to call
    `cal.get()`, the module already imports `cal` for exactly this kind of
    reuse. Monkeypatching `cal.get` and checking it is actually invoked
    (with the right event_id, and that ITS OWN "participants" list -- not
    a second, independently-queried one -- ends up in the exported body)
    pins the delegation, not just today's matching output."""
    # Create the event BEFORE monkeypatching cal.get -- cal.add() itself
    # calls the real get() internally to build its own return value, and
    # that internal call must not be confused with the one this test
    # actually wants to observe (export_own's own use of cal.get).
    event = _hermes_event(db, title="Ужин", start="2037-07-20T18:00:00+00:00")
    db.commit()

    calls = []
    real_get = cal.get

    def spy_get(conn, event_id):
        calls.append(event_id)
        result = real_get(conn, event_id)
        if result is not None:
            result = dict(result)
            result["participants"] = [{"name": "ПОДСТАВНОЙ УЧАСТНИК"}]
        return result
    monkeypatch.setattr(cal, "get", spy_get)

    captured = {}

    def fake_open(req, timeout):
        if req.get_method() == "PUT":
            captured["body"] = req.data.decode("utf-8")
        return extcal.Response(201, b"", {})
    monkeypatch.setattr(extcal, "_default_open", fake_open)

    counts = extcal.export_own(db, _cfg(extcal_write_calendar=WRITE_URL), now_utc=TEST_NOW)
    assert counts["exported"] == 1
    assert event["id"] in calls
    assert "ПОДСТАВНОЙ УЧАСТНИК" in captured["body"]


def test_participant_set_change_triggers_a_fresh_put_via_body_hash(db, monkeypatch):
    """Before this fix, participants were absent from BOTH the VEVENT body
    AND body_hash -- a participant-set edit would never re-PUT at all,
    leaving the iCloud copy permanently stale. Now it's part of the hash:
    adding a participant to an already-exported event must trigger an
    UPDATE PUT on the very next tick."""
    calls = []

    def fake_open(req, timeout):
        calls.append(req.get_method())
        return extcal.Response(201, b"", {"ETag": '"e1"'})
    monkeypatch.setattr(extcal, "_default_open", fake_open)

    event = _hermes_event(db, title="Ужин", start="2037-07-20T18:00:00+00:00")
    db.commit()
    cfg = _cfg(extcal_write_calendar=WRITE_URL)

    c1 = extcal.export_own(db, cfg, now_utc=TEST_NOW)
    assert c1["exported"] == 1
    assert calls == ["PUT"]

    taya = people.add(db, "Таня")
    db.execute("INSERT INTO event_participants(event_id, person_id) VALUES (?,?)",
               (event["id"], taya["id"]))
    db.commit()

    c2 = extcal.export_own(db, cfg, now_utc="2037-07-15T00:10:00+00:00")
    assert c2 == {"exported": 0, "updated": 1, "unchanged": 0, "deleted": 0, "errors": []}
    assert calls == ["PUT", "PUT"]


# ---------------------------------------------------------------------
# requirement #4: body_hash gates re-PUTs -- unchanged event, zero network
# ---------------------------------------------------------------------

def test_second_export_of_unchanged_event_touches_no_network(db, monkeypatch):
    calls = []

    def fake_open(req, timeout):
        calls.append(req.get_method())
        return extcal.Response(201, b"", {"ETag": '"e1"'})
    monkeypatch.setattr(extcal, "_default_open", fake_open)

    _hermes_event(db, title="Йога", start="2037-07-20T13:00:00+00:00")
    db.commit()
    cfg = _cfg(extcal_write_calendar=WRITE_URL)

    c1 = extcal.export_own(db, cfg, now_utc=TEST_NOW)
    assert c1["exported"] == 1
    assert calls == ["PUT"]

    # A later tick, different wall-clock time (a fresh DTSTAMP would
    # differ if it were part of the hash) but the SAME event content.
    c2 = extcal.export_own(db, cfg, now_utc="2037-07-15T00:15:00+00:00")
    assert c2 == {"exported": 0, "updated": 0, "unchanged": 1, "deleted": 0, "errors": []}
    assert calls == ["PUT"]  # no new network call at all


# ---------------------------------------------------------------------
# requirement #5: a real content change -> PUT with If-Match: <etag>
# ---------------------------------------------------------------------

def test_changed_time_triggers_put_with_if_match_etag(db, monkeypatch):
    seen = {}

    def fake_open(req, timeout):
        if req.get_method() == "PUT":
            seen["if_match"] = req.get_header("If-match")
        return extcal.Response(200, b"", {"ETag": '"new-etag"'})
    monkeypatch.setattr(extcal, "_default_open", fake_open)

    event = _hermes_event(db, title="Йога", start="2037-07-20T13:00:00+00:00")
    href = _seed_export_row(db, event["id"], etag='"old-etag"',
                             body_hash="not-the-real-hash")

    counts = extcal.export_own(db, _cfg(extcal_write_calendar=WRITE_URL), now_utc=TEST_NOW)
    assert counts["updated"] == 1
    assert seen["if_match"] == '"old-etag"'

    row = db.execute("SELECT * FROM ext_exports WHERE event_id=?", (event["id"],)).fetchone()
    assert row["href"] == href
    assert row["etag"] == '"new-etag"'


# ---------------------------------------------------------------------
# requirement #5: 412 conflict -> re-read (GET) + retry ONCE
# ---------------------------------------------------------------------

def test_412_conflict_is_reread_and_retried_once_then_succeeds(db, monkeypatch):
    calls = []

    def fake_open(req, timeout):
        method = req.get_method()
        calls.append(method)
        if method == "PUT" and calls.count("PUT") == 1:
            return extcal.Response(412, b"", {})
        if method == "GET":
            return extcal.Response(200, b"", {"ETag": '"fresh-etag"'})
        if method == "PUT" and calls.count("PUT") == 2:
            return extcal.Response(201, b"", {"ETag": '"final-etag"'})
        raise AssertionError(f"unexpected extra call: {calls}")
    monkeypatch.setattr(extcal, "_default_open", fake_open)

    event = _hermes_event(db, title="Йога", start="2037-07-20T13:00:00+00:00")
    _seed_export_row(db, event["id"], etag='"old-etag"', body_hash="not-the-real-hash")

    counts = extcal.export_own(db, _cfg(extcal_write_calendar=WRITE_URL), now_utc=TEST_NOW)
    assert counts["updated"] == 1
    assert counts["errors"] == []
    assert calls == ["PUT", "GET", "PUT"]

    row = db.execute("SELECT * FROM ext_exports WHERE event_id=?", (event["id"],)).fetchone()
    assert row["etag"] == '"final-etag"'


def test_412_conflict_retry_also_failing_is_recorded_as_one_error_not_retried_again(db, monkeypatch):
    calls = []

    def fake_open(req, timeout):
        method = req.get_method()
        calls.append(method)
        if method == "GET":
            return extcal.Response(200, b"", {"ETag": '"fresh-etag"'})
        return extcal.Response(412, b"", {})  # every PUT attempt conflicts
    monkeypatch.setattr(extcal, "_default_open", fake_open)

    _hermes_event(db, title="Йога", start="2037-07-20T13:00:00+00:00")
    db.commit()

    counts = extcal.export_own(db, _cfg(extcal_write_calendar=WRITE_URL), now_utc=TEST_NOW)
    assert counts["exported"] == 0
    assert len(counts["errors"]) == 1
    assert counts["errors"][0]["action"] == "insert"
    assert calls.count("PUT") == 2  # exactly one retry -- never a second
    assert calls.count("GET") == 1


def test_export_commit_one_caps_error_length_for_export_failure_too(db):
    """Final review blocker 3 (privacy): `_export_commit_one`'s
    `_ExportFailure` branch used to skip the `[:300]` cap entirely (only
    the OTHER branch -- an unexpected non-`_ExportFailure` exception --
    had it). `_ExportFailure`'s own messages embed an absolute CalDAV
    resource href (`f"PUT {href} failed (status=...)"`,
    `f"DELETE {href} failed (status=...)"`), so an unbounded `str(e)`
    here was the one inconsistent channel -- both branches must cap the
    same way now."""
    long_href = ("https://caldav.icloud.com/1/calendars/hermes/"
                 + ("x" * 400) + ".ics")

    def boom():
        raise extcal._ExportFailure(f"PUT {long_href} failed (status=500)")

    counts = {"errors": []}
    extcal._export_commit_one(db, 1, "update", "updated", counts, boom)

    assert len(counts["errors"]) == 1
    assert len(counts["errors"][0]["error"]) <= 300

    rows = db.execute(
        "SELECT payload FROM audit_log WHERE kind='cal.ext.export_error'"
    ).fetchall()
    assert len(rows) == 1
    payload = json.loads(rows[0]["payload"])
    assert len(payload["error"]) <= 300


# ---------------------------------------------------------------------
# requirement #6: cancellation -> DELETE + ext_exports row dropped
# ---------------------------------------------------------------------

def test_cancelled_event_triggers_delete_and_drops_ext_exports_row(db, monkeypatch):
    calls = []

    def fake_open(req, timeout):
        calls.append((req.get_method(), req.full_url, req.get_header("If-match")))
        return extcal.Response(204, b"", {})
    monkeypatch.setattr(extcal, "_default_open", fake_open)

    event = _hermes_event(db, title="Йога", start="2037-07-20T13:00:00+00:00")
    href = _seed_export_row(db, event["id"], etag='"e5"', body_hash="whatever")
    cal.cancel(db, event["id"])
    db.commit()

    counts = extcal.export_own(db, _cfg(extcal_write_calendar=WRITE_URL), now_utc=TEST_NOW)
    assert counts == {"exported": 0, "updated": 0, "unchanged": 0, "deleted": 1, "errors": []}
    assert calls == [("DELETE", href, '"e5"')]

    assert db.execute(
        "SELECT * FROM ext_exports WHERE event_id=?", (event["id"],)
    ).fetchone() is None


def test_done_event_previously_exported_is_also_deleted(db, monkeypatch):
    """Not literally 'cancelled' but exactly as wrong to leave visible on
    her phone -- export_own's eligibility query is status='active' only,
    so a transition to 'done' routes through the same DELETE cleanup path
    as an explicit cancellation (see export_own's own docstring: broader
    than requirement #6's literal wording, on purpose)."""
    calls = []

    def fake_open(req, timeout):
        calls.append(req.get_method())
        return extcal.Response(204, b"", {})
    monkeypatch.setattr(extcal, "_default_open", fake_open)

    event = _hermes_event(db, title="Йога", start="2037-07-20T13:00:00+00:00")
    _seed_export_row(db, event["id"])
    cal.done(db, event["id"])
    db.commit()

    counts = extcal.export_own(db, _cfg(extcal_write_calendar=WRITE_URL), now_utc=TEST_NOW)
    assert counts["deleted"] == 1
    assert calls == ["DELETE"]


def test_owner_flipped_away_from_hermes_after_export_is_also_deleted(db, monkeypatch):
    """N2(a): the same broader removal-pass scoping (see the 'done' test
    above) applied to a re-owned row -- a previously-exported event whose
    owner later flips to 'iphone' (e.g. a future `cal disown`) is exactly
    as wrong to leave on her phone under Hermes' own write as an explicit
    cancellation would be."""
    calls = []

    def fake_open(req, timeout):
        calls.append(req.get_method())
        return extcal.Response(204, b"", {})
    monkeypatch.setattr(extcal, "_default_open", fake_open)

    event = _hermes_event(db, title="Йога", start="2037-07-20T13:00:00+00:00")
    _seed_export_row(db, event["id"])
    db.execute("UPDATE events SET owner='iphone' WHERE id=?", (event["id"],))
    db.commit()

    counts = extcal.export_own(db, _cfg(extcal_write_calendar=WRITE_URL), now_utc=TEST_NOW)
    assert counts["deleted"] == 1
    assert calls == ["DELETE"]
    assert db.execute(
        "SELECT * FROM ext_exports WHERE event_id=?", (event["id"],)
    ).fetchone() is None


def test_start_moved_beyond_horizon_after_export_is_also_deleted(db, monkeypatch):
    """N2(b): same scoping again, for the window-aged-out case -- a
    previously-exported event whose start_utc is later pushed past
    [today-1d, +extcal_horizon_weeks] is cleaned up the same way, not left
    behind as a permanent ghost on her phone."""
    calls = []

    def fake_open(req, timeout):
        calls.append(req.get_method())
        return extcal.Response(204, b"", {})
    monkeypatch.setattr(extcal, "_default_open", fake_open)

    event = _hermes_event(db, title="Йога", start="2037-07-20T13:00:00+00:00")
    _seed_export_row(db, event["id"])
    # TEST_NOW is 2037-07-15; default extcal_horizon_weeks=8 -> window ends
    # ~2037-09-09. Push start_utc well past that.
    db.execute("UPDATE events SET start_utc=? WHERE id=?",
               ("2038-01-01T00:00:00+00:00", event["id"]))
    db.commit()

    counts = extcal.export_own(db, _cfg(extcal_write_calendar=WRITE_URL), now_utc=TEST_NOW)
    assert counts["deleted"] == 1
    assert calls == ["DELETE"]
    assert db.execute(
        "SELECT * FROM ext_exports WHERE event_id=?", (event["id"],)
    ).fetchone() is None


def test_deleted_href_is_treated_as_already_gone_success(db, monkeypatch):
    """A previous tick's DELETE may have actually succeeded on the server
    even if this module never got to record that (crash, timeout on the
    response). A 404/410 on our own follow-up DELETE means "already gone"
    -- the exact end state this call wanted -- not a failure."""
    def fake_open(req, timeout):
        return extcal.Response(404, b"", {})
    monkeypatch.setattr(extcal, "_default_open", fake_open)

    event = _hermes_event(db, title="Йога", start="2037-07-20T13:00:00+00:00")
    _seed_export_row(db, event["id"])
    cal.cancel(db, event["id"])
    db.commit()

    counts = extcal.export_own(db, _cfg(extcal_write_calendar=WRITE_URL), now_utc=TEST_NOW)
    assert counts["deleted"] == 1
    assert counts["errors"] == []


# ---------------------------------------------------------------------
# owner='iphone' rows are NEVER exported -- structurally, by the query
# ---------------------------------------------------------------------

def test_owner_iphone_event_never_exported(db, monkeypatch):
    def boom(req, timeout):
        raise AssertionError("must not touch the network for an "
                              "owner='iphone' event")
    monkeypatch.setattr(extcal, "_default_open", boom)

    event = _hermes_event(db, title="Её событие", start="2037-07-20T13:00:00+00:00")
    db.execute("UPDATE events SET owner='iphone' WHERE id=?", (event["id"],))
    db.commit()

    counts = extcal.export_own(db, _cfg(extcal_write_calendar=WRITE_URL), now_utc=TEST_NOW)
    assert counts == {"exported": 0, "updated": 0, "unchanged": 0, "deleted": 0, "errors": []}


# ---------------------------------------------------------------------
# invariant #1: gate.deliver is never called on the export path
# ---------------------------------------------------------------------

def test_gate_deliver_never_called(db, monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("gate.deliver must never be called by export_own")
    monkeypatch.setattr(gate, "deliver", _boom)

    def fake_open(req, timeout):
        return extcal.Response(201, b"", {"ETag": '"e1"'})
    monkeypatch.setattr(extcal, "_default_open", fake_open)

    _hermes_event(db, title="Йога", start="2037-07-20T13:00:00+00:00")
    db.commit()
    extcal.export_own(db, _cfg(extcal_write_calendar=WRITE_URL), now_utc=TEST_NOW)
    # No assertion needed beyond "this didn't raise" -- _boom would have.


# ---------------------------------------------------------------------
# anti-echo (invariant #4), both belts, against export_own's OWN output
# ---------------------------------------------------------------------

def test_export_uid_matches_anti_echo_belt2_pattern(db):
    uid = extcal._export_uid(123)
    assert uid == "fam-123@hermes-home"
    assert extcal._HERMES_UID_RE.match(uid)
    # cli.py's own belt-2 regex (the one actually applied on the import
    # path) is written to recognize exactly the same pattern.
    assert cli._EXTCAL_ECHO_UID_RE.match(uid)


def test_belt1_excludes_the_configured_write_calendar_by_url(db):
    calendars = [{"url": WRITE_URL, "name": "Гермес"},
                 {"url": "https://caldav.icloud.com/1/calendars/personal/",
                  "name": "Personal"}]
    eligible = cli._extcal_eligible_calendars(_cfg(extcal_write_calendar=WRITE_URL), calendars)
    assert [c["name"] for c in eligible] == ["Personal"]


def test_belt2_filters_our_own_exported_event_even_with_write_url_blank(db, monkeypatch):
    """Requirement #7's hard case: extcal_write_calendar is BLANK (or
    wrong) in config at IMPORT time, so belt 1 (URL-based exclusion) does
    nothing at all -- belt 2 (UID pattern) must independently still keep
    our own exported event from being re-imported as a brand-new
    owner='iphone' row."""
    captured = {}

    def fake_open(req, timeout):
        if req.get_method() == "PUT":
            captured["body"] = req.data.decode("utf-8")
        return extcal.Response(201, b"", {"ETag": '"e1"'})
    monkeypatch.setattr(extcal, "_default_open", fake_open)

    _hermes_event(db, title="Йога", start="2037-07-20T13:00:00+00:00")
    db.commit()
    export_counts = extcal.export_own(db, _cfg(extcal_write_calendar=WRITE_URL), now_utc=TEST_NOW)
    assert export_counts["exported"] == 1
    exported_ics = captured["body"]
    assert "fam-" in exported_ics and "@hermes-home" in exported_ics

    # Import side, config's extcal_write_calendar left BLANK -- belt 1
    # cannot help here -- reading it back from a calendar that is NOT the
    # write target at all, exactly as if she were subscribed to "Гермес"
    # from a second device/account and it showed up as an ordinary
    # calendar to read.
    other_url = "https://caldav.icloud.com/1/calendars/personal/"
    monkeypatch.setattr(cli.gate, "load_config",
                         lambda *a, **k: _cfg(extcal_write_calendar=""))
    monkeypatch.setattr(cli.extcal, "discover",
                         lambda cfg, request=None: [
                             {"url": other_url, "name": "Personal", "ctag": "c1",
                              "sync_token": None, "supports_sync_token": True,
                              "components": ["VEVENT"]}])
    monkeypatch.setattr(cli.extcal, "fetch_changes",
                         lambda cfg, calendar, sync_token=None, request=None:
                         ([{"href": other_url + "echo.ics", "deleted": False,
                            "etag": "e9", "ics": exported_ics}],
                          None, {"mode": "initial_full", "reason": None}))

    rc = cli.cmd_tick_cal_ext(types.SimpleNamespace(now=TEST_NOW, json=False))
    assert rc == 0
    assert db.execute(
        "SELECT COUNT(*) AS n FROM events WHERE owner='iphone'"
    ).fetchone()["n"] == 0


def test_belt2_filters_our_own_exported_event_even_with_write_url_pointing_elsewhere(db, monkeypatch):
    """N3: the brief's literal "even with a WRONG URL in config" case, not
    just blank -- extcal_write_calendar at IMPORT time is set to a
    DIFFERENT, unrelated calendar than either the one export_own actually
    wrote to or the one the remote item is read from. Belt 1 is therefore
    inactive for a different reason than the blank case above (it matches
    the wrong thing, rather than nothing) -- belt 2 (UID pattern) must
    still independently keep this out."""
    captured = {}

    def fake_open(req, timeout):
        if req.get_method() == "PUT":
            captured["body"] = req.data.decode("utf-8")
        return extcal.Response(201, b"", {"ETag": '"e1"'})
    monkeypatch.setattr(extcal, "_default_open", fake_open)

    _hermes_event(db, title="Йога", start="2037-07-20T13:00:00+00:00")
    db.commit()
    export_counts = extcal.export_own(db, _cfg(extcal_write_calendar=WRITE_URL), now_utc=TEST_NOW)
    assert export_counts["exported"] == 1
    exported_ics = captured["body"]

    other_url = "https://caldav.icloud.com/1/calendars/personal/"
    wrong_write_url = "https://caldav.icloud.com/1/calendars/some-unrelated-one/"
    monkeypatch.setattr(cli.gate, "load_config",
                         lambda *a, **k: _cfg(extcal_write_calendar=wrong_write_url))
    monkeypatch.setattr(cli.extcal, "discover",
                         lambda cfg, request=None: [
                             {"url": other_url, "name": "Personal", "ctag": "c1",
                              "sync_token": None, "supports_sync_token": True,
                              "components": ["VEVENT"]}])
    monkeypatch.setattr(cli.extcal, "fetch_changes",
                         lambda cfg, calendar, sync_token=None, request=None:
                         ([{"href": other_url + "echo.ics", "deleted": False,
                            "etag": "e9", "ics": exported_ics}],
                          None, {"mode": "initial_full", "reason": None}))

    rc = cli.cmd_tick_cal_ext(types.SimpleNamespace(now=TEST_NOW, json=False))
    assert rc == 0
    assert db.execute(
        "SELECT COUNT(*) AS n FROM events WHERE owner='iphone'"
    ).fetchone()["n"] == 0


# ---------------------------------------------------------------------
# requirement #10: an export error reaches tick.error the SAME way an
# import error does
# ---------------------------------------------------------------------

def test_export_error_reaches_tick_error_same_path_as_import_error(db, monkeypatch):
    def fake_open(req, timeout):
        if req.get_method() == "PUT":
            return extcal.Response(500, b"", {})
        return extcal.Response(207, b"<multistatus/>", {})
    monkeypatch.setattr(extcal, "_default_open", fake_open)

    monkeypatch.setattr(cli.gate, "load_config",
                         lambda *a, **k: _cfg(extcal_write_calendar=WRITE_URL))
    monkeypatch.setattr(cli.extcal, "discover", lambda cfg, request=None: [])

    _hermes_event(db, title="Йога", start="2037-07-20T13:00:00+00:00")
    db.commit()

    rc = cli.cmd_tick_cal_ext(types.SimpleNamespace(now=TEST_NOW, json=False))
    assert rc == 1

    rows = db.execute(
        "SELECT payload FROM audit_log WHERE kind='tick.error' ORDER BY id"
    ).fetchall()
    assert len(rows) == 1
    payload = json.loads(rows[0]["payload"])
    assert payload["where"] == "cal-ext"
    assert "export." in payload["error"]

    export_error_rows = db.execute(
        "SELECT payload FROM audit_log WHERE kind='cal.ext.export_error'"
    ).fetchall()
    assert len(export_error_rows) == 1


def test_export_wired_into_tick_and_reported_in_cal_ext_sync_audit(db, monkeypatch):
    def fake_open(req, timeout):
        return extcal.Response(201, b"", {"ETag": '"e1"'})
    monkeypatch.setattr(extcal, "_default_open", fake_open)

    monkeypatch.setattr(cli.gate, "load_config",
                         lambda *a, **k: _cfg(extcal_write_calendar=WRITE_URL))
    monkeypatch.setattr(cli.extcal, "discover", lambda cfg, request=None: [])

    _hermes_event(db, title="Йога", start="2037-07-20T13:00:00+00:00")
    db.commit()

    rc = cli.cmd_tick_cal_ext(types.SimpleNamespace(now=TEST_NOW, json=False))
    assert rc == 0

    rows = db.execute(
        "SELECT payload FROM audit_log WHERE kind='cal.ext.sync' ORDER BY id"
    ).fetchall()
    assert len(rows) == 1
    payload = json.loads(rows[0]["payload"])
    assert payload["export"]["exported"] == 1

    assert db.execute(
        "SELECT COUNT(*) AS n FROM ext_exports"
    ).fetchone()["n"] == 1


def test_dry_run_never_calls_export_own(db, monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("export_own must not run on --dry-run")
    monkeypatch.setattr(cli.extcal, "export_own", _boom)
    monkeypatch.setattr(cli.gate, "load_config",
                         lambda *a, **k: _cfg(extcal_write_calendar=WRITE_URL))
    monkeypatch.setattr(cli.extcal, "discover", lambda cfg, request=None: [])

    _hermes_event(db, title="Йога", start="2037-07-20T13:00:00+00:00")
    db.commit()

    rc = cli.cmd_tick_cal_ext(types.SimpleNamespace(now=TEST_NOW, dry_run=True, json=False))
    assert rc == 0
