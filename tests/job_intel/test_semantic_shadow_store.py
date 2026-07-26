"""Phase III shadow persistence — store round-trip. Offline, temp DB."""
from __future__ import annotations

from job_intel.models import Vacancy
from job_intel.store import JobIntelStore


def _store(tmp_path):
    s = JobIntelStore(str(tmp_path / "t.sqlite3"))
    s.bootstrap()
    return s


def _run(s):
    return s.start_run("test")


def test_semantic_shadow_table_roundtrip(tmp_path):
    s = _store(tmp_path)
    run_id = _run(s)
    s.upsert_semantic_shadow_evaluation(
        run_id=run_id, vacancy_key="v1", source="ashby",
        recommendation="promising", action="save", lane="core",
        confidence="low", applied_caps=["cap_uncertain"],
        semantic_hash="abc123", observations_total=4,
        shadow_version="phase3-shadow-1.0.0", error=None)
    rows = s.fetch_semantic_shadow_evaluations(run_id=run_id)
    assert len(rows) == 1
    r = rows[0]
    assert r["vacancy_key"] == "v1"
    assert r["recommendation"] == "promising"
    assert r["applied_caps"] == ["cap_uncertain"]
    assert r["error"] is None


def test_semantic_shadow_upsert_is_idempotent(tmp_path):
    s = _store(tmp_path)
    run_id = _run(s)
    for rec in ("unclear", "promising"):  # second write updates, not duplicates
        s.upsert_semantic_shadow_evaluation(
            run_id=run_id, vacancy_key="v1", source="ashby",
            recommendation=rec, action="save", lane="core", confidence="low",
            applied_caps=[], semantic_hash="h", observations_total=1,
            shadow_version="phase3-shadow-1.0.0", error=None)
    rows = s.fetch_semantic_shadow_evaluations(run_id=run_id)
    assert len(rows) == 1
    assert rows[0]["recommendation"] == "promising"


def test_semantic_shadow_records_error_rows(tmp_path):
    s = _store(tmp_path)
    run_id = _run(s)
    s.upsert_semantic_shadow_evaluation(
        run_id=run_id, vacancy_key="v2", source="lever",
        recommendation=None, action=None, lane=None, confidence=None,
        applied_caps=[], semantic_hash=None, observations_total=None,
        shadow_version="phase3-shadow-1.0.0", error="ValueError: boom")
    rows = s.fetch_semantic_shadow_evaluations(run_id=run_id)
    assert rows[0]["error"] == "ValueError: boom"
    assert rows[0]["recommendation"] is None
