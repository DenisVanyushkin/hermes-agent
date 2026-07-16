import json
import pytest
from fam import shopping


def test_add_and_list_open_roundtrips(db):
    item_id = shopping.add(db, "Молоко", qty="2 л", added_by="Денис")
    db.commit()
    assert isinstance(item_id, int)

    rows = shopping.list_open(db)
    assert len(rows) == 1
    it = rows[0]
    assert it["id"] == item_id
    assert it["name"] == "Молоко"
    assert it["qty"] == "2 л"
    assert it["added_by"] == "Денис"
    assert it["source"] == "manual"
    assert it["status"] == "open"
    assert it["done_at"] is None


def test_add_defaults(db):
    item_id = shopping.add(db, "Хлеб")
    db.commit()
    it = shopping.list_open(db)[0]
    assert it["id"] == item_id
    assert it["qty"] == ""
    assert it["added_by"] == ""
    assert it["source"] == "manual"


def test_add_audits(db):
    shopping.add(db, "Соль", qty="1 пачка", added_by="Тая")
    db.commit()
    assert db.execute(
        "SELECT COUNT(*) FROM audit_log WHERE kind='shop.add'"
    ).fetchone()[0] == 1


def test_mark_done_hides_from_list_open_and_sets_done_at(db):
    item_id = shopping.add(db, "Сахар")
    db.commit()
    ok = shopping.mark_done(db, item_id)
    db.commit()
    assert ok is True

    assert shopping.list_open(db) == []

    row = db.execute(
        "SELECT status, done_at FROM shopping WHERE id=?", (item_id,)
    ).fetchone()
    assert row["status"] == "done"
    assert row["done_at"] is not None


def test_mark_done_audits(db):
    item_id = shopping.add(db, "Чай")
    db.commit()
    shopping.mark_done(db, item_id)
    db.commit()
    assert db.execute(
        "SELECT COUNT(*) FROM audit_log WHERE kind='shop.done'"
    ).fetchone()[0] == 1


def test_mark_done_unknown_returns_false(db):
    assert shopping.mark_done(db, 9999) is False
    assert db.execute(
        "SELECT COUNT(*) FROM audit_log WHERE kind='shop.done'"
    ).fetchone()[0] == 0


def test_add_from_meds_dedup_same_name_twice(db):
    first = shopping.add_from_meds(db, "Магний", qty="1 упаковка")
    db.commit()
    assert isinstance(first, int)

    second = shopping.add_from_meds(db, "Магний", qty="1 упаковка")
    db.commit()
    assert second is None

    rows = shopping.list_open(db)
    assert len(rows) == 1
    assert rows[0]["id"] == first
    assert rows[0]["source"] == "meds"


def test_add_from_meds_dedup_is_casefold_insensitive(db):
    first = shopping.add_from_meds(db, "магний")
    db.commit()
    second = shopping.add_from_meds(db, "МАГНИЙ")
    db.commit()
    assert isinstance(first, int)
    assert second is None
    assert len(shopping.list_open(db)) == 1


def test_add_from_meds_sets_source_and_audits(db):
    item_id = shopping.add_from_meds(db, "Витамин D", qty="1 флакон")
    db.commit()
    it = shopping.list_open(db)[0]
    assert it["id"] == item_id
    assert it["source"] == "meds"
    row = db.execute(
        "SELECT payload FROM audit_log WHERE kind='shop.add' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    payload = json.loads(row["payload"])
    assert payload["source"] == "meds"


def test_add_from_meds_does_not_dedup_manual_source(db):
    # a manual entry with the same name should not block add_from_meds --
    # dedup only applies among source='meds' open rows.
    shopping.add(db, "Аспирин", source="manual")
    db.commit()
    second = shopping.add_from_meds(db, "Аспирин")
    db.commit()
    assert isinstance(second, int)
    assert len(shopping.list_open(db)) == 2


def test_add_from_meds_allows_readd_after_done(db):
    first = shopping.add_from_meds(db, "Омега-3")
    db.commit()
    shopping.mark_done(db, first)
    db.commit()

    second = shopping.add_from_meds(db, "Омега-3")
    db.commit()
    assert isinstance(second, int)
    assert second != first
    assert len(shopping.list_open(db)) == 1


# --- CLI ---

def test_cli_shop_add_and_list(db, capsys):
    from fam import cli

    rc = cli.main(["shop", "add", "Молоко", "--qty", "2 л", "--by", "Денис", "--json"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Молоко" in out

    rc = cli.main(["shop", "list", "--json"])
    assert rc == 0
    out = capsys.readouterr().out
    data = json.loads(out)
    assert len(data) == 1
    assert data[0]["name"] == "Молоко"
    assert data[0]["qty"] == "2 л"


def test_cli_shop_add_audits(db, capsys):
    from fam import cli
    cli.main(["shop", "add", "Хлеб", "--json"])
    capsys.readouterr()
    assert db.execute(
        "SELECT COUNT(*) FROM audit_log WHERE kind='shop.add'"
    ).fetchone()[0] == 1


def test_cli_shop_done(db, capsys):
    from fam import cli
    cli.main(["shop", "add", "Сыр", "--json"])
    item_id = json.loads(capsys.readouterr().out)["id"]

    rc = cli.main(["shop", "done", str(item_id), "--json"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["status"] == "done"

    rc = cli.main(["shop", "list", "--json"])
    assert rc == 0
    assert json.loads(capsys.readouterr().out) == []


def test_cli_shop_done_unknown_exits_2(db, capsys):
    from fam import cli
    rc = cli.main(["shop", "done", "999999"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "shopping" in err.lower() or "999999" in err


def test_cli_shop_list_text_output(db, capsys):
    from fam import cli
    cli.main(["shop", "add", "Кофе", "--qty", "1 пачка", "--json"])
    capsys.readouterr()

    rc = cli.main(["shop", "list"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Кофе" in out
    assert "1 пачка" in out
