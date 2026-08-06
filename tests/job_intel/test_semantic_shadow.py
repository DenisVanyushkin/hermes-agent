"""Phase III shadow deployment — observe-only semantic shadow. Offline."""
from __future__ import annotations

import os

from job_intel.models import Vacancy
from job_intel.vacancy_understanding.shadow_deploy import (
    SEMANTIC_SHADOW_VERSION,
    evaluate_semantic_shadow,
    semantic_shadow_enabled,
)


def _vac(**kw):
    base = dict(source="ashby", source_id="s1", company="Acme",
                title="Head of Product, Growth",
                location="Remote", url="https://ex/1",
                description="Own the growth product roadmap end to end. Full P&L ownership.")
    base.update(kw)
    return Vacancy(**base)


def test_shadow_returns_wellformed_decision_for_normal_vacancy():
    r = evaluate_semantic_shadow(_vac())
    assert r["status"] == "ok"
    assert r["shadow_version"] == SEMANTIC_SHADOW_VERSION
    assert r["recommendation"] in {
        "exceptional", "strong", "promising", "unclear", "not_recommended"}
    assert r["action"]
    assert "semantic_hash" in r and r["semantic_hash"]


def test_shadow_is_deterministic():
    a = evaluate_semantic_shadow(_vac())
    b = evaluate_semantic_shadow(_vac())
    assert a["semantic_hash"] == b["semantic_hash"]
    assert a["recommendation"] == b["recommendation"]


def test_shadow_never_raises_on_bad_input():
    # empty/degenerate vacancy must yield an error dict, not an exception
    r = evaluate_semantic_shadow(_vac(title="", description="", company=""))
    assert r["status"] in {"ok", "error"}
    assert r["shadow_version"] == SEMANTIC_SHADOW_VERSION
    if r["status"] == "error":
        assert "error" in r


def test_shadow_makes_no_network_call():
    import socket

    real = socket.socket
    socket.socket = lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("network in shadow"))
    try:
        r = evaluate_semantic_shadow(_vac())
    finally:
        socket.socket = real
    assert r["status"] == "ok"


def test_flag_default_and_override(monkeypatch):
    monkeypatch.delenv("SEMANTIC_SHADOW_ENABLED", raising=False)
    assert semantic_shadow_enabled() is True  # deployed default: observe-only ON
    monkeypatch.setenv("SEMANTIC_SHADOW_ENABLED", "0")
    assert semantic_shadow_enabled() is False
    monkeypatch.setenv("SEMANTIC_SHADOW_ENABLED", "false")
    assert semantic_shadow_enabled() is False
    monkeypatch.setenv("SEMANTIC_SHADOW_ENABLED", "1")
    assert semantic_shadow_enabled() is True
