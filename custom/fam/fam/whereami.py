"""Откуда считать дорогу: подсказка -> машина -> текущее событие -> дом.

Раньше origin был константой road_home_lat/lon, и время в пути считалось
от дома, где бы Амина ни была. Этот модуль отвечает на тот же вопрос
динамически, оставаясь внутри контракта road.py: никогда не бросает
наружу и не шлёт сообщений.

Лестница (первая сработавшая ступень выигрывает):

  1. location_hints -- точка, присланная Аминой в WhatsApp, или ручной
     override. Между собой ступени НЕ ранжируются по типу: выигрывает
     самая свежая. Ручной override, поставленный утром, не должен
     перебивать точку, присланную час назад.
  2. GPS машины (StarLine) -- но ТОЛЬКО когда машина не у дома.
     Машина во дворе -- это не свидетельство о том, где Амина; это его
     отсутствие, и проваливаться на следующую ступень честнее, чем
     выдавать дом с высокой уверенностью.
  3. Место идущего (или только что закончившегося) события.
  4. Дом -- всегда доступное дно лестницы, ровно прежнее поведение.

Если дом тоже не сконфигурирован, возвращается None: вызывающие
(cal.recompute_road, tick.road_recompute) исторически умели распознавать
"origin неизвестен" и обязаны сохранить это умение.

`confidence` -- насколько мы верим точке: "high" (она сама сказала /
свежий фикс), "medium" (косвенный вывод), "low" (предположение по
умолчанию). Task 6 дописывает к напоминанию приглашение прислать точку,
когда уверенность не "high".
"""
from datetime import datetime, timedelta, timezone

from fam import road

CONFIG_DEFAULTS = {
    # Насколько близко к дому машина считается "во дворе" и ступень
    # пропускается. 300 м покрывает парковку и погрешность GPS.
    "whereami_home_radius_km": 0.3,
    # Возраст фикса, до которого позиция считается актуальной без оговорок.
    "whereami_car_fresh_min": 20,
    # Радиус, в котором координата подписывается именем известного места.
    "whereami_place_radius_km": 0.3,
    # Сколько минут после конца события считать, что она ещё там.
    "whereami_event_grace_min": 30,
    # TTL по умолчанию для присланной точки и ручного override.
    "whereami_shared_ttl_min": 90,
    "whereami_manual_ttl_min": 180,
    # На сколько должен сместиться origin, чтобы инвалидировать кэш (Task 5).
    "whereami_origin_move_km": 1.0,
    # Насколько далеко вперёд физические свидетельства (машина,
    # присланная точка) ещё что-то говорят о моменте выезда.
    "whereami_predict_horizon_min": 180,
    # Живой опрос StarLine: включён, и только когда до выезда меньше
    # указанного (позже он всё равно успеет обновиться по таймеру).
    "whereami_live_poll": True,
    "whereami_live_poll_within_min": 60,
}


def _live_poll(conn, now=None):
    """Сходить в StarLine прямо сейчас и записать строку в car_metrics.

    Ровно тот же путь, которым уже идёт `fam car status --live`
    (cli.py): poll() никогда не бросает и возвращает None при любой
    неудаче. Вынесено отдельной функцией модуля, чтобы тесты подменяли
    её целиком, не трогая сеть.
    """
    from fam import car
    metrics = car.StarlineClient().poll()
    if not metrics:
        return None
    car.record_metrics(conn, metrics, now=now)
    conn.commit()
    return metrics


def _now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _parse_utc(value):
    """ISO-8601 -> aware datetime, или None. Терпит 'Z' и наивные строки
    (их трактуем как UTC), потому что в базе лежат обе формы: car.py
    пишет со смещением, часть кода -- с 'Z'."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _coord(value):
    """float или None -- конфиг правится руками, там встречается мусор."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if -180.0 <= f <= 180.0 else None


def _home_point(cfg):
    lat = _coord(cfg.get("road_home_lat"))
    lon = _coord(cfg.get("road_home_lon"))
    return (lat, lon) if lat is not None and lon is not None else None


def _result(lat, lon, source, confidence, label, fix_age_min=None):
    return {"lat": lat, "lon": lon, "source": source,
            "confidence": confidence, "label": label,
            "fix_age_min": fix_age_min}


