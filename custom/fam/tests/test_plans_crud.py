import pytest
from fam import cal, people, places, plans


def _seed(db):
    people.add(db, "Тая", slug="taya")
    places.add(db, "Клиника Дента", aliases=["стоматолог"])
    db.commit()


def test_add_and_list_open_roundtrips(db):
    _seed(db)
    pid = plans.add(db, "Купить корм коту", place="стоматолог", person="Тая",
                     deadline="2026-08-01", notes="важно")
    db.commit()
    assert isinstance(pid, int)

    open_plans = plans.list_open(db)
    assert len(open_plans) == 1
    p = open_plans[0]
    assert p["title"] == "Купить корм коту"
    assert p["status"] == "open"
    assert p["deadline"] == "2026-08-01"
    assert p["notes"] == "важно"
    assert p["place"]["name"] == "Клиника Дента"
    assert p["place"]["lat"] is None and "lon" in p["place"]
    assert p["person"]["name"] == "Тая"
    assert p["done_at"] is None


def test_add_without_place_or_person(db):
    _seed(db)
    pid = plans.add(db, "Просто дело")
    db.commit()
    p = plans.list_open(db)[0]
    assert p["id"] == pid
    assert p["place"] is None
    assert p["person"] is None


def test_mark_done_hides_from_list_open_and_sets_done_at(db):
    _seed(db)
    pid = plans.add(db, "Сделать что-то")
    db.commit()
    ok = plans.mark(db, pid, "done")
    db.commit()
    assert ok is True

    assert plans.list_open(db) == []

    row = db.execute("SELECT status, done_at FROM plans WHERE id=?", (pid,)).fetchone()
    assert row["status"] == "done"
    assert row["done_at"] is not None


def test_mark_dropped_hides_from_list_open_no_done_at(db):
    _seed(db)
    pid = plans.add(db, "Отменённое дело")
    db.commit()
    plans.mark(db, pid, "dropped")
    db.commit()

    assert plans.list_open(db) == []
    row = db.execute("SELECT status, done_at FROM plans WHERE id=?", (pid,)).fetchone()
    assert row["status"] == "dropped"
    assert row["done_at"] is None


def test_mark_unknown_plan_returns_false(db):
    _seed(db)
    assert plans.mark(db, 9999, "done") is False


def test_attach_sets_event_id(db):
    _seed(db)
    pid = plans.add(db, "Записаться к врачу")
    db.commit()
    e = cal.add(db, "Стоматолог", "2026-07-15T05:00:00+00:00")
    db.commit()

    ok = plans.attach(db, pid, e["id"])
    db.commit()
    assert ok is True

    row = db.execute("SELECT attached_event_id FROM plans WHERE id=?", (pid,)).fetchone()
    assert row["attached_event_id"] == e["id"]


def test_attach_unknown_plan_returns_false(db):
    _seed(db)
    e = cal.add(db, "Событие", "2026-07-15T05:00:00+00:00")
    db.commit()
    assert plans.attach(db, 9999, e["id"]) is False


def test_attach_unknown_event_returns_false(db):
    _seed(db)
    pid = plans.add(db, "Записаться к врачу")
    db.commit()

    ok = plans.attach(db, pid, 9999)
    db.commit()
    assert ok is False

    row = db.execute("SELECT attached_event_id FROM plans WHERE id=?", (pid,)).fetchone()
    assert row["attached_event_id"] is None
    assert db.execute(
        "SELECT COUNT(*) FROM audit_log WHERE kind=?"
        " AND json_extract(payload, '$.event_id')=9999",
        ("plan.attach",),
    ).fetchone()[0] == 0


def test_add_unknown_place_raises(db):
    _seed(db)
    with pytest.raises(ValueError):
        plans.add(db, "Что-то", place="Несуществующее место")
    assert db.execute("SELECT COUNT(*) FROM plans").fetchone()[0] == 0


def test_add_unknown_person_raises(db):
    _seed(db)
    with pytest.raises(ValueError):
        plans.add(db, "Что-то", person="Незнакомец")
    assert db.execute("SELECT COUNT(*) FROM plans").fetchone()[0] == 0


