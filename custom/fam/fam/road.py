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


def _attached_via_latlons(conn, event):
    """Local-import shim onto plans.attached_via_points -- road.py can't
    import plans at module level (plans.py already imports road at
    module level; a module-level `from fam import plans` here would make
    the two modules load each other mid-import). Same local-import
    pattern plans.add()/attach()/mark() already use in reverse for cal.
    Never raises: a bare AttributeError/ImportError during import would
    be a packaging bug, not something to swallow, but a lookup failure
    inside plans.attached_via_points itself already can't raise (pure
    SQL + dict lookups) -- so nothing extra is caught here.
    """
    from fam import plans as plans_mod
    return plans_mod.attached_via_points(conn, event)


def compute_travel_min(conn, event, cfg, now_utc=None):
    """Fallback ladder for an event's travel time, guarded by TomTom's
    daily call cap. Never raises.

    0. (Phase 7b, detours) event has usable coordinates AND at least one
       OPEN plan is attached_event_id-linked to it with a resolvable
       via point (road.py's plans.attached_via_points, guarded against a
       via identical to the event's own place): try route_via through
       those waypoints (source "tomtom"). A successful via probe is
       returned as-is; a failed one (cap/no-key/error/non-tomtom
       provider) falls back to straight_line_minutes (source "straight")
       -- NOT to the plain no-via tomtom rung below, since route_via
       already spent this event's one attempt against the shared daily
       counter and audited its own road.cap/road.error.
    1. No attached vias (or none resolvable): event["place"] has lat AND
       lon, and cfg has road_home_lat/lon: try tomtom_route_minutes
       (source "tomtom"); if it returns None, fall back to
       straight_line_minutes (source "straight"). The cap check happens
       before the TomTom attempt -- when exhausted, the ladder skips
       straight to straight_line_minutes and logs road.cap instead of
       attempting the call.
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
        vias = _attached_via_latlons(conn, event)
        if vias:
            minutes, _points, source = route_via(
                conn, (home_lat, home_lon), vias, (to_lat, to_lon), cfg, now_utc=now)
            if source == "tomtom":
                return minutes, "tomtom"
            return straight_line_minutes(home_lat, home_lon, to_lat, to_lon, cfg), "straight"

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


def tomtom_route_points(from_lat, from_lon, to_lat, to_lon, depart_at_utc, cfg):
    """Raw TomTom calculateRoute call collecting the route polyline.
    Returns a flat list of (lat, lon) tuples from routes[0].legs[*].points[*],
    or None on any failure (missing key, HTTP error, malformed response).
    Never raises. Separate HTTP call from tomtom_route_minutes -- points
    aren't reused from a prior minutes-call because the minutes rung may
    have already run (and possibly failed) earlier in the ladder.
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
        points = []
        for leg in data["routes"][0]["legs"]:
            for p in leg["points"]:
                points.append((p["latitude"], p["longitude"]))
        if not points:
            return None
        return points
    except Exception:
        return None


def point_to_route_km(lat, lon, route_points):
    """Minimum distance from (lat, lon) to a polyline given as a list of
    (lat, lon) tuples, taking the min over consecutive-pair segments.
    Uses an equirectangular-projection approximation for the point-to-
    segment distance (acceptable for short, city-scale segments). Pure
    function -- no I/O, never raises for well-formed input.

    Empty route -> +inf (no segments to measure against). A single-point
    route is treated as a degenerate segment (distance to that point).
    """
    if not route_points:
        return float("inf")
    if len(route_points) == 1:
        return _haversine_km(lat, lon, *route_points[0])

    best = float("inf")
    for (lat1, lon1), (lat2, lon2) in zip(route_points, route_points[1:]):
        d = _point_to_segment_km(lat, lon, lat1, lon1, lat2, lon2)
        if d < best:
            best = d
    return best


def _haversine_km(lat1, lon1, lat2, lon2):
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _point_to_segment_km(lat, lon, lat1, lon1, lat2, lon2):
    """Equirectangular-projection point-to-segment distance in km. Projects
    lat/lon to a local flat xy plane (scaled by cos of mean latitude for
    the lon axis), then does standard 2D point-to-segment math. Good
    enough for short city-scale segments; not valid for long segments or
    near the poles.
    """
    r = 6371.0
    lat0 = math.radians((lat1 + lat2) / 2.0)
    kx = r * math.cos(lat0)  # km per radian of longitude at this latitude
    ky = r  # km per radian of latitude

    def to_xy(la, lo):
        return (math.radians(lo) * kx, math.radians(la) * ky)

    x, y = to_xy(lat, lon)
    x1, y1 = to_xy(lat1, lon1)
    x2, y2 = to_xy(lat2, lon2)

    dx, dy = x2 - x1, y2 - y1
    seg_len_sq = dx * dx + dy * dy
    if seg_len_sq == 0:
        px, py = x1, y1
    else:
        t = ((x - x1) * dx + (y - y1) * dy) / seg_len_sq
        t = max(0.0, min(1.0, t))
        px, py = x1 + t * dx, y1 + t * dy

    return math.hypot(x - px, y - py)


