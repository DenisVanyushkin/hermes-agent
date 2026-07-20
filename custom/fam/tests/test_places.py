import pytest

from fam import places


def test_add_and_resolve_by_alias(db):
    places.add(db, "Клиника Дента", address="ул. Абая 10",
               aliases=["стоматолог", "дента"])
    db.commit()
    for ref in ("Клиника Дента", "СТОМАТОЛОГ", "дента"):
        p = places.resolve(db, ref)
        assert p and p["name"] == "Клиника Дента"


def test_resolve_unknown_returns_none(db):
    assert places.resolve(db, "Луна-парк") is None


def test_mutations_are_audited(db):
    from fam import audit
    places.add(db, "Мега"); db.commit()
    assert audit.query(db, None, "places.add", None)

# --- Cyrillic case-variant alias misrouting (mirrors people.py's Finding 1) ---
# SQLite's NOCASE PK on place_aliases only folds ASCII, so a naive INSERT
# would let "Мега" and "мега" coexist as two distinct alias rows pointing at
# two different places. places.alias()/places.add(aliases=...) must do a
# Python-level casefold uniqueness pre-check across ALL existing place names
# AND aliases before touching the DB.

def test_alias_cyrillic_case_variant_rejected(db):
    places.add(db, "МестоА", aliases=["Мега"])
    places.add(db, "МестоБ")
    db.commit()
    with pytest.raises(ValueError):
        places.alias(db, "МестоБ", "мега")


def test_add_with_alias_cyrillic_case_variant_rejected(db):
    places.add(db, "МестоА", aliases=["Мега"])
    db.commit()
    with pytest.raises(ValueError):
        places.add(db, "МестоВ", aliases=["МЕГА"])
    # no partial insert: rollback the failed transaction, then confirm the
    # rejected alias/place never landed (only what was committed above).
    db.rollback()
    alias_rows = db.execute("SELECT alias FROM place_aliases").fetchall()
    assert [r["alias"] for r in alias_rows] == ["Мега"]
    place_rows = db.execute("SELECT name FROM places").fetchall()
    assert {r["name"] for r in place_rows} == {"МестоА"}

# --- ValueError contract tests ---

def test_add_duplicate_name_raises(db):
    places.add(db, "Дубликат"); db.commit()
    with pytest.raises(ValueError):
        places.add(db, "дубликат")


def test_alias_unknown_ref_raises(db):
    with pytest.raises(ValueError):
        places.alias(db, "НетТакого", "кличка")

# --- T4 review fast-follow: pure intra-batch dup (no pre-existing conflict) ---
# Two aliases in the SAME add() call that only collide with each other
# (neither collides with anything already in the DB). Code already handles
# this via seen_folds in places.add() — this is a regression guard, expected
# green immediately.

def test_add_intra_batch_duplicate_alias_rejected(db):
    with pytest.raises(ValueError):
        places.add(db, "Новая", aliases=["Кафе", "КАФЕ"])

# --- Task 5 (3a): places.update ---

def _seed_rules(db):
    from fam import rem
    rem.seed_default_rules(db)
    rem.migrate_rules_2c(db)


def test_update_unknown_place_raises(db):
    with pytest.raises(ValueError):
        places.update(db, "НетТакого", travel_min=10)


def test_update_no_fields_raises(db):
    places.add(db, "Мега"); db.commit()
    with pytest.raises(ValueError):
        places.update(db, "Мега")


def test_update_unknown_field_raises(db):
    places.add(db, "Мега"); db.commit()
    with pytest.raises(ValueError):
        places.update(db, "Мега", color="red")


def test_update_via_alias_sets_fields_and_audits(db):
    from fam import audit
    places.add(db, "Мега", aliases=["мол"])
    db.commit()
    p = places.update(db, "МОЛ", lat=43.2, lon=76.9, address="Розыбакиева 247")
    db.commit()
    assert p["lat"] == 43.2 and p["lon"] == 76.9
    assert p["address"] == "Розыбакиева 247"
    rows = audit.query(db, None, "places.update", None)
    assert rows
    payload = rows[0]["payload"]
    assert payload["id"] == p["id"]
    assert payload["events_touched"] == 0


