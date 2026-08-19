# Invictus headless-логин — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Дать Гермесу метод логина в Invictus без телефона — два POST на `entryx.io` с фейковым стабильным device-id, OTP диктует пользователь через WhatsApp, токены пишутся в `session.json`.

**Architecture:** Новый модуль `fitness/auth.py` держит device-identity инстанса (`device.json`: device-id + номер). `InvictusClient` получает два метода `request_otp()`/`login()` поверх существующего транспорта. Два агентских тула `fitness_login_request`/`fitness_login_confirm` в toolset `fitness_booking` дают Амине/Денису запускать перелогин из чата.

**Tech Stack:** Python 3.12, stdlib `urllib` (без новых зависимостей), pytest 9, существующий `FakeTransport` и `JsonStore`.

## Global Constraints

- Только stdlib `urllib` — новых зависимостей не добавлять.
- Токены и refresh наружу не логируются; `device.json` и `session.json` — режим `0o600` (обеспечивается `JsonStore.write` по умолчанию).
- Литеральные `registry.register(...)` строго на верхнем уровне модуля `tools/fitness_tool.py` (иначе автодискавери не импортирует файл — дефект №3 из отчёта о развёртывании).
- Имена полей API живут в `ENDPOINTS`/`FIELDS`, не в теле функций.
- Состояние — под `HERMES_HOME` (`state_dir()`); тесты изолируются `monkeypatch.setenv("HERMES_HOME", tmp_path)`.
- Метод НЕ вызывает `/api/refresh` и не трогает `/api/user/device-data` / `/api/bootstrap`.
- Разработка в worktree `~/.hermes/worktrees/invictus-login`, ветка `feature/invictus-headless-login`.
- Тесты запускать: `cd ~/.hermes/worktrees/invictus-login && ~/.hermes/hermes-agent/venv/bin/python -m pytest tests/fitness/ --timeout=90 -q`

---

### Task 1: `fitness/auth.py` — device-identity и номер

**Files:**
- Create: `fitness/auth.py`
- Test: `tests/fitness/test_auth.py`

**Interfaces:**
- Consumes: `fitness.store.JsonStore` (`.read(default)`, `.write(payload, mode=0o600)`), `state_dir()` через `HERMES_HOME`.
- Produces:
  - `load_or_create_device() -> dict` — `{"device_id": str, "phone_number": str | None}`, при отсутствии файла создаёт с новым UUID и `phone_number=None`, персистит `600`.
  - `set_phone_number(number: str) -> None` — сохраняет/перезаписывает номер.
  - `device_headers(device_id: str) -> dict[str, str]` — заголовки iOS-клиента.
  - `MissingPhoneNumber(RuntimeError)`, `LoginError(RuntimeError)`.
  - Константы `APP_VERSION = "4.2.1"`, `BUILD = "616"`, `USER_AGENT`.

- [ ] **Step 1: Написать падающий тест**

```python
# tests/fitness/test_auth.py
import stat

import pytest

from fitness import auth


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    return tmp_path


def test_device_file_is_created_with_uuid_and_mode_600(home):
    device = auth.load_or_create_device()
    assert len(device["device_id"]) == 36  # UUID с дефисами
    assert device["device_id"] == device["device_id"].upper()
    assert device["phone_number"] is None
    path = home / "state" / "fitness" / "device.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_device_id_is_reused_on_second_call(home):
    first = auth.load_or_create_device()["device_id"]
    second = auth.load_or_create_device()["device_id"]
    assert first == second


def test_set_phone_number_persists_and_overwrites(home):
    auth.set_phone_number("77011102626")
    assert auth.load_or_create_device()["phone_number"] == "77011102626"
    auth.set_phone_number("77770000000")
    assert auth.load_or_create_device()["phone_number"] == "77770000000"


def test_set_phone_number_keeps_device_id(home):
    device_id = auth.load_or_create_device()["device_id"]
    auth.set_phone_number("77011102626")
    assert auth.load_or_create_device()["device_id"] == device_id


def test_device_headers_has_all_required_keys(home):
    headers = auth.device_headers("F9150314-C260-4F9F-B634-14A2A61BE181")
    assert headers["x-device-id"] == "F9150314-C260-4F9F-B634-14A2A61BE181"
    assert headers["x-platform"] == "ios"
    assert headers["x-app-version"] == "4.2.1"
    assert "Invictus/616" in headers["user-agent"]
    assert "accept" in headers and "accept-language" in headers
```

