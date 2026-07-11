import json
import pytest
from fam import cal, cli, people

def test_json_flag_works_before_and_after_subcommand(db, capsys, monkeypatch):
    # db fixture sets FAM_DB to tmp DB; init writes to it
    assert cli.main(["--json", "init"]) == 0
    out1 = json.loads(capsys.readouterr().out)
    assert cli.main(["init", "--json"]) == 0
    out2 = json.loads(capsys.readouterr().out)
    assert out1["ok"] and out2["ok"]

# --- Finding 2 (Important): exercise the new `people list` SUPPRESS call sites ---

def test_people_list_json_before_and_after_subcommand(db, capsys):
    people.add(db, "Тестова")
    db.commit()

    assert cli.main(["--json", "people", "list"]) == 0
    out1 = json.loads(capsys.readouterr().out)
    assert cli.main(["people", "list", "--json"]) == 0
    out2 = json.loads(capsys.readouterr().out)

    assert isinstance(out1, list) and any(p["name"] == "Тестова" for p in out1)
    assert isinstance(out2, list) and any(p["name"] == "Тестова" for p in out2)

# --- Finding 3 (Important): CLI-level ValueError contract check ---

def test_people_add_duplicate_cli_exit_code_2(db, capsys):
    assert cli.main(["people", "add", "X"]) == 0
    capsys.readouterr()  # discard first call's stdout/stderr
    rc = cli.main(["people", "add", "X"])
    captured = capsys.readouterr()
    assert rc == 2
    assert captured.err.strip() != ""

# --- Fix round 1, Finding 3: cal show unknown id -> exit 2 ---

def test_cal_show_unknown_id_exit_2(db, capsys):
    rc = cli.main(["cal", "show", "999"])
    captured = capsys.readouterr()
    assert rc == 2
    assert captured.err.strip() != ""

# --- Task 6: `fam cal grid` wiring ---

def test_cal_grid_month_json_writes_png(db, capsys, tmp_path):
    cal.add(db, "Врач", "2026-07-15T05:00:00+00:00"); db.commit()
    out_path = str(tmp_path / "july.png")
    rc = cli.main(["cal", "grid", "--month", "2026-07", "-o", out_path, "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out == {"ok": True, "path": out_path}
    import os
    assert os.path.getsize(out_path) > 5000

def test_cal_grid_week_writes_png(db, capsys, tmp_path):
    out_path = str(tmp_path / "week.png")
    rc = cli.main(["cal", "grid", "--week", "2026-07-13", "-o", out_path])
    captured = capsys.readouterr()
    assert rc == 0
    assert out_path in captured.out
    import os
    assert os.path.getsize(out_path) > 5000

def test_cal_grid_requires_exactly_one_of_month_or_week(db, tmp_path):
    out_path = str(tmp_path / "x.png")
    with pytest.raises(SystemExit) as exc_neither:
        cli.main(["cal", "grid", "-o", out_path])
    assert exc_neither.value.code == 2

    with pytest.raises(SystemExit) as exc_both:
        cli.main(["cal", "grid", "--month", "2026-07", "--week", "2026-07-13",
                   "-o", out_path])
    assert exc_both.value.code == 2

def test_cal_grid_bad_month_format_exit_2(db, tmp_path):
    out_path = str(tmp_path / "x.png")
    with pytest.raises(SystemExit) as exc:
        cli.main(["cal", "grid", "--month", "2026-7", "-o", out_path])
    assert exc.value.code == 2

def test_cal_grid_bad_week_format_exit_2(db, tmp_path):
    out_path = str(tmp_path / "x.png")
    with pytest.raises(SystemExit) as exc:
        cli.main(["cal", "grid", "--week", "2026-13-99", "-o", out_path])
    assert exc.value.code == 2

# --- day view: `fam cal grid --day` wiring ---

def test_cal_grid_day_writes_png(db, capsys, tmp_path):
    cal.add(db, "Врач", "2026-07-11T05:00:00+00:00"); db.commit()
    out_path = str(tmp_path / "day.png")
    rc = cli.main(["cal", "grid", "--day", "2026-07-11", "-o", out_path])
    captured = capsys.readouterr()
    assert rc == 0
    assert out_path in captured.out
    import os
    assert os.path.getsize(out_path) > 5000

def test_cal_grid_day_json_writes_png(db, capsys, tmp_path):
    out_path = str(tmp_path / "day.png")
    rc = cli.main(["cal", "grid", "--day", "2026-07-11", "-o", out_path, "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out == {"ok": True, "path": out_path}

def test_cal_grid_bad_day_format_exit_2(db, tmp_path):
    out_path = str(tmp_path / "x.png")
    with pytest.raises(SystemExit) as exc:
        cli.main(["cal", "grid", "--day", "2026-13-99", "-o", out_path])
    assert exc.value.code == 2

def test_cal_grid_requires_exactly_one_of_day_week_or_month(db, tmp_path):
    out_path = str(tmp_path / "x.png")

    # None of the three.
    with pytest.raises(SystemExit) as exc_none:
        cli.main(["cal", "grid", "-o", out_path])
    assert exc_none.value.code == 2

    # day + week.
    with pytest.raises(SystemExit) as exc_day_week:
        cli.main(["cal", "grid", "--day", "2026-07-11", "--week", "2026-07-13",
                   "-o", out_path])
    assert exc_day_week.value.code == 2

    # day + month.
    with pytest.raises(SystemExit) as exc_day_month:
        cli.main(["cal", "grid", "--day", "2026-07-11", "--month", "2026-07",
                   "-o", out_path])
    assert exc_day_month.value.code == 2

    # all three.
    with pytest.raises(SystemExit) as exc_all_three:
        cli.main(["cal", "grid", "--day", "2026-07-11", "--week", "2026-07-13",
                   "--month", "2026-07", "-o", out_path])
    assert exc_all_three.value.code == 2

# --- Final-review hardening: audit.query limit guard, CLI-level ---

def test_log_rejects_negative_limit_exit_2(db, capsys):
    rc = cli.main(["log", "--limit", "-1"])
    captured = capsys.readouterr()
    assert rc == 2
    assert captured.err.strip() != ""

def test_log_rejects_zero_limit_exit_2(db, capsys):
    rc = cli.main(["log", "--limit", "0"])
    captured = capsys.readouterr()
    assert rc == 2
    assert captured.err.strip() != ""

# --- Final-review hardening: naive-ISO datetimes rejected, CLI-level ---

def test_cal_add_naive_start_exit_2(db, capsys):
    rc = cli.main(["cal", "add", "--title", "X", "--start", "2026-07-15T10:00:00"])
    captured = capsys.readouterr()
    assert rc == 2
    assert captured.err.strip() != ""
