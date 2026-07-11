import json
import subprocess
from datetime import datetime, timedelta, timezone

import pytest

from fam import audit, cal, gate, people, places, tick

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

NOW = "2026-07-20T04:30:00+00:00"
# 10 min before NOW: always "due" relative to NOW, and -- since Fix 1's
# stale-age guard cancels anything older than cfg["reminder_max_age_min"]
# (120 min default) -- fresh enough to survive it and reach gate.deliver,
# unlike the year-2000 timestamp this used to be.
PAST = "2026-07-20T04:20:00+00:00"
FUTURE = "2099-01-01T00:00:00+00:00"  # never "due" relative to NOW


def _insert_reminder(db, event_id, label="напоминание", fire_at=PAST,
                      status="pending", anchor="start", created_at=PAST):
    cur = db.execute(
        "INSERT INTO reminders(event_id, label, anchor, fire_at_utc, "
        "status, created_at) VALUES (?,?,?,?,?,?)",
        (event_id, label, anchor, fire_at, status, created_at),
    )
    return cur.lastrowid


class FakeDeliver:
    """Records every gate.deliver() call and returns canned statuses
    consumed in order -- tick.py tests never touch subprocess/hermes;
    gate.py's own subprocess contract is exercised in test_gate.py."""

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


# ---- due selection by time ----

def test_due_selection_only_past_fire_at_is_processed(db, fake_deliver):
    e = _event(db)
    db.commit()
    due_id = _insert_reminder(db, e["id"], fire_at=PAST)
    _insert_reminder(db, e["id"], fire_at=FUTURE)
    db.commit()
    fake_deliver.responses = ["sent"]

    counts = tick.reminders(db, now_utc=NOW, cfg=CFG)

    assert counts == {"due": 1, "sent": 1, "quiet": 0, "budget": 0,
                       "error": 0, "cancelled": 0, "stale": 0,
                       "error_capped": 0}
    assert len(fake_deliver.calls) == 1
    statuses = {r["id"]: r["status"] for r in db.execute(
        "SELECT id, status FROM reminders WHERE event_id=?", (e["id"],))}
    assert statuses[due_id] == "sent"


# ---- sent updates status + sent_at ----

def test_sent_updates_status_and_sent_at(db, fake_deliver):
    e = _event(db)
    db.commit()
    rid = _insert_reminder(db, e["id"])
    db.commit()
    fake_deliver.responses = ["sent"]

    tick.reminders(db, now_utc=NOW, cfg=CFG)

    row = db.execute(
        "SELECT status, sent_at FROM reminders WHERE id=?", (rid,)
    ).fetchone()
    assert row["status"] == "sent"
    assert row["sent_at"] == NOW


# ---- raw/fallback payload shape passed to gate.deliver ----

def test_raw_and_fallback_shape(db, fake_deliver):
    people.add(db, "Тая", slug="taya")
    places.add(db, "Клиника")
    db.commit()
    e = _event(db, title="Врач", place="Клиника", participants=["Тая"])
    db.commit()
    _insert_reminder(db, e["id"], label="пора выходить")
    db.commit()
    fake_deliver.responses = ["sent"]

    tick.reminders(db, now_utc=NOW, cfg=CFG)

    call = fake_deliver.calls[0]
    assert call["kind"] == "reminder"
    assert call["raw"] == {
        "label": "пора выходить",
        "event_id": e["id"],
        "title": "Врач",
        "start_local": e["start_local"],
        "participants": ["Тая"],
        "place_name": "Клиника",
    }
    assert call["human_fallback"] == (
        f"пора выходить: Врач — {e['start_local']}"
    )
    assert call["now_utc"] == NOW


def test_raw_omits_place_name_when_no_place(db, fake_deliver):
    e = _event(db)
    db.commit()
    _insert_reminder(db, e["id"])
    db.commit()
    fake_deliver.responses = ["sent"]

    tick.reminders(db, now_utc=NOW, cfg=CFG)

    assert "place_name" not in fake_deliver.calls[0]["raw"]
    assert fake_deliver.calls[0]["raw"]["participants"] == []


