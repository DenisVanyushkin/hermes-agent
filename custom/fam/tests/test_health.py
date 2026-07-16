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
