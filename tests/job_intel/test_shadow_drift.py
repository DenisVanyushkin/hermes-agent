"""Phase III drift report (B1). Pure mappings + temp-DB integration. Offline."""
from __future__ import annotations

from job_intel.shadow_drift import (
    build_drift_report,
    prod_band,
    reaction_alignment,
    reaction_polarity,
    shadow_band,
)
from job_intel.store import JobIntelStore


# --- pure mappings ----------------------------------------------------------

def test_band_mappings():
    assert shadow_band("exceptional") == "top"
    assert shadow_band("promising") == "mid"
    assert shadow_band("unclear") == "review"
    assert shadow_band("not_recommended") == "reject"
    assert shadow_band(None) == "unknown"
    assert prod_band("strong_fit") == "top"
    assert prod_band("needs_review") == "review"
    assert prod_band("reject") == "reject"
    assert prod_band("weird") == "unknown"


def test_reaction_polarity():
    assert reaction_polarity("applied") == "positive"
    assert reaction_polarity("save_for_later") == "weak_positive"
    assert reaction_polarity("not_interesting") == "negative"


def test_reaction_alignment_scores_each_provider():
    reacted = [
        # user liked it: provider that recommended is aligned, one that rejected missed
        {"polarity": "positive", "shadow_band": "mid", "prod_band": "reject"},
        # user disliked it: provider that rejected is aligned, one that recommended is FP
        {"polarity": "negative", "shadow_band": "top", "prod_band": "reject"},
    ]
    a = reaction_alignment(reacted)
    assert a["shadow"]["aligned"] == 1 and a["shadow"]["false_positive"] == 1
    assert a["prod"]["missed"] == 1 and a["prod"]["aligned"] == 1


# --- integration on a temp DB ----------------------------------------------

def _seed(store, run_id):
    from job_intel.models import Vacancy
    for k in ("v1", "v2"):
        store.upsert_vacancy(
            Vacancy(source="ashby", source_id=k, company="Acme", title="Head of Product",
                    location="Remote", url=f"https://ex/{k}", description="x"), k)
    store.upsert_semantic_shadow_evaluation(
        run_id=run_id, vacancy_key="v1", source="ashby", recommendation="not_recommended",
        action="reject", lane="core", confidence="low", applied_caps=[],
        semantic_hash="h1", observations_total=0, shadow_version="x", error=None)
    store.upsert_semantic_shadow_evaluation(
        run_id=run_id, vacancy_key="v2", source="ashby", recommendation="promising",
        action="save", lane="core", confidence="low", applied_caps=[],
        semantic_hash="h2", observations_total=3, shadow_version="x", error=None)
    from job_intel.models import Evaluation
    def ev(rec, score):
        return Evaluation(score=score, tier=rec, recommendation=rec,
                          matched_signals=[], concerns=[], reasons=[])
    store.save_evaluation("v1", ev("reject", 0), run_id=run_id)
    store.save_evaluation("v2", ev("reject", 10), run_id=run_id)  # prod rejects what shadow likes


def test_drift_report_end_to_end(tmp_path):
    s = JobIntelStore(str(tmp_path / "t.sqlite3"))
    s.bootstrap()
    run_id = s.start_run("test")
    _seed(s, run_id)
    rep = build_drift_report(s, lookback_days=3650)
    assert rep["vacancies_compared"] == 2
    # v1: shadow reject / prod reject -> agree; v2: shadow mid / prod reject -> disagree
    assert rep["coarse_agreement_rate"] == 0.5
    assert rep["shadow_band_distribution"]["reject"] == 1
    assert rep["shadow_band_distribution"]["mid"] == 1
    assert rep["prod_band_distribution"]["reject"] == 2


def test_drift_report_dedupes_vacancy_across_runs(tmp_path):
    """A vacancy re-shadowed in multiple daily runs must count ONCE (latest
    run), else a reacted vacancy inflates alignment counts over the window."""
    from job_intel.models import Vacancy, Evaluation
    s = JobIntelStore(str(tmp_path / "t.sqlite3"))
    s.bootstrap()
    s.upsert_vacancy(Vacancy(source="ashby", source_id="v1", company="A",
                             title="T", location="R", url="u", description="x"), "v1")
    run1 = s.start_run("r1")
    run2 = s.start_run("r2")
    for rid, rec in ((run1, "unclear"), (run2, "promising")):  # verdict changed run-to-run
        s.upsert_semantic_shadow_evaluation(
            run_id=rid, vacancy_key="v1", source="ashby", recommendation=rec,
            action="x", lane="core", confidence="low", applied_caps=[],
            semantic_hash="h", observations_total=1, shadow_version="x", error=None)
        s.save_evaluation("v1", Evaluation(score=0, tier="reject", recommendation="reject",
                          matched_signals=[], concerns=[], reasons=[]), run_id=rid)
    rep = build_drift_report(s, lookback_days=3650)
    assert rep["vacancies_compared"] == 1  # not 2
    assert rep["shadow_band_distribution"]["mid"] == 1  # latest run's "promising"
    assert rep["shadow_band_distribution"]["review"] == 0


def test_drift_report_empty_is_safe(tmp_path):
    s = JobIntelStore(str(tmp_path / "t.sqlite3"))
    s.bootstrap()
    s.start_run("test")
    rep = build_drift_report(s, lookback_days=14)
    assert rep["vacancies_compared"] == 0
    assert rep["coarse_agreement_rate"] is None
