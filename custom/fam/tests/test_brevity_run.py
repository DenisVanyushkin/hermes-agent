from fam import brevity


def test_run_audit_empty_week(db, monkeypatch):
    sent = []
    n = lambda t: sent.append(t) or True

    monkeypatch.setattr(
        brevity, "collect_corpus",
        lambda c, cfg, now=None: {"items": [], "stats": {"total": 0}})

    result = brevity.run_audit({}, notify=n)

    assert result["reason"] == "empty"
    assert result["sent"] is True
    assert len(sent) == 1
    assert "нет" in sent[0]


def test_run_audit_llm_skip(db, monkeypatch):
    sent = []
    n = lambda t: sent.append(t) or True

    monkeypatch.setattr(
        brevity, "collect_corpus",
        lambda c, cfg, now=None: {
            "items": [{"kind": "reminder"}],
            "stats": {"total": 1, "per_day": 0.1, "rewrite_ratio": 0.0,
                      "avg_len": 10.0}})
    monkeypatch.setattr(
        brevity, "review", lambda corpus, cfg, caller=None: None)

    result = brevity.run_audit({}, notify=n)

    assert result["reason"] == "llm_skip"
    assert result["sent"] is True
    assert len(sent) == 1
    assert "пропущен" in sent[0]


def test_run_audit_full_report(db, monkeypatch):
    sent = []
    n = lambda t: sent.append(t) or True

    monkeypatch.setattr(
        brevity, "collect_corpus",
        lambda c, cfg, now=None: {
            "items": [{"kind": "reminder"}],
            "stats": {"total": 5, "per_day": 0.7, "rewrite_ratio": 0.4,
                      "avg_len": 42.0}})
    monkeypatch.setattr(
        brevity, "review",
        lambda corpus, cfg, caller=None: {
            "assessment": "местами длинно",
            "rewrite_gap": "шлюз правит 40%",
            "examples": [{"before": "длинно длинно", "after": "коротко"}],
            "edits": ["убери приветствия"],
        })

    result = brevity.run_audit({}, notify=n)

    assert result["reason"] == "ok"
    assert result["sent"] is True
    assert len(sent) == 1
    msg = sent[0]
    assert "5" in msg
    assert "коротко" in msg
    assert "убери приветствия" in msg
