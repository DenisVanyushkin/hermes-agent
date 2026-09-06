"""S6 route planner and write-only target tests."""

import json
import sqlite3
import types

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


def event(db, subject=None, title="Taya event"):
    row = cal.add(
        db,
        title,
        "2037-07-20T13:00:00+00:00",
        end_utc="2037-07-20T14:00:00+00:00",
        subject_person_id=subject,
    )
    db.commit()
    return row


def seed_people(db):
    taya = people.add(db, "Тая", slug="taya")
    amina = people.add(db, "Амина", slug="amina")
    db.commit()
    return amina, taya


def journal(db, table, event_id, url, etag='"e0"'):
    db.execute(
        f"INSERT INTO {table}(event_id,href,etag,body_hash,synced_at) "
        "VALUES(?,?,?,?,?)",
        (event_id, f"{url}fam-{event_id}@hermes-home.ics", etag, "v2:" + "0" * 64, NOW),
    )
    db.commit()


def test_v15_fresh_schema_has_same_shape_and_migration_marker(db):
    hermes = {tuple(row) for row in db.execute("PRAGMA table_info(ext_exports)")}
    taya = {tuple(row) for row in db.execute("PRAGMA table_info(ext_exports_taya)")}
    assert taya == hermes
    assert (
        db.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0]
        == "15"
    )


def test_subject_routes_to_exactly_one_target_destination_first(db):
    _, taya = seed_people(db)
    row = event(db, taya["id"])
    calls = []

    def request(method, url, **kwargs):
        calls.append((method, url))
        if method == "PUT":
            return extcal.Response(201, b"", {"ETag": '"taya-e1"'})
        return extcal.Response(204, b"", {})

    counts = extcal.export_routes(db, cfg(), request=request, now_utc=NOW)

    assert counts["exported"] == 1
    assert [method for method, _ in calls] == ["PUT"]
    assert calls[0][1].startswith(TAYA)
    assert (
        db.execute(
            "SELECT event_id FROM ext_exports_taya WHERE event_id=?", (row["id"],)
        ).fetchone()
        is not None
    )
    assert (
        db.execute(
            "SELECT event_id FROM ext_exports WHERE event_id=?", (row["id"],)
        ).fetchone()
        is None
    )


def test_route_transition_moves_destination_and_removes_only_local_source(db):
    _, taya = seed_people(db)
    row = event(db, taya["id"])
    journal(db, "ext_exports", row["id"], HERMES)
    body = extcal._build_export_vevent(
        row, "", [], extcal._coerce_utc_dt(NOW)
    ).encode()
    calls = []

    def request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        if method == "GET":
            assert url.startswith(TAYA)
            if len([call for call in calls if call[0] == "GET"]) == 1:
                return extcal.Response(404, b"", {})
            return extcal.Response(200, body, {"ETag": '"taya-e1"'})
        if method == "MOVE":
            assert url.startswith(HERMES)
            assert kwargs["headers"]["Destination"].startswith(TAYA)
            assert kwargs["headers"]["Overwrite"] == "F"
            return extcal.Response(201, b"", {})
        pytest.fail("route transition must not PUT or network DELETE")

    counts = extcal.export_routes(db, cfg(), request=request, now_utc=NOW)

    assert counts["exported"] == 1
    assert counts["deleted"] == 1
    assert [method for method, _, _ in calls] == ["GET", "MOVE", "GET"]
    assert db.execute("SELECT COUNT(*) FROM ext_exports").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM ext_exports_taya").fetchone()[0] == 1



def test_cleanup_has_priority_and_never_puts_without_taya_config(db):
    _, taya = seed_people(db)
    row = event(db, taya["id"])
    journal(db, "ext_exports", row["id"], HERMES)
    journal(db, "ext_exports_taya", row["id"], TAYA)
    db.execute("UPDATE events SET status='cancelled' WHERE id=?", (row["id"],))
    db.commit()
    calls = []

    def request(method, url, **kwargs):
        calls.append(method)
        return extcal.Response(204, b"", {})

    counts = extcal.export_routes(
        db, cfg(extcal_taya_calendar=""), request=request, now_utc=NOW
    )

    assert counts["deleted"] == 2
    assert calls == ["DELETE", "DELETE"]
    assert db.execute("SELECT COUNT(*) FROM ext_exports").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM ext_exports_taya").fetchone()[0] == 0