- [ ] **Step 2: Прогнать — убедиться, что падает**

Run: `cd ~/.hermes/worktrees/invictus-login && ~/.hermes/hermes-agent/venv/bin/python -m pytest tests/fitness/test_auth.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'fitness.auth'`

- [ ] **Step 3: Написать минимальную реализацию**

```python
# fitness/auth.py
"""Device-identity Invictus: фейковый стабильный device-id и номер аккаунта.

device.json (state/fitness/, 600) держит идентичность ЭТОГО инстанса Гермеса.
device-id генерируется один раз и не меняется — серверная сессия Гермеса
стабильна и не пересекается с реальным телефоном пользователя. Номер вводит
пользователь и переиспользуется. У каждого инстанса (VPS/home) свой device.json.
"""

import uuid

from fitness.store import JsonStore

DEVICE_FILE = "device.json"

APP_VERSION = "4.2.1"  # x-app-version; бампить при форс-апгрейде приложения
BUILD = "616"          # build в user-agent
USER_AGENT = f"Invictus/{BUILD} CFNetwork/3860.700.1 Darwin/25.6.0"


class MissingPhoneNumber(RuntimeError):
    """В device.json нет номера, и он не передан явно."""


class LoginError(RuntimeError):
    """Сервер отверг /api/login или /api/checkSms."""


def _store() -> JsonStore:
    return JsonStore(DEVICE_FILE)


def load_or_create_device() -> dict:
    data = _store().read(default=None)
    if not data or not data.get("device_id"):
        data = {"device_id": str(uuid.uuid4()).upper(), "phone_number": None}
        _store().write(data)  # mode 0o600 по умолчанию
    return data


def set_phone_number(number: str) -> None:
    data = load_or_create_device()
    data["phone_number"] = number
    _store().write(data)


def device_headers(device_id: str) -> dict[str, str]:
    return {
        "x-device-id": device_id,
        "x-platform": "ios",
        "x-app-version": APP_VERSION,
        "user-agent": USER_AGENT,
        "accept": "application/json, text/plain, */*",
        "accept-language": "en",
    }
```

- [ ] **Step 4: Прогнать — убедиться, что проходит**

Run: `cd ~/.hermes/worktrees/invictus-login && ~/.hermes/hermes-agent/venv/bin/python -m pytest tests/fitness/test_auth.py -q`
Expected: PASS (5 passed)

- [ ] **Step 5: Коммит**

```bash
cd ~/.hermes/worktrees/invictus-login
git add fitness/auth.py tests/fitness/test_auth.py
git commit -m "feat(fitness): device-identity для headless-логина (device.json)"
```

---

### Task 2: `InvictusClient.request_otp` — запрос SMS

**Files:**
- Modify: `fitness/invictus_client.py` (ENDPOINTS, FIELDS, импорт auth, helper `_err_ru`, метод `request_otp`)
- Test: `tests/fitness/test_client.py` (добавить блок тестов логина)

**Interfaces:**
- Consumes: `fitness.auth.load_or_create_device`, `set_phone_number`, `device_headers`, `MissingPhoneNumber`, `LoginError`; `self._transport.request`; `self._base_url`.
- Produces: `InvictusClient.request_otp(phone_number: str | None = None) -> str` — возвращает использованный номер; поднимает `MissingPhoneNumber` без номера, `LoginError` на не-2xx.

- [ ] **Step 1: Написать падающий тест**

