import json
import pytest
from fam import meds


def test_add_and_list_roundtrips(db):
    med_id = meds.add(db, "Магний", ["08:00", "20:00"], dose="1 таб",
                       remaining=30, threshold=5)
    db.commit()
    assert isinstance(med_id, int)

    rows = meds.list(db)
    assert len(rows) == 1
    m = rows[0]
    assert m["id"] == med_id
    assert m["name"] == "Магний"
    assert m["dose"] == "1 таб"
    assert m["times"] == ["08:00", "20:00"]
    assert m["remaining"] == 30
    assert m["threshold"] == 5
    assert m["enabled"] is True


def test_add_sorts_and_dedupes_times(db):
    med_id = meds.add(db, "Витамин D", ["20:00", "08:00", "08:00"])
    db.commit()
    m = meds.get(db, med_id)
    assert m["times"] == ["08:00", "20:00"]


def test_add_invalid_time_raises(db):
    with pytest.raises(ValueError):
        meds.add(db, "Плохое лекарство", ["8am"])
    assert db.execute("SELECT COUNT(*) FROM meds").fetchone()[0] == 0


def test_add_empty_times_raises(db):
    with pytest.raises(ValueError):
        meds.add(db, "Пустое", [])
    assert db.execute("SELECT COUNT(*) FROM meds").fetchone()[0] == 0


def test_get_unknown_returns_none(db):
    assert meds.get(db, 9999) is None


def test_list_excludes_disabled_by_default(db):
    med_id = meds.add(db, "Отключённое", ["08:00"])
    db.commit()
    meds.edit(db, med_id, enabled=False)
    db.commit()
    assert meds.list(db) == []
    assert len(meds.list(db, include_disabled=True)) == 1


def test_edit_changes_remaining_and_times(db):
    med_id = meds.add(db, "Аспирин", ["08:00"], remaining=10)
    db.commit()
    ok = meds.edit(db, med_id, remaining=8, times=["09:00", "21:00"])
    db.commit()
    assert ok is True
    m = meds.get(db, med_id)
    assert m["remaining"] == 8
    assert m["times"] == ["09:00", "21:00"]


def test_edit_invalid_time_raises_and_does_not_write(db):
    med_id = meds.add(db, "Аспирин", ["08:00"])
    db.commit()
    with pytest.raises(ValueError):
        meds.edit(db, med_id, times=["25:99"])
    m = meds.get(db, med_id)
    assert m["times"] == ["08:00"]


def test_edit_empty_times_raises(db):
    med_id = meds.add(db, "Аспирин", ["08:00"])
    db.commit()
    with pytest.raises(ValueError):
        meds.edit(db, med_id, times=[])
    m = meds.get(db, med_id)
    assert m["times"] == ["08:00"]


def test_edit_unknown_field_raises(db):
    med_id = meds.add(db, "Аспирин", ["08:00"])
    db.commit()
    with pytest.raises(ValueError):
        meds.edit(db, med_id, bogus="x")


def test_edit_unknown_med_returns_false(db):
    assert meds.edit(db, 9999, remaining=1) is False


def test_edit_no_fields_raises(db):
    med_id = meds.add(db, "Аспирин", ["08:00"])
    db.commit()
    with pytest.raises(ValueError):
        meds.edit(db, med_id)


def test_remove_deletes_and_cascades_intakes(db):
    med_id = meds.add(db, "Аспирин", ["08:00"])
    db.commit()
    db.execute(
        "INSERT INTO med_intakes(med_id, plan_ts_utc, created_at) VALUES (?,?,?)",
        (med_id, "2026-07-13T08:00:00+00:00", "2026-07-13T08:00:00+00:00"),
    )
    db.commit()
    assert db.execute(
        "SELECT COUNT(*) FROM med_intakes WHERE med_id=?", (med_id,)
    ).fetchone()[0] == 1

    ok = meds.remove(db, med_id)
    db.commit()
    assert ok is True
    assert meds.get(db, med_id) is None
    assert db.execute(
        "SELECT COUNT(*) FROM med_intakes WHERE med_id=?", (med_id,)
    ).fetchone()[0] == 0


def test_remove_unknown_returns_false(db):
    assert meds.remove(db, 9999) is False


def test_schema_version_is_5(db):
    assert db.execute(
        "SELECT value FROM meta WHERE key='schema_version'"
    ).fetchone()["value"] == "5"


def test_shopping_table_exists(db):
    db.execute(
        "INSERT INTO shopping(name, created_at) VALUES ('Соль', '2026-07-13T00:00:00+00:00')"
    )
    row = db.execute("SELECT name, status, source FROM shopping").fetchone()
    assert row["name"] == "Соль"
    assert row["status"] == "open"
    assert row["source"] == "manual"


# --- CLI ---

def test_cli_meds_add_and_list(db, capsys):
    from fam import cli
    rc = cli.main(["meds", "add", "Магний", "--times", "08:00,20:00",
                   "--dose", "1 таб", "--remaining", "30", "--threshold", "5",
                   "--json"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Магний" in out

    rc = cli.main(["meds", "list", "--json"])
    assert rc == 0
    out = capsys.readouterr().out
    data = json.loads(out)
    assert len(data) == 1
    assert data[0]["times"] == ["08:00", "20:00"]


def test_cli_meds_add_invalid_time_exits_2(db, capsys):
    from fam import cli
    rc = cli.main(["meds", "add", "Плохое", "--times", "8am"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "time" in err.lower()
    assert db.execute("SELECT COUNT(*) FROM meds").fetchone()[0] == 0


def test_cli_meds_add_empty_times_exits_2(db, capsys):
    from fam import cli
    rc = cli.main(["meds", "add", "Плохое", "--times", ""])
    assert rc == 2
    assert db.execute("SELECT COUNT(*) FROM meds").fetchone()[0] == 0


def test_cli_meds_edit_and_rm(db, capsys):
    from fam import cli
    rc = cli.main(["meds", "add", "Аспирин", "--times", "08:00", "--json"])
    assert rc == 0
    med_id = json.loads(capsys.readouterr().out)["id"]

    rc = cli.main(["meds", "edit", str(med_id), "--remaining", "3", "--json"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["remaining"] == 3

    rc = cli.main(["meds", "rm", str(med_id), "--json"])
    assert rc == 0

    rc = cli.main(["meds", "edit", str(med_id), "--remaining", "1"])
    assert rc == 2


def test_cli_meds_rm_unknown_exits_2(db, capsys):
    from fam import cli
    rc = cli.main(["meds", "rm", "999999"])
    assert rc == 2


def test_cli_meds_list_all_includes_disabled(db, capsys):
    from fam import cli
    cli.main(["meds", "add", "Аспирин", "--times", "08:00", "--json"])
    med_id = json.loads(capsys.readouterr().out)["id"]
    cli.main(["meds", "edit", str(med_id), "--enabled", "0", "--json"])
    capsys.readouterr()

    rc = cli.main(["meds", "list", "--json"])
    assert rc == 0
    assert json.loads(capsys.readouterr().out) == []

    rc = cli.main(["meds", "list", "--all", "--json"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert len(data) == 1


def test_cli_meds_add_audits(db, capsys):
    from fam import cli
    cli.main(["meds", "add", "Аспирин", "--times", "08:00", "--json"])
    capsys.readouterr()
    assert db.execute(
        "SELECT COUNT(*) FROM audit_log WHERE kind='meds.add'"
    ).fetchone()[0] == 1