def test_empty_taya_url_does_not_fallback_to_hermes(db):
    _, taya = seed_people(db)
    row = event(db, taya["id"])
    calls = []
    counts = extcal.export_routes(
        db,
        cfg(extcal_taya_calendar=""),
        request=lambda *args, **kwargs: calls.append(args),
        now_utc=NOW,
    )
    assert calls == []
    assert counts["exported"] == 0
    assert counts["errors"][0]["event_id"] == row["id"]
    assert (
        db.execute(
            "SELECT COUNT(*) FROM ext_exports WHERE event_id=?", (row["id"],)
        ).fetchone()[0]
        == 0
    )


def test_identical_write_urls_make_global_export_noop(db):
    _, taya = seed_people(db)
    row = event(db, taya["id"])
    calls = []
    counts = extcal.export_routes(
        db,
        cfg(extcal_taya_calendar=HERMES),
        request=lambda *args, **kwargs: calls.append(args),
        now_utc=NOW,
    )
    assert calls == []
    assert counts == {
        "exported": 0,
        "updated": 0,
        "unchanged": 0,
        "deleted": 0,
        "retained": 0,
        "errors": [],
        "conflicts": [],
    }
    assert row["id"] not in {
        r[0] for r in db.execute("SELECT event_id FROM ext_exports_taya")
    }


def test_route_plan_is_global_and_dry_run_is_redacted(db):
    _, taya = seed_people(db)
    row = event(db, taya["id"], title="private title")
    plan = extcal._export_route_plan(db, cfg(), extcal._coerce_utc_dt(NOW))
    assert [(p["event_id"], p["target"], p["action"]) for p in plan] == [
        (row["id"], "taya", "insert")
    ]
    from fam import cli

    preview = cli._dry_run_export_summary(plan)
    text = json.dumps(preview, ensure_ascii=False)
    assert preview["target_event_ids"] == {"hermes": [], "taya": [row["id"]]}
    assert "private title" not in text
    assert TAYA not in text
    assert "BEGIN:VCALENDAR" not in text


def test_probe_excludes_both_write_urls_before_read_allowlist(db, monkeypatch):
    calendars = [
        {"url": HERMES, "name": "Hermes", "ctag": "h"},
        {"url": TAYA, "name": "Taya", "ctag": "t"},
        {
            "url": "https://caldav.icloud.com/1/calendars/personal/",
            "name": "Taya",
            "ctag": "p",
        },
    ]
    monkeypatch.setattr(extcal, "_discover", lambda cfg, request: (calendars, []))
    monkeypatch.setattr(
        extcal,
        "fetch_changes",
        lambda *args, **kwargs: (
            [],
            "token",
            {"mode": "sync_collection", "reason": None},
        ),
    )
    result = extcal.probe(
        cfg(extcal_read_calendars=["Hermes", "Taya", "personal"]),
        request=lambda *args, **kwargs: None,
    )
    assert [row["url"] for row in result["calendars"]] == [
        "https://caldav.icloud.com/1/calendars/personal/"
    ]


def test_uid_belt_remains_independent_of_url_filter(db):
    cfg_value = cfg()
    ics = (
        "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\n"
        "UID:fam-77@hermes-home\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
    )
    counts = {"total": 0, "timed": 0, "all_day": 0, "recurring": 0}
    extcal._tally_ics(counts, ics)
    assert counts["total"] == 0








def test_route_transition_remote_edit_is_conflict_without_delete(db):
    _, taya = seed_people(db)
    row = event(db, taya["id"])
    journal(db, "ext_exports", row["id"], HERMES)
    journal(db, "ext_exports_taya", row["id"], TAYA, etag='"taya-e1"')
    db.execute(
        "UPDATE ext_exports_taya SET body_hash=? WHERE event_id=?",
        (extcal._export_body_hash(row, "", []), row["id"]),
    )
    db.commit()
    remote_event = dict(row)
    remote_event["title"] = "phone edit"
    body = extcal._build_export_vevent(
        remote_event, "", [], extcal._coerce_utc_dt(NOW)
    )
    calls = []

    def request(method, url, **kwargs):
        calls.append(method)
        assert method == "GET"
        assert url.startswith(TAYA)
        return extcal.Response(200, body.encode(), {"ETag": '"phone"'})

    counts = extcal.export_routes(db, cfg(), request=request, now_utc=NOW)

    assert counts["conflicts"]
    assert calls == ["GET"]
    assert db.execute("SELECT COUNT(*) FROM ext_exports").fetchone()[0] == 1
    assert db.execute("SELECT COUNT(*) FROM ext_exports_taya").fetchone()[0] == 1