```python
# tests/fitness/test_client.py — добавить в конец файла.
# Использует уже имеющиеся FakeTransport, _client, NOW, home, MY_ID.
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
```

- [ ] **Step 2: Прогнать — убедиться, что падает**

Run: `cd ~/.hermes/worktrees/invictus-login && ~/.hermes/hermes-agent/venv/bin/python -m pytest tests/fitness/test_client.py -k request_otp -q`
Expected: FAIL — `AttributeError: 'InvictusClient' object has no attribute 'request_otp'`

- [ ] **Step 3: Написать минимальную реализацию**

В `fitness/invictus_client.py`:

1. Расширить `ENDPOINTS` (после строки `"refresh": ...`):
```python
    "login": "/api/login",         # POST — запрос SMS-кода
    "check_sms": "/api/checkSms",  # POST — подтверждение кода, выдаёт токены
```

2. Добавить в `FIELDS` (рядом с `access_token`/`refresh_token`):
```python
    "phone_number": "phoneNumber",  # тело login/checkSms
    "otp_method": "otpMethod",
    "sms_code": "smsCode",          # тело checkSms
    "language": "language",
```

3. Импорт auth (к строке `from fitness.session import ...` добавить рядом):
```python
from fitness.auth import (
    LoginError,
    MissingPhoneNumber,
    device_headers,
    load_or_create_device,
    set_phone_number,
)
```

4. Модульный helper (рядом с прочими helper-функциями, вне класса):
```python
def _err_ru(payload) -> str | None:
    """Человекочитаемое сообщение сервера: {"err": {"ru": "..."}}."""
    if isinstance(payload, dict):
        err = payload.get("err")
        if isinstance(err, dict):
            return err.get("ru")
    return None
```

5. Метод `request_otp` в классе `InvictusClient` (после публичных read-методов):
```python
    def request_otp(self, phone_number: str | None = None) -> str:
        """POST /api/login — сервер шлёт SMS с кодом на номер аккаунта.

        Телефон/прокси не нужны: device-id фейковый и стабильный (auth), SMS
        уходит на номер, код диктует пользователь (см. login()).
        """
        device = load_or_create_device()
        if phone_number:
            set_phone_number(phone_number)
            number = phone_number
        else:
            number = device.get("phone_number")
        if not number:
            raise MissingPhoneNumber("нет номера телефона аккаунта Invictus")
        url = self._base_url.rstrip("/") + ENDPOINTS["login"]
        body = {
            FIELDS["phone_number"]: number,
            FIELDS["otp_method"]: "sms",
            FIELDS["language"]: "en",
        }
        status, payload = self._transport.request(
            "POST", url, headers=device_headers(device["device_id"]), body=body
        )
        if not 200 <= status < 300:
            raise LoginError(_err_ru(payload) or f"login {status}")
        return number
```

- [ ] **Step 4: Прогнать — убедиться, что проходит**

Run: `cd ~/.hermes/worktrees/invictus-login && ~/.hermes/hermes-agent/venv/bin/python -m pytest tests/fitness/test_client.py -k request_otp -q`
Expected: PASS (4 passed)

- [ ] **Step 5: Коммит**

```bash
cd ~/.hermes/worktrees/invictus-login
git add fitness/invictus_client.py tests/fitness/test_client.py
git commit -m "feat(fitness): InvictusClient.request_otp — запрос SMS-кода"
```

---

### Task 3: `InvictusClient.login` — подтверждение кода и сохранение сессии

**Files:**
- Modify: `fitness/invictus_client.py` (метод `login`)
- Test: `tests/fitness/test_client.py`

**Interfaces:**
- Consumes: `load_or_create_device`, `device_headers`, `MissingPhoneNumber`, `LoginError`, `access_token_expiry`, `Session`, `SessionStore` (через `self._sessions`), `FALLBACK_TOKEN_TTL_HOURS`.
- Produces: `InvictusClient.login(code: str) -> Session` — сохраняет `Session` в `session.json` с чистыми dead-флагами; `MissingPhoneNumber` без номера; `LoginError` на не-2xx (session.json не трогается).

