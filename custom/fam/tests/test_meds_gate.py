"""Sleep- и away-гейты напоминаний о лекарствах.

Спека: docs/2026-07-29-med-reminder-gating-design.md

Следует конвенции test_tick_meds_series.py: gate.deliver
монkeypatch-ится FakeDeliver'ом, реальный hermes-субпроцесс не
трогается.
"""
import json
import sqlite3

import pytest

from fam import gate


def test_gate_reason_column_exists(db):
    cols = {r[1] for r in db.execute("PRAGMA table_info(med_intakes)")}
    assert "gate_reason" in cols


def test_sent_message_refs_table_exists(db):
    cols = {r[1] for r in db.execute("PRAGMA table_info(sent_message_refs)")}
    assert cols == {"id", "sent_message_id", "kind", "ref_id"}


def test_new_config_defaults_present():
    d = gate.CONFIG_DEFAULTS
    assert d["med_wake_gate_enabled"] is True
    assert d["med_wake_gate_until"] == "12:00"
    assert d["med_away_gate_enabled"] is True
    assert d["med_away_gate_until"] == "21:00"
    assert d["med_gate_recheck_min"] == 10
    assert d["med_snooze_min"] == 60


def test_example_config_mirrors_new_keys():
    import pathlib
    here = pathlib.Path(__file__).resolve().parent.parent
    cfg = json.loads((here / "fam-config.example.json").read_text())
    for key in ("med_wake_gate_enabled", "med_wake_gate_until",
                "med_away_gate_enabled", "med_away_gate_until",
                "med_gate_recheck_min", "med_snooze_min",
                "whereami_home_radius_km", "whereami_car_fresh_min"):
        assert key in cfg, key


from fam import audit, presence

# 2026-07-20, Алматы = UTC+5. 10:00 UTC = 15:00 Алматы.
NOW = "2026-07-20T10:00:00+00:00"
# 02:00 UTC = 07:00 Алматы того же дня.
EARLY = "2026-07-20T02:00:00+00:00"
# Вчера по Алматы: 18:00 UTC 19-го = 23:00 Алматы 19-го.
YESTERDAY = "2026-07-19T18:00:00+00:00"

CFG = {
    "target": "whatsapp:+77782110625",
    "state_db_path": "/nonexistent/state.db",
    "med_wake_gate_enabled": True,
    "med_wake_gate_until": "12:00",
    "med_away_gate_enabled": True,
    "med_away_gate_until": "21:00",
    "med_gate_recheck_min": 10,
    "med_snooze_min": 60,
    "med_repeat_min": 45,
    "road_home_lat": 43.197391,
    "road_home_lon": 76.872737,
    "whereami_home_radius_km": 0.3,
    "whereami_car_fresh_min": 20,
}


def _audit_at(db, kind, ts_utc):
    db.execute(
        "INSERT INTO audit_log(ts_utc, kind, actor, payload) VALUES(?,?,?,?)",
        (ts_utc, kind, "agent", "{}"))
    db.commit()


def test_awake_none_without_any_signal(db):
    assert presence.awake_since(db, CFG, NOW) is None


@pytest.mark.parametrize("kind", [
    "meds.take", "meds.skip", "meds.defer", "rem.ack_chain",
    "react.handle", "cal.add", "shopping.add", "goals.add",
])
def test_awake_from_each_signal_kind(db, kind):
    _audit_at(db, kind, EARLY)
    assert presence.awake_since(db, CFG, NOW) == EARLY


def test_awake_ignores_yesterday(db):
    _audit_at(db, "meds.take", YESTERDAY)
    assert presence.awake_since(db, CFG, NOW) is None


def test_awake_ignores_tick_and_gate_kinds(db):
    # Эти пишет сам тик -- они не признак того, что Амина проснулась.
    for kind in ("tick.reminders", "tick.med", "gate.sent", "tick.digest"):
        _audit_at(db, kind, EARLY)
    assert presence.awake_since(db, CFG, NOW) is None


def test_awake_returns_earliest_signal(db):
    _audit_at(db, "cal.add", "2026-07-20T04:00:00+00:00")
    _audit_at(db, "meds.take", EARLY)
    assert presence.awake_since(db, CFG, NOW) == EARLY


def test_awake_survives_missing_state_db(db):
    # state_db_path указывает в никуда -- источник "входящее сообщение"
    # обязан деградировать в "нет сигнала", а не бросить.
    assert presence.awake_since(db, CFG, NOW) is None


