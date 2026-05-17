from __future__ import annotations

from job_intel.sources import extract_duckduckgo_destination_url, normalize_search_hit, normalize_remoteok_job, normalize_remotive_job, search_remoteok_jobs, SearchHit


def test_extract_duckduckgo_destination_url_unwraps_redirect() -> None:
    redirected = "https://duckduckgo.com/l/?uddg=https%3A%2F%2Fremoteok.com%2Fremote-jobs%2Fabc"
    assert extract_duckduckgo_destination_url(redirected) == "https://remoteok.com/remote-jobs/abc"


def test_normalize_search_hit_infers_remoteok_company_and_title() -> None:
    hit = SearchHit(
        title="Remote VP Product at Spark Advisors - RemoteOK",
        url="https://duckduckgo.com/l/?uddg=https%3A%2F%2Fremoteok.com%2Fremote-jobs%2Fspark-advisors-remote-vp-product",
        snippet="",
        source="duckduckgo",
    )

    vacancy = normalize_search_hit(hit)

    assert vacancy.company == "Spark Advisors"
    assert vacancy.title == "Remote VP Product"
    assert vacancy.url == "https://remoteok.com/remote-jobs/spark-advisors-remote-vp-product"



def test_normalize_search_hit_does_not_infer_bogus_linkedin_company() -> None:
    hit = SearchHit(
        title="VP Product",
        url="https://www.linkedin.com/jobs/view/123",
        snippet="Senior product leadership role",
        source="duckduckgo",
    )

    vacancy = normalize_search_hit(hit)

    assert vacancy.company == "Unknown"
    assert vacancy.title == "VP Product"
    assert vacancy.url == "https://www.linkedin.com/jobs/view/123"


def test_normalize_remoteok_job_maps_fields() -> None:
    vacancy = normalize_remoteok_job(
        {
            "id": 123,
            "company": "Acme",
            "position": "VP Product",
            "location": "Remote",
            "url": "https://remoteok.com/remote-jobs/acme-vp-product",
            "description": "<p>Own monetization</p>",
            "date": "2026-05-16",
            "salary_min": 200000,
            "salary_max": 300000,
            "tags": ["product", "monetization"],
        }
    )

    assert vacancy.source == "remoteok"
    assert vacancy.company == "Acme"
    assert vacancy.title == "VP Product"
    assert vacancy.salary == "200000 300000"
    assert vacancy.metadata["tags"] == ["product", "monetization"]


def test_normalize_remotive_job_maps_fields() -> None:
    vacancy = normalize_remotive_job(
        {
            "id": 456,
            "company_name": "Acme",
            "title": "Director of Product",
            "candidate_required_location": "Worldwide",
            "url": "https://remotive.com/remote-jobs/director-of-product",
            "description": "<div>Lead monetization</div>",
            "publication_date": "2026-05-16",
            "salary": "$200k-$250k",
            "tags": ["product"],
            "category": "software-development",
        }
    )

    assert vacancy.source == "remotive"
    assert vacancy.company == "Acme"
    assert vacancy.title == "Director of Product"
    assert vacancy.location == "Worldwide"
    assert vacancy.salary == "$200k-$250k"
    assert vacancy.metadata["category"] == "software-development"


def test_search_remoteok_jobs_filters_low_signal_roles(monkeypatch) -> None:
    class Response:
        status_code = 200
        def raise_for_status(self):
            return None
        def json(self):
            return [
                {},
                {"id": 1, "company": "Acme", "position": "VP Product", "location": "Remote", "url": "https://remoteok.com/a", "description": "Own monetization", "tags": ["product"]},
                {"id": 2, "company": "Acme", "position": "Support Specialist", "location": "Remote", "url": "https://remoteok.com/b", "description": "Help customers", "tags": ["support"]},
            ]

    monkeypatch.setattr("job_intel.sources.requests.get", lambda *args, **kwargs: Response())
    vacancies = search_remoteok_jobs(max_results=10)
    assert len(vacancies) == 1
    assert vacancies[0].title == "VP Product"
