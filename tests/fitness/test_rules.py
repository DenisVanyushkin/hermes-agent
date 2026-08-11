from datetime import date, datetime, time, timedelta, timezone

import pytest

from fitness.models import ClassSlot
from fitness.rules import RuleStore, WatchRule, is_expired, rule_matches


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    return tmp_path


def _slot(**kw):
    base = dict(
        class_id="c1",
        title="Функциональный тренинг",
        trainer="Иван",
        club_id="abay",
        # 14:00 UTC = 19:00 Алматы, вторник 11.08.2026
        starts_at=datetime(2026, 8, 11, 14, 0, tzinfo=timezone.utc),
        ends_at=datetime(2026, 8, 11, 15, 0, tzinfo=timezone.utc),
        capacity=20,
        taken=10,
        booking_opens_at=None,
        my_status="none",
    )
    base.update(kw)
    return ClassSlot(**base)


def _recurring(**kw):
    base = dict(
        rule_id="r1",
        kind="recurring",
        title_pattern="функционал",
        club_id="abay",
        weekday=1,  # вторник
        at_time=time(19, 0),
        window_minutes=30,
        trainer=None,
        waitlist_ok=True,
        target_date=None,
        expires_at=None,
        active=True,
    )
    base.update(kw)
    return WatchRule(**base)


def test_recurring_rule_matches_by_weekday_title_and_local_time(home):
    assert rule_matches(_recurring(), _slot()) is True


def test_title_match_is_case_insensitive_substring(home):
    assert rule_matches(_recurring(title_pattern="ФУНКЦИОНАЛ"), _slot()) is True
    assert rule_matches(_recurring(title_pattern="йога"), _slot()) is False


def test_wrong_weekday_does_not_match(home):
    assert rule_matches(_recurring(weekday=3), _slot()) is False


def test_time_outside_window_does_not_match(home):
    # занятие в 19:00 Алматы, правило на 17:00 ± 30 мин
    assert rule_matches(_recurring(at_time=time(17, 0)), _slot()) is False


def test_time_inside_window_matches(home):
    assert rule_matches(_recurring(at_time=time(18, 40)), _slot()) is True


def test_other_club_does_not_match(home):
    assert rule_matches(_recurring(club_id="aport"), _slot()) is False


def test_club_none_matches_any_club(home):
    assert rule_matches(_recurring(club_id=None), _slot()) is True


def test_trainer_filter_is_applied_when_set(home):
    assert rule_matches(_recurring(trainer="Иван"), _slot()) is True
    assert rule_matches(_recurring(trainer="Пётр"), _slot()) is False


def test_inactive_rule_never_matches(home):
    assert rule_matches(_recurring(active=False), _slot()) is False


def test_oneshot_rule_matches_only_its_target_date(home):
    rule = WatchRule(
        rule_id="r2",
        kind="oneshot",
        title_pattern="йога",
        club_id=None,
        weekday=None,
        at_time=time(19, 0),
        window_minutes=30,
        trainer=None,
        waitlist_ok=False,
        target_date=date(2026, 8, 11),
        expires_at=None,
        active=True,
    )
    assert rule_matches(rule, _slot(title="Йога")) is True
    assert rule_matches(
        rule,
        _slot(
            title="Йога",
            starts_at=datetime(2026, 8, 12, 14, 0, tzinfo=timezone.utc),
            ends_at=datetime(2026, 8, 12, 15, 0, tzinfo=timezone.utc),
        ),
    ) is False


def test_expired_rule_is_detected(home):
    now = datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc)
    assert is_expired(_recurring(expires_at=now - timedelta(days=1)), now) is True
    assert is_expired(_recurring(expires_at=now + timedelta(days=1)), now) is False
    assert is_expired(_recurring(expires_at=None), now) is False


def test_rule_store_roundtrip(home):
    store = RuleStore()
    store.add(_recurring())
    loaded = store.load()
    assert len(loaded) == 1
    assert loaded[0].rule_id == "r1"
    assert loaded[0].at_time == time(19, 0)


def test_rule_store_roundtrips_oneshot_dates(home):
    store = RuleStore()
    store.add(_recurring(rule_id="r2", kind="oneshot", weekday=None,
                         target_date=date(2026, 8, 11)))
    assert store.load()[0].target_date == date(2026, 8, 11)


def test_remove_returns_false_for_unknown_rule(home):
    store = RuleStore()
    store.add(_recurring())
    assert store.remove("nope") is False
    assert store.remove("r1") is True
    assert store.load() == []
