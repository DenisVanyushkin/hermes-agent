"""Closing the ritual cycle: the Monday repeat and the verb that marks a
week answered.

Without a way to reach done/declined the state machine would sit in
"offered" forever -- the answer creating plans while the ritual went on
believing it had never been answered.
"""
import pytest

from fam import gate, tick, weekly


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

# 2026-07-19 Sunday, 2026-07-20 Monday, 2026-07-21 Tuesday -> week W30.
SUNDAY = "2026-07-19"
MONDAY = "2026-07-20"
WEEK = "2026-W30"


# --- the Monday repeat -------------------------------------------------

def test_monday_repeats_an_unanswered_offer(db):
    weekly.plan_state_set(db, WEEK, "offered", SUNDAY)
    db.commit()

    assert weekly.repeat_question(db, MONDAY) is not None


@pytest.mark.parametrize("answered", ["done", "declined"])
def test_no_repeat_once_the_week_was_answered(db, answered):
    weekly.plan_state_set(db, WEEK, answered, SUNDAY)
    db.commit()

    assert weekly.repeat_question(db, MONDAY) is None


def test_no_repeat_when_nothing_was_ever_offered(db):
    """Silence on Sunday (budget, error) must not become a Monday
    question about a week nobody was ever asked about."""
    assert weekly.repeat_question(db, MONDAY) is None


@pytest.mark.parametrize("day", [
    "2026-07-21",   # Tuesday: at most ONE repeat, no daily nagging
    SUNDAY,         # Sunday is the offer itself, not the repeat
])
def test_the_repeat_happens_on_monday_only(db, day):
    weekly.plan_state_set(db, WEEK, "offered", SUNDAY)
    db.commit()

    assert weekly.repeat_question(db, day) is None


# --- marking a week answered -------------------------------------------

@pytest.mark.parametrize("status", ["done", "declined"])
def test_mark_closes_the_week_that_was_actually_offered(db, status):
    """The week comes from the OPEN cycle, not from today: an answer can
    arrive long after the question. Replying to Monday's question on the
    following Sunday would otherwise close the NEXT week -- silencing it
    before it was ever offered -- and leave W30 open forever.
    """
    weekly.plan_state_set(db, "2026-W29", "done", "2026-07-12")
    weekly.plan_state_set(db, WEEK, "offered", SUNDAY)
    db.commit()

    weekly.mark(db, "2026-07-26", status)      # the NEXT Sunday
    db.commit()

    assert weekly.plan_state_get(db, WEEK)[0] == status
    assert weekly.plan_state_get(db, "2026-W31") is None


def test_mark_falls_back_to_today_when_nothing_is_open(db):
    """No open cycle -> she is planning on her own initiative, and
    "today" is the only sensible target."""
    weekly.mark(db, MONDAY, "done")
    db.commit()

    assert weekly.plan_state_get(db, WEEK)[0] == "done"
    assert "weekly.mark" in [r[0] for r in
                             db.execute("SELECT kind FROM audit_log")]


def test_mark_rejects_an_unknown_status(db):
    with pytest.raises(ValueError):
        weekly.mark(db, MONDAY, "maybe")


def test_recording_an_offer_never_reopens_an_answered_week(db):
    """The send path reads the state, then makes a slow LLM call before
    writing "offered". If she answers inside that window, the write must
    not reopen the week she just closed."""
    weekly.plan_state_set(db, WEEK, "done", SUNDAY)
    db.commit()

    assert weekly.record_offer(db, WEEK, SUNDAY) is False
    assert weekly.plan_state_get(db, WEEK)[0] == "done"


def test_recording_an_offer_on_a_fresh_week_works(db):
    assert weekly.record_offer(db, WEEK, SUNDAY) is True
    assert weekly.plan_state_get(db, WEEK) == ("offered", SUNDAY)


# --- wiring into the morning digest ------------------------------------

def test_monday_digest_carries_the_repeat(db, fake_deliver):
    weekly.plan_state_set(db, WEEK, "offered", SUNDAY)
    db.commit()

    tick.digest(db, now_utc="2026-07-20T02:30:00+00:00", cfg=CFG,
                _fetch_weather=lambda: None)

    digests = [c for c in fake_deliver.calls if c["kind"] == "digest"]
    assert "неделю" in digests[0]["raw"]["question"].lower()


def test_the_monthly_ritual_drops_the_weekly_repeat(db, fake_deliver,
                                                    monkeypatch):
    """One planning question per message: the month wins and the weekly
    repeat is DROPPED, not deferred -- Tuesday is not eligible and next
    Sunday targets a different week. Accepted: the repeat is a courtesy,
    not a guarantee (see weekly.repeat_question)."""
    monkeypatch.setattr(tick, "_goal_ritual",
                        lambda *a, **k: "Готова запланировать цели на август?")
    weekly.plan_state_set(db, WEEK, "offered", SUNDAY)
    db.commit()

    tick.digest(db, now_utc="2026-07-20T02:30:00+00:00", cfg=CFG,
                _fetch_weather=lambda: None)

    digests = [c for c in fake_deliver.calls if c["kind"] == "digest"]
    assert digests[0]["raw"]["question"] == "Готова запланировать цели на август?"