- [ ] **Step 1: Написать падающий тест**

```python
# tests/fitness/test_client.py — добавить к блоку логина.
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
```

- [ ] **Step 2: Прогнать — убедиться, что падает**

Run: `cd ~/.hermes/worktrees/invictus-login && ~/.hermes/hermes-agent/venv/bin/python -m pytest tests/fitness/test_client.py -k login -q`
Expected: FAIL — `AttributeError: ... 'login'`

- [ ] **Step 3: Написать минимальную реализацию**

Метод `login` в `InvictusClient` (рядом с `request_otp`):
```python
    def login(self, code: str) -> Session:
        """POST /api/checkSms — код из SMS в обмен на пару токенов.

        Собирает Session с фейковыми device-заголовками и чистыми dead-флагами,
        сохраняет в session.json. Не вызывает /api/refresh (не жжёт свежую пару).
        """
        device = load_or_create_device()
        number = device.get("phone_number")
        if not number:
            raise MissingPhoneNumber("нет номера телефона аккаунта Invictus")
        url = self._base_url.rstrip("/") + ENDPOINTS["check_sms"]
        headers = device_headers(device["device_id"])
        body = {FIELDS["phone_number"]: number, FIELDS["sms_code"]: code}
        status, payload = self._transport.request("POST", url, headers=headers, body=body)
        if not 200 <= status < 300:
            raise LoginError(_err_ru(payload) or f"checkSms {status}")
        access = payload[FIELDS["access_token"]]
        expires_at = access_token_expiry(access) or (
            self._now() + timedelta(hours=FALLBACK_TOKEN_TTL_HOURS)
        )
        session = Session(
            access_token=access,
            refresh_token=payload[FIELDS["refresh_token"]],
            expires_at=expires_at,
            device_headers=headers,
            captured_at=self._now(),
        )
        self._sessions.save(session)
        return session
```

- [ ] **Step 4: Прогнать — убедиться, что проходит**

Run: `cd ~/.hermes/worktrees/invictus-login && ~/.hermes/hermes-agent/venv/bin/python -m pytest tests/fitness/test_client.py -k login -q`
Expected: PASS (3 passed)

- [ ] **Step 5: Коммит**

```bash
cd ~/.hermes/worktrees/invictus-login
git add fitness/invictus_client.py tests/fitness/test_client.py
git commit -m "feat(fitness): InvictusClient.login — сессия из checkSms без /api/refresh"
```

---

### Task 4: Агентские тулы `fitness_login_request` / `fitness_login_confirm`

**Files:**
- Modify: `tools/fitness_tool.py` (импорты, две функции, два `registry.register`, `REGISTERED_NAMES`)
- Modify: `config/hermes-role-tool-map.yaml` (добавить два тула в `categories.fitness_booking.tools`)
- Test: `tests/fitness/test_tools.py`

**Interfaces:**
- Consumes: `_client().request_otp(...)`, `_client().login(...)`, `fitness.auth.LoginError`, `fitness.auth.MissingPhoneNumber`, `fitness.models.CLUB_TZ`, `fitness.session.Session`.
- Produces:
  - `fitness_login_request(phone_number: str | None = None, person_name: str | None = None) -> str`
  - `fitness_login_confirm(code: str) -> str`
  - оба зарегистрированы в toolset `fitness_booking`, попадают в `REGISTERED_NAMES`.

- [ ] **Step 1: Написать падающий тест**

