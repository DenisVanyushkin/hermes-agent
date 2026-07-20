"""Phase 8b (goals) Task 5: month-goals block in the morning digest.
Mirrors test_digest_meds.py's fixtures (CFG/NOW/_fetch_wx/fake_deliver/db).
"""
import pytest

from fam import db as dbmod
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
    "goal_digest_intervals": [4, 2, 1],
}

# Almaty offset is UTC+5 -- 04:30 UTC is always 09:30 Almaty the same
# calendar date, so NOW_FOR(day) below always lands "today" on `day`.


def NOW_FOR(day):
    return f"2026-07-{day:02d}T04:30:00+00:00"


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


# ---- cadence: three terciles ----

def test_tercile_1_10_uses_interval_0(db, fake_deliver):
    # day=5 -> tercile[0], interval=4. Never shown before -> due regardless.
    goals.add(db, "Спорт 3x/нед", period="2026-07")
    db.commit()
    fake_deliver.responses = ["sent"]

    tick.digest(db, now_utc=NOW_FOR(5), cfg=CFG, _fetch_weather=_fetch_wx())

    raw = fake_deliver.calls[0]["raw"]
    assert raw["month_goals"] == [{"goal_id": 1, "title": "Спорт 3x/нед"}]

    # Shown on day 5 -> not due again on day 8 (3 days later, interval=4).
    fake_deliver.calls.clear()
    fake_deliver.responses = ["sent"]
    tick.digest(db, now_utc=NOW_FOR(8), cfg=CFG, _fetch_weather=_fetch_wx())
    assert "month_goals" not in fake_deliver.calls[0]["raw"]

    # Due again on day 9 (4 days later, interval=4).
    fake_deliver.calls.clear()
    fake_deliver.responses = ["sent"]
    tick.digest(db, now_utc=NOW_FOR(9), cfg=CFG, _fetch_weather=_fetch_wx())
    assert "month_goals" in fake_deliver.calls[0]["raw"]


def test_tercile_11_20_uses_interval_1(db, fake_deliver):
    # day=12 -> tercile[1], interval=2.
    goals.add(db, "Читать книгу", period="2026-07")
    db.commit()
    dbmod.meta_set(db, "goals_block_last", "2026-07-11")
    db.commit()

    fake_deliver.responses = ["sent"]
    tick.digest(db, now_utc=NOW_FOR(12), cfg=CFG, _fetch_weather=_fetch_wx())
    # only 1 day elapsed since 2026-07-11, interval=2 -> not due
    assert "month_goals" not in fake_deliver.calls[0]["raw"]

    fake_deliver.calls.clear()
    fake_deliver.responses = ["sent"]
    tick.digest(db, now_utc=NOW_FOR(13), cfg=CFG, _fetch_weather=_fetch_wx())
    # 2 days elapsed -> due
    assert "month_goals" in fake_deliver.calls[0]["raw"]


def test_tercile_21_plus_uses_interval_2(db, fake_deliver):
    # day=25 -> tercile[2], interval=1.
    goals.add(db, "Отчёт по бюджету", period="2026-07")
    db.commit()
    dbmod.meta_set(db, "goals_block_last", "2026-07-24")
    db.commit()

    fake_deliver.responses = ["sent"]
    tick.digest(db, now_utc=NOW_FOR(25), cfg=CFG, _fetch_weather=_fetch_wx())
    # 1 day elapsed, interval=1 -> due
    assert "month_goals" in fake_deliver.calls[0]["raw"]


# ---- empty goals list -> key omitted entirely ----

def test_no_open_month_goals_omits_key(db, fake_deliver):
    fake_deliver.responses = ["sent"]

    tick.digest(db, now_utc=NOW_FOR(5), cfg=CFG, _fetch_weather=_fetch_wx())

    assert "month_goals" not in fake_deliver.calls[0]["raw"]
    fallback = fake_deliver.calls[0]["human_fallback"]
    assert "Цели месяца" not in fallback


def test_not_due_omits_key_even_with_open_goals(db, fake_deliver):
    goals.add(db, "Спорт", period="2026-07")
    db.commit()
    dbmod.meta_set(db, "goals_block_last", "2026-07-05")
    db.commit()

    fake_deliver.responses = ["sent"]
    # day=6, tercile[0] interval=4, only 1 day elapsed -> not due
    tick.digest(db, now_utc=NOW_FOR(6), cfg=CFG, _fetch_weather=_fetch_wx())

    assert "month_goals" not in fake_deliver.calls[0]["raw"]


