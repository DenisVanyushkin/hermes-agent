from __future__ import annotations

from datetime import date

from hermes_cli.almaty_weather import (
    DailyWeather,
    build_weekly,
    connect,
    fetch_station,
    meteostat_url,
    parse_open_meteo,
    parse_yr,
    render_morning,
    save_today_snapshots,
)


def open_payload(day: str = "2026-07-12"):
    return {
        "daily": {
            "time": [day], "temperature_2m_min": [20], "temperature_2m_max": [30],
            "temperature_2m_mean": [25], "precipitation_sum": [1.2], "wind_speed_10m_mean": [2],
        },
        "hourly": {"time": [f"{day}T08:00", f"{day}T09:00"], "precipitation": [0.2, 0.0]},
    }


def yr_payload(day: str = "2026-07-12"):
    rows = []
    for hour in range(24):
        rows.append({
            "time": f"{day}T{hour:02d}:00:00+05:00",
            "data": {
                "instant": {"details": {"air_temperature": 18 + hour / 2, "wind_speed": 2}},
                "next_1_hours": {"details": {"precipitation_amount": 0.2 if hour == 8 else 0}},
            },
        })
    return {"properties": {"timeseries": rows}}


def station_payload(days):
    return {"data": [
        {"date": f"{day} 00:00:00", "tmin": 19, "tmax": 31, "tavg": 25,
         "prcp": 0, "wspd": 2.5}
        for day in days
    ]}


def test_parsers_normalize_daily_weather():
    open_row = parse_open_meteo(open_payload())[0]
    yr_row = parse_yr(yr_payload())[0]
    assert open_row.rain_start == "08:00"
    assert open_row.prcp == 1.2
    assert yr_row.target_date == "2026-07-12"
    assert yr_row.prcp == 0.2
    assert yr_row.wspd == 2.0


def test_meteostat_disables_model_fill_and_converts_wind_to_ms():
    seen = []
    def getter(url, _headers):
        seen.append(url)
        return {"data": [{
            "date": "2026-07-11 00:00:00", "tmin": 19, "tmax": 31,
            "tavg": 25, "prcp": 0, "wspd": 7.2,
        }]}
    rows = fetch_station(date(2026, 7, 11), date(2026, 7, 11), getter)
    assert rows[0].wspd == 2.0
    assert "model=false" in seen[0]
    assert "model=false" in meteostat_url(date(2026, 7, 11), date(2026, 7, 11))


def test_morning_snapshot_is_idempotent_and_matches_render(tmp_path):
    conn = connect(tmp_path / "weather.sqlite3")
    def getter(url, _headers):
        return open_payload() if "open-meteo" in url else yr_payload()
    for _ in range(2):
        collected, tomorrow, errors = save_today_snapshots(conn, today=date(2026, 7, 12), getter=getter)
    assert not errors
    assert conn.execute("SELECT COUNT(*) FROM forecast_snapshots").fetchone()[0] == 2
    rendered = render_morning(collected, tomorrow, errors, date(2026, 7, 12))
    assert "19.0…29.8°C" in rendered
    assert "yr.no и Open‑Meteo" in rendered


def test_partial_source_is_saved_and_reported(tmp_path):
    conn = connect(tmp_path / "weather.sqlite3")
    def getter(url, _headers):
        if "open-meteo" in url:
            raise OSError("offline")
        return yr_payload()
    collected, tomorrow, errors = save_today_snapshots(conn, today=date(2026, 7, 12), getter=getter)
    assert list(collected) == ["yr"]
    assert "open_meteo" in errors
    assert "Нет данных: Open‑Meteo" in render_morning(collected, tomorrow, errors, date(2026, 7, 12))


def test_weekly_cursor_stops_at_latest_station_date(tmp_path):
    conn = connect(tmp_path / "weather.sqlite3")
    for day in ("2026-07-10", "2026-07-11", "2026-07-12"):
        for source in ("yr", "open_meteo"):
            row = DailyWeather(source, day, 20, 30, 25, 1, 2)
            conn.execute(
                "INSERT INTO forecast_snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (source, day, day + "T07:00:00+05:00", row.tmin, row.tmax, row.tavg,
                 row.prcp, row.wspd, None, None, source + day),
            )
    conn.commit()
    def first_getter(_url, _headers):
        return station_payload(["2026-07-10", "2026-07-11"])
    report = build_weekly(conn, today=date(2026, 7, 12), getter=first_getter)
    assert "2026-07-10 — 2026-07-11" in report
    assert conn.execute("SELECT value FROM state").fetchone()[0] == "2026-07-11"
    def second_getter(_url, _headers):
        return station_payload(["2026-07-12"])
    report = build_weekly(conn, today=date(2026, 7, 19), getter=second_getter)
    assert "2026-07-12 — 2026-07-12" in report


def test_empty_station_does_not_advance_cursor(tmp_path):
    conn = connect(tmp_path / "weather.sqlite3")
    conn.execute(
        "INSERT INTO forecast_snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        ("yr", "2026-07-12", "now", 20, 30, 25, 0, 2, None, None, "x"),
    )
    conn.commit()
    report = build_weekly(conn, today=date(2026, 7, 12), getter=lambda *_: {"data": []})
    assert "пока не опубликовал" in report
    assert conn.execute("SELECT COUNT(*) FROM state").fetchone()[0] == 0


def test_weekly_never_accepts_current_or_future_station_rows(tmp_path):
    conn = connect(tmp_path / "weather.sqlite3")
    for day in ("2026-07-11", "2026-07-12", "2026-07-13"):
        conn.execute(
            "INSERT INTO forecast_snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            ("yr", day, "now", 20, 30, 25, 0, 2, None, None, day),
        )
    conn.commit()
    report = build_weekly(
        conn,
        today=date(2026, 7, 12),
        getter=lambda *_: station_payload(["2026-07-11", "2026-07-12", "2026-07-13"]),
    )
    assert "2026-07-11 — 2026-07-11" in report
    assert "2026-07-12:" not in report
    assert conn.execute("SELECT value FROM state").fetchone()[0] == "2026-07-11"
