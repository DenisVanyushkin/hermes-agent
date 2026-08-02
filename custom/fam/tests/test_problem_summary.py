import json
from datetime import datetime, timezone

from fam import audit, db as famdb, diag, maint


def _ok_probe(name):
    return {"name": name, "status": "ok", "detail": "", "last_ok_ts": None}


def _all_probes_ok(monkeypatch):
    monkeypatch.setattr(maint.health, "all_probes",
                        lambda conn, cfg, now=None:
                        [_ok_probe("bridge"), _ok_probe("starline")])


def _cfg(tmp_path, **overrides):
    cfg = {"daily_budget": 8,
           "backup_dir": str(tmp_path / "backups"),
           "offsite_enabled": False,
           "diagnostics_dir": str(tmp_path / "diagnostics"),
           "report_jobs_path": str(tmp_path / "jobs.json"),
           "report_job_name": "fam-nightly-report"}
    cfg.update(overrides)
    return cfg


def _confirmed_job(tmp_path, last_run_at):
    (tmp_path / "jobs.json").write_text(json.dumps(
        [{"name": "fam-nightly-report", "last_status": "ok",
          "last_run_at": last_run_at, "last_delivery_error": None}]),
        encoding="utf-8")


NOW = datetime(2026, 8, 2, 22, 30, tzinfo=timezone.utc)


def test_writes_digest_instead_of_sending(db, tmp_path, monkeypatch):
    _all_probes_ok(monkeypatch)
    monkeypatch.setattr(maint.diag, "collect_timers",
                        lambda runner=None: {"failed": [], "ok": ["fam-reminders.timer"]})
    famdb.meta_set(db, "maint_digest_last_written", "2026-08-01T22:30:00+00:00")
    db.commit()
    _confirmed_job(tmp_path, "2026-08-02T03:00:00+00:00")
    audit.log(db, "tick.error", {"where": "meds_row", "intake_id": 10,
                                 "exc_type": "KeyError",
                                 "error": "No item with that key"}, actor="tick")
    db.commit()

    sent = []
    out = maint.problem_summary(_cfg(tmp_path), now=NOW,
                                notify=lambda t: sent.append(t) or True)

    assert sent == [], "a confirmed report means fam stays silent"
    assert out["delivery_ok"] is True
    digest = json.loads((tmp_path / "diagnostics" / "fam-digest-latest.json")
                        .read_text(encoding="utf-8"))
    assert digest["sections"]["errors"]["findings"][0]["count"] == 1
    # To the CONFIRMED digest's timestamp, not NOW: tonight's digest has
    # not been delivered yet and its window must stay open.
    assert famdb.meta_get(db, "maint_summary_last_run") == "2026-08-01T22:30:00+00:00"


def test_raw_fallback_when_report_not_confirmed(db, tmp_path, monkeypatch):
    _all_probes_ok(monkeypatch)
    monkeypatch.setattr(maint.diag, "collect_timers",
                        lambda runner=None: {"failed": [], "ok": []})
    famdb.meta_set(db, "maint_digest_last_written", "2026-08-01T22:30:00+00:00")
    famdb.meta_set(db, "maint_summary_last_run", "2026-08-01T22:30:00+00:00")
    db.commit()
    _confirmed_job(tmp_path, "2026-07-01T03:00:00+00:00")   # stale run
    audit.log(db, "tick.error", {"where": "digest", "exc_type": "ValueError",
                                 "error": "boom"}, actor="tick")
    db.commit()

    sent = []
    out = maint.problem_summary(_cfg(tmp_path), now=NOW,
                                notify=lambda t: sent.append(t) or True)

    assert out["delivery_ok"] is False
    assert out["fallback_sent"] is True
    assert len(sent) == 1 and "digest" in sent[0] and "×1" in sent[0]
    assert famdb.meta_get(db, "maint_summary_last_run") == NOW.isoformat(timespec="seconds")


def test_watermark_held_when_fallback_also_fails(db, tmp_path, monkeypatch):
    _all_probes_ok(monkeypatch)
    monkeypatch.setattr(maint.diag, "collect_timers",
                        lambda runner=None: {"failed": [], "ok": []})
    famdb.meta_set(db, "maint_digest_last_written", "2026-08-01T22:30:00+00:00")
    famdb.meta_set(db, "maint_summary_last_run", "2026-08-01T22:30:00+00:00")
    db.commit()
    _confirmed_job(tmp_path, "2026-07-01T03:00:00+00:00")
    audit.log(db, "tick.error", {"where": "digest", "error": "boom"}, actor="tick")
    db.commit()

    out = maint.problem_summary(_cfg(tmp_path), now=NOW, notify=lambda t: False)

    assert out["fallback_sent"] is False
    assert famdb.meta_get(db, "maint_summary_last_run") == "2026-08-01T22:30:00+00:00", \
        "an undelivered problem window must survive into the next sweep"


def test_clean_window_is_silent_and_holds_the_watermark(db, tmp_path, monkeypatch):
    # Daily cadence is the LLM report's job. The fallback stays an
    # emergency channel: silence on a clean night keeps rollout behaviour
    # identical to today's. And with nothing confirmed delivered, the
    # watermark must not move -- see the next test for why.
    _all_probes_ok(monkeypatch)
    monkeypatch.setattr(maint.diag, "collect_timers",
                        lambda runner=None: {"failed": [], "ok": []})
    sent = []
    out = maint.problem_summary(_cfg(tmp_path), now=NOW,
                                notify=lambda t: sent.append(t) or True)
    assert sent == []
    assert out["skipped_clean"] is True
    assert famdb.meta_get(db, "maint_summary_last_run") is None


def test_clean_night_does_not_strand_an_undelivered_digest(db, tmp_path, monkeypatch):
    # The failure this contract exists to prevent. Night A publishes a
    # digest holding a real error. Night B finds that digest undelivered
    # but has a quiet window of its own. If B advanced the watermark,
    # night A's error would fall behind every future window and be lost
    # with no fallback ever attempted for it.
    _all_probes_ok(monkeypatch)
    monkeypatch.setattr(maint.diag, "collect_timers",
                        lambda runner=None: {"failed": [], "ok": []})
    famdb.meta_set(db, "maint_summary_last_run", "2026-07-31T22:30:00+00:00")
    famdb.meta_set(db, "maint_digest_last_written", "2026-08-01T22:30:00+00:00")
    db.commit()
    _confirmed_job(tmp_path, "2026-07-01T03:00:00+00:00")   # never delivered digest A

    sent = []
    out = maint.problem_summary(_cfg(tmp_path), now=NOW,
                                notify=lambda t: sent.append(t) or True)

    assert out["delivery_ok"] is False
    assert sent == [], "a clean window has nothing of its own to report"
    assert famdb.meta_get(db, "maint_summary_last_run") == "2026-07-31T22:30:00+00:00"


def test_run_errors_are_folded_into_the_digest(db, tmp_path, monkeypatch):
    _all_probes_ok(monkeypatch)
    monkeypatch.setattr(maint.diag, "collect_timers",
                        lambda runner=None: {"failed": [], "ok": []})
    out = maint.problem_summary(_cfg(tmp_path), now=NOW,
                                notify=lambda t: True,
                                run_errors=["backup /x: disk full"])
    digest = json.loads((tmp_path / "diagnostics" / "fam-digest-latest.json")
                        .read_text(encoding="utf-8"))
    assert digest["sections"]["maintenance_errors"] == ["backup /x: disk full"]
    assert out["skipped_clean"] is False
