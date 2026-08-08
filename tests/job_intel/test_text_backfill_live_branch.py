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


def test_malformed_budget_falls_back_to_default_and_does_not_raise(monkeypatch, caplog):
    """Finding 1 (fix round 1): a malformed JOB_INTEL_TEXT_BACKFILL_BUDGET must
    never crash run_daily. It must fall back to the documented default of 400
    and log the bad raw value once at warning level."""
    monkeypatch.setenv("JOB_INTEL_TEXT_BACKFILL_BUDGET", "abc")
    vacancies = [_v(url=f"https://x/{i}") for i in range(401)]
    with caplog.at_level("WARNING"):
        report = _apply_text_backfill(vacancies, fetchers={"smartrecruiters": lambda url: "y" * 400})
    # 401 eligible rows, default budget 400 -> exactly 400 attempted proves the
    # fallback landed on 400, not e.g. 0 or "all of them".
    assert report.attempted == 400
    assert any("text_backfill_budget_invalid" in record.message for record in caplog.records)


def test_empty_budget_falls_back_to_default_and_does_not_raise(monkeypatch):
    """An explicitly empty env value must also fall back to 400, not raise."""
    monkeypatch.setenv("JOB_INTEL_TEXT_BACKFILL_BUDGET", "")
    vacancies = [_v(url=f"https://x/{i}") for i in range(401)]
    report = _apply_text_backfill(vacancies, fetchers={"smartrecruiters": lambda url: "y" * 400})
    assert report.attempted == 400


def test_zero_budget_means_do_nothing(monkeypatch):
    """A budget of 0 is a legitimate, deliberate 'fetch nothing this run' --
    it must not raise and must not be silently promoted to the default."""
    monkeypatch.setenv("JOB_INTEL_TEXT_BACKFILL_BUDGET", "0")
    vacancies = [_v()]
    report = _apply_text_backfill(vacancies, fetchers={"smartrecruiters": lambda url: "y" * 400})
    assert report.attempted == 0
    assert vacancies[0].description == ""


def test_negative_budget_behaves_like_zero(monkeypatch):
    """A negative budget is a syntactically valid int (int() does not raise on
    it) and is intentionally left as-is: select() already floors it to 0 via
    candidates[:max(budget, 0)], so it behaves identically to an explicit 0."""
    monkeypatch.setenv("JOB_INTEL_TEXT_BACKFILL_BUDGET", "-5")
    vacancies = [_v()]
    report = _apply_text_backfill(vacancies, fetchers={"smartrecruiters": lambda url: "y" * 400})
    assert report.attempted == 0
    assert vacancies[0].description == ""


def test_two_vacancies_sharing_a_url_both_receive_text():
    """Finding 2 (fix round 1): global URL dedup happens later in run_daily,
    after this backfill call, so two distinct Vacancy objects can legitimately
    share (source, url) at this point. Keying results by (source, url) would
    silently drop text for one of them. Mapping results back by list index
    fixes that -- both are attempted (the duplicate fetch is a known,
    disclosed cost of the minimal fix) and both receive their text, and the
    report's counters match what actually happened to the objects."""
    calls = []

    def fetcher(url):
        calls.append(url)
        return "y" * 400

    v1 = _v(url="https://x/dup")
    v2 = _v(url="https://x/dup")
    vacancies = [v1, v2]
    report = _apply_text_backfill(vacancies, fetchers={"smartrecruiters": fetcher})
    assert v1.description == "y" * 400
    assert v2.description == "y" * 400
    assert report.attempted == 2
    assert report.filled == 2
    assert len(calls) == 2


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
