"""Sending a pin has to change the numbers immediately.

Waiting for the next T-120/T-60 threshold would miss the point: she sent
the location precisely because the current "выезжать через 25 минут" is
wrong right now.

Also pins down the budget exemption, which has a trap in it: force=True
stops a message being BLOCKED but not being COUNTED, so a kind needs
both force=True at the call site and membership in BUDGET_EXEMPT_KINDS.
digest and med each needed the pair; whereami is the third.
"""
import json
from datetime import datetime, timedelta, timezone

from fam import cal, cli, gate, places, whereami

HOME_LAT, HOME_LON = 43.197391, 76.872737
AWAY_LAT, AWAY_LON = 43.26, 76.94
DEST_LAT, DEST_LON = 43.20, 76.95
# Заведомо прошедшая дата. Инъецированное время обязано быть НЕ равно
# стенным часам: пока NOW стояло "на сегодня", файл зеленел только те
# три часа суток, когда две шкалы случайно совпадали, и разлом с
# потерянным now_utc прожил в коде до вечера того же дня.
NOW = "2026-01-15T10:00:00+00:00"


def _cfg():
    return {"road_home_lat": HOME_LAT, "road_home_lon": HOME_LON,
            "road_coef": 1.4, "road_speed_kmh": 30, "daily_budget": 8,
            "quiet_start": "21:30", "quiet_end": "07:30",
            "max_len_reminder": 300, "max_len_digest": 900}


def _wall_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _soon_event(db, minutes=90, anchor=NOW):
    """anchor -- часы, по которым живёт проверяемый код.

    Для инъецированного пути это NOW. У CLI шва времени нет и не должно
    быть: в проде `fam whereami set` всегда работает по настоящим часам,
    и событие, привязанное там к NOW, просто лежит в прошлом и не
    попадает в окно отбора.
    """
    places.add(db, "Театр", lat=DEST_LAT, lon=DEST_LON)
    db.commit()
    start = (datetime.fromisoformat(anchor) + timedelta(minutes=minutes)).isoformat(
        timespec="seconds")
    e = cal.add(db, "Спектакль", start, place="Театр")
    db.commit()
    return e["id"]


def test_recompute_updates_upcoming_events(db, monkeypatch):
    monkeypatch.delenv("TOMTOM_API_KEY", raising=False)
    monkeypatch.setattr(cal.gate, "load_config", _cfg)
    event_id = _soon_event(db)
    cal.recompute_road(db, event_id)
    db.commit()
    before = db.execute("SELECT travel_min_road FROM events WHERE id=?",
                        (event_id,)).fetchone()["travel_min_road"]

    whereami.set_hint(db, AWAY_LAT, AWAY_LON, now_utc=NOW, cfg=_cfg())
    db.commit()
    changed = whereami.recompute_affected(db, _cfg(), now_utc=NOW)

    assert [c["event_id"] for c in changed] == [event_id]
    assert changed[0]["old"] == before
    after = db.execute("SELECT travel_min_road FROM events WHERE id=?",
                       (event_id,)).fetchone()["travel_min_road"]
    assert after == changed[0]["new"] != before


def test_recompute_road_honours_the_injected_clock(db, monkeypatch):
    """Шов, на котором всё и сломалось.

    resolve_origin различает ДВА времени: now_utc (настоящее -- им
    меряется срок жизни присланной точки) и at_utc (момент выезда).
    cal.recompute_road отдавал только второе, а первое молча брал со
    стены. Из-за этого recompute_affected(now_utc=X) чинила окно
    отбора, но к резолву origin инъекция терялась: только что
    записанная подсказка выглядела просроченной, дорога считалась от
    дома, и «изменившихся» событий не находилось ни одного.
    """
    monkeypatch.delenv("TOMTOM_API_KEY", raising=False)
    monkeypatch.setattr(cal.gate, "load_config", _cfg)
    event_id = _soon_event(db)
    whereami.set_hint(db, AWAY_LAT, AWAY_LON, now_utc=NOW, cfg=_cfg())
    db.commit()

    cal.recompute_road(db, event_id, now_utc=NOW)
    db.commit()

    row = db.execute("SELECT road_origin_lat, road_origin_lon, "
                     "road_origin_source FROM events WHERE id=?",
                     (event_id,)).fetchone()
    assert row["road_origin_source"] == "shared"
    assert (round(row["road_origin_lat"], 4),
            round(row["road_origin_lon"], 4)) == (AWAY_LAT, AWAY_LON)


