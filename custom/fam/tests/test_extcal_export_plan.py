"""S1: export retention and explicit DELETE reasons."""
import json
import types

from fam import cal, cli, extcal, gate
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
    })
    cfg.update(over)
    return cfg


def _event(conn, title, start):
    return cal.add(conn, title, start)


def _seed_export(conn, event_id, body_hash="stale-hash"):
    href = f"{WRITE_URL}fam-{event_id}@hermes-home.ics"
    conn.execute(
        "INSERT INTO ext_exports(event_id, href, etag, body_hash, synced_at) "
        "VALUES (?,?,?,?,?)",
        (event_id, href, '"e0"', body_hash, TEST_NOW),
    )
    conn.commit()
    return href


def _plan_by_id(plan):
    return {entry["event_id"]: entry for entry in plan}


def _audit_rows(conn, kind):
    rows = conn.execute(
        "SELECT payload FROM audit_log WHERE kind=? ORDER BY id", (kind,)
    ).fetchall()
    return [json.loads(row["payload"]) for row in rows]


def test_active_past_journal_is_retained_without_network(db):
    event = _event(db, "Прошедшее", "2037-07-10T13:00:00+00:00")
    _seed_export(db, event["id"])
    calls = []

    def request(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("retained event must not use the transport")

    plan = extcal._export_plan(db, _cfg(), extcal._coerce_utc_dt(TEST_NOW))
    entry = _plan_by_id(plan)[event["id"]]
    assert entry["action"] == "retain"
    assert entry["reason"] == "past_retained"

    counts = extcal.export_own(db, _cfg(), request=request, now_utc=TEST_NOW)
    assert counts == {
        "exported": 0, "updated": 0, "unchanged": 0,
        "deleted": 0, "retained": 1, "errors": [],
    }
    assert calls == []
    assert db.execute(
        "SELECT event_id FROM ext_exports WHERE event_id=?", (event["id"],)
    ).fetchone() is not None


def test_export_plan_matrix_uses_explicit_reasons_and_horizon_rules(db):
    cancelled = _event(db, "Отменено", "2037-07-10T13:00:00+00:00")
    done = _event(db, "Завершено", "2037-07-20T13:00:00+00:00")
    owner_changed = _event(db, "Передано", "2037-07-20T14:00:00+00:00")
    external_uid = _event(db, "Усыновлено", "2037-07-20T15:00:00+00:00")
    future_journal = _event(db, "Далеко, но уже экспортировано", "2037-12-20T13:00:00+00:00")
    future_new = _event(db, "Далеко и ещё не экспортировано", "2037-12-20T14:00:00+00:00")
    in_window = _event(db, "В окне", "2037-07-20T16:00:00+00:00")

    for event in (cancelled, done, owner_changed, external_uid, future_journal):
        _seed_export(db, event["id"])
    cal.cancel(db, cancelled["id"])
    cal.done(db, done["id"])
    db.execute("UPDATE events SET owner='iphone' WHERE id=?", (owner_changed["id"],))
    db.execute("UPDATE events SET external_uid='legacy-uid' WHERE id=?", (external_uid["id"],))
    db.commit()

    plan = extcal._export_plan(db, _cfg(), extcal._coerce_utc_dt(TEST_NOW))
    by_id = _plan_by_id(plan)
    assert (by_id[cancelled["id"]]["action"], by_id[cancelled["id"]]["reason"]) == ("delete", "cancelled")
    assert (by_id[done["id"]]["action"], by_id[done["id"]]["reason"]) == ("delete", "done")
    assert (by_id[owner_changed["id"]]["action"], by_id[owner_changed["id"]]["reason"]) == ("delete", "owner_changed")
    assert (by_id[external_uid["id"]]["action"], by_id[external_uid["id"]]["reason"]) == ("delete", "external_uid")
    assert by_id[future_journal["id"]]["reason"] == "future_retained"
    assert by_id[future_journal["id"]]["action"] in {"update", "unchanged"}
    assert (by_id[future_new["id"]]["action"], by_id[future_new["id"]]["reason"]) == ("retain", "not_yet_eligible")
    assert (by_id[in_window["id"]]["action"], by_id[in_window["id"]]["reason"]) == ("insert", "in_window")


def test_cancelled_past_event_has_delete_priority_over_retention(db):
    event = _event(db, "Отменено после даты", "2037-07-10T13:00:00+00:00")
    _seed_export(db, event["id"])
    cal.cancel(db, event["id"])
    db.commit()

    entry = _plan_by_id(
        extcal._export_plan(db, _cfg(), extcal._coerce_utc_dt(TEST_NOW))
    )[event["id"]]
    assert entry["action"] == "delete"
    assert entry["reason"] == "cancelled"


def test_legacy_external_uid_journal_is_planned_for_cleanup(db):
    event = _event(db, "Легаси", "2037-07-20T13:00:00+00:00")
    _seed_export(db, event["id"])
    db.execute("UPDATE events SET external_uid='iphone-uid' WHERE id=?", (event["id"],))
    db.commit()
    calls = []

    def request(method, url, **kwargs):
        calls.append(method)
        return extcal.Response(204, b"", {})

    counts = extcal.export_own(db, _cfg(), request=request, now_utc=TEST_NOW)
    assert counts["deleted"] == 1
    assert counts["retained"] == 0
    assert calls == ["DELETE"]


def test_cmd_tick_cal_ext_composes_retention_and_resets_export_streak(
    db, monkeypatch, capsys
):
    for event_id, start in ((65, "2037-07-10T05:00:00+00:00"), (150, "2037-07-11T08:00:00+00:00")):
        event = _event(db, f"poison-{event_id}", start)
        db.execute("UPDATE events SET id=? WHERE id=?", (event_id, event["id"]))
        _seed_export(db, event_id)

    cfg = _cfg(extcal_fail_streak_threshold=1)
    monkeypatch.setattr(cli.gate, "load_config", lambda *a, **k: cfg)
    monkeypatch.setattr(
        cli.extcal, "discover",
        lambda cfg, request=None: [{
            "url": "https://caldav.icloud.com/1/calendars/personal/",
            "name": "Calendar", "ctag": "c1", "sync_token": "tok",
            "supports_sync_token": True, "components": ["VEVENT"],
        }],
    )
    monkeypatch.setattr(
        cli.extcal, "fetch_changes",
        lambda cfg, calendar, sync_token=None, request=None, force_full=False:
        ([], "next", {"mode": "sync_collection", "reason": None}),
    )
    famdb.meta_set(db, "extcal_last_mode:https://caldav.icloud.com/1/calendars/personal/", "sync_collection")
    db.commit()
    calls = []

    def request(method, url, **kwargs):
        calls.append(method)
        raise AssertionError("S1 composition must not send retained rows")

    monkeypatch.setattr(cli.extcal, "_request", request)
    args = types.SimpleNamespace(now=TEST_NOW, json=True)
    assert cli.cmd_tick_cal_ext(args) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["export"]["retained"] == 2
    assert out["export"]["deleted"] == 0
    assert out["export"]["errors"] == []
    assert calls == []
    assert famdb.meta_get(
        db, cli._extcal_streak_meta_key(cli._EXTCAL_STREAK_IMPORT_APPLY_KEY)
    ) == "0"
    assert famdb.meta_get(db, "extcal_last_ok") == TEST_NOW
    assert _audit_rows(db, "cal.ext.sync") == []


def test_cmd_tick_cal_ext_audits_real_export_action(db, monkeypatch):
    event = _event(db, "В окне", "2037-07-20T13:00:00+00:00")
    cfg = _cfg(extcal_fail_streak_threshold=1)
    monkeypatch.setattr(cli.gate, "load_config", lambda *a, **k: cfg)
    calendar_url = "https://caldav.icloud.com/1/calendars/personal/"
    monkeypatch.setattr(
        cli.extcal, "discover",
        lambda cfg, request=None: [{
            "url": calendar_url, "name": "Calendar", "ctag": "c1",
            "sync_token": "tok", "supports_sync_token": True,
            "components": ["VEVENT"],
        }],
    )
    monkeypatch.setattr(
        cli.extcal, "fetch_changes",
        lambda cfg, calendar, sync_token=None, request=None, force_full=False:
        ([], "next", {"mode": "sync_collection", "reason": None}),
    )
    famdb.meta_set(db, f"extcal_last_mode:{calendar_url}", "sync_collection")
    db.commit()
    calls = []

    def request(method, url, **kwargs):
        calls.append(method)
        return extcal.Response(201, b"", {"ETag": '"e1"'})

    monkeypatch.setattr(cli.extcal, "_request", request)
    args = types.SimpleNamespace(now=TEST_NOW)
    assert cli.cmd_tick_cal_ext(args) == 0
    assert calls == ["PUT"]
    rows = _audit_rows(db, "cal.ext.sync")
    assert len(rows) == 1
    assert rows[0]["export"]["exported"] == 1
    assert rows[0]["export"]["retained"] == 0
