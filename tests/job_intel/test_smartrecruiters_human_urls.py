from types import SimpleNamespace

from job_intel.ats_sources import fetch_smartrecruiters
from job_intel.cli import _apply_text_backfill
from job_intel.models import Vacancy
from job_intel.text_backfill import backfill


def _response(payload):
    return SimpleNamespace(
        status_code=200,
        content=b"{}",
        headers={},
        json=lambda: payload,
    )


def test_smartrecruiters_listing_uses_human_url_and_preserves_api_detail_url(monkeypatch):
    api_url = "https://api.smartrecruiters.com/v1/companies/wise/postings/744"
    responses = iter([
        _response({
            "content": [{
                "id": "744",
                "name": "Head of Product",
                "ref": api_url,
            }],
            "totalFound": 1,
            "offset": 0,
            "limit": 100,
        }),
    ])
    monkeypatch.setattr("job_intel.ats_sources._http_get", lambda *args, **kwargs: next(responses))

    result = fetch_smartrecruiters([], companies=["wise"])

    assert len(result.vacancies) == 1
    vacancy = result.vacancies[0]
    assert vacancy.url == "https://jobs.smartrecruiters.com/wise/744"
    assert vacancy.metadata["detail_api_url"] == api_url


def test_smartrecruiters_listing_without_id_is_skipped_with_error(monkeypatch):
    api_url = "https://api.smartrecruiters.com/v1/companies/wise/postings/missing"
    monkeypatch.setattr(
        "job_intel.ats_sources._http_get",
        lambda *args, **kwargs: _response({
            "content": [{"name": "Head of Product", "ref": api_url}],
            "totalFound": 1,
            "offset": 0,
            "limit": 100,
        }),
    )

    result = fetch_smartrecruiters([], companies=["wise"])

    assert result.vacancies == []
    assert any("missing_id" in error for error in result.errors)


def test_backfill_uses_smartrecruiters_api_url_from_metadata():
    api_url = "https://api.smartrecruiters.com/v1/companies/wise/postings/744"
    seen = []
    row = {
        "source": "smartrecruiters",
        "title": "Head of Product",
        "description": "",
        "url": "https://jobs.smartrecruiters.com/wise/744",
        "metadata": {"detail_api_url": api_url},
    }

    report = backfill(
        [row],
        budget=1,
        fetchers={"smartrecruiters": lambda url: seen.append(url) or "x" * 400},
    )

    assert report.filled == 1
    assert seen == [api_url]


def test_backfill_falls_back_to_row_url_without_smartrecruiters_metadata():
    human_url = "https://jobs.smartrecruiters.com/wise/744"
    seen = []
    row = {
        "source": "smartrecruiters",
        "title": "Head of Product",
        "description": "",
        "url": human_url,
    }

    report = backfill(
        [row],
        budget=1,
        fetchers={"smartrecruiters": lambda url: seen.append(url) or "x" * 400},
    )

    assert report.filled == 1
    assert seen == [human_url]


def test_live_backfill_passes_vacancy_metadata_to_backfill():
    api_url = "https://api.smartrecruiters.com/v1/companies/wise/postings/744"
    seen = []
    vacancy = Vacancy(
        source="smartrecruiters",
        source_id="wise-744",
        company="wise",
        title="Head of Product",
        location="Remote",
        url="https://jobs.smartrecruiters.com/wise/744",
        description="",
        metadata={"detail_api_url": api_url},
    )

    report = _apply_text_backfill(
        [vacancy],
        fetchers={"smartrecruiters": lambda url: seen.append(url) or "x" * 400},
    )

    assert report.filled == 1
    assert seen == [api_url]
