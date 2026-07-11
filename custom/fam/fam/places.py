"""Places glossary: named locations, aliases, resolve.

Domain functions never commit — callers (tests, CLI) own the transaction,
mirroring people.py's pattern.
"""
from datetime import datetime, timezone

from fam import audit


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def get(conn, ref):
    """Resolve ref (id|name|alias) to a place dict, or None.

    Lookup order for string refs: exact name (case-insensitive), then
    alias (case-insensitive). SQLite's built-in NOCASE collation only
    folds ASCII (it leaves Cyrillic case untouched), so case-insensitive
    comparisons are done here with Python's str.casefold() instead of
    relying on SQL COLLATE NOCASE.
    """
    if ref is None:
        return None

    if isinstance(ref, int):
        row = conn.execute("SELECT * FROM places WHERE id=?", (ref,)).fetchone()
        return dict(row) if row else None

    ref_fold = ref.casefold()

    for row in conn.execute("SELECT * FROM places").fetchall():
        if row["name"].casefold() == ref_fold:
            return dict(row)

    for row in conn.execute(
        "SELECT a.alias AS _alias, p.* FROM place_aliases a "
        "JOIN places p ON p.id = a.place_id"
    ).fetchall():
        if row["_alias"].casefold() == ref_fold:
            d = dict(row)
            d.pop("_alias", None)
            return d

    return None


def resolve(conn, text):
    """Resolve text (name or alias, case-insensitive) to a place dict, or None."""
    return get(conn, text)


def _alias_conflict_owner(conn, alias_fold):
    """Return the name of the place that already owns alias_fold (a
    casefolded string), checking both place names and existing aliases.
    None if alias_fold is free.

    SQLite's built-in NOCASE collation (used by the place_aliases PK) only
    folds ASCII, so it will happily let "Мега" and "мега" coexist as two
    distinct rows pointing at two different places, silently misrouting
    lookups. This does the uniqueness check in Python with str.casefold()
    instead, which is correct for any Unicode script.
    """
    for row in conn.execute("SELECT name FROM places").fetchall():
        if row["name"].casefold() == alias_fold:
            return row["name"]
    for row in conn.execute(
        "SELECT a.alias AS _alias, p.name AS _name FROM place_aliases a "
        "JOIN places p ON p.id = a.place_id"
    ).fetchall():
        if row["_alias"].casefold() == alias_fold:
            return row["_name"]
    return None


def add(conn, name, address="", lat=None, lon=None, aliases=(), source="manual"):
    """Create a place. Raises ValueError if the name already exists, or if
    any alias collides (casefolded) with an existing place name or alias,
    or with another alias in this same call.
    """
    name_fold = name.casefold()
    for row in conn.execute("SELECT name FROM places").fetchall():
        if row["name"].casefold() == name_fold:
            raise ValueError(f"place already exists: {name}")

    # Validate every alias up front, before inserting anything, so a
    # rejected alias never leaves a partial insert (a place row with no or
    # partial aliases attached).
    seen_folds = {}
    for a in aliases:
        a_fold = a.casefold()
        owner = _alias_conflict_owner(conn, a_fold)
        if owner is not None:
            raise ValueError(f"alias already in use by {owner}: {a}")
        if a_fold in seen_folds:
            raise ValueError(f"duplicate alias in request: {a}")
        seen_folds[a_fold] = a

    cur = conn.execute(
        "INSERT INTO places(name, address, lat, lon, source, created_at) "
        "VALUES (?,?,?,?,?,?)",
        (name, address, lat, lon, source, _now()),
    )
    place_id = cur.lastrowid

    for a in aliases:
        conn.execute(
            "INSERT INTO place_aliases(alias, place_id) VALUES (?,?)",
            (a, place_id),
        )

    audit.log(
        conn,
        "places.add",
        {"id": place_id, "name": name, "address": address, "lat": lat,
         "lon": lon, "source": source, "aliases": list(aliases)},
    )

    row = conn.execute("SELECT * FROM places WHERE id=?", (place_id,)).fetchone()
    return dict(row)


def alias(conn, place_ref, alias):
    """Attach an additional alias to an existing place. Raises ValueError
    if place_ref is unknown, or if alias (casefolded) collides with an
    existing place name or alias belonging to anyone.
    """
    p = get(conn, place_ref)
    if p is None:
        raise ValueError(f"unknown place: {place_ref}")

    owner = _alias_conflict_owner(conn, alias.casefold())
    if owner is not None:
        raise ValueError(f"alias already in use by {owner}: {alias}")

    conn.execute(
        "INSERT INTO place_aliases(alias, place_id) VALUES (?,?)",
        (alias, p["id"]),
    )
    audit.log(
        conn, "places.alias",
        {"place_id": p["id"], "name": p["name"], "alias": alias},
    )
    return None


def list_all(conn):
    """List all places, ordered by name."""
    rows = conn.execute(
        "SELECT * FROM places ORDER BY name COLLATE NOCASE"
    ).fetchall()
    return [dict(r) for r in rows]