def _nearest_place_name(conn, lat, lon, cfg):
    """Имя известного места в радиусе whereami_place_radius_km, иначе
    None. Читать «≈25 мин от Спортзала» человечнее, чем «от машины»."""
    radius = cfg.get("whereami_place_radius_km", 0.3)
    best_name, best_km = None, None
    for row in conn.execute(
            "SELECT name, lat, lon FROM places "
            "WHERE lat IS NOT NULL AND lon IS NOT NULL"):
        km = road._haversine_km(lat, lon, row["lat"], row["lon"])
        if km <= radius and (best_km is None or km < best_km):
            best_name, best_km = row["name"], km
    return best_name


def _hint_origin(conn, now):
    """Самая свежая неистёкшая подсказка. Срок жизни сравнивается
    разобранными датами, а не строками: в базе сосуществуют 'Z' и
    '+00:00', которые лексикографически несравнимы."""
    for row in conn.execute(
            "SELECT source, lat, lon, label, ts_utc, expires_utc "
            "FROM location_hints ORDER BY ts_utc DESC, id DESC LIMIT 20"):
        expires = _parse_utc(row["expires_utc"])
        if expires is not None and expires <= now:
            continue
        lat, lon = _coord(row["lat"]), _coord(row["lon"])
        if lat is None or lon is None:
            continue
        label = row["label"] or "от присланной точки"
        return _result(lat, lon, row["source"], "high", label)
    return None


def _car_origin(conn, cfg, now, home, may_poll=False):
    """Позиция машины, или None если она ничего не говорит о том, где Амина.

    may_poll: разрешён ли живой опрос StarLine. Он делается ровно в
    одном случае -- единственный известный фикс устарел И машина в этот
    момент ехала. Стоящая машина уже даёт годный ответ, а свежая не
    нуждается в уточнении; тратить два HTTP-запроса на каждом минутном
    тике ради этого не нужно.
    """
    row = conn.execute(
        "SELECT gps_lat, gps_lon, gps_ts, gps_speed, ts_utc FROM car_metrics "
        "WHERE gps_lat IS NOT NULL AND gps_lon IS NOT NULL "
        "ORDER BY ts_utc DESC, id DESC LIMIT 1").fetchone()
    found, stale_moving = _evaluate_car(conn, cfg, now, home, row)
    if found is not None or not stale_moving or not may_poll:
        return found

    # Оцениваем то, что вернул опрос, а не перечитываем таблицу:
    # _live_poll сам решает, записывать ли строку, и повторное чтение
    # завязало бы результат на видимость чужой записи.
    try:
        metrics = _live_poll(conn, now=now.isoformat(timespec="seconds"))
    except Exception:
        return None
    if not metrics:
        return None
    found, _ = _evaluate_car(conn, cfg, now, home, metrics)
    return found


def _evaluate_car(conn, cfg, now, home, row):
    """(результат, устарел_ли_фикс_на_ходу) по одной записи телеметрии.

    `row` -- строка car_metrics или тот же по форме dict из car.normalize;
    обращения идут только по общим ключам, поэтому подходят оба.
    """
    if row is None:
        return None, False
    lat, lon = _coord(row["gps_lat"]), _coord(row["gps_lon"])
    if lat is None or lon is None:
        return None, False

    if home is not None:
        radius = cfg.get("whereami_home_radius_km", 0.3)
        if road._haversine_km(lat, lon, home[0], home[1]) <= radius:
            return None, False

    # Возраст ФИКСА, не строки. gps_ts -- когда спутники увидели машину;
    # ts_utc -- когда fam сходил в StarLine. В проде они расходились на
    # ~7 минут, а на движущейся машине это и есть вся разница между
    # "она здесь" и "она была здесь".
    fix_dt = None
    if row["gps_ts"] is not None:
        try:
            fix_dt = datetime.fromtimestamp(int(row["gps_ts"]), timezone.utc)
        except (TypeError, ValueError, OSError, OverflowError):
            fix_dt = None
    if fix_dt is None:
        fix_dt = _parse_utc(row["ts_utc"])
    if fix_dt is None:
        return None, False

    age_min = (now - fix_dt).total_seconds() / 60.0
    if age_min < 0:
        age_min = 0.0

    label = _nearest_place_name(conn, lat, lon, cfg)
    label = f"от «{label}»" if label else "от машины"

    if age_min <= cfg.get("whereami_car_fresh_min", 20):
        return _result(lat, lon, "car", "high", label, round(age_min)), False

    # Фикс устарел. Стоящая машина всё ещё что-то значит -- она
    # припаркована там, куда Амина приехала. Едущая не значит ничего:
    # за сорок минут на скорости она в другом районе.
    speed = row["gps_speed"]
    if speed is None or speed == 0:
        return _result(lat, lon, "car", "medium", label, round(age_min)), False
    return None, True


