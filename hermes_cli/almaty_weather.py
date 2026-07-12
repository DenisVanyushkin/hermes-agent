from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from statistics import mean
from typing import Any, Callable, Iterable
from zoneinfo import ZoneInfo


ALMATY_TZ = ZoneInfo("Asia/Almaty")
LAT = 43.238949
LON = 76.889709
STATION = "36870"
SOURCES = ("yr", "open_meteo")
RAIN_THRESHOLD_MM = 0.1
JsonGetter = Callable[[str, dict[str, str] | None], dict[str, Any]]


@dataclass(frozen=True)
class DailyWeather:
    source: str
    target_date: str
    tmin: float | None
    tmax: float | None
    tavg: float | None
    prcp: float | None
    wspd: float | None
    rain_start: str | None = None
    rain_end: str | None = None


def _round(value: Any, digits: int = 2) -> float | None:
    if value is None:
        return None
    return round(float(value), digits)


def default_db_path() -> Path:
    home = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
    return home / "weather" / "weather.sqlite3"


def connect(path: Path | None = None) -> sqlite3.Connection:
    db_path = path or default_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS forecast_snapshots (
            source TEXT NOT NULL,
            target_date TEXT NOT NULL,
            issued_at TEXT NOT NULL,
            tmin REAL,
            tmax REAL,
            tavg REAL,
            prcp REAL,
            wspd REAL,
            rain_start TEXT,
            rain_end TEXT,
            raw_sha256 TEXT NOT NULL,
            PRIMARY KEY (source, target_date)
        );
        CREATE TABLE IF NOT EXISTS weekly_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            generated_at TEXT NOT NULL,
            period_start TEXT,
            period_end TEXT,
            station_last_date TEXT,
            report_sha256 TEXT NOT NULL UNIQUE
        );
        CREATE TABLE IF NOT EXISTS state (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        """
    )
    return conn


def fetch_json(url: str, headers: dict[str, str] | None = None) -> dict[str, Any]:
    merged = {
        "User-Agent": "HermesAlmatyWeather/1.0 (scheduled personal forecast)",
        "Accept": "application/json, text/plain, */*",
    }
    merged.update(headers or {})
    request = urllib.request.Request(url, headers=merged)
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.load(response)
    if not isinstance(payload, dict):
        raise ValueError("weather endpoint returned a non-object JSON payload")
    return payload


def open_meteo_url() -> str:
    params = {
        "latitude": LAT,
        "longitude": LON,
        "daily": "temperature_2m_min,temperature_2m_max,temperature_2m_mean,precipitation_sum,wind_speed_10m_mean",
        "hourly": "precipitation",
        "timezone": "Asia/Almaty",
        "forecast_days": 2,
        "wind_speed_unit": "ms",
    }
    return "https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode(params)


def parse_open_meteo(payload: dict[str, Any]) -> list[DailyWeather]:
    daily = payload.get("daily") or {}
    hourly = payload.get("hourly") or {}
    dates = list(daily.get("time") or [])
    result: list[DailyWeather] = []
    for index, target in enumerate(dates):
        wet = [
            stamp[11:16]
            for stamp, amount in zip(hourly.get("time") or [], hourly.get("precipitation") or [])
            if stamp[:10] == target and amount is not None and float(amount) >= RAIN_THRESHOLD_MM
        ]
        def at(name: str) -> Any:
            values = daily.get(name) or []
            return values[index] if index < len(values) else None
        result.append(DailyWeather(
            source="open_meteo",
            target_date=target,
            tmin=_round(at("temperature_2m_min")),
            tmax=_round(at("temperature_2m_max")),
            tavg=_round(at("temperature_2m_mean")),
            prcp=_round(at("precipitation_sum")),
            wspd=_round(at("wind_speed_10m_mean")),
            rain_start=wet[0] if wet else None,
            rain_end=wet[-1] if wet else None,
        ))
    return result


def yr_url() -> str:
    return f"https://api.met.no/weatherapi/locationforecast/2.0/compact?lat={LAT}&lon={LON}"


def parse_yr(payload: dict[str, Any]) -> list[DailyWeather]:
    buckets: dict[str, dict[str, list[float] | list[str]]] = {}
    for item in ((payload.get("properties") or {}).get("timeseries") or []):
        stamp = str(item.get("time") or "")
        if not stamp:
            continue
        local = datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone(ALMATY_TZ)
        key = local.date().isoformat()
        bucket = buckets.setdefault(key, {"temp": [], "wind": [], "prcp": [], "wet": []})
        data = item.get("data") or {}
        details = ((data.get("instant") or {}).get("details") or {})
        if details.get("air_temperature") is not None:
            bucket["temp"].append(float(details["air_temperature"]))
        if details.get("wind_speed") is not None:
            bucket["wind"].append(float(details["wind_speed"]))
        amount = ((data.get("next_1_hours") or {}).get("details") or {}).get("precipitation_amount")
        if amount is not None:
            bucket["prcp"].append(float(amount))
            if float(amount) >= RAIN_THRESHOLD_MM:
                bucket["wet"].append(local.strftime("%H:%M"))
    result: list[DailyWeather] = []
    for target in sorted(buckets):
        bucket = buckets[target]
        temps = list(bucket["temp"])
        winds = list(bucket["wind"])
        prcp = list(bucket["prcp"])
        wet = list(bucket["wet"])
        if len(temps) < 12:
            continue
        result.append(DailyWeather(
            source="yr",
            target_date=target,
            tmin=_round(min(temps)),
            tmax=_round(max(temps)),
            tavg=_round(mean(temps)),
            prcp=_round(sum(prcp)) if prcp else None,
            wspd=_round(mean(winds)) if winds else None,
            rain_start=wet[0] if wet else None,
            rain_end=wet[-1] if wet else None,
        ))
    return result


def _sha(payload: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def save_today_snapshots(
    conn: sqlite3.Connection,
    *,
    today: date,
    getter: JsonGetter = fetch_json,
) -> tuple[dict[str, DailyWeather], dict[str, DailyWeather], dict[str, str]]:
    collected: dict[str, DailyWeather] = {}
    tomorrow: dict[str, DailyWeather] = {}
    errors: dict[str, str] = {}
    issued_at = datetime.now(ALMATY_TZ).isoformat()
    for source, url, parser in (
        ("yr", yr_url(), parse_yr),
        ("open_meteo", open_meteo_url(), parse_open_meteo),
    ):
        try:
            payload = getter(url, None)
            parsed = parser(payload)
            match = next((row for row in parsed if row.target_date == today.isoformat()), None)
            if match is None:
                raise ValueError(f"source has no complete forecast for {today.isoformat()}")
            collected[source] = match
            next_date = (today + timedelta(days=1)).isoformat()
            next_match = next((row for row in parsed if row.target_date == next_date), None)
            if next_match is not None:
                tomorrow[source] = next_match
            values = asdict(match)
            conn.execute(
                """INSERT INTO forecast_snapshots
                (source,target_date,issued_at,tmin,tmax,tavg,prcp,wspd,rain_start,rain_end,raw_sha256)
                VALUES (:source,:target_date,:issued_at,:tmin,:tmax,:tavg,:prcp,:wspd,:rain_start,:rain_end,:raw_sha256)
                ON CONFLICT(source,target_date) DO UPDATE SET
                  issued_at=excluded.issued_at,tmin=excluded.tmin,tmax=excluded.tmax,
                  tavg=excluded.tavg,prcp=excluded.prcp,wspd=excluded.wspd,
                  rain_start=excluded.rain_start,rain_end=excluded.rain_end,
                  raw_sha256=excluded.raw_sha256""",
                {**values, "issued_at": issued_at, "raw_sha256": _sha(payload)},
            )
        except Exception as exc:  # source isolation is intentional
            errors[source] = f"{type(exc).__name__}: {exc}"
    conn.commit()
    return collected, tomorrow, errors


def _fmt(value: float | None, unit: str = "") -> str:
    return "н/д" if value is None else f"{value:.1f}{unit}"


def render_morning(
    collected: dict[str, DailyWeather],
    tomorrow: dict[str, DailyWeather],
    errors: dict[str, str],
    today: date,
) -> str:
    rows = [collected[source] for source in SOURCES if source in collected]
    if not rows:
        raise RuntimeError("оба источника прогноза недоступны: " + "; ".join(errors.values()))
    def avg(field: str) -> float | None:
        values = [getattr(row, field) for row in rows if getattr(row, field) is not None]
        return _round(mean(values), 1) if values else None
    wet = [row for row in rows if (row.prcp or 0) >= RAIN_THRESHOLD_MM]
    rain_text = "без осадков"
    if wet:
        starts = [row.rain_start for row in wet if row.rain_start]
        ends = [row.rain_end for row in wet if row.rain_end]
        window = f", примерно {min(starts)}–{max(ends)}" if starts and ends else ""
        rain_text = f"{_fmt(avg('prcp'), ' мм')}{window}"
    labels = {"yr": "yr.no", "open_meteo": "Open‑Meteo"}
    missing = [labels[source] for source in SOURCES if source not in collected]
    source_line = " и ".join(labels[source] for source in SOURCES if source in collected)
    text = [
        f"## Алматы — погода на {today.isoformat()}", "",
        "**Сегодня**",
        f"- 🌡 Температура: {_fmt(avg('tmin'))}…{_fmt(avg('tmax'), '°C')}, средняя {_fmt(avg('tavg'), '°C')}",
        f"- 🌧 Осадки: {rain_text}",
        f"- 💨 Средний ветер: {_fmt(avg('wspd'), ' м/с')}", "",
    ]
    tomorrow_rows = [tomorrow[source] for source in SOURCES if source in tomorrow]
    if tomorrow_rows:
        def tomorrow_avg(field: str) -> float | None:
            values = [getattr(row, field) for row in tomorrow_rows if getattr(row, field) is not None]
            return _round(mean(values), 1) if values else None
        wet_tomorrow = [row for row in tomorrow_rows if (row.prcp or 0) >= RAIN_THRESHOLD_MM]
        tomorrow_rain = "без осадков"
        if wet_tomorrow:
            tomorrow_rain = _fmt(tomorrow_avg("prcp"), " мм")
        text.extend([
            "**Завтра**",
            f"- 🌡 Температура: {_fmt(tomorrow_avg('tmin'))}…{_fmt(tomorrow_avg('tmax'), '°C')}, средняя {_fmt(tomorrow_avg('tavg'), '°C')}",
            f"- 🌧 Осадки: {tomorrow_rain}",
            f"- 💨 Средний ветер: {_fmt(tomorrow_avg('wspd'), ' м/с')}", "",
        ])
    text.extend([
        "**Источники**",
        f"- Использованы: {source_line}",
    ])
    if len(rows) == 2:
        text.append(
            "- Расхождение yr.no / Open‑Meteo: "
            f"tmin {abs((rows[0].tmin or 0)-(rows[1].tmin or 0)):.1f}°C; "
            f"tmax {abs((rows[0].tmax or 0)-(rows[1].tmax or 0)):.1f}°C; "
            f"осадки {abs((rows[0].prcp or 0)-(rows[1].prcp or 0)):.1f} мм"
        )
    if missing:
        text.extend(["", f"⚠️ Нет данных: {', '.join(missing)}."])
    return "\n".join(text)


def meteostat_url(start: date, end: date) -> str:
    params = {
        "station": STATION,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "model": "false",
    }
    return "https://d.meteostat.net/app/proxy/stations/daily?" + urllib.parse.urlencode(params)


def fetch_station(start: date, end: date, getter: JsonGetter = fetch_json) -> list[DailyWeather]:
    headers = {
        "Referer": f"https://meteostat.net/en/station/{STATION}",
        "Origin": "https://meteostat.net",
        "X-Requested-With": "XMLHttpRequest",
    }
    payload = getter(meteostat_url(start, end), headers)
    result = []
    for row in payload.get("data") or []:
        target = str(row.get("date") or "")[:10]
        if target:
            result.append(DailyWeather(
                source=f"meteostat:{STATION}", target_date=target,
                tmin=_round(row.get("tmin")), tmax=_round(row.get("tmax")),
                tavg=_round(row.get("tavg")), prcp=_round(row.get("prcp")),
                # Meteostat's default metric unit for wspd is km/h. Forecast
                # snapshots use m/s, so normalize observations before scoring.
                wspd=_round(float(row["wspd"]) / 3.6) if row.get("wspd") is not None else None,
            ))
    return sorted(result, key=lambda row: row.target_date)


def _state(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
    return str(row[0]) if row else None


def _snapshots(conn: sqlite3.Connection, start: date, end: date) -> dict[tuple[str, str], DailyWeather]:
    rows = conn.execute(
        "SELECT * FROM forecast_snapshots WHERE target_date BETWEEN ? AND ? ORDER BY target_date, source",
        (start.isoformat(), end.isoformat()),
    ).fetchall()
    return {(row["source"], row["target_date"]): DailyWeather(
        source=row["source"], target_date=row["target_date"], tmin=row["tmin"],
        tmax=row["tmax"], tavg=row["tavg"], prcp=row["prcp"], wspd=row["wspd"],
        rain_start=row["rain_start"], rain_end=row["rain_end"],
    ) for row in rows}


def _mae(pairs: Iterable[tuple[DailyWeather, DailyWeather]], field: str) -> float | None:
    errors = [abs(getattr(forecast, field) - getattr(actual, field)) for forecast, actual in pairs
              if getattr(forecast, field) is not None and getattr(actual, field) is not None]
    return _round(mean(errors), 1) if errors else None


def build_weekly(
    conn: sqlite3.Connection,
    *,
    today: date,
    getter: JsonGetter = fetch_json,
    persist: bool = True,
) -> str:
    first = conn.execute("SELECT MIN(target_date) FROM forecast_snapshots").fetchone()[0]
    if not first:
        return "## Качество прогноза Алматы\n\nНовых сохранённых утренних прогнозов пока нет."
    cursor = _state(conn, "last_reported_station_date")
    start = date.fromisoformat(cursor) + timedelta(days=1) if cursor else date.fromisoformat(first)
    latest_possible_observation = today - timedelta(days=1)
    if start > latest_possible_observation:
        return f"## Качество прогноза Алматы\n\nMeteostat 36870 пока не опубликовал новые завершённые сутки начиная с {start.isoformat()}."
    station = fetch_station(start, latest_possible_observation, getter)
    station = [row for row in station if date.fromisoformat(row.target_date) <= latest_possible_observation]
    if not station:
        return f"## Качество прогноза Алматы\n\nMeteostat 36870 пока не опубликовал новые данные начиная с {start.isoformat()}."
    end = date.fromisoformat(station[-1].target_date)
    snapshots = _snapshots(conn, start, end)
    actuals = {row.target_date: row for row in station}
    by_source: dict[str, list[tuple[DailyWeather, DailyWeather]]] = {source: [] for source in SOURCES}
    daily_lines: list[str] = []
    missing: list[str] = []
    day = start
    while day <= end:
        key = day.isoformat()
        actual = actuals.get(key)
        present = []
        if actual:
            for source in SOURCES:
                forecast = snapshots.get((source, key))
                if forecast:
                    by_source[source].append((forecast, actual))
                    present.append(source)
        if actual and present:
            daily_lines.append(f"- {key}: " + ", ".join(
                f"{source} Δtavg {_fmt(abs((snapshots[(source,key)].tavg or 0)-(actual.tavg or 0)), '°C')}, "
                f"Δосадки {_fmt(abs((snapshots[(source,key)].prcp or 0)-(actual.prcp or 0)), ' мм')}"
                for source in present
            ))
        else:
            missing.append(key)
        day += timedelta(days=1)
    labels = {"yr": "yr.no", "open_meteo": "Open‑Meteo"}
    sections = [f"## Качество прогноза Алматы: {start.isoformat()} — {end.isoformat()}", ""]
    source_metrics: dict[str, dict[str, float]] = {}
    for source in SOURCES:
        pairs = by_source[source]
        sections.extend([f"**{labels[source]}**", f"- Покрытие: {len(pairs)} дней"])
        for field, label, unit in (
            ("tmin", "MAE tmin", "°C"), ("tmax", "MAE tmax", "°C"),
            ("tavg", "MAE tavg", "°C"), ("prcp", "MAE осадков", " мм"),
            ("wspd", "MAE ветра", " м/с"),
        ):
            value = _mae(pairs, field)
            sections.append(f"- {label}: {_fmt(value, unit)}")
        hit = miss = false_alarm = 0
        for forecast, actual in pairs:
            predicted = (forecast.prcp or 0) >= RAIN_THRESHOLD_MM
            observed = (actual.prcp or 0) >= RAIN_THRESHOLD_MM
            hit += int(predicted and observed)
            miss += int(not predicted and observed)
            false_alarm += int(predicted and not observed)
        sections.extend([f"- Дождь: hit {hit}, miss {miss}, false alarm {false_alarm}", ""])
        if len(pairs) >= 4:
            source_metrics[source] = {
                field: value
                for field in ("tmin", "tmax", "tavg", "prcp", "wspd")
                if (value := _mae(pairs, field)) is not None
            }
    if len(source_metrics) == 2:
        scores = {source: 0.0 for source in SOURCES}
        common_fields = set(source_metrics["yr"]) & set(source_metrics["open_meteo"])
        for field in common_fields:
            scale = max(source_metrics[source][field] for source in SOURCES) or 1.0
            for source in SOURCES:
                scores[source] += source_metrics[source][field] / scale
        winner = min(scores, key=scores.get)
        sections.extend([f"**Итог:** на этом периоде точнее {labels[winner]} по сумме нормализованных доступных ошибок.", ""])
    sections.extend(["**По дням**", *daily_lines])
    if missing:
        sections.extend(["", "**Без сопоставления**", "- " + ", ".join(missing)])
    report = "\n".join(sections)
    if persist:
        digest = hashlib.sha256(report.encode()).hexdigest()
        conn.execute(
            "INSERT OR IGNORE INTO weekly_reports (generated_at,period_start,period_end,station_last_date,report_sha256) VALUES (?,?,?,?,?)",
            (datetime.now(ALMATY_TZ).isoformat(), start.isoformat(), end.isoformat(), end.isoformat(), digest),
        )
        conn.execute(
            "INSERT INTO state(key,value) VALUES('last_reported_station_date',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (end.isoformat(),),
        )
        conn.commit()
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("morning", "weekly", "weekly-preview"))
    parser.add_argument("--db", type=Path)
    parser.add_argument("--date", type=date.fromisoformat)
    args = parser.parse_args(argv)
    today = args.date or datetime.now(ALMATY_TZ).date()
    conn = connect(args.db)
    if args.command == "morning":
        collected, tomorrow, errors = save_today_snapshots(conn, today=today)
        print(render_morning(collected, tomorrow, errors, today))
    else:
        print(build_weekly(conn, today=today, persist=args.command == "weekly"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
