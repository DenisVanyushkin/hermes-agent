from __future__ import annotations

import json
import subprocess

import pytest

from job_intel import company_intel
from job_intel.ats_sources import extract_teamtailor_job_urls
from job_intel.observability import derive_source_reason
from job_intel.store import JobIntelStore


# --- A. Teamtailor job-URL extraction ------------------------------------

TT_RELATIVE_HTML = """
<html><body>
<a href="/jobs/123-head-of-product">Head of Product</a>
<a href="/jobs/456-director">Director</a>
<a href="/about">About</a>
</body></html>
"""

TT_ABSOLUTE_CUSTOM_DOMAIN_HTML = """
<html><body>
<a href="https://career.instabee.com/jobs/7763789-staff-engineer">Staff Engineer</a>
<a href="https://career.instabee.com/jobs/7979576-decision-analyst">Decision Analyst</a>
<a href="https://career.instabee.com/departments/tech">Tech dept</a>
<a href="https://www.linkedin.com/company/instabee/jobs/">LinkedIn jobs</a>
<a href="https://other-company.example.com/jobs/999-external">External job</a>
</body></html>
"""


def test_teamtailor_relative_job_urls_still_extracted() -> None:
    urls = extract_teamtailor_job_urls(TT_RELATIVE_HTML, "https://acme.teamtailor.com/jobs")
    assert urls == [
        "https://acme.teamtailor.com/jobs/123-head-of-product",
        "https://acme.teamtailor.com/jobs/456-director",
    ]


def test_teamtailor_absolute_custom_domain_job_urls_extracted() -> None:
    urls = extract_teamtailor_job_urls(TT_ABSOLUTE_CUSTOM_DOMAIN_HTML, "https://career.instabee.com/jobs")
    assert "https://career.instabee.com/jobs/7763789-staff-engineer" in urls
    assert "https://career.instabee.com/jobs/7979576-decision-analyst" in urls


def test_teamtailor_external_links_not_treated_as_jobs() -> None:
    urls = extract_teamtailor_job_urls(TT_ABSOLUTE_CUSTOM_DOMAIN_HTML, "https://career.instabee.com/jobs")
    assert all("linkedin.com" not in u for u in urls)
    assert all("other-company.example.com" not in u for u in urls)
    assert all("/departments/" not in u for u in urls)


# --- C. Diagnostics taxonomy ----------------------------------------------


def test_reason_disabled_by_config() -> None:
    assert derive_source_reason({"source": "remotive", "status": "skipped", "hits": 0}) == "disabled_by_config"


def test_reason_missing_seeds() -> None:
    payload = {"source": "lever", "status": "empty", "hits": 0, "errors": [], "seeds_present": False}
    assert derive_source_reason(payload) == "missing_seeds"


def test_reason_login_wall() -> None:
    payload = {
        "source": "linkedin",
        "status": "empty",
        "hits": 0,
        "session_health": {"login_walls": 1, "auth_redirects": 1, "anti_bot_events": 1},
    }
    assert derive_source_reason(payload) == "login_wall"


def test_reason_anti_bot_without_login_wall() -> None:
    payload = {
        "source": "headhunter",
        "status": "degraded",
        "hits": 0,
        "session_health": {"login_walls": 0, "auth_redirects": 0, "anti_bot_events": 2},
    }
    assert derive_source_reason(payload) == "anti_bot"


def test_reason_ok_non_empty() -> None:
    assert derive_source_reason({"source": "ashby", "status": "ok", "hits": 12}) == "ok_non_empty"


def test_reason_real_empty_when_seeds_present_and_fetch_attempted() -> None:
    payload = {
        "source": "greenhouse",
        "status": "empty",
        "hits": 0,
        "errors": [],
        "seeds_present": True,
        "pages_fetched": 5,
    }
    assert derive_source_reason(payload) == "real_empty"


def test_reason_explicit_reason_wins() -> None:
    payload = {"source": "target-companies", "status": "ok", "hits": 0, "reason": "js_render_required"}
    assert derive_source_reason(payload) == "js_render_required"


# --- B. target_companies outcome classification ---------------------------


def test_target_company_blocked_403() -> None:
    outcome = company_intel.classify_target_company_outcome(
        openings=0, errors=["403 Client Error: Forbidden for url: https://www.revolut.com/"],
        career_urls=[], ats_refs=[], job_link_count=0, browser_used=False,
    )
    assert outcome == "blocked_403"


def test_target_company_wrong_path_404() -> None:
    outcome = company_intel.classify_target_company_outcome(
        openings=0, errors=["404 Client Error: Not Found for url: https://indrive.com/careers"],
        career_urls=[], ats_refs=[], job_link_count=0, browser_used=False,
    )
    assert outcome == "wrong_path"


def test_target_company_js_render_required() -> None:
    outcome = company_intel.classify_target_company_outcome(
        openings=0, errors=[], career_urls=["https://wise.com/careers"],
        ats_refs=[], job_link_count=98, browser_used=False,
    )
    assert outcome == "js_render_required"


def test_target_company_ats_seeds_discovered_only() -> None:
    outcome = company_intel.classify_target_company_outcome(
        openings=0, errors=[], career_urls=["https://ramp.com/careers"],
        ats_refs=["ashbyhq.com"], job_link_count=5, browser_used=False,
    )
    assert outcome == "ats_seeds_discovered_only"


