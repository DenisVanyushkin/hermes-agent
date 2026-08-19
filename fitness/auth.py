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
