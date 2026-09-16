"""S2: terminal heartbeat and independent cal-ext failure accounting."""
import sqlite3
import types

from fam import cli
from fam import db as famdb


TEST_NOW = "2037-07-15T00:00:00+00:00"
CAL_URL = "https://caldav.icloud.com/1/calendars/home/"


def _cfg(**over):
    cfg = {
        "extcal_enabled": True,
        "extcal_fail_streak_threshold": 1,
    }
    cfg.update(over)
    return cfg


def _args(*, dry_run=False):
    args = types.SimpleNamespace(now=TEST_NOW)
    if dry_run:
        args.dry_run = True
    return args


def _result(*, apply_errors=None, export_errors=None):
    return {
        "counts": {
            "events_inserted": 0, "events_updated": 0,
            "events_cancelled": 0, "plans_inserted": 0,
            "plans_updated": 0, "plans_dropped": 0,
            "collisions": 0, "errors": apply_errors or [],
        },
        "calendars": [
            {"url": CAL_URL, "name": "Home",
             "mode": "sync_collection", "reason": None},
        ],
        "changeset": {
            "events": {"insert": [], "update": [], "cancel": []},
            "plans": {"insert": [], "update": [], "drop": []},
            "collisions": [],
        },
        "sync_errors": [],
        "tokens": {},
        "export_counts": {
            "exported": 0, "updated": 0, "unchanged": 0,
            "deleted": 0, "retained": 0, "errors": export_errors or [],
        },
        "export_plan": [],
        "full_mode_urls": set(),
        "calendar_had_error": {CAL_URL: False},
        "calendar_error_msgs": {},
        "discovery_error": False,
    }


def test_export_error_advances_last_run_but_preserves_last_ok(db, monkeypatch):
    old_ok = "2037-07-14T00:00:00+00:00"
    famdb.meta_set(db, "extcal_last_ok", old_ok)
    db.commit()
    monkeypatch.setattr(cli.gate, "load_config",
                        lambda *a, **k: _cfg())
    monkeypatch.setattr(
        cli, "_cal_ext_sync",
        lambda conn, cfg, now, dry_run: _result(
            export_errors=[{"event_id": 7, "action": "update",
                            "error": "HTTP 500"}]))

    rc = cli.cmd_tick_cal_ext(_args())

    assert rc == 1
    assert famdb.meta_get(db, "extcal_last_run") == TEST_NOW
    assert famdb.meta_get(db, "extcal_last_ok") == old_ok


def test_exception_before_terminal_accounting_and_dry_run_do_not_advance_last_run(
        db, monkeypatch):
    monkeypatch.setattr(cli.gate, "load_config",
                        lambda *a, **k: _cfg())
    monkeypatch.setattr(
        cli, "_cal_ext_sync",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("before accounting")))

    assert cli.cmd_tick_cal_ext(_args()) == 1
    assert famdb.meta_get(db, "extcal_last_run") is None

    famdb.meta_set(db, "extcal_last_run", "2037-07-14T00:00:00+00:00")
    db.commit()
    monkeypatch.setattr(cli, "_cal_ext_sync",
                        lambda *a, **k: _result())

    assert cli.cmd_tick_cal_ext(_args(dry_run=True)) == 0
    assert famdb.meta_get(db, "extcal_last_run") == \
        "2037-07-14T00:00:00+00:00"


def test_failed_marker_write_is_rolled_back_and_verified_from_new_connection(
        db, monkeypatch):
    old_run = "2037-07-14T00:00:00+00:00"
    famdb.meta_set(db, "extcal_last_run", old_run)
    db.commit()
    monkeypatch.setattr(cli.gate, "load_config",
                        lambda *a, **k: _cfg())
    monkeypatch.setattr(cli, "_cal_ext_sync",
                        lambda *a, **k: _result())
    real_meta_set = famdb.meta_set

    def fail_marker(conn, key, value):
        real_meta_set(conn, key, value)
        if key == "extcal_last_run":
            raise sqlite3.OperationalError("meta write unavailable")

    monkeypatch.setattr(cli.famdb, "meta_set", fail_marker)

    assert cli.cmd_tick_cal_ext(_args()) == 1

    reread = famdb.connect()
    try:
        assert famdb.meta_get(reread, "extcal_last_run") == old_run
    finally:
        reread.close()


def test_import_apply_and_export_streaks_are_independent_at_terminal_tick(
        db, monkeypatch):
    monkeypatch.setattr(cli.gate, "load_config",
                        lambda *a, **k: _cfg(
                            extcal_fail_streak_threshold=2))
    monkeypatch.setattr(cli, "_audit_tick_error", lambda *a, **k: None)
    import_error = {
        "branch": "events", "action": "insert", "id": 1,
        "external_uid": "uid", "error": "locked",
    }
    export_error = {
        "event_id": 2, "action": "update", "error": "HTTP 500",
    }
    mode = {"kind": "import"}
    monkeypatch.setattr(
        cli, "_cal_ext_sync",
        lambda *a, **k: _result(
            apply_errors=[import_error] if mode["kind"] == "import" else [],
            export_errors=[export_error] if mode["kind"] == "export" else []))

    assert cli.cmd_tick_cal_ext(_args()) == 0
    assert famdb.meta_get(
        db, "extcal_fail_streak:__import_apply__") == "1"
    assert famdb.meta_get(db, "extcal_fail_streak:__export__") == "0"

    assert cli.cmd_tick_cal_ext(_args()) == 1
    assert famdb.meta_get(
        db, "extcal_fail_streak:__import_apply__") == "2"
    assert famdb.meta_get(db, "extcal_fail_streak:__export__") == "0"

    mode["kind"] = "export"
    assert cli.cmd_tick_cal_ext(_args()) == 0
    assert famdb.meta_get(
        db, "extcal_fail_streak:__import_apply__") == "0"
    assert famdb.meta_get(db, "extcal_fail_streak:__export__") == "1"

    assert cli.cmd_tick_cal_ext(_args()) == 1
    assert famdb.meta_get(
        db, "extcal_fail_streak:__import_apply__") == "0"
    assert famdb.meta_get(db, "extcal_fail_streak:__export__") == "2"
    assert famdb.meta_get(db, "extcal_fail_streak:__apply__") is None



