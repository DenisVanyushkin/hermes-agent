"""3b Task 5: burning deadline-plans + busy-facts (two days) carried into
the digest raw/fallback, plus the weather:null fallback-mention minor.

Mirrors test_tick.py's digest fixtures (CFG/NOW/WX/_fetch_wx/_event/
fake_deliver/db) -- kept in a separate file per the task brief rather than
appended to the already-large test_tick.py.
"""
from datetime import datetime

import pytest

from fam import cal, gate, people, places, plans, tick

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
}

# 2026-07-20T09:30 Almaty -- same instant test_tick.py's digest section
# uses, so "today" is 2026-07-20 and "tomorrow" is 2026-07-21.
NOW = "2026-07-20T04:30:00+00:00"

WX = {
    "today": {"tmin": 19.0, "tmax": 33.0, "precip_mm": 0.0,
              "precip_hours": 0.0, "wind": 10.0},
    "tomorrow": {"tmin": 18.0, "tmax": 30.0, "precip_mm": 2.0,
                 "precip_hours": 3.0, "wind": 12.0},
}


def _fetch_wx(wx=WX):
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


def _event(db, title="Событие", start="2026-07-20T05:00:00+00:00", **kw):
    return cal.add(db, title, start, **kw)


# ---- burning plans: raw ----

def test_burning_plan_with_deadline_tomorrow_is_in_raw(db, fake_deliver):
    plans.add(db, "Купить подарок", deadline="2026-07-21")
    db.commit()
    fake_deliver.responses = ["sent"]

    tick.digest(db, now_utc=NOW, cfg=CFG, _fetch_weather=_fetch_wx())

    burning = fake_deliver.calls[0]["raw"]["burning_plans"]
    assert len(burning) == 1
    assert burning[0]["title"] == "Купить подарок"
    assert burning[0]["deadline"] == "2026-07-21"
    assert burning[0]["overdue"] is False


def test_burning_plan_overdue_is_marked(db, fake_deliver):
    plans.add(db, "Оплатить счёт", deadline="2026-07-19")
    db.commit()
    fake_deliver.responses = ["sent"]

    tick.digest(db, now_utc=NOW, cfg=CFG, _fetch_weather=_fetch_wx())

    burning = fake_deliver.calls[0]["raw"]["burning_plans"]
    assert len(burning) == 1
    assert burning[0]["overdue"] is True


def test_plan_without_deadline_is_not_burning(db, fake_deliver):
    plans.add(db, "Как-нибудь потом")
    db.commit()
    fake_deliver.responses = ["sent"]

    tick.digest(db, now_utc=NOW, cfg=CFG, _fetch_weather=_fetch_wx())

    assert fake_deliver.calls[0]["raw"]["burning_plans"] == []


def test_plan_deadline_beyond_horizon_is_not_burning(db, fake_deliver):
    # horizon default/CFG is 3 days from 2026-07-20 -> cutoff 2026-07-23.
    plans.add(db, "Далёкий дедлайн", deadline="2026-07-25")
    db.commit()
    fake_deliver.responses = ["sent"]

    tick.digest(db, now_utc=NOW, cfg=CFG, _fetch_weather=_fetch_wx())

    assert fake_deliver.calls[0]["raw"]["burning_plans"] == []


def test_malformed_deadline_is_skipped_not_crashed(db, fake_deliver):
    # Final review Finding 1 (defense-in-depth): plans.add() now rejects
    # a bad deadline, but a row that reached the table via some other
    # path (direct SQL) must not crash the whole digest tick -- it's
    # skipped and audited as plan.bad_deadline instead.
    pid = plans.add(db, "Обычный план")
    db.commit()
    db.execute("UPDATE plans SET deadline=? WHERE id=?", ("не дата", pid))
    db.commit()
    fake_deliver.responses = ["sent"]

    tick.digest(db, now_utc=NOW, cfg=CFG, _fetch_weather=_fetch_wx())

    assert fake_deliver.calls[0]["raw"]["burning_plans"] == []
    row = db.execute(
        "SELECT COUNT(*) FROM audit_log WHERE kind='plan.bad_deadline' "
        "AND json_extract(payload, '$.plan_id')=?", (pid,)
    ).fetchone()
    assert row[0] == 1


def test_attached_plan_is_not_burning(db, fake_deliver):
    e = _event(db, start="2026-07-21T05:00:00+00:00")
    pid = plans.add(db, "Уже привязано", deadline="2026-07-21")
    db.commit()
    plans.attach(db, pid, e["id"])
    db.commit()
    fake_deliver.responses = ["sent"]

    tick.digest(db, now_utc=NOW, cfg=CFG, _fetch_weather=_fetch_wx())

    assert fake_deliver.calls[0]["raw"]["burning_plans"] == []


