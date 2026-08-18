# Invictus — логин без телефона (headless), OTP через WhatsApp

Дата: 2026-08-18
Связанные: `docs/superpowers/specs/2026-08-10-invictus-fitness-booking-design.md`,
`docs/reports/2026-08-11-invictus-deployment.md`

## Проблема

Сессия Invictus в Гермесе — это захваченная пара `access`/`refresh` токенов в
`state/fitness/session.json`. Захват делался вручную через mitmproxy с телефона.
Refresh на стороне вендора устроен с **ротацией-инвалидацией семейства**: каждый
`/api/refresh` выпускает новый refresh-токен и убивает предыдущий. Поэтому живой
второй клиент (реальный телефон Амины) при своём обновлении убивает захваченную
пару — сессия Гермеса умирает (`refresh 401`), автозапись и утренний дайджест
встают, приходит уведомление «Сессия недействительна».

Ручной перезахват через mitmproxy — дорого и требует телефона/прокси. Нужен
способ переавторизоваться **без телефона**, прямо из Гермеса.

## Ключевой факт (захват 2026-08-18)

Аутентификация Invictus (`entryx.io`) тривиальна — два POST, без капчи,
attestation и сторонних сервисов:

1. `POST /api/login` — тело `{"phoneNumber","otpMethod":"sms","language":"en"}`,
   заголовки `x-device-id`, `x-platform: ios`, `x-app-version`, `user-agent`.
   Ответ `200 "ok"` → сервер шлёт SMS на номер.
2. `POST /api/checkSms` — тело `{"phoneNumber","smsCode"}`, те же заголовки.
   Ответ `200 {"accessToken","refreshToken","refreshExpiresIn":2591999,...}`.

`x-device-id` — **произвольный UUID, выбираемый клиентом** (iOS-формат
`XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX`), не аппаратный; сервер выводит из него
`dvh` в токене. Значит логиниться можно с фейковым стабильным device-id прямо с
Гермеса — SMS всё равно уходит на номер аккаунта, нужен только код из SMS.

`POST /api/user/device-data` и `GET /api/bootstrap` после логина — телеметрия и
лента, к авторизации и к fitness-операциям отношения не имеют.

## Цель

Метод логина без телефона, переиспользуемый в **обоих инстансах** Гермеса
(VPS/аккаунт Денис, home/аккаунт Амина): фейковый стабильный device-id на инстанс,
`/api/login` запрашивает SMS, пользователь (Амина/Денис) диктует OTP через
WhatsApp, `/api/checkSms` даёт токены, которые пишутся в `session.json`.

Ничего специфичного для аккаунта в коде не хардкодится — номер вводит
пользователь и хранится в стейте; имя берётся из контекста диалога.

## Компоненты

Три слоя, каждый тестируется независимо через fake-транспорт (как остальные
тесты `fitness/`).

### `fitness/auth.py` (новый) — device-identity и номер

Персистентный файл `state/fitness/device.json`, режим `600`:

```json
{ "device_id": "F9150314-C260-4F9F-B634-14A2A61BE181", "phone_number": "77011102626" }
```

- `load_or_create_device()` → читает `device.json`; если файла нет — генерирует
  `uuid4()` в верхнем регистре, сохраняет `600`, возвращает состояние.
  `phone_number` при первом создании отсутствует (пусто).
- `device_headers(device_id)` → собирает заголовки: `x-device-id=device_id`,
  плюс константы модуля `x-platform: ios`, `x-app-version`, `user-agent`,
  `accept`, `accept-language`.
- `set_phone_number(number)` → сохраняет/перезаписывает номер в `device.json`.

device-id генерируется один раз и **не меняется** при смене номера — идентичность
устройства стабильна, меняется только аккаунт. Каждый инстанс Гермеса имеет свой
`device.json` → свою device-identity → свою серверную сессию (VPS и home не
пересекаются).

Константы версии приложения (`x-app-version: 4.2.1`, build в user-agent `616`) —
на уровне модуля, с комментарием «бампить при форс-апгрейде приложения». Они не
привязаны к аккаунту, общие для платформы iOS.

### `InvictusClient` — два новых публичных метода

Поверх существующего транспорта (`_transport.request(method, url, *, headers, body)`
→ `(status, payload)`); `/api/login` и `/api/checkSms` добавляются в `ENDPOINTS`.

- `request_otp(phone_number: str | None = None) -> str`
  - `phone_number` передан → «залогинить другой номер»: `set_phone_number(...)`.
  - не передан → берём из `device.json`; если пусто → `MissingPhoneNumber`.
  - `POST /api/login` с device-заголовками и телом
    `{"phoneNumber": number, "otpMethod": "sms", "language": "en"}`.
  - не-2xx → `LoginError(err.ru сервера)`.
  - возвращает использованный номер (для текста ответа).
