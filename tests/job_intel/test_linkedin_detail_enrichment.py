from types import SimpleNamespace
from pathlib import Path

import pytest

from job_intel.browser_sourcing import (
    BrowserAcquisitionConfig,
    BrowserSourceClient,
    extract_linkedin_detail_content_from_html,
    linkedin_detail_title_matches,
)
from job_intel.models import Vacancy
from job_intel.store import JobIntelStore


FIXTURES = Path(__file__).parents[1] / "fixtures" / "job_intel" / "linkedin"


def _vacancy(title: str, number: int = 1) -> Vacancy:
    return Vacancy(
        source="linkedin",
        source_id=str(number),
        company="Example",
        title=title,
        location="Remote",
        url=f"https://www.linkedin.com/jobs/view/{number}",
        description=title,
    )


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("VP Product", True),
        ("Digital Business Director", True),
        ("GM Market", True),
        ("Head of Growth Product", True),
        ("Senior Product Manager", False),
        ("Product Designer", False),
    ],
)
def test_detail_title_filter_uses_the_accepted_eight_family_vocabulary(title, expected) -> None:
    assert linkedin_detail_title_matches(title) is expected


def test_detail_parser_reads_both_real_public_fixtures() -> None:
    for name in ("detail-cybertrend.html", "detail-transunion.html"):
        content = extract_linkedin_detail_content_from_html(
            (FIXTURES / name).read_text(encoding="utf-8"),
            page_url="https://www.linkedin.com/jobs/view/1",
        )
        assert content is not None
        assert len(content.description) > 1_000
        assert content.criteria
        assert any(value.strip() for value in content.criteria.values())


def test_public_detail_fixture_does_not_create_a_false_login_wall() -> None:
    html = (FIXTURES / "detail-transunion.html").read_text(encoding="utf-8")
    client = BrowserSourceClient(BrowserAcquisitionConfig(source_name="linkedin"))
    client._health.source = "linkedin"

    client._observe_page(
        "https://www.linkedin.com/jobs/view/1",
        html,
        0,
        detail_page=True,
        http_status=200,
    )

    health = client.session_health_snapshot()
    assert health["login_walls"] == 0
    assert health["auth_redirects"] == 0
    assert health["anti_bot_events"] == 0


def test_detail_enrichment_updates_candidate_and_records_public_provenance(monkeypatch) -> None:
    html = (FIXTURES / "detail-transunion.html").read_text(encoding="utf-8")
    client = BrowserSourceClient(BrowserAcquisitionConfig(source_name="linkedin"))
    vacancy = _vacancy("VP Product")

    def fake_fetch(url, **_kwargs):
        client._last_fetch_result = SimpleNamespace(final_url=url, http_status=200)
        return html

    monkeypatch.setattr(client, "fetch_html", fake_fetch)
    monkeypatch.setattr("job_intel.browser_sourcing.time.sleep", lambda _seconds: None)
    monkeypatch.setenv("JOB_INTEL_LINKEDIN_DETAIL_PAGE_BUDGET", "1")
    monkeypatch.setenv("JOB_INTEL_LINKEDIN_DETAIL_PAGE_DELAY_MS", "0")

    stats = client._enrich_linkedin_vacancies(
        [vacancy], observed_at="2026-09-14T10:00:00+00:00"
    )

    assert stats["planned"] == 1
    assert stats["opened"] == 1
    assert stats["filled"] == 1
    assert stats["blocked"] == 0
    assert len(vacancy.description) > 1_000
    assert "Job criteria:" in vacancy.description
    provenance = vacancy.metadata["linkedin_detail_enrichment"]
    assert provenance["source"] == "public_linkedin_job_detail"
    assert provenance["observed_at"] == "2026-09-14T10:00:00+00:00"
    assert provenance["url"] == vacancy.url
    assert provenance["criteria"]


def test_upsert_replaces_title_only_description_on_repeat(tmp_path) -> None:
    store = JobIntelStore(tmp_path / "job-intel.sqlite3")
    store.bootstrap()
    first = _vacancy("VP Product", number=7)
    vacancy_key = "linkedin:https://www.linkedin.com/jobs/view/7"
    store.upsert_vacancy(first, vacancy_key)

    enriched = first.model_copy(
        update={
            "description": "Long public detail text " * 100,
            "metadata": {
                "linkedin_detail_enrichment": {
                    "source": "public_linkedin_job_detail",
                    "observed_at": "2026-09-14T10:00:00+00:00",
                }
            },
        }
    )
    store.upsert_vacancy(enriched, vacancy_key)

    saved = store.get_vacancy_by_key(vacancy_key)
    assert saved is not None
    assert saved["description"] == enriched.description
    assert "public_linkedin_job_detail" in saved["metadata_json"]


