from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import os
from pathlib import Path
import socket
import signal
import subprocess
import sys
import threading
import time
import types

import pytest

from job_intel import browser_worker
from job_intel.product_search.acquisition_probe import (
    ProbeQuery,
    RuntimeCapabilityResult,
    SourceIsolation,
    build_isolated_probe_environment,
    run_probe,
    validate_experiment_manifest,
)

from job_intel.browser_sourcing import (
    AcquisitionMetrics,
    BrowserAcquisitionConfig,
    BrowserNativeUnavailable,
    BrowserSourceClient,
    browser_native_available,
    extract_company_career_vacancies_from_html,
    extract_linkedin_vacancies_from_html,
    metrics_from_counts,
    resolve_browser_config,
)


def _load_browser_supervisor():
    script = Path(__file__).parents[2] / "scripts/job_intel_browser_supervisor.py"
    spec = importlib.util.spec_from_file_location("job_intel_browser_supervisor", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _manifest_with_source_isolation(root: Path, settings: dict[str, object]) -> dict[str, object]:
    return {
        "gate": "gate-a",
        "environment_id": "product-search-gate-a",
        "root": str(root),
        "paths": {
            name: str(root / name)
            for name in (
                "runtime",
                "experiment.sqlite3",
                "raw-evidence",
                "logs",
                "locks",
                "browser-profile",
                "cache",
                "tmp",
            )
        },
        "python": {
            "executable_path": str(root / "python-runtime/bin/python"),
            "executable_sha256": "a" * 64,
            "stdlib_tree_sha256": "b" * 64,
        },
        "environment": {
            "import_root": str(root / "runtime"),
            "dependency_lock_sha256": "c" * 64,
            "installed_distributions_sha256": "d" * 64,
            "sys_path_sha256": "e" * 64,
        },
        "source_isolation": {"linkedin": settings},
    }


def test_browser_probe_reports_actual_dispatch_counters(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(
        browser_worker,
        "_probe",
        lambda _source: ([], {"status": "ok"}, {"browser_start_ms": 3}),
    )

    assert browser_worker.main(["probe", "linkedin"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["market_query_dispatch_count"] == 0
    assert payload["sudo_dispatch_count"] == 0


def test_extract_linkedin_vacancies_from_html_uses_jobposting_data() -> None:
    html = """
    <html>
      <head>
        <script type="application/ld+json">
        {
          "@context": "https://schema.org",
          "@type": "JobPosting",
          "title": "VP Product, Monetization",
          "description": "Lead monetization and growth",
          "datePosted": "2026-05-16",
          "hiringOrganization": {"name": "Spark Mobility"},
          "jobLocation": {"address": {"addressLocality": "Dubai", "addressCountry": "AE"}},
          "url": "https://www.linkedin.com/jobs/view/123"
        }
        </script>
      </head>
      <body>
        <a href="/jobs/view/123">VP Product, Monetization</a>
      </body>
    </html>
    """

    vacancies = extract_linkedin_vacancies_from_html(html, page_url="https://www.linkedin.com/jobs/search")

    assert len(vacancies) == 1
    vacancy = vacancies[0]
    assert vacancy.source == "linkedin"
    assert vacancy.company == "Spark Mobility"
    assert vacancy.title == "VP Product, Monetization"
    assert vacancy.location == "Dubai, AE"
    assert vacancy.url == "https://www.linkedin.com/jobs/view/123"
    assert "monetization" in vacancy.description.lower()


def test_extract_company_career_vacancies_from_html_parses_common_ats_links() -> None:
    html = """
    <html>
      <body>
        <a href="https://boards.greenhouse.io/acme/jobs/789">Director of Product</a>
        <a href="https://jobs.lever.co/acme/abc">VP Growth</a>
        <script type="application/ld+json">
        {
          "@context": "https://schema.org",
          "@type": "JobPosting",
          "title": "Chief Product Officer",
          "description": "Lead ecosystem and monetization",
          "hiringOrganization": {"name": "Acme"},
          "jobLocation": {"address": {"addressLocality": "London", "addressCountry": "GB"}},
          "url": "https://acme.com/careers/cpo"
        }
        </script>
      </body>
    </html>
    """

    vacancies = extract_company_career_vacancies_from_html(html, page_url="https://acme.com/careers")

    assert [v.title for v in vacancies] == ["Chief Product Officer", "Director of Product", "VP Growth"]
    assert vacancies[0].source == "company_career"
    assert vacancies[0].company == "Acme"
    assert vacancies[0].location == "London, GB"


def test_extract_company_career_vacancies_from_html_handles_custom_ats_domains() -> None:
    html = """
    <html>
      <body>
        <a href="https://careers.adapty.io/roles/vp-product">VP Product</a>
      </body>
    </html>
    """

    vacancies = extract_company_career_vacancies_from_html(html, page_url="https://careers.adapty.io")

    assert len(vacancies) == 1
    vacancy = vacancies[0]
    assert vacancy.company == "Adapty"
    assert vacancy.title == "VP Product"
    assert vacancy.url == "https://careers.adapty.io/roles/vp-product"


def test_extract_linkedin_vacancies_from_html_skips_link_without_company() -> None:
    html = """
    <html>
      <body>
        <a href="/jobs/view/123">VP Product</a>
      </body>
    </html>
    """

    vacancies = extract_linkedin_vacancies_from_html(html, page_url="https://www.linkedin.com/jobs/search")

    assert vacancies == []


def test_metrics_from_counts_calculates_quality_ratios() -> None:
    metrics = metrics_from_counts(
        source="linkedin",
        found=20,
        executive_matches=8,
        accepted=5,
        rejected=15,
        extraction_successes=18,
        extraction_attempts=20,
        anti_bot_failures=2,
    )

    assert isinstance(metrics, AcquisitionMetrics)
    assert metrics.source == "linkedin"
    assert metrics.vacancies_found == 20
    assert metrics.executive_fit_ratio == 0.4
    assert metrics.accepted_rejected_ratio == (5 / 15)
    assert metrics.extraction_success_rate == 0.9
    assert metrics.anti_bot_failure_rate == 0.1
    assert metrics.source_reliability > 0.0
    assert metrics.status in {"operational", "degraded"}


def test_resolve_browser_config_uses_source_specific_defaults(monkeypatch) -> None:
    monkeypatch.delenv("JOB_INTEL_BROWSER_PROFILE_DIR", raising=False)
    monkeypatch.delenv("JOB_INTEL_BROWSER_PROFILE_DIR_LINKEDIN", raising=False)

    linkedin_config = resolve_browser_config("linkedin")
    assert linkedin_config.user_data_dir.as_posix() == "/var/lib/browser-desktop/profiles/linkedin"


def test_resolve_browser_config_respects_env_overrides(monkeypatch) -> None:
    monkeypatch.setenv("JOB_INTEL_BROWSER_PROFILE_DIR", "/tmp/browser-profile")
    monkeypatch.setenv("JOB_INTEL_BROWSER_HEADLESS", "0")
    monkeypatch.setenv("JOB_INTEL_BROWSER_SLOW_MO_MS", "321")
    monkeypatch.setenv("JOB_INTEL_BROWSER_MIN_DELAY_MS", "11")
    monkeypatch.setenv("JOB_INTEL_BROWSER_MAX_DELAY_MS", "22")
    monkeypatch.setenv("JOB_INTEL_BROWSER_SCROLL_PAUSE_MS", "33")
    monkeypatch.setenv("JOB_INTEL_BROWSER_NAV_TIMEOUT_MS", "4444")
    monkeypatch.setenv("JOB_INTEL_BROWSER_MAX_SCROLLS", "5")

    config = resolve_browser_config()

    assert config.user_data_dir.as_posix() == "/tmp/browser-profile"
    assert config.headless is False
    assert config.slow_mo_ms == 321
    assert config.min_delay_ms == 11
    assert config.max_delay_ms == 22
    assert config.scroll_pause_ms == 33
    assert config.navigation_timeout_ms == 4444
    assert config.max_scrolls == 5


def test_browser_client_refuses_empty_required_profile_before_launch(monkeypatch, tmp_path) -> None:
    profile_dir = tmp_path / "custom-profile"
    profile_dir.mkdir()
    client = BrowserSourceClient(BrowserAcquisitionConfig(source_name="linkedin", user_data_dir=profile_dir))

    fake_context = types.SimpleNamespace(
        chromium=types.SimpleNamespace(
            launch_persistent_context=lambda **kwargs: pytest.fail("launch_persistent_context should not be reached")
        )
    )
    fake_playwright = types.SimpleNamespace(start=lambda: fake_context)
    fake_sync_api = types.SimpleNamespace(sync_playwright=lambda: fake_playwright)
    monkeypatch.setattr("job_intel.browser_sourcing.find_spec", lambda _name: object())
    monkeypatch.setitem(sys.modules, "playwright", types.SimpleNamespace(sync_api=fake_sync_api))
    monkeypatch.setitem(sys.modules, "playwright.sync_api", fake_sync_api)
    monkeypatch.setattr(Path, "mkdir", lambda *args, **kwargs: pytest.fail("mkdir should not be called for an empty required profile"))

    with pytest.raises(BrowserNativeUnavailable):
        client.__enter__()


def test_browser_client_fetch_html_wraps_runtime_failures() -> None:
    client = BrowserSourceClient(BrowserAcquisitionConfig())

    class _BrokenContext:
        def new_page(self):
            raise RuntimeError("boom")

    client._context = _BrokenContext()  # type: ignore[attr-defined]

    try:
        client.fetch_html("https://example.com")
    except BrowserNativeUnavailable as exc:
        assert "Playwright browser fetch failed" in str(exc)
    else:
        raise AssertionError("expected BrowserNativeUnavailable")


def test_linkedin_fetch_result_keeps_redirect_and_protected_diagnostic_artifact(monkeypatch, tmp_path: Path) -> None:
    html = """
    <html><body>
      <a class="changed-card-class" href="/jobs/view/101">VP Product</a>
      <a class="another-changed-class" href="/jobs/view/202">Director Product</a>
    </body></html>
    """
    diagnostics_dir = tmp_path / "diagnostics"
    monkeypatch.setenv("JOB_INTEL_BROWSER_DIAGNOSTICS_DIR", str(diagnostics_dir))
    client = BrowserSourceClient(BrowserAcquisitionConfig(source_name="linkedin", max_scrolls=1))

    class _Locator:
        def evaluate_all(self, _script: str) -> list[str]:
            return [
                "https://www.linkedin.com/jobs/view/101",
                "https://www.linkedin.com/jobs/view/202",
            ]

    class _Page:
        url = "https://www.linkedin.com/login"
        mouse = types.SimpleNamespace(wheel=lambda *_args: None)

        def goto(self, _url: str, **_kwargs) -> None:
            self.url = "https://www.linkedin.com/login"

        def wait_for_timeout(self, _milliseconds: int) -> None:
            return None

        def content(self) -> str:
            return html

        def locator(self, selector: str) -> _Locator:
            assert selector == "a[href*='/jobs/view/']"
            return _Locator()

        def screenshot(self, *, path: str, full_page: bool) -> None:
            assert full_page
            Path(path).write_bytes(b"png")

        def title(self) -> str:
            return "Sign in"

        def close(self) -> None:
            return None

    client._context = types.SimpleNamespace(new_page=lambda: _Page())  # type: ignore[attr-defined]
    result = client.fetch_page(
        "https://www.linkedin.com/jobs/search/?keywords=product",
        scrolls=1,
        page_offset=1,
        capture_label="run-1-query-2-cell-uk-page-1",
    )

    assert result.requested_url == "https://www.linkedin.com/jobs/search/?keywords=product"
    assert result.final_url == "https://www.linkedin.com/login"
    assert result.html == html
    assert len(result.html_sha256) == 64
    assert result.page_offset == 1
    assert result.completed_scroll_steps == 1
    assert result.dom_unique_job_ids == frozenset({"101", "202"})
    assert result.artifact_ref and "run-1-query-2-cell-uk-page-1" in result.artifact_ref
    artifacts = list(diagnostics_dir.iterdir())
    page_artifacts = [path for path in artifacts if "run-1-query-2-cell-uk-page-1." in path.name]
    assert {path.suffix for path in page_artifacts} == {".html", ".json", ".png"}
    assert all((path.stat().st_mode & 0o777) == 0o600 for path in page_artifacts)
    summary = json.loads(next(path for path in page_artifacts if path.suffix == ".json").read_text())
    assert "html_excerpt" not in summary
    assert summary["html_sha256"] == result.html_sha256


def test_linkedin_trace_reconciles_dom_ids_independently_from_card_parser(monkeypatch, tmp_path: Path) -> None:
    html = """
    <html><head>
      <script type="application/ld+json">
      {"@context":"https://schema.org","@type":"JobPosting","title":"VP Product",
       "description":"Own monetization","url":"https://www.linkedin.com/jobs/view/101",
       "hiringOrganization":{"name":"Spark"}}
      </script>
      <script type="application/ld+json">
      {"@context":"https://schema.org","@type":"JobPosting","title":"Director Product",
       "description":"Lead growth","url":"https://www.linkedin.com/jobs/view/202",
       "hiringOrganization":{"name":"Spark"}}
      </script>
    </head><body>
      <a class="class-name-changed-again" href="/jobs/view/101">one</a>
      <a class="class-name-changed-again" href="/jobs/view/202">two</a>
      <a class="class-name-changed-again" href="/jobs/view/303">unparsed</a>
    </body></html>
    """
    diagnostics_dir = tmp_path / "diagnostics"
    monkeypatch.setenv("JOB_INTEL_BROWSER_DIAGNOSTICS_DIR", str(diagnostics_dir))
    client = BrowserSourceClient(BrowserAcquisitionConfig(source_name="linkedin", max_scrolls=0, noise_probability=0.0))
    monkeypatch.setattr(client, "_validate_linkedin_auth", lambda: None)

    class _Locator:
        def evaluate_all(self, _script: str) -> list[str]:
            return [
                "https://www.linkedin.com/jobs/view/101",
                "https://www.linkedin.com/jobs/view/202",
                "https://www.linkedin.com/jobs/view/303",
            ]

    class _Page:
        url = ""
        mouse = types.SimpleNamespace(wheel=lambda *_args: None)

        def goto(self, url: str, **_kwargs) -> None:
            self.url = url

        def wait_for_timeout(self, _milliseconds: int) -> None:
            return None

        def content(self) -> str:
            return html

        def locator(self, selector: str) -> _Locator:
            assert selector == "a[href*='/jobs/view/']"
            return _Locator()

        def screenshot(self, *, path: str, full_page: bool) -> None:
            assert full_page
            Path(path).write_bytes(b"png")

        def title(self) -> str:
            return "Search"

        def close(self) -> None:
            return None

    client._context = types.SimpleNamespace(new_page=lambda: _Page())  # type: ignore[attr-defined]
    vacancies = client.search_linkedin(
        "product",
        run_id="run-1",
        query_id="query-2",
        cell_id="uk",
        geography_location="United Kingdom",
    )
    page_trace = client.last_search_trace_snapshot()["pages"][0]

    assert {vacancy.url.rsplit("/", 1)[-1] for vacancy in vacancies} == {"101", "202"}
    assert page_trace["dom_unique_job_ids"] == ["101", "202", "303"]
    assert page_trace["parsed_unique_job_ids_before_role_filter"] == ["101", "202"]
    assert page_trace["returned_unique_job_ids"] == ["101", "202"]
    assert page_trace["excluded_job_ids_by_reason"] == {}
    assert page_trace["unexplained_dom_job_ids"] == ["303"]
    assert set(page_trace["dom_unique_job_ids"]) == (
        set(page_trace["parsed_unique_job_ids_before_role_filter"])
        | set(page_trace["excluded_job_ids_by_reason"])
        | set(page_trace["unexplained_dom_job_ids"])
    )
    assert "run-1-query-2-cell-uk-page-0" in page_trace["artifact_ref"]


def test_linkedin_search_fails_closed_when_diagnostic_artifact_is_unavailable(monkeypatch, tmp_path: Path) -> None:
    diagnostics_path = tmp_path / "diagnostics-not-a-directory"
    diagnostics_path.write_text("not a directory")
    monkeypatch.setenv("JOB_INTEL_BROWSER_DIAGNOSTICS_DIR", str(diagnostics_path))
    client = BrowserSourceClient(BrowserAcquisitionConfig(source_name="linkedin"))
    monkeypatch.setattr(client, "_validate_linkedin_auth", lambda: None)

    class _Page:
        url = "https://www.linkedin.com/jobs/search"
        mouse = types.SimpleNamespace(wheel=lambda *_args: None)

        def goto(self, _url: str, **_kwargs) -> None:
            return None

        def wait_for_timeout(self, _milliseconds: int) -> None:
            return None

        def content(self) -> str:
            return "<html><body>results</body></html>"

        def locator(self, _selector: str):
            return types.SimpleNamespace(evaluate_all=lambda _script: [])

        def close(self) -> None:
            return None

    client._context = types.SimpleNamespace(new_page=lambda: _Page())  # type: ignore[attr-defined]
    with pytest.raises(BrowserNativeUnavailable, match="diagnostic"):
        client.search_linkedin("product", geography_location="United Kingdom")
    health = client.session_health_snapshot()
    assert health["critical_degradation"] is True
    assert health["status"] == "blocked"


def test_acquisition_warm_and_cold_paths_never_dispatch_sudo(monkeypatch) -> None:
    calls: list[list[str]] = []

    def exploding_sudo_run(argv, **kwargs):
        command = list(argv)
        calls.append(command)
        if command[:1] == ["sudo"]:
            raise AssertionError(f"acquisition dispatched sudo: {command!r}")
        return subprocess.CompletedProcess(command, 0, stdout="0\n", stderr="")

    monkeypatch.setattr(browser_worker.subprocess, "run", exploding_sudo_run)
    monkeypatch.setattr(browser_worker, "_close_foreign_pages", lambda *_args: {"remaining_foreign": 0})
    monkeypatch.setattr(browser_worker, "_endpoint_dirty", lambda *_args: False)
    monkeypatch.setattr(browser_worker, "_cdp_ready", lambda *_args, **_kwargs: True)

    assert browser_worker._ensure_browser_desktop("linkedin") == "http://169.254.77.2:19222"

    monkeypatch.setattr(browser_worker, "_cdp_ready", lambda *_args, **_kwargs: False)
    with pytest.raises(BrowserNativeUnavailable, match="bootstrap|endpoint"):
        browser_worker._ensure_browser_desktop("linkedin")

    assert all(command[:1] != ["sudo"] for command in calls)


def test_cloned_profile_manifest_requires_cdp_url(tmp_path: Path) -> None:
    manifest = _manifest_with_source_isolation(
        tmp_path / "experiment",
        {
            "mode": "cloned_profile",
            "collection_method": "browser",
            "path": str(tmp_path / "experiment" / "clone"),
        },
    )

    with pytest.raises(ValueError, match="cdp_url"):
        validate_experiment_manifest(manifest)


def test_cloned_profile_manifest_rejects_invalid_cdp_url(tmp_path: Path) -> None:
    manifest = _manifest_with_source_isolation(
        tmp_path / "experiment",
        {
            "mode": "cloned_profile",
            "collection_method": "browser",
            "path": str(tmp_path / "experiment" / "clone"),
            "cdp_url": "not-a-url",
        },
    )

    with pytest.raises(ValueError, match="invalid cloned profile cdp_url"):
        validate_experiment_manifest(manifest)


def test_cloned_profile_cannot_point_at_the_shared_linkedin_profile(tmp_path: Path) -> None:
    manifest = _manifest_with_source_isolation(
        tmp_path / "experiment",
        {
            "mode": "cloned_profile",
            "collection_method": "browser",
            "path": "/var/lib/browser-desktop/profiles/linkedin",
            "cdp_url": "http://169.254.77.2:19222",
        },
    )

    with pytest.raises(ValueError, match="must differ from the shared LinkedIn profile"):
        validate_experiment_manifest(manifest)


def test_cloned_profile_without_cdp_fails_before_market_dispatch(tmp_path: Path) -> None:
    calls: list[str] = []
    result = run_probe(
        run_id="clone-without-cdp",
        queries=[
            ProbeQuery(
                query_id="query-1",
                cell_id="cell-1",
                source_family="linkedin",
                query="VP Product Almaty",
            )
        ],
        sources={"linkedin": lambda query: calls.append(query) or []},
        output_dir=tmp_path / "evidence",
        runtime_capability_checks={
            "linkedin": lambda: RuntimeCapabilityResult(state="ready")
        },
        isolation={
            "linkedin": SourceIsolation(
                mode="cloned_profile",
                path=tmp_path / "clone",
                collection_method="browser",
                cdp_url="",
            )
        },
        max_attempts=1,
    )

    assert calls == []
    assert result.family_attempts[0]["market_query_dispatch_count"] == 0
    assert result.source_states["linkedin"] == "runtime_capability_blocked"


def test_browser_worker_uses_manifest_cdp_override_without_changing_exclusive_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = Path("/tmp/experiment")
    manifest = _manifest_with_source_isolation(
        root,
        {
            "mode": "cloned_profile",
            "collection_method": "browser",
            "path": str(root / "clone"),
            "cdp_url": "http://127.0.0.1:19223",
        },
    )
    environment = build_isolated_probe_environment(
        manifest, ambient={"JOB_INTEL_BROWSER_CDP_URL": "http://127.0.0.1:1"}
    )
    assert environment["JOB_INTEL_BROWSER_CDP_URL"] == "http://127.0.0.1:19223"
    assert environment["JOB_INTEL_BROWSER_PROFILE_DIR_LINKEDIN"] == str(root / "clone")

    seen: list[str] = []

    def fake_ready(url: str, **_kwargs: object) -> bool:
        seen.append(url)
        return False

    monkeypatch.setattr(browser_worker, "_cdp_ready", fake_ready)
    monkeypatch.setenv("JOB_INTEL_BROWSER_CDP_URL", "http://127.0.0.1:19223")
    with pytest.raises(browser_worker.BrowserNativeUnavailable):
        browser_worker._ensure_browser_desktop("linkedin")
    assert seen == ["http://127.0.0.1:19223"]

    seen.clear()
    monkeypatch.delenv("JOB_INTEL_BROWSER_CDP_URL")
    with pytest.raises(browser_worker.BrowserNativeUnavailable):
        browser_worker._ensure_browser_desktop("linkedin")
    assert seen == ["http://169.254.77.2:19222"]


def test_browser_process_age_check_stays_unprivileged_and_semantic(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_ps(argv, **kwargs):
        calls.append(list(argv))
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout=(
                "  42 /usr/bin/chromium --user-data-dir=/var/lib/browser-desktop/"
                "profiles/linkedin --remote-debugging-port=19222\n"
            ),
            stderr="",
        )

    monkeypatch.setattr(browser_worker.subprocess, "run", fake_ps)

    assert browser_worker._browser_process_age_seconds("linkedin") == 42
    assert calls == [["ps", "-eo", "etimes=,args="]]


def test_supervisor_rejects_profile_override_without_explicit_cdp(tmp_path: Path) -> None:
    command = [
        sys.executable,
        str(Path(__file__).parents[2] / "scripts/job_intel_browser_supervisor.py"),
        "--source",
        "linkedin",
        "--profile",
        str(tmp_path / "a0probe"),
        "--bootstrap-script",
        str(tmp_path / "unused-bootstrap.sh"),
        "--lock-path",
        str(tmp_path / "profile.lock"),
    ]

    result = subprocess.run(command, capture_output=True, text=True, timeout=5)

    assert result.returncode != 0
    assert "--cdp-url" in result.stderr


def test_supervisor_stop_releases_the_profile_lock_holder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    supervisor = _load_browser_supervisor()
    lock_script = Path(__file__).parents[2] / "scripts/job_intel_profile_lock.sh"
    lock_path = tmp_path / "profile.lock"
    holder = subprocess.Popen(
        [str(lock_script), "--path", str(lock_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        assert holder.stdout is not None
        assert b"profile lock acquired:" in holder.stdout.readline()
        holder_process_group = os.getpgid(holder.pid)
        monkeypatch.setattr(supervisor, "_stop_profile", lambda _profile: 0)

        result = supervisor.main(
            [
                "--stop",
                "--source",
                "linkedin",
                "--profile",
                str(tmp_path / "linkedin"),
                "--lock-path",
                str(lock_path),
            ]
        )

        assert result == 0
        assert holder.wait(timeout=3) is not None
        assert subprocess.run(
            ["flock", "-n", str(lock_path), "true"], check=False
        ).returncode == 0
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            group_pids = []
            for process_dir in Path("/proc").iterdir():
                if not process_dir.name.isdigit():
                    continue
                try:
                    stat = (process_dir / "stat").read_text(encoding="ascii")
                    process_group = int(stat.rsplit(")", 1)[1].split()[2])
                except (OSError, ValueError, IndexError):
                    continue
                if process_group == holder_process_group:
                    group_pids.append(int(process_dir.name))
            if not group_pids:
                break
            time.sleep(0.02)
        else:
            pytest.fail(f"profile lock process group survived stop: {group_pids}")
    finally:
        if holder.poll() is None:
            holder.terminate()
            holder.wait(timeout=3)


def test_supervisor_sigterm_releases_the_profile_lock_holder(tmp_path: Path) -> None:
    supervisor = _load_browser_supervisor()
    lock_path = tmp_path / "profile.lock"
    bootstrap_script = tmp_path / "hanging-bootstrap.sh"
    bootstrap_started = tmp_path / "bootstrap-started"
    bootstrap_script.write_text(
        "#!/bin/sh\n"
        f"touch {str(bootstrap_started)!r}\n"
        "while :; do sleep 1; done\n",
        encoding="utf-8",
    )
    bootstrap_script.chmod(0o755)
    command = [
        sys.executable,
        str(Path(__file__).parents[2] / "scripts/job_intel_browser_supervisor.py"),
        "--source",
        "linkedin",
        "--profile",
        str(tmp_path / "linkedin"),
        "--cdp-url",
        "http://127.0.0.1:1",
        "--lock-path",
        str(lock_path),
        "--bootstrap-script",
        str(bootstrap_script),
        "--bootstrap-timeout",
        "30",
        "--startup-timeout",
        "30",
    ]
    process = subprocess.Popen(command, start_new_session=True)
    holder_process_group: int | None = None
    try:
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if bootstrap_started.exists():
                break
            time.sleep(0.02)
        else:
            pytest.fail("supervisor never started the bootstrap after acquiring the profile lock")

        holder_pids = supervisor._lock_holder_pids(lock_path)
        assert len(holder_pids) == 1
        holder_process_group = os.getpgid(holder_pids[0])

        process.send_signal(signal.SIGTERM)
        assert process.wait(timeout=5) == 0
        assert subprocess.run(
            ["flock", "-n", str(lock_path), "true"], check=False
        ).returncode == 0
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            group_pids = []
            for process_dir in Path("/proc").iterdir():
                if not process_dir.name.isdigit():
                    continue
                try:
                    stat = (process_dir / "stat").read_text(encoding="ascii")
                    process_group = int(stat.rsplit(")", 1)[1].split()[2])
                except (OSError, ValueError, IndexError):
                    continue
                if process_group == holder_process_group:
                    group_pids.append(int(process_dir.name))
            if not group_pids:
                break
            time.sleep(0.02)
        else:
            pytest.fail(f"lock-holder process group survived SIGTERM: {group_pids}")
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=3)
        for pid in supervisor._lock_holder_pids(lock_path):
            try:
                process_group = os.getpgid(pid)
            except ProcessLookupError:
                continue
            subprocess.run(["kill", "-TERM", f"-{process_group}"], check=False)
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline and supervisor._pid_is_running(pid):
                time.sleep(0.02)
            if supervisor._pid_is_running(pid):
                subprocess.run(["kill", "-KILL", f"-{process_group}"], check=False)


def _notify_socket(path: Path) -> socket.socket:
    receiver = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    receiver.bind(str(path))
    receiver.settimeout(3)
    return receiver


def _supervisor_command(
    *,
    profile: Path,
    cdp_url: str,
    lock_path: Path,
    notify_path: Path,
    timeout: str,
    network_namespace: str | None = None,
) -> tuple[list[str], dict[str, str], Path]:
    bootstrap_script = profile.parent / "fake-browser-desktop-bootstrap.sh"
    bootstrap_log = profile.parent / "bootstrap-argv.log"
    bootstrap_script.write_text(
        "#!/bin/sh\n"
        f"printf '%s\\n' \"$@\" > {str(bootstrap_log)!r}\n",
        encoding="utf-8",
    )
    bootstrap_script.chmod(0o755)
    command = [
        sys.executable,
        str(Path(__file__).parents[2] / "scripts/job_intel_browser_supervisor.py"),
        "--source",
        "linkedin",
        "--profile",
        str(profile),
        "--cdp-url",
        cdp_url,
        "--lock-path",
        str(lock_path),
        "--startup-timeout",
        timeout,
        "--poll-interval",
        "0.02",
        "--monitor-interval",
        "30",
        "--bootstrap-script",
        str(bootstrap_script),
    ]
    if network_namespace:
        command.extend(["--network-namespace", network_namespace])
    return command, {**os.environ, "NOTIFY_SOCKET": str(notify_path)}, bootstrap_log


def test_supervisor_notifies_ready_only_after_cdp_version_responds(tmp_path: Path) -> None:
    events: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
            if self.path != "/json/version":
                self.send_response(404)
                self.end_headers()
                return
            events.append("json/version")
            body = b'{"Browser":"fake"}'
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    notify_path = tmp_path / "notify.sock"
    receiver = _notify_socket(notify_path)
    profile = tmp_path / "linkedin"
    profile.mkdir()
    command, environment, bootstrap_log = _supervisor_command(
        profile=profile,
        cdp_url=f"http://127.0.0.1:{server.server_port}",
        lock_path=tmp_path / "profile.lock",
        notify_path=notify_path,
        timeout="30",
        network_namespace="ln-eg",
    )
    process = subprocess.Popen(command, env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        try:
            message, _ = receiver.recvfrom(128)
        except socket.timeout:
            stdout, stderr = process.communicate(timeout=3)
            pytest.fail(
                "supervisor did not notify READY: "
                f"returncode={process.returncode}, stdout={stdout!r}, stderr={stderr!r}"
            )
        assert message == b"READY=1\n"
        assert events == ["json/version"]
        assert bootstrap_log.read_text(encoding="utf-8").splitlines() == [
            "--profile",
            "linkedin",
            "--url",
            "https://www.linkedin.com/",
            "--network-namespace",
            "ln-eg",
        ]
        assert process.poll() is None
    finally:
        process.terminate()
        process.wait(timeout=5)
        receiver.close()
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=3)


def test_supervisor_times_out_without_cdp_ready_and_never_notifies(tmp_path: Path) -> None:
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    notify_path = tmp_path / "notify.sock"
    receiver = _notify_socket(notify_path)
    profile = tmp_path / "profile"
    profile.mkdir()
    command, environment, _bootstrap_log = _supervisor_command(
        profile=profile,
        cdp_url=f"http://127.0.0.1:{port}",
        lock_path=tmp_path / "profile.lock",
        notify_path=notify_path,
        timeout="0.2",
    )

    result = subprocess.run(command, env=environment, capture_output=True, timeout=5)

    assert result.returncode != 0
    with pytest.raises(socket.timeout):
        receiver.recvfrom(128)
    receiver.close()


def test_bootstrap_unit_declares_foreground_notify_and_explicit_teardown() -> None:
    unit = (Path(__file__).parents[2] / "deploy/systemd/experiments/job-intel-browser-bootstrap.service").read_text()

    assert "Type=notify" in unit
    assert "KillMode=control-group" in unit
    assert "KillMode=process" not in unit
    assert "KillMode=none" not in unit
    assert "StopWhenUnneeded=yes" in unit
    assert "ExecStop=" in unit
    assert "job_intel_browser_supervisor.py" in unit
    assert "browser-desktop-bootstrap.sh" in unit
    assert "--source linkedin" in unit
    assert "--cdp-url" not in unit
    assert "User=root" in unit


def test_bootstrap_unit_uses_one_interpreter_for_start_and_stop() -> None:
    unit = (Path(__file__).parents[2] / "deploy/systemd/experiments/job-intel-browser-bootstrap.service").read_text()
    commands = {
        line.split("=", 1)[0]: line.split("=", 1)[1]
        for line in unit.splitlines()
        if line.startswith(("ExecStart=", "ExecStop="))
    }

    start_interpreter = commands["ExecStart"].split(maxsplit=1)[0]
    stop_interpreter = commands["ExecStop"].split(maxsplit=1)[0]

    assert start_interpreter == stop_interpreter == "${PRODUCT_SEARCH_PYTHON}"


def _bootstrap_runtime_config(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    script = Path(__file__).parents[2] / "scripts/browser-desktop-bootstrap.sh"
    return subprocess.run(
        ["bash", str(script), "--print-runtime-config", *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=5,
    )


def _runtime_config(stdout: str) -> dict[str, str]:
    return dict(line.split("=", 1) for line in stdout.splitlines() if "=" in line)


def test_bootstrap_runtime_config_preserves_linkedin_defaults() -> None:
    result = _bootstrap_runtime_config("--profile", "linkedin")

    assert result.returncode == 0, result.stderr
    assert _runtime_config(result.stdout) == {
        "profile": "linkedin",
        "namespace": "ln-eg",
        "cdp_port": "9222",
        "novnc_bind": "169.254.77.2",
        "cdp_endpoint": "http://169.254.77.2:19222",
        "cdp_tunnel_host": "169.254.77.2",
        "cdp_tunnel_port": "19222",
    }


def test_bootstrap_runtime_config_preserves_non_linkedin_defaults() -> None:
    result = _bootstrap_runtime_config("--profile", "hh")

    assert result.returncode == 0, result.stderr
    config = _runtime_config(result.stdout)
    assert config["profile"] == "hh"
    assert config["namespace"] == ""
    assert config["novnc_bind"] == "127.0.0.1"
    assert config["cdp_endpoint"] == "http://127.0.0.1:9223"
    assert config["cdp_tunnel_host"] == "127.0.0.1"
    assert config["cdp_tunnel_port"] == "9223"


def test_bootstrap_runtime_config_allows_an_arbitrary_profile_in_a_namespace(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_ip = fake_bin / "ip"
    fake_ip.write_text("#!/bin/sh\nprintf '%s\\n' ln-eg\n", encoding="utf-8")
    fake_ip.chmod(0o755)
    environment = {**os.environ, "PATH": f"{fake_bin}:{os.environ['PATH']}"}

    result = _bootstrap_runtime_config(
        "--profile", "a0probe", "--network-namespace", "ln-eg", env=environment
    )

    assert result.returncode == 0, result.stderr
    config = _runtime_config(result.stdout)
    assert config["profile"] == "a0probe"
    assert config["namespace"] == "ln-eg"
    assert config["novnc_bind"] == "169.254.77.2"
    assert config["cdp_endpoint"] == "http://169.254.77.2:19222"
    assert config["cdp_tunnel_host"] == "169.254.77.2"
    assert config["cdp_tunnel_port"] == "19222"


def test_bootstrap_runtime_config_rejects_a_missing_namespace(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_ip = fake_bin / "ip"
    fake_ip.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake_ip.chmod(0o755)
    environment = {**os.environ, "PATH": f"{fake_bin}:{os.environ['PATH']}"}

    result = _bootstrap_runtime_config(
        "--profile", "a0probe", "--network-namespace", "typo", env=environment
    )

    assert result.returncode != 0
    assert "network namespace" in result.stderr.lower()


def test_acquisition_unit_binds_to_bootstrap_without_privilege_escalation() -> None:
    unit = (Path(__file__).parents[2] / "deploy/systemd/experiments/job-intel-product-search-probe-experiment.service").read_text()

    assert "BindsTo=job-intel-browser-bootstrap.service" in unit
    assert "After=job-intel-browser-bootstrap.service" in unit
    assert "NoNewPrivileges=yes" in unit
    assert "sudo" not in unit.lower()


def test_search_linkedin_stops_when_login_wall_appears(monkeypatch) -> None:
    client = BrowserSourceClient(
        BrowserAcquisitionConfig(
            min_delay_ms=0,
            max_delay_ms=0,
            scroll_pause_ms=0,
            max_scrolls=0,
            noise_probability=0.0,
            linkedin_followup_page_probability=1.0,
            max_linkedin_pages=2,
        )
    )

    page_urls: list[str] = []
    feed_page = '<html><body><main data-testid="mainfeed">Feed</main></body></html>'
    first_page = """
    <html>
      <head>
        <script type="application/ld+json">
        {"@context": "https://schema.org", "@type": "JobPosting", "title": "VP Product", "description": "Own monetization", "url": "https://www.linkedin.com/jobs/view/123", "hiringOrganization": {"name": "Spark"}}
        </script>
      </head>
      <body>
        <a href="/jobs/view/123">VP Product</a>
      </body>
    </html>
    """
    login_wall = "<html><body>Sign in to view more jobs</body></html>"

    def fake_fetch(url: str, *, scrolls=None, capture_label=None):
        page_urls.append(url)
        if url.endswith("/feed/"):
            return feed_page
        return first_page if "start=25" not in url else login_wall

    monkeypatch.setattr(client, "fetch_html", fake_fetch)

    vacancies = client.search_linkedin(
        "VP Product", max_pages=5, geography_location="United Kingdom"
    )

    assert len(vacancies) == 1
    assert page_urls[0] == "https://www.linkedin.com/feed/"
    assert page_urls[1] == "https://www.linkedin.com/jobs/search/?keywords=VP+Product&location=United+Kingdom"
    assert page_urls[-1] == "https://www.linkedin.com/jobs/search/?keywords=VP+Product&location=United+Kingdom&start=25"
    health = client.session_health_snapshot()
    assert health["pages_fetched"] >= 2
    assert health["login_walls"] == 1
    assert health["auth_redirects"] == 1
    assert health["status"] in {"degraded", "blocked"}


def test_search_linkedin_occasionally_opens_a_detail_page(monkeypatch) -> None:
    client = BrowserSourceClient(
        BrowserAcquisitionConfig(
            min_delay_ms=0,
            max_delay_ms=0,
            scroll_pause_ms=0,
            max_scrolls=0,
            noise_probability=1.0,
            linkedin_followup_page_probability=0.0,
            max_linkedin_pages=1,
        )
    )

    page_urls: list[str] = []
    feed_page = '<html><body><main data-testid="mainfeed">Feed</main></body></html>'
    search_page = """
    <html>
      <head>
        <script type="application/ld+json">
        {"@context": "https://schema.org", "@type": "JobPosting", "title": "VP Product", "description": "Own monetization", "url": "https://www.linkedin.com/jobs/view/123", "hiringOrganization": {"name": "Spark"}}
        </script>
        <script type="application/ld+json">
        {"@context": "https://schema.org", "@type": "JobPosting", "title": "Director of Product", "description": "Lead ecosystem", "url": "https://www.linkedin.com/jobs/view/456", "hiringOrganization": {"name": "Spark"}}
        </script>
      </head>
      <body>
        <a href="/jobs/view/123">VP Product</a>
        <a href="/jobs/view/456">Director of Product</a>
      </body>
    </html>
    """
    detail_page = """
    <html>
      <head>
        <script type="application/ld+json">
        {"@context": "https://schema.org", "@type": "JobPosting", "title": "Director of Product", "description": "Lead ecosystem", "url": "https://www.linkedin.com/jobs/view/456", "hiringOrganization": {"name": "Spark"}}
        </script>
      </head>
      <body>Director of Product</body>
    </html>
    """

    def fake_fetch(url: str, *, scrolls=None, capture_label=None):
        page_urls.append(url)
        if url.endswith("/feed/"):
            return feed_page
        return detail_page if url.endswith("/456") else search_page

    monkeypatch.setattr(client, "fetch_html", fake_fetch)
    monkeypatch.setattr("job_intel.browser_sourcing.random.choice", lambda items: items[1])

    vacancies = client.search_linkedin(
        "VP Product", max_pages=1, geography_location="United Kingdom"
    )

    assert any(url.endswith("/456") for url in page_urls)
    assert client.session_health_snapshot()["detail_pages_opened"] == 2
    assert len(vacancies) >= 1


def test_metrics_from_counts_calculates_quality_score() -> None:
    metrics = metrics_from_counts(
        source="linkedin",
        found=20,
        executive_matches=8,
        accepted=5,
        rejected=15,
        extraction_successes=18,
        extraction_attempts=20,
        anti_bot_failures=2,
        normalization_quality=0.9,
    )

    assert isinstance(metrics, AcquisitionMetrics)
    assert metrics.executive_density == 0.4
    assert metrics.signal_noise_ratio == (5 / 15)
    assert metrics.normalization_quality == 0.9
    assert 0.0 <= metrics.acquisition_quality_score <= 1.0
    assert metrics.source_reliability > 0.0
    assert metrics.status in {"operational", "degraded"}

def test_linkedin_url_uses_independent_structured_geography_target() -> None:
    from job_intel.browser_sourcing import build_linkedin_search_url

    urls = [
        build_linkedin_search_url(keywords="VP Product", location="United Kingdom"),
        build_linkedin_search_url(keywords="VP Product", location="Singapore"),
        build_linkedin_search_url(keywords="VP Product", location="Kazakhstan"),
        build_linkedin_search_url(keywords="VP Product", geo_id="103644278"),
    ]

    assert urls == [
        "https://www.linkedin.com/jobs/search/?keywords=VP+Product&location=United+Kingdom",
        "https://www.linkedin.com/jobs/search/?keywords=VP+Product&location=Singapore",
        "https://www.linkedin.com/jobs/search/?keywords=VP+Product&location=Kazakhstan",
        "https://www.linkedin.com/jobs/search/?keywords=VP+Product&geoId=103644278",
    ]
    assert len(set(urls)) == 4


def test_linkedin_without_confirmed_geography_is_named_block_not_keywords_fallback() -> None:
    client = BrowserSourceClient(BrowserAcquisitionConfig(source_name="linkedin"))

    with pytest.raises(BrowserNativeUnavailable, match="blocked_unsupported_geography"):
        client.search_linkedin("VP Product")
