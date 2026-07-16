"""Calendar grid rendering: day/week/month PNG views via Pillow.

Pillow (PIL) is imported lazily inside the rendering functions so the rest
of fam (core CRUD, non-grid CLI commands) keeps working in environments
where Pillow isn't installed.

Month/week layout: 7 columns (Mon..Sun, Almaty week start), one row per
week. Each cell shows the day number and up to MAX_EVENTS events (local
HH:MM + truncated title); events beyond that are summarized as "+N ещё".
Today's cell (Asia/Almaty "today") is highlighted.

Day layout: a single column of hourly agenda rows from DAY_HOUR_START to
DAY_HOUR_END (local Asia/Almaty); each event is shown at its local start
hour as "HH:MM–HH:MM title" (see render_day).

Only ACTIVE events are shown in any view (cal.day() already filters
status="active").
"""
import calendar
import functools
import os
import sys
from datetime import date, datetime, timedelta

from fam import cal

FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"

DOW_NAMES = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
MONTH_NAMES = [
    "", "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
    "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь",
]
# Genitive case ("11 июля 2026"), used only for the day-view header —
# month/week headers above use the nominative MONTH_NAMES.
MONTH_NAMES_GENITIVE = [
    "", "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
]
WEEKDAY_NAMES = [
    "Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье",
]

CELL_W = 180
CELL_H = 130
MARGIN = 20
HEADER_H = 50
DOW_H = 32
MAX_EVENTS = 3

# Day-view layout: a single column of hourly agenda rows, 08:00..22:00.
DAY_WIDTH = 640
DAY_ROW_H = 60
DAY_HOUR_LABEL_W = 70
DAY_HOUR_START = 8
DAY_HOUR_END = 22  # exclusive; last row covers 21:00-22:00
DAY_MAX_EVENTS_PER_ROW = 3

COLOR_BG = "white"
COLOR_GRID = (200, 200, 200)
COLOR_TEXT = (30, 30, 30)
COLOR_MUTED = (150, 150, 150)
COLOR_TODAY_BG = (255, 244, 200)
COLOR_EVENT = (20, 90, 160)
COLOR_HEADER = (20, 20, 20)


@functools.lru_cache(maxsize=None)
def _load_font(size):
    from PIL import ImageFont
    try:
        return ImageFont.truetype(FONT_PATH, size)
    except OSError:
        print(
            f"grid: font not found at {FONT_PATH!r}, falling back to "
            "PIL's built-in default font (fixed size, Cyrillic rendering "
            "may degrade)",
            file=sys.stderr,
        )
        return ImageFont.load_default()


def _fit_text(draw, text, font, max_width):
    """Truncate text with an ellipsis so its rendered width fits max_width
    pixels (measured with the actual font, so it works for proportional
    and non-Latin text alike).
    """
    if draw.textlength(text, font=font) <= max_width:
        return text
    ellipsis = "…"
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        candidate = text[:mid].rstrip() + ellipsis
        if draw.textlength(candidate, font=font) <= max_width:
            lo = mid
        else:
            hi = mid - 1
    return (text[:lo].rstrip() + ellipsis) if lo else ellipsis


def _today_almaty():
    return datetime.now(cal.ALMATY).date()


def _collect_events(conn, start_date, end_date):
    """Map each date in [start_date, end_date] to its active events."""
    events_by_day = {}
    d = start_date
    while d <= end_date:
        events_by_day[d] = cal.day(conn, d.isoformat())
        d += timedelta(days=1)
    return events_by_day