def test_iphone_owned_events_never_reach_taya_reconciler(db):
    _, taya = seed_people(db)
    row = event(db, taya["id"])
    db.execute("UPDATE events SET owner='iphone' WHERE id=?", (row["id"],))
    db.commit()
    calls = []
    counts = extcal.export_routes(
        db, cfg(), request=lambda *a, **k: calls.append(a), now_utc=NOW
    )
    assert calls == []
    assert counts["exported"] == 0
    assert db.execute("SELECT COUNT(*) FROM ext_exports_taya").fetchone()[0] == 0


def test_cli_ingest_filter_excludes_both_write_urls_before_name_allowlist(db):
    from fam import cli

    calendars = [
        {"url": HERMES, "name": "Personal"},
        {"url": TAYA, "name": "Personal"},
        {"url": "https://caldav.icloud.com/1/calendars/read/", "name": "Personal"},
    ]
    eligible = cli._extcal_eligible_calendars(
        cfg(extcal_read_calendars=["Personal"]), calendars
    )
    assert [row["url"] for row in eligible] == [
        "https://caldav.icloud.com/1/calendars/read/"
    ]


def test_real_cmd_tick_cal_ext_executes_global_route(db, monkeypatch, capsys):
    from fam import cli

    _, taya = seed_people(db)
    row = event(db, taya["id"])
    configuration = cfg()
    monkeypatch.setattr(cli.gate, "load_config", lambda *a, **k: configuration)
    monkeypatch.setattr(cli.extcal, "discover", lambda *a, **k: [])
    monkeypatch.setattr(
        cli.gate,
        "deliver",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("reverse export must not deliver a message")
        ),
    )
    calls = []

    def request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        return cli.extcal.Response(201, b"", {"ETag": '"tick-e1"'})

    monkeypatch.setattr(cli.extcal, "_request", request)
    assert cli.cmd_tick_cal_ext(types.SimpleNamespace(now=NOW, json=True)) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["export"]["exported"] == 1
    assert calls and calls[0][0] == "PUT" and TAYA in calls[0][1]
    assert (
        db.execute(
            "SELECT COUNT(*) FROM ext_exports_taya WHERE event_id=?", (row["id"],)
        ).fetchone()[0]
        == 1
    )


def test_v15_migration_from_v14_creates_only_taya_journal(db):
    db.execute("DROP TABLE ext_exports_taya")
    db.execute("UPDATE meta SET value='14' WHERE key='schema_version'")
    db.commit()
    from fam import db as famdb

    famdb.init_db(db)
    assert db.execute("SELECT COUNT(*) FROM ext_exports").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM ext_exports_taya").fetchone()[0] == 0
    assert (
        db.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0]
        == "15"
    )


def test_route_transition_back_to_hermes_uses_move(db):
    _, taya = seed_people(db)
    row = event(db, taya["id"])
    journal(db, "ext_exports_taya", row["id"], TAYA, etag='"taya-e1"')
    db.execute("UPDATE events SET subject_person_id=NULL WHERE id=?", (row["id"],))
    db.commit()
    body = extcal._build_export_vevent(
        row, "", [], extcal._coerce_utc_dt(NOW)
    ).encode()
    calls = []

    def request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        if method == "GET":
            assert url.startswith(HERMES)
            if len([call for call in calls if call[0] == "GET"]) == 1:
                return extcal.Response(404, b"", {})
            return extcal.Response(200, body, {"ETag": '"hermes-e1"'})
        if method == "MOVE":
            assert url.startswith(TAYA)
            assert kwargs["headers"]["Destination"].startswith(HERMES)
            return extcal.Response(201, b"", {})
        pytest.fail("route transition must not PUT or network DELETE")

    counts = extcal.export_routes(db, cfg(), request=request, now_utc=NOW)

    assert counts["exported"] == 1 and counts["deleted"] == 1
    assert [method for method, _, _ in calls] == ["GET", "MOVE", "GET"]
    assert db.execute("SELECT COUNT(*) FROM ext_exports").fetchone()[0] == 1
    assert db.execute("SELECT COUNT(*) FROM ext_exports_taya").fetchone()[0] == 0



