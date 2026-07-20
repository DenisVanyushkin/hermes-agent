import pytest
from fam import goals


# --- period helpers ---------------------------------------------------

def test_validate_period_month():
    assert goals.validate_period("2026-08") == "month"


def test_validate_period_quarter():
    assert goals.validate_period("2026-Q3") == "quarter"


@pytest.mark.parametrize("bad", [
    "2026-13", "2026-00", "2026-Q0", "2026-Q5", "2026", "26-08",
    "2026-8", "not-a-period", "", None, 20260801,
])
def test_validate_period_rejects_bad_values(bad):
    with pytest.raises(ValueError):
        goals.validate_period(bad)


def test_next_month_regular():
    assert goals.next_month("2026-07") == "2026-08"


def test_next_month_december_wraps_year():
    assert goals.next_month("2026-12") == "2027-01"


def test_prev_month_regular():
    assert goals.prev_month("2026-08") == "2026-07"


def test_prev_month_january_wraps_year():
    assert goals.prev_month("2026-01") == "2025-12"


def test_next_month_rejects_quarter():
    with pytest.raises(ValueError):
        goals.next_month("2026-Q3")


def test_quarter_of():
    assert goals.quarter_of("2026-01") == "2026-Q1"
    assert goals.quarter_of("2026-03") == "2026-Q1"
    assert goals.quarter_of("2026-04") == "2026-Q2"
    assert goals.quarter_of("2026-06") == "2026-Q2"
    assert goals.quarter_of("2026-07") == "2026-Q3"
    assert goals.quarter_of("2026-09") == "2026-Q3"
    assert goals.quarter_of("2026-10") == "2026-Q4"
    assert goals.quarter_of("2026-12") == "2026-Q4"


def test_quarter_of_december_stays_same_year_q4():
    assert goals.quarter_of("2026-12") == "2026-Q4"


def test_is_first_month_of_quarter():
    assert goals.is_first_month_of_quarter("2026-01") is True
    assert goals.is_first_month_of_quarter("2026-04") is True
    assert goals.is_first_month_of_quarter("2026-07") is True
    assert goals.is_first_month_of_quarter("2026-10") is True
    assert goals.is_first_month_of_quarter("2026-02") is False
    assert goals.is_first_month_of_quarter("2026-12") is False


def test_current_month_derives_from_now_utc():
    # 2026-08-01T03:00:00+00:00 is 2026-08-01 08:00 in Asia/Almaty (+05)
    assert goals.current_month("2026-08-01T03:00:00+00:00") == "2026-08"


def test_current_month_almaty_rolls_past_utc_midnight():
    # 2026-07-31T20:00:00Z -> 2026-08-01 01:00 Almaty -> next month already
    assert goals.current_month("2026-07-31T20:00:00+00:00") == "2026-08"


# --- add ----------------------------------------------------------------

def test_add_defaults_period_to_current_month(db):
    gid = goals.add(db, "Пробежать 100км", notes="фитнес")
    db.commit()
    assert isinstance(gid, int)
    g = goals.get(db, gid)
    assert g["period"] == goals.current_month()
    assert g["period_type"] == "month"
    assert g["status"] == "open"
    assert g["notes"] == "фитнес"
    assert g["parent_goal_id"] is None
    assert g["closed_at"] is None
    assert g["created_at"]


def test_add_explicit_quarter_period(db):
    gid = goals.add(db, "Квартальная цель", period="2026-Q3")
    db.commit()
    g = goals.get(db, gid)
    assert g["period_type"] == "quarter"
    assert g["period"] == "2026-Q3"


def test_add_invalid_period_raises_before_insert(db):
    with pytest.raises(ValueError):
        goals.add(db, "Плохая цель", period="not-a-period")
    assert db.execute("SELECT COUNT(*) c FROM goals").fetchone()["c"] == 0


def test_add_month_goal_with_quarter_parent(db):
    qid = goals.add(db, "Квартальная", period="2026-Q3")
    db.commit()
    mid = goals.add(db, "Месячная", period="2026-08", parent=qid)
    db.commit()
    g = goals.get(db, mid)
    assert g["parent_goal_id"] == qid


def test_add_quarter_goal_with_parent_raises(db):
    qid = goals.add(db, "Квартальная 1", period="2026-Q3")
    db.commit()
    with pytest.raises(ValueError):
        goals.add(db, "Квартальная 2", period="2026-Q4", parent=qid)


def test_add_parent_must_be_quarter_goal(db):
    m1 = goals.add(db, "Месячная 1", period="2026-07")
    db.commit()
    with pytest.raises(ValueError):
        goals.add(db, "Месячная 2", period="2026-08", parent=m1)


def test_add_parent_unknown_id_raises(db):
    with pytest.raises(ValueError):
        goals.add(db, "Месячная", period="2026-08", parent=9999)


# --- list_goals -----------------------------------------------------------

def test_list_goals_default_current_month_and_quarter(db):
    current = goals.current_month()
    quarter = goals.quarter_of(current)
    other_month = goals.next_month(goals.next_month(current))

    g1 = goals.add(db, "Текущий месяц")
    g2 = goals.add(db, "Текущий квартал", period=quarter)
    g3 = goals.add(db, "Другой месяц", period=other_month)
    db.commit()

    result = {g["id"] for g in goals.list_goals(db)}
    assert result == {g1, g2}
    assert g3 not in result


