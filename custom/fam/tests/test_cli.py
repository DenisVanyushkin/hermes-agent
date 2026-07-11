import json
from fam import cli, people

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
