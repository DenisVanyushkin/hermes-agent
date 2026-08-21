import os, sqlite3, pytest

@pytest.fixture(autouse=True)
def _isolate_db(tmp_path, monkeypatch):
    # No test may resolve to HOST_DB. The explicit `db` fixture overrides
    # this with its own path; a test that forgets it still hits tmp, not prod.
    monkeypatch.setenv("FAM_DB", str(tmp_path / "assistant.db"))

@pytest.fixture(autouse=True)
def _isolate_prod_stores(tmp_path, monkeypatch):
    # Same class of leak as _isolate_db, for the non-DB prod files: on
    # 2026-07-16 the set-device tests wrote device_id=D9 into the LIVE
    # starline-token.json when a refactor briefly broke their TOKEN_PATH
    # monkeypatch. Repoint every default path at tmp so a test that
    # forgets an explicit path can only ever touch tmp.
    from fam import car, diag, gate
    monkeypatch.setattr(car, "TOKEN_PATH", str(tmp_path / "starline-token.json"))
    monkeypatch.setattr(car, "SANDBOX_TOKEN_PATH", str(tmp_path / "sandbox-starline-token.json"))
    monkeypatch.setattr(gate, "CONFIG_PATH", tmp_path / "fam-config.json")
    monkeypatch.setattr(gate, "SANDBOX_CONFIG_PATH", tmp_path / "sandbox-fam-config.json")
    # Same class of leak as the starline token above: problem_summary({})
    # would otherwise write into the LIVE ~/.hermes/diagnostics.
    monkeypatch.setattr(diag, "DEFAULT_DIAGNOSTICS_DIR", str(tmp_path / "diagnostics"))

@pytest.fixture(autouse=True)
def _isolate_icloud_credentials(monkeypatch):
    # Same class of leak as _isolate_db/_isolate_prod_stores, for the ONE
    # secret extcal reads straight out of the environment
    # (ICLOUD_APP_PASSWORD -- it is env-only by design, never in
    # fam-config.json). Since 2026-08-21 the cal-ext tick re-fetches an
    # unreadable resource body itself (`extcal.fetch_resource`), so a
    # test that stubs `discover`/`fetch_changes` but not `fetch_resource`
    # would make a REAL request to iCloud if a developer happened to run
    # pytest from a shell with the password exported. Without credentials
    # `_auth_header` returns None and the fetch degrades to None before
    # any socket is opened. A test that wants credentials still sets them
    # with its own `monkeypatch.setenv`, which runs after this.
    monkeypatch.delenv("ICLOUD_APP_PASSWORD", raising=False)


@pytest.fixture()
def db(tmp_path, monkeypatch):
    dbfile = tmp_path / "assistant.db"
    monkeypatch.setenv("FAM_DB", str(dbfile))
    from fam import db as famdb
    conn = famdb.connect(str(dbfile))
    famdb.init_db(conn)
    yield conn
    conn.close()
