import sqlite3

import pytest

from job_intel.universe.discover import discover_d7

SCHEMA = """
CREATE TABLE vacancy_observability (
  company TEXT, title TEXT, source TEXT, role_bucket TEXT, geo_bucket TEXT,
  industry_bucket TEXT, executive_detected INTEGER, created_at TEXT
);
"""


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.executescript(SCHEMA)
    rows = [
        ("Nium", "VP Product, Payments", "greenhouse", "product", "apac", "payments", 1, "2026-07-01"),
        ("Nium", "Director of Product", "greenhouse", "product", "apac", "payments", 1, "2026-07-01"),
        ("DesignCo", "Head of Product Design", "greenhouse", "design", "eu", "fintech", 1, "2026-07-01"),
        ("HHOnly", "Head of Product", "headhunter", "product", "eu", "fintech", 1, "2026-07-01"),
        ("Old", "VP Product", "lever", "product", "eu", "fintech", 1, "2020-01-01"),
        ("Wise", "Product Director", "greenhouse", "product", "apac", "payments", 1, "2026-07-01"),
    ]
    c.executemany("INSERT INTO vacancy_observability VALUES (?,?,?,?,?,?,?,?)", rows)
    return c


def test_d7_finds_qualifying_company(conn):
    out = discover_d7(conn, exclude_slugs={"wise"})
    names = {c.name for c in out}
    assert "Nium" in names
    nium = next(c for c in out if c.name == "Nium")
    assert "senior_product_titles" in nium.reasons
    assert "fintech_payments_fit" in nium.reasons
    assert nium.sources == ["d7_cooccurrence"]


def test_d7_negative_anchors_and_exclusions(conn):
    names = {c.name for c in discover_d7(conn, exclude_slugs={"wise"})}
    assert "DesignCo" not in names       # negative role bucket
    assert "HHOnly" not in names         # single HH-only title
    assert "Old" not in names            # outside window
    assert "Wise" not in names           # excluded (already a seed/anchor)
