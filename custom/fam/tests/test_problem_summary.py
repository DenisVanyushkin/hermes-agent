from fam import maint, audit


def _ok_probe(name):
    return {"name": name, "status": "ok", "detail": "", "last_ok_ts": None}


def test_silent_when_clean(db, monkeypatch):
    monkeypatch.setattr(maint.health, "bridge_readiness",
                         lambda conn, cfg, now=None: _ok_probe("bridge"))
    monkeypatch.setattr(maint.health, "starline_staleness",
                         lambda conn, cfg, now=None: _ok_probe("starline"))
    monkeypatch.setattr(maint.health, "degradation_flags",
                         lambda conn, cfg, now=None: _ok_probe("degradation"))
    sent = []
    out = maint.problem_summary({}, notify=lambda t: sent.append(t) or True)
    assert out["skipped_clean"] is True
    assert not out["sent"]
    assert sent == []


def test_reports_tick_error_since_watermark(db, monkeypatch):
    monkeypatch.setattr(maint.health, "bridge_readiness",
                         lambda conn, cfg, now=None: _ok_probe("bridge"))
    monkeypatch.setattr(maint.health, "starline_staleness",
                         lambda conn, cfg, now=None: _ok_probe("starline"))
    monkeypatch.setattr(maint.health, "degradation_flags",
                         lambda conn, cfg, now=None: _ok_probe("degradation"))
    audit.log(db, "tick.error", {"where": "digest", "error": "boom"}, actor="tick")
    db.commit()
    sent = []
    out = maint.problem_summary({}, notify=lambda t: sent.append(t) or True)
    assert out["sent"] is True
    assert len(sent) == 1
    assert "digest" in sent[0]


def test_probe_degraded_shows_up(db, monkeypatch):
    monkeypatch.setattr(maint.health, "bridge_readiness",
                         lambda conn, cfg, now=None:
                         {"name": "bridge", "status": "degraded",
                          "detail": "X", "last_ok_ts": None})
    monkeypatch.setattr(maint.health, "starline_staleness",
                         lambda conn, cfg, now=None: _ok_probe("starline"))
    monkeypatch.setattr(maint.health, "degradation_flags",
                         lambda conn, cfg, now=None: _ok_probe("degradation"))
    sent = []
    out = maint.problem_summary({}, notify=lambda t: sent.append(t) or True)
    assert out["sent"] is True
    assert len(sent) == 1
    assert "bridge" in sent[0]


def test_watermark_advances_on_clean_run(db, monkeypatch):
    monkeypatch.setattr(maint.health, "bridge_readiness",
                         lambda conn, cfg, now=None: _ok_probe("bridge"))
    monkeypatch.setattr(maint.health, "starline_staleness",
                         lambda conn, cfg, now=None: _ok_probe("starline"))
    monkeypatch.setattr(maint.health, "degradation_flags",
                         lambda conn, cfg, now=None: _ok_probe("degradation"))
    from fam import db as famdb
    from datetime import datetime, timezone
    now = datetime(2026, 7, 14, 3, 0, 0, tzinfo=timezone.utc)
    out = maint.problem_summary({}, now=now, notify=lambda t: True)
    assert out["skipped_clean"] is True
    assert famdb.meta_get(db, "maint_summary_last_run") == now.isoformat(timespec="seconds")


def test_run_errors_reported_same_night(db, monkeypatch):
    monkeypatch.setattr(maint.health, "bridge_readiness",
                         lambda conn, cfg, now=None: _ok_probe("bridge"))
    monkeypatch.setattr(maint.health, "starline_staleness",
                         lambda conn, cfg, now=None: _ok_probe("starline"))
    monkeypatch.setattr(maint.health, "degradation_flags",
                         lambda conn, cfg, now=None: _ok_probe("degradation"))
    sent = []
    out = maint.problem_summary(
        {}, run_errors=["backup /x: disk full"],
        notify=lambda t: sent.append(t) or True)
    assert out["sent"] is True
    assert len(sent) == 1
    assert "maintenance: backup /x: disk full" in sent[0]


def test_tick_error_reported_once_not_twice(db, monkeypatch):
    monkeypatch.setattr(maint.health, "bridge_readiness",
                         lambda conn, cfg, now=None: _ok_probe("bridge"))
    monkeypatch.setattr(maint.health, "starline_staleness",
                         lambda conn, cfg, now=None: _ok_probe("starline"))
    monkeypatch.setattr(maint.health, "degradation_flags",
                         lambda conn, cfg, now=None: _ok_probe("degradation"))
    from datetime import datetime, timezone, timedelta
    audit.log(db, "tick.error", {"where": "digest", "error": "boom"}, actor="tick")
    db.commit()
    now1 = datetime.now(timezone.utc) + timedelta(minutes=1)
    sent = []
    out1 = maint.problem_summary({}, now=now1, notify=lambda t: sent.append(t) or True)
    assert out1["sent"] is True
    assert len(sent) == 1

    now2 = now1 + timedelta(days=1)
    out2 = maint.problem_summary({}, now=now2, notify=lambda t: sent.append(t) or True)
    assert out2["skipped_clean"] is True
    assert len(sent) == 1
