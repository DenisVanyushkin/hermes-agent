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
