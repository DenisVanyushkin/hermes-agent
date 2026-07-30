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


def test_whereami_tunables_are_real_config_defaults():
    """Final review, Should-fix 8: ключи были только в
    fam-config.example.json, а load_config мержит лишь CONFIG_DEFAULTS --
    правка примера не влияла ни на что."""
    d = gate.CONFIG_DEFAULTS
    assert d["whereami_home_radius_km"] == 0.3
    assert d["whereami_car_fresh_min"] == 20


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


HOME_LAT, HOME_LON = 43.197391, 76.872737
# ~6 км от дома.
AWAY_LAT, AWAY_LON = 43.250000, 76.900000


def _add_place(db, name, lat, lon):
    cur = db.execute(
        "INSERT INTO places(name, address, lat, lon, created_at) "
        "VALUES(?,?,?,?,?)", (name, "", lat, lon, NOW))
    return cur.lastrowid


def _add_event(db, title, start_utc, end_utc, place_id=None, travel_min=None):
    cur = db.execute(
        "INSERT INTO events(title, start_utc, end_utc, place_id, status, "
        "travel_min, created_at, updated_at) VALUES(?,?,?,?,'active',?,?,?)",
        (title, start_utc, end_utc, place_id, travel_min, NOW, NOW))
    return cur.lastrowid


_AUTO_GPS_TS = object()


def _add_car_metric(db, ts_utc, lat, lon, gps_ts=_AUTO_GPS_TS):
    """Строка телеметрии в той форме, которую производит прод.

    gps_ts -- Unix-эпоха INTEGER (fam.car пишет StarLine position.ts,
    колонка объявлена INTEGER). Раньше этот хелпер по умолчанию писал
    сюда ISO-СТРОКУ, форму, которой прод не производит никогда, из-за
    чего все car-GPS тесты проходили против несуществующей формы данных
    и не замечали, что SQL-сравнение эпохи со строкой не пропускает ни
    одной реальной строки. gps_ts=None пишет NULL (фоллбэк на ts_utc).
    """
    from datetime import datetime as _dt
    if gps_ts is _AUTO_GPS_TS:
        gps_ts = int(_dt.fromisoformat(ts_utc).timestamp())
    db.execute(
        "INSERT INTO car_metrics(ts_utc, gps_lat, gps_lon, gps_ts) "
        "VALUES(?,?,?,?)", (ts_utc, lat, lon, gps_ts))
    db.commit()


def test_away_false_with_no_evidence(db):
    away, reason, _ = presence.is_away(db, CFG, NOW)
    assert away is False
    assert reason == "home_or_unknown"


def test_away_true_during_event_at_remote_place(db):
    pid = _add_place(db, "Зал", AWAY_LAT, AWAY_LON)
    _add_event(db, "Тренировка", "2026-07-20T09:30:00+00:00",
               "2026-07-20T10:30:00+00:00", place_id=pid, travel_min=20)
    db.commit()

    away, reason, expected_home = presence.is_away(db, CFG, NOW)
    assert away is True
    assert reason == "event"
    # конец 10:30 UTC + 20 минут дороги
    assert expected_home == "2026-07-20T10:50:00+00:00"


def test_away_false_during_event_at_home_place(db):
    pid = _add_place(db, "Дом", HOME_LAT, HOME_LON)
    _add_event(db, "Уборка", "2026-07-20T09:30:00+00:00",
               "2026-07-20T10:30:00+00:00", place_id=pid)
    db.commit()
    assert presence.is_away(db, CFG, NOW)[0] is False


def test_away_false_for_event_without_place(db):
    # Событие без места отлучкой не считается -- выбор Дениса 2026-07-29.
    _add_event(db, "Созвон", "2026-07-20T09:30:00+00:00",
               "2026-07-20T10:30:00+00:00", place_id=None)
    db.commit()
    assert presence.is_away(db, CFG, NOW)[0] is False


def test_away_false_for_event_already_over(db):
    pid = _add_place(db, "Зал", AWAY_LAT, AWAY_LON)
    _add_event(db, "Тренировка", "2026-07-20T07:00:00+00:00",
               "2026-07-20T08:00:00+00:00", place_id=pid)
    db.commit()
    assert presence.is_away(db, CFG, NOW)[0] is False


def test_away_true_on_fresh_car_gps_far_from_home(db):
    _add_car_metric(db, "2026-07-20T09:55:00+00:00", AWAY_LAT, AWAY_LON)
    away, reason, expected_home = presence.is_away(db, CFG, NOW)
    assert away is True
    assert reason == "car_gps"
    assert expected_home is None


def test_car_gps_is_stored_as_an_epoch_integer(db):
    """Форма данных, вокруг которой всё и сломалось: gps_ts -- INTEGER.
    SQLite ранжирует INTEGER ниже любого TEXT, поэтому сравнение с
    ISO-cutoff отбрасывало ровно все реальные строки."""
    _add_car_metric(db, "2026-07-20T09:55:00+00:00", AWAY_LAT, AWAY_LON)
    kind = db.execute(
        "SELECT typeof(gps_ts) FROM car_metrics").fetchone()[0]
    assert kind == "integer"
    assert presence.is_away(db, CFG, NOW)[1] == "car_gps"


