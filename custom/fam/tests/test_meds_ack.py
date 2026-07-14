"""Phase 5 Task 5: ack (taken/skip) of a med_intakes dose, plus the
threshold restock trigger. Unlike test_tick_meds_series.py (the
minute-tick's own persistent-reminder series), this exercises the ack
call itself -- meds.take/meds.skip -- and the CLI wrapping them.
"""
import json

import pytest

from fam import meds, shopping


def _insert_intake(db, med_id, plan_ts_utc="2026-07-20T03:00:00+00:00",
                    series_next_utc=None, status="pending"):
    cur = db.execute(
        "INSERT INTO med_intakes(med_id, plan_ts_utc, taken_ts_utc, status, "
        "series_next_utc, created_at) VALUES (?,?,?,?,?,?)",
        (med_id, plan_ts_utc, None, status, series_next_utc, plan_ts_utc),
    )
    return cur.lastrowid


# ---- take ----

def test_take_decrements_remaining_and_clears_series(db):
    med_id = meds.add(db, "Магний", ["08:00"], remaining=10, threshold=2)
    db.commit()
    intake_id = _insert_intake(db, med_id,
                                series_next_utc="2026-07-20T03:45:00+00:00")

    result = meds.take(db, intake_id, now_utc="2026-07-20T03:10:00+00:00")
    db.commit()

    assert result["status"] == "taken"
    assert result["taken_ts_utc"] == "2026-07-20T03:10:00+00:00"
    assert result["series_next_utc"] is None
    assert result["remaining"] == 9
    assert result["restock"] is False
    assert result["restock_added"] is False

    row = db.execute(
        "SELECT status, taken_ts_utc, series_next_utc FROM med_intakes "
        "WHERE id=?", (intake_id,),
    ).fetchone()
    assert row["status"] == "taken"
    assert row["taken_ts_utc"] == "2026-07-20T03:10:00+00:00"
    assert row["series_next_utc"] is None

    m = meds.get(db, med_id)
    assert m["remaining"] == 9


def test_take_floors_remaining_at_zero(db):
    med_id = meds.add(db, "Аспирин", ["08:00"], remaining=0, threshold=0)
    db.commit()
    intake_id = _insert_intake(db, med_id)

    result = meds.take(db, intake_id)
    db.commit()

    assert result["remaining"] == 0
    m = meds.get(db, med_id)
    assert m["remaining"] == 0


def test_take_untracked_remaining_stays_none_no_restock(db):
    med_id = meds.add(db, "Витамин D", ["08:00"])  # remaining=None default
    db.commit()
    intake_id = _insert_intake(db, med_id)

    result = meds.take(db, intake_id)
    db.commit()

    assert result["remaining"] is None
    assert result["restock"] is False
    assert result["restock_added"] is False
    assert shopping.list_open(db) == []

    m = meds.get(db, med_id)
    assert m["remaining"] is None


def test_take_triggers_restock_when_remaining_hits_threshold(db):
    med_id = meds.add(db, "Магний", ["08:00"], remaining=3, threshold=2)
    db.commit()
    intake_id = _insert_intake(db, med_id)

    result = meds.take(db, intake_id)
    db.commit()

    assert result["remaining"] == 2
    assert result["restock"] is True
    assert result["restock_added"] is True

    items = shopping.list_open(db)
    assert len(items) == 1
    assert items[0]["name"] == "Магний"
    assert items[0]["source"] == "meds"


def test_take_triggers_restock_at_zero_with_default_threshold(db):
    med_id = meds.add(db, "Омега-3", ["08:00"], remaining=1, threshold=0)
    db.commit()
    intake_id = _insert_intake(db, med_id)

    result = meds.take(db, intake_id)
    db.commit()

    assert result["remaining"] == 0
    assert result["restock"] is True
    assert result["restock_added"] is True


def test_take_does_not_restock_above_threshold(db):
    med_id = meds.add(db, "Магний", ["08:00"], remaining=10, threshold=2)
    db.commit()
    intake_id = _insert_intake(db, med_id)

    result = meds.take(db, intake_id)
    db.commit()

    assert result["remaining"] == 9
    assert result["restock"] is False
    assert result["restock_added"] is False
    assert shopping.list_open(db) == []


def test_take_restock_dedups_across_meds_with_same_name(db):
    med_id_1 = meds.add(db, "Магний", ["08:00"], remaining=1, threshold=1)
    med_id_2 = meds.add(db, "Магний", ["20:00"], remaining=1, threshold=1)
    db.commit()
    intake_1 = _insert_intake(db, med_id_1,
                               plan_ts_utc="2026-07-20T03:00:00+00:00")
    intake_2 = _insert_intake(db, med_id_2,
                               plan_ts_utc="2026-07-20T15:00:00+00:00")

    result_1 = meds.take(db, intake_1)
    db.commit()
    assert result_1["restock"] is True
    assert result_1["restock_added"] is True

    result_2 = meds.take(db, intake_2)
    db.commit()
    assert result_2["restock"] is True
    assert result_2["restock_added"] is False  # dedup: already an open row

    assert len(shopping.list_open(db)) == 1