def test_empty_live_set_has_zero_route_count(db):
    counts = extcal.export_routes(
        db,
        cfg(),
        request=lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("empty planner must not call transport")
        ),
        now_utc=NOW,
    )
    assert counts == {
        "exported": 0,
        "updated": 0,
        "unchanged": 0,
        "deleted": 0,
        "retained": 0,
        "errors": [],
        "conflicts": [],
    }


def test_taya_slug_is_unique_in_database(db):
    people.add(db, "Тая", slug="taya")
    db.commit()
    with pytest.raises(sqlite3.IntegrityError):
        people.add(db, "Тая 2", slug="taya")
    db.rollback()



def test_taya_export_has_no_valarm_and_never_delivers_gate(db, monkeypatch):
    _, taya = seed_people(db)
    direct = event(db, taya["id"], title="direct Taya")
    transition = event(db, taya["id"], title="transition Taya")
    hermes = event(db, title="direct Hermes")
    journal(db, "ext_exports", transition["id"], HERMES)
    transition_body = extcal._build_export_vevent(
        transition, "", [], extcal._coerce_utc_dt(NOW)
    ).encode()
    bodies = []
    calls = []
    monkeypatch.setattr(
        gate,
        "deliver",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("Taya export must not deliver a gate message")
        ),
    )

    def request(method, url, **kwargs):
        calls.append((method, url))
        if method == "PUT":
            body = kwargs["body"]
            bodies.append(body.decode() if isinstance(body, bytes) else body)
            return extcal.Response(201, b"", {"ETag": '"e1"'})
        if method == "GET":
            assert url.startswith(TAYA)
            if len([call for call in calls if call[0] == "GET"]) == 1:
                return extcal.Response(404, b"", {})
            return extcal.Response(200, transition_body, {"ETag": '"moved"'})
        if method == "MOVE":
            assert kwargs["headers"]["Destination"].startswith(TAYA)
            return extcal.Response(201, b"", {})
        pytest.fail("Taya route must not use network DELETE")

    counts = extcal.export_routes(db, cfg(), request=request, now_utc=NOW)

    assert counts["exported"] == 3
    assert counts["deleted"] == 1
    assert direct["id"] != transition["id"]
    assert hermes["id"] not in (direct["id"], transition["id"])
    assert len(bodies) == 2
    assert all("VALARM" not in body for body in bodies)
    assert {method for method, _ in calls} == {"PUT", "MOVE", "GET"}
    assert [method for method, _ in calls].count("MOVE") == 1
    assert "DELETE" not in [method for method, _ in calls]



def test_adopted_events_and_plans_never_reach_taya_reconciler(db):
    _, taya = seed_people(db)
    adopted = event(db, taya["id"], title="adopted phone event")
    db.execute(
        "UPDATE events SET owner='iphone', external_uid=? WHERE id=?",
        ("iphone-adopted-1", adopted["id"]),
    )
    db.execute(
        "INSERT INTO plans(title, person_id, deadline, status, created_at) "
        "VALUES (?, ?, ?, 'open', ?)",
        ("Taya preparation", taya["id"], "2037-07-20", NOW),
    )
    db.commit()
    calls = []

    counts = extcal.export_routes(
        db,
        cfg(),
        request=lambda *args, **kwargs: calls.append(args),
        now_utc=NOW,
    )

    assert counts["exported"] == 0
    assert counts["updated"] == 0
    assert counts["deleted"] == 0
    assert calls == []
    assert (
        db.execute(
            "SELECT COUNT(*) FROM ext_exports_taya WHERE event_id=?", (adopted["id"],)
        ).fetchone()[0]
        == 0
    )


def test_subject_filter_ids_match_view_dry_run_and_global_route_plan(db):
    _, taya = seed_people(db)
    taya_row = event(db, taya["id"], title="subject filtered Taya")
    event(db, title="unrelated Hermes")
    viewed = {
        row["id"]
        for row in cal.list_range(
            db, "2037-07-15T00:00:00+00:00", "2037-07-21T00:00:00+00:00",
            subject_person_id=taya["id"])
    }
    from fam import cli
    export_plan = extcal._export_route_plan(db, cfg(), extcal._coerce_utc_dt(NOW))
    dry_run_ids = set(
        cli._dry_run_export_summary(export_plan)["target_event_ids"]["taya"]
    )
    route_ids = {
        item["event_id"] for item in export_plan
        if item.get("target") == "taya"
    }
    assert viewed == {taya_row["id"]}
    assert dry_run_ids == viewed
    assert route_ids == viewed