def test_away_true_on_fresh_car_gps_with_null_gps_ts(db):
    # gps_ts NULL (StarLine не отдал position.ts) -- свежесть считается
    # по ts_utc как по ISO-строке.
    _add_car_metric(db, "2026-07-20T09:55:00+00:00", AWAY_LAT, AWAY_LON,
                    gps_ts=None)
    away, reason, _ = presence.is_away(db, CFG, NOW)
    assert away is True
    assert reason == "car_gps"


def test_away_false_on_stale_car_gps_with_null_gps_ts(db):
    _add_car_metric(db, "2026-07-20T09:00:00+00:00", AWAY_LAT, AWAY_LON,
                    gps_ts=None)
    assert presence.is_away(db, CFG, NOW)[0] is False


def test_fresh_far_car_gps_wins_over_stale_far_row(db):
    # Более свежий фикс у дома перебивает старый далёкий: выигрывает
    # самый свежий фикс, а не первый подходящий.
    _add_car_metric(db, "2026-07-20T09:50:00+00:00", AWAY_LAT, AWAY_LON)
    _add_car_metric(db, "2026-07-20T09:58:00+00:00", HOME_LAT, HOME_LON)
    assert presence.is_away(db, CFG, NOW)[0] is False


def test_nearby_event_does_not_mask_a_far_car_gps(db):
    """Отложенный в Task 3 тест, ставший несущим: идущее событие в месте
    РЯДОМ с домом не должно затенять свежий далёкий фикс машины --
    лестница обязана провалиться на ступень car_gps, а не ответить
    "дома" по первому же совпадению."""
    pid = _add_place(db, "Двор", HOME_LAT, HOME_LON)
    _add_event(db, "Уборка", "2026-07-20T09:30:00+00:00",
               "2026-07-20T10:30:00+00:00", place_id=pid)
    db.commit()
    _add_car_metric(db, "2026-07-20T09:55:00+00:00", AWAY_LAT, AWAY_LON)

    away, reason, expected_home = presence.is_away(db, CFG, NOW)
    assert away is True
    assert reason == "car_gps"
    assert expected_home is None


def test_parse_treats_naive_timestamps_as_utc(db):
    # Как tick._parse_utc: наивная строка -- это UTC, а не системная
    # зона хоста (иначе границы суток Алматы уезжают вне UTC-хоста).
    assert presence._parse("2026-07-20T10:00:00") == presence._parse(
        "2026-07-20T10:00:00+00:00")


def test_is_away_accepts_a_naive_now(db):
    _add_car_metric(db, "2026-07-20T09:55:00+00:00", AWAY_LAT, AWAY_LON)
    assert presence.is_away(db, CFG, "2026-07-20T10:00:00")[0] is True


def test_away_false_on_stale_car_gps(db):
    # 09:00 при NOW=10:00 -- старше whereami_car_fresh_min (20 мин).
    _add_car_metric(db, "2026-07-20T09:00:00+00:00", AWAY_LAT, AWAY_LON)
    assert presence.is_away(db, CFG, NOW)[0] is False


def test_away_false_when_car_is_at_home(db):
    _add_car_metric(db, "2026-07-20T09:55:00+00:00", HOME_LAT, HOME_LON)
    assert presence.is_away(db, CFG, NOW)[0] is False


def test_away_true_on_unexpired_shared_location(db):
    db.execute(
        "INSERT INTO location_hints(source, lat, lon, label, ts_utc, "
        "expires_utc) VALUES('shared',?,?,'',?,?)",
        (AWAY_LAT, AWAY_LON, "2026-07-20T09:30:00+00:00",
         "2026-07-20T11:00:00+00:00"))
    db.commit()
    away, reason, _ = presence.is_away(db, CFG, NOW)
    assert away is True
    assert reason == "shared_location"


def test_away_false_on_expired_shared_location(db):
    db.execute(
        "INSERT INTO location_hints(source, lat, lon, label, ts_utc, "
        "expires_utc) VALUES('shared',?,?,'',?,?)",
        (AWAY_LAT, AWAY_LON, "2026-07-20T07:00:00+00:00",
         "2026-07-20T09:00:00+00:00"))
    db.commit()
    assert presence.is_away(db, CFG, NOW)[0] is False


from fam import meds, tick


class FakeDeliver:
    def __init__(self):
        self.calls = []
        self.responses = []

    def __call__(self, conn, kind, raw, human_fallback, cfg, force=False,
                 now_utc=None, sent_ref=None):
        self.calls.append({"kind": kind, "raw": raw,
                           "human_fallback": human_fallback,
                           "sent_ref": sent_ref})
        return self.responses.pop(0) if self.responses else "sent"


@pytest.fixture()
def fake_deliver(monkeypatch):
    fd = FakeDeliver()
    monkeypatch.setattr(gate, "deliver", fd)
    return fd


def _pending_intake(db, name="Эутирокс", plan="2026-07-20T04:00:00+00:00",
                    times=("09:00",)):
    """Доза на 09:00 Алматы = 04:00 UTC, готовая к отправке."""
    med_id = meds.add(db, name, list(times), remaining=10)
    cur = db.execute(
        "INSERT INTO med_intakes(med_id, plan_ts_utc, status, "
        "series_next_utc, created_at) VALUES(?,?,'pending',?,?)",
        (med_id, plan, plan, plan))
    db.commit()
    return cur.lastrowid


def _intake(db, intake_id):
    return db.execute("SELECT * FROM med_intakes WHERE id=?",
                      (intake_id,)).fetchone()


