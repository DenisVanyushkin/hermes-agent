import json
from types import SimpleNamespace
from pathlib import Path

from job_intel.browser_sourcing import BrowserAcquisitionConfig, BrowserSourceClient
from job_intel.models import Vacancy


FIXTURES = Path(__file__).parents[1] / "fixtures" / "job_intel" / "linkedin"


def _vacancy(number: int) -> Vacancy:
    return Vacancy(
        source="linkedin",
        source_id=str(number),
        company="Example",
        title="VP Product",
        location="Remote",
        url=f"https://www.linkedin.com/jobs/view/{number}",
        description="VP Product",
    )


def test_search_linkedin_cap_is_shared_across_followup_pages(monkeypatch) -> None:
    detail_html = (FIXTURES / "detail-transunion.html").read_text(encoding="utf-8")

    def search_html(page_number: int) -> str:
        scripts = []
        for offset in range(3):
            number = page_number * 10 + offset
            jobposting = {
                "@type": "JobPosting",
                "title": "VP Product",
                "description": "VP Product",
                "url": f"https://www.linkedin.com/jobs/view/{number}",
                "hiringOrganization": {"name": "Example"},
            }
            scripts.append(
                '<script type="application/ld+json">'
                f"{json.dumps(jobposting)}"
                "</script>"
            )
        return "\n".join(scripts)

    client = BrowserSourceClient(
        BrowserAcquisitionConfig(
            source_name="linkedin",
            min_delay_ms=0,
            max_delay_ms=0,
            scroll_pause_ms=0,
            max_scrolls=0,
        )
    )
    detail_urls: list[str] = []

    def fake_fetch(url, **_kwargs):
        client._last_fetch_result = SimpleNamespace(final_url=url, http_status=200)
        if "/jobs/view/" in url:
            detail_urls.append(url)
            return detail_html
        page_number = 1 if "start=25" in url else 0
        return search_html(page_number)

    monkeypatch.setattr(client, "fetch_html", fake_fetch)
    monkeypatch.setattr(
        client, "_validate_linkedin_auth", lambda **_kwargs: "without_session"
    )
    monkeypatch.setattr(client, "_sleep", lambda **_kwargs: None)
    monkeypatch.setattr(
        client, "_linkedin_page_plan", lambda *_args, **_kwargs: [0, 1]
    )
    monkeypatch.setenv("JOB_INTEL_LINKEDIN_DETAIL_PAGES_PER_CELL_MAX", "2")
    monkeypatch.setenv("JOB_INTEL_LINKEDIN_DETAIL_PAGE_DELAY_MS", "0")

    client.search_linkedin(
        "VP Product",
        max_pages=2,
        geography_location="United Kingdom",
        detail_page_budget=100,
    )

    assert len(detail_urls) == 2
    assert client._last_search_trace["detail_pages_budget_opened"] == 2
