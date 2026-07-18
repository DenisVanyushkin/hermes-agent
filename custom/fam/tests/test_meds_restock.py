"""Phase 5 Task 10 (finding F1): a purchase restocks meds.remaining, so
the "time to buy" loop actually closes. Domain fn meds.restock_by_name +
the `fam shop done <id> --restock N` CLI path that drives it.
"""
import json

import pytest

from fam import meds, shopping


# ---- meds.restock_by_name (domain) ----

def test_restock_by_name_increases_remaining(db):
    med_id = meds.add(db, "Магний", ["08:00"], remaining=1, threshold=1)
    db.commit()

    result = meds.restock_by_name(db, "Магний", 30)
    db.commit()

    assert result["restocked"] is True
    assert result["remaining"] == 31
    assert meds.get(db, med_id)["remaining"] == 31


def test_restock_by_name_lifts_above_threshold_closes_loop(db):
    # remaining==threshold==1: a take() here would re-trigger restock.
    # After restocking, a subsequent take must NOT re-trigger it.
    med_id = meds.add(db, "Магний", ["08:00"], remaining=1, threshold=1)
    db.commit()
    meds.restock_by_name(db, "Магний", 30)
    db.commit()

    cur = db.execute(
        "INSERT INTO med_intakes(med_id, plan_ts_utc, status, created_at) "
        "VALUES (?,?,?,?)",
        (med_id, "2026-07-20T03:00:00+00:00", "pending",
         "2026-07-20T03:00:00+00:00"),
    )
    take_result = meds.take(db, cur.lastrowid,
                            now_utc="2026-07-20T03:10:00+00:00")
    assert take_result["remaining"] == 30
    assert take_result["restock"] is False


def test_restock_by_name_casefold_match(db):
    med_id = meds.add(db, "Магний", ["08:00"], remaining=2, threshold=1)
    db.commit()

    result = meds.restock_by_name(db, "магний", 10)
    db.commit()

    assert result["restocked"] is True
    assert meds.get(db, med_id)["remaining"] == 12


def test_restock_by_name_untracked_is_noop(db):
    med_id = meds.add(db, "Магний", ["08:00"], remaining=None, threshold=0)
    db.commit()

    result = meds.restock_by_name(db, "Магний", 30)
    db.commit()

    assert result["restocked"] is False
    assert meds.get(db, med_id)["remaining"] is None


def test_restock_by_name_unknown_med_returns_none(db):
    # A plain grocery item ("Молоко") has no med -- restocking is a no-op.
    result = meds.restock_by_name(db, "Молоко", 3)
    assert result is None


def test_restock_by_name_rejects_nonpositive(db):
    meds.add(db, "Магний", ["08:00"], remaining=1, threshold=1)
    db.commit()
    with pytest.raises(ValueError):
        meds.restock_by_name(db, "Магний", 0)
    with pytest.raises(ValueError):
        meds.restock_by_name(db, "Магний", -5)


# ---- CLI: fam shop done <id> [--restock N] ----

def test_cli_shop_done_without_restock_leaves_remaining(db, capsys):
    from fam import cli
    med_id = meds.add(db, "Магний", ["08:00"], remaining=1, threshold=1)
    item_id = shopping.add_from_meds(db, "Магний")
    db.commit()

    rc = cli.main(["shop", "done", str(item_id), "--json"])
    assert rc == 0
    capsys.readouterr()
    # remaining untouched -- existing contract preserved (F1's current bug
    # is exactly this: done without restock strands remaining at 1).
    assert meds.get(db, med_id)["remaining"] == 1


def test_cli_shop_done_with_restock_increases_remaining(db, capsys):
    from fam import cli
    med_id = meds.add(db, "Магний", ["08:00"], remaining=1, threshold=1)
    item_id = shopping.add_from_meds(db, "Магний")
    db.commit()

    rc = cli.main(["shop", "done", str(item_id), "--restock", "30", "--json"])
    assert rc == 0
    capsys.readouterr()
    assert meds.get(db, med_id)["remaining"] == 31
    # item is done, so it's off the open shopping list.
    assert shopping.list_open(db) == []


def test_cli_shop_done_restock_is_idempotent(db, capsys):
    from fam import cli
    med_id = meds.add(db, "Магний", ["08:00"], remaining=1, threshold=1)
    item_id = shopping.add_from_meds(db, "Магний")
    db.commit()

    rc = cli.main(["shop", "done", str(item_id), "--restock", "30", "--json"])
    assert rc == 0
    capsys.readouterr()

    # Second done on an already-bought item is a no-op for stock: restock is
    # gated on the open->done transition, so remaining stays 31, not 61.
    rc2 = cli.main(["shop", "done", str(item_id), "--restock", "30", "--json"])
    assert rc2 == 0
    capsys.readouterr()
    assert meds.get(db, med_id)["remaining"] == 31
