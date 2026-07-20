"""Phase 8b (goals) Task 6: planning-ritual question in the morning
digest. Harness mirrors test_digest_goals.py's fixtures (CFG/NOW_FOR/
_fetch_wx/fake_deliver/db).
"""
import pytest

from fam import cal, db as dbmod
from fam import gate, goals, tick

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
    "plan_deadline_horizon_days": 3,
    "goal_ritual_window_days": 3,
    "goal_digest_intervals": [4, 2, 1],
}

# Almaty offset is UTC+5 -- 04:30 UTC is always 09:30 Almaty the same
# calendar date, so NOW_FOR(day) below always lands "today" on `day`.


def NOW_FOR(year, month, day):
    return f"{year:04d}-{month:02d}-{day:02d}T04:30:00+00:00"


def _fetch_wx(wx=None):
    return lambda: wx


class FakeDeliver:
    def __init__(self):
        self.calls = []
        self.responses = []

    def __call__(self, conn, kind, raw, human_fallback, cfg, force=False,
                 now_utc=None):
        self.calls.append({
            "kind": kind, "raw": raw, "human_fallback": human_fallback,
            "force": force, "now_utc": now_utc,
        })
        return self.responses.pop(0)


@pytest.fixture()
def fake_deliver(monkeypatch):
    fd = FakeDeliver()
    monkeypatch.setattr(gate, "deliver", fd)
    return fd


# July 2026 has 31 days; window=3 -> window days are 29, 30, 31.


# ---- quiet-day selection / tie ----

def test_quiet_day_is_least_busy_remaining_window_day(db, fake_deliver):
    # Window days 29/30/31, called on 29 (window start, all 3 still
    # "remaining"). 1 event on 29, 0 on 30, 2 on 31 -> quiet day = 30.
    # today (29) is NOT the quiet day -> no ritual question yet.
    cal.add(db, "Врач", "2026-07-29T06:00:00+00:00")
    cal.add(db, "Кино", "2026-07-31T06:00:00+00:00")
    cal.add(db, "Ужин", "2026-07-31T18:00:00+00:00")
    db.commit()

    fake_deliver.responses = ["sent"]
    tick.digest(db, now_utc=NOW_FOR(2026, 7, 29), cfg=CFG, _fetch_weather=_fetch_wx())
    assert fake_deliver.calls[0]["raw"]["question"] == tick.DIGEST_QUESTION

    # Called again the next tick day (30) -- now 30 IS the quiet day.
    fake_deliver.calls.clear()
    fake_deliver.responses = ["sent"]
    tick.digest(db, now_utc=NOW_FOR(2026, 7, 30), cfg=CFG, _fetch_weather=_fetch_wx())
    assert fake_deliver.calls[0]["raw"]["question"] == goals.ritual_question_text("2026-08")


def test_quiet_day_tie_breaks_to_earlier_day(db, fake_deliver):
    # No events at all on 29/30/31 -> all tie at 0 -> earliest (29) wins.
    fake_deliver.responses = ["sent"]
    tick.digest(db, now_utc=NOW_FOR(2026, 7, 29), cfg=CFG, _fetch_weather=_fetch_wx())
    q = fake_deliver.calls[0]["raw"]["question"]
    assert q == goals.ritual_question_text("2026-08")


def test_quiet_day_not_today_stays_silent(db, fake_deliver):
    # Event on 29 (today), none on 30/31 -> quiet day is 30 or 31, not
    # today -> no ritual question today, DIGEST_QUESTION stands.
    cal.add(db, "Врач", "2026-07-29T06:00:00+00:00")
    db.commit()

    fake_deliver.responses = ["sent"]
    tick.digest(db, now_utc=NOW_FOR(2026, 7, 29), cfg=CFG, _fetch_weather=_fetch_wx())
    q = fake_deliver.calls[0]["raw"]["question"]
    assert q == tick.DIGEST_QUESTION


def test_last_window_day_always_gets_offer(db, fake_deliver):
    # On the LAST day of the window, the remaining-days set is just
    # {today} -- always the argmin, always the offer, regardless of
    # events on that day.
    cal.add(db, "Врач", "2026-07-31T06:00:00+00:00")
    db.commit()

    fake_deliver.responses = ["sent"]
    tick.digest(db, now_utc=NOW_FOR(2026, 7, 31), cfg=CFG, _fetch_weather=_fetch_wx())
    q = fake_deliver.calls[0]["raw"]["question"]
    assert q == goals.ritual_question_text("2026-08")


# ---- offered repeats daily ----

def test_offered_state_repeats_question_every_day(db, fake_deliver):
    goals.plan_state_set(db, "2026-08", "offered", "2026-07-29")
    db.commit()

    fake_deliver.responses = ["sent"]
    tick.digest(db, now_utc=NOW_FOR(2026, 7, 30), cfg=CFG, _fetch_weather=_fetch_wx())
    q = fake_deliver.calls[0]["raw"]["question"]
    assert q == goals.ritual_question_text("2026-08")

    # offered date is NOT re-stamped (state was not empty before this send)
    assert goals.plan_state_get(db, "2026-08") == ("offered", "2026-07-29")


# ---- carry past the 1st ----

