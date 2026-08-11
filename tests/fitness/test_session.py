import base64
import json
from datetime import datetime, timedelta, timezone

import pytest

from fitness.session import Session, SessionStore, access_token_expiry, access_token_user_id


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    return tmp_path


NOW = datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc)


def _jwt(**claims) -> str:
    """Собирает JWT-подобный токен. Подпись не проверяется — читаем только claims."""

    def seg(payload):
        raw = json.dumps(payload).encode("utf-8")
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")

    return f"{seg({'alg': 'HS256', 'typ': 'JWT'})}.{seg(claims)}.signature"


TOKEN = _jwt(
    id="6221276709aaaaaaaaaaaaaa",
    sid="s1",
    typ="access",
    iat=int((NOW - timedelta(hours=1)).timestamp()),
    exp=int((NOW + timedelta(hours=23)).timestamp()),
)


def _session(**kw):
    base = dict(
        access_token="acc",
        refresh_token="ref",
        expires_at=NOW + timedelta(hours=1),
        device_headers={"X-Device-Id": "dev"},
        captured_at=NOW - timedelta(days=1),
        dead_since=None,
        last_death_notice_at=None,
    )
    base.update(kw)
    return Session(**base)


def test_load_returns_none_when_nothing_captured(home):
    assert SessionStore().load() is None


def test_save_then_load_roundtrip(home):
    store = SessionStore()
    store.save(_session())
    loaded = store.load()
    assert loaded.access_token == "acc"
    assert loaded.expires_at == NOW + timedelta(hours=1)
    assert loaded.device_headers == {"X-Device-Id": "dev"}


def test_needs_refresh_only_inside_the_margin(home):
    store = SessionStore()
    assert store.needs_refresh(_session(expires_at=NOW + timedelta(hours=1)), NOW) is False
    assert store.needs_refresh(_session(expires_at=NOW + timedelta(seconds=299)), NOW) is True
    assert store.needs_refresh(_session(expires_at=NOW - timedelta(seconds=1)), NOW) is True


def test_mark_dead_records_moment_and_reason(home):
    dead = SessionStore().mark_dead(_session(), NOW, reason="refresh 401")
    assert dead.dead_since == NOW
    assert dead.death_reason == "refresh 401"


def test_first_death_is_notified(home):
    dead = SessionStore().mark_dead(_session(), NOW, reason="refresh 401")
    assert SessionStore().should_notify_death(dead, NOW) is True


def test_repeat_death_is_silent_inside_cooldown(home):
    store = SessionStore()
    dead = store.note_death_notified(store.mark_dead(_session(), NOW, reason="x"), NOW)
    assert store.should_notify_death(dead, NOW + timedelta(hours=23)) is False
    assert store.should_notify_death(dead, NOW + timedelta(hours=25)) is True


def test_live_session_is_never_notified_as_dead(home):
    assert SessionStore().should_notify_death(_session(), NOW) is False


def test_session_file_is_not_world_readable(home):
    import os
    import stat

    from fitness import store as store_mod

    SessionStore().save(_session())
    mode = stat.S_IMODE(os.stat(store_mod.state_dir() / "session.json").st_mode)
    assert mode == 0o600


# --- Ревизия 3: user_id и срок жизни вычисляются из самого токена ----------


def test_user_id_is_derived_from_the_access_token_claim():
    # рассинхронизация «токен ↔ чей это токен» невозможна: id берётся из токена
    assert _session(access_token=TOKEN).user_id == "6221276709aaaaaaaaaaaaaa"


def test_user_id_is_empty_when_the_token_is_not_a_jwt():
    assert _session(access_token="not-a-jwt").user_id == ""


def test_access_token_expiry_reads_the_exp_claim():
    # поля expires_in в ответе refresh нет — срок жизни только из токена
    assert access_token_expiry(TOKEN) == NOW + timedelta(hours=23)


def test_access_token_expiry_is_none_for_unparsable_token():
    assert access_token_expiry("garbage") is None


def test_access_token_user_id_tolerates_missing_padding():
    # сегменты JWT приходят без '=' — декодер обязан дополнять паддинг сам
    token = _jwt(id="abc")
    assert "=" not in token.split(".")[1]
    assert access_token_user_id(token) == "abc"


def test_user_id_survives_save_and_load(home):
    store = SessionStore()
    store.save(_session(access_token=TOKEN))
    assert store.load().user_id == "6221276709aaaaaaaaaaaaaa"