def route_for_event(conn, event, cfg, now_utc=None):
    """Fallback ladder for an event's route polyline, guarded by TomTom's
    daily call cap (shared with compute_travel_min's counter). Never
    raises.

    0. (Phase 7b, detours) event has usable coordinates AND at least one
       OPEN plan is attached_event_id-linked to it with a resolvable via
       point: try route_via through those waypoints (source "tomtom") --
       same via-gathering (plans.attached_via_points, via the local
       _attached_via_latlons shim) and same "failed via probe falls to
       the straight pair, not to the plain no-via tomtom rung" contract
       as compute_travel_min above, since route_via already spent the
       shared daily counter's one attempt.
    1. No attached vias (or none resolvable): event["place"] has lat AND
       lon, and cfg has road_home_lat/lon: try tomtom_route_points
       (source "tomtom"); if it returns None, fall back to the straight
       home->place pair (source "straight"). The cap check happens
       before the TomTom attempt -- when exhausted, the ladder skips
       straight to the straight-pair rung and logs road.cap instead of
       attempting the call.
    2. No usable coordinates -> (None, "none").
    """
    now = now_utc or _now()
    event_id = event.get("id")
    place = event.get("place") or {}
    home_lat = cfg.get("road_home_lat")
    home_lon = cfg.get("road_home_lon")
    to_lat = place.get("lat")
    to_lon = place.get("lon")

    if to_lat is not None and to_lon is not None and home_lat is not None and home_lon is not None:
        vias = _attached_via_latlons(conn, event)
        if vias:
            minutes, points, source = route_via(
                conn, (home_lat, home_lon), vias, (to_lat, to_lon), cfg, now_utc=now)
            if source == "tomtom":
                return points, "tomtom"
            return [(home_lat, home_lon), (to_lat, to_lon)], "straight"

        if os.environ.get("TOMTOM_API_KEY", "").strip():
            cap = cfg.get("road_daily_cap", 100)
            if _tomtom_calls_today(conn) >= cap:
                audit.log(conn, "road.cap", {"event_id": event_id})
            else:
                depart_at = now if isinstance(now, str) else now.isoformat(timespec="seconds")
                points = tomtom_route_points(home_lat, home_lon, to_lat, to_lon, depart_at, cfg)
                if points is not None:
                    audit.log(conn, "road.call",
                               {"event_id": event_id, "points": len(points), "source": "tomtom"})
                    return points, "tomtom"
                audit.log(conn, "road.error", {"event_id": event_id})
        return [(home_lat, home_lon), (to_lat, to_lon)], "straight"

    return None, "none"


