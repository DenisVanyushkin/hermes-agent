"""_fetch_page routing: headhunter must reuse the persistent CDP session."""
from pathlib import Path
from types import SimpleNamespace

import job_intel.browser_worker as browser_worker


def test_linkedin_worker_targets_the_namespace_management_relay():
    assert browser_worker._CDP_TARGETS["linkedin"]["cdp_url"] == "http://169.254.77.2:19222"


def test_bootstrap_receives_the_pinned_browser_python(monkeypatch, tmp_path: Path):
    script = tmp_path / "browser-desktop-bootstrap.sh"
    script.write_text("#!/bin/sh\n", encoding="utf-8")
    commands = []
    readiness = iter((False, True))

    monkeypatch.setenv("JOB_INTEL_BROWSER_PYTHON", "/opt/gate-a/venv/bin/python")
    monkeypatch.setattr(browser_worker, "_bootstrap_script", lambda: script)
    monkeypatch.setattr(browser_worker, "_cdp_ready", lambda *args, **kwargs: next(readiness))
    monkeypatch.setattr(browser_worker, "_close_foreign_pages", lambda *args: {})

    def fake_run(command, **kwargs):
        commands.append(command)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(browser_worker.subprocess, "run", fake_run)

    browser_worker._ensure_browser_desktop("linkedin")

    command = commands[0]
    assert command[:3] == ["sudo", "-n", "env"]
    assert "JOB_INTEL_BROWSER_PYTHON=/opt/gate-a/venv/bin/python" in command
    assert any(item.startswith("PYTHONPATH=") for item in command)


def test_recycle_matches_chrome_arguments_in_either_order(monkeypatch):
    commands = []

    monkeypatch.setattr(browser_worker, "_cdp_ready", lambda *args, **kwargs: False)

    def fake_run(command, **kwargs):
        commands.append(command)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(browser_worker.subprocess, "run", fake_run)

    browser_worker._recycle_browser_desktop("linkedin")

    kill_script = commands[0][-1]
    assert "ps -u browser -o pid=,args=" in kill_script
    assert 'index($0, "remote-debugging-port=" port)' in kill_script
    assert 'index($0, "profiles/" profile)' in kill_script


def test_fetch_page_routes_headhunter_through_the_cdp_session(monkeypatch):
    """The generic `fetch` CLI command used to skip _with_browser_source
    entirely and always local-launch a fresh Chromium. For headhunter that
    fresh browser has no cookies/profile and can't reach the sandboxed
    chromium binary either -- it always failed. Routing through
    _with_browser_source attaches to the already-running, DDoS-Guard-cleared
    `hh` profile the listing fetcher warms up."""
    calls = []

    def fake_with_browser_source(source, fn):
        calls.append(source)

        class FakeClient:
            def fetch_html(self, url):
                return f"HTML:{url}"

            def session_health_snapshot(self):
                return {"status": "healthy"}

        html, health = fn(FakeClient())
        return html, health, {}

    monkeypatch.setattr(browser_worker, "_with_browser_source", fake_with_browser_source)

    html = browser_worker._fetch_page("https://hh.ru/vacancy/1", source="headhunter")

    assert html == "HTML:https://hh.ru/vacancy/1"
    assert calls == ["headhunter"]


def test_fetch_page_leaves_company_career_on_the_local_launch_path(monkeypatch):
    """company_career has no persistent CDP session (see _CDP_TARGETS) and
    never did -- routing it through _with_browser_source would raise, since
    _ensure_browser_desktop only knows headhunter/linkedin. This pins that
    the fix is scoped to headhunter and doesn't touch the working path."""

    def boom(*a, **kw):
        raise AssertionError("_with_browser_source must not be called for company_career")

    monkeypatch.setattr(browser_worker, "_with_browser_source", boom)

    calls = []

    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def fetch_html(self, url):
            calls.append(url)
            return "LOCAL_HTML"

    monkeypatch.setattr(browser_worker, "resolve_browser_config", lambda source: "CONFIG")
    monkeypatch.setattr(browser_worker, "BrowserSourceClient", lambda config: FakeClient())

    html = browser_worker._fetch_page("https://example.com/careers", source="company_career")

    assert html == "LOCAL_HTML"
    assert calls == ["https://example.com/careers"]
