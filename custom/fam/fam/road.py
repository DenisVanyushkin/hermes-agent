"""Дорога с пробками: TomTom -> прямая x коэффициент -> ручное -> место -> 0.

Никогда не бросает наружу и не шлёт сообщений: ошибка дороги — это audit
road.error и следующая ступень лестницы. Ключ ТОЛЬКО из env TOMTOM_API_KEY,
никогда не попадает в audit/исключения.

compute_travel_min ТОЛЬКО вычисляет; персистентность и audit road.computed
живут в cal.py (Task 3). Но проверка дневного лимита TomTom-вызовов и её
audit (road.call/road.cap) живут здесь, потому что лимит должен охранять
каждую точку вызова TomTom, а не только cal.py.
"""
import json
import math
import os
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from fam import audit
from fam.gate import _almaty_day_utc_bounds

CONFIG_DEFAULTS = {
    "road_provider": "tomtom",
    "road_home_lat": None,
    "road_home_lon": None,
    "road_coef": 1.4,
    "road_speed_kmh": 30,
    "road_daily_cap": 100,
    "road_timeout_sec": 10,
    "road_recompute_min": [120, 60],
}


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _wall_now():
    """Real wall-clock now (UTC), as an ISO-8601 string -- matches the
    string type _almaty_day_utc_bounds expects (same shape as _now()).
    Kept as a separate seam from the caller-supplied depart anchor so
    tests can monkeypatch wall-clock independently of it."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _http_get(url, timeout):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.read()


def tomtom_route_minutes(from_lat, from_lon, to_lat, to_lon, depart_at_utc, cfg):
    """Raw TomTom calculateRoute call. Returns minutes (ceil) or None on
    any failure (missing key, HTTP error, malformed response). Never
    raises.
    """
    key = os.environ.get("TOMTOM_API_KEY", "").strip()
    if not key:
        return None
    locs = f"{from_lat},{from_lon}:{to_lat},{to_lon}"
    q = urllib.parse.urlencode({
        "key": key, "traffic": "true", "departAt": depart_at_utc,
        "routeType": "fastest", "travelMode": "car",
    })
    url = f"https://api.tomtom.com/routing/1/calculateRoute/{locs}/json?{q}"
    try:
        data = json.loads(_http_get(url, cfg.get("road_timeout_sec", 10)))
        secs = data["routes"][0]["summary"]["travelTimeInSeconds"]
        return max(1, math.ceil(secs / 60))
    except Exception:
        return None


def straight_line_minutes(from_lat, from_lon, to_lat, to_lon, cfg):
    """Haversine great-circle distance x road_coef, at road_speed_kmh."""
    r = 6371.0
    p1, p2 = math.radians(from_lat), math.radians(to_lat)
    dp = math.radians(to_lat - from_lat)
    dl = math.radians(to_lon - from_lon)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    km = 2 * r * math.asin(math.sqrt(a)) * cfg.get("road_coef", 1.4)
    return max(1, math.ceil(km / cfg.get("road_speed_kmh", 30) * 60))


def _tomtom_calls_today(conn):
    """Count of road.call audit rows within today's Asia/Almaty day,
    relative to real wall-clock now -- same day-bounds pattern as
    gate.budget_spent_today. Audit rows are stamped wall-clock, so this
    MUST use wall-clock now, never the caller's depart anchor (which for
    a future event can be days ahead and would always find 0 rows).
    """
    from_utc, to_utc = _almaty_day_utc_bounds(_wall_now())
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM audit_log WHERE kind='road.call' "
        "AND ts_utc >= ? AND ts_utc < ?",
        (from_utc, to_utc),
    ).fetchone()
    return row["n"] if row else 0


def compute_travel_min(conn, event, cfg, now_utc=None):
    """Fallback ladder for an event's travel time, guarded by TomTom's
    daily call cap. Never raises.

    1. event["place"] has lat AND lon, and cfg has road_home_lat/lon:
       try tomtom_route_minutes (source "tomtom"); if it returns None,
       fall back to straight_line_minutes (source "straight"). The cap
       check happens before the TomTom attempt -- when exhausted, the
       ladder skips straight to straight_line_minutes and logs
       road.cap instead of attempting the call.
    2. No usable coordinates: event["travel_min"] is not None ->
       ("manual").
    3. place["travel_min"] > 0 -> ("place").
    4. Otherwise -> (None, "none").
    """
    now = now_utc or _now()
    event_id = event.get("id")
    place = event.get("place") or {}
    home_lat = cfg.get("road_home_lat")
    home_lon = cfg.get("road_home_lon")
    to_lat = place.get("lat")
    to_lon = place.get("lon")

    if to_lat is not None and to_lon is not None and home_lat is not None and home_lon is not None:
        # No key at all: skip the tomtom rung silently -- no road.call,
        # no road.cap, no road.error. Distinguishes "not configured" from
        # a real attempt that failed (which still logs road.error below).
        if os.environ.get("TOMTOM_API_KEY", "").strip():
            cap = cfg.get("road_daily_cap", 100)
            if _tomtom_calls_today(conn) >= cap:
                audit.log(conn, "road.cap", {"event_id": event_id})
            else:
                depart_at = now if isinstance(now, str) else now.isoformat(timespec="seconds")
                minutes = tomtom_route_minutes(home_lat, home_lon, to_lat, to_lon, depart_at, cfg)
                if minutes is not None:
                    audit.log(conn, "road.call",
                               {"event_id": event_id, "minutes": minutes, "source": "tomtom"})
                    return minutes, "tomtom"
                audit.log(conn, "road.error", {"event_id": event_id})
        return straight_line_minutes(home_lat, home_lon, to_lat, to_lon, cfg), "straight"

    if event.get("travel_min") is not None:
        return event["travel_min"], "manual"

    if place.get("travel_min", 0) and place["travel_min"] > 0:
        return place["travel_min"], "place"

    return None, "none"
