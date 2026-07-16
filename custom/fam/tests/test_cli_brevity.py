from fam import cli, brevity
def test_cmd_tick_brevity_invokes_run_audit(monkeypatch):
    called = {}
    monkeypatch.setattr(brevity, "run_audit",
        lambda cfg, now=None: called.setdefault("ok", True) or {"sent": True, "reason": "ok"})
    monkeypatch.setattr(cli.gate, "load_config", lambda: {"brevity_window_days": 7})
    class Args: now = None; json = True
    rc = cli.cmd_tick_brevity(Args())
    assert rc == 0 and called.get("ok")