# 05:00 UTC = 10:00 Алматы -- утро, доза на 09:00 уже просрочена.
MORNING = "2026-07-20T05:00:00+00:00"
# 07:00 UTC = 12:00 Алматы ровно -- момент сдачи sleep-гейта.
NOON = "2026-07-20T07:00:00+00:00"


def test_sleep_gate_holds_morning_dose_without_signal(db, fake_deliver):
    intake_id = _pending_intake(db)
    tick._meds_series(db, MORNING, CFG)

    assert fake_deliver.calls == []
    row = _intake(db, intake_id)
    assert row["status"] == "pending"
    assert row["gate_reason"] == "asleep"
    # перепроверка через 10 минут, а не через 45
    assert row["series_next_utc"] == "2026-07-20T05:10:00+00:00"


def test_sleep_gate_releases_after_signal(db, fake_deliver):
    intake_id = _pending_intake(db)
    tick._meds_series(db, MORNING, CFG)
    assert fake_deliver.calls == []

    _audit_at(db, "cal.add", "2026-07-20T05:05:00+00:00")
    tick._meds_series(db, "2026-07-20T05:10:00+00:00", CFG)

    assert len(fake_deliver.calls) == 1
    row = _intake(db, intake_id)
    assert row["gate_reason"] is None
    # после реальной отправки -- обычные 45 минут
    assert row["series_next_utc"] == "2026-07-20T05:55:00+00:00"


def test_sleep_gate_gives_up_at_med_wake_gate_until(db, fake_deliver):
    _pending_intake(db)
    tick._meds_series(db, NOON, CFG)
    assert len(fake_deliver.calls) == 1


def test_sleep_gate_ignores_afternoon_dose(db, fake_deliver):
    # Доза на 14:00 Алматы = 09:00 UTC; гейт её не касается.
    _pending_intake(db, name="Магний", plan="2026-07-20T09:00:00+00:00",
                    times=("14:00",))
    tick._meds_series(db, "2026-07-20T09:00:00+00:00", CFG)
    assert len(fake_deliver.calls) == 1


def test_sleep_gate_disabled_by_config(db, fake_deliver):
    cfg = {**CFG, "med_wake_gate_enabled": False}
    _pending_intake(db)
    tick._meds_series(db, MORNING, cfg)
    assert len(fake_deliver.calls) == 1


def test_hold_does_not_spend_budget_or_audit_twice(db, fake_deliver):
    _pending_intake(db)
    tick._meds_series(db, MORNING, CFG)
    tick._meds_series(db, "2026-07-20T05:10:00+00:00", CFG)
    tick._meds_series(db, "2026-07-20T05:20:00+00:00", CFG)

    holds = db.execute(
        "SELECT COUNT(*) FROM audit_log WHERE kind='med.gate_hold'"
    ).fetchone()[0]
    assert holds == 1, "аудит только на переходе, не на каждой перепроверке"
    sent = db.execute(
        "SELECT COUNT(*) FROM audit_log WHERE kind='gate.sent'").fetchone()[0]
    assert sent == 0


# 16:00 UTC = 21:00 Алматы ровно -- момент сдачи away-гейта.
AWAY_GIVEUP = "2026-07-20T16:00:00+00:00"
# 11:00 UTC = 16:00 Алматы -- день, sleep-гейт неприменим.
AFTERNOON = "2026-07-20T11:00:00+00:00"


def test_away_gate_holds_during_remote_event(db, fake_deliver):
    pid = _add_place(db, "Зал", AWAY_LAT, AWAY_LON)
    _add_event(db, "Тренировка", "2026-07-20T10:30:00+00:00",
               "2026-07-20T11:30:00+00:00", place_id=pid, travel_min=20)
    intake_id = _pending_intake(db, name="Магний",
                                plan="2026-07-20T11:00:00+00:00",
                                times=("16:00",))
    tick._meds_series(db, AFTERNOON, CFG)

    assert fake_deliver.calls == []
    row = _intake(db, intake_id)
    assert row["gate_reason"] == "away"
    assert row["series_next_utc"] == "2026-07-20T11:10:00+00:00"


def test_away_gate_releases_when_event_over(db, fake_deliver):
    pid = _add_place(db, "Зал", AWAY_LAT, AWAY_LON)
    _add_event(db, "Тренировка", "2026-07-20T10:30:00+00:00",
               "2026-07-20T11:30:00+00:00", place_id=pid, travel_min=20)
    _pending_intake(db, name="Магний", plan="2026-07-20T11:00:00+00:00",
                    times=("16:00",))
    tick._meds_series(db, AFTERNOON, CFG)
    assert fake_deliver.calls == []

    # 11:40 UTC -- событие кончилось в 11:30
    tick._meds_series(db, "2026-07-20T11:40:00+00:00", CFG)
    assert len(fake_deliver.calls) == 1


def test_away_gate_releases_when_car_returns_home(db, fake_deliver):
    _add_car_metric(db, "2026-07-20T10:55:00+00:00", AWAY_LAT, AWAY_LON)
    _pending_intake(db, name="Магний", plan="2026-07-20T11:00:00+00:00",
                    times=("16:00",))
    tick._meds_series(db, AFTERNOON, CFG)
    assert fake_deliver.calls == []

    _add_car_metric(db, "2026-07-20T11:05:00+00:00", HOME_LAT, HOME_LON)
    tick._meds_series(db, "2026-07-20T11:10:00+00:00", CFG)
    assert len(fake_deliver.calls) == 1


