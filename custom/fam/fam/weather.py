"""Open-Meteo weather fetch for Almaty. stdlib only (urllib.request/json);
no API key needed. Network access is isolated behind an injectable
_urlopen so tests never touch the real network.
"""
import json
import sys
import urllib.request

URL = (
    "https://api.open-meteo.com/v1/forecast"
    "?latitude=43.238949&longitude=76.889709"
    "&daily=temperature_2m_min,temperature_2m_max,precipitation_sum,"
    "precipitation_hours,windspeed_10m_max"
    "&timezone=Asia%2FAlmaty&forecast_days=2"
)

_DAILY_KEYS = (
    "temperature_2m_min",
    "temperature_2m_max",
    "precipitation_sum",
    "precipitation_hours",
    "windspeed_10m_max",
)


def _day(daily, i):
    return {
        "tmin": daily["temperature_2m_min"][i],
        "tmax": daily["temperature_2m_max"][i],
        "precip_mm": daily["precipitation_sum"][i],
        "precip_hours": daily["precipitation_hours"][i],
        "wind": daily["windspeed_10m_max"][i],
    }


def fetch_almaty(timeout=10, _urlopen=None):
    """Fetch today/tomorrow forecast for Almaty from Open-Meteo.

    Returns {"today": {tmin, tmax, precip_mm, precip_hours, wind},
    "tomorrow": {...}} (all floats) on success.

    On ANY failure — network error, timeout, non-200 status, malformed
    JSON, missing keys, or daily arrays shorter than 2 entries — returns
    None and writes a one-line diagnostic to stderr. Never raises.

    _urlopen is an injection point for tests; defaults to
    urllib.request.urlopen.
    """
    urlopen = _urlopen or urllib.request.urlopen

    try:
        with urlopen(URL, timeout=timeout) as resp:
            status = getattr(resp, "status", 200)
            if status != 200:
                print(f"weather.fetch_almaty: HTTP {status}", file=sys.stderr)
                return None
            body = resp.read()
    except Exception as e:
        print(f"weather.fetch_almaty: request failed: {e}", file=sys.stderr)
        return None

    try:
        data = json.loads(body)
        daily = data["daily"]
        for key in _DAILY_KEYS:
            if len(daily[key]) < 2:
                raise ValueError(f"daily.{key} has fewer than 2 entries")
        return {"today": _day(daily, 0), "tomorrow": _day(daily, 1)}
    except Exception as e:
        print(f"weather.fetch_almaty: bad response: {e}", file=sys.stderr)
        return None
