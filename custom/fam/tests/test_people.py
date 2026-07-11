import pytest

from fam import people

def test_add_and_resolve_by_alias_case_insensitive(db):
    people.add(db, "Тая", slug="taya", aliases=["Таюша", "дочь"])
    db.commit()
    for ref in ("Тая", "таюша", "ДОЧЬ", "taya"):
        p = people.resolve(db, ref)
        assert p and p["name"] == "Тая"

def test_group_resolves_with_members(db):
    people.add(db, "Мадина"); people.add(db, "Салтанат"); people.add(db, "Гульнара")
    g = people.add(db, "татешки", kind="group")
    for m in ("Мадина", "Салтанат", "Гульнара"):
        people.add_member(db, "татешки", m)
    db.commit()
    r = people.resolve(db, "татешки")
    assert r["kind"] == "group" and len(r["members"]) == 3

def test_resolve_unknown_returns_none(db):
    assert people.resolve(db, "Незнакомка") is None

def test_mutations_are_audited(db):
    from fam import audit
    people.add(db, "Мадина"); db.commit()
    assert audit.query(db, None, "people.add", None)

# --- Finding 1 (CRITICAL): Cyrillic case-variant alias misrouting ---
# SQLite's NOCASE PK on people_aliases only folds ASCII, so a naive INSERT
# would let "Таюша" and "таюша" coexist as two distinct alias rows pointing
# at two different people. people.alias()/people.add(aliases=...) must do a
# Python-level casefold uniqueness pre-check across ALL existing aliases AND
# person names before touching the DB.

def test_alias_cyrillic_case_variant_rejected(db):
    people.add(db, "ПерсонаА", aliases=["Таюша"])
    people.add(db, "ПерсонаБ")
    db.commit()
    with pytest.raises(ValueError):
        people.alias(db, "ПерсонаБ", "таюша")

def test_add_with_alias_cyrillic_case_variant_rejected(db):
    people.add(db, "ПерсонаА", aliases=["Таюша"])
    db.commit()
    with pytest.raises(ValueError):
        people.add(db, "ПерсонаВ", aliases=["ТАЮША"])
    # no partial insert: rollback the failed transaction, then confirm the
    # rejected alias/person never landed (only what was committed above).
    db.rollback()
    alias_rows = db.execute("SELECT alias FROM people_aliases").fetchall()
    assert [r["alias"] for r in alias_rows] == ["Таюша"]
    person_rows = db.execute("SELECT name FROM people").fetchall()
    assert {r["name"] for r in person_rows} == {"ПерсонаА"}

# --- Finding 3 (Important): ValueError contract tests ---

def test_add_duplicate_name_raises(db):
    people.add(db, "Дубликат"); db.commit()
    with pytest.raises(ValueError):
        people.add(db, "дубликат")

def test_add_member_kind_mismatch_raises(db):
    people.add(db, "Персона1")
    people.add(db, "Персона2")
    db.commit()
    with pytest.raises(ValueError):
        people.add_member(db, "Персона1", "Персона2")  # Персона1 is not a group

def test_alias_unknown_ref_raises(db):
    with pytest.raises(ValueError):
        people.alias(db, "НетТакого", "кличка")

# --- Finding 4 (Minor): audit noise on duplicate group_members insert ---

def test_add_member_duplicate_call_adds_no_new_audit_row(db):
    from fam import audit
    people.add(db, "Группа1", kind="group")
    people.add(db, "Участник1")
    db.commit()

    people.add_member(db, "Группа1", "Участник1")
    db.commit()
    count_after_first = len(audit.query(db, None, "people.member", None))
    assert count_after_first == 1

    people.add_member(db, "Группа1", "Участник1")  # duplicate: INSERT OR IGNORE no-ops
    db.commit()
    count_after_second = len(audit.query(db, None, "people.member", None))
    assert count_after_second == count_after_first
