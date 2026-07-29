"""Reaction acks: emoji -> ack mapping, correlation, idempotency."""
import io
import json

import pytest

from fam import audit, cal, gate, meds, people, react, rem


def _audit_kinds(db, kind):
    return [json.loads(r["payload"]) for r in db.execute(
        "SELECT payload FROM audit_log WHERE kind=? ORDER BY id", (kind,))]


# ---- emoji normalization ----

@pytest.mark.parametrize("raw,expected", [
    ("\U0001F44D", True),            # 👍
    ("\U0001F44D\U0001F3FD", True),  # 👍 skin tone
    ("❤️", True),                    # heart + VS16
    ("💪", True),
    ("✅", True),
])
def test_confirm_emoji_variants_normalize(raw, expected):
    assert (react._normalize_emoji(raw) in react.EMOJI_CONFIRM) is expected


def test_skip_emoji_variants_normalize():
    assert react._normalize_emoji("👎") in react.EMOJI_SKIP
    assert react._normalize_emoji("❌") in react.EMOJI_SKIP


# ---- fixtures ----

@pytest.fixture()
def event_with_chain(db):
    rem.seed_default_rules(db)
    db.commit()
    ev = cal.add(db, "Тренировка", "2037-07-20T05:00:00+00:00")
    rem.regenerate(db, ev["id"])
    db.commit()
    return ev


@pytest.fixture()
def pending_intake(db):
    med_id = meds.add(db, name="Мисол", dose="1 таблетка",
                      times=["09:00"], remaining=5, threshold=1)
    db.execute(
        "INSERT INTO med_intakes(med_id, plan_ts_utc, status, created_at) "
        "VALUES (?,?,'pending',?)",
        (med_id, "2026-07-22T04:00:00+00:00", "2026-07-22T00:00:00+00:00"))
    db.commit()
    return db.execute("SELECT * FROM med_intakes ORDER BY id DESC").fetchone()


# ---- correlation table ----

def test_record_sent_persists_mapping(db):
    react.record_sent(db, "WAMID1", "reminder", 7, event_id=3,
                      chat_jid="whatsapp:+7701")
    db.commit()
    row = db.execute("SELECT * FROM sent_messages").fetchone()
    assert (row["wa_message_id"], row["kind"], row["ref_id"],
            row["event_id"], row["ack_status"]) == ("WAMID1", "reminder", 7, 3, "none")


def test_record_sent_is_idempotent_on_repeated_id(db):
    react.record_sent(db, "WAMID1", "med", 1)
    react.record_sent(db, "WAMID1", "med", 1)
    db.commit()
    assert db.execute("SELECT COUNT(*) c FROM sent_messages").fetchone()["c"] == 1


# ---- unknown / ignored ----

def test_reaction_on_unrecorded_message_is_not_an_ack(db):
    out = react.handle(db, "NOPE", "👍")
    assert out["result"] == "unknown_message"
    assert _audit_kinds(db, "react.handle") == []


def test_unmapped_emoji_is_ignored(db):
    react.record_sent(db, "M1", "med", 1)
    out = react.handle(db, "M1", "🤔")
    assert out["result"] == "ignored" and out["reason"] == "unmapped_emoji"
    assert db.execute(
        "SELECT ack_status FROM sent_messages").fetchone()["ack_status"] == "none"


def test_reaction_removal_is_ignored(db):
    react.record_sent(db, "M1", "med", 1)
    out = react.handle(db, "M1", "👍", removal=True)
    assert out["result"] == "ignored" and out["reason"] == "removal"


# ---- reminder chains ----

def test_thumbs_up_acks_whole_reminder_chain(db, event_with_chain):
    ev_id = event_with_chain["id"]
    stages = db.execute(
        "SELECT id FROM reminders WHERE event_id=?", (ev_id,)).fetchall()
    assert len(stages) > 1, "fixture must produce a multi-stage chain"
    react.record_sent(db, "R1", "reminder", stages[0]["id"], event_id=ev_id)
    react.record_sent(db, "R2", "reminder", stages[1]["id"], event_id=ev_id)

    out = react.handle(db, "R1", "👍")

    assert out["result"] == "confirmed"
    assert db.execute(
        "SELECT COUNT(*) c FROM reminders WHERE event_id=? AND status='pending'",
        (ev_id,)).fetchone()["c"] == 0
    # every recorded message of the chain flips, so a later reaction on a
    # sibling stage reads as already_acked rather than re-acking
    assert react.handle(db, "R2", "👍")["result"] == "already_acked"


