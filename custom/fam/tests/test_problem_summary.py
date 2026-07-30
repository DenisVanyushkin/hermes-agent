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


def test_watermark_not_advanced_when_notify_fails(db, monkeypatch):
    # Go-live review finding 6: a failed delivery must not burn the
    # watermark -- otherwise the day's problems vanish silently.
    monkeypatch.setattr(maint.health, "bridge_readiness",
                         lambda conn, cfg, now=None: _ok_probe("bridge"))
    monkeypatch.setattr(maint.health, "starline_staleness",
                         lambda conn, cfg, now=None: _ok_probe("starline"))
    monkeypatch.setattr(maint.health, "degradation_flags",
                         lambda conn, cfg, now=None: _ok_probe("degradation"))
    from fam import db as famdb
    famdb.meta_set(db, "maint_summary_last_run", "2026-07-13T03:00:00+00:00")
    db.commit()
    audit.log(db, "tick.error", {"where": "digest", "error": "boom"}, actor="tick")
    db.commit()
    before = famdb.meta_get(db, "maint_summary_last_run")
    out = maint.problem_summary({}, notify=lambda body: False)
    assert out["sent"] is False
    assert out["problems"]
    after = famdb.meta_get(db, "maint_summary_last_run")
    assert after == before        # next night re-sweeps the same window


def test_watermark_advances_when_notify_succeeds(db, monkeypatch):
    monkeypatch.setattr(maint.health, "bridge_readiness",
                         lambda conn, cfg, now=None: _ok_probe("bridge"))
    monkeypatch.setattr(maint.health, "starline_staleness",
                         lambda conn, cfg, now=None: _ok_probe("starline"))
    monkeypatch.setattr(maint.health, "degradation_flags",
                         lambda conn, cfg, now=None: _ok_probe("degradation"))
    from fam import db as famdb
    from datetime import datetime, timezone
    audit.log(db, "tick.error", {"where": "digest", "error": "boom"}, actor="tick")
    db.commit()
    now = datetime(2026, 7, 14, 3, 0, 0, tzinfo=timezone.utc)
    out = maint.problem_summary({}, now=now, notify=lambda body: True)
    assert out["sent"] is True
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


# ---- cal-ext collision counter (Task 8, fix-round 1) -------------------

def test_collision_counter_shows_up_no_content_leaked(db, monkeypatch):
    monkeypatch.setattr(maint.health, "bridge_readiness",
                         lambda conn, cfg, now=None: _ok_probe("bridge"))
    monkeypatch.setattr(maint.health, "starline_staleness",
                         lambda conn, cfg, now=None: _ok_probe("starline"))
    monkeypatch.setattr(maint.health, "degradation_flags",
                         lambda conn, cfg, now=None: _ok_probe("degradation"))
    audit.log(db, "cal.ext.sync", {
        "collisions": 3, "events_inserted": 0, "errors": [],
        "calendars": [{"url": "https://p01-caldav.icloud.com/secret/personal/",
                        "name": "Личный", "mode": "read", "reason": None}],
    })
    db.commit()
    sent = []
    out = maint.problem_summary({}, notify=lambda t: sent.append(t) or True)
    assert out["sent"] is True
    assert len(sent) == 1
    assert "3 совпадающих записей" in sent[0]
    assert "разобрать вручную" in sent[0]
    # counter only -- no calendar name/URL, no titles, ever leak into
    # the summary (spec: audit/summary carry UIDs and counts only).
    assert "icloud.com" not in sent[0]
    assert "Личный" not in sent[0]


def test_no_collision_line_when_zero(db, monkeypatch):
    monkeypatch.setattr(maint.health, "bridge_readiness",
                         lambda conn, cfg, now=None: _ok_probe("bridge"))
    monkeypatch.setattr(maint.health, "starline_staleness",
                         lambda conn, cfg, now=None: _ok_probe("starline"))
    monkeypatch.setattr(maint.health, "degradation_flags",
                         lambda conn, cfg, now=None: _ok_probe("degradation"))
    # a healthy, zero-collision cal.ext.sync row (e.g. written because a
    # calendar's mode changed) must not add a collision line on its own.
    audit.log(db, "cal.ext.sync", {"collisions": 0, "events_inserted": 1})
    db.commit()
    audit.log(db, "tick.error", {"where": "digest", "error": "boom"}, actor="tick")
    db.commit()
    sent = []
    out = maint.problem_summary({}, notify=lambda t: sent.append(t) or True)
    assert out["sent"] is True
    assert "совпадающих" not in sent[0]


def test_collision_counter_sums_across_window(db, monkeypatch):
    monkeypatch.setattr(maint.health, "bridge_readiness",
                         lambda conn, cfg, now=None: _ok_probe("bridge"))
    monkeypatch.setattr(maint.health, "starline_staleness",
                         lambda conn, cfg, now=None: _ok_probe("starline"))
    monkeypatch.setattr(maint.health, "degradation_flags",
                         lambda conn, cfg, now=None: _ok_probe("degradation"))
    audit.log(db, "cal.ext.sync", {"collisions": 2})
    db.commit()
    audit.log(db, "cal.ext.sync", {"collisions": 1})
    db.commit()
    sent = []
    out = maint.problem_summary({}, notify=lambda t: sent.append(t) or True)
    assert out["sent"] is True
    assert "3 совпадающих записей" in sent[0]


def test_collision_counter_uses_same_watermark_as_tick_error(db, monkeypatch):
    monkeypatch.setattr(maint.health, "bridge_readiness",
                         lambda conn, cfg, now=None: _ok_probe("bridge"))
    monkeypatch.setattr(maint.health, "starline_staleness",
                         lambda conn, cfg, now=None: _ok_probe("starline"))
    monkeypatch.setattr(maint.health, "degradation_flags",
                         lambda conn, cfg, now=None: _ok_probe("degradation"))
    from fam import db as famdb
    famdb.meta_set(db, "maint_summary_last_run", "2026-07-13T03:00:00+00:00")
    db.commit()
    # pre-watermark cal.ext.sync row: must NOT be counted, same as a
    # pre-watermark tick.error row wouldn't be.
    db.execute(
        "INSERT INTO audit_log(ts_utc, kind, payload, actor) "
        "VALUES (?, 'cal.ext.sync', ?, 'tick')",
        ("2026-07-12T00:00:00", '{"collisions": 5}'))
    db.commit()
    sent = []
    out = maint.problem_summary({}, notify=lambda t: sent.append(t) or True)
    assert out["skipped_clean"] is True
    assert sent == []


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
