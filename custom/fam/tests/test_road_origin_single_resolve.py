"""Один origin на один пересчёт дороги.

cal.recompute_road и tick.road_recompute резолвили точку отправления
дважды: сами -- ради колонок road_origin_* -- и ещё раз внутри
road.compute_travel_min -> road._origin_for. Две резолюции могли
разойтись, и в tick.py расходились: он передавал свои часы в свой
resolve_origin, а compute_travel_min звал _wall_now(). Тогда в
road_origin_lat/lon лежала одна точка, а минуты рядом были посчитаны от
другой -- ровно то, что ломает origin_moved на следующем тике.

Эти тесты фиксируют инвариант конструкцией, а не соглашением: тот самый
объект, который сохраняют, и есть тот, от которого считают.
"""
from datetime import datetime, timedelta

from fam import cal, places, road, tick, whereami

HOME_LAT, HOME_LON = 43.197391, 76.872737
# ~9 км от дома -- достаточно далеко, чтобы прямую оценку отсюда нельзя
# было спутать с оценкой от дома
AWAY_LAT, AWAY_LON = 43.26, 76.94
DEST_LAT, DEST_LON = 43.20, 76.95

NOW = "2026-07-29T10:00:00+00:00"
SOON = "2026-07-29T11:00:00+00:00"


def _cfg():
    return {"road_home_lat": HOME_LAT, "road_home_lon": HOME_LON,
            "road_coef": 1.4, "road_speed_kmh": 30, "road_daily_cap": 100,
            "road_recompute_min": [120, 60]}


def _origin_at(lat, lon, source="hint"):
    """Той же формы, что строит whereami._result."""
    return {"lat": lat, "lon": lon, "source": source,
            "confidence": "high", "label": "", "fix_age_min": None}


def _event_with_place(db):
    places.add(db, "Театр", lat=DEST_LAT, lon=DEST_LON)
    db.commit()
    e = cal.add(db, "Спектакль", SOON, place="Театр")
    db.commit()
    return cal.get(db, e["id"])


def _park_car_away(db, when=NOW):
    db.execute(
        "INSERT INTO car_metrics(ts_utc,gps_lat,gps_lon,gps_ts,gps_speed)"
        " VALUES (?,?,?,?,?)",
        (when, AWAY_LAT, AWAY_LON,
         int(datetime.fromisoformat(when).timestamp()), 0))
    db.commit()


def _spy_resolve_origin(monkeypatch):
    """Считает КАЖДЫЙ resolve_origin, откуда бы его ни звали: cal.py и
    tick.py держат модуль, road._origin_for делает `from fam import
    whereami` внутри функции -- все трое приходят к этому атрибуту в
    момент вызова.

    Пишет id события, чтобы тест мог отличить модульную проверку «есть
    ли origin вообще» (event=None) от поевентных.
    """
    real = whereami.resolve_origin
    calls = []

    def spy(conn, cfg, now_utc=None, event=None, at_utc=None):
        calls.append({
            "event_id": event.get("id") if isinstance(event, dict) else None,
            "now_utc": now_utc, "at_utc": at_utc})
        return real(conn, cfg, now_utc=now_utc, event=event, at_utc=at_utc)

    monkeypatch.setattr(whereami, "resolve_origin", spy)
    return calls


# --- Task 1: the _origin_for contract -------------------------------------

def test_origin_override_short_circuits_the_ladder(db, monkeypatch):
    """origin= авторитетен: лестница не должна запускаться вовсе."""
    def boom(*a, **k):
        raise AssertionError("resolve_origin must not run when origin= is given")
    monkeypatch.setattr(whereami, "resolve_origin", boom)

    assert road._origin_for(db, None, _cfg(),
                            origin=_origin_at(AWAY_LAT, AWAY_LON)) == (
        AWAY_LAT, AWAY_LON)


def test_origin_none_means_no_origin_not_go_resolve_one(db, monkeypatch):
    """None здесь -- ЗНАЧЕНИЕ («резолвили, origin'а нет»), а не «не
    передали». Схлопнуть их значило бы перерезолвить ровно в том случае,
    который держит tick.road_recompute: его поевентная лестница исключает
    целевое событие и может прийти пустой там, где модульная проверка
    пустой не пришла."""
    def boom(*a, **k):
        raise AssertionError("resolve_origin must not run when origin=None")
    monkeypatch.setattr(whereami, "resolve_origin", boom)

    assert road._origin_for(db, None, _cfg(), origin=None) == (None, None)


