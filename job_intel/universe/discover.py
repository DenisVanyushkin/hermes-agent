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


def discover_d1(*, exclude_slugs: set[str] | None = None) -> list[CandidateCompany]:
    exclude = {normalize_slug(s) for s in (exclude_slugs or set())}
    out: list[CandidateCompany] = []
    for anchor, names in load_anchor_similar().items():
        for name in names:
            slug = normalize_slug(name)
            if slug in exclude:
                continue
            c = CandidateCompany(name=name, slug=slug, sources=["d1_anchor_similar"])
            c.add_reason("positive_anchor_similarity", f"similar to {anchor}")
            out.append(c)
    return out


def merge_candidates(*lists: list[CandidateCompany]) -> list[CandidateCompany]:
    merged: dict[str, CandidateCompany] = {}
    for lst in lists:
        for c in lst:
            key = c.domain or c.slug
            if key not in merged:
                merged[key] = c
                continue
            m = merged[key]
            m.sources = list(dict.fromkeys(m.sources + c.sources))
            m.evidence = list(dict.fromkeys(m.evidence + c.evidence))
            m.senior_titles = list(dict.fromkeys(m.senior_titles + c.senior_titles))
            for r in c.reasons:
                m.add_reason(r)
    return list(merged.values())


def ensure_cache_table(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS company_candidate_cache (
            slug TEXT PRIMARY KEY, name TEXT, ats_type TEXT, endpoint_url TEXT,
            bucket TEXT, reasons TEXT, sources TEXT,
            first_seen_at TEXT DEFAULT (datetime('now')),
            last_seen_at TEXT DEFAULT (datetime('now')),
            probe_attempts INTEGER NOT NULL DEFAULT 0
        )""")
    conn.commit()


def save_cache(conn: sqlite3.Connection, candidates: list[CandidateCompany]) -> None:
    import json
    for c in candidates:
        conn.execute(
            """INSERT INTO company_candidate_cache
               (slug, name, ats_type, endpoint_url, bucket, reasons, sources)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(slug) DO UPDATE SET
                 last_seen_at = datetime('now'), bucket = excluded.bucket,
                 reasons = excluded.reasons,
                 ats_type = COALESCE(excluded.ats_type, company_candidate_cache.ats_type),
                 endpoint_url = COALESCE(excluded.endpoint_url, company_candidate_cache.endpoint_url),
                 probe_attempts = company_candidate_cache.probe_attempts
                                  + (excluded.ats_type IS NULL)""",
            (c.slug, c.name, c.ats_type, c.endpoint_url, c.bucket,
             json.dumps(c.reasons), json.dumps(c.sources)))
    conn.commit()


def load_cache(conn: sqlite3.Connection) -> dict[str, dict]:
    import json
    rows = conn.execute("SELECT slug, name, ats_type, endpoint_url, bucket, reasons,"
                        " sources, first_seen_at, last_seen_at, probe_attempts"
                        " FROM company_candidate_cache").fetchall()
    keys = ["slug", "name", "ats_type", "endpoint_url", "bucket", "reasons",
            "sources", "first_seen_at", "last_seen_at", "probe_attempts"]
    out = {}
    for row in rows:
        d = dict(zip(keys, row))
        d["reasons"] = json.loads(d["reasons"] or "[]")
        d["sources"] = json.loads(d["sources"] or "[]")
        out[d["slug"]] = d
    return out
