from datetime import datetime, timezone, timedelta
from fam import maint, audit

def _seed_audit(db, kind, ts):
    db.execute(
        "INSERT INTO audit_log(ts_utc, kind, actor, payload) VALUES(?,?,?,'{}')",
        (ts, kind, "agent"))
    db.commit()

def test_prune_deletes_only_older_than_days(db):
    now = datetime(2026, 7, 13, 22, 30, tzinfo=timezone.utc)
    old = (now - timedelta(days=91)).isoformat(timespec="seconds")
    fresh = (now - timedelta(days=10)).isoformat(timespec="seconds")
    _seed_audit(db, "cal.add", old)
    _seed_audit(db, "cal.add", fresh)
    deleted = maint.prune_audit_log(db, days=90, now=now)
    assert deleted == 1
    rows = db.execute("SELECT ts_utc FROM audit_log WHERE kind='cal.add'").fetchall()
    assert [r["ts_utc"] for r in rows] == [fresh]

def test_prune_records_itself_and_is_not_self_pruned(db):
    now = datetime(2026, 7, 13, 22, 30, tzinfo=timezone.utc)
    _seed_audit(db, "cal.add", (now - timedelta(days=200)).isoformat(timespec="seconds"))
    maint.prune_audit_log(db, days=90, now=now)
    rec = db.execute(
        "SELECT payload FROM audit_log WHERE kind='tick.maintenance'").fetchall()
    assert len(rec) == 1  # the maintenance record survives (ts=now)
