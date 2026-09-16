import json

from fam import cal, cli, grid, people, series


def _seed(db):
    people.add(db, "Амина", slug="amina")
    taya = people.add(db, "Тая", slug="taya")
    people.add(db, "татешки", kind="group")
    db.commit()
    return taya


def test_cli_add_writes_subject(db, capsys):
    _seed(db)
    assert cli.main(["cal", "add", "--title", "Математика", "--start",
                     "2030-01-08T10:00:00+00:00", "--for-person", "Тая", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["subject"]["slug"] == "taya"


def test_cli_recurring_add_writes_series_and_occurrence_subject(db, capsys):
    _seed(db)
    assert cli.main(["cal", "add", "--title", "Тренировка", "--repeat",
                     "weekly", "--days", "tue", "--start-time", "10:00",
                     "--until", "2030-02-01", "--for-person", "Тая", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    sid = payload["series"]["id"]
    assert payload["series"]["subject"]["slug"] == "taya"
    assert db.execute(
        "SELECT COUNT(*) FROM events WHERE series_id=? AND subject_person_id IS NOT NULL",
        (sid,),
    ).fetchone()[0] > 0


def test_cli_update_clears_event_subject(db, capsys):
    taya = _seed(db)
    event = cal.add(db, "Математика", "2030-01-08T10:00:00+00:00",
                    subject_person_id=taya["id"])
    db.commit()
    assert cli.main(["cal", "update", str(event["id"]),
                     "--clear-for-person", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["subject"] is None
    assert db.execute(
        "SELECT subject_person_id FROM events WHERE id=?", (event["id"],)
    ).fetchone()[0] is None


def test_cli_series_update_writes_series_subject(db, capsys):
    taya = _seed(db)
    row = series.add(db, "Тренировка", "tue", "10:00", until_local="2030-02-01")
    series.generate(db, now_utc="2030-01-01T00:00:00+00:00")
    db.commit()
    assert cli.main(["cal", "series", "update", str(row["id"]),
                     "--for-person", "Тая", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["subject"]["slug"] == "taya"
    assert db.execute(
        "SELECT subject_person_id FROM event_series WHERE id=?", (row["id"],)
    ).fetchone()[0] == taya["id"]


def test_cli_grid_subject_filter_uses_person_id(db, capsys, monkeypatch, tmp_path):
    taya = _seed(db)
    cal.add(db, "Тая", "2030-01-08T10:00:00+00:00", subject_person_id=taya["id"])
    db.commit()
    seen = {}
    monkeypatch.setattr(
        grid, "render_day",
        lambda conn, day, out, subject=None: seen.update(subject=subject)
        or str(tmp_path / "day.png"),
    )
    assert cli.main(["cal", "grid", "--day", "2030-01-08",
                     "--for-person", "Тая", "-o", str(tmp_path / "day.png"),
                     "--json"]) == 0
    capsys.readouterr()
    assert seen["subject"] == taya["id"]