def test_away_gate_gives_up_at_med_away_gate_until(db, fake_deliver):
    pid = _add_place(db, "Зал", AWAY_LAT, AWAY_LON)
    _add_event(db, "Долгая встреча", "2026-07-20T10:00:00+00:00",
               "2026-07-20T18:00:00+00:00", place_id=pid)
    _pending_intake(db, name="Магний", plan="2026-07-20T11:00:00+00:00",
                    times=("16:00",))
    tick._meds_series(db, AWAY_GIVEUP, CFG)
    assert len(fake_deliver.calls) == 1, "в 21:00 гейт обязан сдаться"


def test_away_gate_disabled_by_config(db, fake_deliver):
    cfg = {**CFG, "med_away_gate_enabled": False}
    pid = _add_place(db, "Зал", AWAY_LAT, AWAY_LON)
    _add_event(db, "Тренировка", "2026-07-20T10:30:00+00:00",
               "2026-07-20T11:30:00+00:00", place_id=pid)
    _pending_intake(db, name="Магний", plan="2026-07-20T11:00:00+00:00",
                    times=("16:00",))
    tick._meds_series(db, AFTERNOON, cfg)
    assert len(fake_deliver.calls) == 1


# --- Task 6: склейка доз, освобождённых одним тиком, и групповой ack ---


def test_released_doses_merge_into_one_message(db, fake_deliver):
    a = _pending_intake(db, name="Эутирокс")
    b = _pending_intake(db, name="Магний")
    tick._meds_series(db, MORNING, CFG)
    assert fake_deliver.calls == []

    _audit_at(db, "cal.add", "2026-07-20T05:05:00+00:00")
    tick._meds_series(db, "2026-07-20T05:10:00+00:00", CFG)

    assert len(fake_deliver.calls) == 1
    call = fake_deliver.calls[0]
    assert call["raw"]["mode"] == "take_group"
    assert {i["name"] for i in call["raw"]["items"]} == {"Эутирокс", "Магний"}
    assert sorted(call["sent_ref"]["ref_ids"]) == sorted([a, b])
    assert "Эутирокс" in call["human_fallback"]
    assert "Магний" in call["human_fallback"]


def test_released_doses_advance_series_and_clear_gate_reason(db, fake_deliver):
    a = _pending_intake(db, name="Эутирокс")
    b = _pending_intake(db, name="Магний")
    tick._meds_series(db, MORNING, CFG)
    _audit_at(db, "cal.add", "2026-07-20T05:05:00+00:00")
    tick._meds_series(db, "2026-07-20T05:10:00+00:00", CFG)

    for iid in (a, b):
        row = _intake(db, iid)
        assert row["gate_reason"] is None, iid
        assert row["status"] == "pending", iid
        # после реальной отправки -- обычные 45 минут
        assert row["series_next_utc"] == "2026-07-20T05:55:00+00:00", iid


def test_single_released_dose_stays_a_take_message(db, fake_deliver):
    a = _pending_intake(db, name="Эутирокс")
    tick._meds_series(db, MORNING, CFG)
    assert fake_deliver.calls == []

    _audit_at(db, "cal.add", "2026-07-20T05:05:00+00:00")
    tick._meds_series(db, "2026-07-20T05:10:00+00:00", CFG)

    assert len(fake_deliver.calls) == 1
    call = fake_deliver.calls[0]
    assert call["raw"]["mode"] == "take"
    assert call["raw"]["name"] == "Эутирокс"
    assert call["sent_ref"]["ref_id"] == a
    # одиночная доза не порождает групповых ссылок
    assert not call["sent_ref"].get("ref_ids") or \
        call["sent_ref"]["ref_ids"] == [a]
    assert db.execute(
        "SELECT COUNT(*) FROM sent_message_refs").fetchone()[0] == 0


def test_ordinary_repeats_stay_separate(db, fake_deliver):
    _pending_intake(db, name="Эутирокс", plan="2026-07-20T11:00:00+00:00",
                    times=("16:00",))
    _pending_intake(db, name="Магний", plan="2026-07-20T11:00:00+00:00",
                    times=("16:00",))
    tick._meds_series(db, AFTERNOON, CFG)
    # обе дозы не удерживались -- значит два отдельных сообщения
    assert len(fake_deliver.calls) == 2
    assert all(c["raw"]["mode"] == "take" for c in fake_deliver.calls)


def test_group_send_exception_leaves_doses_held(db, fake_deliver):
    """Путь ИСКЛЮЧЕНИЯ (не то же, что упавшая отправка -- та возвращает
    "error", см. соседний тест ниже): если групповая отправка бросила,
    ни одна доза не должна остаться с очищенным gate_reason -- иначе она
    «отпущена», хотя ничего не ушло."""
    a = _pending_intake(db, name="Эутирокс")
    b = _pending_intake(db, name="Магний")
    tick._meds_series(db, MORNING, CFG)
    _audit_at(db, "cal.add", "2026-07-20T05:05:00+00:00")

    def boom(*args, **kwargs):
        raise RuntimeError("bridge down")

    fake_deliver.calls = []
    import fam.gate as _gate
    orig = _gate.deliver
    _gate.deliver = boom
    try:
        tick._meds_series(db, "2026-07-20T05:10:00+00:00", CFG)
    finally:
        _gate.deliver = orig

    for iid in (a, b):
        row = _intake(db, iid)
        assert row["gate_reason"] == "asleep", iid
        assert row["series_next_utc"] == "2026-07-20T05:10:00+00:00", iid
    assert db.execute(
        "SELECT COUNT(*) FROM audit_log WHERE kind='med.gate_release'"
    ).fetchone()[0] == 0
    assert db.execute(
        "SELECT COUNT(*) FROM audit_log WHERE kind='tick.error'"
    ).fetchone()[0] == 1


