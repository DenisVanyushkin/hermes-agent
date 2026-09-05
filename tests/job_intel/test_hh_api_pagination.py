"""Pagination must stop at hh's hard 2000-item search cap."""
from job_intel import hh_api


def test_never_requests_beyond_the_depth_cap(monkeypatch):
    pages = []

    def _fake(**params):
        pages.append(params["page"])
        return {
            "found": 50000,
            "pages": 500,
            "per_page": 100,
            "page": params["page"],
            "items": [{"id": str(params["page"])}] * 100,
            "arguments": [],
        }

    monkeypatch.setattr(hh_api, "search_vacancies", _fake)
    list(hh_api.iter_search_results(text="x"))

    assert max(pages) == 19


def test_reports_truncation_so_it_is_never_silent(monkeypatch):
    monkeypatch.setattr(
        hh_api,
        "search_vacancies",
        lambda **params: {
            "found": 50000,
            "pages": 500,
            "per_page": 100,
            "page": params["page"],
            "items": [{"id": "x"}] * 100,
            "arguments": [],
        },
    )

    result = hh_api.collect_search_results(text="x")

    assert result.truncated is True
    assert result.found == 50000
    assert len(result.items) == 2000


def test_stops_early_when_a_page_is_short(monkeypatch):
    def _fake(**params):
        n = 100 if params["page"] == 0 else 7
        return {
            "found": 107,
            "pages": 2,
            "per_page": 100,
            "page": params["page"],
            "items": [{"id": "x"}] * n,
            "arguments": [],
        }

    monkeypatch.setattr(hh_api, "search_vacancies", _fake)
