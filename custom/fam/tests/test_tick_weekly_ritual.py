"""The weekly planning ritual rides in Sunday's evening follow-up.

The monthly goal ritual lives in the morning digest; the weekly plan
ritual is its evening counterpart, and for the same reason: one message
at a time she already expects, instead of a second timer competing with
it for the daily budget.

Two things make Sunday special. The follow-up normally stays SILENT when
the day had no outbound events -- and a Sunday very often has none, so
the ritual has to keep it speaking. And the weekly question REPLACES the
usual "how did the day go" closing question rather than joining it: two
questions in one message get one answer, which is exactly the failure
this project spent the morning fixing on the clarify path.
"""
import pytest

from fam import cal, gate, plans, tick, weekly


class FakeDeliver:
    def __init__(self):
        self.calls = []
        self.responses = []

    def __call__(self, conn, kind, raw, human_fallback, cfg, force=False,
                 now_utc=None, sent_ref=None):
        self.calls.append({"kind": kind, "raw": raw,
                           "human_fallback": human_fallback})
        return self.responses.pop(0)


@pytest.fixture()
def fake_deliver(monkeypatch):
    fd = FakeDeliver()
    monkeypatch.setattr(gate, "deliver", fd)
    return fd


CFG = {
    "target": "whatsapp:+77782110625",
    "quiet_start": "21:30",
    "quiet_end": "07:30",
    "daily_budget": 8,
    "gate_model": "gpt-5.4-mini",
    "gate_provider": "openai-codex",
    "max_len_reminder": 300,
    "max_len_digest": 900,
    "reminder_max_age_min": 120,
}

# 2026-07-19 is a Sunday, 2026-07-20 the Monday after it.
SUNDAY_AT_FOLLOWUP = "2026-07-19T15:00:00+00:00"    # 20:00 Almaty
MONDAY_AT_FOLLOWUP = "2026-07-20T15:00:00+00:00"
TARGET_WEEK = "2026-W30"                            # the week starting 20 Jul


def _followups(fd):
    return [c for c in fd.calls if c["kind"] == "followup"]


def test_sunday_speaks_even_with_nothing_to_recap(db, fake_deliver):
    """An empty Sunday must not swallow the ritual."""
    fake_deliver.responses = ["sent"]

    tick.reminders(db, now_utc=SUNDAY_AT_FOLLOWUP, cfg=CFG)

    assert len(_followups(fake_deliver)) == 1


def test_sunday_message_carries_the_weekly_question(db, fake_deliver):
    fake_deliver.responses = ["sent"]

    tick.reminders(db, now_utc=SUNDAY_AT_FOLLOWUP, cfg=CFG)

    call = _followups(fake_deliver)[0]
    assert "Что запланируем на неделю?" in call["human_fallback"]
    assert call["raw"]["question"] == "Что запланируем на неделю?"


def test_weekly_question_replaces_the_usual_closing_question(db, fake_deliver):
    """One question per message -- never the day-recap one AND this one."""
    fake_deliver.responses = ["sent"]

    tick.reminders(db, now_utc=SUNDAY_AT_FOLLOWUP, cfg=CFG)

    assert tick.FOLLOWUP_QUESTION not in _followups(fake_deliver)[0]["human_fallback"]


def test_a_sent_sunday_records_the_offer(db, fake_deliver):
    fake_deliver.responses = ["sent"]

    tick.reminders(db, now_utc=SUNDAY_AT_FOLLOWUP, cfg=CFG)

    assert weekly.plan_state_get(db, TARGET_WEEK) == ("offered", "2026-07-19")


def test_a_refused_sunday_records_no_offer(db, fake_deliver):
    """Same contract as the follow-up's own meta: a budget refusal must
    not leave the ritual believing it already asked."""
    fake_deliver.responses = ["budget"]

    tick.reminders(db, now_utc=SUNDAY_AT_FOLLOWUP, cfg=CFG)

    assert weekly.plan_state_get(db, TARGET_WEEK) is None


def test_an_answered_week_makes_sunday_silent_again(db, fake_deliver):
    """Once planned, the ritual stops -- and with nothing else to say,
    the follow-up goes back to its ordinary silence."""
    weekly.plan_state_set(db, TARGET_WEEK, "done", "2026-07-19")
    db.commit()

    tick.reminders(db, now_utc=SUNDAY_AT_FOLLOWUP, cfg=CFG)

    assert _followups(fake_deliver) == []


def test_a_declined_week_makes_sunday_silent_again(db, fake_deliver):
    weekly.plan_state_set(db, TARGET_WEEK, "declined", "2026-07-19")
    db.commit()

    tick.reminders(db, now_utc=SUNDAY_AT_FOLLOWUP, cfg=CFG)

    assert _followups(fake_deliver) == []


def test_monday_followup_has_no_weekly_question(db, fake_deliver):
    """The ritual is Sunday's; Monday's repeat belongs to the digest."""
    tick.reminders(db, now_utc=MONDAY_AT_FOLLOWUP, cfg=CFG)

    for call in _followups(fake_deliver):
        assert "неделю" not in call["raw"]["question"].lower()


def test_sunday_context_names_next_weeks_events(db, fake_deliver):
    """She answers against the real week, not into the void."""
    fake_deliver.responses = ["sent"]
    cal.add(db, "Тренировка", "2026-07-22T05:00:00+00:00")   # Wed of W30
    db.commit()

    tick.reminders(db, now_utc=SUNDAY_AT_FOLLOWUP, cfg=CFG)

    assert "Тренировка" in _followups(fake_deliver)[0]["human_fallback"]


def test_sunday_context_names_the_tails(db, fake_deliver):
    fake_deliver.responses = ["sent"]
    plans.add(db, "Забрать куртку", deadline="2026-07-17")   # before W30
    db.commit()

    tick.reminders(db, now_utc=SUNDAY_AT_FOLLOWUP, cfg=CFG)

    assert "Забрать куртку" in _followups(fake_deliver)[0]["human_fallback"]


def test_raw_question_is_only_the_closing_line(db, fake_deliver):
    """gate._ensure_trailing_question re-appends raw["question"] verbatim
    as the message's last line, and on the shorten path strips it off,
    compresses the rest and glues it back. Putting the whole context
    block in there would make the gate repeat the week summary a second
    time -- a fake deliver never shows it, because it hands back
    human_fallback untouched.
    """
    fake_deliver.responses = ["sent"]

    tick.reminders(db, now_utc=SUNDAY_AT_FOLLOWUP, cfg=CFG)

    question = _followups(fake_deliver)[0]["raw"]["question"]
    assert question == "Что запланируем на неделю?"
    assert "\n" not in question


def test_human_fallback_ends_with_that_same_question_exactly_once(db,
                                                                   fake_deliver):
    """The fallback path sends human_fallback as-is, so it must already
    end the way gate._ensure_trailing_question forces the rewrite path to
    end -- and contain the question only once, or the gate re-appending
    it would print the block twice."""
    fake_deliver.responses = ["sent"]

    tick.reminders(db, now_utc=SUNDAY_AT_FOLLOWUP, cfg=CFG)

    call = _followups(fake_deliver)[0]
    assert call["human_fallback"].rstrip().endswith(call["raw"]["question"])
    assert call["human_fallback"].count(call["raw"]["question"]) == 1
