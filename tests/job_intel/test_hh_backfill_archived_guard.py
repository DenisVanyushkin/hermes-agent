"""Backfilled HH text may be rescored, but closed placements never notify."""

from job_intel.cli import _apply_text_backfill, _card_decision_plan
from job_intel.models import Evaluation, Vacancy
from job_intel.sources import hh_item_to_vacancy_filtered
from job_intel.store import JobIntelStore


def _vacancy(*, description: str = "", metadata: dict | None = None) -> Vacancy:
    return Vacancy(
        source="headhunter",
        source_id="123",
        company="Acme",
        title="Head of Product",
        location="Алматы",
        url="https://hh.ru/vacancy/123",
        description=description,
        metadata=metadata or {},
    )


def _evaluation() -> Evaluation:
    return Evaluation(
        score=95,
        tier="strong_fit",
        recommendation="potential_fit",
    )


def test_archived_vacancy_is_backfilled_but_never_notified(tmp_path):
    vacancy = _vacancy(metadata={"archived": True})
    report = _apply_text_backfill(
        [vacancy],
        fetchers={"headhunter": lambda _url: "x" * 400},
    )

    assert report.filled == 1
    assert vacancy.description == "x" * 400

    store = JobIntelStore(tmp_path / "job_intel.sqlite3")
    store.bootstrap()
    plan = _card_decision_plan(store, vacancy, _evaluation(), repost_window_days=14)
    assert plan.should_notify is False
    assert plan.suppression_reason == "hh_non_opening"


def test_advertising_placements_are_not_scored_as_openings():
    item = {
        "id": "456",
        "name": "Head of Product",
        "alternate_url": "https://hh.ru/vacancy/456",
        "employer": {"name": "Acme"},
        "area": {"name": "Алматы"},
        "vacancy_properties": [{"id": "HH_ADVERTISING"}],
    }

    assert hh_item_to_vacancy_filtered([item]) == []


def test_live_vacancy_still_notifies_normally(tmp_path):
    store = JobIntelStore(tmp_path / "job_intel.sqlite3")
    store.bootstrap()
    plan = _card_decision_plan(store, _vacancy(), _evaluation(), repost_window_days=14)
    assert plan.should_notify is True


def test_vacancy_with_unavailable_detail_is_not_notifiable(tmp_path):
    store = JobIntelStore(tmp_path / "job_intel.sqlite3")
    store.bootstrap()
    plan = _card_decision_plan(
        store,
        _vacancy(metadata={"detail_fetch_failed": True}),
        _evaluation(),
        repost_window_days=14,
    )

    assert plan.should_notify is False
    assert plan.suppression_reason == "hh_detail_unavailable"