# ---- quiet / budget / error leave the reminder pending ----

@pytest.mark.parametrize("gate_status", ["quiet", "budget", "error"])
def test_non_sent_outcomes_leave_reminder_pending(db, fake_deliver, gate_status):
    e = _event(db)
    db.commit()
    rid = _insert_reminder(db, e["id"])
    db.commit()
    fake_deliver.responses = [gate_status]

    counts = tick.reminders(db, now_utc=NOW, cfg=CFG)

    assert counts["due"] == 1
    assert counts[gate_status] == 1
    assert counts["sent"] == 0
    row = db.execute(
        "SELECT status, sent_at FROM reminders WHERE id=?", (rid,)
    ).fetchone()
    assert row["status"] == "pending"
    assert row["sent_at"] is None


# ---- cancelled/inactive event cancels the reminder, no send attempted ----

@pytest.mark.parametrize("event_status", ["cancelled", "done"])
def test_inactive_event_cancels_reminder_without_delivering(db, fake_deliver,
                                                              event_status):
    e = _event(db)
    db.commit()
    # Flip status directly via SQL (not cal.cancel()/cal.done()) so this
    # test pins the tick's OWN defensive check, not cal.py's existing
    # cancel_chain hook -- mirrors test_rem.py's
    # test_regenerate_on_cancelled_event_clears_pending pattern.
    db.execute("UPDATE events SET status=? WHERE id=?", (event_status, e["id"]))
    rid = _insert_reminder(db, e["id"])
    db.commit()
    fake_deliver.responses = []  # must not be called

    counts = tick.reminders(db, now_utc=NOW, cfg=CFG)

    assert counts == {"due": 1, "sent": 0, "quiet": 0, "budget": 0,
                       "error": 0, "cancelled": 1, "stale": 0,
                       "error_capped": 0}
    assert fake_deliver.calls == []
    row = db.execute(
        "SELECT status FROM reminders WHERE id=?", (rid,)
    ).fetchone()
    assert row["status"] == "cancelled"

    audit_rows = audit.query(db, since_utc=None, kind_prefix="rem.cancel_stale",
                              grep=None, limit=10)
    assert len(audit_rows) == 1
    assert audit_rows[0]["payload"] == {"reminder_id": rid, "event_id": e["id"]}


# ---- stale-age guard: parked-too-long reminders are cancelled, not sent
# (Fix 1, pre-live guards review round) ----

def test_stale_pending_reminder_8h_old_is_cancelled_not_delivered(db, fake_deliver):
    # Models a reminder repeatedly parked by quiet hours until it's 8h
    # old -- well past reminder_max_age_min (120 min default) -- which
    # must now be cancelled as stale rather than delivered late.
    e = _event(db)  # start 2026-07-20T05:00:00+00:00, 30 min after NOW
    db.commit()
    old_fire_at = "2026-07-19T20:30:00+00:00"  # 8h before NOW
    rid = _insert_reminder(db, e["id"], fire_at=old_fire_at)
    db.commit()
    fake_deliver.responses = []  # must not be called

    counts = tick.reminders(db, now_utc=NOW, cfg=CFG)

    assert counts["due"] == 1
    assert counts["stale"] == 1
    assert counts["sent"] == 0
    assert fake_deliver.calls == []
    row = db.execute(
        "SELECT status FROM reminders WHERE id=?", (rid,)
    ).fetchone()
    assert row["status"] == "cancelled"

    audit_rows = audit.query(db, since_utc=None, kind_prefix="rem.cancel_stale_age",
                              grep=None, limit=10)
    assert len(audit_rows) == 1
    assert audit_rows[0]["payload"] == {
        "reminder_id": rid, "event_id": e["id"],
        "fire_at_utc": old_fire_at, "age_min": 480,
    }


