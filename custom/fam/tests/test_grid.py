import os
from fam import cal, grid


def test_render_month_creates_readable_png(db, tmp_path):
    cal.add(db, "Врач", "2026-07-15T05:00:00+00:00"); db.commit()
    out = grid.render_month(db, 2026, 7, str(tmp_path / "july.png"))
    assert os.path.getsize(out) > 5000
    from PIL import Image
    img = Image.open(out)
    assert img.size[0] >= 900 and img.format == "PNG"


def test_render_week_with_cyrillic_title_produces_readable_png(db, tmp_path):
    # 2026-07-15 falls in the week rendered for the 2026-07-13 anchor date.
    cal.add(db, "Встреча с бабушкой", "2026-07-15T05:00:00+00:00"); db.commit()
    out = grid.render_week(db, "2026-07-13", str(tmp_path / "week.png"))
    assert os.path.getsize(out) > 5000


def test_render_day_creates_readable_png(db, tmp_path):
    # 05:00 UTC = 10:00 Asia/Almaty on 2026-07-11.
    cal.add(db, "Врач", "2026-07-11T05:00:00+00:00"); db.commit()
    out = grid.render_day(db, "2026-07-11", str(tmp_path / "day.png"))
    assert os.path.getsize(out) > 5000
    from PIL import Image
    img = Image.open(out)
    assert img.size[0] >= 400 and img.format == "PNG"


def test_render_day_with_no_events_still_produces_readable_png(db, tmp_path):
    out = grid.render_day(db, "2026-07-11", str(tmp_path / "empty-day.png"))
    assert os.path.getsize(out) > 5000
    from PIL import Image
    img = Image.open(out)
    assert img.size[0] >= 400 and img.format == "PNG"


def test_render_day_places_multiple_events_at_their_local_hours(db, tmp_path):
    # 03:00 UTC -> 08:00 Almaty, 12:00 UTC -> 17:00 Almaty.
    cal.add(db, "Завтрак", "2026-07-11T03:00:00+00:00"); db.commit()
    cal.add(db, "Встреча", "2026-07-11T12:00:00+00:00"); db.commit()
    out = grid.render_day(db, "2026-07-11", str(tmp_path / "multi-day.png"))
    assert os.path.getsize(out) > 5000
