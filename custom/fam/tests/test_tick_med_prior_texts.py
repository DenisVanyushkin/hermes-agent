"""Design spec 2026-07-29 (docs/2026-07-29-med-reminder-gating-design.md,
S5): raw for kind='med' should carry "одна-две предыдущие формулировки
для этой дозы (из payload'ов gate.sent)" so the rewrite LLM has
something concrete to vary against -- the same problem
gate.prior_texts_today already solves for kind='reminder', but keyed on
payload.raw.intake_id instead of raw.event_id, and living in tick.py
(not gate.py) since intake_id is a meds/tick concept.

tick._med_prior_texts(conn, intake_id, now_utc) is the helper under
test: last one or two DELIVERED (gate.sent) texts for this exact
intake, most recent first, scanning only audit_log rows inside today's
Asia/Almaty day (doses are same-day by construction; audit_log holds
22k+ rows and this runs every minute tick).
"""
import json

from fam import tick

# 2026-07-20, Asia/Almaty = UTC+5 (no DST). 16:00 Almaty.
NOW = "2026-07-20T11:00:00+00:00"


def _seed_gate_sent(db, ts_utc, kind="med", raw=None, final=None):
    payload = {"kind": kind}
    if raw is not None:
        payload["raw"] = raw
    if final is not None:
        payload["final"] = final
    db.execute(
        "INSERT INTO audit_log(ts_utc, kind, actor, payload) VALUES(?,?,?,?)",
        (ts_utc, "gate.sent", "test", json.dumps(payload, ensure_ascii=False)),
    )


def test_returns_this_intakes_finals_most_recent_first(db):
    _seed_gate_sent(db, "2026-07-20T05:00:00+00:00",
                     raw={"intake_id": 7}, final="Первое.")
    _seed_gate_sent(db, "2026-07-20T06:00:00+00:00",
                     raw={"intake_id": 7}, final="Второе.")
    db.commit()
    assert tick._med_prior_texts(db, 7, NOW) == ["Второе.", "Первое."]


def test_empty_when_no_prior_sends(db):
    db.commit()
    assert tick._med_prior_texts(db, 7, NOW) == []


def test_ignores_other_intakes(db):
    _seed_gate_sent(db, "2026-07-20T05:00:00+00:00",
                     raw={"intake_id": 8}, final="Чужая доза.")
    db.commit()
    assert tick._med_prior_texts(db, 7, NOW) == []


def test_ignores_rows_missing_intake_id(db):
    # Verified in production: older med gate.sent rows have raw keys
    # ["dose", "mode", "name"] only, no intake_id at all -- must not be
    # heuristically matched by name, just skipped.
    _seed_gate_sent(db, "2026-07-20T05:00:00+00:00",
                     raw={"dose": "1 таб", "mode": "take", "name": "Магний"},
                     final="Пора принять Магний.")
    db.commit()
    assert tick._med_prior_texts(db, 7, NOW) == []


def test_ignores_yesterdays_row(db):
    _seed_gate_sent(db, "2026-07-19T05:00:00+00:00",
                     raw={"intake_id": 7}, final="Вчера.")
    db.commit()
    assert tick._med_prior_texts(db, 7, NOW) == []


def test_ignores_malformed_payload_json(db):
    db.execute(
        "INSERT INTO audit_log(ts_utc, kind, actor, payload) VALUES(?,?,?,?)",
        ("2026-07-20T05:00:00+00:00", "gate.sent", "test", "{not valid json"),
    )
    _seed_gate_sent(db, "2026-07-20T06:00:00+00:00",
                     raw={"intake_id": 7}, final="Валидная.")
    db.commit()
    assert tick._med_prior_texts(db, 7, NOW) == ["Валидная."]


def test_ignores_non_med_gate_sent_rows(db):
    _seed_gate_sent(db, "2026-07-20T05:00:00+00:00", kind="reminder",
                     raw={"intake_id": 7}, final="Не то напоминание.")
    db.commit()
    assert tick._med_prior_texts(db, 7, NOW) == []


def test_caps_at_two_most_recent(db):
    _seed_gate_sent(db, "2026-07-20T03:00:00+00:00",
                     raw={"intake_id": 7}, final="Первое.")
    _seed_gate_sent(db, "2026-07-20T04:00:00+00:00",
                     raw={"intake_id": 7}, final="Второе.")
    _seed_gate_sent(db, "2026-07-20T05:00:00+00:00",
                     raw={"intake_id": 7}, final="Третье.")
    db.commit()
    assert tick._med_prior_texts(db, 7, NOW) == ["Третье.", "Второе."]
