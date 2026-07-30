from datetime import datetime, timedelta, timezone

import pytest

from fam import health


def _now():
    return datetime(2026, 7, 14, 12, tzinfo=timezone.utc)


def test_starline_staleness_ok(db):
    now = _now()
    ts = (now - timedelta(hours=1)).isoformat(timespec="seconds")
    db.execute("INSERT INTO car_metrics(ts_utc) VALUES(?)", (ts,))
    db.commit()
    result = health.starline_staleness(db, {"car_staleness_hours": 24}, now=now)
    assert result["name"] == "starline_staleness"
    assert result["status"] == "ok"


def test_starline_staleness_degraded(db):
    now = _now()
    ts = (now - timedelta(hours=30)).isoformat(timespec="seconds")
    db.execute("INSERT INTO car_metrics(ts_utc) VALUES(?)", (ts,))
    db.commit()
    result = health.starline_staleness(db, {"car_staleness_hours": 24}, now=now)
    assert result["name"] == "starline_staleness"
    assert result["status"] == "degraded"


def test_all_probes_isolates_broken_probe(db, monkeypatch):
    def boom(conn, cfg, now=None):
        raise RuntimeError("x")
    boom.__name__ = "starline_staleness"

    monkeypatch.setattr(health, "starline_staleness", boom)
    results = health.all_probes(db, {"car_staleness_hours": 24}, now=_now())
    matches = [r for r in results if r["name"] == "starline_staleness"]
    assert len(matches) == 1
    assert matches[0]["status"] == "down"
    assert "x" in matches[0]["detail"]


# ---- extcal_staleness (Task 8) ----------------------------------------

def test_extcal_staleness_disabled_is_silent(db):
    # No extcal_last_ok at all either -- disabled must win over "never
    # synced", it is not a degradation.
    result = health.extcal_staleness(
        db, {"extcal_enabled": False, "extcal_stale_hours": 6}, now_utc=_now())
    assert result["name"] == "extcal_staleness"
    assert result["status"] == "ok"


def test_extcal_staleness_disabled_silent_even_with_stale_last_ok(db):
    from fam import db as famdb
    old = (_now() - timedelta(hours=100)).isoformat()
    famdb.meta_set(db, "extcal_last_ok", old)
    db.commit()
    result = health.extcal_staleness(
        db, {"extcal_enabled": False, "extcal_stale_hours": 6}, now_utc=_now())
    assert result["status"] == "ok"


def test_extcal_staleness_never_synced_is_degraded(db):
    # enabled, meta.extcal_last_ok never written -- distinct from disabled.
    result = health.extcal_staleness(
        db, {"extcal_enabled": True, "extcal_stale_hours": 6}, now_utc=_now())
    assert result["status"] == "degraded"
    assert "ни разу" in result["detail"]


def test_extcal_staleness_stale_is_degraded_with_age(db):
    from fam import db as famdb
    now = _now()
    old = (now - timedelta(hours=10)).isoformat()
    famdb.meta_set(db, "extcal_last_ok", old)
    db.commit()
    result = health.extcal_staleness(
        db, {"extcal_enabled": True, "extcal_stale_hours": 6}, now_utc=now)
    assert result["status"] == "degraded"
    assert "10.0" in result["detail"]
    assert result["last_ok_ts"] == old


def test_extcal_staleness_fresh_is_silent(db):
    from fam import db as famdb
    now = _now()
    fresh = (now - timedelta(hours=1)).isoformat()
    famdb.meta_set(db, "extcal_last_ok", fresh)
    db.commit()
    result = health.extcal_staleness(
        db, {"extcal_enabled": True, "extcal_stale_hours": 6}, now_utc=now)
    assert result["status"] == "ok"


def test_extcal_staleness_threshold_comes_from_cfg_not_hardcoded(db):
    from fam import db as famdb
    now = _now()
    ts = (now - timedelta(hours=2)).isoformat()
    famdb.meta_set(db, "extcal_last_ok", ts)
    db.commit()
    # Same age (2h), two different configured thresholds -> two different
    # verdicts: proves the 6h default from the spec isn't baked in.
    tight = health.extcal_staleness(
        db, {"extcal_enabled": True, "extcal_stale_hours": 1}, now_utc=now)
    loose = health.extcal_staleness(
        db, {"extcal_enabled": True, "extcal_stale_hours": 3}, now_utc=now)
    assert tight["status"] == "degraded"
    assert loose["status"] == "ok"


def test_extcal_staleness_never_writes_db(db):
    from fam import db as famdb
    now = _now()
    before = famdb.meta_get(db, "extcal_last_ok")
    assert before is None
    health.extcal_staleness(
        db, {"extcal_enabled": True, "extcal_stale_hours": 6}, now_utc=now)
    after_count = db.execute("SELECT COUNT(*) AS c FROM meta").fetchone()["c"]
    health.extcal_staleness(
        db, {"extcal_enabled": True, "extcal_stale_hours": 6}, now_utc=now)
    assert db.execute("SELECT COUNT(*) AS c FROM meta").fetchone()["c"] == after_count
    assert famdb.meta_get(db, "extcal_last_ok") is None


def test_all_probes_isolates_broken_extcal_probe(db, monkeypatch):
    def boom(conn, cfg, now_utc=None):
        raise RuntimeError("icloud auth failed")
    boom.__name__ = "extcal_staleness"

    monkeypatch.setattr(health, "extcal_staleness", boom)
    results = health.all_probes(
        db, {"car_staleness_hours": 24, "extcal_enabled": True,
             "extcal_stale_hours": 6},
        now=_now())
    matches = [r for r in results if r["name"] == "extcal_staleness"]
    assert len(matches) == 1
    assert matches[0]["status"] == "down"
    assert "icloud auth failed" in matches[0]["detail"]
    # the other probes must still come back clean/whatever they are --
    # one broken probe never sinks the batch.
    names = {r["name"] for r in results}
    assert {"bridge_readiness", "starline_staleness",
            "degradation_flags"} <= names


def test_all_probes_includes_extcal_staleness(db):
    results = health.all_probes(
        db, {"extcal_enabled": True, "extcal_stale_hours": 6}, now=_now())
    names = {r["name"] for r in results}
    assert "extcal_staleness" in names
