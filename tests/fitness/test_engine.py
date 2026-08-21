from datetime import date, datetime, time, timedelta, timezone

import pytest

from fitness.engine import DAILY_AUTOBOOK_LIMIT, tick
from fitness.invictus_client import BookingRejected, BookingsInfo, SessionDead
from fitness.models import Booking, ClassSlot, ClubRules
from fitness.rules import RuleStore, WatchRule
from fitness.session import Session, SessionStore

# 12:20 UTC выбрано намеренно: minute=20 — это НЕ «ленивая» минута часа,
# поэтому тесты видят именно жадный проход по кромке горизонта.
NOW = datetime(2026, 8, 4, 12, 20, tzinfo=timezone.utc)  # вторник
TARGET = datetime(2026, 8, 11, 14, 0, tzinfo=timezone.utc)  # вторник, 19:00 Алматы, кромка


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    SessionStore().save(
        Session(access_token="a", refresh_token="r", expires_at=NOW + timedelta(hours=2))
    )
    return tmp_path


def _slot(**kw):
    base = dict(
        class_id="c1",
        title="Функциональный тренинг",
        trainer=None,
        club_id="abay",
        starts_at=TARGET,
        ends_at=TARGET + timedelta(hours=1),
        capacity=20,
        taken=10,
        booking_opens_at=NOW - timedelta(minutes=1),
        my_status="none",
    )
    base.update(kw)
    return ClassSlot(**base)