def test_take_unknown_intake_raises(db):
    with pytest.raises(ValueError):
        meds.take(db, 9999)
    assert db.execute("SELECT COUNT(*) FROM audit_log WHERE kind='meds.take'"
                       ).fetchone()[0] == 0


def test_take_on_already_taken_raises_no_double_decrement(db):
    """Review finding (5 T5): take() must reject a retry/duplicate ack
    on a dose that is already status='taken' -- otherwise remaining
    decrements a second time (double stock deduction), taken_ts_utc is
    overwritten, and a second meds.take audit row is written. A retried
    skill call or a duplicate "выпила" must be a no-op error, not a
    second write.
    """
    med_id = meds.add(db, "Магний", ["08:00"], remaining=10, threshold=2)
    db.commit()
    intake_id = _insert_intake(db, med_id)

    first = meds.take(db, intake_id, now_utc="2026-07-20T03:10:00+00:00")
    db.commit()
    assert first["remaining"] == 9

    with pytest.raises(ValueError):
        meds.take(db, intake_id, now_utc="2026-07-20T03:20:00+00:00")
    db.commit()

    m = meds.get(db, med_id)
    assert m["remaining"] == 9  # not decremented a second time

    row = db.execute(
        "SELECT taken_ts_utc FROM med_intakes WHERE id=?", (intake_id,)
    ).fetchone()
    assert row["taken_ts_utc"] == "2026-07-20T03:10:00+00:00"  # not overwritten

    count = db.execute(
        "SELECT COUNT(*) FROM audit_log WHERE kind='meds.take'"
    ).fetchone()[0]
    assert count == 1  # no second audit entry


def test_take_audits(db):
    med_id = meds.add(db, "Магний", ["08:00"], remaining=5, threshold=1)
    db.commit()
    intake_id = _insert_intake(db, med_id)

    meds.take(db, intake_id)
    db.commit()

    row = db.execute(
        "SELECT payload FROM audit_log WHERE kind='meds.take' "
        "ORDER BY id DESC LIMIT 1"
    ).fetchone()
    payload = json.loads(row["payload"])
    assert payload["intake_id"] == intake_id
    assert payload["med_id"] == med_id
    assert payload["remaining"] == 4
    assert payload["restock"] is False


# ---- skip ----

def test_skip_only_this_dose_remaining_untouched(db):
    med_id = meds.add(db, "Магний", ["08:00"], remaining=10, threshold=2)
    db.commit()
    intake_id = _insert_intake(db, med_id,
                                series_next_utc="2026-07-20T03:45:00+00:00")

    result = meds.skip(db, intake_id)
    db.commit()

    assert result["status"] == "skipped"
    assert result["series_next_utc"] is None

    row = db.execute(
        "SELECT status, series_next_utc, taken_ts_utc FROM med_intakes "
        "WHERE id=?", (intake_id,),
    ).fetchone()
    assert row["status"] == "skipped"
    assert row["series_next_utc"] is None
    assert row["taken_ts_utc"] is None

    m = meds.get(db, med_id)
    assert m["remaining"] == 10  # untouched
    assert shopping.list_open(db) == []


def test_skip_does_not_affect_other_intakes(db):
    med_id = meds.add(db, "Магний", ["08:00", "20:00"], remaining=10)
    db.commit()
    intake_1 = _insert_intake(db, med_id,
                               plan_ts_utc="2026-07-20T03:00:00+00:00")
    intake_2 = _insert_intake(db, med_id,
                               plan_ts_utc="2026-07-20T15:00:00+00:00",
                               series_next_utc="2026-07-20T15:00:00+00:00")

    meds.skip(db, intake_1)
    db.commit()

    row2 = db.execute(
        "SELECT status, series_next_utc FROM med_intakes WHERE id=?",
        (intake_2,),
    ).fetchone()
    assert row2["status"] == "pending"
    assert row2["series_next_utc"] == "2026-07-20T15:00:00+00:00"


def test_skip_unknown_intake_raises(db):
    with pytest.raises(ValueError):
        meds.skip(db, 9999)
    assert db.execute("SELECT COUNT(*) FROM audit_log WHERE kind='meds.skip'"
                       ).fetchone()[0] == 0


def test_skip_on_already_taken_raises_status_unchanged(db):
    """Review finding (5 T5): skip() must reject a dose that is already
    status='taken' -- otherwise the status rolls back to 'skipped' even
    though remaining was already decremented, a rescan/resync
    desync between med_intakes.status and the actual stock deduction.
    """
    med_id = meds.add(db, "Магний", ["08:00"], remaining=10, threshold=2)
    db.commit()
    intake_id = _insert_intake(db, med_id)

    meds.take(db, intake_id)
    db.commit()

    with pytest.raises(ValueError):
        meds.skip(db, intake_id)
    db.commit()

    row = db.execute(
        "SELECT status FROM med_intakes WHERE id=?", (intake_id,)
    ).fetchone()
    assert row["status"] == "taken"  # not rolled back to skipped

    count = db.execute(
        "SELECT COUNT(*) FROM audit_log WHERE kind='meds.skip'"
    ).fetchone()[0]
    assert count == 0  # no skip audit for the rejected call


