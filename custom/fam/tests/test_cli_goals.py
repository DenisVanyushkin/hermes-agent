import json

import pytest

from fam import cli, goals


def test_goal_add_json_and_text(db, capsys):
    rc = cli.main(["goal", "add", "Похудеть", "--period", "2026-08",
                   "--notes", "план", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["title"] == "Похудеть"
    assert out["period"] == "2026-08"
    assert out["notes"] == "план"
    assert out["status"] == "open"

    rc = cli.main(["goal", "add", "Читать книги"])
    captured = capsys.readouterr()
    assert rc == 0
    assert "Читать книги" in captured.out


def test_goal_add_default_period_is_current_month(db, capsys):
    rc = cli.main(["goal", "add", "Без периода", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["period"] == goals.current_month()


def test_goal_add_bad_period_exit_2(db, capsys):
    rc = cli.main(["goal", "add", "X", "--period", "not-a-period"])
    captured = capsys.readouterr()
    assert rc == 2
    assert captured.err.strip() != ""


def test_goal_add_with_parent(db, capsys):
    rc = cli.main(["goal", "add", "Квартальная", "--period", "2026-Q3", "--json"])
    parent = json.loads(capsys.readouterr().out)

    rc = cli.main(["goal", "add", "Месячная", "--period", "2026-08",
                   "--parent", str(parent["id"]), "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["parent_goal_id"] == parent["id"]


def test_goal_add_bad_parent_exit_2(db, capsys):
    rc = cli.main(["goal", "add", "X", "--parent", "999"])
    captured = capsys.readouterr()
    assert rc == 2
    assert captured.err.strip() != ""


def test_goal_list_json_before_and_after_subcommand(db, capsys):
    goals.add(db, "Цель месяца", period="2026-08")
    db.commit()

    rc = cli.main(["--json", "goal", "list", "--period", "2026-08"])
    out1 = json.loads(capsys.readouterr().out)
    rc2 = cli.main(["goal", "list", "--period", "2026-08", "--json"])
    out2 = json.loads(capsys.readouterr().out)

    assert rc == 0 and rc2 == 0
    assert isinstance(out1, list) and any(g["title"] == "Цель месяца" for g in out1)
    assert isinstance(out2, list) and any(g["title"] == "Цель месяца" for g in out2)


def test_goal_list_all_includes_closed(db, capsys):
    gid = goals.add(db, "Закрытая", period="2026-08")
    db.commit()
    goals.mark(db, gid, "done")
    db.commit()

    rc = cli.main(["goal", "list", "--period", "2026-08", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert out == []

    rc = cli.main(["goal", "list", "--period", "2026-08", "--all", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert any(g["title"] == "Закрытая" for g in out)


def test_goal_show_json_and_text(db, capsys):
    gid = goals.add(db, "Показать", period="2026-08")
    db.commit()

    rc = cli.main(["goal", "show", str(gid), "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["id"] == gid
    assert out["title"] == "Показать"

    rc = cli.main(["goal", "show", str(gid)])
    captured = capsys.readouterr()
    assert rc == 0
    assert "Показать" in captured.out


def test_goal_show_unknown_id_exit_2(db, capsys):
    rc = cli.main(["goal", "show", "999"])
    captured = capsys.readouterr()
    assert rc == 2
    assert captured.err.strip() != ""


def test_goal_done_json_and_text(db, capsys):
    gid = goals.add(db, "Сделать", period="2026-08")
    db.commit()

    rc = cli.main(["goal", "done", str(gid), "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["status"] == "done"
    assert out["closed_at"] is not None
    assert out.get("parent_hint") is None

    gid2 = goals.add(db, "Сделать2", period="2026-08")
    db.commit()
    rc = cli.main(["goal", "done", str(gid2)])
    captured = capsys.readouterr()
    assert rc == 0
    assert "Сделать2" in captured.out


def test_goal_done_unknown_id_exit_2(db, capsys):
    rc = cli.main(["goal", "done", "999"])
    captured = capsys.readouterr()
    assert rc == 2
    assert captured.err.strip() != ""


def test_goal_done_prints_parent_hint_when_parent_open_quarter(db, capsys):
    parent_id = goals.add(db, "Квартальная", period="2026-Q3")
    child_id = goals.add(db, "Месячная", period="2026-08", parent=parent_id)
    db.commit()

    rc = cli.main(["goal", "done", str(child_id)])
    captured = capsys.readouterr()
    assert rc == 0
    assert f"parent: #{parent_id}" in captured.out
    assert "Квартальная" in captured.out
    assert "(quarter, open)" in captured.out

    rc = cli.main(["goal", "done", str(child_id), "--json"])
    # already done -> ValueError (invalid transition) -> exit 2
    captured = capsys.readouterr()
    assert rc == 2


def test_goal_done_json_includes_parent_hint(db, capsys):
    parent_id = goals.add(db, "Квартальная", period="2026-Q3")
    child_id = goals.add(db, "Месячная", period="2026-08", parent=parent_id)
    db.commit()

    rc = cli.main(["goal", "done", str(child_id), "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["parent_hint"] is not None
    assert f"#{parent_id}" in out["parent_hint"]


def test_goal_done_no_hint_when_parent_already_closed(db, capsys):
    parent_id = goals.add(db, "Квартальная", period="2026-Q3")
    goals.mark(db, parent_id, "done")
    child_id = goals.add(db, "Месячная", period="2026-08", parent=parent_id)
    db.commit()

    rc = cli.main(["goal", "done", str(child_id), "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out.get("parent_hint") is None


def test_goal_decline_json_and_text(db, capsys):
    gid = goals.add(db, "Отказаться", period="2026-08")
    db.commit()

    rc = cli.main(["goal", "decline", str(gid), "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["status"] == "declined"

    gid2 = goals.add(db, "Отказаться2", period="2026-08")
    db.commit()
    rc = cli.main(["goal", "decline", str(gid2)])
    captured = capsys.readouterr()
    assert rc == 0
    assert "Отказаться2" in captured.out


def test_goal_decline_unknown_id_exit_2(db, capsys):
    rc = cli.main(["goal", "decline", "999"])
    captured = capsys.readouterr()
    assert rc == 2


def test_goal_reopen_json_and_text(db, capsys):
    gid = goals.add(db, "Переоткрыть", period="2026-08")
    db.commit()
    goals.mark(db, gid, "done")
    db.commit()

    rc = cli.main(["goal", "reopen", str(gid), "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["status"] == "open"
    assert out["closed_at"] is None

    gid2 = goals.add(db, "Переоткрыть2", period="2026-08")
    goals.mark(db, gid2, "declined")
    db.commit()
    rc = cli.main(["goal", "reopen", str(gid2)])
    captured = capsys.readouterr()
    assert rc == 0
    assert "Переоткрыть2" in captured.out


def test_goal_reopen_unknown_id_exit_2(db, capsys):
    rc = cli.main(["goal", "reopen", "999"])
    captured = capsys.readouterr()
    assert rc == 2


def test_goal_take_json_and_text(db, capsys):
    gid = goals.add(db, "Перенести", period="2026-07")
    goals.mark(db, gid, "declined")
    db.commit()

    rc = cli.main(["goal", "take", str(gid), "--period", "2026-08", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["period"] == "2026-08"
    assert out["status"] == "open"

    gid2 = goals.add(db, "Перенести2", period="2026-07")
    db.commit()
    rc = cli.main(["goal", "take", str(gid2), "--period", "2026-09"])
    captured = capsys.readouterr()
    assert rc == 0
    assert "Перенести2" in captured.out


def test_goal_take_unknown_id_exit_2(db, capsys):
    rc = cli.main(["goal", "take", "999", "--period", "2026-08"])
    captured = capsys.readouterr()
    assert rc == 2


def test_goal_take_non_month_target_exit_2(db, capsys):
    gid = goals.add(db, "X", period="2026-07")
    db.commit()
    rc = cli.main(["goal", "take", str(gid), "--period", "2026-Q3"])
    captured = capsys.readouterr()
    assert rc == 2


def test_goal_take_quarter_goal_exit_2(db, capsys):
    gid = goals.add(db, "Квартальная", period="2026-Q3")
    db.commit()
    rc = cli.main(["goal", "take", str(gid), "--period", "2026-08"])
    captured = capsys.readouterr()
    assert rc == 2


def test_goal_cmd_required(db, capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["goal"])
    assert exc.value.code != 0
