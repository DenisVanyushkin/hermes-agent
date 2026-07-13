"""Task 6 (3b): evening combined follow-up for outbound days.

One message per day, kind="followup", sent in the first minute-tick at or
after cfg["followup_local_time"] (default 20:00 Asia/Almaty) and before
quiet hours -- ONLY when today had at least one outbound event (active,
place_id NOT NULL, start_utc already in the past relative to the tick)
AND at least one open plan related to those events (attached_event_id ==
event.id, or plan.person_id among the event's participants). Dedup via
meta key followup_sent:<date_local> -- set on every outcome (sent,
no_events, no_plans) so a later tick the same day never re-checks.
"""
import pytest

from fam import audit, cal, gate, people, places, plans, tick


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

# 2026-07-20 is a Monday, Asia/Almaty is UTC+5 (single zone since the
# 2024 unification) -- 20:00 local = 15:00 UTC.
BEFORE_FOLLOWUP = "2026-07-20T14:59:00+00:00"   # 19:59 Almaty
AT_FOLLOWUP = "2026-07-20T15:00:00+00:00"       # 20:00 Almaty
AFTER_FOLLOWUP = "2026-07-20T15:05:00+00:00"    # 20:05 Almaty
EVENT_START = "2026-07-20T09:00:00+00:00"       # 14:00 Almaty -- already past by 20:00


def _place_event(db, title="Врач", start=EVENT_START, **kw):
    places.add(db, "Клиника Дента", aliases=["клиника"],
               lat=43.2260, lon=76.8670)
    db.commit()
    e = cal.add(db, title, start, place="Клиника Дента", **kw)
    db.commit()
    return e


def _plan_attached(db, event_id, title="Забрать справку"):
    pid = plans.add(db, title)
    db.commit()
    plans.attach(db, pid, event_id)
    db.commit()
    return pid


def test_no_message_before_followup_time(db, fake_deliver):
    e = _place_event(db)
    _plan_attached(db, e["id"])

    tick.reminders(db, now_utc=BEFORE_FOLLOWUP, cfg=CFG)

    assert fake_deliver.calls == []
    row = db.execute(
        "SELECT value FROM meta WHERE key='followup_sent:2026-07-20'"
    ).fetchone()
    assert row is None


def test_followup_exception_does_not_break_reminders_tick(db, fake_deliver, monkeypatch):
    # Final review Finding 2: an exception raised from _followup must not
    # propagate out of tick.reminders() -- reminders already processed
    # this tick stay committed, and the failure is audited as tick.error.
    e = _place_event(db)
    _plan_attached(db, e["id"])

    def boom(conn, now_utc, cfg):
        raise RuntimeError("followup blew up")

    monkeypatch.setattr(tick, "_followup", boom)

    counts = tick.reminders(db, now_utc=AT_FOLLOWUP, cfg=CFG)

    assert isinstance(counts, dict)  # tick.reminders itself completed
    rows = db.execute(
        "SELECT payload FROM audit_log WHERE kind='tick.error'"
    ).fetchall()
    assert len(rows) == 1
    import json
    payload = json.loads(rows[0]["payload"])
    assert payload["where"] == "followup"
    # tick.reminders' own summary audit row still gets written after.
    assert db.execute(
        "SELECT COUNT(*) FROM audit_log WHERE kind='tick.reminders'"
    ).fetchone()[0] == 1


def test_sends_one_followup_with_plan_titles_and_sets_meta(db, fake_deliver):
    e = _place_event(db)
    _plan_attached(db, e["id"], title="Забрать справку")
    fake_deliver.responses = ["sent"]

    counts = tick.reminders(db, now_utc=AT_FOLLOWUP, cfg=CFG)

    followup_calls = [c for c in fake_deliver.calls if c["kind"] == "followup"]
    assert len(followup_calls) == 1
    call = followup_calls[0]
    assert call["force"] is False
    text_blob = str(call["raw"]) + call["human_fallback"]
    assert "Забрать справку" in text_blob

    row = db.execute(
        "SELECT value FROM meta WHERE key='followup_sent:2026-07-20'"
    ).fetchone()
    assert row is not None

    audit_rows = db.execute(
        "SELECT kind, payload FROM audit_log WHERE kind='tick.followup'"
    ).fetchall()
    assert len(audit_rows) == 1


def test_second_tick_same_day_is_silent(db, fake_deliver):
    e = _place_event(db)
    _plan_attached(db, e["id"])
    fake_deliver.responses = ["sent"]

    tick.reminders(db, now_utc=AT_FOLLOWUP, cfg=CFG)
    assert len(fake_deliver.calls) == 1

    fake_deliver.calls = []
    tick.reminders(db, now_utc=AFTER_FOLLOWUP, cfg=CFG)

    followup_calls = [c for c in fake_deliver.calls if c["kind"] == "followup"]
    assert followup_calls == []


def test_no_outbound_events_is_silent_but_sets_meta(db, fake_deliver):
    # Event exists but has no place -- doesn't count as "outbound".
    cal.add(db, "Звонок", EVENT_START)
    db.commit()

    tick.reminders(db, now_utc=AT_FOLLOWUP, cfg=CFG)

    followup_calls = [c for c in fake_deliver.calls if c["kind"] == "followup"]
    assert followup_calls == []
    row = db.execute(
        "SELECT value FROM meta WHERE key='followup_sent:2026-07-20'"
    ).fetchone()
    assert row is not None


def test_outbound_event_but_no_related_plan_is_silent_but_sets_meta(db, fake_deliver):
    _place_event(db)
    # An open plan exists but is unrelated (not attached, no shared person).
    plans.add(db, "Купить корм")
    db.commit()

    tick.reminders(db, now_utc=AT_FOLLOWUP, cfg=CFG)

    followup_calls = [c for c in fake_deliver.calls if c["kind"] == "followup"]
    assert followup_calls == []
    row = db.execute(
        "SELECT value FROM meta WHERE key='followup_sent:2026-07-20'"
    ).fetchone()
    assert row is not None


def test_related_plan_via_person_match(db, fake_deliver):
    people.add(db, "Тая")
    db.commit()
    e = _place_event(db, participants=["Тая"])
    pid = plans.add(db, "Забрать игрушку", person="Тая")
    db.commit()
    fake_deliver.responses = ["sent"]

    tick.reminders(db, now_utc=AT_FOLLOWUP, cfg=CFG)

    followup_calls = [c for c in fake_deliver.calls if c["kind"] == "followup"]
    assert len(followup_calls) == 1
    text_blob = str(followup_calls[0]["raw"])
    assert "Забрать игрушку" in text_blob


def test_event_not_yet_started_does_not_count_as_outbound(db, fake_deliver):
    # Use an event starting AFTER the followup tick itself (21:00 Almaty = 16:00 UTC).
    places.add(db, "Клиника Дента", aliases=["клиника"],
               lat=43.2260, lon=76.8670)
    db.commit()
    e = cal.add(db, "Врач вечером", "2026-07-20T16:00:00+00:00",
                place="Клиника Дента")
    db.commit()
    _plan_attached(db, e["id"])

    tick.reminders(db, now_utc=AT_FOLLOWUP, cfg=CFG)

    followup_calls = [c for c in fake_deliver.calls if c["kind"] == "followup"]
    assert followup_calls == []