def test_group_reaction_acks_every_dose(db):
    from fam import react
    a = _pending_intake(db, name="Эутирокс")
    b = _pending_intake(db, name="Магний")
    react.record_sent(db, "wa1", "med", a, ref_ids=[a, b])
    db.commit()

    out = react.handle(db, "wa1", "\U0001F44D")

    assert out["result"] == "confirmed"
    assert _intake(db, a)["status"] == "taken"
    assert _intake(db, b)["status"] == "taken"


def test_group_reaction_skips_already_acked_member(db):
    from fam import react
    a = _pending_intake(db, name="Эутирокс")
    b = _pending_intake(db, name="Магний")
    meds.take(db, a, now_utc=MORNING)
    react.record_sent(db, "wa1", "med", a, ref_ids=[a, b])
    db.commit()

    out = react.handle(db, "wa1", "\U0001F44D")

    assert out["result"] == "confirmed"
    assert _intake(db, b)["status"] == "taken"


def test_group_reaction_skip_emoji_skips_every_dose(db):
    from fam import react
    a = _pending_intake(db, name="Эутирокс")
    b = _pending_intake(db, name="Магний")
    react.record_sent(db, "wa1", "med", a, ref_ids=[a, b])
    db.commit()

    out = react.handle(db, "wa1", "\U0001F44E")

    assert out["result"] == "skipped"
    assert _intake(db, a)["status"] == "skipped"
    assert _intake(db, b)["status"] == "skipped"


def test_group_reaction_all_members_not_pending_is_already_acked(db):
    from fam import react
    a = _pending_intake(db, name="Эутирокс")
    b = _pending_intake(db, name="Магний")
    meds.take(db, a, now_utc=MORNING)
    meds.take(db, b, now_utc=MORNING)
    react.record_sent(db, "wa1", "med", a, ref_ids=[a, b])
    db.commit()

    out = react.handle(db, "wa1", "\U0001F44D")

    assert out["result"] == "already_acked"
    assert out["reason"] == "not_pending"


def test_group_reaction_is_idempotent_on_repeat(db):
    from fam import react
    a = _pending_intake(db, name="Эутирокс")
    b = _pending_intake(db, name="Магний")
    react.record_sent(db, "wa1", "med", a, ref_ids=[a, b])
    db.commit()

    assert react.handle(db, "wa1", "\U0001F44D")["result"] == "confirmed"
    assert react.handle(db, "wa1", "\U0001F44D")["result"] == "already_acked"


def test_group_ack_after_a_sibling_ack_still_acks_the_other_dose(db):
    """Final review, Blocker 2: 👍 сначала на обычном +45 повторе дозы a
    (её ref_id совпадает с ref_id группового сообщения, потому что
    sent_messages.ref_id -- скаляр и хранит ids[0]), потом 👍 на самом
    групповом сообщении.

    Раньше fan-out первого ack помечал ack_status='confirmed' И на
    групповом сообщении, поэтому второй 👍 коротил на проверке
    ack_status: доза b оставалась pending, Амина видела ✅ и считала
    обе дозы отмеченными, а полуночный closeout записывал b как
    missed -- ложная медицинская запись из явного положительного
    действия пользователя."""
    from fam import react
    a = _pending_intake(db, name="Эутирокс")
    b = _pending_intake(db, name="Магний")
    react.record_sent(db, "wa-grp", "med", a, ref_ids=[a, b])
    react.record_sent(db, "wa-sib", "med", a)      # обычный повтор дозы a
    db.commit()

    first = react.handle(db, "wa-sib", "\U0001F44D")
    assert first["result"] == "confirmed"
    assert _intake(db, a)["status"] == "taken"
    assert _intake(db, b)["status"] == "pending"

    second = react.handle(db, "wa-grp", "\U0001F44D")

    assert second["result"] == "confirmed"
    assert _intake(db, b)["status"] == "taken", (
        "👍 на групповом сообщении обязан отметить все ещё pending дозы")


def test_group_ack_repeat_is_still_idempotent(db):
    # Групповое сообщение больше не полагается на свой ack_status, так
    # что идемпотентность повторного 👍 держится на per-dose пути:
    # ни одна доза не применима -> already_acked/not_pending.
    from fam import react
    a = _pending_intake(db, name="Эутирокс")
    b = _pending_intake(db, name="Магний")
    react.record_sent(db, "wa-grp", "med", a, ref_ids=[a, b])
    db.commit()

    assert react.handle(db, "wa-grp", "\U0001F44D")["result"] == "confirmed"
    again = react.handle(db, "wa-grp", "\U0001F44D")
    assert again["result"] == "already_acked"
    assert again["reason"] == "not_pending"


