"""Shopping list: manual entries plus auto-added items from low med stock.

Domain functions never commit — callers (tests, CLI) own the transaction,
mirroring plans.py/meds.py's pattern.
"""
from datetime import datetime, timezone

from fam import audit


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
