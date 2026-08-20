"""HeadHunter acquisition uses the API and fetches detail inline."""
import pytest

from job_intel import sources


def _search_result(item):
    return sources.hh_api.SearchResult(items=[item], found=1, truncated=False)


def test_fetches_detail_for_each_vacancy(monkeypatch):
    item = {
        "id": "1",
        "name": "Head of Product",
        "alternate_url": "https://hh.ru/vacancy/1",
        "employer": {"name": "Acme"},
        "area": {"name": "Алматы"},
        "archived": False,
    }
    monkeypatch.setenv("JOB_INTEL_HEADHUNTER_MAX_ITEMS", "75")
    seen_params = {}

    def _collect(**params):
        seen_params.update(params)
        return _search_result(item)

    monkeypatch.setattr(sources.hh_api, "collect_search_results", _collect)
    seen = []
    monkeypatch.setattr(
        sources.hh_api,
        "fetch_vacancy_detail",
        lambda vacancy_id: seen.append(vacancy_id) or {"description": "<p>" + "x" * 300 + "</p>"},
    )

    result = sources.fetch_headhunter_vacancies("Head of Product")

    assert seen == ["1"]
    assert len(result) == 1
    assert len(result[0].description) > 200
    assert seen_params["max_items"] == 75


def test_a_failed_detail_never_loses_the_vacancy(monkeypatch):
    item = {
        "id": "1",
        "name": "Head of Product",
        "alternate_url": "https://hh.ru/vacancy/1",
        "employer": {"name": "Acme"},
        "area": {"name": "Алматы"},
        "archived": False,
    }
    monkeypatch.setattr(sources.hh_api, "collect_search_results", lambda **params: _search_result(item))
    monkeypatch.setattr(
        sources.hh_api,
        "fetch_vacancy_detail",
        lambda vacancy_id: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    result = sources.fetch_headhunter_vacancies("Head of Product")

    assert len(result) == 1
    assert result[0].description == ""
    assert result[0].metadata["detail_fetch_failed"] is True


def test_rate_limit_latch_counts_each_skipped_detail_failure(monkeypatch):
    items = [
        {"id": "1", "name": "Head of Product", "alternate_url": "https://hh.ru/vacancy/1"},
        {"id": "2", "name": "VP Product", "alternate_url": "https://hh.ru/vacancy/2"},
    ]
    monkeypatch.setattr(
        sources.hh_api,
        "collect_search_results",
        lambda **params: sources.hh_api.SearchResult(items=items, found=2, truncated=False),
    )
    monkeypatch.setattr(
        sources.hh_api,
        "fetch_vacancy_detail",
        lambda vacancy_id: (_ for _ in ()).throw(sources.hh_api.HHRateLimited()),
    )

    result = sources.fetch_headhunter_vacancies("Head of Product")

    assert len(result) == 2
    assert sources.fetch_headhunter_vacancies.last_trace["detail_failures"] == 2


def test_rate_limit_stops_the_source_without_raising_raw_api_error(monkeypatch):
    monkeypatch.setattr(
        sources.hh_api,
        "collect_search_results",
        lambda **params: (_ for _ in ()).throw(sources.hh_api.HHRateLimited()),
    )

    with pytest.raises(sources.SourceFetchError):
        sources.fetch_headhunter_vacancies("Head of Product")


def test_reports_truncation_into_last_trace(monkeypatch):
    monkeypatch.setattr(
        sources.hh_api,
        "collect_search_results",
        lambda **params: sources.hh_api.SearchResult(items=[], found=50000, truncated=True),
    )

    sources.fetch_headhunter_vacancies("Head of Product")

    assert sources.fetch_headhunter_vacancies.last_trace["truncated"] is True


def test_headhunter_acquisition_never_touches_the_browser(monkeypatch):
    def _boom(*args, **kwargs):
        raise AssertionError("browser worker invoked on the API path")

    monkeypatch.setattr(sources, "_browser_worker_payload", _boom)
    monkeypatch.setattr(
        sources.hh_api,
        "collect_search_results",
        lambda **params: sources.hh_api.SearchResult(items=[], found=0, truncated=False),
    )

    assert sources.fetch_headhunter_vacancies("Head of Product") == []
