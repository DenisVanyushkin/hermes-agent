from fam import health


def test_transition_alerts_once(db, monkeypatch):
    sent = []

    def fake_readiness(conn, cfg, now=None):
        return {"status": "down"}

    monkeypatch.setattr(health, "bridge_readiness", fake_readiness)

    result1 = health.maybe_alert_readiness(
        db, {}, notify=lambda t: sent.append(t) or True)
    assert result1 is True
    assert len(sent) == 1
    db.commit()

    result2 = health.maybe_alert_readiness(
        db, {}, notify=lambda t: sent.append(t) or True)
    assert result2 is False
    assert len(sent) == 1
    db.commit()


def test_recovery_clears_then_realerts(db, monkeypatch):
    sent = []
    status = {"value": "down"}

    def fake_readiness(conn, cfg, now=None):
        return {"status": status["value"]}

    monkeypatch.setattr(health, "bridge_readiness", fake_readiness)

    health.maybe_alert_readiness(db, {}, notify=lambda t: sent.append(t) or True)
    db.commit()
    assert len(sent) == 1

    status["value"] = "ok"
    result = health.maybe_alert_readiness(db, {}, notify=lambda t: sent.append(t) or True)
    assert result is False
    db.commit()
    assert len(sent) == 1

    status["value"] = "down"
    result = health.maybe_alert_readiness(db, {}, notify=lambda t: sent.append(t) or True)
    assert result is True
    db.commit()
    assert len(sent) == 2
