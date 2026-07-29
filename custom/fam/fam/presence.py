"""Присутствие Амины: спит ли она ещё и не вне ли дома.

Спека: docs/2026-07-29-med-reminder-gating-design.md

Два предиката для tick._meds_series. Ничего не пишет в БД -- только
читает. Оба намеренно консервативны: при недостатке данных отвечают
так, чтобы напоминание УШЛО. Ложное "спит" или "вне дома" означает
молчание там, где надо было напомнить, а все лекарства дома и
пропущенная доза дороже лишнего сообщения.

Почему не whereami.resolve_origin: та лестница отвечает на другой
вопрос -- "откуда она поедет на событие" -- имеет горизонт
предсказания whereami_predict_horizon_min и трактует "машина у дома"
как ОТСУТСТВИЕ доказательств, а не как "она дома". Здесь нужен прямой
предикат "прямо сейчас не дома".
"""
import sqlite3
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fam.road import _haversine_km

ALMATY = ZoneInfo("Asia/Almaty")

# Виды audit_log, означающие, что Амина что-то сделала: ack/реакция
# либо просьба, которую агент выполнил через fam. Виды, которые пишет
# сам тик (tick.*, gate.*, road.*), сюда не входят -- они не признак
# её активности.
AWAKE_AUDIT_KINDS = (
    "meds.take", "meds.skip", "meds.defer",
    "rem.ack_chain", "rem.cancel_chain",
    "react.handle",
    "cal.add", "cal.update", "cal.cancel",
    "plans.add", "plans.done",
    "shopping.add", "shopping.bought",
    "goals.add", "goals.done", "goals.decline",
    "people.add", "places.add",
)


def _parse(ts):
    return datetime.fromisoformat(ts)


def _almaty_day_bounds_utc(now_utc):
    """(начало, конец) текущих суток Алматы в виде ISO UTC строк."""
    local = _parse(now_utc).astimezone(ALMATY)
    start_local = local.replace(hour=0, minute=0, second=0, microsecond=0)
    end_local = start_local + timedelta(days=1)
    return (start_local.astimezone(timezone.utc).isoformat(timespec="seconds"),
            end_local.astimezone(timezone.utc).isoformat(timespec="seconds"))


def _inbound_message_ts(cfg, day_start, day_end):
    """Самое раннее входящее сообщение Амины за сутки, из state.db гейтвея.

    Строго защищённо: отсутствующий файл, таблица, routing-строка или
    любая sqlite3.Error означают "нет сигнала". Этот тик не имеет права
    падать из-за чужой БД.

    На 2026-07-29 источник дремлющий: routing-строки
    agent:main:whatsapp:dm:<номер> в state.db ещё нет, потому что Амина
    отвечает реакциями, а не текстом. Оживёт сам, когда она напишет.
    """
    import json

    path = cfg.get("state_db_path")
    if not path:
        return None
    target = str(cfg.get("target", ""))
    number = target.split(":")[-1].lstrip("+")
    if not number:
        return None
    session_key = f"agent:main:whatsapp:dm:{number}"

    conn = None
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2.0)
        row = conn.execute(
            "SELECT entry_json FROM gateway_routing WHERE session_key=?",
            (session_key,)).fetchone()
        if row is None:
            return None
        session_id = json.loads(row[0]).get("session_id")
        if not session_id:
            return None
        lo = _parse(day_start).timestamp()
        hi = _parse(day_end).timestamp()
        got = conn.execute(
            "SELECT MIN(timestamp) FROM messages "
            "WHERE session_id=? AND role='user' "
            "AND timestamp >= ? AND timestamp < ?",
            (session_id, lo, hi)).fetchone()
        if got is None or got[0] is None:
            return None
        return datetime.fromtimestamp(got[0], timezone.utc).isoformat(
            timespec="seconds")
    except (sqlite3.Error, ValueError, TypeError, OSError, AttributeError):
        return None
    finally:
        if conn is not None:
            try:
                conn.close()
            except sqlite3.Error:
                pass