def test_done_plan_is_not_burning(db, fake_deliver):
    pid = plans.add(db, "Готово", deadline="2026-07-21")
    db.commit()
    plans.mark(db, pid, "done")
    db.commit()
    fake_deliver.responses = ["sent"]

    tick.digest(db, now_utc=NOW, cfg=CFG, _fetch_weather=_fetch_wx())

    assert fake_deliver.calls[0]["raw"]["burning_plans"] == []


# ---- burning plans: fallback ----

def test_burning_plan_appears_in_fallback(db, fake_deliver):
    plans.add(db, "Купить подарок", deadline="2026-07-21")
    db.commit()
    fake_deliver.responses = ["sent"]

    tick.digest(db, now_utc=NOW, cfg=CFG, _fetch_weather=_fetch_wx())

    fallback = fake_deliver.calls[0]["human_fallback"]
    assert "Купить подарок" in fallback
    assert "2026-07-21" in fallback


def test_overdue_plan_marked_in_fallback(db, fake_deliver):
    plans.add(db, "Оплатить счёт", deadline="2026-07-19")
    db.commit()
    fake_deliver.responses = ["sent"]

    tick.digest(db, now_utc=NOW, cfg=CFG, _fetch_weather=_fetch_wx())

    fallback = fake_deliver.calls[0]["human_fallback"]
    assert "просрочен" in fallback


def test_no_burning_plans_section_omitted_from_fallback(db, fake_deliver):
    fake_deliver.responses = ["sent"]

    tick.digest(db, now_utc=NOW, cfg=CFG, _fetch_weather=_fetch_wx())

    fallback = fake_deliver.calls[0]["human_fallback"]
    assert "Горящие планы" not in fallback


def test_fallback_never_mentions_slots_for_burning_plans(db, fake_deliver):
    # Fallback lists burning plans plainly, without proposing a slot --
    # slot suggestion is the LLM rewrite's job (busy-facts feed it, but
    # the deterministic fallback text never invents a time for a plan).
    plans.add(db, "Купить подарок", deadline="2026-07-21")
    db.commit()
    fake_deliver.responses = ["sent"]

    tick.digest(db, now_utc=NOW, cfg=CFG, _fetch_weather=_fetch_wx())

    fallback = fake_deliver.calls[0]["human_fallback"]
    assert "слот" not in fallback.lower()


# ---- busy facts (today + tomorrow): raw only ----

def test_busy_includes_todays_and_tomorrows_events_in_raw(db, fake_deliver):
    _event(db, title="Встреча", start="2026-07-20T10:00:00+00:00")
    _event(db, title="Завтрашнее", start="2026-07-21T06:00:00+00:00")
    db.commit()
    fake_deliver.responses = ["sent"]

    tick.digest(db, now_utc=NOW, cfg=CFG, _fetch_weather=_fetch_wx())

    busy = fake_deliver.calls[0]["raw"]["busy_two_days"]
    titles = [b["title"] for b in busy]
    assert "Встреча" in titles
    assert "Завтрашнее" in titles


def test_busy_excludes_events_beyond_tomorrow(db, fake_deliver):
    _event(db, title="Послезавтра", start="2026-07-22T06:00:00+00:00")
    db.commit()
    fake_deliver.responses = ["sent"]

    tick.digest(db, now_utc=NOW, cfg=CFG, _fetch_weather=_fetch_wx())

    busy = fake_deliver.calls[0]["raw"]["busy_two_days"]
    titles = [b["title"] for b in busy]
    assert "Послезавтра" not in titles


def test_busy_excludes_cancelled_events(db, fake_deliver):
    e = _event(db, title="Отменено", start="2026-07-20T10:00:00+00:00")
    cal.cancel(db, e["id"])
    db.commit()
    fake_deliver.responses = ["sent"]

    tick.digest(db, now_utc=NOW, cfg=CFG, _fetch_weather=_fetch_wx())

    busy = fake_deliver.calls[0]["raw"]["busy_two_days"]
    titles = [b["title"] for b in busy]
    assert "Отменено" not in titles


def test_busy_not_mentioned_in_fallback(db, fake_deliver):
    # Slots are the model's job (Denis's decision) -- the deterministic
    # fallback never lists busy-today/tomorrow facts, only burning plans.
    _event(db, title="Встреча", start="2026-07-20T10:00:00+00:00")
    db.commit()
    fake_deliver.responses = ["sent"]

    tick.digest(db, now_utc=NOW, cfg=CFG, _fetch_weather=_fetch_wx())

    fallback = fake_deliver.calls[0]["human_fallback"]
    assert "Занято" not in fallback


# ---- weather:null minor: fallback never says "погода" ----

def test_weather_none_fallback_never_says_pogoda(db, fake_deliver):
    fake_deliver.responses = ["sent"]

    tick.digest(db, now_utc=NOW, cfg=CFG, _fetch_weather=lambda: None)

    fallback = fake_deliver.calls[0]["human_fallback"]
    assert "погод" not in fallback.lower()