def test_thumbs_down_cancels_reminder_chain(db, event_with_chain):
    ev_id = event_with_chain["id"]
    rid = db.execute("SELECT id FROM reminders WHERE event_id=?",
                     (ev_id,)).fetchone()["id"]
    react.record_sent(db, "R1", "reminder", rid, event_id=ev_id)

    out = react.handle(db, "R1", "👎")

    assert out["result"] == "skipped"
    assert db.execute(
        "SELECT COUNT(*) c FROM reminders WHERE event_id=? AND status='cancelled'",
        (ev_id,)).fetchone()["c"] > 0


# ---- meds ----

def test_thumbs_up_takes_the_dose_and_decrements(db, pending_intake):
    react.record_sent(db, "M1", "med", pending_intake["id"])

    out = react.handle(db, "M1", "👍")

    assert out["result"] == "confirmed"
    row = db.execute("SELECT * FROM med_intakes WHERE id=?",
                     (pending_intake["id"],)).fetchone()
    assert row["status"] == "taken"
    assert row["series_next_utc"] is None       # +45min series stops
    assert db.execute("SELECT remaining FROM meds WHERE id=?",
                      (pending_intake["med_id"],)).fetchone()["remaining"] == 4


def test_thumbs_down_skips_only_this_dose(db, pending_intake):
    react.record_sent(db, "M1", "med", pending_intake["id"])

    out = react.handle(db, "M1", "👎")

    assert out["result"] == "skipped"
    assert db.execute("SELECT status FROM med_intakes WHERE id=?",
                      (pending_intake["id"],)).fetchone()["status"] == "skipped"
    assert db.execute("SELECT remaining FROM meds WHERE id=?",
                      (pending_intake["med_id"],)).fetchone()["remaining"] == 5


def test_double_thumbs_up_does_not_double_decrement(db, pending_intake):
    react.record_sent(db, "M1", "med", pending_intake["id"])
    react.handle(db, "M1", "👍")

    out = react.handle(db, "M1", "👍")

    assert out["result"] == "already_acked"
    assert db.execute("SELECT remaining FROM meds WHERE id=?",
                      (pending_intake["med_id"],)).fetchone()["remaining"] == 4
    assert len(_audit_kinds(db, "meds.take")) == 1


def test_reaction_after_verbal_ack_is_idempotent_success(db, pending_intake):
    """Amina says "выпила" (skill path) and *also* reacts 👍: the intake
    already left 'pending', so meds.take raises -- that must read as
    already_acked, not as a failure the adapter alerts Denis about."""
    meds.take(db, pending_intake["id"])
    db.commit()
    react.record_sent(db, "M1", "med", pending_intake["id"])

    out = react.handle(db, "M1", "👍")

    assert out["result"] == "already_acked" and out["reason"] == "not_pending"
    assert db.execute("SELECT remaining FROM meds WHERE id=?",
                      (pending_intake["med_id"],)).fetchone()["remaining"] == 4


def test_every_handled_outcome_is_audited(db, pending_intake):
    react.record_sent(db, "M1", "med", pending_intake["id"])
    react.handle(db, "M1", "🤔")
    react.handle(db, "M1", "👍")
    kinds = [p["result"] for p in _audit_kinds(db, "react.handle")]
    assert kinds == ["ignored", "confirmed"]


# ---- hook I/O contract ----

def _run_hook(db, event):
    out = io.StringIO()
    rc = react.run_hook(stdin=io.StringIO(json.dumps(event)), stdout=out,
                        connect=lambda: db)
    return rc, json.loads(out.getvalue())


def test_hook_asks_for_feedback_reaction_on_ack(db, pending_intake):
    react.record_sent(db, "M1", "med", pending_intake["id"])
    rc, payload = _run_hook(db, {"target_message_id": "M1", "emoji": "👍"})
    assert rc == 0 and payload["react"] == react.FEEDBACK_EMOJI


def test_hook_stays_silent_on_unknown_message(db):
    rc, payload = _run_hook(db, {"target_message_id": "ZZZ", "emoji": "👍"})
    assert rc == 0 and "react" not in payload


def test_hook_rejects_malformed_event(db):
    rc = react.run_hook(stdin=io.StringIO("not json"), stdout=io.StringIO(),
                        connect=lambda: db)
    assert rc == 2


# ---- gate wiring ----

def test_deliver_records_message_id_for_reactable_kinds(db, monkeypatch):
    cfg = gate.load_config()
    monkeypatch.setattr(gate, "_call_rewrite", lambda *a, **k: "Пора принять мисол")
    monkeypatch.setattr(gate, "_call_send", lambda *a, **k: (True, "WAMID9"))

    gate.deliver(db, "med", {"mode": "take", "name": "Мисол"}, "fallback", cfg,
                 force=True, sent_ref={"kind": "med", "ref_id": 42})
    db.commit()

    row = db.execute("SELECT * FROM sent_messages").fetchone()
    assert (row["wa_message_id"], row["kind"], row["ref_id"]) == ("WAMID9", "med", 42)


