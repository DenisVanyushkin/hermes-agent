import json
import socket
import urllib.error

from fam import weather

FIXTURE = {
    "daily": {
        "time": ["2026-07-11", "2026-07-12"],
        "temperature_2m_min": [18.5, 17.2],
        "temperature_2m_max": [29.3, 27.8],
        "precipitation_sum": [0.0, 1.2],
        "precipitation_hours": [0.0, 2.0],
        "windspeed_10m_max": [12.4, 15.6],
    }
}


class FakeResponse:
    """Minimal stand-in for the object urllib.request.urlopen() returns
    when used as a context manager."""

    def __init__(self, body, status=200):
        self._body = body
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self._body


def _urlopen_returning(payload, status=200):
    body = json.dumps(payload).encode("utf-8")

    def fake(url, timeout=10):
        assert "api.open-meteo.com/v1/forecast" in url
        assert "latitude=43.238949" in url
        assert "longitude=76.889709" in url
        return FakeResponse(body, status=status)

    return fake


# ---- success ----

def test_fetch_almaty_parses_fixture():
    result = weather.fetch_almaty(_urlopen=_urlopen_returning(FIXTURE))
    assert result == {
        "today": {
            "tmin": 18.5, "tmax": 29.3,
            "precip_mm": 0.0, "precip_hours": 0.0, "wind": 12.4,
        },
        "tomorrow": {
            "tmin": 17.2, "tmax": 27.8,
            "precip_mm": 1.2, "precip_hours": 2.0, "wind": 15.6,
        },
    }


# ---- network failures ----

def test_fetch_almaty_urlerror_returns_none(capsys):
    def fake(url, timeout=10):
        raise urllib.error.URLError("network down")

    assert weather.fetch_almaty(_urlopen=fake) is None
    err = capsys.readouterr().err
    assert err.strip() != ""


def test_fetch_almaty_timeout_returns_none(capsys):
    def fake(url, timeout=10):
        raise socket.timeout("timed out")

    assert weather.fetch_almaty(_urlopen=fake) is None
    assert capsys.readouterr().err.strip() != ""


def test_fetch_almaty_http_error_returns_none(capsys):
    def fake(url, timeout=10):
        raise urllib.error.HTTPError(url, 500, "Internal Server Error", None, None)

    assert weather.fetch_almaty(_urlopen=fake) is None
    assert capsys.readouterr().err.strip() != ""


def test_fetch_almaty_non_200_status_returns_none(capsys):
    assert weather.fetch_almaty(_urlopen=_urlopen_returning(FIXTURE, status=204)) is None
    assert capsys.readouterr().err.strip() != ""


# ---- bad payloads ----

def test_fetch_almaty_malformed_json_returns_none(capsys):
    def fake(url, timeout=10):
        return FakeResponse(b"not json at all {{{")

    assert weather.fetch_almaty(_urlopen=fake) is None
    assert capsys.readouterr().err.strip() != ""


def test_fetch_almaty_missing_daily_key_returns_none(capsys):
    def fake(url, timeout=10):
        return FakeResponse(json.dumps({"latitude": 43.2}).encode())

    assert weather.fetch_almaty(_urlopen=fake) is None
    assert capsys.readouterr().err.strip() != ""


def test_fetch_almaty_short_daily_arrays_returns_none(capsys):
    short = json.loads(json.dumps(FIXTURE))
    for key in short["daily"]:
        short["daily"][key] = short["daily"][key][:1]

    assert weather.fetch_almaty(_urlopen=_urlopen_returning(short)) is None
    assert capsys.readouterr().err.strip() != ""


# ---- timeout is forwarded ----

def test_fetch_almaty_forwards_timeout():
    seen = {}

    def fake(url, timeout=10):
        seen["timeout"] = timeout
        return FakeResponse(json.dumps(FIXTURE).encode())

    weather.fetch_almaty(timeout=3, _urlopen=fake)
    assert seen["timeout"] == 3