def test_single_dose_ack_shape_is_unchanged(db):
    # Одиночное сообщение по-прежнему коротит на ack_status и отдаёт
    # ровно ту же форму ответа, что до этой правки.
    from fam import react
    a = _pending_intake(db, name="Эутирокс")
    react.record_sent(db, "wa-one", "med", a)
    db.commit()

    assert react.handle(db, "wa-one", "\U0001F44D")["result"] == "confirmed"
    again = react.handle(db, "wa-one", "\U0001F44D")
    assert again["result"] == "already_acked"
    assert again["ack_status"] == "confirmed"
    assert "reason" not in again


def test_record_sent_single_ref_writes_no_refs_rows(db):
    from fam import react
    a = _pending_intake(db, name="Эутирокс")
    react.record_sent(db, "wa1", "med", a)
    react.record_sent(db, "wa2", "med", a, ref_ids=[a])
    db.commit()
    assert db.execute(
        "SELECT COUNT(*) FROM sent_message_refs").fetchone()[0] == 0


def test_group_send_marks_every_member_acked_in_sent_messages(db):
    from fam import react
    a = _pending_intake(db, name="Эутирокс")
    b = _pending_intake(db, name="Магний")
    # отдельное более раннее сообщение по дозе b (обычный +45 повтор)
    react.record_sent(db, "wa0", "med", b)
    react.record_sent(db, "wa1", "med", a, ref_ids=[a, b])
    db.commit()

    react.handle(db, "wa1", "\U0001F44D")

    statuses = {r["wa_message_id"]: r["ack_status"] for r in db.execute(
        "SELECT wa_message_id, ack_status FROM sent_messages")}
    assert statuses == {"wa0": "confirmed", "wa1": "confirmed"}


# --- Fix round 1: the two seams the first pass left uncovered. ---
#
# 1. gate.deliver does NOT raise when the send fails: _call_send returning
#    (False, None) makes it audit gate.error and RETURN "error". Without a
#    status check the release would commit as if delivered.
# 2. The tick -> gate.deliver -> react.record_sent -> sent_message_refs ->
#    react.handle seam ran with gate.deliver replaced wholesale in every
#    existing test, so the one-line ref_ids passthrough in gate.deliver was
#    never executed by anything.


def _live_cfg():
    """CFG plus the keys real gate.deliver needs (max_len_*, budget, ...)."""
    return {**gate.load_config(), **CFG}


def test_group_send_error_status_leaves_doses_held(db, monkeypatch):
    a = _pending_intake(db, name="Эутирокс")
    b = _pending_intake(db, name="Магний")
    cfg = _live_cfg()
    monkeypatch.setattr(gate, "_call_rewrite", lambda *a, **k: None)
    monkeypatch.setattr(gate, "_call_send", lambda *a, **k: (True, "WAM1"))
    tick._meds_series(db, MORNING, cfg)
    for iid in (a, b):
        assert _intake(db, iid)["gate_reason"] == "asleep"

    # Мост умер: _call_send -> (False, None); deliver возвращает "error",
    # НЕ бросая. Отпускание обязано откатиться целиком.
    _audit_at(db, "cal.add", "2026-07-20T05:05:00+00:00")
    monkeypatch.setattr(gate, "_call_send", lambda *a, **k: (False, None))
    tick._meds_series(db, "2026-07-20T05:10:00+00:00", cfg)

    for iid in (a, b):
        row = _intake(db, iid)
        assert row["gate_reason"] == "asleep", iid
        assert row["status"] == "pending", iid
        # Дозы остались удержанными, но повтор -- через
        # med_gate_recheck_min, а НЕ на следующей же минуте (final
        # review, Should-fix 4): раньше здесь оставалось 05:10, то есть
        # строка была due каждую минуту до полуночи.
        assert row["series_next_utc"] == "2026-07-20T05:20:00+00:00", iid
    assert db.execute(
        "SELECT COUNT(*) FROM audit_log WHERE kind='med.gate_release'"
    ).fetchone()[0] == 0
    assert db.execute(
        "SELECT COUNT(*) FROM sent_messages").fetchone()[0] == 0
    deferred = db.execute(
        "SELECT payload FROM audit_log WHERE kind='tick.med' "
        "ORDER BY id DESC LIMIT 1").fetchone()[0]
    assert "release_deferred" in deferred


def test_release_deferred_retries_after_recheck_not_next_minute(db,
                                                                fake_deliver):
    """Final review, Should-fix 4: неудавшееся отпускание обязано ждать
    med_gate_recheck_min. Раньше откат оставлял series_next_utc на
    значении последнего _gate_hold (уже <= now), и доза была due каждую
    минуту -- один вызов моста, один откат и одна audit-строка в
    минуту."""
    a = _pending_intake(db, name="Эутирокс")
    tick._meds_series(db, MORNING, CFG)              # удержана как asleep
    _audit_at(db, "cal.add", "2026-07-20T05:05:00+00:00")

    fake_deliver.responses = ["error"]
    tick._meds_series(db, "2026-07-20T05:10:00+00:00", CFG)
    assert len(fake_deliver.calls) == 1
    row = _intake(db, a)
    assert row["gate_reason"] == "asleep", "удержание должно выжить откат"
    assert row["series_next_utc"] == "2026-07-20T05:20:00+00:00"

    tick._meds_series(db, "2026-07-20T05:11:00+00:00", CFG)
    assert len(fake_deliver.calls) == 1, "через минуту доза ещё не due"

    tick._meds_series(db, "2026-07-20T05:20:00+00:00", CFG)
    assert len(fake_deliver.calls) == 2, "через 10 минут -- повторная попытка"
    assert _intake(db, a)["gate_reason"] is None


