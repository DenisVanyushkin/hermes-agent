"""The weekly ritual rides in Sunday's evening follow-up.

The monthly goal ritual lives in the morning digest; this is its evening
counterpart, for the same reason: one message at a time she already
expects, instead of a second timer competing for the daily budget.

Two things make Sunday special. The follow-up normally stays SILENT on a
day with no outbound events -- and a Sunday often is one -- so the ritual
has to keep it speaking. And the weekly question REPLACES the usual "how
did the day go" question rather than joining it: two questions in one
message get one answer, the same failure this project fixed on the
clarify path the same morning.
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
        return self.responses.pop(0) if self.responses else "sent"


@pytest.fixture()
def fake_deliver(monkeypatch):
    fd = FakeDeliver()
    monkeypatch.setattr(gate, "deliver", fd)
    return fd


CFG = {
    "target": "whatsapp:+77782110625",
    "quiet_start": "21:30", "quiet_end": "07:30", "daily_budget": 8,
    "gate_model": "gpt-5.4-mini", "gate_provider": "openai-codex",
    "max_len_reminder": 300, "max_len_digest": 900,
    "reminder_max_age_min": 120,
}

SUNDAY_AT_FOLLOWUP = "2026-07-19T15:00:00+00:00"    # 20:00 Almaty
MONDAY_AT_FOLLOWUP = "2026-07-20T15:00:00+00:00"
TARGET_WEEK = "2026-W30"


def _followups(fd):
    return [c for c in fd.calls if c["kind"] == "followup"]


def test_an_empty_sunday_still_carries_the_whole_ritual(db, fake_deliver):
    """One rich case for the happy path: an otherwise silent Sunday
    speaks, the week's context and tails are in the message, the weekly
    question replaces the day-recap one, and raw["question"] holds that
    question ALONE -- gate re-appends it verbatim as the final line, so
    handing it the context block would print the week twice.
    """
    cal.add(db, "Тренировка", "2026-07-22T05:00:00+00:00")   # Wed of W30
    plans.add(db, "Забрать куртку", deadline="2026-07-17")
    db.commit()

    tick.reminders(db, now_utc=SUNDAY_AT_FOLLOWUP, cfg=CFG)

    calls = _followups(fake_deliver)
    assert len(calls) == 1
    text, raw = calls[0]["human_fallback"], calls[0]["raw"]
    assert "Тренировка" in text and "Забрать куртку" in text
    assert tick.FOLLOWUP_QUESTION not in text
    # With tails present the closing line also asks what to do with
    # them -- still ONE line, still one question.
    assert raw["question"].startswith("Что запланируем на неделю")
    assert "\n" not in raw["question"]
    assert text.rstrip().endswith(raw["question"])
    assert text.count(raw["question"]) == 1


@pytest.mark.parametrize("outcome,expected", [
    ("sent", ("offered", "2026-07-19")),
    ("budget", None),     # a refusal must not leave the ritual
    ("error", None),      # believing it already asked
])
def test_the_offer_is_recorded_only_on_a_real_send(db, fake_deliver,
                                                    outcome, expected):
    fake_deliver.responses = [outcome]

    tick.reminders(db, now_utc=SUNDAY_AT_FOLLOWUP, cfg=CFG)

    assert weekly.plan_state_get(db, TARGET_WEEK) == expected


@pytest.mark.parametrize("answered", ["done", "declined"])
def test_an_answered_week_makes_sunday_silent_again(db, fake_deliver,
                                                     answered):
    """Once planned, the ritual stops -- and with nothing else to say the
    follow-up returns to its ordinary silence."""
    weekly.plan_state_set(db, TARGET_WEEK, answered, "2026-07-19")
    db.commit()

    tick.reminders(db, now_utc=SUNDAY_AT_FOLLOWUP, cfg=CFG)

    assert _followups(fake_deliver) == []


def test_monday_followup_has_no_weekly_question(db, fake_deliver):
    """The ritual is Sunday's; Monday's repeat belongs to the digest."""
    tick.reminders(db, now_utc=MONDAY_AT_FOLLOWUP, cfg=CFG)

    for call in _followups(fake_deliver):
        assert "неделю" not in call["raw"]["question"].lower()