def test_events_beyond_the_horizon_are_left_alone(db, monkeypatch):
    """Past the prediction horizon the resolver ignores physical evidence
    anyway, so recomputing there would only spend TomTom calls."""
    monkeypatch.delenv("TOMTOM_API_KEY", raising=False)
    monkeypatch.setattr(cal.gate, "load_config", _cfg)
    far_id = _soon_event(db, minutes=60 * 40)  # почти двое суток
    whereami.set_hint(db, AWAY_LAT, AWAY_LON, now_utc=NOW, cfg=_cfg())
    db.commit()

    changed = whereami.recompute_affected(db, _cfg(), now_utc=NOW)

    assert far_id not in [c["event_id"] for c in changed]


def test_cli_set_recomputes_without_messaging(db, capsys, monkeypatch):
    """Default path: the agent is already replying to her in the chat, so
    a second automatic message would be a duplicate."""
    monkeypatch.delenv("TOMTOM_API_KEY", raising=False)
    monkeypatch.setattr(cli.gate, "load_config", _cfg)
    monkeypatch.setattr(cal.gate, "load_config", _cfg)
    sent = []
    monkeypatch.setattr(gate, "deliver",
                        lambda *a, **k: sent.append(a) or "sent")
    _soon_event(db, anchor=_wall_now())

    assert cli.main(["whereami", "set", "--lat", str(AWAY_LAT),
                     "--lon", str(AWAY_LON), "--json"]) == 0
    out = json.loads(capsys.readouterr().out)

    assert out["changed"]
    assert sent == []


def test_notify_flag_sends_one_message(db, capsys, monkeypatch):
    monkeypatch.delenv("TOMTOM_API_KEY", raising=False)
    monkeypatch.setattr(cli.gate, "load_config", _cfg)
    monkeypatch.setattr(cal.gate, "load_config", _cfg)
    calls = []

    def fake_deliver(conn, kind, raw, human_fallback, cfg, force=False, **kw):
        calls.append({"kind": kind, "force": force, "text": human_fallback})
        return "sent"

    monkeypatch.setattr(cli.gate, "deliver", fake_deliver)
    _soon_event(db, anchor=_wall_now())

    cli.main(["whereami", "set", "--lat", str(AWAY_LAT), "--lon",
              str(AWAY_LON), "--notify", "--json"])

    assert len(calls) == 1
    assert calls[0]["kind"] == "whereami"
    assert calls[0]["force"] is True, "без force сообщение заблокируется на лимите"


def test_whereami_is_exempt_from_the_daily_budget(db):
    """The trap: force=True alone would still let this message eat a slot
    from the eight Amina gets per day."""
    assert "whereami" in gate.BUDGET_EXEMPT_KINDS

    for _ in range(3):
        audit_payload = {"kind": "whereami", "raw": {}}
        db.execute(
            "INSERT INTO audit_log(ts_utc,kind,payload,actor) VALUES (?,?,?,?)",
            (NOW, "gate.sent", json.dumps(audit_payload), "tick"))
    db.commit()

    assert gate.budget_spent_today(db, now_utc=NOW) == 0


def test_reminder_still_counts_toward_the_budget(db):
    """Guard against the constant refactor quietly exempting everything."""
    db.execute(
        "INSERT INTO audit_log(ts_utc,kind,payload,actor) VALUES (?,?,?,?)",
        (NOW, "gate.sent",
         json.dumps({"kind": "reminder", "raw": {"event_id": 1}}), "tick"))
    db.commit()
    assert gate.budget_spent_today(db, now_utc=NOW) == 1
