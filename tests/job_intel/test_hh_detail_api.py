"""HeadHunter text backfill uses the authenticated official API."""
from job_intel import ats_sources, hh_api
from job_intel.ats_sources import (
    DETAIL_RATE_LIMITED,
    DETAIL_TRANSIENT,
    fetch_headhunter_detail,
)


def test_headhunter_detail_uses_the_api(monkeypatch):
    monkeypatch.setattr(
        ats_sources.hh_api,
        "fetch_vacancy_detail",
        lambda vacancy_id: {"description": "<p>Вы будете отвечать за P&amp;L.</p>"},
    )

    text = fetch_headhunter_detail("https://hh.ru/vacancy/133446873")

    assert "отвечать за P&L" in text
    assert "<p>" not in text


def test_404_is_permanent_not_transient(monkeypatch):
    monkeypatch.setattr(
        ats_sources.hh_api,
        "fetch_vacancy_detail",
        lambda vacancy_id: (_ for _ in ()).throw(hh_api.HHNotFound()),
    )

    assert fetch_headhunter_detail("https://hh.ru/vacancy/1") is None


def test_rate_limited_maps_to_the_rate_limited_signal(monkeypatch):
    monkeypatch.setattr(
        ats_sources.hh_api,
        "fetch_vacancy_detail",
        lambda vacancy_id: (_ for _ in ()).throw(hh_api.HHRateLimited()),
    )

    assert fetch_headhunter_detail("https://hh.ru/vacancy/1") is DETAIL_RATE_LIMITED


def test_auth_failure_is_transient_not_permanent(monkeypatch):
    monkeypatch.setattr(
        ats_sources.hh_api,
        "fetch_vacancy_detail",
        lambda vacancy_id: (_ for _ in ()).throw(hh_api.HHAuthError()),
    )

    assert fetch_headhunter_detail("https://hh.ru/vacancy/1") is DETAIL_TRANSIENT


def test_unaddressable_url_returns_none_without_a_request(monkeypatch):
    monkeypatch.setattr(
        ats_sources.hh_api,
        "fetch_vacancy_detail",
        lambda vacancy_id: (_ for _ in ()).throw(AssertionError("must not request")),
    )

    assert fetch_headhunter_detail("https://hh.ru/employer/1234") is None
