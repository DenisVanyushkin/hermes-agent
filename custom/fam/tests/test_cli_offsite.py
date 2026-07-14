import types, pytest
from fam import cli

def test_tick_offsite_disabled_is_noop(monkeypatch, capsys):
    monkeypatch.setattr(cli.gate, "load_config", lambda *a, **k: {"offsite_enabled": False})
    called = {"n": 0}
    monkeypatch.setattr(cli.maint, "offsite_backup", lambda *a, **k: called.__setitem__("n", called["n"]+1) or {"written":[],"errors":[]})
    rc = cli.cmd_tick_offsite(types.SimpleNamespace(now=None))
    assert rc == 0 and called["n"] == 0

def test_tick_offsite_errors_exit_1(monkeypatch):
    monkeypatch.setattr(cli.gate, "load_config", lambda *a, **k: {"offsite_enabled": True})
    monkeypatch.setattr(cli.maint, "offsite_backup",
                        lambda *a, **k: {"written": [], "errors": ["offsite ...: boom"]})
    rc = cli.cmd_tick_offsite(types.SimpleNamespace(now=None))
    assert rc == 1