- `login(code: str) -> Session`
  - читает номер из `device.json` (тот, что использовал `request_otp`); пусто →
    `MissingPhoneNumber`.
  - `POST /api/checkSms` с телом `{"phoneNumber": number, "smsCode": code}`.
  - 4xx → `LoginError` («код не подошёл»); success → собирает `Session`:
    `access_token`, `refresh_token`, `expires_at = access_token_expiry(access)`,
    `device_headers` = фейковые заголовки, `captured_at = now`, все dead-флаги
    (`dead_since`, `death_reason`, `last_death_notice_at`) = `None`.
    Сохраняет через `SessionStore.save(...)`.

Метод НЕ вызывает `/api/refresh` (не жжёт свежую пару ротацией) и не трогает
`/api/user/device-data` / `/api/bootstrap`.

### `tools/fitness_tool.py` — два агентских тула

Toolset `fitness_booking`, регистрация **литеральными** `registry.register(...)`
на верхнем уровне (иначе автодискавери не импортирует модуль — см. отчёт о
развёртывании, дефект №3).

- `fitness_login_request(phone_number: str | None = None, person_name: str | None = None) -> str`
  - зовёт `request_otp(phone_number)`; возвращает человеку: «Код отправлен
    {person_name|«»} по SMS на номер …NNNN. Продиктуй его — я введу.»
  - `person_name` — только для текста, из контекста диалога; в API не уходит.
  - нет номера → «Продиктуй номер телефона аккаунта Invictus».
- `fitness_login_confirm(code: str) -> str`
  - зовёт `login(code)`; успех → «Готово. Сессия активна до <локальное время>.»
  - ошибка кода → «Код не подошёл, запроси новый (…request).»

## Поток данных

```
Пользователь: «перелогинься в инвиктус»
 → fitness_login_request()          device.json.phone → POST /api/login → "ok"
 → агент: «код отправлен, продиктуй»
 → пользователь: «9797»
 → fitness_login_confirm("9797")    POST /api/checkSms → {accessToken, refreshToken}
                                    → Session → session.json (dead-флаги сброшены)
 → агент: «готово, сессия жива до …»
```

Смена номера: `fitness_login_request("77XXXXXXXXX")` явным аргументом →
перезаписывает `phone_number` в `device.json`, дальше как обычно.

## Обработка ошибок

Оба тула ловят исключения и возвращают человекочитаемый текст, не роняя агента:

| Ситуация | Реакция |
|---|---|
| нет номера в `device.json`, не передан | «продиктуй номер» |
| `/api/login` не-2xx | отдать `err.ru` сервера (форс-апгрейд версии, неизвестный номер) |
| `/api/checkSms` 4xx (неверный/просроченный код) | «код не подошёл, запроси новый» |
| сеть недоступна | «сеть недоступна, попробуй ещё раз» |

## Тестирование (TDD)

Все — через fake-транспорт, без сети, как существующие `tests/fitness/`.

- `auth`: `device.json` создаётся с валидным UUID и режимом `600`; повторный
  вызов переиспользует device-id; `set_phone_number` сохраняет/перезаписывает
  номер; `device_headers` содержит все обязательные ключи.
- `request_otp`: правильные URL/заголовки/тело; `MissingPhoneNumber` без номера;
  `LoginError` с `err.ru` на не-2xx; явный номер перезаписывает стейт.
- `login`: парсит токены; `expires_at` из claim `exp`; `Session` с фейковыми
  device_headers и чистыми dead-флагами сохранён; на 4xx — `LoginError`,
  `session.json` не изменён.
- тулы: happy-path; путь «нет номера»; ошибка кода; регистрация тулов в реестре
  (путь автодискавери, как в отчёте).

## Безопасность / ограничения

- Тулы доступны только на платформе `whatsapp` (и `cli`), а канал заперт на
  `WHATSAPP_ALLOWED_USERS` (Амина, Денис). Сторонний триггер в худшем случае
  шлёт SMS на номер аккаунта — без кода сессию не получить.
- Логин под другим номером логинит в аккаунт этого номера — это осознанное
  действие пользователя (явный аргумент), поведение по замыслу.
- Токены наружу не логируются (`InvictusClient.__repr__` уже это соблюдает);
  `device.json` и `session.json` — режим `600`.

## Что НЕ входит (YAGNI)

- Автоматический перелогин при смерти сессии — остаётся ручной триггер.
- CLI-обёртка для оператора — ядро на клиенте позволяет добавить позже одной
  функцией, сейчас не нужно.
- Хранение истории номеров/несколько аккаунтов на инстанс — один активный
  номер в `device.json`.