def test_failed_marker_commit_is_rolled_back_and_verified_from_new_connection(
        db, monkeypatch):
    old_run = "2037-07-14T00:00:00+00:00"
    famdb.meta_set(db, "extcal_last_run", old_run)
    db.commit()
    monkeypatch.setattr(cli.gate, "load_config",
                        lambda *a, **k: _cfg())
    monkeypatch.setattr(cli, "_cal_ext_sync",
                        lambda *a, **k: _result())
    real_connect = famdb.connect

    class CommitFailConnection:
        def __init__(self, real):
            self._real = real
            self.commit_calls = 0

        def commit(self):
            self.commit_calls += 1
            if self.commit_calls == 3:
                raise sqlite3.OperationalError("commit unavailable")
            return self._real.commit()

        def __getattr__(self, name):
            return getattr(self._real, name)

    holder = {}

    def connect_for_tick(*args, **kwargs):
        wrapper = CommitFailConnection(real_connect(*args, **kwargs))
        holder["conn"] = wrapper
        return wrapper

    monkeypatch.setattr(cli, "_audit_tick_error", lambda *a, **k: None)
    monkeypatch.setattr(cli.famdb, "connect", connect_for_tick)

    assert cli.cmd_tick_cal_ext(_args()) == 1
    assert holder["conn"].commit_calls == 3

    reread = real_connect()
    try:
        assert famdb.meta_get(reread, "extcal_last_run") == old_run
    finally:
        reread.close()


def test_split_streaks_are_wired_through_real_cal_ext_pipeline(
        db, monkeypatch):
    from fam import gate, extcal
    cfg = dict(gate.CONFIG_DEFAULTS)
    cfg.update({
        "extcal_enabled": True,
        "extcal_username": "amina@example.com",
        "extcal_read_calendars": [],
        "extcal_write_calendar": "https://caldav.icloud.com/write/",
        "extcal_fail_streak_threshold": 2,
    })
    calendar = {
        "url": CAL_URL, "name": "Home", "ctag": "c1",
        "sync_token": "TOK0", "supports_sync_token": True,
        "components": ["VEVENT"],
    }
    monkeypatch.setattr(cli.gate, "load_config",
                        lambda *a, **k: cfg)
    monkeypatch.setattr(extcal, "discover",
                        lambda cfg: [calendar])
    monkeypatch.setattr(
        extcal, "fetch_changes",
        lambda cfg, calendar, sync_token=None, force_full=False:
        ([], "TOK1", {"mode": "sync_collection", "reason": None}))
    clean_apply = {
        "events_inserted": 0, "events_updated": 0, "events_cancelled": 0,
        "plans_inserted": 0, "plans_updated": 0, "plans_dropped": 0,
        "collisions": 0, "errors": [],
    }
    import_error = {
        "branch": "events", "action": "insert", "id": 1,
        "external_uid": "uid", "error": "locked",
    }
    export_error = {
        "event_id": 2, "action": "update", "error": "HTTP 500",
    }
    phase = {"kind": "import"}
    monkeypatch.setattr(
        extcal, "apply_changes",
        lambda conn, changeset, cfg:
        dict(clean_apply, errors=[import_error]
             if phase["kind"] == "import" else []))
    monkeypatch.setattr(
        extcal, "export_routes",
        lambda conn, cfg, request=None, now_utc=None:
        {"exported": 0, "updated": 0, "unchanged": 0, "deleted": 0,
         "retained": 0,
         "errors": [export_error] if phase["kind"] == "export" else []})
    monkeypatch.setattr(cli, "_audit_tick_error", lambda *a, **k: None)

    assert cli.cmd_tick_cal_ext(_args()) == 0
    assert cli.cmd_tick_cal_ext(_args()) == 1
    assert famdb.meta_get(
        db, "extcal_fail_streak:__import_apply__") == "2"
    assert famdb.meta_get(db, "extcal_fail_streak:__export__") == "0"

    phase["kind"] = "export"
    assert cli.cmd_tick_cal_ext(_args()) == 0
    assert cli.cmd_tick_cal_ext(_args()) == 1
    assert famdb.meta_get(
        db, "extcal_fail_streak:__import_apply__") == "0"
    assert famdb.meta_get(db, "extcal_fail_streak:__export__") == "2"


def test_legacy_apply_streak_constant_is_removed():
    assert not hasattr(cli, "_EXTCAL_STREAK_APPLY_KEY")
