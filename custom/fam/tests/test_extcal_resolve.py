from fam import cal, cli, extcal, gate
import json
import types

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


def _event(db, title="Local title"):
    event = cal.add(db, title, "2037-07-20T13:00:00+00:00",
                     end_utc="2037-07-20T14:00:00+00:00")
    db.commit()
    return event


def _ics(event_id, title="Remote title", uid=None):
    uid = uid or f"fam-{event_id}@hermes-home"
    return ("\r\n".join([
        "BEGIN:VCALENDAR", "VERSION:2.0", "BEGIN:VEVENT",
        f"UID:{uid}", "DTSTART:20370720T130000Z",
        "DTEND:20370720T140000Z", f"SUMMARY:{title}",
        "END:VEVENT", "END:VCALENDAR", ""
    ]))


def _make_conflict(db, event):
    def request(method, url, **kwargs):
        if method == "PUT":
            return extcal.Response(412, b"", {})
        return extcal.Response(200, _ics(event["id"]).encode(),
                               {"ETag": '"phone"'})
    counts = extcal.export_routes(db, _cfg(), request=request, now_utc=NOW)
    assert counts["conflicts"]
    return counts


def test_keep_remote_uses_cal_update_and_resolves_issue(db):
    event = _event(db)
    _make_conflict(db, event)

    calls = []

    def request(method, url, **kwargs):
        calls.append(method)
        if method == "GET":
            return extcal.Response(200, _ics(event["id"], "Phone title").encode(),
                                   {"ETag": '"phone"'})
        raise AssertionError("keep-remote must not PUT")

    result = extcal.resolve_conflict(
        db, event["id"], decision="keep-remote", cfg=_cfg(),
        request=request, now_utc=NOW,
    )

    assert result["resolved"] is True
    assert calls == ["GET"]
    assert cal.get(db, event["id"])["title"] == "Phone title"
    assert db.execute("SELECT 1 FROM extcal_export_issues WHERE event_id=?",
                      (event["id"],)).fetchone() is None
    assert db.execute("SELECT kind FROM audit_log WHERE kind='cal.ext.resolve'"
                      ).fetchone() is not None


def test_force_push_requires_explicit_get_then_one_put(db):
    event = _event(db)
    _make_conflict(db, event)
    calls = []

    def request(method, url, **kwargs):
        calls.append(method)
        if method == "GET":
            return extcal.Response(200, _ics(event["id"]).encode(),
                                   {"ETag": '"phone"'})
        return extcal.Response(204, b"", {"ETag": '"hermes"'})

    result = extcal.resolve_conflict(
        db, event["id"], decision="force-push", cfg=_cfg(),
        request=request, now_utc=NOW,
    )

    assert result["resolved"] is True
    assert calls == ["GET", "PUT"]
    assert db.execute("SELECT 1 FROM extcal_export_issues WHERE event_id=?",
                      (event["id"],)).fetchone() is None


def test_keep_remote_hook_failure_rolls_back_issue_and_local_state(db, monkeypatch):
    event = _event(db)
    _make_conflict(db, event)
    db.execute(
        "INSERT INTO ext_exports(event_id, href, etag, body_hash, synced_at) "
        "VALUES (?,?,?,?,?)",
        (event["id"], f"{WRITE_URL}fam-{event['id']}@hermes-home.ics",
         '"phone"', "v2:" + "0" * 64, NOW),
    )
    db.commit()
    before_event = dict(db.execute("SELECT * FROM events WHERE id=?",
                                   (event["id"],)).fetchone())
    before_export = dict(db.execute("SELECT * FROM ext_exports WHERE event_id=?",
                                    (event["id"],)).fetchone())
    before_issue = dict(db.execute(
        "SELECT * FROM extcal_export_issues WHERE event_id=?",
        (event["id"],)).fetchone())

    def request(method, url, **kwargs):
        return extcal.Response(200, _ics(event["id"], "Phone title").encode(),
                               {"ETag": '"phone"'})

    def boom(*args, **kwargs):
        raise RuntimeError("hook failure")

    monkeypatch.setattr(extcal.cal, "update", boom)
    try:
        extcal.resolve_conflict(
            db, event["id"], decision="keep-remote", cfg=_cfg(),
            request=request, now_utc=NOW,
        )
    except RuntimeError:
        pass
    else:
        raise AssertionError("hook failure must fail closed")

    assert dict(db.execute("SELECT * FROM events WHERE id=?",
                           (event["id"],)).fetchone()) == before_event
    assert dict(db.execute("SELECT * FROM ext_exports WHERE event_id=?",
                           (event["id"],)).fetchone()) == before_export
    assert dict(db.execute("SELECT * FROM extcal_export_issues WHERE event_id=?",
                           (event["id"],)).fetchone()) == before_issue


def test_conflicts_json_has_only_safe_operator_fields(db, monkeypatch, capsys):
    event = _event(db)
    _make_conflict(db, event)
    monkeypatch.setattr(cli.famdb, "connect", lambda: db)

    assert cli.cmd_cal_ext_conflicts(types.SimpleNamespace(json=True)) == 0
    output = json.loads(capsys.readouterr().out)
    assert set(output[0]) == {
        "event_id", "target", "action", "reason_code",
        "first_seen_utc", "last_seen_utc",
    }
    assert "Local title" not in json.dumps(output, ensure_ascii=False)
    assert WRITE_URL not in json.dumps(output, ensure_ascii=False)

def test_conflict_tick_holds_last_ok_but_moves_heartbeat_without_streak(db, monkeypatch):
    event = _event(db)
    cfg = _cfg(extcal_fail_streak_threshold=1)
    old_ok = "2037-07-14T00:00:00+00:00"
    from fam import db as famdb
    famdb.meta_set(db, "extcal_last_ok", old_ok)
    db.commit()
    monkeypatch.setattr(cli.gate, "load_config", lambda: cfg)
    monkeypatch.setattr(cli.famdb, "connect", lambda: db)
    monkeypatch.setattr(cli.extcal, "discover", lambda cfg, request=None: [])
    calls = []

    def fake_open(req, timeout):
        method = req.get_method()
        calls.append(method)
        if method == "PUT":
            return extcal.Response(412, b"", {})
        return extcal.Response(200, _ics(event["id"]).encode(),
                               {"ETag": '"phone"'})

    monkeypatch.setattr(cli.extcal, "_default_open", fake_open)
    assert cli.cmd_tick_cal_ext(types.SimpleNamespace(now=NOW)) == 0
    assert famdb.meta_get(db, "extcal_last_ok") == old_ok
    assert famdb.meta_get(db, "extcal_last_run") == NOW
    assert famdb.meta_get(db, "extcal_fail_streak:__export__") == "0"
    assert calls == ["PUT", "GET"]

    monkeypatch.setattr(cli.extcal, "_default_open",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("quarantine must not touch network")))
    assert cli.cmd_tick_cal_ext(types.SimpleNamespace(
        now="2037-07-15T01:00:00+00:00")) == 0
    assert famdb.meta_get(db, "extcal_last_run") == "2037-07-15T01:00:00+00:00"
    assert db.execute("SELECT COUNT(*) FROM audit_log "
                      "WHERE kind='cal.ext.conflict'").fetchone()[0] == 1
