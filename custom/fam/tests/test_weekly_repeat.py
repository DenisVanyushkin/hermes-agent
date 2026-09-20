"""Slice 2: closing the ritual cycle -- the Monday repeat and the verb
that marks a week answered.

Without a way to reach done/declined the state machine would sit in
"offered" forever: the answer would create plans, but the ritual would
never know it had been answered.
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
TUESDAY = "2026-07-21"
WEEK = "2026-W30"


# --- the Monday repeat -------------------------------------------------

def test_monday_repeats_an_unanswered_offer(db):
    weekly.plan_state_set(db, WEEK, "offered", SUNDAY)
    db.commit()
    assert weekly.repeat_question(db, MONDAY) is not None


def test_monday_repeat_targets_the_week_sunday_asked_about(db):
    assert weekly.target_week(MONDAY) == WEEK


def test_no_repeat_when_the_week_was_answered(db):
    weekly.plan_state_set(db, WEEK, "done", SUNDAY)
    db.commit()
    assert weekly.repeat_question(db, MONDAY) is None


def test_no_repeat_when_the_week_was_declined(db):
    weekly.plan_state_set(db, WEEK, "declined", SUNDAY)
    db.commit()
    assert weekly.repeat_question(db, MONDAY) is None


def test_no_repeat_when_nothing_was_ever_offered(db):
    """Silence on Sunday (budget, error) must not become a Monday
    question about a week nobody was ever asked about."""
    assert weekly.repeat_question(db, MONDAY) is None


def test_no_repeat_on_tuesday(db):
    """Exactly one repeat: Monday's digest, then the week is left alone."""
    weekly.plan_state_set(db, WEEK, "offered", SUNDAY)
    db.commit()
    assert weekly.repeat_question(db, TUESDAY) is None


def test_no_repeat_on_sunday_itself(db):
    """Sunday is the offer, not the repeat."""
    weekly.plan_state_set(db, "2026-W29", "offered", "2026-07-12")
    db.commit()
    assert weekly.repeat_question(db, SUNDAY) is None


# --- marking a week answered -------------------------------------------

def test_mark_done_closes_the_cycle(db):
    weekly.plan_state_set(db, WEEK, "offered", SUNDAY)
    db.commit()
    weekly.mark(db, MONDAY, "done")
    db.commit()
    assert weekly.plan_state_get(db, WEEK)[0] == "done"


def test_mark_on_sunday_closes_the_week_just_offered(db):
    weekly.plan_state_set(db, WEEK, "offered", SUNDAY)
    db.commit()
    weekly.mark(db, SUNDAY, "declined")
    db.commit()
    assert weekly.plan_state_get(db, WEEK)[0] == "declined"


def test_mark_rejects_an_unknown_status(db):
    with pytest.raises(ValueError):
        weekly.mark(db, MONDAY, "maybe")


def test_mark_works_without_a_prior_offer(db):
    """She may start planning on her own before the ritual asks."""
    weekly.mark(db, MONDAY, "done")
    db.commit()
    assert weekly.plan_state_get(db, WEEK)[0] == "done"


def test_mark_is_audited(db):
    weekly.mark(db, MONDAY, "done")
    db.commit()
    kinds = [r[0] for r in db.execute("SELECT kind FROM audit_log")]
    assert "weekly.mark" in kinds


# --- wiring into the morning digest ------------------------------------

def test_monday_digest_carries_the_repeat(db, fake_deliver):
    weekly.plan_state_set(db, WEEK, "offered", SUNDAY)
    db.commit()

    tick.digest(db, now_utc="2026-07-20T02:30:00+00:00", cfg=CFG,
                _fetch_weather=lambda: None)

    digests = [c for c in fake_deliver.calls if c["kind"] == "digest"]
    assert "неделю" in digests[0]["raw"]["question"].lower()


def test_the_monthly_ritual_outranks_the_weekly_repeat(db, fake_deliver,
                                                       monkeypatch):
    """One planning question per message: the month wins, the week waits."""
    monkeypatch.setattr(tick, "_goal_ritual",
                        lambda *a, **k: "Готова запланировать цели на август?")
    weekly.plan_state_set(db, WEEK, "offered", SUNDAY)
    db.commit()

    tick.digest(db, now_utc="2026-07-20T02:30:00+00:00", cfg=CFG,
                _fetch_weather=lambda: None)

    digests = [c for c in fake_deliver.calls if c["kind"] == "digest"]
    assert digests[0]["raw"]["question"] == "Готова запланировать цели на август?"
