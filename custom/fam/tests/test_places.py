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
