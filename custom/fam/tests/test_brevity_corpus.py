from datetime import datetime, timezone, timedelta
from fam import brevity, audit


def _insert(db, kind, raw, final, ts=None):
    audit.log(db, "gate.sent", {"kind": kind, "raw": raw, "final": final}, actor="tick")
    if ts is not None:
        db.execute(
            "UPDATE audit_log SET ts_utc=? WHERE id=(SELECT MAX(id) FROM audit_log)",
            (ts,),
        )
        db.commit()


NOW = datetime(2026, 7, 14, 12, tzinfo=timezone.utc)


def test_window_excludes_old_rows(db):
    old_ts = (NOW - timedelta(days=10)).isoformat(timespec="seconds")
    fresh_ts = (NOW - timedelta(days=1)).isoformat(timespec="seconds")
    _insert(db, "reminder", {"label": "old"}, "old sent", ts=old_ts)
    _insert(db, "reminder", {"label": "fresh"}, "fresh sent", ts=fresh_ts)

    result = brevity.collect_corpus(db, {"brevity_window_days": 7}, now=NOW)

    assert len(result["items"]) == 1
    assert result["items"][0]["final"] == "fresh sent"
    assert result["stats"]["total"] == 1


def test_empty_week(db):
    result = brevity.collect_corpus(db, {"brevity_window_days": 7}, now=NOW)

    assert result["items"] == []
    stats = result["stats"]
    assert stats["total"] == 0
    assert stats["per_day"] == 0.0
    assert stats["rewrite_ratio"] == 0.0
    assert stats["avg_len"] == 0.0


def test_rewrite_ratio(db):
    ts = (NOW - timedelta(days=1)).isoformat(timespec="seconds")
    _insert(db, "reminder", {"label": "same text"}, "same text", ts=ts)
    _insert(db, "digest", {"label": "raw text"}, "different final text", ts=ts)

    result = brevity.collect_corpus(db, {"brevity_window_days": 7}, now=NOW)

    assert result["stats"]["total"] == 2
    assert result["stats"]["rewrite_ratio"] == 0.5


def test_skips_empty_final(db):
    ts = (NOW - timedelta(days=1)).isoformat(timespec="seconds")
    _insert(db, "reminder", {"label": "x"}, "", ts=ts)

    result = brevity.collect_corpus(db, {"brevity_window_days": 7}, now=NOW)

    assert result["items"] == []
    assert result["stats"]["total"] == 0