def test_fresh_reminder_10_min_late_is_delivered_normally(db, fake_deliver):
    # Age < reminder_max_age_min (120): delivered as normal, not stale.
    e = _event(db)
    db.commit()
    rid = _insert_reminder(db, e["id"], fire_at=PAST)  # 10 min before NOW
    db.commit()
    fake_deliver.responses = ["sent"]

    counts = tick.reminders(db, now_utc=NOW, cfg=CFG)

    assert counts["due"] == 1
    assert counts["stale"] == 0
    assert counts["sent"] == 1
    assert len(fake_deliver.calls) == 1
    row = db.execute(
        "SELECT status FROM reminders WHERE id=?", (rid,)
    ).fetchone()
    assert row["status"] == "sent"


def test_stale_via_event_start_far_in_past_even_with_fresh_fire_at(db, fake_deliver):
    # The OR branch: a fresh fire_at (age 0) is still stale if the
    # event's own start_utc is already more than max_age in the past --
    # e.g. a reminder regenerated late for an event that already happened.
    e = _event(db, start="2026-07-20T02:00:00+00:00")  # 2h30m before NOW
    db.commit()
    rid = _insert_reminder(db, e["id"], fire_at=NOW)
    db.commit()
    fake_deliver.responses = []  # must not be called

    counts = tick.reminders(db, now_utc=NOW, cfg=CFG)

    assert counts["stale"] == 1
    assert counts["sent"] == 0
    assert fake_deliver.calls == []
    row = db.execute(
        "SELECT status FROM reminders WHERE id=?", (rid,)
    ).fetchone()
    assert row["status"] == "cancelled"


# ---- retry cap: repeated delivery errors eventually cancel the reminder
# (Fix 2, pre-live guards review round) ----

def test_two_errors_keep_reminder_pending_with_incrementing_error_count(db, fake_deliver):
    e = _event(db)
    db.commit()
    rid = _insert_reminder(db, e["id"])
    db.commit()

    fake_deliver.responses = ["error"]
    first = tick.reminders(db, now_utc=NOW, cfg=CFG)
    assert first["error"] == 1
    assert first["error_capped"] == 0

    fake_deliver.responses = ["error"]
    second = tick.reminders(db, now_utc=NOW, cfg=CFG)
    assert second["error"] == 1
    assert second["error_capped"] == 0

    row = db.execute(
        "SELECT status, error_count FROM reminders WHERE id=?", (rid,)
    ).fetchone()
    assert row["status"] == "pending"
    assert row["error_count"] == 2


def test_third_error_hits_cap_and_cancels(db, fake_deliver):
    e = _event(db)
    db.commit()
    rid = _insert_reminder(db, e["id"])
    db.commit()

    for _ in range(2):
        fake_deliver.responses = ["error"]
        tick.reminders(db, now_utc=NOW, cfg=CFG)

    fake_deliver.responses = ["error"]
    counts = tick.reminders(db, now_utc=NOW, cfg=CFG)

    assert counts["error"] == 0
    assert counts["error_capped"] == 1
    row = db.execute(
        "SELECT status, error_count FROM reminders WHERE id=?", (rid,)
    ).fetchone()
    assert row["status"] == "cancelled"
    assert row["error_count"] == 3

    audit_rows = audit.query(db, since_utc=None, kind_prefix="rem.cancel_error_cap",
                              grep=None, limit=10)
    assert len(audit_rows) == 1
    assert audit_rows[0]["payload"] == {"reminder_id": rid, "errors": 3}


# ---- zero-run always audits ----

def test_zero_due_run_still_audits_tick_reminders(db, fake_deliver):
    fake_deliver.responses = []

    counts = tick.reminders(db, now_utc=NOW, cfg=CFG)

    assert counts == {"due": 0, "sent": 0, "quiet": 0, "budget": 0,
                       "error": 0, "cancelled": 0, "stale": 0,
                       "error_capped": 0}
    rows = audit.query(db, since_utc=None, kind_prefix="tick.reminders",
                        grep=None, limit=10)
    assert len(rows) == 1
    assert rows[0]["payload"] == counts


