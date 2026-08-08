"""Orchestration: a backfill failure is never a run failure."""
import pytest

from job_intel.text_backfill import backfill


def _row(title="Head of Product", source="smartrecruiters", url="https://x/1"):
    return {"source": source, "title": title, "description": "", "url": url}


def test_successful_fetch_is_reported_as_ok():
    report = backfill([_row()], budget=10,
                      fetchers={"smartrecruiters": lambda url: "y" * 400})
    assert report.attempted == 1
    assert report.filled == 1
    assert report.results[0].state == "ok"
    assert report.results[0].text == "y" * 400


def test_a_raising_fetcher_is_contained():
    def boom(url):
        raise RuntimeError("upstream on fire")

    report = backfill([_row()], budget=10, fetchers={"smartrecruiters": boom})
    assert report.failed == 1
    assert report.filled == 0
    assert report.results[0].state == "failed"
    assert report.results[0].text is None


def test_a_fetcher_returning_none_is_unavailable_not_failed():
    report = backfill([_row()], budget=10, fetchers={"smartrecruiters": lambda url: None})
    assert report.unavailable == 1
    assert report.results[0].state == "unavailable"


def test_text_below_the_usable_threshold_is_unavailable():
    """A detail response that is still too short did not solve the problem."""
    report = backfill([_row()], budget=10, fetchers={"smartrecruiters": lambda url: "short"})
    assert report.unavailable == 1


def test_budget_leaves_the_remainder_untouched():
    rows = [_row(url=f"https://x/{i}") for i in range(5)]
    report = backfill(rows, budget=2, fetchers={"smartrecruiters": lambda url: "y" * 400})
    assert report.attempted == 2
    assert report.skipped_budget == 3
    assert len(report.results) == 2


def test_counts_are_reported_per_source():
    rows = [_row(url="https://x/1"),
            _row(source="headhunter", url="https://hh.ru/vacancy/1")]
    report = backfill(rows, budget=10, fetchers={
        "smartrecruiters": lambda url: "y" * 400,
        "headhunter": lambda url: None,
    })
    assert report.per_source["smartrecruiters"]["filled"] == 1
    assert report.per_source["headhunter"]["unavailable"] == 1


def test_a_source_without_a_registered_fetcher_is_skipped_not_crashed():
    report = backfill([_row(source="teamtailor", url="https://t/1")], budget=10,
                      fetchers={"smartrecruiters": lambda url: "y" * 400})
    assert report.attempted == 0


def test_a_raising_fetcher_does_not_abort_remaining_rows():
    """A raise on the FIRST row must not stop the loop over the rest.

    A single-row test (test_a_raising_fetcher_is_contained) cannot tell a
    correct `except Exception: ...; continue` apart from a buggy
    `except Exception: ...; return report` -- both look identical with one
    row. This test puts the raise on the first row specifically, so an early
    return would swallow rows 2 and 3 and this test would catch it. All rows
    share the same title ("Head of Product", priority bucket 0 under
    _priority) so select()'s stable sort does not reorder them -- the first
    row in, with a raising fetcher, stays the first row attempted.
    """
    rows = [_row(url="https://x/1"), _row(url="https://x/2"), _row(url="https://x/3")]

    def selective_boom(url):
        if url == "https://x/1":
            raise RuntimeError("upstream on fire")
        return "y" * 400

    report = backfill(rows, budget=10, fetchers={"smartrecruiters": selective_boom})

    assert report.attempted == 3
    assert report.failed == 1
    assert report.filled == 2
    assert [r.state for r in report.results] == ["failed", "ok", "ok"]