def test_add_invalid_deadline_raises(db):
    # Final review Finding 1: a malformed deadline must be rejected at
    # add() time, not silently stored and crash the digest later
    # (tick._burning_plans does date.fromisoformat with no guard).
    _seed(db)
    with pytest.raises(ValueError):
        plans.add(db, "Что-то", deadline="не дата")
    assert db.execute("SELECT COUNT(*) FROM plans").fetchone()[0] == 0


def test_add_invalid_deadline_message_mentions_deadline(db):
    _seed(db)
    with pytest.raises(ValueError, match="deadline"):
        plans.add(db, "Что-то", deadline="2026-13-40")


# --- CLI ---

def test_cli_plan_add_and_list(db, capsys, monkeypatch):
    import os
    from fam import cli

    _seed(db)
    db.commit()

    monkeypatch.setenv("FAM_DB", os.environ["FAM_DB"])
    rc = cli.main(["plan", "add", "Купить корм", "--place", "стоматолог",
                   "--person", "Тая", "--deadline", "2026-08-01", "--json"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Купить корм" in out

    rc = cli.main(["plan", "list", "--json"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Купить корм" in out


def test_cli_plan_add_unknown_place_exits_2(db, capsys):
    from fam import cli
    rc = cli.main(["plan", "add", "Дело", "--place", "Нигде"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "Нигде" in err or "place" in err.lower()


def test_cli_plan_done_and_drop(db, capsys):
    from fam import cli
    _seed(db)
    db.commit()
    cli.main(["plan", "add", "Дело1", "--json"])
    capsys.readouterr()
    pid = plans.add(db, "Дело2")
    db.commit()

    rc = cli.main(["plan", "done", str(pid), "--json"])
    assert rc == 0
    rc = cli.main(["plan", "drop", "999999"])
    assert rc == 2


def test_cli_plan_attach(db, capsys):
    from fam import cli
    _seed(db)
    pid = plans.add(db, "Дело3")
    e = cal.add(db, "Событие3", "2026-07-15T05:00:00+00:00")
    db.commit()

    rc = cli.main(["plan", "attach", str(pid), "--event", str(e["id"]), "--json"])
    assert rc == 0
    row = db.execute("SELECT attached_event_id FROM plans WHERE id=?", (pid,)).fetchone()
    assert row["attached_event_id"] == e["id"]


def test_cli_plan_add_invalid_deadline_exits_2(db, capsys):
    from fam import cli
    _seed(db)
    db.commit()
    rc = cli.main(["plan", "add", "Дело", "--deadline", "не дата"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "deadline" in err.lower()
    assert db.execute("SELECT COUNT(*) FROM plans").fetchone()[0] == 0


def test_cli_plan_attach_recomputes_road(db, capsys, monkeypatch):
    # Final review Finding 3: attach should trigger the same per-event
    # road recompute mechanism cal.add/cal.update/`fam road` already use.
    from fam import cal as cal_mod, cli

    _seed(db)
    pid = plans.add(db, "Дело4")
    e = cal.add(db, "Событие4", "2026-07-15T05:00:00+00:00")
    db.commit()

    calls = []
    monkeypatch.setattr(
        cal_mod, "recompute_road",
        lambda conn, event_id: calls.append(event_id) or
        {"minutes": None, "reason": "no_place_coords"},
    )

    rc = cli.main(["plan", "attach", str(pid), "--event", str(e["id"]), "--json"])
    assert rc == 0
    assert calls == [e["id"]]


def test_cli_plan_attach_recompute_failure_does_not_break_attach(db, capsys, monkeypatch):
    from fam import cal as cal_mod, cli

    _seed(db)
    pid = plans.add(db, "Дело5")
    e = cal.add(db, "Событие5", "2026-07-15T05:00:00+00:00")
    db.commit()

    def boom(conn, event_id):
        raise RuntimeError("road down")

    monkeypatch.setattr(cal_mod, "recompute_road", boom)

    rc = cli.main(["plan", "attach", str(pid), "--event", str(e["id"]), "--json"])
    assert rc == 0
    row = db.execute("SELECT attached_event_id FROM plans WHERE id=?", (pid,)).fetchone()
    assert row["attached_event_id"] == e["id"]
    assert db.execute(
        "SELECT COUNT(*) FROM audit_log WHERE kind='tick.error' "
        "AND json_extract(payload, '$.where')='plan_attach_recompute'"
    ).fetchone()[0] == 1