def test_update_coords_ripples_future_active_events(db):
    from fam import audit, cal
    _seed_rules(db)
    places.add(db, "Мега"); db.commit()
    future = cal.add(db, "Кино", "2099-01-02T06:00:00+00:00", place="Мега")
    past = cal.add(db, "Было", "2000-01-02T06:00:00+00:00", place="Мега")
    cal.add(db, "Без места", "2099-01-03T06:00:00+00:00")
    # simulate a previously computed (now stale) road value
    for eid in (future["id"], past["id"]):
        db.execute(
            "UPDATE events SET travel_min_road=26, "
            "road_checked_at='2026-01-01T00:00:00+00:00' WHERE id=?", (eid,))
    db.commit()

    places.update(db, "Мега", lat=43.2, lon=76.9)
    db.commit()

    # future event's road freshness AND its stale computed figure are
    # invalidated (leave_at must fall back to manual/place immediately,
    # not anchor on garbage until T-120 recompute); past event untouched
    row = db.execute("SELECT travel_min_road, road_checked_at FROM events "
                      "WHERE id=?", (future["id"],)).fetchone()
    assert row["road_checked_at"] is None
    assert row["travel_min_road"] is None
    row = db.execute("SELECT travel_min_road, road_checked_at FROM events "
                      "WHERE id=?", (past["id"],)).fetchone()
    assert row["road_checked_at"] is not None
    assert row["travel_min_road"] == 26

    payload = audit.query(db, None, "places.update", None)[0]["payload"]
    assert payload["events_touched"] == 1


def test_update_travel_min_shifts_future_leave_at_chain(db):
    from fam import cal
    _seed_rules(db)
    places.add(db, "Мега"); db.commit()
    e = cal.add(db, "Кино", "2099-01-02T06:00:00+00:00", place="Мега")
    db.commit()
    q = ("SELECT fire_at_utc FROM reminders WHERE event_id=? AND status='pending' "
         "AND anchor='leave_at' AND label='пора выходить'")
    before = db.execute(q, (e["id"],)).fetchone()
    assert before["fire_at_utc"] == "2099-01-02T06:00:00+00:00"

    places.update(db, "Мега", travel_min=40)
    db.commit()

    after = db.execute(q, (e["id"],)).fetchone()
    assert after["fire_at_utc"] == "2099-01-02T05:20:00+00:00"


# --- Separator-insensitive name/alias matching ---
# "-", "_" and runs of whitespace are equivalent to a plain space when
# looking up a name or alias (fam.textnorm.fold). Storage is unchanged --
# only the comparison is normalized.

def test_resolve_name_separator_variants(db):
    places.add(db, "Гуля Тате"); db.commit()
    for ref in ("гуля-тате", "Гуля_Тате", "гуля   тате", "ГУЛЯ-ТАТЕ"):
        p = places.resolve(db, ref)
        assert p and p["name"] == "Гуля Тате"


def test_resolve_alias_separator_variants(db):
    places.add(db, "Клиника Дента", aliases=["Зубной Врач"]); db.commit()
    for ref in ("зубной-врач", "зубной_врач", "зубной   врач"):
        p = places.resolve(db, ref)
        assert p and p["name"] == "Клиника Дента"


def test_add_duplicate_name_separator_variant_rejected(db):
    places.add(db, "Гуля Тате"); db.commit()
    with pytest.raises(ValueError):
        places.add(db, "Гуля-Тате")


def test_add_duplicate_alias_separator_variant_rejected(db):
    places.add(db, "МестоА", aliases=["Зубной Врач"])
    places.add(db, "МестоБ")
    db.commit()
    with pytest.raises(ValueError):
        places.alias(db, "МестоБ", "зубной_врач")


def test_ambiguous_fold_match_prefers_exact_casefold(db):
    places.add(db, "Анна-Мария")
    places.add(db, "Анна Мария Вторая")
    db.commit()
    p = places.resolve(db, "анна-мария")
    assert p and p["name"] == "Анна-Мария"


def test_ambiguous_fold_match_returns_none_when_not_unique(db):
    # add()'s own duplicate-fold guard prevents creating this state through
    # the API -- simulate a legacy DB (rows inserted before this fold logic
    # existed) with a direct insert, bypassing add().
    db.execute(
        "INSERT INTO places(name, address, source, created_at) "
        "VALUES (?,?,?,datetime('now'))",
        ("Анна-Мария", "", "manual"),
    )
    db.execute(
        "INSERT INTO places(name, address, source, created_at) "
        "VALUES (?,?,?,datetime('now'))",
        ("Анна_Мария", "", "manual"),
    )
    db.commit()
    assert places.resolve(db, "анна   мария") is None