def test_release_deferred_audit_is_throttled_during_an_outage(db,
                                                             fake_deliver):
    """audit_log уже несёт 22k+ tick.reminders; многочасовой простой
    моста не должен дописывать в него строку на каждую перепроверку."""
    a = _pending_intake(db, name="Эутирокс")
    b = _pending_intake(db, name="Магний")
    tick._meds_series(db, MORNING, CFG)
    _audit_at(db, "cal.add", "2026-07-20T05:05:00+00:00")

    fake_deliver.responses = ["error"] * 5
    for minute in (10, 20, 30, 40, 50):
        tick._meds_series(db, f"2026-07-20T05:{minute}:00+00:00", CFG)

    assert len(fake_deliver.calls) == 5, "каждые 10 минут -- одна попытка"
    rows = db.execute(
        "SELECT payload FROM audit_log WHERE kind='tick.med'").fetchall()
    deferred = [r[0] for r in rows if "release_deferred" in r[0]]
    assert len(deferred) == 1, "одна строка на непрерывный простой"
    for iid in (a, b):
        row = _intake(db, iid)
        assert row["gate_reason"] == "asleep", iid
        assert row["series_next_utc"] == "2026-07-20T06:00:00+00:00", iid

    # Простой кончился -> ключ троттлинга снят, следующий простой снова
    # оставит свою строку.
    tick._meds_series(db, "2026-07-20T06:00:00+00:00", CFG)
    assert _intake(db, a)["gate_reason"] is None
    assert db.execute(
        "SELECT COUNT(*) FROM meta WHERE key LIKE 'med_release_deferred:%'"
    ).fetchone()[0] == 0


def test_group_release_end_to_end_through_real_deliver(db, monkeypatch):
    """Полный шов: tick -> gate.deliver -> record_sent ->
    sent_message_refs -> react.handle. Опечатка в проброс ref_ids тихо
    выродила бы групповое сообщение в одиночный ack -- ловится только
    здесь, потому что все остальные тесты подменяют gate.deliver целиком."""
    from fam import react
    a = _pending_intake(db, name="Эутирокс")
    b = _pending_intake(db, name="Магний")
    cfg = _live_cfg()
    sent = []
    monkeypatch.setattr(gate, "_call_rewrite", lambda *a, **k: None)

    def _send(text, _cfg):
        sent.append(text)
        return True, "WAMGRP"

    monkeypatch.setattr(gate, "_call_send", _send)

    tick._meds_series(db, MORNING, cfg)
    assert sent == []
    _audit_at(db, "cal.add", "2026-07-20T05:05:00+00:00")
    tick._meds_series(db, "2026-07-20T05:10:00+00:00", cfg)

    assert len(sent) == 1, "одно сообщение на обе дозы"
    assert "Эутирокс" in sent[0] and "Магний" in sent[0]
    msg = db.execute("SELECT * FROM sent_messages").fetchall()
    assert len(msg) == 1 and msg[0]["wa_message_id"] == "WAMGRP"
    refs = {r["ref_id"] for r in db.execute(
        "SELECT ref_id FROM sent_message_refs WHERE sent_message_id=?",
        (msg[0]["id"],))}
    assert refs == {a, b}

    out = react.handle(db, "WAMGRP", "\U0001F44D")
    assert out["result"] == "confirmed"
    assert _intake(db, a)["status"] == "taken"
    assert _intake(db, b)["status"] == "taken"


def test_list_pending_exposes_gate_reason(db, fake_deliver):
    _pending_intake(db)
    tick._meds_series(db, MORNING, CFG)

    rows = meds.list_pending(db)
    assert len(rows) == 1
    assert rows[0]["gate_reason"] == "asleep"


def test_list_pending_gate_reason_none_when_not_held(db):
    _pending_intake(db)
    rows = meds.list_pending(db)
    assert rows[0]["gate_reason"] is None


# 15:00 UTC = 20:00 Алматы -- followup_local_time.
FOLLOWUP_NOW = "2026-07-20T15:00:00+00:00"
# 16:00 UTC = 21:00 Алматы -- момент сдачи away-гейта.
FOLLOWUP_AFTER_BACKSTOP = "2026-07-20T16:00:00+00:00"


def _away_held_dose(db, fake_deliver):
    """Доза на 16:00 Алматы, удержанная away-гейтом (событие в зале)."""
    pid = _add_place(db, "Зал", AWAY_LAT, AWAY_LON)
    _add_event(db, "Долгая тренировка", "2026-07-20T10:30:00+00:00",
               "2026-07-20T18:00:00+00:00", place_id=pid)
    iid = _pending_intake(db, name="Магний", plan="2026-07-20T11:00:00+00:00",
                          times=("16:00",))
    tick._meds_series(db, AFTERNOON, CFG)
    assert _intake(db, iid)["gate_reason"] == "away"
    fake_deliver.calls = []
    return iid