def _event_origin(conn, cfg, now, event):
    """Место события, которое идёт сейчас или только что кончилось.

    Целевое событие исключается: считать дорогу К событию, стартуя ОТ
    него же, дало бы ноль минут и напоминание, которое никогда не
    сработает вовремя.
    """
    exclude_id = None
    if isinstance(event, dict):
        exclude_id = event.get("id")

    grace = timedelta(minutes=cfg.get("whereami_event_grace_min", 30))
    rows = conn.execute(
        "SELECT e.id, e.start_utc, e.end_utc, p.name, p.lat, p.lon "
        "FROM events e JOIN places p ON p.id = e.place_id "
        "WHERE e.status='active' "
        "AND p.lat IS NOT NULL AND p.lon IS NOT NULL "
        "ORDER BY e.start_utc DESC LIMIT 50").fetchall()

    for row in rows:
        if exclude_id is not None and row["id"] == exclude_id:
            continue
        start = _parse_utc(row["start_utc"])
        if start is None or start > now:
            continue
        end = _parse_utc(row["end_utc"]) or start
        if end + grace < now:
            continue
        lat, lon = _coord(row["lat"]), _coord(row["lon"])
        if lat is None or lon is None:
            continue
        return _result(lat, lon, "event", "medium", f"от «{row['name']}»")
    return None


def resolve_origin(conn, cfg, now_utc=None, event=None, at_utc=None):
    """Точка, от которой считать дорогу, или None если неизвестна даже
    домашняя координата.

    Здесь ДВА разных времени, и путать их нельзя -- ровно тот же разлом,
    из-за которого _tomtom_calls_today в road.py обязан брать
    _wall_now(), а не якорь выезда:

      now_utc -- настоящее. Им и только им меряется свежесть GPS-фикса и
        срок жизни присланной точки.
      at_utc -- момент выезда, ради которого считается дорога. Для
        cal.recompute_road это start_utc события, то есть возможно через
        несколько дней. Им проверяется календарная ступень: «где она
        будет, когда поедет».

    Когда выезд дальше whereami_predict_horizon_min, физические
    свидетельства отбрасываются целиком: сегодняшняя парковка ничего не
    говорит о четверге, и маршрут от неё был бы уверенной чепухой.
    По умолчанию at_utc == now_utc, и поведение сводится к обычному.

    Никогда не бросает: любая неожиданная ошибка деградирует до дома,
    потому что road.py гарантирует вызывающим отсутствие исключений, а
    неточный origin -- это неточное время, тогда как исключение здесь
    сорвало бы всё напоминание целиком.
    """
    cfg = cfg or {}
    home = _home_point(cfg)
    home_result = (_result(home[0], home[1], "home", "low", "от дома")
                   if home else None)
    try:
        now = _parse_utc(now_utc) or _parse_utc(_now_iso())
        at = _parse_utc(at_utc) or now

        horizon = timedelta(
            minutes=cfg.get("whereami_predict_horizon_min", 180))
        physical_evidence_applies = at - now <= horizon

        if physical_evidence_applies:
            found = _hint_origin(conn, now)
            if found is not None:
                return found
            # Живой опрос StarLine разрешён только у самого выезда:
            # дальше по времени кэшированной строки достаточно, а
            # тик напоминаний ходит каждую минуту.
            may_poll = (
                cfg.get("whereami_live_poll", True)
                and (at - now) <= timedelta(
                    minutes=cfg.get("whereami_live_poll_within_min", 60)))
            found = _car_origin(conn, cfg, now, home, may_poll=may_poll)
            if found is not None:
                return found
        found = _event_origin(conn, cfg, at, event)
        if found is not None:
            return found
    except Exception:
        return home_result
    return home_result