def test_list_goals_excludes_closed_by_default(db):
    gid = goals.add(db, "Цель")
    db.commit()
    goals.mark(db, gid, "done")
    db.commit()
    assert goals.list_goals(db) == []


def test_list_goals_include_closed(db):
    gid = goals.add(db, "Цель")
    db.commit()
    goals.mark(db, gid, "done")
    db.commit()
    result = goals.list_goals(db, include_closed=True)
    assert len(result) == 1
    assert result[0]["id"] == gid


def test_list_goals_explicit_period_filter(db):
    goals.add(db, "Месяц 1", period="2026-07")
    g2 = goals.add(db, "Месяц 2", period="2026-08")
    db.commit()
    result = goals.list_goals(db, period="2026-08")
    assert [g["id"] for g in result] == [g2]


# --- mark -----------------------------------------------------------------

def test_mark_open_to_done_sets_closed_at(db):
    gid = goals.add(db, "Цель")
    db.commit()
    ok = goals.mark(db, gid, "done")
    db.commit()
    assert ok is True
    g = goals.get(db, gid)
    assert g["status"] == "done"
    assert g["closed_at"] is not None


def test_mark_open_to_declined_sets_closed_at(db):
    gid = goals.add(db, "Цель")
    db.commit()
    goals.mark(db, gid, "declined")
    db.commit()
    g = goals.get(db, gid)
    assert g["status"] == "declined"
    assert g["closed_at"] is not None


def test_mark_reopen_clears_closed_at(db):
    gid = goals.add(db, "Цель")
    db.commit()
    goals.mark(db, gid, "done")
    db.commit()
    goals.mark(db, gid, "open")
    db.commit()
    g = goals.get(db, gid)
    assert g["status"] == "open"
    assert g["closed_at"] is None


def test_mark_declined_to_open_reopen(db):
    gid = goals.add(db, "Цель")
    db.commit()
    goals.mark(db, gid, "declined")
    db.commit()
    goals.mark(db, gid, "open")
    db.commit()
    g = goals.get(db, gid)
    assert g["status"] == "open"
    assert g["closed_at"] is None


def test_mark_done_to_declined_invalid_transition(db):
    gid = goals.add(db, "Цель")
    db.commit()
    goals.mark(db, gid, "done")
    db.commit()
    with pytest.raises(ValueError):
        goals.mark(db, gid, "declined")


def test_mark_open_to_open_invalid_transition(db):
    gid = goals.add(db, "Цель")
    db.commit()
    with pytest.raises(ValueError):
        goals.mark(db, gid, "open")


def test_mark_invalid_status_value_raises(db):
    gid = goals.add(db, "Цель")
    db.commit()
    with pytest.raises(ValueError):
        goals.mark(db, gid, "bogus")


def test_mark_unknown_goal_returns_false(db):
    assert goals.mark(db, 9999, "done") is False


# --- take -------------------------------------------------------------

def test_take_from_open_moves_period(db):
    gid = goals.add(db, "Хвост", period="2026-07")
    db.commit()
    ok = goals.take(db, gid, "2026-08")
    db.commit()
    assert ok is True
    g = goals.get(db, gid)
    assert g["period"] == "2026-08"
    assert g["status"] == "open"
    assert g["closed_at"] is None


def test_take_from_declined_revives(db):
    gid = goals.add(db, "Хвост", period="2026-07")
    db.commit()
    goals.mark(db, gid, "declined")
    db.commit()
    ok = goals.take(db, gid, "2026-08")
    db.commit()
    assert ok is True
    g = goals.get(db, gid)
    assert g["period"] == "2026-08"
    assert g["status"] == "open"
    assert g["closed_at"] is None


def test_take_quarter_goal_raises(db):
    gid = goals.add(db, "Квартальная", period="2026-Q3")
    db.commit()
    with pytest.raises(ValueError):
        goals.take(db, gid, "2026-08")


def test_take_target_must_be_month(db):
    gid = goals.add(db, "Месячная", period="2026-07")
    db.commit()
    with pytest.raises(ValueError):
        goals.take(db, gid, "2026-Q3")


def test_take_unknown_goal_returns_false(db):
    assert goals.take(db, 9999, "2026-08") is False


# --- digest scoping (spec §3: only month goals feed the digest) -------

def test_quarter_goals_excluded_from_month_period_filter(db):
    goals.add(db, "Квартальная", period="2026-Q3")
    db.commit()
    result = goals.list_goals(db, period="2026-08")
    assert result == []


# --- audit --------------------------------------------------------------

def test_add_writes_audit_log(db):
    gid = goals.add(db, "Цель", period="2026-08")
    db.commit()
    row = db.execute(
        "SELECT kind, payload FROM audit_log WHERE kind='goal.add' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert row is not None
    assert f'"id": {gid}' in row["payload"]


def test_mark_writes_audit_log(db):
    gid = goals.add(db, "Цель")
    db.commit()
    goals.mark(db, gid, "done")
    db.commit()
    row = db.execute(
        "SELECT kind FROM audit_log WHERE kind='goal.mark' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert row is not None


def test_take_writes_audit_log(db):
    gid = goals.add(db, "Цель", period="2026-07")
    db.commit()
    goals.take(db, gid, "2026-08")
    db.commit()
    row = db.execute(
        "SELECT kind FROM audit_log WHERE kind='goal.take' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert row is not None
