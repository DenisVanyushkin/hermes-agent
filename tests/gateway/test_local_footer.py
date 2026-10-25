"""Fork: the account-usage quota line under the runtime footer (gateway/local_footer.py)."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from gateway import local_footer
from gateway.runtime_footer import build_footer_line


@pytest.fixture(autouse=True)
def _fresh_cache():
    local_footer._USAGE_CACHE.clear()
    yield
    local_footer._USAGE_CACHE.clear()


def _snapshot(*windows, available=True):
    return SimpleNamespace(available=available, windows=list(windows))


def _window(label, used, reset_at=None, detail=None):
    return SimpleNamespace(label=label, used_percent=used, reset_at=reset_at, detail=detail)


def _config(account_usage):
    return {
        "model": {"provider": "openai-codex", "base_url": "https://chatgpt.com/backend-api/codex"},
        "display": {"runtime_footer": {"enabled": True, "fields": ["model"], "account_usage": account_usage}},
    }


def _footer(cfg):
    return build_footer_line(user_config=cfg, platform_key="telegram", model="gpt-6-luna",
                             context_tokens=0, context_length=None)


def test_quota_line_goes_under_the_footer_when_enabled(monkeypatch):
    calls = []
    reset = datetime(2026, 10, 25, 9, 0, tzinfo=timezone.utc)

    def fetch(provider, *, base_url=None, api_key=None):
        calls.append((provider, base_url))
        return _snapshot(_window("5h", 38, reset_at=reset), _window("week", 90, detail="plus"))

    monkeypatch.setattr("agent.account_usage.fetch_account_usage", fetch)

    out = _footer(_config(True))

    assert out.splitlines() == [
        "gpt-6-luna",
        "Quota: 5h: 62% left until 2026-10-25 14:00 +05 · week: 10% left (plus)",
    ]
    assert calls == [("openai-codex", "https://chatgpt.com/backend-api/codex")]


def test_no_quota_line_without_the_flag(monkeypatch):
    monkeypatch.setattr("agent.account_usage.fetch_account_usage",
                        lambda *a, **k: pytest.fail("usage fetched without account_usage"))

    assert _footer(_config(False)) == "gpt-6-luna"


def test_usage_is_cached_between_turns(monkeypatch):
    calls = []
    monkeypatch.setattr("agent.account_usage.fetch_account_usage",
                        lambda *a, **k: calls.append(1) or _snapshot(_window("5h", 10)))

    _footer(_config(True))
    _footer(_config(True))

    assert len(calls) == 1


@pytest.mark.parametrize("snapshot", [None, _snapshot(available=False), _snapshot(_window("5h", None))])
def test_unavailable_usage_adds_nothing(monkeypatch, snapshot):
    monkeypatch.setattr("agent.account_usage.fetch_account_usage", lambda *a, **k: snapshot)

    assert _footer(_config(True)) == "gpt-6-luna"


def test_usage_fetch_failure_never_breaks_the_footer(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("usage endpoint down")

    monkeypatch.setattr("agent.account_usage.fetch_account_usage", boom)

    assert _footer(_config(True)) == "gpt-6-luna"
