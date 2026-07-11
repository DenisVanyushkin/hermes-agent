import os, sqlite3, pytest

@pytest.fixture()
def db(tmp_path, monkeypatch):
    dbfile = tmp_path / "assistant.db"
    monkeypatch.setenv("FAM_DB", str(dbfile))
    from fam import db as famdb
    conn = famdb.connect(str(dbfile))
    famdb.init_db(conn)
    yield conn
    conn.close()
