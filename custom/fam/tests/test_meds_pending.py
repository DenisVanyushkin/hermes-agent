"""Phase 5 Task 8 review round 1: fam med list --pending -- a direct
CLI lookup for pending med_intakes, replacing the amina-fam skill's
fragile audit-log join (gate.sent "name" + tick.med intake_id, matched
by timestamp). That join breaks in two ways: (1) the digest logs a
gate.sent row for EVERY today's dose, not just the ones actually
delivered as a tick.med reminder -- a grep can land on the digest's
row (no tick.med twin) and wrongly conclude "nothing pending"; (2)
tick.med carries no name, so two doses due at the same timestamp are
indistinguishable. meds.list_pending()/`fam med list` sidestep both by
reading med_intakes directly.
"""
import json

import pytest

from fam import meds


def _insert_intake(db, med_id, plan_ts_utc="2026-07-20T03:00:00+00:00",
                    status="pending"):
    cur = db.execute(
        "INSERT INTO med_intakes(med_id, plan_ts_utc, taken_ts_utc, status, "
        "series_next_utc, created_at) VALUES (?,?,?,?,?,?)",
        (med_id, plan_ts_utc, None, status, None, plan_ts_utc),
    )
    return cur.lastrowid


# ---- meds.list_pending ----

def test_list_pending_returns_expected_fields(db):
    med_id = meds.add(db, "Магний", ["08:00"], dose="200mg")
    db.commit()
    intake_id = _insert_intake(db, med_id)

    rows = meds.list_pending(db)

    assert len(rows) == 1
    r = rows[0]
    assert r["intake_id"] == intake_id
    assert r["med_id"] == med_id
    assert r["name"] == "Магний"
    assert r["plan_ts_utc"] == "2026-07-20T03:00:00+00:00"
    assert r["status"] == "pending"


def test_list_pending_excludes_taken_and_skipped(db):
    med_id = meds.add(db, "Магний", ["08:00"])
    db.commit()
    _insert_intake(db, med_id, plan_ts_utc="2026-07-20T03:00:00+00:00",
                    status="taken")
    _insert_intake(db, med_id, plan_ts_utc="2026-07-20T15:00:00+00:00",
                    status="skipped")
    pending_id = _insert_intake(db, med_id, plan_ts_utc="2026-07-21T03:00:00+00:00")

    rows = meds.list_pending(db)

    assert [r["intake_id"] for r in rows] == [pending_id]


def test_list_pending_sorted_by_plan_ts_utc(db):
    med_id = meds.add(db, "Магний", ["08:00", "20:00"])
    db.commit()
    later = _insert_intake(db, med_id, plan_ts_utc="2026-07-20T15:00:00+00:00")
    earlier = _insert_intake(db, med_id, plan_ts_utc="2026-07-20T03:00:00+00:00")

    rows = meds.list_pending(db)

    assert [r["intake_id"] for r in rows] == [earlier, later]


def test_list_pending_disambiguates_concurrent_doses_by_name(db):
    """The review's core failure mode: two meds due at the exact same
    plan_ts_utc must remain distinguishable -- the old audit-join
    protocol had no name on the tick.med row to split the tie.
    """
    med_a = meds.add(db, "Магний", ["08:00"])
    med_b = meds.add(db, "Омега-3", ["08:00"])
    db.commit()
    same_ts = "2026-07-20T03:00:00+00:00"
    intake_a = _insert_intake(db, med_a, plan_ts_utc=same_ts)
    intake_b = _insert_intake(db, med_b, plan_ts_utc=same_ts)

    rows = meds.list_pending(db)

    names_by_id = {r["intake_id"]: r["name"] for r in rows}
    assert names_by_id[intake_a] == "Магний"
    assert names_by_id[intake_b] == "Омега-3"


def test_list_pending_empty(db):
    assert meds.list_pending(db) == []


# ---- CLI ----

def test_cli_med_list_pending_json(db, capsys):
    from fam import cli
    med_id = meds.add(db, "Магний", ["08:00"])
    db.commit()
    intake_id = _insert_intake(db, med_id)
    db.commit()

    rc = cli.main(["med", "list", "--pending", "--json"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert len(data) == 1
    assert data[0]["intake_id"] == intake_id
    assert data[0]["name"] == "Магний"
    assert data[0]["status"] == "pending"


def test_cli_med_list_default_is_pending(db, capsys):
    """No other status is worth listing (Denis's call, 5 T8 review) --
    `fam med list` with no --pending flag still returns pending-only.
    """
    from fam import cli
    med_id = meds.add(db, "Магний", ["08:00"])
    db.commit()
    intake_id = _insert_intake(db, med_id)
    _insert_intake(db, med_id, plan_ts_utc="2026-07-20T15:00:00+00:00",
                    status="taken")
    db.commit()

    rc = cli.main(["med", "list", "--json"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert [r["intake_id"] for r in data] == [intake_id]


def test_cli_med_list_empty_json(db, capsys):
    from fam import cli
    rc = cli.main(["med", "list", "--json"])
    assert rc == 0
    assert json.loads(capsys.readouterr().out) == []


def test_cli_med_list_text_output(db, capsys):
    from fam import cli
    med_id = meds.add(db, "Магний", ["08:00"])
    db.commit()
    intake_id = _insert_intake(db, med_id)
    db.commit()

    rc = cli.main(["med", "list"])
    assert rc == 0
    out = capsys.readouterr().out
    assert str(intake_id) in out
    assert "Магний" in out