def test_upsert_preserves_linkedin_enrichment_when_plain_listing_repeats(tmp_path) -> None:
    store = JobIntelStore(tmp_path / "job-intel.sqlite3")
    store.bootstrap()
    listing = _vacancy("VP Product", number=8)
    vacancy_key = "linkedin:https://www.linkedin.com/jobs/view/8"
    enriched = listing.model_copy(
        update={
            "description": "Full public detail text " * 100,
            "metadata": {
                "linkedin_detail_enrichment": {
                    "source": "public_linkedin_job_detail",
                    "observed_at": "2026-09-14T10:00:00+00:00",
                }
            },
        }
    )
    store.upsert_vacancy(enriched, vacancy_key)

    store.upsert_vacancy(
        listing.model_copy(update={"metadata": {"surface": "linkedin_search"}}),
        vacancy_key,
    )

    saved = store.get_vacancy_by_key(vacancy_key)
    assert saved is not None
    assert saved["description"] == enriched.description
    assert "public_linkedin_job_detail" in saved["metadata_json"]
    assert store.fetch_linkedin_enriched_urls() == {
        "https://www.linkedin.com/jobs/view/8"
    }


def test_fresh_linkedin_enrichment_replaces_previous_text_and_provenance(tmp_path) -> None:
    store = JobIntelStore(tmp_path / "job-intel.sqlite3")
    store.bootstrap()
    listing = _vacancy("VP Product", number=9)
    vacancy_key = "linkedin:https://www.linkedin.com/jobs/view/9"
    old = listing.model_copy(
        update={
            "description": "Old public detail text " * 100,
            "metadata": {
                "linkedin_detail_enrichment": {
                    "source": "public_linkedin_job_detail",
                    "observed_at": "2026-09-14T10:00:00+00:00",
                }
            },
        }
    )
    fresh = listing.model_copy(
        update={
            "description": "Fresh public detail text " * 100,
            "metadata": {
                "linkedin_detail_enrichment": {
                    "source": "public_linkedin_job_detail",
                    "observed_at": "2026-09-14T11:00:00+00:00",
                }
            },
        }
    )
    store.upsert_vacancy(old, vacancy_key)
    store.upsert_vacancy(fresh, vacancy_key)

    saved = store.get_vacancy_by_key(vacancy_key)
    assert saved is not None
    assert saved["description"] == fresh.description
    assert "2026-09-14T11:00:00+00:00" in saved["metadata_json"]


def test_detail_enrichment_skips_a_cached_linkedin_detail_url(monkeypatch) -> None:
    client = BrowserSourceClient(BrowserAcquisitionConfig(source_name="linkedin"))
    vacancy = _vacancy("VP Product", number=10)
    client._linkedin_detail_enriched_urls.add(vacancy.url)

    def unexpected_fetch(*_args, **_kwargs):
        raise AssertionError("cached detail URL must not be fetched")

    monkeypatch.setattr(client, "fetch_html", unexpected_fetch)
    stats = client._enrich_linkedin_vacancies([vacancy], detail_page_budget=1)

    assert stats["planned"] == 0
    assert stats["opened"] == 0
    assert stats["filled"] == 0


def test_detail_enrichment_honours_budget_and_delay(monkeypatch) -> None:
    html = (FIXTURES / "detail-cybertrend.html").read_text(encoding="utf-8")
    client = BrowserSourceClient(BrowserAcquisitionConfig(source_name="linkedin"))
    urls: list[str] = []
    sleeps: list[float] = []

    def fake_fetch(url, **_kwargs):
        urls.append(url)
        client._last_fetch_result = SimpleNamespace(final_url=url, http_status=200)
        return html

    monkeypatch.setattr(client, "fetch_html", fake_fetch)
    monkeypatch.setattr("job_intel.browser_sourcing.time.sleep", sleeps.append)
    monkeypatch.setenv("JOB_INTEL_LINKEDIN_DETAIL_PAGE_BUDGET", "2")
    monkeypatch.setenv("JOB_INTEL_LINKEDIN_DETAIL_PAGE_DELAY_MS", "17")

    stats = client._enrich_linkedin_vacancies(
        [_vacancy("VP Product", 1), _vacancy("Chief Growth Officer", 2), _vacancy("GM Market", 3)],
        observed_at="2026-09-14T10:00:00+00:00",
    )

    assert stats["planned"] == 2
    assert stats["opened"] == 2
    assert stats["filled"] == 2
    assert len(urls) == 2
    assert sleeps == [0.017]


@pytest.mark.parametrize(
    ("html", "status", "url"),
    [
        ("<html><body>Sign in to view more jobs</body></html>", 200, "https://www.linkedin.com/jobs/view/1"),
        ("<html><body>Please verify this challenge</body></html>", 200, "https://www.linkedin.com/checkpoint/challenge/verify"),
        ("<html><body>Too many requests</body></html>", 429, "https://www.linkedin.com/jobs/view/1"),
    ],
)
def test_detail_enrichment_stops_after_login_challenge_or_rate_limit(monkeypatch, html, status, url) -> None:
    client = BrowserSourceClient(BrowserAcquisitionConfig(source_name="linkedin"))
    requested: list[str] = []

    def fake_fetch(requested_url, **_kwargs):
        requested.append(requested_url)
        client._last_fetch_result = SimpleNamespace(final_url=url, http_status=status)
        return html

    monkeypatch.setattr(client, "fetch_html", fake_fetch)
    monkeypatch.setattr("job_intel.browser_sourcing.time.sleep", lambda _seconds: None)
    monkeypatch.setenv("JOB_INTEL_LINKEDIN_DETAIL_PAGE_BUDGET", "2")

    stats = client._enrich_linkedin_vacancies(
        [_vacancy("VP Product", 1), _vacancy("GM Market", 2)],
        observed_at="2026-09-14T10:00:00+00:00",
    )

    assert stats["planned"] == 2
    assert stats["opened"] == 1
    assert stats["filled"] == 0
    assert stats["blocked"] == 1
    assert len(requested) == 1