def _build_image(title_text, week_dates, today, events_by_day):
    from PIL import Image, ImageDraw

    n_weeks = len(week_dates)
    width = MARGIN * 2 + CELL_W * 7
    height = MARGIN * 2 + HEADER_H + DOW_H + CELL_H * n_weeks

    img = Image.new("RGB", (width, height), COLOR_BG)
    draw = ImageDraw.Draw(img)

    title_font = _load_font(28)
    dow_font = _load_font(16)
    day_font = _load_font(16)
    event_font = _load_font(13)
    more_font = _load_font(11)

    tw = draw.textlength(title_text, font=title_font)
    draw.text(((width - tw) / 2, MARGIN), title_text, font=title_font, fill=COLOR_HEADER)

    dow_y = MARGIN + HEADER_H
    for col, name in enumerate(DOW_NAMES):
        x = MARGIN + col * CELL_W
        tw = draw.textlength(name, font=dow_font)
        draw.text((x + (CELL_W - tw) / 2, dow_y + 8), name, font=dow_font, fill=COLOR_HEADER)

    grid_top = dow_y + DOW_H
    grid_bottom = grid_top + CELL_H * n_weeks
    for col in range(8):
        x = MARGIN + col * CELL_W
        draw.line([(x, grid_top), (x, grid_bottom)], fill=COLOR_GRID)
    for row in range(n_weeks + 1):
        y = grid_top + row * CELL_H
        draw.line([(MARGIN, y), (MARGIN + CELL_W * 7, y)], fill=COLOR_GRID)

    for row, week in enumerate(week_dates):
        for col, d in enumerate(week):
            if d is None:
                continue
            x0 = MARGIN + col * CELL_W
            y0 = grid_top + row * CELL_H

            if d == today:
                draw.rectangle(
                    [x0 + 1, y0 + 1, x0 + CELL_W - 1, y0 + CELL_H - 1],
                    fill=COLOR_TODAY_BG,
                )

            draw.text((x0 + 6, y0 + 4), str(d.day), font=day_font, fill=COLOR_TEXT)

            events = events_by_day.get(d, [])
            shown = events[:MAX_EVENTS]
            ey = y0 + 26
            for e in shown:
                local_dt = datetime.fromisoformat(e["start_local"])
                line = _fit_text(
                    draw, f"{local_dt:%H:%M} {e['title']}", event_font, CELL_W - 12
                )
                draw.text((x0 + 6, ey), line, font=event_font, fill=COLOR_EVENT)
                ey += 17
            extra = len(events) - len(shown)
            if extra > 0:
                draw.text((x0 + 6, ey), f"+{extra} ещё", font=more_font, fill=COLOR_MUTED)

    return img