def set_hint(conn, lat, lon, source="shared", label="", ttl_min=None,
             now_utc=None, cfg=None):
    """Записать точку, от которой считать дорогу, на ограниченный срок.

    TTL обязателен по смыслу задачи: подсказка о местоположении верна
    недолго, а просроченная хуже отсутствующей -- она молча уводит
    расчёт в место, где Амины давно нет. Поэтому строки не обновляются,
    а добавляются: _hint_origin берёт самую свежую, и история остаётся
    в базе для разбора «почему Гермес посчитал оттуда».

    Возвращает записанную подсказку. Бросает ValueError на негодных
    координатах -- это вызов из CLI/агента, где молчаливое проглатывание
    ошибки скрыло бы опечатку.
    """
    cfg = cfg or {}
    lat_f, lon_f = _coord(lat), _coord(lon)
    if lat_f is None or lon_f is None or not -90.0 <= lat_f <= 90.0:
        raise ValueError(f"негодные координаты: {lat!r}, {lon!r}")
    if source not in ("shared", "manual"):
        raise ValueError(f"неизвестный источник: {source!r}")

    if ttl_min is None:
        ttl_min = cfg.get(
            "whereami_shared_ttl_min" if source == "shared"
            else "whereami_manual_ttl_min",
            90 if source == "shared" else 180)
    now = _parse_utc(now_utc) or _parse_utc(_now_iso())
    expires = now + timedelta(minutes=int(ttl_min))

    conn.execute(
        "INSERT INTO location_hints(source,lat,lon,label,ts_utc,expires_utc)"
        " VALUES (?,?,?,?,?,?)",
        (source, lat_f, lon_f, label or "",
         now.isoformat(timespec="seconds"),
         expires.isoformat(timespec="seconds")))
    return {"source": source, "lat": lat_f, "lon": lon_f,
            "label": label or "", "expires_utc": expires.isoformat(timespec="seconds")}


def clear_hints(conn):
    """Убрать все подсказки. Возвращает число удалённых строк."""
    return conn.execute("DELETE FROM location_hints").rowcount


def recompute_affected(conn, cfg, now_utc=None, horizon_min=None):
    """Пересчитать дорогу для ближайших событий после смены origin.

    Смысл присланной точки в том, чтобы цифры поменялись СЕЙЧАС, а не на
    следующем пороге T-120/T-60: Амина прислала локацию именно потому,
    что текущее «через 25 минут выезжать» неверно.

    Границей служит whereami_predict_horizon_min -- ровно тот горизонт,
    за которым resolve_origin всё равно перестаёт учитывать физические
    свидетельства, так что пересчитывать дальше него бессмысленно.

    Возвращает список изменившихся событий:
    [{"event_id", "old", "new", "title"}]. Никогда не бросает наружу --
    вызывается из CLI-пути, который не должен падать из-за дороги.
    """
    from fam import cal

    now = _parse_utc(now_utc) or _parse_utc(_now_iso())
    if horizon_min is None:
        horizon_min = cfg.get("whereami_predict_horizon_min", 180)
    until = (now + timedelta(minutes=int(horizon_min))).isoformat(
        timespec="seconds")

    changed = []
    try:
        rows = conn.execute(
            "SELECT e.id FROM events e JOIN places p ON p.id = e.place_id "
            "WHERE e.status='active' AND e.start_utc > ? AND e.start_utc <= ? "
            "AND p.lat IS NOT NULL AND p.lon IS NOT NULL "
            "ORDER BY e.start_utc",
            (now.isoformat(timespec="seconds"), until)).fetchall()
    except Exception:
        return changed

    for row in rows:
        event_id = row["id"]
        try:
            before = conn.execute(
                "SELECT travel_min_road FROM events WHERE id=?",
                (event_id,)).fetchone()
            old = before["travel_min_road"] if before else None
            result = cal.recompute_road(conn, event_id)
            conn.commit()
            new = result.get("minutes")
            if new is not None and new != old:
                event = cal.get(conn, event_id)
                changed.append({"event_id": event_id, "old": old, "new": new,
                                "title": event["title"] if event else ""})
        except Exception:
            conn.rollback()
    return changed
