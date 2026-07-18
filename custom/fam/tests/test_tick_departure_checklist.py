"""Task 5 (Phase 7): departure checklist piggybacks on the PREPARE-stage
reminder only (not leave) -- open plans with prep_when='departure' for
this event go into raw["departure_checklist"] so the rewrite/human_fallback
can remind Amina what to grab before leaving. Deliberately scoped to
kind == "prepare" (not "leave" too, unlike the enroute/shop_enroute/car
piggyback blocks) -- the brief is explicit that the checklist must not be
repeated on every stage, only surfaced once, on prepare.
"""
import pytest

from fam import cal, gate, plans, tick


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

NOW = "2026-07-20T04:30:00+00:00"
PAST = "2026-07-20T04:20:00+00:00"


def _insert_reminder(db, event_id, label="пора собираться", fire_at=PAST,
                      status="pending", anchor="prepare_at", kind="prepare",
                      created_at=PAST):
    cur = db.execute(
        "INSERT INTO reminders(event_id, label, anchor, kind, fire_at_utc, "
        "status, created_at) VALUES (?,?,?,?,?,?,?)",
        (event_id, label, anchor, kind, fire_at, status, created_at),
    )
    return cur.lastrowid


def test_prepare_raw_includes_departure_checklist(db, fake_deliver):
    e = cal.add(db, "Врач", NOW)
    db.commit()
    other = cal.add(db, "Другое событие", NOW)
    db.commit()
    p1 = plans.add(db, "Взять карту", prep_for_event=e["id"],
                    prep_when="departure")
    p2 = plans.add(db, "Взять анализы", prep_for_event=e["id"],
                    prep_when="departure")
    db.commit()
    done_id = plans.add(db, "Уже сделано", prep_for_event=e["id"],
                         prep_when="departure")
    db.commit()
    plans.mark(db, done_id, "done")
    db.commit()
    # plan for a different event must not leak in
    plans.add(db, "Чужое дело", prep_for_event=other["id"],
              prep_when="departure")
    db.commit()

    _insert_reminder(db, e["id"], kind="prepare")
    db.commit()
    fake_deliver.responses = ["sent"]

    tick.reminders(db, now_utc=NOW, cfg=CFG)

    raw = fake_deliver.calls[0]["raw"]
    assert raw["departure_checklist"] == [
        {"plan_id": p1, "title": "Взять карту"},
        {"plan_id": p2, "title": "Взять анализы"},
    ]


def test_leave_raw_no_checklist(db, fake_deliver):
    e = cal.add(db, "Врач", NOW)
    db.commit()
    plans.add(db, "Взять карту", prep_for_event=e["id"], prep_when="departure")
    db.commit()

    _insert_reminder(db, e["id"], label="пора выходить", anchor="leave_at",
                      kind="leave")
    db.commit()
    fake_deliver.responses = ["sent"]

    tick.reminders(db, now_utc=NOW, cfg=CFG)

    raw = fake_deliver.calls[0]["raw"]
    assert "departure_checklist" not in raw


def test_ack_done_removes_from_next_checklist(db, fake_deliver):
    e = cal.add(db, "Врач", NOW)
    db.commit()
    p1 = plans.add(db, "Взять карту", prep_for_event=e["id"],
                    prep_when="departure")
    p2 = plans.add(db, "Взять анализы", prep_for_event=e["id"],
                    prep_when="departure")
    db.commit()

    _insert_reminder(db, e["id"], kind="prepare")
    db.commit()
    fake_deliver.responses = ["sent"] * 5
    tick.reminders(db, now_utc=NOW, cfg=CFG)

    reminder_calls = [c for c in fake_deliver.calls if c["kind"] == "reminder"]
    raw1 = reminder_calls[-1]["raw"]
    assert raw1["departure_checklist"] == [
        {"plan_id": p1, "title": "Взять карту"},
        {"plan_id": p2, "title": "Взять анализы"},
    ]

    plans.mark(db, p1, "done")
    db.commit()

    _insert_reminder(db, e["id"], kind="prepare",
                      fire_at="2026-07-20T04:40:00+00:00",
                      created_at="2026-07-20T04:40:00+00:00")
    db.commit()
    # Second tick's now_utc is a fresh response budget: as many gate.deliver
    # calls as this tick makes (reminder + possibly digest_retry/followup)
    # each need a queued response, so pad generously with "sent".
    fake_deliver.responses = ["sent"] * 5
    tick.reminders(db, now_utc="2026-07-20T04:45:00+00:00", cfg=CFG)

    reminder_calls = [c for c in fake_deliver.calls if c["kind"] == "reminder"]
    raw2 = reminder_calls[-1]["raw"]
    assert raw2["departure_checklist"] == [{"plan_id": p2, "title": "Взять анализы"}]


def test_human_fallback_lists_checklist_titles(db, fake_deliver):
    e = cal.add(db, "Врач", NOW)
    db.commit()
    plans.add(db, "Взять карту", prep_for_event=e["id"], prep_when="departure")
    plans.add(db, "Взять анализы", prep_for_event=e["id"], prep_when="departure")
    db.commit()

    _insert_reminder(db, e["id"], kind="prepare")
    db.commit()
    fake_deliver.responses = ["sent"]

    tick.reminders(db, now_utc=NOW, cfg=CFG)

    fallback = fake_deliver.calls[0]["human_fallback"]
    assert "Взять карту" in fallback
    assert "Взять анализы" in fallback
