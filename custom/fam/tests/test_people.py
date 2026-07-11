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
