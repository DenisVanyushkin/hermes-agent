from fam import db as famdb


def test_meta_get_default_none(db):
    assert famdb.meta_get(db, 'missing_key') is None


def test_meta_get_default_explicit(db):
    assert famdb.meta_get(db, 'missing_key', '0') == '0'


def test_meta_set_then_get_roundtrip(db):
    famdb.meta_set(db, 'flag', '1')
    db.commit()
    assert famdb.meta_get(db, 'flag') == '1'


def test_meta_set_upserts(db):
    famdb.meta_set(db, 'flag', '1')
    db.commit()
    famdb.meta_set(db, 'flag', '2')
    db.commit()
    assert famdb.meta_get(db, 'flag') == '2'
