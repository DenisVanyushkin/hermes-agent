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

def test_backup_file_mode_600(tmp_path):
    # the backup carries the same PII as the live DB -- must not be group/world readable
    src = tmp_path / "assistant.db"
    con = sqlite3.connect(str(src)); con.execute("CREATE TABLE t(x)"); con.close()
    dest = maint.backup_db(src, tmp_path / "backups", keep=7)
    assert (dest.stat().st_mode & 0o777) == 0o600

def test_backup_rotation_keeps_newest(tmp_path):
    src = tmp_path / "assistant.db"; _make_db(src)
    dest_dir = tmp_path / "backups"; dest_dir.mkdir()
    for day in (10, 11, 12):  # pre-existing older copies
        (dest_dir / f"assistant-202607{day:02d}.db").write_bytes(b"x")
    now = datetime(2026, 7, 13, 22, 30, tzinfo=timezone.utc)
    maint.backup_db(src, dest_dir, keep=2, now=now)
    names = sorted(p.name for p in dest_dir.glob("assistant-*.db"))
    assert names == ["assistant-20260712.db", "assistant-20260713.db"]


def test_verify_backup_passes_on_real_db(db, tmp_path):
    # `db` is an initialised assistant.db (schema_version=7); back it up and verify
    src = maint.backup_db(Path(db.execute("PRAGMA database_list").fetchone()[2]),
                          tmp_path / "b", keep=7,
                          now=datetime(2026, 7, 13, tzinfo=timezone.utc))
    ok, detail = maint.verify_backup(src)
    assert ok is True
    assert detail["integrity"] == "ok"
    assert detail["schema_version"] == "7"

def test_verify_backup_fails_on_corrupt_file(tmp_path):
    bad = tmp_path / "corrupt.db"
    bad.write_bytes(b"this is not a sqlite database")
    ok, _ = maint.verify_backup(bad)
    assert ok is False


def test_run_maintenance_prunes_and_backups(db, tmp_path, monkeypatch):
    # `db` fixture already set FAM_DB to its tmp assistant.db (schema 5)
    now = datetime(2026, 7, 13, 22, 30, tzinfo=timezone.utc)
    db.execute("INSERT INTO audit_log(ts_utc,kind,actor,payload) VALUES(?,?,?,'{}')",
               ((now - timedelta(days=200)).isoformat(timespec="seconds"), "cal.add", "agent"))
    db.commit()
    cfg = {"audit_retention_days": 90, "backup_keep": 7,
           "backup_dir": str(tmp_path / "bk"),
           "state_db_path": str(tmp_path / "missing-state.db")}  # absent → skipped
    res = maint.run_maintenance(cfg, now=now)
    assert res["pruned"] == 1
    assert res["errors"] == []
    assert len(res["backups"]) == 1  # only assistant.db (state absent)
    assert Path(res["backups"][0]).exists()

def test_run_maintenance_dry_run_writes_nothing(db, tmp_path):
    now = datetime(2026, 7, 13, 22, 30, tzinfo=timezone.utc)
    db.execute("INSERT INTO audit_log(ts_utc,kind,actor,payload) VALUES(?,?,?,'{}')",
               ((now - timedelta(days=200)).isoformat(timespec="seconds"), "cal.add", "agent"))
    db.commit()
    cfg = {"audit_retention_days": 90, "backup_keep": 7,
           "backup_dir": str(tmp_path / "bk"), "state_db_path": str(tmp_path / "no.db")}
    res = maint.run_maintenance(cfg, dry_run=True, now=now)
    assert res["pruned"] == 1  # counted, not deleted
    assert db.execute("SELECT COUNT(*) FROM audit_log WHERE kind='cal.add'").fetchone()[0] == 1
    assert not (tmp_path / "bk").exists()

def test_config_defaults_present():
    from fam import gate
    cfg = gate.CONFIG_DEFAULTS
    assert cfg["audit_retention_days"] == 90
    assert cfg["backup_keep"] == 7
    assert cfg["backup_dir"].endswith("/backups")
    assert cfg["state_db_path"].endswith("/state.db")

def test_run_maintenance_records_errors(db, tmp_path):
    now = datetime(2026, 7, 13, 22, 30, tzinfo=timezone.utc)
    blocker = tmp_path / "blocker"; blocker.write_text("x")   # a file, not a dir
    cfg = {"audit_retention_days": 90, "backup_keep": 7,
           "backup_dir": str(blocker / "sub"),                # mkdir under a file → error
           "state_db_path": str(tmp_path / "no.db")}
    res = maint.run_maintenance(cfg, now=now)
    assert res["errors"]                                      # backup failure surfaced
    from fam import db as famdb
    c = famdb.connect()
    n = c.execute(
        "SELECT COUNT(*) FROM audit_log WHERE payload LIKE '%\"op\": \"errors\"%'"
    ).fetchone()[0]
    c.close()
    assert n == 1                                             # failure recorded in the journal


def test_run_maintenance_dry_run_path_matches_real_naming(db, tmp_path):
    # the dry-run preview path must never drift from the real backup naming
    now = datetime(2026, 7, 13, 22, 30, tzinfo=timezone.utc)
    dest_dir = tmp_path / "bk"
    cfg = {"audit_retention_days": 90, "backup_keep": 7,
           "backup_dir": str(dest_dir), "state_db_path": str(tmp_path / "no.db")}
    res = maint.run_maintenance(cfg, dry_run=True, now=now)
    src = Path(db.execute("PRAGMA database_list").fetchone()[2])
    expected = str(maint._backup_dest(src, dest_dir, now))
    assert res["backups"] == [expected]
    real = maint.backup_db(src, dest_dir, keep=7, now=now)
    assert res["backups"] == [str(real)]

def test_run_maintenance_captures_target_resolution_error(db, tmp_path, monkeypatch):
    def _boom():
        raise RuntimeError("boom")
    monkeypatch.setattr(maint.famdb, "resolve_db_path", _boom)
    now = datetime(2026, 7, 13, 22, 30, tzinfo=timezone.utc)
    cfg = {"audit_retention_days": 90, "backup_keep": 7,
           "backup_dir": str(tmp_path / "bk"), "state_db_path": str(tmp_path / "no.db")}
    res = maint.run_maintenance(cfg, now=now)  # must not raise
    assert any("boom" in e for e in res["errors"])
    assert "pruned" in res and "backups" in res