def test_skip_audits(db):
    med_id = meds.add(db, "Магний", ["08:00"], remaining=10)
    db.commit()
    intake_id = _insert_intake(db, med_id)

    meds.skip(db, intake_id)
    db.commit()

    row = db.execute(
        "SELECT payload FROM audit_log WHERE kind='meds.skip' "
        "ORDER BY id DESC LIMIT 1"
    ).fetchone()
    payload = json.loads(row["payload"])
    assert payload["intake_id"] == intake_id
    assert payload["med_id"] == med_id


# --- CLI ---

def test_cli_med_taken(db, capsys):
    from fam import cli
    med_id = meds.add(db, "Магний", ["08:00"], remaining=5, threshold=1)
    db.commit()
    intake_id = _insert_intake(db, med_id)
    db.commit()

    rc = cli.main(["med", "taken", str(intake_id), "--json"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["status"] == "taken"
    assert data["remaining"] == 4
    assert data["restock"] is False


def test_cli_med_taken_triggers_restock(db, capsys):
    from fam import cli
    med_id = meds.add(db, "Магний", ["08:00"], remaining=1, threshold=1)
    db.commit()
    intake_id = _insert_intake(db, med_id)
    db.commit()

    rc = cli.main(["med", "taken", str(intake_id), "--json"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["restock"] is True
    assert data["restock_added"] is True
    assert len(shopping.list_open(db)) == 1


def test_cli_med_taken_text_output_mentions_restock(db, capsys):
    from fam import cli
    med_id = meds.add(db, "Магний", ["08:00"], remaining=1, threshold=1)
    db.commit()
    intake_id = _insert_intake(db, med_id)
    db.commit()

    rc = cli.main(["med", "taken", str(intake_id)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "restock" in out.lower() or "куп" in out.lower()


def test_cli_med_taken_unknown_exits_2(db, capsys):
    from fam import cli
    rc = cli.main(["med", "taken", "999999"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "999999" in err or "intake" in err.lower()


def test_cli_med_skip(db, capsys):
    from fam import cli
    med_id = meds.add(db, "Магний", ["08:00"], remaining=5)
    db.commit()
    intake_id = _insert_intake(db, med_id)
    db.commit()

    rc = cli.main(["med", "skip", str(intake_id), "--json"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["status"] == "skipped"

    m = meds.get(db, med_id)
    assert m["remaining"] == 5


def test_cli_med_skip_unknown_exits_2(db, capsys):
    from fam import cli
    rc = cli.main(["med", "skip", "999999"])
    assert rc == 2


def test_cli_med_taken_audits(db, capsys):
    from fam import cli
    med_id = meds.add(db, "Магний", ["08:00"], remaining=5)
    db.commit()
    intake_id = _insert_intake(db, med_id)
    db.commit()

    cli.main(["med", "taken", str(intake_id), "--json"])
    capsys.readouterr()
    assert db.execute(
        "SELECT COUNT(*) FROM audit_log WHERE kind='meds.take'"
    ).fetchone()[0] == 1


def test_cli_med_skip_audits(db, capsys):
    from fam import cli
    med_id = meds.add(db, "Магний", ["08:00"], remaining=5)
    db.commit()
    intake_id = _insert_intake(db, med_id)
    db.commit()

    cli.main(["med", "skip", str(intake_id), "--json"])
    capsys.readouterr()
    assert db.execute(
        "SELECT COUNT(*) FROM audit_log WHERE kind='meds.skip'"
    ).fetchone()[0] == 1


def test_take_with_tracked_remaining_writes_no_redundant_meds_edit_audit(db):
    # Backlog: take()'s internal call to edit(conn, med_id,
    # remaining=remaining) writes its own meds.edit audit row (the same
    # remaining value take()'s own meds.take audit already carries) --
    # a redundant double-audit of one logical event (decrementing stock
    # as part of acking a dose). A single take() must produce exactly
    # one audit row for the decrement (meds.take), not two.
    med_id = meds.add(db, "Магний", ["08:00"], remaining=10, threshold=2)
    db.commit()
    intake_id = _insert_intake(db, med_id)

    meds.take(db, intake_id, now_utc="2026-07-20T03:10:00+00:00")
    db.commit()

    assert db.execute(
        "SELECT COUNT(*) FROM audit_log WHERE kind='meds.edit'"
    ).fetchone()[0] == 0
    assert db.execute(
        "SELECT COUNT(*) FROM audit_log WHERE kind='meds.take'"
    ).fetchone()[0] == 1

    m = meds.get(db, med_id)
    assert m["remaining"] == 9
