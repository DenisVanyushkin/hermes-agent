import subprocess
from fam import gate

def test_notify_denis_sends_to_home_channel(monkeypatch):
    calls = {}
    def fake_run(cmd, **kw):
        calls["cmd"] = cmd; calls["input"] = kw.get("input")
        class R: returncode = 0
        return R()
    monkeypatch.setenv("TELEGRAM_HOME_CHANNEL", "79564752")
    monkeypatch.setattr(subprocess, "run", fake_run)
    assert gate.notify_denis("StarLine молчит >24ч") is True
    assert calls["cmd"][-2:] == ["-t", "telegram:79564752"]
    assert calls["input"] == "StarLine молчит >24ч"

def test_notify_denis_false_without_channel(monkeypatch):
    monkeypatch.delenv("TELEGRAM_HOME_CHANNEL", raising=False)
    assert gate.notify_denis("x") is False