def awake_since(conn, cfg, now_utc):
    """Момент первого признака жизни Амины за сегодняшние сутки Алматы,
    ISO UTC, либо None.

    Источники (берётся самый ранний):
      * строка audit_log вида из AWAKE_AUDIT_KINDS -- ack, реакция или
        просьба, выполненная агентом через fam;
      * входящее сообщение в state.db гейтвея (_inbound_message_ts).
    """
    day_start, day_end = _almaty_day_bounds_utc(now_utc)

    placeholders = ",".join("?" * len(AWAKE_AUDIT_KINDS))
    row = conn.execute(
        f"SELECT MIN(ts_utc) FROM audit_log "
        f"WHERE kind IN ({placeholders}) AND ts_utc >= ? AND ts_utc < ?",
        (*AWAKE_AUDIT_KINDS, day_start, day_end)).fetchone()
    candidates = [row[0]] if row and row[0] else []

    inbound = _inbound_message_ts(cfg, day_start, day_end)
    if inbound:
        candidates.append(inbound)

    return min(candidates) if candidates else None


def _far_from_home(cfg, lat, lon):
    """True, если точка дальше whereami_home_radius_km от дома.

    Отсутствующие координаты (None) -- не доказательство отлучки.
    """
    if lat is None or lon is None:
        return False
    home_lat = cfg.get("road_home_lat")
    home_lon = cfg.get("road_home_lon")
    if home_lat is None or home_lon is None:
        return False
    radius = float(cfg.get("whereami_home_radius_km", 0.3))
    return _haversine_km(lat, lon, home_lat, home_lon) > radius


def is_away(conn, cfg, now_utc):
    """(away, reason, expected_home_utc) -- уверенно ли Амина не дома.

    Только уверенные признаки, в порядке проверки:
      1. "event" -- идёт событие с place_id, чьи координаты дальше
         радиуса. expected_home = end_utc + travel_min.
      2. "car_gps" -- свежая (не старше whereami_car_fresh_min) строка
         car_metrics с координатами дальше радиуса. expected_home
         неизвестен.
      3. "shared_location" -- неистёкшая строка location_hints дальше
         радиуса.
    Иначе (False, "home_or_unknown", None).

    Событие БЕЗ места отлучкой не считается: слишком много домашних дел
    попадает в календарь, а ложное "вне дома" стоит дороже лишнего
    сообщения (все лекарства дома).
    """
    now = _parse(now_utc)

    row = conn.execute(
        "SELECT e.end_utc AS end_utc, e.travel_min AS travel_min, "
        "       p.lat AS lat, p.lon AS lon "
        "FROM events e JOIN places p ON p.id = e.place_id "
        "WHERE e.status='active' AND e.place_id IS NOT NULL "
        "  AND e.start_utc <= ? AND e.end_utc >= ? "
        "ORDER BY e.start_utc LIMIT 1",
        (now_utc, now_utc)).fetchone()
    if row is not None and _far_from_home(cfg, row["lat"], row["lon"]):
        expected = None
        if row["end_utc"]:
            back = _parse(row["end_utc"]) + timedelta(
                minutes=int(row["travel_min"] or 0))
            expected = back.astimezone(timezone.utc).isoformat(
                timespec="seconds")
        return (True, "event", expected)

    fresh_min = int(cfg.get("whereami_car_fresh_min", 20))
    cutoff = (now - timedelta(minutes=fresh_min)).isoformat(timespec="seconds")
    car = conn.execute(
        "SELECT gps_lat, gps_lon FROM car_metrics "
        "WHERE gps_lat IS NOT NULL AND gps_lon IS NOT NULL "
        "  AND COALESCE(gps_ts, ts_utc) >= ? "
        "ORDER BY COALESCE(gps_ts, ts_utc) DESC LIMIT 1",
        (cutoff,)).fetchone()
    if car is not None and _far_from_home(cfg, car["gps_lat"], car["gps_lon"]):
        return (True, "car_gps", None)

    hint = conn.execute(
        "SELECT lat, lon FROM location_hints "
        "WHERE expires_utc > ? ORDER BY ts_utc DESC LIMIT 1",
        (now_utc,)).fetchone()
    if hint is not None and _far_from_home(cfg, hint["lat"], hint["lon"]):
        return (True, "shared_location", None)

    return (False, "home_or_unknown", None)