# --- Fix round 1: cover the state.db inbound-message success path. ---
#
# The tests above only exercise `_inbound_message_ts`'s "file absent"
# branch. These cover the real read: gateway_routing lookup + entry_json
# JSON parse, the epoch(messages.timestamp)-vs-ISO(audit_log.ts_utc)
# comparison inside the cross-source min(), and the epoch->ISO
# conversion at the end. Schema below mirrors the columns
# presence._inbound_message_ts actually queries, verified against
# `PRAGMA table_info(gateway_routing|messages)` on the real
# /home/denis/.hermes/state.db on the host.

SESSION_KEY = "agent:main:whatsapp:dm:77782110625"
SESSION_ID = "20260720_010000_test0001"


def _build_state_db(tmp_path, entry_json_by_key=None, messages=()):
    path = tmp_path / "state.db"
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE gateway_routing ("
        "scope TEXT NOT NULL DEFAULT '', session_key TEXT NOT NULL, "
        "entry_json TEXT NOT NULL, updated_at REAL NOT NULL)")
    conn.execute(
        "CREATE TABLE messages ("
        "id INTEGER PRIMARY KEY, session_id TEXT NOT NULL, "
        "role TEXT NOT NULL, timestamp REAL NOT NULL)")
    for key, entry_json in (entry_json_by_key or {}).items():
        conn.execute(
            "INSERT INTO gateway_routing(session_key, entry_json, updated_at) "
            "VALUES(?,?,0)", (key, entry_json))
    for session_id, role, ts in messages:
        conn.execute(
            "INSERT INTO messages(session_id, role, timestamp) VALUES(?,?,?)",
            (session_id, role, ts))
    conn.commit()
    conn.close()
    return str(path)


def _epoch(iso):
    from datetime import datetime
    return datetime.fromisoformat(iso).timestamp()


def test_awake_from_inbound_message_today(tmp_path, db):
    path = _build_state_db(
        tmp_path,
        entry_json_by_key={SESSION_KEY: json.dumps({"session_id": SESSION_ID})},
        messages=[(SESSION_ID, "user", _epoch(EARLY))])
    cfg = dict(CFG, state_db_path=path)
    assert presence.awake_since(db, cfg, NOW) == EARLY


def test_awake_ignores_inbound_message_from_yesterday(tmp_path, db):
    path = _build_state_db(
        tmp_path,
        entry_json_by_key={SESSION_KEY: json.dumps({"session_id": SESSION_ID})},
        messages=[(SESSION_ID, "user", _epoch(YESTERDAY))])
    cfg = dict(CFG, state_db_path=path)
    assert presence.awake_since(db, cfg, NOW) is None


def test_awake_cross_source_min_picks_earlier_inbound(tmp_path, db):
    # Inbound message (epoch) earlier than an audit-log signal (ISO
    # string). If the comparison ever regressed to comparing the raw
    # epoch float against the ISO string, this would break -- either by
    # raising or by silently picking the wrong one.
    inbound_iso = "2026-07-20T01:00:00+00:00"
    _audit_at(db, "meds.take", EARLY)  # 02:00 UTC -- later than inbound
    path = _build_state_db(
        tmp_path,
        entry_json_by_key={SESSION_KEY: json.dumps({"session_id": SESSION_ID})},
        messages=[(SESSION_ID, "user", _epoch(inbound_iso))])
    cfg = dict(CFG, state_db_path=path)
    assert presence.awake_since(db, cfg, NOW) == inbound_iso


def test_awake_none_when_no_matching_routing_row(tmp_path, db):
    path = _build_state_db(
        tmp_path,
        entry_json_by_key={"agent:main:whatsapp:dm:00000000000":
                            json.dumps({"session_id": "other"})},
        messages=[("other", "user", _epoch(EARLY))])
    cfg = dict(CFG, state_db_path=path)
    assert presence.awake_since(db, cfg, NOW) is None


def test_awake_survives_non_object_entry_json(tmp_path, db):
    # entry_json is valid JSON but not an object -- json.loads(...).get(...)
    # would raise AttributeError. Must degrade to "no signal", never raise:
    # this runs inside a minute-tick that must not die.
    path = _build_state_db(
        tmp_path,
        entry_json_by_key={SESSION_KEY: json.dumps([1, 2, 3])},
        messages=[(SESSION_ID, "user", _epoch(EARLY))])
    cfg = dict(CFG, state_db_path=path)
    assert presence.awake_since(db, cfg, NOW) is None
