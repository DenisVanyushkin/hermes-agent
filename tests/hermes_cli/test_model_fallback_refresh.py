from __future__ import annotations

import json
from argparse import Namespace
from datetime import datetime, timezone
from pathlib import Path

from hermes_cli import model_fallback_refresh as refresh
from hermes_cli.fallback_cmd import cmd_fallback


def _sample_config() -> dict:
    return {
        "fallback_providers": [
            {"provider": "openrouter", "model": "anthropic/claude-sonnet-4.6"},
            {"provider": "anthropic", "model": "claude-opus-4.6", "base_url": "https://proxy.example.com/anthropic"},
            {"provider": "custom", "model": "my-local-model", "base_url": "http://127.0.0.1:1234/v1"},
            {"provider": "nous", "model": "kimi-k2.5"},
        ]
    }


def test_refresh_records_mocked_statuses_and_preserves_order():
    statuses = {
        ("openrouter", "anthropic/claude-sonnet-4.6"): {"status": "ok", "latency_ms": 11},
        ("anthropic", "claude-opus-4.6"): {"status": "degraded", "error_type": "timeout", "error_summary": "timeout talking to https://proxy.example.com/anthropic?api_key=secret"},
        ("custom", "my-local-model"): {"status": "unavailable", "error_type": "connection_error", "error_summary": "Authorization: Bearer secret-token"},
        ("nous", "kimi-k2.5"): {"status": "skipped", "error_type": "missing_credentials", "error_summary": "OPENAI_API_KEY=sk-secret"},
    }

    def probe(candidate, timeout_s):
        del timeout_s
        return statuses[(candidate["provider"], candidate["model"])]

    state = refresh.refresh_model_fallbacks(
        config=_sample_config(),
        probe_backend=probe,
        now=datetime(2026, 6, 9, 12, 0, tzinfo=timezone.utc),
    )

    assert [entry["status"] for entry in state["checked_candidates"]] == ["ok", "degraded", "unavailable", "skipped"]
    assert state["summary"]["status_counts"] == {"ok": 1, "degraded": 1, "unavailable": 1, "skipped": 1}
    assert state["checked_candidates"][1]["sanitized_error_summary"].endswith("api_key=[REDACTED]")
    assert state["checked_candidates"][2]["sanitized_error_summary"] == "Authorization: Bearer [REDACTED]"
    assert state["checked_candidates"][0]["last_success_at"] == "2026-06-09T12:00:00Z"


def test_missing_credentials_do_not_crash_refresh(monkeypatch):
    class Pool:
        def has_credentials(self):
            return False

    monkeypatch.setattr(refresh, "load_pool", lambda provider: Pool())

    state = refresh.refresh_model_fallbacks(
        config={"fallback_providers": [{"provider": "openrouter", "model": "anthropic/claude-sonnet-4.6"}]},
        now=datetime(2026, 6, 9, 12, 0, tzinfo=timezone.utc),
    )

    entry = state["checked_candidates"][0]
    assert entry["status"] == "skipped"
    assert entry["sanitized_error_type"] == "missing_credentials"
    assert "credentials" in entry["sanitized_error_summary"].lower()


def test_error_sanitizer_removes_tokens_cookies_headers_and_query_secrets():
    err_type, summary = refresh.sanitize_provider_error(
        "Authorization: Bearer secret-token Cookie: sessionid=abc123 https://example.com/v1?api_key=abc&token=xyz OPENAI_API_KEY=sk-topsecret"
    )

    assert err_type == "provider_error"
    assert "secret-token" not in summary
    assert "abc123" not in summary
    assert "sk-topsecret" not in summary
    assert "api_key=[REDACTED]" in summary
    assert "token=[REDACTED]" in summary
    assert "Cookie: [REDACTED]" in summary


def test_state_file_schema_is_deterministic_and_versioned(tmp_path: Path):
    config = {"fallback_providers": [{"provider": "openrouter", "model": "anthropic/claude-sonnet-4.6"}]}

    def probe(candidate, timeout_s):
        del candidate, timeout_s
        return {"status": "ok", "latency_ms": 7}

    now = datetime(2026, 6, 9, 12, 0, tzinfo=timezone.utc)
    first = refresh.refresh_model_fallbacks(config=config, probe_backend=probe, now=now, output_path=tmp_path / "model_fallbacks.json")
    second = refresh.refresh_model_fallbacks(config=config, probe_backend=probe, now=now, output_path=tmp_path / "model_fallbacks.json")

    assert first == second
    assert first["schema_version"] == refresh.SCHEMA_VERSION == 1

    path = refresh.write_fallback_state(first, tmp_path / "model_fallbacks.json")
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded == first


def test_recommended_fallback_chain_is_deterministic():
    config = _sample_config()

    def probe(candidate, timeout_s):
        del timeout_s
        if candidate["provider"] == "custom":
            return {"status": "unavailable", "error_type": "down", "error_summary": "down"}
        if candidate["provider"] == "nous":
            return {"status": "skipped", "error_type": "missing_credentials", "error_summary": "missing"}
        return {"status": "degraded", "error_type": "soft", "error_summary": "safe-mode"}

    state = refresh.refresh_model_fallbacks(config=config, probe_backend=probe, now=datetime(2026, 6, 9, 12, 0, tzinfo=timezone.utc))

    assert state["recommended_fallback_chain"] == [
        {"provider": "openrouter", "model": "anthropic/claude-sonnet-4.6"},
        {"provider": "anthropic", "model": "claude-opus-4.6", "base_url": "https://proxy.example.com/anthropic"},
    ]


def test_cli_refresh_dry_run_uses_mocked_backend(monkeypatch, capsys):
    sample_state = {
        "schema_version": 1,
        "generated_at": "2026-06-09T12:00:00Z",
        "config_hash": "sha256:test",
        "checked_candidates": [],
        "recommended_fallback_chain": [],
        "summary": {"total_candidates": 0, "status_counts": {"ok": 0, "degraded": 0, "unavailable": 0, "skipped": 0}},
    }

    calls = {"refreshed": 0, "written": 0}

    monkeypatch.setattr("hermes_cli.model_fallback_refresh.refresh_model_fallbacks", lambda **kwargs: calls.__setitem__("refreshed", calls["refreshed"] + 1) or sample_state)
    monkeypatch.setattr("hermes_cli.model_fallback_refresh.write_fallback_state", lambda *args, **kwargs: calls.__setitem__("written", calls["written"] + 1))

    cmd_fallback(Namespace(fallback_command="refresh", output="/tmp/model_fallbacks.json", dry_run=True, timeout=1.5))

    out = capsys.readouterr().out
    assert calls == {"refreshed": 1, "written": 0}
    assert "Dry run" in out
    assert "generated_at=2026-06-09T12:00:00Z" in out


def test_refresh_does_not_mutate_live_config():
    config = _sample_config()
    snapshot = json.loads(json.dumps(config))

    def probe(candidate, timeout_s):
        del candidate, timeout_s
        return {"status": "ok", "latency_ms": 3}

    refresh.refresh_model_fallbacks(config=config, probe_backend=probe, now=datetime(2026, 6, 9, 12, 0, tzinfo=timezone.utc))

    assert config == snapshot
