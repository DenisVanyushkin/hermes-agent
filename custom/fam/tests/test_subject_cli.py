import json

from fam import cal, cli, grid, people, series


def _seed(db):
    people.add(db, "Амина", slug="amina")
    people.add(db, "Тая", slug="taya")
    people.add(db, "татешки", kind="group")
    db.commit()


def test_cli_all_subject_writer_paths_and_views(db, capsys, monkeypatch, tmp_path):
    _seed(db)
    rc = cli.main(["cal", "add", "--title", "Математика", "--start", "2030-01-08T10:00:00+00:00", "--for-person", "Тая", "--json"])
    assert rc == 0
    one = json.loads(capsys.readouterr().out)
    assert one["subject"]["slug"] == "taya"

    rc = cli.main(["cal", "add", "--title", "Тренировка", "--repeat", "weekly", "--days", "tue", "--start-time", "10:00", "--until", "2030-02-01", "--for-person", "Тая", "--json"])
    assert rc == 0
    recurring = json.loads(capsys.readouterr().out)
    sid = recurring["series"]["id"]
    assert recurring["series"]["subject"]["slug"] == "taya"
    assert db.execute("SELECT COUNT(*) FROM events WHERE series_id=? AND subject_person_id IS NOT NULL", (sid,)).fetchone()[0] > 0

    event_id = db.execute("SELECT id FROM events WHERE series_id=? ORDER BY id LIMIT 1", (sid,)).fetchone()[0]
    assert cli.main(["cal", "update", str(event_id), "--clear-for-person", "--json"]) == 0
    updated = json.loads(capsys.readouterr().out)
    assert updated["subject"] is None
    assert cli.main(["cal", "series", "update", str(sid), "--for-person", "Тая", "--json"]) == 0
    series_out = json.loads(capsys.readouterr().out)
    assert series_out["subject"]["slug"] == "taya"

    seen = {}
    monkeypatch.setattr(grid, "render_day", lambda conn, day, out, subject=None: seen.update(subject=subject) or str(tmp_path / "day.png"))
    assert cli.main(["cal", "grid", "--day", "2030-01-08", "--for-person", "Тая", "-o", str(tmp_path / "day.png"), "--json"]) == 0
    capsys.readouterr()
    assert seen["subject"] == people.get(db, "Тая")["id"]
