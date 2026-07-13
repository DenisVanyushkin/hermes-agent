from datetime import datetime

import pytest
from fam import audit


def test_log_ts_utc_format_matches_isoformat_seconds_shape(db):
    # Phase 6a closeout regression lock: maint.prune_audit_log's
    # retention cutoff (maint.py) compares ts_utc via plain string
    # comparison against another isoformat(timespec="seconds") string --
    # that only works if every row written by audit.log() keeps this
    # exact shape. Locks it via the REAL log(), not a hand-built literal.
    rowid = audit.log(db, "test.kind", {})
    db.commit()
    row = db.execute(
        "SELECT ts_utc FROM audit_log WHERE id = ?", (rowid,)
    ).fetchone()
    ts = row["ts_utc"]

    parsed = datetime.fromisoformat(ts)
    assert parsed.tzinfo is not None
    assert "T" in ts
    assert ts.endswith("+00:00")


def test_log_and_query_roundtrip(db):
    audit.log(db, "cal.add", {"event_id": 1, "title": "врач"})
    db.commit()
    rows = audit.query(db, since_utc=None, kind_prefix="cal.", grep=None)
    assert len(rows) == 1
    assert rows[0]["kind"] == "cal.add"
    assert rows[0]["payload"]["title"] == "врач"

def test_query_filters(db):
    audit.log(db, "cal.add", {"t": "a"}); audit.log(db, "people.add", {"t": "b"})
    db.commit()
    assert len(audit.query(db, None, "people.", None)) == 1
    assert len(audit.query(db, None, None, '"t": "a"')) == 1
    assert audit.query(db, "2999-01-01T00:00:00+00:00", None, None) == []

def test_query_rejects_non_positive_limit(db):
    audit.log(db, "cal.add", {"t": "a"})
    db.commit()
    with pytest.raises(ValueError):
        audit.query(db, None, None, None, limit=0)
    with pytest.raises(ValueError):
        audit.query(db, None, None, None, limit=-1)
