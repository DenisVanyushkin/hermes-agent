import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from fitness.invictus_client import BookingRejected, InvictusClient, SessionDead
from fitness.session import Session, SessionStore

FIXTURES = Path(__file__).parent.parent / "fixtures" / "fitness"
NOW = datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc)

MY_ID = "6221276709aaaaaaaaaaaaaa"
BOOKED_EVENT = "694f96adc11075c757399215"
WAITLIST_EVENT = "694f78e6efa26fa2b7dff5f4"
TOKEN = json.loads((FIXTURES / "refresh_ok.json").read_text(encoding="utf-8"))["accessToken"]


def _fixture(name):
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


class FakeTransport:
    """Подменяет сетевой слой: отдаёт заготовленные ответы и пишет запросы."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, *, headers, body=None):
        self.calls.append({"method": method, "url": url, "headers": headers, "body": body})
        status, payload = self.responses.pop(0)
        return status, payload


def _save(**kw):
    base = dict(
        access_token=TOKEN,
        refresh_token="ref",
        expires_at=NOW + timedelta(hours=1),
        device_headers={"X-Device-Id": "dev"},
    )
    base.update(kw)
    SessionStore().save(Session(**base))


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    _save()
    return tmp_path


def _client(transport, **kw):
    return InvictusClient(transport=transport, now=lambda: NOW, **kw)


# --- расписание -----------------------------------------------------------


def test_schedule_parses_fixture_into_slots(home):
    transport = FakeTransport([(200, _fixture("schedule_day"))])

    slots = _client(transport).schedule(date(2026, 8, 12))

    assert len(slots) == 2
    first = slots[0]
    assert first.class_id == BOOKED_EVENT
    assert first.title == "FUNCTIONAL TRAINING"
    assert first.starts_at.tzinfo is not None
    assert first.my_status in {"none", "booked", "waitlisted"}


def test_class_id_is_the_event_id_not_the_group_training_template(home):
    # groupTraining._id повторяется у десятков слотов — записываться по нему нельзя
    transport = FakeTransport([(200, _fixture("schedule_day"))])
    slots = _client(transport).schedule(date(2026, 8, 12))
    assert slots[0].class_id != "64ae8f3235b31e00dff033ea"


def test_my_status_is_computed_from_participants_and_waitlist(home):
    transport = FakeTransport([(200, _fixture("schedule_day"))])
    slots = _client(transport).schedule(date(2026, 8, 12))
    assert slots[0].my_status == "booked"      # мой id в participantsList
    assert slots[1].my_status == "waitlisted"  # мой id в waitlist


def test_taken_is_the_length_of_participants_list(home):
    transport = FakeTransport([(200, _fixture("schedule_day"))])
    slots = _client(transport).schedule(date(2026, 8, 12))
    assert slots[0].taken == 2
    assert slots[0].capacity == 16
    assert slots[0].spots_left == 14


def test_trainer_name_is_stripped(home):
    transport = FakeTransport([(200, _fixture("schedule_day"))])
    slots = _client(transport).schedule(date(2026, 8, 12))
    assert slots[0].trainer == "Тренер Один"


def test_slots_never_carry_other_members_identifiers(home):
    # participantsList сводится к булеву значению при разборе и дальше не едет
    transport = FakeTransport([(200, _fixture("schedule_day"))])
    slots = _client(transport).schedule(date(2026, 8, 12))
    dumped = repr(slots)
    assert "000000000000000000000002" not in dumped
    assert "000000000000000000000004" not in dumped


def test_schedule_sends_device_headers_and_bearer(home):
    transport = FakeTransport([(200, _fixture("schedule_day"))])
    _client(transport).schedule(date(2026, 8, 12))

    headers = transport.calls[0]["headers"]
    assert headers["Authorization"] == f"Bearer {TOKEN}"
    assert headers["X-Device-Id"] == "dev"


def test_schedule_range_is_sent_as_club_days_expressed_in_utc(home):
    # 00:00 Алматы = 19:00Z предыдущего дня
    transport = FakeTransport([(200, _fixture("schedule_day"))])
    _client(transport).schedule(date(2026, 8, 12), date(2026, 8, 12))

    url = transport.calls[0]["url"]
    assert "2026-08-11T19%3A00%3A00.000Z" in url
    assert "2026-08-12T18%3A59%3A59.999Z" in url
    assert "62212767097e5c317055385a" in url


def test_schedule_asks_for_a_whole_range_in_one_request(home):
    transport = FakeTransport([(200, _fixture("schedule_day"))])
    _client(transport).schedule(date(2026, 8, 12), date(2026, 8, 18))
    assert len(transport.calls) == 1


# --- мои записи -----------------------------------------------------------


def test_my_bookings_uses_its_own_endpoint(home):
    transport = FakeTransport([(200, _fixture("my_bookings"))])
    bookings = _client(transport).my_bookings()

    assert len(transport.calls) == 1
    assert "/api/users/me/bookings-info" in transport.calls[0]["url"]
    assert [b.class_id for b in bookings] == [BOOKED_EVENT]
    assert all(b.status in {"booked", "waitlisted"} for b in bookings)


def test_bookings_info_exposes_the_ban(home):
    transport = FakeTransport([(200, _fixture("my_bookings_banned"))])
    info = _client(transport).bookings_info()

    assert info.banned_till == datetime(2026, 8, 14, 9, 0, tzinfo=timezone.utc)
    assert info.ban_reason == "Пропуск тренировки"
    assert info.bookings == []


def test_bookings_info_reports_no_ban_when_field_is_null(home):
    transport = FakeTransport([(200, _fixture("my_bookings"))])
    info = _client(transport).bookings_info()
    assert info.banned_till is None


# --- запись, отмена, лист ожидания ---------------------------------------


def test_book_posts_event_id_and_verifies_by_rereading_bookings(home):
    transport = FakeTransport([(200, _fixture("book_ok")), (200, _fixture("my_bookings"))])

    booking = _client(transport).book(BOOKED_EVENT)

    assert booking.status == "booked"
    assert len(transport.calls) == 2, "после book обязателен перечит my_bookings"
    assert transport.calls[0]["method"] == "POST"
    assert "/api/eventAddParticipant" in transport.calls[0]["url"]
    assert transport.calls[0]["body"] == {"eventId": BOOKED_EVENT}


def test_book_fails_when_verification_does_not_show_the_booking(home):
    transport = FakeTransport([(200, _fixture("book_ok")), (200, {"records": []})])

    with pytest.raises(BookingRejected) as exc:
        _client(transport).book("class-not-in-my-bookings")
    assert exc.value.reason == "unverified"


def test_any_non_200_is_an_unknown_rejection(home):
    # Таксономия ошибок не наблюдалась: коды не угадываем (ревизия 3)
    transport = FakeTransport([(409, {"message": "some server text"})])

    with pytest.raises(BookingRejected) as exc:
        _client(transport).book(BOOKED_EVENT)
    assert exc.value.reason == "unknown"


def test_cancel_posts_event_id_and_verifies_the_booking_is_gone(home):
    transport = FakeTransport([(200, _fixture("cancel_ok")), (200, {"records": []})])

    _client(transport).cancel(BOOKED_EVENT)

    assert len(transport.calls) == 2
    assert transport.calls[0]["method"] == "POST"  # именно POST, не DELETE
    assert "/api/eventDeleteParticipant" in transport.calls[0]["url"]
    assert transport.calls[0]["body"] == {"eventId": BOOKED_EVENT}


def test_cancel_fails_when_the_booking_is_still_there(home):
    transport = FakeTransport([(200, _fixture("cancel_ok")), (200, _fixture("my_bookings"))])

    with pytest.raises(BookingRejected) as exc:
        _client(transport).cancel(BOOKED_EVENT)
    assert exc.value.reason == "unverified"


def test_waitlist_uses_put_and_verifies_from_the_response_itself(home):
    # ответ PUT — полное событие с обновлённым waitlist, перечит не нужен
    transport = FakeTransport([(200, _fixture("waitlist_ok"))])

    booking = _client(transport).join_waitlist(WAITLIST_EVENT)

    assert len(transport.calls) == 1
    assert transport.calls[0]["method"] == "PUT"
    assert transport.calls[0]["body"] == {"eventId": WAITLIST_EVENT}
    assert booking.status == "waitlisted"


# --- сессия ---------------------------------------------------------------


def test_rate_limit_is_retried_with_backoff(home):
    transport = FakeTransport([(429, {}), (200, _fixture("schedule_day"))])
    sleeps = []
    _client(transport, sleep=sleeps.append).schedule(date(2026, 8, 12))

    assert sleeps and sleeps[0] > 0
    assert len(transport.calls) == 2


def test_expired_access_token_is_refreshed_before_the_call(home):
    _save(access_token="old", expires_at=NOW - timedelta(minutes=1))
    transport = FakeTransport([(200, _fixture("refresh_ok")), (200, _fixture("schedule_day"))])

    _client(transport).schedule(date(2026, 8, 12))

    assert len(transport.calls) == 2
    assert transport.calls[0]["body"] == {"refreshToken": "ref"}
    assert transport.calls[1]["headers"]["Authorization"] == f"Bearer {TOKEN}"


def test_refresh_stores_the_fresh_refresh_token(home):
    # вендор может в любой момент ужесточить ротацию — свежий токен сохраняем всегда
    _save(access_token="old", expires_at=NOW - timedelta(minutes=1))
    transport = FakeTransport([(200, _fixture("refresh_ok")), (200, _fixture("schedule_day"))])

    _client(transport).schedule(date(2026, 8, 12))

    stored = SessionStore().load()
    assert stored.refresh_token == "REDACTED-refresh-token-43-chars-aaaaaaaaaa"
    assert stored.access_token == TOKEN


def test_expiry_after_refresh_comes_from_the_token_itself(home):
    # поля expires_in в ответе нет — срок берётся из claim exp
    _save(access_token="old", expires_at=NOW - timedelta(minutes=1))
    transport = FakeTransport([(200, _fixture("refresh_ok")), (200, _fixture("schedule_day"))])

    _client(transport).schedule(date(2026, 8, 12))

    assert SessionStore().load().expires_at == datetime(2026, 8, 12, 12, 0, tzinfo=timezone.utc)


def test_failed_refresh_marks_session_dead(home):
    _save(access_token="old", expires_at=NOW - timedelta(minutes=1), device_headers={})
    transport = FakeTransport([(401, {"error": "invalid_grant"})])

    with pytest.raises(SessionDead):
        _client(transport).schedule(date(2026, 8, 12))

    assert SessionStore().load().is_dead is True


def test_401_mid_flight_triggers_refresh_and_one_retry(home):
    transport = FakeTransport(
        [(401, {}), (200, _fixture("refresh_ok")), (200, _fixture("schedule_day"))]
    )
    slots = _client(transport).schedule(date(2026, 8, 12))
    assert len(slots) == 2
    assert len(transport.calls) == 3


def test_401_after_refresh_marks_session_dead(home):
    transport = FakeTransport([(401, {}), (200, _fixture("refresh_ok")), (401, {})])

    with pytest.raises(SessionDead):
        _client(transport).schedule(date(2026, 8, 12))
    assert SessionStore().load().is_dead is True


def test_dead_session_is_refused_without_touching_the_network(home):
    session = SessionStore().load()
    SessionStore().mark_dead(session, NOW, reason="refresh 401")
    transport = FakeTransport([])

    with pytest.raises(SessionDead):
        _client(transport).schedule(date(2026, 8, 12))
    assert transport.calls == []


def test_missing_session_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    with pytest.raises(SessionDead):
        _client(FakeTransport([])).schedule(date(2026, 8, 12))


def test_headless_login_never_calls_refresh(home):
    # Перелогин теперь есть (headless, фейковый device-id), но /api/refresh он не
    # трогает: ротация сожгла бы свежую пару прямо из-под нас.
    fitness_auth.set_phone_number("77011102626")
    transport = FakeTransport([(200, {"accessToken": TOKEN, "refreshToken": "r"})])
    _client(transport).login("9797")
    assert all("/api/refresh" not in c["url"] for c in transport.calls)


def test_tokens_are_redacted_in_repr(home):
    assert TOKEN not in repr(_client(FakeTransport([])))


# --- headless-логин -------------------------------------------------------

from fitness.auth import LoginError, MissingPhoneNumber
from fitness import auth as fitness_auth


def test_request_otp_posts_login_with_device_headers_and_body(home):
    fitness_auth.set_phone_number("77011102626")
    transport = FakeTransport([(200, "ok")])

    number = _client(transport).request_otp()

    assert number == "77011102626"
    call = transport.calls[0]
    assert call["method"] == "POST"
    assert call["url"].endswith("/api/login")
    assert call["body"] == {
        "phoneNumber": "77011102626",
        "otpMethod": "sms",
        "language": "en",
    }
    assert "x-device-id" in call["headers"]
    assert call["headers"]["x-platform"] == "ios"


def test_request_otp_without_number_raises_missing_phone(home):
    transport = FakeTransport([(200, "ok")])
    with pytest.raises(MissingPhoneNumber):
        _client(transport).request_otp()
    assert transport.calls == []  # в сеть не ходили


def test_request_otp_with_explicit_number_persists_it(home):
    transport = FakeTransport([(200, "ok")])
    _client(transport).request_otp("77770000000")
    assert fitness_auth.load_or_create_device()["phone_number"] == "77770000000"


def test_request_otp_raises_login_error_on_non_2xx(home):
    fitness_auth.set_phone_number("77011102626")
    transport = FakeTransport([(401, {"err": {"ru": "Неизвестный номер"}})])
    with pytest.raises(LoginError, match="Неизвестный номер"):
        _client(transport).request_otp()


def test_login_saves_session_from_checksms(home):
    fitness_auth.set_phone_number("77011102626")
    transport = FakeTransport([(200, {"accessToken": TOKEN, "refreshToken": "newref"})])

    session = _client(transport).login("9797")

    call = transport.calls[0]
    assert call["method"] == "POST"
    assert call["url"].endswith("/api/checkSms")
    assert call["body"] == {"phoneNumber": "77011102626", "smsCode": "9797"}

    saved = SessionStore().load()
    assert saved.access_token == TOKEN
    assert saved.refresh_token == "newref"
    assert saved.dead_since is None
    assert saved.death_reason is None
    assert saved.device_headers["x-device-id"]  # фейковый device-id проставлен
    from fitness.session import access_token_expiry
    assert saved.expires_at == access_token_expiry(TOKEN)
    assert session.access_token == TOKEN


def test_login_without_number_raises_missing_phone(home):
    transport = FakeTransport([(200, {"accessToken": TOKEN, "refreshToken": "r"})])
    with pytest.raises(MissingPhoneNumber):
        _client(transport).login("9797")
    assert transport.calls == []


def test_login_bad_code_raises_and_keeps_existing_session(home):
    # home-фикстура уже сохранила рабочую сессию (_save). Плохой код её не портит.
    fitness_auth.set_phone_number("77011102626")
    before = SessionStore().load().access_token
    transport = FakeTransport([(400, {"err": {"ru": "Неверный код"}})])
    with pytest.raises(LoginError, match="Неверный код"):
        _client(transport).login("0000")
    assert SessionStore().load().access_token == before
