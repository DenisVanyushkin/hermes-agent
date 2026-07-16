"""People glossary: persons and groups, aliases, group membership, resolve.

Domain functions never commit — callers (tests, CLI) own the transaction,
mirroring audit.py's pattern.
"""
from datetime import datetime, timezone

from fam import audit


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def get(conn, ref):
    """Resolve ref (id|name|alias|slug) to a person/group dict, or None.

    Lookup order for string refs: exact name (case-insensitive), then
    alias (case-insensitive), then slug (exact). SQLite's built-in NOCASE
    collation only folds ASCII (it leaves Cyrillic case untouched), so
    case-insensitive comparisons are done here with Python's str.casefold()
    instead of relying on SQL COLLATE NOCASE.
    """
    if ref is None:
        return None

    if isinstance(ref, int):
        row = conn.execute("SELECT * FROM people WHERE id=?", (ref,)).fetchone()
        return dict(row) if row else None

    ref_fold = ref.casefold()

    for row in conn.execute("SELECT * FROM people").fetchall():
        if row["name"].casefold() == ref_fold:
            return dict(row)

    for row in conn.execute(
        "SELECT a.alias AS _alias, p.* FROM people_aliases a "
        "JOIN people p ON p.id = a.person_id"
    ).fetchall():
        if row["_alias"].casefold() == ref_fold:
            d = dict(row)
            d.pop("_alias", None)
            return d

    row = conn.execute("SELECT * FROM people WHERE slug = ?", (ref,)).fetchone()
    if row:
        return dict(row)

    return None


def resolve(conn, text):
    """Like get(), but groups also carry a 'members' list of person dicts."""
    p = get(conn, text)
    if p is None:
        return None
    if p["kind"] == "group":
        rows = conn.execute(
            "SELECT pe.* FROM group_members gm "
            "JOIN people pe ON pe.id = gm.person_id "
            "WHERE gm.group_id = ? "
            "ORDER BY pe.name COLLATE NOCASE",
            (p["id"],),
        ).fetchall()
        p["members"] = [dict(r) for r in rows]
    return p


def _alias_conflict_owner(conn, alias_fold):
    """Return the name of the person/group that already owns alias_fold (a
    casefolded string), checking both person/group names and existing
    aliases. None if alias_fold is free.

    SQLite's built-in NOCASE collation (used by the people_aliases PK) only
    folds ASCII, so it will happily let "Таюша" and "таюша" coexist as two
    distinct rows pointing at two different people, silently misrouting
    lookups. This does the uniqueness check in Python with str.casefold()
    instead, which is correct for any Unicode script.
    """
    for row in conn.execute("SELECT name FROM people").fetchall():
        if row["name"].casefold() == alias_fold:
            return row["name"]
    for row in conn.execute(
        "SELECT a.alias AS _alias, p.name AS _name FROM people_aliases a "
        "JOIN people p ON p.id = a.person_id"
    ).fetchall():
        if row["_alias"].casefold() == alias_fold:
            return row["_name"]
    return None


def add(conn, name, kind="person", slug=None, aliases=()):
    """Create a person or group. Raises ValueError if the name already
    exists, or if any alias collides (casefolded) with an existing
    person/group name or alias, or with another alias in this same call.
    """
    name_fold = name.casefold()
    for row in conn.execute("SELECT name FROM people").fetchall():
        if row["name"].casefold() == name_fold:
            raise ValueError(f"person already exists: {name}")

    # Validate every alias up front, before inserting anything, so a
    # rejected alias never leaves a partial insert (a person row with no or
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
        "INSERT INTO people(name, kind, slug, created_at) VALUES (?,?,?,?)",
        (name, kind, slug, _now()),
    )
    person_id = cur.lastrowid

    for a in aliases:
        conn.execute(
            "INSERT INTO people_aliases(alias, person_id) VALUES (?,?)",
            (a, person_id),
        )

    audit.log(
        conn,
        "people.add",
        {"id": person_id, "name": name, "kind": kind, "slug": slug,
         "aliases": list(aliases)},
    )

    row = conn.execute("SELECT * FROM people WHERE id=?", (person_id,)).fetchone()
    return dict(row)


def alias(conn, person_ref, alias):
    """Attach an additional alias to an existing person/group. Raises
    ValueError if person_ref is unknown, or if alias (casefolded) collides
    with an existing person/group name or alias belonging to anyone.
    """
    p = get(conn, person_ref)
    if p is None:
        raise ValueError(f"unknown person: {person_ref}")

    owner = _alias_conflict_owner(conn, alias.casefold())
    if owner is not None:
        raise ValueError(f"alias already in use by {owner}: {alias}")

    conn.execute(
        "INSERT INTO people_aliases(alias, person_id) VALUES (?,?)",
        (alias, p["id"]),
    )
    audit.log(
        conn, "people.alias",
        {"person_id": p["id"], "name": p["name"], "alias": alias},
    )
    return None


def add_member(conn, group_ref, person_ref):
    """Add a person to a group. Both refs must resolve and have the right kind."""
    g = get(conn, group_ref)
    if g is None or g["kind"] != "group":
        raise ValueError(f"not a group: {group_ref}")

    p = get(conn, person_ref)
    if p is None or p["kind"] != "person":
        raise ValueError(f"not a person: {person_ref}")

    cur = conn.execute(
        "INSERT OR IGNORE INTO group_members(group_id, person_id) VALUES (?,?)",
        (g["id"], p["id"]),
    )
    # INSERT OR IGNORE no-ops on a duplicate membership (rowcount 0) — only
    # audit-log an actual insert, so re-adding an existing member doesn't
    # spam the audit trail.
    if cur.rowcount == 1:
        audit.log(
            conn, "people.member",
            {"group_id": g["id"], "group": g["name"],
             "person_id": p["id"], "person": p["name"]},
        )
    return None


def list_people(conn, kind=None):
    """List all people/groups, optionally filtered by kind."""
    if kind:
        rows = conn.execute(
            "SELECT * FROM people WHERE kind=? ORDER BY name COLLATE NOCASE",
            (kind,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM people ORDER BY name COLLATE NOCASE"
        ).fetchall()
    return [dict(r) for r in rows]