def route_via(conn, origin_latlon, via_latlons, dest_latlon, cfg, now_utc=None):
    """TomTom calculateRoute through one or more waypoints, in path form
    `{lat},{lon}:{via_lat},{via_lon}:...:{dest_lat},{dest_lon}` (TomTom's
    coordinate order is lat,lon, same as the rest of this module).

    Cap-guarded like compute_travel_min/route_for_event and shares the
    SAME daily counter (_tomtom_calls_today counts all road.call rows
    regardless of which function logged them) -- a detour probe spends
    from the same TomTom budget as a normal recompute. Only attempts the
    call when cfg's provider is "tomtom" and TOMTOM_API_KEY is set;
    otherwise -- like a cap hit or any HTTP/parse failure -- returns
    (None, None, "none") without raising.

    Returns (travel_min, route_points, source) where route_points is a
    flat list of (lat, lon) tuples from routes[0].legs[*].points[*] (same
    shape as tomtom_route_points), or (None, None, "none") on failure.
    """
    if cfg.get("road_provider", "tomtom") != "tomtom":
        return None, None, "none"
    key = os.environ.get("TOMTOM_API_KEY", "").strip()
    if not key:
        return None, None, "none"

    cap = cfg.get("road_daily_cap", 100)
    if _tomtom_calls_today(conn) >= cap:
        audit.log(conn, "road.cap", {"via": len(via_latlons)})
        return None, None, "none"

    now = now_utc or _now()
    depart_at = now if isinstance(now, str) else now.isoformat(timespec="seconds")

    o_lat, o_lon = origin_latlon
    d_lat, d_lon = dest_latlon
    waypoints = [f"{o_lat},{o_lon}"]
    waypoints += [f"{v_lat},{v_lon}" for v_lat, v_lon in via_latlons]
    waypoints.append(f"{d_lat},{d_lon}")
    locs = ":".join(waypoints)

    q = urllib.parse.urlencode({
        "key": key, "traffic": "true", "departAt": depart_at,
        "routeType": "fastest", "travelMode": "car",
    })
    url = f"https://api.tomtom.com/routing/1/calculateRoute/{locs}/json?{q}"
    try:
        data = json.loads(_http_get(url, cfg.get("road_timeout_sec", 10)))
        secs = data["routes"][0]["summary"]["travelTimeInSeconds"]
        minutes = max(1, math.ceil(secs / 60))
        points = []
        for leg in data["routes"][0]["legs"]:
            for p in leg["points"]:
                points.append((p["latitude"], p["longitude"]))
        audit.log(conn, "road.call",
                  {"via": len(via_latlons), "minutes": minutes, "source": "tomtom"})
        return minutes, (points or None), "tomtom"
    except Exception:
        audit.log(conn, "road.error", {"via": len(via_latlons)})
        return None, None, "none"


def direct_leg_min(conn, event, cfg):
    """Live TomTom minutes for the direct home->event leg (no waypoints) --
    the shared baseline detour_min compares every candidate against.

    Phase 7b, Task 3 (fam cal detours): iterating N candidate plans for
    the same event must NOT spend N+1 TomTom calls on the direct leg (one
    per detour_min call, as the original single-candidate detour_min did)
    -- that's the whole daily cap gone on one event. Callers computing
    detour_min for multiple candidates against the same event call this
    ONCE and pass the result into detour_min's direct_min= param instead.

    Same "never raises, None on anything not live" contract as
    detour_min: missing home/event coords or a non-tomtom route_via
    result -> None.
    """
    home_lat = cfg.get("road_home_lat")
    home_lon = cfg.get("road_home_lon")
    place = (event or {}).get("place") or {}
    dest_lat, dest_lon = place.get("lat"), place.get("lon")

    if None in (home_lat, home_lon, dest_lat, dest_lon):
        return None

    direct_min, _, direct_src = route_via(
        conn, (home_lat, home_lon), [], (dest_lat, dest_lon), cfg)
    if direct_src != "tomtom":
        return None
    return direct_min


def detour_min(conn, event, plan_place, cfg, direct_min=None):
    """How many extra minutes a stop at plan_place would add to the trip
    to event's place, vs going there directly -- both legs measured live
    via route_via (never the straight-line/manual/place fallback rungs,
    which aren't comparable traffic estimates). Requires home coords
    (cfg), event's place coords, and plan_place's lat/lon; missing
    coordinates, a non-tomtom provider, or either live call failing ->
    None (a detour figure built from a stale/estimated leg would be
    misleading, not merely approximate). Negative deltas (via ends up
    "faster" than direct, e.g. traffic-model noise) clamp to 0.

    direct_min: optional pre-computed direct-leg minutes (from
    direct_leg_min, or a prior detour_min call for the same event) --
    when given, the direct leg is NOT re-fetched from TomTom, letting a
    caller iterating several candidate plans for one event spend only one
    TomTom call on the direct leg total instead of one per candidate
    (Phase 7b, Task 3 budget concern). When omitted, behaves exactly as
    before: fetches the direct leg itself via route_via.
    """
    home_lat = cfg.get("road_home_lat")
    home_lon = cfg.get("road_home_lon")
    place = (event or {}).get("place") or {}
    dest_lat, dest_lon = place.get("lat"), place.get("lon")
    via_lat, via_lon = (plan_place or {}).get("lat"), (plan_place or {}).get("lon")

    if None in (home_lat, home_lon, dest_lat, dest_lon, via_lat, via_lon):
        return None

    if direct_min is None:
        direct_min, _, direct_src = route_via(
            conn, (home_lat, home_lon), [], (dest_lat, dest_lon), cfg)
        if direct_src != "tomtom":
            return None

    via_min, _, via_src = route_via(
        conn, (home_lat, home_lon), [(via_lat, via_lon)], (dest_lat, dest_lon), cfg)
    if via_src != "tomtom":
        return None

    return max(0, via_min - direct_min)
