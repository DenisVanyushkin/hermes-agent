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

import sqlite3
from pathlib import Path
from datetime import datetime, timezone

def _make_db(path):
    con = sqlite3.connect(str(path))
    con.execute("CREATE TABLE t(x)"); con.execute("INSERT INTO t VALUES(1)")
    con.commit(); con.close()

def test_backup_creates_dated_copy(tmp_path):
    src = tmp_path / "assistant.db"; _make_db(src)
    dest_dir = tmp_path / "backups"
    now = datetime(2026, 7, 13, 22, 30, tzinfo=timezone.utc)
    out = maint.backup_db(src, dest_dir, keep=7, now=now)
    assert out == dest_dir / "assistant-20260713.db"
    assert out.exists()
    con = sqlite3.connect(str(out))
    assert con.execute("SELECT x FROM t").fetchone()[0] == 1
    con.close()
    assert oct(dest_dir.stat().st_mode)[-3:] == "700"

def test_backup_rotation_keeps_newest(tmp_path):
    src = tmp_path / "assistant.db"; _make_db(src)
    dest_dir = tmp_path / "backups"; dest_dir.mkdir()
    for day in (10, 11, 12):  # pre-existing older copies
        (dest_dir / f"assistant-202607{day:02d}.db").write_bytes(b"x")
    now = datetime(2026, 7, 13, 22, 30, tzinfo=timezone.utc)
    maint.backup_db(src, dest_dir, keep=2, now=now)
    names = sorted(p.name for p in dest_dir.glob("assistant-*.db"))
    assert names == ["assistant-20260712.db", "assistant-20260713.db"]