```python
# tests/fitness/test_tools.py — добавить в конец файла.
from datetime import datetime, timezone

from fitness.auth import LoginError, MissingPhoneNumber


def test_login_request_asks_for_number_when_missing(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    class FakeClient:
        def request_otp(self, phone_number=None):
            raise MissingPhoneNumber("нет номера")

    monkeypatch.setattr(fitness_tool, "_client", lambda: FakeClient())
    out = fitness_tool.fitness_login_request()
    assert "номер" in out.lower()


def test_login_request_returns_masked_number(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    class FakeClient:
        def request_otp(self, phone_number=None):
            return "77011102626"

    monkeypatch.setattr(fitness_tool, "_client", lambda: FakeClient())
    out = fitness_tool.fitness_login_request(person_name="Амина")
    assert "2626" in out
    assert "Амина" in out
    assert "77011102626" not in out  # номер маскируется


def test_login_confirm_reports_success(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    from fitness.session import Session

    class FakeClient:
        def login(self, code):
            return Session(
                access_token="a", refresh_token="r",
                expires_at=datetime(2026, 8, 19, 17, 47, tzinfo=timezone.utc),
                device_headers={"x-device-id": "d"},
            )

    monkeypatch.setattr(fitness_tool, "_client", lambda: FakeClient())
    out = fitness_tool.fitness_login_confirm("9797")
    assert "активна" in out.lower()


def test_login_confirm_reports_bad_code(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    class FakeClient:
        def login(self, code):
            raise LoginError("Неверный код")

    monkeypatch.setattr(fitness_tool, "_client", lambda: FakeClient())
    out = fitness_tool.fitness_login_confirm("0000")
    assert "не подошёл" in out.lower()


def test_login_tools_are_registered():
    assert {"fitness_login_request", "fitness_login_confirm"} <= set(
        fitness_tool.REGISTERED_NAMES
    )


def test_login_tools_are_in_role_map():
    assert {"fitness_login_request", "fitness_login_confirm"} <= set(_map_tools())
```

- [ ] **Step 2: Прогнать — убедиться, что падает**

Run: `cd ~/.hermes/worktrees/invictus-login && ~/.hermes/hermes-agent/venv/bin/python -m pytest tests/fitness/test_tools.py -k login -q`
Expected: FAIL — `AttributeError: module 'tools.fitness_tool' has no attribute 'fitness_login_request'`

- [ ] **Step 3: Написать минимальную реализацию**

1. В `tools/fitness_tool.py` импорты — расширить строку импорта клиента и добавить auth/CLUB_TZ:
```python
from fitness.auth import LoginError, MissingPhoneNumber
from fitness.models import CLUB_TZ
```

2. Функции (рядом с прочими, до блока `registry.register`):
```python
def fitness_login_request(phone_number: str | None = None, person_name: str | None = None) -> str:
    """Запросить SMS-код Invictus. phone_number — только чтобы сменить номер."""
    try:
        number = _client().request_otp(phone_number)
    except MissingPhoneNumber:
        return "Нужен номер телефона аккаунта Invictus — продиктуй его."
    except LoginError as exc:
        return f"⚠️ Не удалось запросить код: {exc}"
    who = f"{person_name}, " if person_name else ""
    return f"{who}код отправлен по SMS на номер …{number[-4:]}. Продиктуй его — я введу."


def fitness_login_confirm(code: str) -> str:
    """Подтвердить код из SMS и сохранить сессию Invictus."""
    try:
        session = _client().login(code)
    except MissingPhoneNumber:
        return "Сначала запроси код: fitness_login_request."
    except LoginError as exc:
        return f"Код не подошёл ({exc}). Запроси новый через fitness_login_request."
    local = session.expires_at.astimezone(CLUB_TZ).strftime("%d.%m %H:%M")
    return f"✅ Готово. Сессия Invictus активна до {local} (клубное время)."
```

