"""Backfilled text must not resend a card that was already delivered."""
import pytest

from job_intel.cli import _card_decision_plan, _card_key_for_vacancy, _notification_payload
from job_intel.models import Vacancy
from job_intel.store import JobIntelStore


@pytest.fixture()
def store(tmp_path):
    s = JobIntelStore(str(tmp_path / "t.sqlite3"))
    s.bootstrap()
    return s


class _Eval:
    score = 80
    recommendation = "apply"
    tier = "strong_fit"
    # format_vacancy_summary() (called by _notification_payload() to build the
    # summary_hash) reads these two attrs unconditionally. The brief's
    # original stub omitted them because it never exercised that path — the
    # "sent" control test below does, via _notification_payload(), so they're
    # needed here.
    matched_signals: list[str] = []
    concerns: list[str] = []


def _v(description):
    return Vacancy(source="smartrecruiters", source_id="a", company="Acme",
                   title="Head of Product", location="Remote",
                   url="https://x/1", description=description)


def test_a_never_delivered_vacancy_is_notified(store):
    plan = _card_decision_plan(store, _v("y" * 400), _Eval(), repost_window_days=14)
    assert plan.should_notify is True


# Brief's original scaffold called store.upsert_vacancy(run_id, vacancy) —
# that signature doesn't exist. upsert_vacancy() takes (vacancy, vacancy_key),
# not a run id, and the vacancies table has no run_id column at all. start_run()
# is kept only because create_notification() genuinely needs a run_id. Same
# pattern already established in tests/job_intel/test_text_backfill_store.py.
def test_a_failed_delivery_does_not_count_as_delivered(store):
    """183 notifications have delivery_status='failed' in production. They never
    reached the user and must stay re-deliverable.

    The brief's original version of this test created the notification row
    without a card_key, so latest_notification_for_card() could never match
    it regardless of delivery_status — should_notify came back True via the
    "never delivered" early-return, not because the failed-delivery guard
    was exercised. This version sets card_key so the row is a genuine
    candidate, then proves with the store's own lookup that delivery_status
    is what excludes it, not the row's absence.
    """
    run_id = store.start_run("test")
    vacancy = _v("y" * 400)
    vacancy_id = store.upsert_vacancy(vacancy, vacancy.url)
    card_key = _card_key_for_vacancy(vacancy)
    payload = _notification_payload(vacancy, _Eval(), vacancy_id)
    nid = store.create_notification(run_id, "C1", "vacancy_card", "body",
                                    card_key=card_key, payload=payload,
                                    delivery_status="pending")
    store.mark_notification_delivery(nid, "failed", attempts=1,
                                     delivery_error="boom")

    # Proof the row is a genuine candidate: it exists, under this card_key,
    # as a vacancy_card message — dropping the delivery_status filter finds it.
    unfiltered = store.latest_notification_for_card(
        card_key, message_types=("vacancy_card", "vacancy_opportunity", "daily_digest"))
    assert unfiltered is not None
    assert unfiltered["id"] == nid
    assert unfiltered["delivery_status"] == "failed"

    # Proof it is excluded specifically because delivery_status != "sent",
    # not because the row can't be found at all.
    sent_only = store.latest_notification_for_card(
        card_key, delivery_status="sent",
        message_types=("vacancy_card", "vacancy_opportunity", "daily_digest"))
    assert sent_only is None

    plan = _card_decision_plan(store, vacancy, _Eval(), repost_window_days=14)
    assert plan.should_notify is True


def test_a_sent_delivery_with_unchanged_content_suppresses_renotification(store):
    """Discrimination control for the test above: same setup, only the
    terminal delivery_status differs (sent instead of failed), with a payload
    that matches the vacancy/evaluation being re-evaluated (so there is no
    material change to force a resend). If flipping failed -> sent didn't
    flip should_notify, the lookup above would not be proving what it claims.
    """
    run_id = store.start_run("test")
    vacancy = _v("y" * 400)
    evaluation = _Eval()
    vacancy_id = store.upsert_vacancy(vacancy, vacancy.url)
    card_key = _card_key_for_vacancy(vacancy)
    payload = _notification_payload(vacancy, evaluation, vacancy_id)
    nid = store.create_notification(run_id, "C1", "vacancy_card", "body",
                                    card_key=card_key, payload=payload,
                                    delivery_status="pending")
    store.mark_notification_delivery(nid, "sent", attempts=1)

    sent_only = store.latest_notification_for_card(
        card_key, delivery_status="sent",
        message_types=("vacancy_card", "vacancy_opportunity", "daily_digest"))
    assert sent_only is not None
    assert sent_only["id"] == nid

    plan = _card_decision_plan(store, vacancy, evaluation, repost_window_days=14)
    assert plan.should_notify is False
    assert plan.decision == "suppressed"
    assert plan.suppression_reason == "already_sent_cooldown_active"
