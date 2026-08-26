from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import types

import pytest

from job_intel import browser_worker

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


def _notify_socket(path: Path) -> socket.socket:
    receiver = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    receiver.bind(str(path))
    receiver.settimeout(3)
    return receiver


def _supervisor_command(
    *, profile: Path, cdp_url: str, lock_path: Path, notify_path: Path, timeout: str
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
    )
    process = subprocess.Popen(command, env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        message, _ = receiver.recvfrom(128)
        assert message == b"READY=1\n"
        assert events == ["json/version"]
        assert bootstrap_log.read_text(encoding="utf-8").splitlines() == [
            "--profile",
            "linkedin",
            "--url",
            "https://www.linkedin.com/",
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

    vacancies = client.search_linkedin("VP Product", max_pages=5)

    assert len(vacancies) == 1
    assert page_urls[0] == "https://www.linkedin.com/feed/"
    assert page_urls[1] == "https://www.linkedin.com/jobs/search/?keywords=VP+Product"
    assert page_urls[-1] == "https://www.linkedin.com/jobs/search/?keywords=VP+Product&start=25"
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

    vacancies = client.search_linkedin("VP Product", max_pages=1)

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
