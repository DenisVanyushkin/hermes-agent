import json

import pytest

from fam import cli, goals


# --- plan_state_get/set -----------------------------------------------

def test_plan_state_roundtrip(db):
    assert goals.plan_state_get(db, "2026-08") is None
    goals.plan_state_set(db, "2026-08", "offered", today="2026-07-29")
    db.commit()
    assert goals.plan_state_get(db, "2026-08") == ("offered", "2026-07-29")


def test_plan_state_set_rejects_bad_status(db):
    with pytest.raises(ValueError):
        goals.plan_state_set(db, "2026-08", "bogus", today="2026-07-29")


def test_plan_state_set_rejects_non_month(db):
    with pytest.raises(ValueError):
        goals.plan_state_set(db, "2026-Q3", "offered", today="2026-07-29")


# --- compute_target_month ----------------------------------------------

def test_target_inside_window_is_next_month(db):
    # July has 31 days; window=3 -> 29,30,31 are "inside".
    assert goals.compute_target_month(db, "2026-07-29", 3) == "2026-08"
    assert goals.compute_target_month(db, "2026-07-31", 3) == "2026-08"


def test_target_outside_window_is_current_month(db):
    assert goals.compute_target_month(db, "2026-07-28", 3) == "2026-07"
    assert goals.compute_target_month(db, "2026-07-01", 3) == "2026-07"


def test_target_stays_same_after_rollover_while_offered(db):
    # An offered-but-unanswered cycle for August, asked on July 30,
    # carries over the 1st: on Aug 3 (outside August's own window),
    # compute_target_month naturally still resolves to August because
    # "current month" has itself advanced.
    goals.plan_state_set(db, "2026-08", "offered", today="2026-07-30")
    db.commit()
    assert goals.compute_target_month(db, "2026-08-03", 3) == "2026-08"


# --- plan_info -----------------------------------------------------------

def test_plan_info_basic_shape_no_state(db):
    info = goals.plan_info(db, "2026-07-28", 3)
    assert info["target_month"] == "2026-07"
    assert info["state"] is None
    assert info["quarter"] == "2026-Q3"  # July is first month of Q3
    assert info["quarter_goals_open"] == []
    assert info["tails_open"] == []
    assert info["tails_declined"] == []


def test_plan_info_quarter_null_outside_first_month(db):
    info = goals.plan_info(db, "2026-08-05", 3)
    assert info["target_month"] == "2026-08"
    assert info["quarter"] is None
    assert info["quarter_goals_open"] == []


def test_plan_info_quarter_goals_open(db):
    goals.add(db, "Квартальная", period="2026-Q3")
    db.commit()
    info = goals.plan_info(db, "2026-07-01", 3)
    assert info["quarter"] == "2026-Q3"
    assert len(info["quarter_goals_open"]) == 1
    assert info["quarter_goals_open"][0]["title"] == "Квартальная"


def test_plan_info_reports_state(db):
    goals.plan_state_set(db, "2026-08", "offered", today="2026-07-29")
    db.commit()
    info = goals.plan_info(db, "2026-07-29", 3)
    assert info["target_month"] == "2026-08"
    assert info["state"] == "offered"


def test_plan_info_tails(db):
    # target = 2026-08 -> tail month = 2026-07
    open_id = goals.add(db, "Открытая", period="2026-07")
    declined_id = goals.add(db, "Отклонённая", period="2026-07")
    goals.mark(db, declined_id, "declined")
    done_id = goals.add(db, "Сделанная", period="2026-07")
    goals.mark(db, done_id, "done")
    db.commit()

    info = goals.plan_info(db, "2026-07-29", 3)
    open_titles = {g["id"] for g in info["tails_open"]}
    declined_titles = {g["id"] for g in info["tails_declined"]}
    assert open_titles == {open_id}
    assert declined_titles == {declined_id}


# --- CLI: plan-info ------------------------------------------------------

def test_cli_plan_info_json(db, capsys, monkeypatch):
    monkeypatch.setattr(goals, "today_almaty", lambda now_utc=None: "2026-07-29")
    rc = cli.main(["goal", "plan-info", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["target_month"] == "2026-08"
    assert out["state"] is None


# --- CLI: plan-mark --------------------------------------------------------

def test_cli_plan_mark_default_month(db, capsys, monkeypatch):
    monkeypatch.setattr(goals, "today_almaty", lambda now_utc=None: "2026-07-29")
    rc = cli.main(["goal", "plan-mark", "done"])
    assert rc == 0
    assert goals.plan_state_get(db, "2026-08") == ("done", "2026-07-29")


def test_cli_plan_mark_explicit_month(db, capsys, monkeypatch):
    monkeypatch.setattr(goals, "today_almaty", lambda now_utc=None: "2026-07-29")
    rc = cli.main(["goal", "plan-mark", "declined", "--month", "2026-09"])
    assert rc == 0
    assert goals.plan_state_get(db, "2026-09") == ("declined", "2026-07-29")


def test_cli_plan_mark_bad_status_exit_2(db, capsys, monkeypatch):
    # "status" is an argparse `choices` positional (done|declined), so an
    # unknown value is rejected by argparse itself -- SystemExit(2), not
    # a returned rc (same as any other bad-choice argparse error in this
    # CLI; there's no domain-level ValueError path to reach here).
    monkeypatch.setattr(goals, "today_almaty", lambda now_utc=None: "2026-07-29")
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["goal", "plan-mark", "bogus"])
    captured = capsys.readouterr()
    assert exc_info.value.code == 2
    assert captured.err.strip() != ""


# --- CLI: plan-status ------------------------------------------------------

def test_cli_plan_status_human_readable(db, capsys, monkeypatch):
    monkeypatch.setattr(goals, "today_almaty", lambda now_utc=None: "2026-07-29")
    rc = cli.main(["goal", "plan-status"])
    captured = capsys.readouterr()
    assert rc == 0
    assert "2026-08" in captured.out
