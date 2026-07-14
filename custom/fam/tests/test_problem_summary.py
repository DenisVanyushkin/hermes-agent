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