def test_search_linkedin_persists_detail_text_and_trace(monkeypatch) -> None:
    search_html = """
    <script type="application/ld+json">
    {"@type":"JobPosting","title":"VP Product","description":"VP Product","url":"https://www.linkedin.com/jobs/view/100","hiringOrganization":{"name":"Example"}}
    </script>
    """
    detail_html = (FIXTURES / "detail-transunion.html").read_text(encoding="utf-8")
    client = BrowserSourceClient(
        BrowserAcquisitionConfig(
            source_name="linkedin",
            min_delay_ms=0,
            max_delay_ms=0,
            scroll_pause_ms=0,
            noise_probability=0.0,
        )
    )

    def fake_fetch(url, **_kwargs):
        client._last_fetch_result = SimpleNamespace(final_url=url, http_status=200)
        return detail_html if "/jobs/view/" in url else search_html

    monkeypatch.setattr(client, "fetch_html", fake_fetch)
    monkeypatch.setattr(client, "_validate_linkedin_auth", lambda **_kwargs: "without_session")
    monkeypatch.setattr(client, "_sleep", lambda **_kwargs: None)
    monkeypatch.setenv("JOB_INTEL_LINKEDIN_DETAIL_PAGE_BUDGET", "1")
    monkeypatch.setenv("JOB_INTEL_LINKEDIN_DETAIL_PAGE_DELAY_MS", "0")

    vacancies = client.search_linkedin(
        "VP Product", max_pages=1, geography_location="United Kingdom"
    )

    assert len(vacancies) == 1
    assert len(vacancies[0].description) > 1_000
    assert client._last_search_trace["detail_pages_planned"] == 1
    assert client._last_search_trace["detail_pages_opened"] == 1
    assert client._last_search_trace["detail_pages_filled"] == 1
    assert client._last_search_trace["detail_description_median_chars"] > 1_000


def test_search_linkedin_respects_persisted_detail_skip_urls(monkeypatch) -> None:
    search_html = """
    <script type="application/ld+json">
    {"@type":"JobPosting","title":"VP Product","description":"VP Product","url":"https://www.linkedin.com/jobs/view/100","hiringOrganization":{"name":"Example"}}
    </script>
    """
    client = BrowserSourceClient(
        BrowserAcquisitionConfig(
            source_name="linkedin",
            min_delay_ms=0,
            max_delay_ms=0,
            scroll_pause_ms=0,
            noise_probability=0.0,
        )
    )

    def fake_fetch(url, **_kwargs):
        client._last_fetch_result = SimpleNamespace(final_url=url, http_status=200)
        return search_html

    monkeypatch.setattr(client, "fetch_html", fake_fetch)
    monkeypatch.setattr(client, "_validate_linkedin_auth", lambda **_kwargs: "without_session")
    monkeypatch.setattr(client, "_sleep", lambda **_kwargs: None)

    vacancies = client.search_linkedin(
        "VP Product",
        max_pages=1,
        geography_location="United Kingdom",
        detail_page_budget=1,
        detail_skip_urls={"https://www.linkedin.com/jobs/view/100"},
    )

    assert len(vacancies) == 1
    assert vacancies[0].description == "VP Product"
    assert client._last_search_trace["detail_pages_planned"] == 0
    assert client._last_search_trace["detail_pages_opened"] == 0


def test_source_kpi_persists_detail_enrichment_fields(tmp_path) -> None:
    store = JobIntelStore(tmp_path / "job-intel.sqlite3")
    store.bootstrap()
    run_id = store.start_run("daily", metadata={})
    store.upsert_source_kpi_run(
        run_id,
        "linkedin",
        {
            "detail_pages_planned": 3,
            "detail_pages_opened": 2,
            "detail_pages_filled": 1,
            "detail_pages_blocked": 1,
            "detail_description_median_chars": 4_321.5,
        },
    )
    with store.connect(read_only=True) as conn:
        row = conn.execute(
            "SELECT detail_pages_planned, detail_pages_opened, detail_pages_filled, "
            "detail_pages_blocked, detail_description_median_chars "
            "FROM source_kpi_run WHERE run_id=? AND source=?",
            (run_id, "linkedin"),
        ).fetchone()
    assert tuple(row) == (3, 2, 1, 1, 4_321.5)
