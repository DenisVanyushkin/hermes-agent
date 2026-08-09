"""fetch_headhunter_detail_html: browser-native detail page fetch."""
import pytest

from job_intel.sources import fetch_headhunter_detail_html, SourceFetchError


def test_fetch_headhunter_detail_html_returns_the_page_html(monkeypatch):
    """Reuses the same _browser_worker_payload plumbing fetch_headhunter_vacancies
    already uses, dispatching to the generic `fetch` CLI command with
    --source headhunter so it rides the persistent, DDoS-Guard-cleared CDP
    session instead of a plain `requests` call (which always 403s from this
    VPS's IP -- see job-intel-title-only-sources incident)."""
    seen = {}

    def fake_payload(command, *args):
        seen["command"] = command
        seen["args"] = args
        return {"ok": True, "html": "<html>vacancy text</html>", "html_len": 24}

    monkeypatch.setattr("job_intel.sources._browser_worker_payload", fake_payload)

    html = fetch_headhunter_detail_html("https://hh.ru/vacancy/133460660")

    assert html == "<html>vacancy text</html>"
    assert seen["command"] == "fetch"
    assert seen["args"] == ("https://hh.ru/vacancy/133460660", "--source", "headhunter")


def test_fetch_headhunter_detail_html_raises_when_browser_native_is_unavailable(monkeypatch):
    """The caller (ats_sources.fetch_headhunter_detail) decides what a
    transport failure means for the retry taxonomy; this function's job is
    only to surface it, not swallow it -- unlike the plain-text detail
    fetchers (_detail_json/_detail_html), which never raise by design."""

    def fake_payload(command, *args):
        raise SourceFetchError("Playwright is not installed")

    monkeypatch.setattr("job_intel.sources._browser_worker_payload", fake_payload)

    with pytest.raises(SourceFetchError):
        fetch_headhunter_detail_html("https://hh.ru/vacancy/133460660")
