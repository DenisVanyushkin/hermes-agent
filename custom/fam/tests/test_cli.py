import json
from fam import cli

def test_json_flag_works_before_and_after_subcommand(db, capsys, monkeypatch):
    # db fixture sets FAM_DB to tmp DB; init writes to it
    assert cli.main(["--json", "init"]) == 0
    out1 = json.loads(capsys.readouterr().out)
    assert cli.main(["init", "--json"]) == 0
    out2 = json.loads(capsys.readouterr().out)
    assert out1["ok"] and out2["ok"]