def test_tick_reminders_audit_payload_matches_counts_on_mixed_run(db, fake_deliver):
    e1 = _event(db, title="A")
    e2 = _event(db, title="B", start="2026-07-21T05:00:00+00:00")
    db.commit()
    _insert_reminder(db, e1["id"])
    _insert_reminder(db, e2["id"])
    db.commit()
    fake_deliver.responses = ["sent", "quiet"]

    counts = tick.reminders(db, now_utc=NOW, cfg=CFG)

    rows = audit.query(db, since_utc=None, kind_prefix="tick.reminders",
                        grep=None, limit=10)
    assert len(rows) == 1
    assert rows[0]["payload"] == counts
    assert counts["due"] == 2 and counts["sent"] == 1 and counts["quiet"] == 1


# ---- idempotence: immediate re-run sees due=0 ----

def test_idempotent_rerun_sees_zero_due(db, fake_deliver):
    e = _event(db)
    db.commit()
    _insert_reminder(db, e["id"])
    db.commit()
    fake_deliver.responses = ["sent"]

    first = tick.reminders(db, now_utc=NOW, cfg=CFG)
    assert first["due"] == 1 and first["sent"] == 1

    fake_deliver.responses = []  # would raise IndexError if called again
    second = tick.reminders(db, now_utc=NOW, cfg=CFG)

    assert second == {"due": 0, "sent": 0, "quiet": 0, "budget": 0,
                       "error": 0, "cancelled": 0, "stale": 0,
                       "error_capped": 0}
    assert len(fake_deliver.calls) == 1  # only the first run's call


def test_idempotent_rerun_after_quiet_leaves_reminder_due_again(db, fake_deliver):
    # A "quiet" outcome leaves the reminder pending, so a later tick
    # (once the quiet window has passed) must see it as due again --
    # this is the whole point of NOT advancing status on quiet/budget.
    e = _event(db)
    db.commit()
    _insert_reminder(db, e["id"])
    db.commit()
    fake_deliver.responses = ["quiet"]

    first = tick.reminders(db, now_utc=NOW, cfg=CFG)
    assert first["due"] == 1 and first["quiet"] == 1

    fake_deliver.responses = ["sent"]
    second = tick.reminders(db, now_utc=NOW, cfg=CFG)

    assert second["due"] == 1 and second["sent"] == 1


# ---- default now_utc / default cfg wiring (no explicit override) ----

def test_reminders_uses_real_now_when_not_given(db, fake_deliver):
    # This pins that omitting now_utc doesn't raise and drives
    # due-selection off the real wall clock. Both the event start and the
    # reminder's fire_at are built relative to the real current time
    # (rather than the fixed NOW/PAST constants) so they stay inside
    # reminder_max_age_min of whenever this test actually runs -- an
    # old fixed timestamp would now be judged stale by Fix 1's guard
    # before ever reaching gate.deliver.
    real_now = datetime.now(timezone.utc)
    start = (real_now + timedelta(hours=1)).isoformat(timespec="seconds")
    fire_at = (real_now - timedelta(minutes=5)).isoformat(timespec="seconds")
    e = _event(db, start=start)
    db.commit()
    _insert_reminder(db, e["id"], fire_at=fire_at)
    db.commit()
    fake_deliver.responses = ["sent"]

    counts = tick.reminders(db, cfg=CFG)

    assert counts["due"] == 1 and counts["sent"] == 1


def test_reminders_loads_config_when_not_given(db, fake_deliver, monkeypatch, tmp_path):
    example = tmp_path / "fam-config.example.json"
    example.write_text(json.dumps(CFG, ensure_ascii=False), encoding="utf-8")
    target = tmp_path / "fam-config.json"
    monkeypatch.setattr(gate, "CONFIG_PATH", target)
    monkeypatch.setattr(gate, "CONFIG_EXAMPLE_PATH", example)
    fake_deliver.responses = []

    counts = tick.reminders(db, now_utc=NOW)

    assert counts["due"] == 0
    assert target.exists()


