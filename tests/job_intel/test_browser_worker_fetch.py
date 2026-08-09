"""_fetch_page routing: headhunter must reuse the persistent CDP session."""
import job_intel.browser_worker as browser_worker


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