def test_target_company_ok_non_empty() -> None:
    outcome = company_intel.classify_target_company_outcome(
        openings=3, errors=[], career_urls=["https://x.com/careers"],
        ats_refs=[], job_link_count=10, browser_used=True,
    )
    assert outcome == "ok_non_empty"


def test_browser_fetch_unavailable_is_fail_visible(monkeypatch) -> None:
    """When JOB_INTEL_TARGET_COMPANY_BROWSER=1 but the worker python/playwright is
    unavailable, the failure must surface as an explicit reason, not silence."""
    monkeypatch.setenv("JOB_INTEL_TARGET_COMPANY_BROWSER", "1")
    monkeypatch.setenv("JOB_INTEL_BROWSER_PYTHON", "/nonexistent/playwright-venv/bin/python")
    company_intel._reset_browser_preflight_cache()
    ok, reason = company_intel._browser_preflight()
    assert ok is False
    assert reason == "browser_unavailable"


def test_browser_fetch_html_uses_worker_subprocess(monkeypatch) -> None:
    calls = {}

    def fake_run(cmd, **kwargs):
        calls["cmd"] = cmd

        class R:
            returncode = 0
            stdout = json.dumps({"ok": True, "html": "<html>rendered</html>"})
            stderr = ""

        return R()

    monkeypatch.setattr(company_intel.subprocess, "run", fake_run)
    html = company_intel._browser_fetch_html("https://www.airwallex.com/careers")
    assert html == "<html>rendered</html>"
    assert "job_intel.browser_worker" in " ".join(str(c) for c in calls["cmd"])
    assert "fetch" in calls["cmd"]


def test_browser_fetch_html_worker_failure_raises_explicit(monkeypatch) -> None:
    def fake_run(cmd, **kwargs):
        class R:
            returncode = 1
            stdout = json.dumps({"ok": False, "error": "boom"})
            stderr = "boom"

        return R()

    monkeypatch.setattr(company_intel.subprocess, "run", fake_run)
    with pytest.raises(company_intel.BrowserFetchUnavailable) as err:
        company_intel._browser_fetch_html("https://www.airwallex.com/careers")
    assert err.value.reason == "browser_worker_failed"


# --- C. LinkedIn re-auth health escalation ---------------------------------


def _kpi(login_walls: int) -> dict:
    return {
        "source_status": "empty",
        "acquisition_mode": "browser-native",
        "found_count": 0,
        "login_walls": login_walls,
        "auth_redirects": login_walls,
        "anti_bot_events": login_walls,
    }


def test_health_flags_linkedin_reauth_after_consecutive_login_walls(tmp_path) -> None:
    from job_intel.cli import _check_health_conditions

    store = JobIntelStore(tmp_path / "job_intel.sqlite3")
    store.bootstrap()
    for _ in range(3):
        run_id = store.start_run("daily", metadata={})
        store.upsert_source_kpi_run(run_id, "linkedin", _kpi(1))
        store.finish_run(run_id, status="ok", notes="test")

    problems = _check_health_conditions(store)
    reauth = [p for p in problems if "re-auth" in p.lower()]
    assert reauth, f"expected re-auth problem, got: {problems}"
    assert any("linkedin-reauth" in p for p in reauth)


def test_health_no_reauth_flag_for_single_login_wall(tmp_path) -> None:
    from job_intel.cli import _check_health_conditions

    store = JobIntelStore(tmp_path / "job_intel.sqlite3")
    store.bootstrap()
    run_id = store.start_run("daily", metadata={})
    store.upsert_source_kpi_run(run_id, "linkedin", _kpi(1))
    store.finish_run(run_id, status="ok", notes="test")
    for _ in range(2):
        run_id = store.start_run("daily", metadata={})
        store.upsert_source_kpi_run(run_id, "linkedin", _kpi(0))
        store.finish_run(run_id, status="ok", notes="test")

    problems = _check_health_conditions(store)
    assert not [p for p in problems if "re-auth" in p.lower()]


# --- C. skip_reason persisted to source_kpi_run ----------------------------


def test_upsert_source_kpi_run_persists_skip_reason_and_enabled(tmp_path) -> None:
    store = JobIntelStore(tmp_path / "job_intel.sqlite3")
    store.bootstrap()
    run_id = store.start_run("daily", metadata={})
    store.upsert_source_kpi_run(
        run_id, "lever", {"source_status": "empty", "skip_reason": "missing_seeds", "enabled": 1}
    )
    with store.connect(read_only=True) as conn:
        row = conn.execute(
            "SELECT skip_reason, enabled FROM source_kpi_run WHERE run_id=? AND source='lever'",
            (run_id,),
        ).fetchone()
    assert row[0] == "missing_seeds"
    assert row[1] == 1


def test_target_company_ats_seeds_win_over_partial_404() -> None:
    outcome = company_intel.classify_target_company_outcome(
        openings=0, errors=["https://ramp.com/jobs: 404 Client Error: Not Found"],
        career_urls=["https://ramp.com/careers"], ats_refs=["ashbyhq.com"],
        job_link_count=5, browser_used=False,
    )
    assert outcome == "ats_seeds_discovered_only"
