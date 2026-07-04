from job_intel.universe.report import format_universe_report
from job_intel.universe.models import CandidateCompany


def _mk(name, bucket, sources, reasons, dry=3):
    c = CandidateCompany(name=name, sources=sources)
    for r in reasons:
        c.add_reason(r)
    c.bucket = bucket
    c.dry_run_vacancies = dry
    c.dry_run_sample_titles = ["VP Product"]
    return c


def test_report_groups_and_flags_quota():
    cands = [
        _mk("Nium", "strong_candidate", ["d1_anchor_similar"],
            ["supported_ats", "geo_fit", "fintech_payments_fit", "senior_product_titles"]),
        _mk("Zepz", "maybe", ["d1_anchor_similar"], ["positive_anchor_similarity"]),
    ]
    text = format_universe_report(cands, week_label="2026-W27")
    assert "read-only" in text
    assert "strong_candidate (1)" in text
    assert "reasons: supported_ats" in text
    assert "⚠️" in text  # 0% non-anchor < 30% quota


def test_report_quota_ok_without_warning():
    cands = [_mk("Nium", "candidate", ["d7_cooccurrence"],
                 ["supported_ats", "geo_fit", "senior_product_titles"])]
    text = format_universe_report(cands, week_label="2026-W27")
    assert "⚠️" not in text


def test_report_rejected_summary():
    c = _mk("SpamCo", "reject", ["d7_cooccurrence"], ["low_relevance"], dry=-1)
    text = format_universe_report([c], week_label="2026-W27")
    assert "Rejected this run: 1" in text
    assert "low_relevance: 1" in text


def test_run_universe_discovery_end_to_end(monkeypatch):
    import sqlite3
    from job_intel import universe

    conn = sqlite3.connect(":memory:")
    conn.executescript("""
    CREATE TABLE vacancy_observability (
      company TEXT, title TEXT, source TEXT, role_bucket TEXT, geo_bucket TEXT,
      industry_bucket TEXT, executive_detected INTEGER, created_at TEXT
    );
    INSERT INTO vacancy_observability VALUES
      ('Nium','VP Product, Payments','greenhouse','product','apac','payments',1,'2026-07-01');
    """)
    monkeypatch.setattr(universe, "_open_ro_conn", lambda: conn)
    monkeypatch.setattr(universe, "_open_cache_conn", lambda: conn)
    monkeypatch.setattr(universe, "_exclude_slugs", lambda: {"wise", "airwallex"})
    monkeypatch.setattr(universe, "_PROBE_DELAY", 0)
    monkeypatch.setattr(universe.endpoints, "probe_ats",
                        lambda slug, session=None: (
                            "greenhouse",
                            f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs")
                        if slug == "nium" else None)
    monkeypatch.setattr(universe.dry_run, "dry_run_candidate",
                        lambda c, fetchers=None: setattr(c, "dry_run_vacancies", 4))
    text = universe.run_universe_discovery(deliver=False)
    assert "Company Discovery Report" in text
    assert "Nium" in text
    assert "read-only" in text
    # cache row persisted for probed slug
    cached = universe.load_cache(conn)
    assert cached["nium"]["ats_type"] == "greenhouse"