def _save(img, out_path):
    out_dir = os.path.dirname(out_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    img.save(out_path, format="PNG")


def render_month(conn, year, month, out_path):
    """Render a 7xN month grid (calendar.monthcalendar weeks, Mon-first)
    to out_path as a PNG. Cells outside the month are left blank. Returns
    out_path.
    """
    raw_weeks = calendar.monthcalendar(year, month)
    week_dates = [
        [date(year, month, day_num) if day_num else None for day_num in week]
        for week in raw_weeks
    ]
    all_dates = [d for week in week_dates for d in week if d is not None]
    events_by_day = _collect_events(conn, all_dates[0], all_dates[-1])

    title = f"{MONTH_NAMES[month]} {year}"
    img = _build_image(title, week_dates, _today_almaty(), events_by_day)
    _save(img, out_path)
    return out_path


def render_week(conn, date_local, out_path):
    """Render the single Mon-Sun week containing date_local (YYYY-MM-DD)
    to out_path as a PNG. Returns out_path.
    """
    y, m, d = (int(x) for x in date_local.split("-"))
    anchor = date(y, m, d)
    monday = anchor - timedelta(days=anchor.weekday())
    week = [monday + timedelta(days=i) for i in range(7)]
    events_by_day = _collect_events(conn, week[0], week[-1])

    start, end = week[0], week[-1]
    if start.month == end.month:
        title = f"{start.day}–{end.day} {MONTH_NAMES[start.month]} {start.year}"
    else:
        title = (
            f"{start.day} {MONTH_NAMES[start.month]} – "
            f"{end.day} {MONTH_NAMES[end.month]} {end.year}"
        )
    img = _build_image(title, [week], _today_almaty(), events_by_day)
    _save(img, out_path)
    return out_path


def _format_day_title(d):
    return f"{WEEKDAY_NAMES[d.weekday()]}, {d.day} {MONTH_NAMES_GENITIVE[d.month]} {d.year}"


def _events_by_hour(events):
    """Bucket events into hour rows [DAY_HOUR_START, DAY_HOUR_END). Events
    starting outside the visible window are clamped into the nearest edge
    row so nothing is silently dropped.
    """
    buckets = {h: [] for h in range(DAY_HOUR_START, DAY_HOUR_END)}
    for e in events:
        hour = datetime.fromisoformat(e["start_local"]).hour
        hour = max(DAY_HOUR_START, min(hour, DAY_HOUR_END - 1))
        buckets[hour].append(e)
    return buckets


def _build_day_image(title_text, events_by_hour):
    from PIL import Image, ImageDraw

    n_rows = DAY_HOUR_END - DAY_HOUR_START
    width = DAY_WIDTH
    height = MARGIN * 2 + HEADER_H + DAY_ROW_H * n_rows

    img = Image.new("RGB", (width, height), COLOR_BG)
    draw = ImageDraw.Draw(img)

    title_font = _load_font(28)
    hour_font = _load_font(14)
    event_font = _load_font(13)
    more_font = _load_font(11)

    tw = draw.textlength(title_text, font=title_font)
    draw.text(((width - tw) / 2, MARGIN), title_text, font=title_font, fill=COLOR_HEADER)

    grid_top = MARGIN + HEADER_H
    grid_left = MARGIN + DAY_HOUR_LABEL_W
    grid_right = width - MARGIN
    grid_bottom = grid_top + DAY_ROW_H * n_rows

    for row in range(n_rows + 1):
        y = grid_top + row * DAY_ROW_H
        draw.line([(MARGIN, y), (grid_right, y)], fill=COLOR_GRID)
    draw.line([(MARGIN, grid_top), (MARGIN, grid_bottom)], fill=COLOR_GRID)
    draw.line([(grid_left, grid_top), (grid_left, grid_bottom)], fill=COLOR_GRID)
    draw.line([(grid_right, grid_top), (grid_right, grid_bottom)], fill=COLOR_GRID)

    for row in range(n_rows):
        hour = DAY_HOUR_START + row
        y0 = grid_top + row * DAY_ROW_H
        draw.text(
            (MARGIN + 6, y0 + 6), f"{hour:02d}:00", font=hour_font, fill=COLOR_MUTED
        )

        events = events_by_hour.get(hour, [])
        shown = events[:DAY_MAX_EVENTS_PER_ROW]
        ey = y0 + 4
        for e in shown:
            start_local = datetime.fromisoformat(e["start_local"])
            if e.get("end_local"):
                end_local = datetime.fromisoformat(e["end_local"])
                time_part = f"{start_local:%H:%M}–{end_local:%H:%M}"
            else:
                time_part = f"{start_local:%H:%M}"
            line = _fit_text(
                draw, f"{time_part} {e['title']}", event_font, grid_right - grid_left - 12
            )
            draw.text((grid_left + 6, ey), line, font=event_font, fill=COLOR_EVENT)
            ey += 18
        extra = len(events) - len(shown)
        if extra > 0:
            draw.text((grid_left + 6, ey), f"+{extra} ещё", font=more_font, fill=COLOR_MUTED)

    return img


def render_day(conn, date_local, out_path):
    """Render a single-day agenda (hour rows DAY_HOUR_START..DAY_HOUR_END,
    local Asia/Almaty) to out_path as a PNG. Each event is shown at its
    local start hour as "HH:MM–HH:MM title" (or "HH:MM title" when the
    event has no end time). Returns out_path.
    """
    y, m, d = (int(x) for x in date_local.split("-"))
    anchor = date(y, m, d)
    events = cal.day(conn, date_local)
    events_by_hour = _events_by_hour(events)

    title = _format_day_title(anchor)
    img = _build_day_image(title, events_by_hour)
    _save(img, out_path)
    return out_path