def test_omitting_origin_still_resolves_as_before(db, monkeypatch):
    """Значение по умолчанию _UNSET: у всех прежних вызывающих поведение
    не меняется."""
    monkeypatch.delenv("TOMTOM_API_KEY", raising=False)
    _park_car_away(db)

    assert road._origin_for(db, None, _cfg(), depart_at=NOW,
                            wall_now_utc=NOW) == (AWAY_LAT, AWAY_LON)


def test_compute_travel_min_routes_from_the_given_origin(db, monkeypatch):
    """Не просто принят, а действительно использован. Прямая ступень --
    чистая функция от origin, поэтому две точки обязаны дать два числа."""
    monkeypatch.delenv("TOMTOM_API_KEY", raising=False)
    event = _event_with_place(db)

    from_home, src = road.compute_travel_min(
        db, event, _cfg(), now_utc=SOON,
        origin=_origin_at(HOME_LAT, HOME_LON, source="home"))
    from_away, _ = road.compute_travel_min(
        db, event, _cfg(), now_utc=SOON,
        origin=_origin_at(AWAY_LAT, AWAY_LON))

    assert src == "straight"
    assert from_away == road.straight_line_minutes(
        AWAY_LAT, AWAY_LON, DEST_LAT, DEST_LON, _cfg())
    assert from_away != from_home


def test_compute_travel_min_with_origin_none_falls_to_the_lower_rungs(db, monkeypatch):
    """origin=None -> (None, None) от _origin_for -> ладдер обязан
    провалиться на manual/place/none, а не сходить за origin'ом сам."""
    monkeypatch.delenv("TOMTOM_API_KEY", raising=False)

    def boom(*a, **k):
        raise AssertionError("resolve_origin must not run when origin=None")
    monkeypatch.setattr(whereami, "resolve_origin", boom)

    event = {"id": None, "travel_min": 42,
             "place": {"lat": DEST_LAT, "lon": DEST_LON}}
    assert road.compute_travel_min(db, event, _cfg(), now_utc=SOON,
                                   origin=None) == (42, "manual")


# --- Task 2: cal.recompute_road -------------------------------------------

def test_recompute_road_resolves_the_origin_exactly_once(db, monkeypatch):
    monkeypatch.delenv("TOMTOM_API_KEY", raising=False)
    monkeypatch.setattr(cal.gate, "load_config", _cfg)
    event = _event_with_place(db)
    _park_car_away(db)
    calls = _spy_resolve_origin(monkeypatch)

    out = cal.recompute_road(db, event["id"])
    db.commit()

    assert out["source"] == "straight"
    assert len(calls) == 1, (
        f"origin resolved {len(calls)}x per recompute, expected 1: "
        f"{calls}")


def test_persisted_origin_is_the_point_the_minutes_were_measured_from(
        db, monkeypatch):
    """Страховка от рассинхрона. С резолвером, который отвечает
    РАЗНОЕ на каждый вызов, вторая резолюция перестаёт быть незаметной:
    в колонках оказалась бы одна точка, а в минутах рядом -- другая.
    Против кода с двумя резолюциями этот тест написать нельзя вовсе --
    он и есть инвариант, который создаёт изменение.
    """
    monkeypatch.delenv("TOMTOM_API_KEY", raising=False)
    monkeypatch.setattr(cal.gate, "load_config", _cfg)
    event = _event_with_place(db)

    seen = []

    def shifting(conn, cfg, now_utc=None, event=None, at_utc=None):
        seen.append(1)
        if len(seen) == 1:
            return _origin_at(AWAY_LAT, AWAY_LON)
        return _origin_at(HOME_LAT, HOME_LON, source="home")

    monkeypatch.setattr(whereami, "resolve_origin", shifting)

    out = cal.recompute_road(db, event["id"])
    db.commit()

    row = db.execute(
        "SELECT road_origin_lat, road_origin_lon, road_origin_source "
        "FROM events WHERE id=?", (event["id"],)).fetchone()
    assert row["road_origin_source"] == "hint"
    assert out["minutes"] == road.straight_line_minutes(
        row["road_origin_lat"], row["road_origin_lon"],
        DEST_LAT, DEST_LON, _cfg()), (
        "persisted origin and the origin the minutes were computed from "
        "have drifted apart")


def test_recompute_road_keeps_its_no_origin_guard(db, monkeypatch):
    """Резолвер вернул None -> прежний ранний выход, а не поход в
    compute_travel_min с origin=None."""
    monkeypatch.delenv("TOMTOM_API_KEY", raising=False)
    monkeypatch.setattr(cal.gate, "load_config", _cfg)
    event = _event_with_place(db)
    monkeypatch.setattr(whereami, "resolve_origin", lambda *a, **k: None)

    out = cal.recompute_road(db, event["id"])
    assert out == {"minutes": None, "reason": "no_home_config"}