def test_offered_carries_past_first_target_unchanged(db, fake_deliver):
    goals.plan_state_set(db, "2026-08", "offered", "2026-07-30")
    db.commit()

    fake_deliver.responses = ["sent"]
    tick.digest(db, now_utc=NOW_FOR(2026, 8, 3), cfg=CFG, _fetch_weather=_fetch_wx())
    q = fake_deliver.calls[0]["raw"]["question"]
    # Target is still August (2026-08), even though "today" is now inside
    # August itself -- goals of the NEW month (August) don't shift the
    # target while the cycle is unanswered.
    assert q == goals.ritual_question_text("2026-08")


# ---- done/declined silence ----

def test_done_state_silences_ritual(db, fake_deliver):
    goals.plan_state_set(db, "2026-08", "done", "2026-07-29")
    db.commit()

    fake_deliver.responses = ["sent"]
    tick.digest(db, now_utc=NOW_FOR(2026, 7, 30), cfg=CFG, _fetch_weather=_fetch_wx())
    assert fake_deliver.calls[0]["raw"]["question"] == tick.DIGEST_QUESTION


def test_declined_state_silences_ritual(db, fake_deliver):
    goals.plan_state_set(db, "2026-08", "declined", "2026-07-29")
    db.commit()

    fake_deliver.responses = ["sent"]
    tick.digest(db, now_utc=NOW_FOR(2026, 7, 30), cfg=CFG, _fetch_weather=_fetch_wx())
    assert fake_deliver.calls[0]["raw"]["question"] == tick.DIGEST_QUESTION


# ---- december -> Q1 text ----

def test_december_target_january_mentions_q1(db, fake_deliver):
    # December has 31 days; window=3 -> 29,30,31 inside window. Target
    # from Dec 30 is January of the FOLLOWING year -- first month of Q1.
    fake_deliver.responses = ["sent"]
    tick.digest(db, now_utc=NOW_FOR(2026, 12, 31), cfg=CFG, _fetch_weather=_fetch_wx())
    q = fake_deliver.calls[0]["raw"]["question"]
    assert "2027-01" not in q  # sanity: text is human, not raw month
    assert "январь" in q
    assert "I квартала" in q
    assert q == goals.ritual_question_text("2027-01")


# ---- first run silent before window ----

def test_first_run_silent_before_window_no_history(db, fake_deliver):
    # Today is well outside the window and no goal_plan_state:* key has
    # EVER been written -- must stay silent (first-run, not catch-up).
    fake_deliver.responses = ["sent"]
    tick.digest(db, now_utc=NOW_FOR(2026, 7, 10), cfg=CFG, _fetch_weather=_fetch_wx())
    assert fake_deliver.calls[0]["raw"]["question"] == tick.DIGEST_QUESTION
    # And no state gets stamped for a question that was never asked.
    assert goals.plan_state_get(db, "2026-07") is None


# ---- catch-up with history ----

def test_catchup_with_history_offers_outside_window(db, fake_deliver):
    # A prior cycle (any month) exists, so this is a genuinely missed
    # window for the CURRENT month, not a fresh install -> catch-up asks.
    goals.plan_state_set(db, "2026-06", "done", "2026-05-30")
    db.commit()

    fake_deliver.responses = ["sent"]
    tick.digest(db, now_utc=NOW_FOR(2026, 7, 10), cfg=CFG, _fetch_weather=_fetch_wx())
    q = fake_deliver.calls[0]["raw"]["question"]
    assert q == goals.ritual_question_text("2026-07")


# ---- offered set only on sent ----

def test_offered_not_stamped_on_skip(db, fake_deliver):
    fake_deliver.responses = ["skipped_budget"]
    tick.digest(db, now_utc=NOW_FOR(2026, 7, 29), cfg=CFG, _fetch_weather=_fetch_wx())
    assert goals.plan_state_get(db, "2026-08") is None


def test_offered_not_stamped_on_error(db, fake_deliver):
    fake_deliver.responses = ["error"]
    tick.digest(db, now_utc=NOW_FOR(2026, 7, 29), cfg=CFG, _fetch_weather=_fetch_wx())
    assert goals.plan_state_get(db, "2026-08") is None


def test_offered_stamped_on_sent(db, fake_deliver):
    fake_deliver.responses = ["sent"]
    tick.digest(db, now_utc=NOW_FOR(2026, 7, 29), cfg=CFG, _fetch_weather=_fetch_wx())
    assert goals.plan_state_get(db, "2026-08") == ("offered", "2026-07-29")


# ---- question replaced only on ritual day ----

def test_non_ritual_day_keeps_digest_question(db, fake_deliver):
    # Day 10 of July -- well outside the window, no history -> ritual
    # stays silent, DIGEST_QUESTION must be untouched, and it must be the
    # actual trailing line of the fallback text too.
    fake_deliver.responses = ["sent"]
    tick.digest(db, now_utc=NOW_FOR(2026, 7, 10), cfg=CFG, _fetch_weather=_fetch_wx())
    call = fake_deliver.calls[0]
    assert call["raw"]["question"] == tick.DIGEST_QUESTION
    assert call["human_fallback"].splitlines()[-1] == tick.DIGEST_QUESTION


def test_ritual_day_replaces_question_in_raw_and_fallback(db, fake_deliver):
    fake_deliver.responses = ["sent"]
    tick.digest(db, now_utc=NOW_FOR(2026, 7, 29), cfg=CFG, _fetch_weather=_fetch_wx())
    call = fake_deliver.calls[0]
    expected = goals.ritual_question_text("2026-08")
    assert call["raw"]["question"] == expected
    assert call["human_fallback"].splitlines()[-1] == expected
