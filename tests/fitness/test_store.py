import json
import os
import stat

import pytest

from fitness import store


@pytest.fixture
def hermes_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    return tmp_path


def test_state_dir_is_under_state_not_beside_the_package(hermes_home):
    path = store.state_dir()
    assert path == hermes_home / "state" / "fitness"
    # каталог с именем пакета рядом с ~/.hermes ломает импорт (PEP 420)
    assert not (hermes_home / "fitness").exists()


def test_state_dir_is_read_lazily_not_at_import(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "one"))
    first = store.state_dir()
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "two"))
    assert store.state_dir() != first


def test_read_returns_default_when_missing(hermes_home):
    assert store.JsonStore("rules.json").read(default={"rules": []}) == {"rules": []}


def test_write_then_read_roundtrip(hermes_home):
    js = store.JsonStore("rules.json")
    js.write({"rules": [{"id": "r1"}]})
    assert js.read(default=None) == {"rules": [{"id": "r1"}]}


def test_write_is_atomic_and_leaves_no_temp_files(hermes_home):
    js = store.JsonStore("session.json")
    js.write({"a": 1})
    names = {p.name for p in store.state_dir().iterdir()}
    assert names == {"session.json"}


def test_write_applies_restrictive_mode(hermes_home):
    js = store.JsonStore("session.json")
    js.write({"token": "secret"})
    mode = stat.S_IMODE(os.stat(store.state_dir() / "session.json").st_mode)
    assert mode == 0o600


def test_corrupt_file_falls_back_to_default(hermes_home):
    store.state_dir().mkdir(parents=True, exist_ok=True)
    (store.state_dir() / "rules.json").write_text("{not json", encoding="utf-8")
    assert store.JsonStore("rules.json").read(default={"rules": []}) == {"rules": []}


def test_is_paused_reflects_flag_file(hermes_home):
    assert store.is_paused() is False
    store.state_dir().mkdir(parents=True, exist_ok=True)
    (store.state_dir() / "PAUSED").touch()
    assert store.is_paused() is True


def test_json_is_written_as_readable_utf8(hermes_home):
    js = store.JsonStore("rules.json")
    js.write({"title": "Йога"})
    raw = (store.state_dir() / "rules.json").read_text(encoding="utf-8")
    assert "Йога" in raw
    assert json.loads(raw) == {"title": "Йога"}
