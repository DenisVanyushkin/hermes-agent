"""The live branch must run BEFORE classification, or a weak title stays a verdict."""
import pytest

from job_intel.cli import _apply_text_backfill
from job_intel.models import Vacancy


def _v(title="Head of Product", source="smartrecruiters", description="",
       url="https://x/1"):
    return Vacancy(source=source, source_id="a", company="Acme", title=title,
                   location="Remote", url=url, description=description)


def test_fetched_text_lands_on_the_vacancy():
    vacancies = [_v()]
    _apply_text_backfill(vacancies, fetchers={"smartrecruiters": lambda url: "y" * 400})
    assert vacancies[0].description == "y" * 400


def test_a_vacancy_with_text_is_left_alone():
    vacancies = [_v(description="z" * 400)]
    _apply_text_backfill(vacancies, fetchers={"smartrecruiters": lambda url: "y" * 400})
    assert vacancies[0].description == "z" * 400


def test_a_raising_fetcher_does_not_break_collection():
    def boom(url):
        raise RuntimeError("upstream on fire")

    vacancies = [_v()]
    report = _apply_text_backfill(vacancies, fetchers={"smartrecruiters": boom})
    assert vacancies[0].description == ""
    assert report.failed == 1


def test_the_flag_disables_the_branch(monkeypatch):
    monkeypatch.setenv("JOB_INTEL_TEXT_BACKFILL_ENABLED", "0")
    vacancies = [_v()]
    report = _apply_text_backfill(vacancies,
                                  fetchers={"smartrecruiters": lambda url: "y" * 400})
    assert vacancies[0].description == ""
    assert report.attempted == 0


def test_backfilled_text_changes_the_classification():
    """The point of the ordering: a title that is not exec-detected on its own
    becomes exec-detected once its text arrives.

    NOTE: the brief's original fixture used title="Product Lead (Афиша)", but
    that title alone already matches the `product_lead` pattern in
    evaluator._ROLE_CLASSIFICATION_PATTERNS (`\\bproduct lead\\b`), so
    executive_detected is True *before* any backfill -- the test would have
    passed regardless of ordering, which defeats its purpose. Verified
    empirically: classify_vacancy() on that title alone returns
    executive_detected=True. Swapped to "Product Manager (Афиша)", which
    matches only the `generic_product_manager` pattern (is_leadership_signal
    False -> executive_detected=False) until the injected description text
    ("...define the product strategy...") matches the earlier-checked
    `product_strategy_lead` pattern via the description, flipping
    executive_detected to True. Confirmed before=False, after=True."""
    from job_intel.evaluator import classify_vacancy

    v = _v(title="Product Manager (Афиша)")
    before = classify_vacancy(v).get("executive_detected")
    _apply_text_backfill([v], fetchers={"smartrecruiters": lambda url:
                                        "You will own the P&L for the business line "
                                        "and define the product strategy. " + "x" * 200})
    after = classify_vacancy(v).get("executive_detected")
    assert (before, after) == (False, True)
