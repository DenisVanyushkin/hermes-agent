import os
import sqlite3
from pathlib import Path
from fam import maint

def _fake_encrypt_factory():
    def _enc(plain, dest_age, recipient):
        Path(dest_age).write_bytes(b"AGE[" + Path(plain).read_bytes()[:8] + b"]")
    return _enc

def _make_db(path):
    # NOTE: brief's literal fixture wrote fake header bytes
    # (b"SQLite format 3\x00rest"), but offsite_backup reuses the real
    # _sqlite_backup -> sqlite3 .backup() C API, which requires a
    # structurally valid database file and raises
    # "DatabaseError: file is not a database" on a truncated fake header
    # (verified against sqlite 3.45.1). test_maint.py's own _make_db
    # helper uses a real minimal db for the same reason; mirrored here.
    con = sqlite3.connect(str(path))
    con.execute("CREATE TABLE t(x)"); con.execute("INSERT INTO t VALUES(1)")
    con.commit(); con.close()

def test_offsite_writes_encrypted_both_dbs(tmp_path, monkeypatch):
    off = tmp_path / "nas"; off.mkdir()
    src = tmp_path / "assistant.db"; _make_db(src)
    state = tmp_path / "state.db"; _make_db(state)
    monkeypatch.setattr(maint, "_age_encrypt", _fake_encrypt_factory())
    monkeypatch.setattr(maint.os.path, "ismount", lambda p: True)
    monkeypatch.setattr(maint.famdb, "resolve_db_path", lambda: src)
    cfg = {"offsite_dir": str(off), "offsite_age_recipient": "age1xxx",
           "offsite_keep": 8, "state_db_path": str(state)}
    class N:  # fixed now
        def strftime(self, f): return "20260714"
    r = maint.offsite_backup(cfg, now=N())
    assert not r["errors"]
    assert (off / "assistant-20260714.db.age").exists()
    assert (off / "state-20260714.db.age").exists()

def test_offsite_not_mounted_errors_no_write(tmp_path, monkeypatch):
    off = tmp_path / "nas"; off.mkdir()
    monkeypatch.setattr(maint.os.path, "ismount", lambda p: False)
    cfg = {"offsite_dir": str(off), "offsite_age_recipient": "age1xxx",
           "offsite_keep": 8, "state_db_path": None}
    r = maint.offsite_backup(cfg)
    assert r["written"] == [] and any("mount" in e.lower() for e in r["errors"])

def test_offsite_rotation_keeps_newest(tmp_path, monkeypatch):
    off = tmp_path / "nas"; off.mkdir()
    for d in ("20260601", "20260608", "20260615"):
        (off / f"assistant-{d}.db.age").write_bytes(b"x")
    src = tmp_path / "assistant.db"; _make_db(src)
    monkeypatch.setattr(maint, "_age_encrypt", _fake_encrypt_factory())
    monkeypatch.setattr(maint.os.path, "ismount", lambda p: True)
    monkeypatch.setattr(maint.famdb, "resolve_db_path", lambda: src)
    class N:
        def strftime(self, f): return "20260622"
    cfg = {"offsite_dir": str(off), "offsite_age_recipient": "age1xxx",
           "offsite_keep": 2, "state_db_path": None}
    maint.offsite_backup(cfg, now=N())
    kept = sorted(p.name for p in off.glob("assistant-*.db.age"))
    assert kept == ["assistant-20260615.db.age", "assistant-20260622.db.age"]