3. Два `registry.register` (после блока `fitness_watch_remove`, перед `REGISTERED_NAMES.extend`):
```python
registry.register(
    name="fitness_login_request",
    toolset=TOOLSET,
    schema=_schema(
        "fitness_login_request",
        "Запросить SMS-код для входа в Invictus. phone_number укажи только чтобы "
        "залогинить другой номер; person_name — имя для обращения в ответе",
        _obj(
            {
                "phone_number": {"type": "string", "description": "Номер аккаунта (только для смены)"},
                "person_name": {"type": "string", "description": "Имя для обращения"},
            }
        ),
    ),
    handler=lambda args, **kw: fitness_login_request(**(args or {})),
    requires_env=[],
    is_async=False,
    emoji=_EMOJI,
    max_result_size_chars=8000,
)

registry.register(
    name="fitness_login_confirm",
    toolset=TOOLSET,
    schema=_schema(
        "fitness_login_confirm",
        "Подтвердить вход в Invictus кодом из SMS (после fitness_login_request)",
        _obj({"code": {"type": "string", "description": "Код из SMS"}}, ["code"]),
    ),
    handler=lambda args, **kw: fitness_login_confirm(**(args or {})),
    requires_env=[],
    is_async=False,
    emoji=_EMOJI,
    max_result_size_chars=8000,
)
```

4. Дописать в `REGISTERED_NAMES.extend([...])` два имени:
```python
        "fitness_login_request",
        "fitness_login_confirm",
```

5. В `config/hermes-role-tool-map.yaml`, в `categories.fitness_booking.tools`, добавить:
```yaml
      - fitness_login_request
      - fitness_login_confirm
```

- [ ] **Step 4: Прогнать — убедиться, что проходит**

Run: `cd ~/.hermes/worktrees/invictus-login && ~/.hermes/hermes-agent/venv/bin/python -m pytest tests/fitness/test_tools.py -k login -q`
Expected: PASS (6 passed)

- [ ] **Step 5: Коммит**

```bash
cd ~/.hermes/worktrees/invictus-login
git add tools/fitness_tool.py tests/fitness/test_tools.py config/hermes-role-tool-map.yaml
git commit -m "feat(fitness): агентские тулы login_request/login_confirm (OTP через WhatsApp)"
```

---

### Task 5: Полный прогон и финальная проверка

**Files:** —

- [ ] **Step 1: Прогнать весь fitness-набор**

Run: `cd ~/.hermes/worktrees/invictus-login && ~/.hermes/hermes-agent/venv/bin/python -m pytest tests/fitness/ --timeout=90 -q`
Expected: PASS — все существующие (≈149) + новые тесты (5 + 4 + 3 + 6 = 18), с итоговой строкой `N passed`.

- [ ] **Step 2: Проверить, что автодискавери видит новый тулсет целиком**

Run: `cd ~/.hermes/worktrees/invictus-login && ~/.hermes/hermes-agent/venv/bin/python -c "from tools.registry import registry, discover_builtin_tools; discover_builtin_tools(); print(sorted(registry.get_tool_names_for_toolset('fitness_booking')))"`
Expected: список включает `fitness_login_request` и `fitness_login_confirm`.

- [ ] **Step 3: Отчёт**

Резюме: файлы, число тестов, что НЕ проверено вживую (живой `/api/login`/`checkSms` на реальном номере — по решению владельца, отдельным шагом развёртывания). Затем — решение о вливании в `local/customizations` (публикация через автопуш) и рестарте гейтвея — с согласия владельца.

## Порядок и зависимости

Task 1 → Task 2 → Task 3 → Task 4 → Task 5, строго последовательно: Task 2/3 импортируют `fitness.auth` из Task 1; Task 4 использует `request_otp`/`login` из Task 2/3.

## Self-review (coverage)

- Спека §«fitness/auth.py» → Task 1. §«InvictusClient» `request_otp` → Task 2, `login` → Task 3. §«tools» → Task 4. §«Обработка ошибок» (`MissingPhoneNumber`, `LoginError`, `err.ru`) → Task 1 (классы) + Task 2/3 (проброс) + Task 4 (человеческий текст). §«Поток данных» покрыт тестами Task 2–4. §«Безопасность» (режим 600, токены не логируются) → Global Constraints + Task 1. §«Тестирование» → тесты в каждой задаче + Task 5.