def _rule(**kw):
    base = dict(
        rule_id="r1",
        kind="recurring",
        title_pattern="функционал",
        club_id="abay",
        weekday=1,
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


class FakeClient:
    def __init__(self, slots=None, bookings=None, book_error=None,
                 banned_till=None, ban_reason=None):
        self.slots = slots if slots is not None else []
        self.bookings = bookings or []
        self.book_error = book_error
        self.banned_till = banned_till
        self.ban_reason = ban_reason
        self.calls = []
        self.booked = []
        self.waitlisted = []

    def schedule(self, day_from, day_to=None, club_id=None):
        self.calls.append(("schedule", day_from, day_to))
        day_to = day_to or day_from
        return [s for s in self.slots if day_from <= s.local_start.date() <= day_to]

    def bookings_info(self):
        self.calls.append(("bookings_info", None, None))
        return BookingsInfo(
            bookings=list(self.bookings),
            banned_till=self.banned_till,
            ban_reason=self.ban_reason,
        )

    def my_bookings(self):
        return list(self.bookings)

    def book(self, class_id):
        self.calls.append(("book", class_id, None))
        if self.book_error:
            raise self.book_error
        booking = Booking(class_id=class_id, title="Функциональный тренинг",
                          starts_at=TARGET, status="booked")
        self.booked.append(class_id)
        self.bookings.append(booking)
        return booking

    def join_waitlist(self, class_id):
        self.calls.append(("waitlist", class_id, None))
        self.waitlisted.append(class_id)
        return Booking(class_id=class_id, title="x", starts_at=TARGET, status="waitlisted")


# Ревизия 3: max_active_bookings больше не условие работы — в API его нет.
RULES = ClubRules(booking_opens_days_ahead=7, cancel_deadline_hours=2,
                  no_show_penalty="блокировка записи на 3 дня")


def _tick(client, now=NOW, club_rules=RULES):
    return tick(client=client, rule_store=RuleStore(), club_rules=club_rules,
                now=now, session_store=SessionStore())


def test_books_a_matching_slot_once_the_window_is_open(home):
    RuleStore().add(_rule())
    client = FakeClient(slots=[_slot()])

    result = _tick(client)

    assert client.booked == ["c1"]
    assert any("Функциональный" in m for m in result.messages)


def test_does_not_touch_api_when_no_rule_day_is_at_the_horizon_edge(home):
    # правило на четверг, а кромка горизонта — вторник 11.08
    RuleStore().add(_rule(weekday=3))
    client = FakeClient(slots=[_slot()])

    result = _tick(client)

    assert result.api_calls == 0
    assert client.calls == []


def test_schedule_is_fetched_as_one_range_request(home):
    RuleStore().add(_rule())
    client = FakeClient(slots=[_slot()])

    _tick(client, now=NOW.replace(minute=2))  # ленивый проход, дней-кандидатов много

    schedule_calls = [c for c in client.calls if c[0] == "schedule"]
    assert len(schedule_calls) == 1, "диапазон запрашивается одним вызовом, не день за днём"


def test_lazy_sweep_queries_every_candidate_day(home):
    RuleStore().add(_rule())
    client = FakeClient(slots=[_slot()])

    result = _tick(client, now=NOW.replace(minute=2))

    assert result.api_calls > 0


def test_paused_flag_stops_all_writes(home):
    from fitness import store as store_mod

    RuleStore().add(_rule())
    store_mod.state_dir().mkdir(parents=True, exist_ok=True)
    (store_mod.state_dir() / "PAUSED").touch()
    client = FakeClient(slots=[_slot()])

    _tick(client)

    assert client.booked == []
    assert client.calls == []


def test_dead_session_stops_the_tick_and_reports_once(home):
    RuleStore().add(_rule())
    session = SessionStore().load()
    SessionStore().mark_dead(session, NOW, reason="refresh 401")
    client = FakeClient(slots=[_slot()])

    first = _tick(client)
    second = _tick(client)

    assert client.booked == []
    assert any("сесси" in a.lower() for a in first.alerts)
    assert first.messages == []
    assert second.alerts == []


def test_already_booked_slot_is_skipped(home):
    RuleStore().add(_rule())
    client = FakeClient(slots=[_slot(my_status="booked")])

    _tick(client)

    assert client.booked == []


def test_time_conflict_with_existing_booking_blocks_the_write(home):
    RuleStore().add(_rule())
    existing = Booking(class_id="other", title="Йога", starts_at=TARGET, status="booked")
    client = FakeClient(slots=[_slot()], bookings=[existing])

    result = _tick(client)

    assert client.booked == []
    assert any("конфликт" in m.lower() for m in result.messages)


def test_daily_autobook_limit_is_enforced(home):
    # по одному правилу на каждое занятие: 19:00, 20:30, 22:00 клубного времени
    for index in range(DAILY_AUTOBOOK_LIMIT + 1):
        RuleStore().add(
            _rule(
                rule_id=f"r{index}",
                at_time=(datetime(2026, 8, 11, 19, 0) + timedelta(minutes=90 * index)).time(),
            )
        )
    slots = [
        _slot(class_id=f"c{i}", starts_at=TARGET + timedelta(minutes=90 * i),
              ends_at=TARGET + timedelta(minutes=90 * i + 60))
        for i in range(DAILY_AUTOBOOK_LIMIT + 1)
    ]
    client = FakeClient(slots=slots)

    _tick(client)

    assert len(client.booked) == DAILY_AUTOBOOK_LIMIT


# --- ревизия 3: заполненное занятие не пробуем записывать вовсе ------------


def test_full_slot_goes_straight_to_the_waitlist_without_trying_to_book(home):
    # приложение при отсутствии мест не показывает кнопку записи: серверное
    # поведение неизвестно, пробовать его на живом аккаунте нельзя
    RuleStore().add(_rule(waitlist_ok=True))
    client = FakeClient(slots=[_slot(taken=20)])

    _tick(client)

    assert client.booked == []
    assert client.waitlisted == ["c1"]
    assert not any(c[0] == "book" for c in client.calls)


def test_full_slot_without_waitlist_stays_silent(home):
    RuleStore().add(_rule(waitlist_ok=False))
    client = FakeClient(slots=[_slot(taken=20)])

    result = _tick(client)

    assert client.booked == []
    assert client.waitlisted == []
    assert result.messages == []


def test_waitlist_is_not_joined_twice_for_the_same_slot(home):
    RuleStore().add(_rule(waitlist_ok=True))
    client = FakeClient(slots=[_slot(taken=20)])

    _tick(client)
    _tick(client)

    assert client.waitlisted == ["c1"]


# --- блокировка записи -----------------------------------------------------


def test_active_ban_stops_the_tick_before_any_booking_attempt(home):
    RuleStore().add(_rule())
    client = FakeClient(
        slots=[_slot()],
        banned_till=NOW + timedelta(days=2),
        ban_reason="Пропуск тренировки",
    )

    result = _tick(client)

    assert client.booked == []
    assert not any(c[0] == "schedule" for c in client.calls)
    assert any("заблокирована" in m.lower() for m in result.messages)
    assert any("Пропуск тренировки" in m for m in result.messages)


def test_ban_is_reported_once_not_every_tick(home):
    RuleStore().add(_rule())
    client = FakeClient(slots=[_slot()], banned_till=NOW + timedelta(days=2),
                        ban_reason="Пропуск тренировки")

    first = _tick(client)
    second = _tick(client)

    assert first.messages
    assert second.messages == []


def test_expired_ban_does_not_stop_the_tick(home):
    RuleStore().add(_rule())
    client = FakeClient(slots=[_slot()], banned_till=NOW - timedelta(days=1))

    _tick(client)

    assert client.booked == ["c1"]


# --- прочее ---------------------------------------------------------------


def test_failure_is_reported_once_close_to_the_class(home):
    RuleStore().add(_rule(waitlist_ok=False))
    client = FakeClient(slots=[_slot()], book_error=BookingRejected("unknown"))
    late = TARGET - timedelta(hours=6)  # 08:00 UTC, минута 0 → ленивый проход

    first = _tick(client, now=late)
    second = _tick(client, now=late + timedelta(hours=1))  # снова ленивый проход

    assert any("не удалось" in m.lower() for m in first.messages)
    assert second.messages == [], "повторное уведомление о том же провале недопустимо"


def test_oneshot_rule_removes_itself_after_success(home):
    RuleStore().add(
        _rule(rule_id="one", kind="oneshot", weekday=None, target_date=date(2026, 8, 11))
    )
    client = FakeClient(slots=[_slot()])

    _tick(client)

    assert RuleStore().load() == []


def test_unknown_club_rules_disable_autobooking(home):
    RuleStore().add(_rule())
    client = FakeClient(slots=[_slot()])

    result = _tick(client, club_rules=ClubRules())

    assert client.booked == []
    assert any("правила клуба" in a.lower() for a in result.alerts)
    assert result.messages == []


def test_missing_cancel_deadline_also_disables_autobooking(home):
    RuleStore().add(_rule())
    client = FakeClient(slots=[_slot()])

    result = _tick(client, club_rules=ClubRules(booking_opens_days_ahead=7))

    assert client.booked == []
    assert result.alerts
    assert result.messages == []


def test_unknown_active_booking_limit_does_not_disable_autobooking(home):
    # ревизия 3: лимита в API нет, требовать его как условие работы нельзя
    RuleStore().add(_rule())
    client = FakeClient(slots=[_slot()])

    _tick(client, club_rules=ClubRules(booking_opens_days_ahead=7, cancel_deadline_hours=2))

    assert client.booked == ["c1"]


def test_session_death_during_the_tick_is_reported_once(home):
    RuleStore().add(_rule())
    client = FakeClient(slots=[_slot()], book_error=SessionDead("умерла"))

    result = _tick(client)

    assert any("сесси" in a.lower() for a in result.alerts)
    assert result.messages == []


# --- две аудитории: Амина в WhatsApp и оператор в Telegram -----------------
# stdout кроновой джобы доставляется Амине как есть, поэтому «что попало в
# messages» — это не косметика, а вопрос того, кому уйдёт сообщение.


def test_dead_session_message_goes_to_operator_not_to_her(home):
    """Мёртвая сессия — не её забота: в messages пусто, в alerts текст."""
    RuleStore().add(_rule())
    SessionStore().mark_dead(SessionStore().load(), NOW, reason="refresh 401")
    client = FakeClient(slots=[_slot()])

    result = _tick(client)

    assert result.messages == []
    assert any("Сессия Invictus" in a for a in result.alerts)


def test_dead_session_alert_points_at_headless_login(home):
    # Захват токена «на ресепшне» устарел: с 18.08 есть headless-логин по OTP.
    RuleStore().add(_rule())
    SessionStore().mark_dead(SessionStore().load(), NOW, reason="refresh 401")

    result = _tick(FakeClient(slots=[_slot()]))

    joined = " ".join(result.alerts)
    assert "fitness_login_request" in joined
    assert "ресепшн" not in joined.lower()


def test_missing_club_rules_goes_to_operator(home):
    RuleStore().add(_rule())
    client = FakeClient(slots=[_slot()])

    result = _tick(client, club_rules=ClubRules(booking_opens_days_ahead=None,
                                                cancel_deadline_hours=None))

    assert result.messages == []
    assert any("Правила клуба" in a for a in result.alerts)


def test_session_death_during_the_tick_goes_to_operator(home):
    RuleStore().add(_rule())
    client = FakeClient(slots=[_slot()], book_error=SessionDead("умерла"))

    result = _tick(client)

    assert result.messages == []
    assert any("Сессия Invictus" in a for a in result.alerts)


def test_ban_notice_still_goes_to_her(home):
    # Санкция клуба — её санкция: ей и знать, оператору тут делать нечего.
    RuleStore().add(_rule())
    client = FakeClient(slots=[_slot()], banned_till=NOW + timedelta(days=2),
                        ban_reason="Пропуск тренировки")

    result = _tick(client)

    assert any("заблокирована" in m for m in result.messages)
    assert result.alerts == []


def test_successful_booking_goes_to_her(home):
    RuleStore().add(_rule())
    client = FakeClient(slots=[_slot()])

    result = _tick(client)

    assert any("Записал" in m for m in result.messages)
    assert result.alerts == []


def test_waitlist_and_conflict_notices_go_to_her(home):
    RuleStore().add(_rule(waitlist_ok=True))
    result = _tick(FakeClient(slots=[_slot(taken=20)]))
    assert any("лист ожидания" in m for m in result.messages)
    assert result.alerts == []


def test_no_user_message_ever_mentions_the_session(home):
    """Инвариант: инженерный словарь не встречается в messages."""
    RuleStore().add(_rule())
    SessionStore().mark_dead(SessionStore().load(), NOW, reason="refresh 401")

    result = _tick(FakeClient(slots=[_slot()]))

    forbidden = ("сесси", "токен", "refresh", "401", "API", "fail-closed")
    joined = " ".join(result.messages).lower()
    assert not any(w.lower() in joined for w in forbidden)