def test_deliver_without_message_id_still_sends_and_audits(db, monkeypatch):
    cfg = gate.load_config()
    monkeypatch.setattr(gate, "_call_rewrite", lambda *a, **k: "текст")
    monkeypatch.setattr(gate, "_call_send", lambda *a, **k: (True, None))

    status = gate.deliver(db, "med", {"mode": "take", "name": "Мисол"},
                          "fallback", cfg, force=True,
                          sent_ref={"kind": "med", "ref_id": 42})
    db.commit()

    assert status == "sent"
    assert db.execute("SELECT COUNT(*) c FROM sent_messages").fetchone()["c"] == 0
    assert len(_audit_kinds(db, "gate.no_msgid")) == 1


def test_reminder_tick_passes_a_sent_ref(db, event_with_chain, monkeypatch):
    """The correlation row is only ever written when tick tells deliver
    which reminder the message belongs to -- guard the wiring itself, not
    just gate's half of it."""
    from fam import tick
    seen = []

    def fake_deliver(conn, kind, raw, human_fallback, cfg, force=False,
                     now_utc=None, sent_ref=None):
        seen.append((kind, sent_ref))
        return "sent"

    monkeypatch.setattr(tick.gate, "deliver", fake_deliver)
    row = db.execute(
        "SELECT id, fire_at_utc FROM reminders WHERE event_id=? "
        "ORDER BY fire_at_utc", (event_with_chain["id"],)).fetchone()
    db.commit()

    tick.reminders(db, now_utc=row["fire_at_utc"], cfg=gate.load_config())

    refs = [ref for kind, ref in seen if kind == "reminder"]
    assert refs, "the fixture's chain must produce at least one due reminder"
    assert refs[0]["kind"] == "reminder"
    assert refs[0]["event_id"] == event_with_chain["id"]


def test_med_tick_passes_a_sent_ref(db, pending_intake, monkeypatch):
    from fam import tick
    seen = []

    def fake_deliver(conn, kind, raw, human_fallback, cfg, force=False,
                     now_utc=None, sent_ref=None):
        seen.append((kind, sent_ref))
        return "sent"

    monkeypatch.setattr(tick.gate, "deliver", fake_deliver)
    db.execute("UPDATE med_intakes SET series_next_utc=plan_ts_utc WHERE id=?",
               (pending_intake["id"],))
    db.commit()

    tick.reminders(db, now_utc="2026-07-22T04:00:00+00:00",
                   cfg=gate.load_config())

    refs = [ref for kind, ref in seen if kind == "med"]
    assert refs and refs[0] == {"kind": "med", "ref_id": pending_intake["id"]}


def test_parse_message_id_tolerates_garbage():
    assert gate._parse_message_id('{"message_id": "X1"}') == "X1"
    assert gate._parse_message_id("not json") is None
    assert gate._parse_message_id('{"message_id": null}') is None
    assert gate._parse_message_id("") is None


# ---- hook verdict: explicit `handled` field ----

def test_hook_verdict_marks_confirmed_as_handled(db, pending_intake):
    """A reaction that acked something must tell the adapter to stop."""
    react.record_sent(db, "M1", "med", pending_intake["id"])
    rc, payload = _run_hook(db, {"target_message_id": "M1", "emoji": "👍"})
    assert rc == 0
    assert payload["handled"] is True
    assert payload["react"] == "✅"
    assert payload["result"] == "confirmed"


def test_hook_verdict_marks_unknown_message_as_not_handled(db):
    """Nothing to ack -> the adapter is free to route it to the agent."""
    rc, payload = _run_hook(db, {"target_message_id": "NOPE", "emoji": "👍"})
    assert rc == 0
    assert payload["handled"] is False
    assert "react" not in payload
    assert payload["result"] == "unknown_message"


def test_hook_verdict_marks_ignored_as_not_handled(db, pending_intake):
    """An unmapped emoji on a known reminder is dialogue material, not an ack."""
    react.record_sent(db, "M2", "med", pending_intake["id"])
    rc, payload = _run_hook(db, {"target_message_id": "M2", "emoji": "😂"})
    assert rc == 0
    assert payload["handled"] is False
    assert payload["result"] == "ignored"


def test_hook_verdict_marks_already_acked_as_handled(db, pending_intake):
    """A repeat 👍 stays a fast-path success -- do not wake the agent twice."""
    react.record_sent(db, "M3", "med", pending_intake["id"])
    rc1, first = _run_hook(db, {"target_message_id": "M3", "emoji": "👍"})
    assert rc1 == 0
    assert first["handled"] is True
    rc2, second = _run_hook(db, {"target_message_id": "M3", "emoji": "👍"})
    assert rc2 == 0
    assert second["handled"] is True
    assert second["result"] == "already_acked"