# ==== Task 7: fam tick digest ====
# NOW ("2026-07-20T04:30:00+00:00") is 2026-07-20T09:30 Almaty, so "today"
# for the digest is 2026-07-20 -- reused from the reminders section above.

WX = {
    "today": {"tmin": 19.0, "tmax": 33.0, "precip_mm": 0.0,
              "precip_hours": 0.0, "wind": 10.0},
    "tomorrow": {"tmin": 18.0, "tmax": 30.0, "precip_mm": 2.0,
                 "precip_hours": 3.0, "wind": 12.0},
}


def _fetch_wx(wx=WX):
    return lambda: wx


def _insert_gate_sent(db, ts_utc, payload):
    db.execute(
        "INSERT INTO audit_log(ts_utc, kind, actor, payload) VALUES(?,?,?,?)",
        (ts_utc, "gate.sent", "test", json.dumps(payload, ensure_ascii=False)),
    )


# ---- raw assembly ----

def test_digest_raw_shape_with_weather_and_event(db, fake_deliver):
    e = _event(db, title="Встреча", start="2026-07-20T10:00:00+00:00")
    db.commit()
    fake_deliver.responses = ["sent"]

    tick.digest(db, now_utc=NOW, cfg=CFG, _fetch_weather=_fetch_wx())

    call = fake_deliver.calls[0]
    assert call["kind"] == "digest"
    assert call["force"] is True
    raw = call["raw"]
    assert raw == {
        "kind": "digest",
        "date_local": "2026-07-20",
        "weather": WX,
        "events": [{"title": "Встреча", "start_local": e["start_local"]}],
        "question": True,
    }


def test_digest_raw_event_includes_place_name_when_present(db, fake_deliver):
    places.add(db, "Клиника")
    db.commit()
    e = _event(db, title="Врач", place="Клиника",
               start="2026-07-20T10:00:00+00:00")
    db.commit()
    fake_deliver.responses = ["sent"]

    tick.digest(db, now_utc=NOW, cfg=CFG, _fetch_weather=_fetch_wx())

    raw = fake_deliver.calls[0]["raw"]
    assert raw["events"] == [
        {"title": "Врач", "start_local": e["start_local"],
         "place_name": "Клиника"}
    ]


def test_digest_raw_weather_none_when_fetch_returns_none(db, fake_deliver):
    fake_deliver.responses = ["sent"]

    tick.digest(db, now_utc=NOW, cfg=CFG, _fetch_weather=lambda: None)

    assert fake_deliver.calls[0]["raw"]["weather"] is None


def test_digest_raw_events_empty_when_none_today(db, fake_deliver):
    # tomorrow, not today -- cal.day() must not pick it up
    _event(db, start="2026-07-21T05:00:00+00:00")
    db.commit()
    fake_deliver.responses = ["sent"]

    tick.digest(db, now_utc=NOW, cfg=CFG, _fetch_weather=_fetch_wx())

    assert fake_deliver.calls[0]["raw"]["events"] == []


def test_digest_raw_omits_cancelled_events(db, fake_deliver):
    # cal.day() is active-only by design (plan: right choice for a "what's
    # still on the plan today" digest) -- a cancelled event today must not
    # appear.
    e = _event(db, start="2026-07-20T10:00:00+00:00")
    cal.cancel(db, e["id"])
    db.commit()
    fake_deliver.responses = ["sent"]

    tick.digest(db, now_utc=NOW, cfg=CFG, _fetch_weather=_fetch_wx())

    assert fake_deliver.calls[0]["raw"]["events"] == []


# ---- fallback text shape ----

def test_digest_fallback_includes_all_sections_and_question(db, fake_deliver):
    e = _event(db, title="Встреча", start="2026-07-20T10:00:00+00:00")
    db.commit()
    fake_deliver.responses = ["sent"]

    tick.digest(db, now_utc=NOW, cfg=CFG, _fetch_weather=_fetch_wx())

    fallback = fake_deliver.calls[0]["human_fallback"]
    assert "2026-07-20" in fallback
    assert "19" in fallback and "33" in fallback and "без осадков" in fallback
    time_str = datetime.fromisoformat(e["start_local"]).strftime("%H:%M")
    assert f"{time_str} Встреча" in fallback
    assert fallback.rstrip().endswith("Какие планы на сегодня?")
    assert len(fallback) <= 900


