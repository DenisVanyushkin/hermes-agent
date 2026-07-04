"""Discoverers for the company universe (MVP-0: D7 co-occurrence, D1a curated)."""
from __future__ import annotations

import sqlite3

from .anchors import NEGATIVE_ROLE_BUCKETS, NEGATIVE_TITLE_RE, load_anchor_similar
from .models import CandidateCompany, normalize_slug

_GEO_FIT = frozenset({"eu", "europe", "gcc", "mena", "apac", "remote"})
_FINTECH = frozenset({"fintech", "payments"})


def discover_d7(conn: sqlite3.Connection, *, days: int = 90,
                exclude_slugs: set[str] | None = None, limit: int = 30) -> list[CandidateCompany]:
    exclude = {normalize_slug(s) for s in (exclude_slugs or set())}
    rows = conn.execute(
        """
        SELECT company, title, source, role_bucket, geo_bucket, industry_bucket
        FROM vacancy_observability
        WHERE executive_detected = 1
          AND company IS NOT NULL AND company != ''
          AND created_at >= date('now', ?)
        """,
        (f"-{days} days",),
    ).fetchall()

    grouped: dict[str, CandidateCompany] = {}
    hh_titles: dict[str, set[str]] = {}
    all_titles: dict[str, set[tuple[str, str]]] = {}
    for company, title, source, role_bucket, geo_bucket, industry_bucket in rows:
        slug = normalize_slug(company)
        if slug in exclude:
            continue
        if (role_bucket or "") in NEGATIVE_ROLE_BUCKETS or NEGATIVE_TITLE_RE.search(title or ""):
            continue
        c = grouped.setdefault(slug, CandidateCompany(name=company, slug=slug,
                                                      sources=["d7_cooccurrence"]))
        c.senior_titles.append(title)
        c.add_reason("senior_product_titles", f"{title} ({source})")
        if (geo_bucket or "").lower() in _GEO_FIT:
            c.add_reason("geo_fit", f"geo_bucket={geo_bucket}")
        if (industry_bucket or "").lower() in _FINTECH:
            c.add_reason("fintech_payments_fit", f"industry_bucket={industry_bucket}")
        all_titles.setdefault(slug, set()).add((title, source))
        if source == "headhunter":
            hh_titles.setdefault(slug, set()).add(title)

    out = []
    for slug, c in grouped.items():
        # HH low-quality negative anchor: HH-only companies need >=2 distinct titles
        titles = all_titles.get(slug, set())
        hh_only = titles and all(src == "headhunter" for _, src in titles)
        if hh_only and len(hh_titles.get(slug, set())) < 2:
            continue
        out.append(c)
    out.sort(key=lambda c: len(set(c.senior_titles)), reverse=True)
    return out[:limit]