# ---- meta marker only moves on status == "sent" ----

def test_goals_block_last_not_set_on_skip(db, fake_deliver):
    goals.add(db, "Спорт", period="2026-07")
    db.commit()
    fake_deliver.responses = ["skipped_budget"]

    tick.digest(db, now_utc=NOW_FOR(5), cfg=CFG, _fetch_weather=_fetch_wx())

    assert dbmod.meta_get(db, "goals_block_last") is None


def test_goals_block_last_not_set_on_error(db, fake_deliver):
    goals.add(db, "Спорт", period="2026-07")
    db.commit()
    fake_deliver.responses = ["error"]

    tick.digest(db, now_utc=NOW_FOR(5), cfg=CFG, _fetch_weather=_fetch_wx())

    assert dbmod.meta_get(db, "goals_block_last") is None


def test_goals_block_last_set_on_sent(db, fake_deliver):
    goals.add(db, "Спорт", period="2026-07")
    db.commit()
    fake_deliver.responses = ["sent"]

    tick.digest(db, now_utc=NOW_FOR(5), cfg=CFG, _fetch_weather=_fetch_wx())

    assert dbmod.meta_get(db, "goals_block_last") == "2026-07-05"


def test_goals_block_last_not_set_when_not_due(db, fake_deliver):
    # Block not due (and thus not in raw) -> even a "sent" digest must
    # not move the marker (nothing was actually shown).
    goals.add(db, "Спорт", period="2026-07")
    db.commit()
    dbmod.meta_set(db, "goals_block_last", "2026-07-05")
    db.commit()

    fake_deliver.responses = ["sent"]
    tick.digest(db, now_utc=NOW_FOR(6), cfg=CFG, _fetch_weather=_fetch_wx())

    assert dbmod.meta_get(db, "goals_block_last") == "2026-07-05"


# ---- month change resets the "last shown" tracking ----

def test_month_change_resets_last_shown(db, fake_deliver):
    goals.add(db, "Цель августа", period="2026-08")
    db.commit()
    # last shown late July -- different month from date_local (August)
    dbmod.meta_set(db, "goals_block_last", "2026-07-30")
    db.commit()

    fake_deliver.responses = ["sent"]
    tick.digest(db, now_utc=NOW_FOR(1).replace("07", "08", 1), cfg=CFG,
                _fetch_weather=_fetch_wx())

    assert fake_deliver.calls[0]["raw"]["month_goals"] == [
        {"goal_id": 1, "title": "Цель августа"}
    ]


def test_no_goals_block_last_at_all_is_due(db, fake_deliver):
    goals.add(db, "Свежая цель", period="2026-07")
    db.commit()
    fake_deliver.responses = ["sent"]

    tick.digest(db, now_utc=NOW_FOR(15), cfg=CFG, _fetch_weather=_fetch_wx())

    assert fake_deliver.calls[0]["raw"]["month_goals"] == [
        {"goal_id": 1, "title": "Свежая цель"}
    ]


# ---- quarter goals never appear ----

def test_quarter_goal_excluded_from_block(db, fake_deliver):
    goals.add(db, "Квартальная цель", period="2026-Q3")
    goals.add(db, "Месячная цель", period="2026-07")
    db.commit()
    fake_deliver.responses = ["sent"]

    tick.digest(db, now_utc=NOW_FOR(5), cfg=CFG, _fetch_weather=_fetch_wx())

    month_goals = fake_deliver.calls[0]["raw"]["month_goals"]
    titles = [g["title"] for g in month_goals]
    assert "Квартальная цель" not in titles
    assert "Месячная цель" in titles


def test_fallback_text_includes_month_goals_section(db, fake_deliver):
    goals.add(db, "Спорт", period="2026-07")
    db.commit()
    fake_deliver.responses = ["sent"]

    tick.digest(db, now_utc=NOW_FOR(5), cfg=CFG, _fetch_weather=_fetch_wx())

    fallback = fake_deliver.calls[0]["human_fallback"]
    assert "Цели месяца:" in fallback
    assert "Спорт" in fallback
