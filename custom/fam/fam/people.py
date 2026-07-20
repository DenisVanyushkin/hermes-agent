"""People glossary: persons and groups, aliases, group membership, resolve.

Domain functions never commit — callers (tests, CLI) own the transaction,
mirroring audit.py's pattern.
"""
from datetime import datetime, timezone

from fam import audit
from fam.textnorm import fold


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _attach_home(conn, d):
    """Add a 'home_place' key (place dict, or None) to a person/group dict,
    joined from home_place_id. Local import to avoid a module-level
    people<->places import cycle (mirrors set_home's local import).
    """
    from fam import places
    home_id = d.get("home_place_id")
    d["home_place"] = places.get(conn, home_id) if home_id else None
    return d


def get(conn, ref):
    """Resolve ref (id|name|alias|slug) to a person/group dict, or None.
    The dict always carries a 'home_place' key (joined place dict, or None).

    Lookup order for string refs: exact name (case-insensitive), then
    alias (case-insensitive), then slug (exact), then separator-insensitive
    name/alias ("гуля-тате" == "гуля тате" == "гуля_тате", see
    fam.textnorm.fold) -- but only when the fold match is unique. If the DB
    already contains two different rows whose names/aliases fold to the
    same key (e.g. both "Анна-Мария" and "Анна Мария" exist), an exact
    casefold match always wins first; a ref that only matches at the fold
    level and is ambiguous between rows resolves to None rather than
    guessing. SQLite's built-in NOCASE collation only folds ASCII (it
    leaves Cyrillic case untouched), so case-insensitive comparisons are
    done here with Python's str.casefold() instead of relying on SQL
    COLLATE NOCASE.
    """
    if ref is None:
        return None

    row = None

    if isinstance(ref, int):
        row = conn.execute("SELECT * FROM people WHERE id=?", (ref,)).fetchone()
    else:
        ref_fold = ref.casefold()

        for r in conn.execute("SELECT * FROM people").fetchall():
            if r["name"].casefold() == ref_fold:
                row = r
                break

        if row is None:
            for r in conn.execute(
                "SELECT a.alias AS _alias, p.* FROM people_aliases a "
                "JOIN people p ON p.id = a.person_id"
            ).fetchall():
                if r["_alias"].casefold() == ref_fold:
                    row = r
                    break

        if row is None:
            row = conn.execute("SELECT * FROM people WHERE slug = ?", (ref,)).fetchone()

        if row is None:
            row = _fold_match(conn, ref)

    if row is None:
        return None

    d = dict(row)
    d.pop("_alias", None)
    return _attach_home(conn, d)


def _fold_match(conn, ref):
    """Separator/case-insensitive fallback lookup (fam.textnorm.fold) across
    person/group names and aliases. Returns the matching row only if exactly
    one distinct person/group folds to ref's key; None if no match or if
    the match is ambiguous across more than one person/group.
    """
    ref_fold = fold(ref)
    if not ref_fold:
        return None

    matches = {}  # person_id -> row
    for r in conn.execute("SELECT * FROM people").fetchall():
        if fold(r["name"]) == ref_fold:
            matches[r["id"]] = r
    for r in conn.execute(
        "SELECT a.alias AS _alias, p.* FROM people_aliases a "
        "JOIN people p ON p.id = a.person_id"
    ).fetchall():
        if fold(r["_alias"]) == ref_fold:
            matches.setdefault(r["id"], r)

    if len(matches) == 1:
        return next(iter(matches.values()))
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
    fam.textnorm.fold key), checking both person/group names and existing
    aliases. None if alias_fold is free.

    SQLite's built-in NOCASE collation (used by the people_aliases PK) only
    folds ASCII, so it will happily let "Таюша" and "таюша" coexist as two
    distinct rows pointing at two different people, silently misrouting
    lookups. This does the uniqueness check in Python with fam.textnorm.fold
    instead, which is correct for any Unicode script and treats -/_/multi-
    space as equivalent to a plain space (so "Гуля-Тате" is recognized as a
    duplicate of an existing "гуля тате").
    """
    for row in conn.execute("SELECT name FROM people").fetchall():
        if fold(row["name"]) == alias_fold:
            return row["name"]
    for row in conn.execute(
        "SELECT a.alias AS _alias, p.name AS _name FROM people_aliases a "
        "JOIN people p ON p.id = a.person_id"
    ).fetchall():
        if fold(row["_alias"]) == alias_fold:
            return row["_name"]
    return None


def add(conn, name, kind="person", slug=None, aliases=()):
    """Create a person or group. Raises ValueError if the name already
    exists, or if any alias collides (casefolded) with an existing
    person/group name or alias, or with another alias in this same call.
    """
    name_fold = fold(name)
    for row in conn.execute("SELECT name FROM people").fetchall():
        if fold(row["name"]) == name_fold:
            raise ValueError(f"person already exists: {name}")

    # Validate every alias up front, before inserting anything, so a
    # rejected alias never leaves a partial insert (a person row with no or
    # partial aliases attached).
    seen_folds = {}
    for a in aliases:
        a_fold = fold(a)
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

    owner = _alias_conflict_owner(conn, fold(alias))
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
    """List all people/groups, optionally filtered by kind. Each dict
    carries a 'home_place' key (joined place dict, or None)."""
    if kind:
        rows = conn.execute(
            "SELECT * FROM people WHERE kind=? ORDER BY name COLLATE NOCASE",
            (kind,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM people ORDER BY name COLLATE NOCASE"
        ).fetchall()
    return [_attach_home(conn, dict(r)) for r in rows]


def set_home(conn, person_ref, place_ref):
    """Attach/clear (place_ref=None) a person's home place. Raises
    ValueError if person_ref or place_ref is unknown (nothing is written
    on either error). Returns the updated person dict (see get()).
    """
    p = get(conn, person_ref)
    if p is None:
        raise ValueError(f"unknown person: {person_ref}")

    place_id = None
    if place_ref is not None:
        from fam import places
        pl = places.resolve(conn, place_ref)
        if pl is None:
            raise ValueError(f"unknown place: {place_ref}")
        place_id = pl["id"]

    conn.execute("UPDATE people SET home_place_id=? WHERE id=?", (place_id, p["id"]))
    audit.log(conn, "people.home", {"person": p["name"], "place_id": place_id})
    return get(conn, p["id"])
