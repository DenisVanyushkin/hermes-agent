import os, sqlite3, pytest

@pytest.fixture(autouse=True)
def _isolate_db(tmp_path, monkeypatch):
    # No test may resolve to HOST_DB. The explicit `db` fixture overrides
    # this with its own path; a test that forgets it still hits tmp, not prod.
    monkeypatch.setenv("FAM_DB", str(tmp_path / "assistant.db"))

@pytest.fixture()
def db(tmp_path, monkeypatch):
    dbfile = tmp_path / "assistant.db"
    monkeypatch.setenv("FAM_DB", str(dbfile))
    from fam import db as famdb
    conn = famdb.connect(str(dbfile))
    famdb.init_db(conn)
    yield conn
    conn.close()
