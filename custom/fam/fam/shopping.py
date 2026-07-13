"""Shopping list: manual entries plus auto-added items from low med stock.

Domain functions never commit — callers (tests, CLI) own the transaction,
mirroring plans.py/meds.py's pattern.
"""
from datetime import datetime, timezone

from fam import audit, places, road


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def add(conn, name, qty="", added_by="", source="manual"):
    """Create a shopping item. Returns the new item's id."""
    now = _now()
    cur = conn.execute(
        "INSERT INTO shopping(name, qty, added_by, source, status, created_at) "
        "VALUES (?,?,?,?,?,?)",
        (name, qty, added_by, source, "open", now),
    )
    item_id = cur.lastrowid

    audit.log(
        conn, "shop.add",
        {"id": item_id, "name": name, "qty": qty, "added_by": added_by,
         "source": source},
    )

    return item_id


def get(conn, item_id):
    """Fetch a shopping item by id, or None if unknown."""
    row = conn.execute("SELECT * FROM shopping WHERE id=?", (item_id,)).fetchone()
    if row is None:
        return None
    return dict(row)


def list_open(conn):
    """List shopping items with status='open', ordered by created_at."""
    rows = conn.execute(
        "SELECT * FROM shopping WHERE status='open' ORDER BY created_at"
    ).fetchall()
    return [dict(r) for r in rows]


def mark_done(conn, item_id):
    """Mark a shopping item done (stamps done_at). Returns False on an
    unknown item_id (no write, no audit); True on success.
    """
    existing = conn.execute("SELECT * FROM shopping WHERE id=?", (item_id,)).fetchone()
    if existing is None:
        return False

    conn.execute(
        "UPDATE shopping SET status='done', done_at=? WHERE id=?",
        (_now(), item_id),
    )
    audit.log(conn, "shop.done", {"id": item_id, "name": existing["name"]})
    return True


def add_from_meds(conn, med_name, qty=""):
    """Auto-add a shopping item for a low-stock med. Dedup: if an open
    row already exists with the same name (casefold-compared, so Кириллица
    like "Магний"/"МАГНИЙ" match) and source='meds', return None without
    inserting -- avoids piling up duplicate restock reminders across
    ticks. Otherwise adds with source='meds' (audited via add()'s
    shop.add, payload includes source:"meds"). A manual entry with the
    same name never blocks this -- only other source='meds' open rows
    count for the dedup check.
    """
    target = med_name.casefold()
    existing_rows = conn.execute(
        "SELECT name FROM shopping WHERE status='open' AND source='meds'"
    ).fetchall()
    for row in existing_rows:
        if row["name"].casefold() == target:
            return None

    return add(conn, med_name, qty=qty, source="meds")


def match_enroute(conn, event, cfg, now_utc=None):
    """Which categorized places (grocery/pharmacy, places.category --
    phase 5 T6) are 'on the way' for this event, with a non-empty
    matching shopping list.

    A categorized place matches when it has coordinates AND is within
    corridor distance of the event's route -- cfg["enroute_walk_km"]
    (default 0.5) when event.transport == "walk", else
    cfg["enroute_car_km"] (default 3.0). Same corridor-threshold pattern
    as plans.match_enroute (3b), but geo-only -- there's no "person"
    reason here, a place is either in the corridor or it isn't.

    The place's category also gates WHICH open shopping items count as a
    match, and the list must be non-empty for that place to be reported
    at all:

    - category == 'grocery': any open shopping item (source doesn't
      matter -- a grocery run covers manual entries and meds-restock
      entries alike).
    - category == 'pharmacy': only open shopping items with
      source='meds' (a pharmacy stop is pointless for a manual grocery
      item).

    road.route_for_event (3b) is called at most once, and only when at
    least one categorized place has coordinates -- skips the (possibly
    TomTom-backed, daily-capped) call entirely when there is nothing to
    match against, same perf guard as plans.match_enroute. Never raises.

    Returns a list of {"category": "grocery"|"pharmacy", "place": <dict>,
    "items": [name, ...]}, one entry per matching place (deduped by
    place -- category is a single column, so a place can only ever
    contribute one entry). items are shopping-item names, capped at
    cfg["enroute_max_items"].
    """
    categorized = [
        p for p in places.list_all(conn)
        if p.get("category") in ("grocery", "pharmacy")
        and p.get("lat") is not None and p.get("lon") is not None
    ]
    if not categorized:
        return []

    route_points, source = road.route_for_event(conn, event, cfg, now_utc=now_utc)
    if source == "none" or not route_points:
        return []

    if event.get("transport") == "walk":
        threshold_km = cfg.get("enroute_walk_km", 0.5)
    else:
        threshold_km = cfg.get("enroute_car_km", 3.0)

    max_items = cfg.get("enroute_max_items", 2)
    open_items = list_open(conn)
    meds_items = [i for i in open_items if i.get("source") == "meds"]

    results = []
    for place in categorized:
        dist_km = road.point_to_route_km(place["lat"], place["lon"], route_points)
        if dist_km > threshold_km:
            continue

        candidate_items = open_items if place["category"] == "grocery" else meds_items
        if not candidate_items:
            continue

        results.append({
            "category": place["category"],
            "place": place,
            "items": [i["name"] for i in candidate_items[:max_items]],
        })

    return results