def test_digest_fallback_no_events_says_so(db, fake_deliver):
    fake_deliver.responses = ["sent"]

    tick.digest(db, now_utc=NOW, cfg=CFG, _fetch_weather=_fetch_wx())

    fallback = fake_deliver.calls[0]["human_fallback"]
    assert "Событий нет" in fallback
    assert fallback.rstrip().endswith("Какие планы на сегодня?")


def test_digest_fallback_omits_weather_section_when_none(db, fake_deliver):
    fake_deliver.responses = ["sent"]

    tick.digest(db, now_utc=NOW, cfg=CFG, _fetch_weather=lambda: None)

    fallback = fake_deliver.calls[0]["human_fallback"]
    assert "°C" not in fallback
    assert fallback.rstrip().endswith("Какие планы на сегодня?")


def test_digest_fallback_mentions_precipitation_when_present(db, fake_deliver):
    wx = {"today": dict(WX["today"], precip_mm=5.0), "tomorrow": WX["tomorrow"]}
    fake_deliver.responses = ["sent"]

    tick.digest(db, now_utc=NOW, cfg=CFG, _fetch_weather=lambda: wx)

    fallback = fake_deliver.calls[0]["human_fallback"]
    assert "без осадков" not in fallback
    assert "осад" in fallback


# ---- dup guard: already sent today ----

def test_digest_dup_guard_skips_when_gate_sent_digest_already_today(db, fake_deliver):
    # 2026-07-20T02:00:00Z falls inside today's Almaty day bounds relative
    # to NOW (2026-07-19T19:00Z .. 2026-07-20T19:00Z).
    _insert_gate_sent(db, "2026-07-20T02:00:00+00:00", {"kind": "digest"})
    db.commit()
    fake_deliver.responses = []  # must not be called

    summary = tick.digest(db, now_utc=NOW, cfg=CFG, _fetch_weather=_fetch_wx())

    assert summary == {"skipped": "already_sent"}
    assert fake_deliver.calls == []
    rows = audit.query(db, since_utc=None, kind_prefix="tick.digest",
                        grep=None, limit=10)
    assert len(rows) == 1
    assert rows[0]["payload"] == {"skipped": "already_sent"}


def test_digest_dup_guard_ignores_gate_sent_reminder_kind(db, fake_deliver):
    # A reminder's gate.sent row (payload kind="reminder") must not trip
    # the digest's own dup guard -- only kind=="digest" counts.
    _insert_gate_sent(db, "2026-07-20T02:00:00+00:00", {"kind": "reminder"})
    db.commit()
    fake_deliver.responses = ["sent"]

    summary = tick.digest(db, now_utc=NOW, cfg=CFG, _fetch_weather=_fetch_wx())

    assert summary["status"] == "sent"
    assert len(fake_deliver.calls) == 1


def test_digest_dup_guard_ignores_yesterdays_digest(db, fake_deliver):
    _insert_gate_sent(db, "2026-07-19T10:00:00+00:00", {"kind": "digest"})
    db.commit()
    fake_deliver.responses = ["sent"]

    summary = tick.digest(db, now_utc=NOW, cfg=CFG, _fetch_weather=_fetch_wx())

    assert summary["status"] == "sent"


class _FakeRunOK:
    """Real gate.deliver, fake hermes subprocess -- exercises the dup
    guard end-to-end against the gate.sent row gate.deliver itself
    writes, unlike the fake_deliver-based tests above which never touch
    gate.py's own audit trail."""

    def __call__(self, args, **kwargs):
        if "-z" in args:
            return subprocess.CompletedProcess(
                args=[], returncode=0, stdout="Доброе утро!", stderr="")
        if "send" in args:
            return subprocess.CompletedProcess(
                args=[], returncode=0, stdout="", stderr="")
        raise AssertionError(f"unexpected hermes invocation: {args}")