# --- Task 3: tick.road_recompute ------------------------------------------

def _tick_event(db, monkeypatch, minutes_ahead, place_name="Клиника"):
    """cal.add() гоняет собственный road-хук на add-time, читая РЕАЛЬНЫЙ
    конфиг с диска (gate.load_config) -- на этом хосте с домашними
    координатами. Глушим compute_travel_min только на время add(), чтобы
    событие легло с travel_min_road=None и leave_at == start; тот же
    приём, что у _add_event_neutral_road в test_tick.py.
    """
    places.add(db, place_name, lat=DEST_LAT, lon=DEST_LON)
    db.commit()
    start = (datetime.fromisoformat(NOW)
             + timedelta(minutes=minutes_ahead)).isoformat(timespec="seconds")
    real = road.compute_travel_min
    monkeypatch.setattr(
        road, "compute_travel_min",
        lambda conn, event, cfg, now_utc=None, **kw: (None, "none"))
    e = cal.add(db, "Врач", start, place=place_name)
    db.commit()
    monkeypatch.setattr(road, "compute_travel_min", real)
    return e


def test_tick_resolves_the_per_event_origin_once(db, monkeypatch):
    """Внутри окна T-120 tick резолвит origin сам (ради origin_moved и
    road_origin_*), а compute_travel_min резолвил его ещё раз."""
    monkeypatch.delenv("TOMTOM_API_KEY", raising=False)
    _tick_event(db, monkeypatch, minutes_ahead=90)
    calls = _spy_resolve_origin(monkeypatch)

    touched = tick.road_recompute(db, now_utc=NOW, cfg=_cfg())

    assert touched == 1
    per_event = [c for c in calls if c["event_id"] is not None]
    assert len(per_event) == 1, (
        f"per-event origin resolved {len(per_event)}x, expected 1: "
        f"{per_event}")


def test_tick_does_not_resolve_an_origin_for_a_far_future_event(db, monkeypatch):
    """minutes_to_leave > самого широкого порога -> ни одно окно не
    открыто, тело цикла не исполняется ни разу, и origin был бы
    выброшен неиспользованным. На календаре из N будущих событий это N
    лишних проходов по лестнице в минуту, а внутри
    whereami_predict_horizon_min лестница включает _car_origin -- то
    есть поход в StarLine."""
    monkeypatch.delenv("TOMTOM_API_KEY", raising=False)
    _tick_event(db, monkeypatch, minutes_ahead=600)   # далеко за T-120
    calls = _spy_resolve_origin(monkeypatch)

    touched = tick.road_recompute(db, now_utc=NOW, cfg=_cfg())

    assert touched == 0
    per_event = [c for c in calls if c["event_id"] is not None]
    assert per_event == [], (
        "no threshold window is open; the per-event origin is resolved "
        f"and thrown away: {per_event}")


def test_tick_persists_the_origin_it_computed_the_minutes_from(db, monkeypatch):
    """Тот же инвариант, что у cal.recompute_road, на тиковом пути:
    резолвер, отвечающий РАЗНОЕ на каждый вызов, разводит колонки и
    минуты, если резолюций две."""
    monkeypatch.delenv("TOMTOM_API_KEY", raising=False)
    e = _tick_event(db, monkeypatch, minutes_ahead=90)

    seen = []

    def shifting(conn, cfg, now_utc=None, event=None, at_utc=None):
        seen.append(1)
        if event is None:          # модульная проверка «есть ли origin»
            return _origin_at(HOME_LAT, HOME_LON, source="home")
        if len(seen) <= 2:
            return _origin_at(AWAY_LAT, AWAY_LON)
        return _origin_at(HOME_LAT, HOME_LON, source="home")

    monkeypatch.setattr(whereami, "resolve_origin", shifting)

    tick.road_recompute(db, now_utc=NOW, cfg=_cfg())

    row = db.execute(
        "SELECT travel_min_road, road_origin_lat, road_origin_lon, "
        "road_origin_source FROM events WHERE id=?", (e["id"],)).fetchone()
    assert row["road_origin_source"] == "hint"
    assert row["travel_min_road"] == road.straight_line_minutes(
        row["road_origin_lat"], row["road_origin_lon"],
        DEST_LAT, DEST_LON, _cfg()), (
        "tick persisted one origin and computed the minutes from another")
