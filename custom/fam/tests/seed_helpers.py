"""Shared DB fixture for seed export/diff tests: one person, one group, one
place, one future event, one active series (+ generated occurrences), one
open plan, one enabled med, one open shopping item. Used by
test_seed_export.py and test_seed_diff.py so both exercise the exact same
baseline shape.
"""
from datetime import datetime, timedelta, timezone

from fam import people, places, cal, series, plans, meds, shopping


def seed_db(conn):
    places.add(conn, "Казакова", address="ул. Казакова 12")
    places.add(conn, "Invictus", lat=43.205156, lon=76.899298)
    people.add(conn, "Аишка", aliases=("Аиша",))
    people.set_home(conn, "Аишка", "Казакова")
    people.add(conn, "татешки", kind="group")
    people.add_member(conn, "татешки", "Аишка")
    future = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
    past = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    ev = cal.add(conn, "ДР", future, place="Invictus", transport="car", participants=("татешки",))
    cal.add(conn, "Прошлое", past)                          # прошедшее — НЕ экспортируется
    series.add(conn, "Тренировка", "mon,wed,fri", "10:00", end_time="12:00",
               place="Invictus", transport="car")
    series.generate(conn)                                    # вхождения — НЕ экспортируются
    plans.add(conn, "Пироги", deadline=None)
    meds.add(conn, "Витамин D", ["08:00"], remaining=30, threshold=5)
    shopping.add(conn, "Молоко", qty="1 л")
    conn.commit()
    return ev