def test_digest_real_second_run_same_day_is_skipped(db, monkeypatch):
    # Uses the REAL wall clock (no now_utc override) on purpose:
    # audit.log() always stamps ts_utc from the real clock, regardless of
    # any now_utc a caller passes to a domain function (see audit.py) --
    # so the gate.sent row gate.deliver writes here is real-time-stamped,
    # and this test's day-bounds must be anchored to that same real "now"
    # for the dup guard to see it. This matches production exactly:
    # systemd invokes `fam tick digest` with no --now override at all.
    monkeypatch.setattr(gate.subprocess, "run", _FakeRunOK())

    first = tick.digest(db, cfg=CFG, _fetch_weather=_fetch_wx())
    assert first["status"] == "sent"

    second = tick.digest(db, cfg=CFG, _fetch_weather=_fetch_wx())
    assert second == {"skipped": "already_sent"}


# ---- gate.deliver wiring: force=True, outside budget ----

def test_digest_uses_force_true_bypassing_quiet_and_budget(db, fake_deliver):
    fake_deliver.responses = ["sent"]

    tick.digest(db, now_utc=NOW, cfg=CFG, _fetch_weather=_fetch_wx())

    assert fake_deliver.calls[0]["force"] is True
    assert fake_deliver.calls[0]["now_utc"] == NOW


# ---- always audits tick.digest ----

def test_digest_audits_tick_digest_with_status_on_sent(db, fake_deliver):
    fake_deliver.responses = ["sent"]

    summary = tick.digest(db, now_utc=NOW, cfg=CFG, _fetch_weather=_fetch_wx())

    assert summary["status"] == "sent"
    rows = audit.query(db, since_utc=None, kind_prefix="tick.digest",
                        grep=None, limit=10)
    assert len(rows) == 1
    assert rows[0]["payload"]["status"] == "sent"
    assert rows[0]["payload"]["date_local"] == "2026-07-20"


def test_digest_audits_tick_digest_with_status_on_error(db, fake_deliver):
    fake_deliver.responses = ["error"]

    summary = tick.digest(db, now_utc=NOW, cfg=CFG, _fetch_weather=_fetch_wx())

    assert summary["status"] == "error"
    rows = audit.query(db, since_utc=None, kind_prefix="tick.digest",
                        grep=None, limit=10)
    assert rows[0]["payload"]["status"] == "error"


# ---- default now_utc / cfg wiring ----

def test_digest_uses_real_now_when_not_given(db, fake_deliver):
    fake_deliver.responses = ["sent"]

    summary = tick.digest(db, cfg=CFG, _fetch_weather=_fetch_wx())

    assert summary["status"] == "sent"
    assert "date_local" in summary


def test_digest_loads_config_when_not_given(db, fake_deliver, monkeypatch, tmp_path):
    example = tmp_path / "fam-config.example.json"
    example.write_text(json.dumps(CFG, ensure_ascii=False), encoding="utf-8")
    target = tmp_path / "fam-config.json"
    monkeypatch.setattr(gate, "CONFIG_PATH", target)
    monkeypatch.setattr(gate, "CONFIG_EXAMPLE_PATH", example)
    fake_deliver.responses = ["sent"]

    summary = tick.digest(db, now_utc=NOW, _fetch_weather=_fetch_wx())

    assert summary["status"] == "sent"
    assert target.exists()


def test_digest_defaults_fetch_weather_to_weather_fetch_almaty(db, fake_deliver, monkeypatch):
    # Pins the production default injection point without ever touching
    # the network: weather.fetch_almaty itself is monkeypatched.
    from fam import weather as weather_mod
    monkeypatch.setattr(weather_mod, "fetch_almaty", lambda: WX)
    fake_deliver.responses = ["sent"]

    tick.digest(db, now_utc=NOW, cfg=CFG)

    assert fake_deliver.calls[0]["raw"]["weather"] == WX
