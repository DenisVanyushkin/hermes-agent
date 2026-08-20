"""The hh request layer makes silent API failure modes explicit."""
import time

import pytest

from job_intel import hh_api


def test_user_agent_is_not_the_blacklisted_doc_example():
    assert "my-app-feedback@example.com" not in hh_api.HH_USER_AGENT
    assert hh_api.HH_USER_AGENT == "hermes-job-intel/1.0 (denis@vanyushk.in)"


def test_search_always_sends_no_magic(monkeypatch):
    seen = {}

    def _fake_get(path, params, token):
        seen.update(params)
        return {"items": [], "found": 0, "arguments": []}

    monkeypatch.setattr(hh_api, "_get", _fake_get)
    monkeypatch.setattr(hh_api, "get_app_token", lambda **kw: "T")

    hh_api.search_vacancies(text="Head of Product")

    assert seen["no_magic"] == "true"
    assert seen["describe_arguments"] == "true"


def test_search_raises_when_the_server_ignored_a_parameter(monkeypatch):
    monkeypatch.setattr(hh_api, "get_app_token", lambda **kw: "T")
    monkeypatch.setattr(
        hh_api,
        "_get",
        lambda path, params, token: {
            "items": [],
            "found": 99999,
            "arguments": [{"argument": "text", "value": "x"}],
        },
    )

    with pytest.raises(hh_api.HHArgumentDropped) as exc:
        hh_api.search_vacancies(text="x", area=40)

    assert "area" in str(exc.value)


def test_auth_failure_is_detected_on_403_not_401(monkeypatch):
    monkeypatch.setattr(hh_api, "get_app_token", lambda **kw: "T")

    def _403(path, params, token):
        raise hh_api._HTTPStatus(403, {"errors": [{"type": "oauth", "value": "token_expired"}]})

    monkeypatch.setattr(hh_api, "_get", _403)

    with pytest.raises(hh_api.HHAuthError):
        hh_api.search_vacancies(text="x")


def test_request_delay_is_overridable(monkeypatch):
    sleeps = []
    monkeypatch.setenv("JOB_INTEL_HH_DELAY_SECONDS", "0.25")
    monkeypatch.setattr(hh_api, "_last_request_at", time.monotonic())
    monkeypatch.setattr(hh_api, "_sleep", sleeps.append)
    monkeypatch.setattr(hh_api, "get_app_token", lambda **kw: "T")
    monkeypatch.setattr(hh_api, "_get", lambda path, params, token: {"items": [], "found": 0, "arguments": []})

    hh_api.search_vacancies(text="x")

    assert sleeps
    assert 0 < sleeps[0] <= 0.25