def test_followup_omits_a_dose_the_away_gate_can_still_hold(db, fake_deliver):
    """Final review, Should-fix 5: в 20:00 away-гейт ещё держит (сдаётся
    в 21:00), поэтому анонс отправил бы на телефон вдали от лекарств
    ровно то сообщение, которое гейт весь день подавлял -- и повторил бы
    его в 21:0x бэкстопом."""
    _away_held_dose(db, fake_deliver)

    tick._followup(db, FOLLOWUP_NOW, CFG)

    held = [c for c in fake_deliver.calls
            if c["kind"] == "followup" and c["raw"].get("held_meds")]
    assert held == [], "удерживаемую away-дозу в 20:00 упоминать нельзя"


def test_followup_mentions_an_away_dose_after_the_backstop(db, fake_deliver):
    # В 21:00 гейт сдался: доза уже не удерживаема, и о ней честно
    # сказать вечером.
    _away_held_dose(db, fake_deliver)

    tick._followup(db, FOLLOWUP_AFTER_BACKSTOP, CFG)

    calls = [c for c in fake_deliver.calls if c["kind"] == "followup"]
    assert len(calls) == 1
    assert [h["name"] for h in calls[0]["raw"]["held_meds"]] == ["Магний"]


def test_followup_ignores_yesterdays_held_dose(db, fake_deliver):
    # Если полуночный тик генерации пропустил ночь, вчерашняя pending
    # строка не должна всплыть в сегодняшнем follow-up с вчерашним
    # временем.
    med_id = meds.add(db, "Эутирокс", ["09:00"], remaining=10)
    db.execute(
        "INSERT INTO med_intakes(med_id, plan_ts_utc, status, "
        "series_next_utc, gate_reason, created_at) "
        "VALUES(?,?,'pending',?,'asleep',?)",
        (med_id, "2026-07-19T04:00:00+00:00", "2026-07-19T04:00:00+00:00",
         "2026-07-19T04:00:00+00:00"))
    db.commit()

    tick._followup(db, FOLLOWUP_NOW, CFG)

    held = [c for c in fake_deliver.calls
            if c["kind"] == "followup" and c["raw"].get("held_meds")]
    assert held == [], "вчерашняя удержанная доза не относится к сегодня"


# --- Should-fix 6: отпускание несёт настоящий attempt_no ---


def test_away_release_carries_the_real_attempt_no(db, fake_deliver):
    """meds.defer не чистит gate_reason, а away-гейт держит ПОВТОРЫ уже
    объявленной дозы, поэтому отпускание почти всегда попытка 2+. Без
    attempt_no в raw gate._build_prompt не добавлял инструкцию
    варьирования -- Task 8 был молча отключён для каждого away-release."""
    from fam import react
    a = _pending_intake(db, name="Магний", plan="2026-07-20T11:00:00+00:00",
                        times=("16:00",))
    # первая отправка этой дозы уже состоялась
    react.record_sent(db, "wa-first", "med", a)
    db.commit()

    pid = _add_place(db, "Зал", AWAY_LAT, AWAY_LON)
    _add_event(db, "Тренировка", "2026-07-20T10:30:00+00:00",
               "2026-07-20T11:30:00+00:00", place_id=pid)
    db.commit()
    tick._meds_series(db, AFTERNOON, CFG)             # удержана как away
    assert fake_deliver.calls == []

    tick._meds_series(db, "2026-07-20T11:40:00+00:00", CFG)   # событие кончилось

    assert len(fake_deliver.calls) == 1
    raw = fake_deliver.calls[0]["raw"]
    assert raw["mode"] == "take"
    assert raw["late"] is True
    assert raw["attempt_no"] == 2
    # формулировка отпускания не менялась
    assert fake_deliver.calls[0]["human_fallback"] == (
        "Магний за 16:00 ещё не отмечено.")


def test_group_release_carries_per_item_attempt_no(db, fake_deliver):
    from fam import react
    a = _pending_intake(db, name="Эутирокс")
    b = _pending_intake(db, name="Магний")
    react.record_sent(db, "wa-first", "med", a)   # у Эутирокса была отправка
    db.commit()

    tick._meds_series(db, MORNING, CFG)
    _audit_at(db, "cal.add", "2026-07-20T05:05:00+00:00")
    tick._meds_series(db, "2026-07-20T05:10:00+00:00", CFG)

    raw = fake_deliver.calls[-1]["raw"]
    assert raw["mode"] == "take_group"
    assert {i["name"]: i["attempt_no"] for i in raw["items"]} == {
        "Эутирокс": 2, "Магний": 1}
    assert "attempt_no" not in raw, (
        "у группы нет одного общего номера попытки -- он был бы выдумкой")
    assert fake_deliver.calls[-1]["human_fallback"].startswith(
        "Ещё не отмечено:")


def test_followup_mentions_held_doses(db, fake_deliver, monkeypatch):
    fake_deliver.responses = ["sent"]
    _pending_intake(db)
    tick._meds_series(db, MORNING, CFG)          # доза удержана как asleep

    # 20:00 Алматы = 15:00 UTC
    tick._followup(db, "2026-07-20T15:00:00+00:00", CFG)

    calls = [c for c in fake_deliver.calls if c["kind"] == "followup"]
    assert len(calls) == 1
    assert calls[0]["raw"]["held_meds"] == [
        {"name": "Эутирокс", "plan_local": "09:00", "reason": "asleep"}]
    assert "Эутирокс" in calls[0]["human_fallback"]
