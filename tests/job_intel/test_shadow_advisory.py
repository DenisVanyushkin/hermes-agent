"""Stage 1 — soft feasibility advisory. Offline; posting is dry-run only."""
from __future__ import annotations

from job_intel.shadow_advisory import (
    advisory_enabled,
    build_feasibility_advisory,
    format_advisory,
)
from job_intel.store import JobIntelStore


def _row(company, title, feas, rec="strong_fit"):
    return {"company": company, "title": title, "url": f"https://x/{title}",
            "prod_recommendation": rec, "feasibility": feas}


def test_advisory_selects_only_rows_with_concerns():
    rows = [
        _row("A", "Head of Product", {"verdict": "feasible", "blockers": [], "unknowns": []}),
        _row("B", "VP Product", {"verdict": "uncertain", "blockers": [],
                                 "unknowns": ["sponsorship not stated"]}),
        _row("C", "Dir Product", {"verdict": "infeasible",
                                  "blockers": ["onsite required, remote-only candidate"],
                                  "unknowns": []}),
        _row("D", "GM", None),  # no feasibility captured -> skip
    ]
    adv = build_feasibility_advisory(rows)
    names = [a["company"] for a in adv]
    assert names == ["B", "C"]  # A feasible, D missing -> excluded
    assert adv[0]["unknowns"] == ["sponsorship not stated"]
    assert adv[1]["blockers"] == ["onsite required, remote-only candidate"]


def test_advisory_never_includes_rejected_rows_by_construction():
    # the builder trusts the store filter (rec != reject); it only gates on
    # feasibility concern, so a feasible row is dropped regardless of rec
    rows = [_row("A", "T", {"verdict": "feasible", "blockers": [], "unknowns": []})]
    assert build_feasibility_advisory(rows) == []


def test_format_is_labelled_advisory_and_lists_flags():
    adv = [{"company": "B", "title": "VP Product", "url": "u",
            "prod_recommendation": "strong_fit", "blockers": [],
            "unknowns": ["sponsorship not stated"]}]
    msg = format_advisory(adv, run_label="run 999")
    low = msg.lower()
    assert "advisory" in low or "feasibility" in low
    assert "observe" in low or "не влияет" in low or "does not change" in low
    assert "VP Product" in msg
    assert "sponsorship not stated" in msg


def test_format_empty_is_none():
    assert format_advisory([], run_label="run 1") is None


def test_advisory_flag_default_off(monkeypatch):
    monkeypatch.delenv("SEMANTIC_SHADOW_ADVISORY_ENABLED", raising=False)
    assert advisory_enabled() is False  # user-facing: OFF until explicitly enabled
    monkeypatch.setenv("SEMANTIC_SHADOW_ADVISORY_ENABLED", "1")
    assert advisory_enabled() is True


def test_store_fetch_advisory_joins_shadow_and_prod(tmp_path):
    from job_intel.models import Vacancy, Evaluation
    s = JobIntelStore(str(tmp_path / "t.sqlite3"))
    s.bootstrap()
    run_id = s.start_run("t")
    for k, rec in (("v1", "strong_fit"), ("v2", "reject")):
        s.upsert_vacancy(Vacancy(source="ashby", source_id=k, company="Co"+k, title="T"+k,
                                 location="R", url="u"+k, description="x"), k)
        s.save_evaluation(k, Evaluation(score=0, tier="reject" if rec == "reject" else "strong_fit",
                          recommendation=rec, matched_signals=[], concerns=[], reasons=[]),
                          run_id=run_id)
        s.upsert_semantic_shadow_evaluation(
            run_id=run_id, vacancy_key=k, source="ashby", recommendation="unclear",
            action="investigate", lane="core", confidence="low", applied_caps=[],
            semantic_hash="h", observations_total=0, shadow_version="x", error=None,
            feasibility={"verdict": "uncertain", "blockers": [], "unknowns": ["geo unknown"]})
    rows = s.fetch_shadow_advisory(run_id=run_id)
    # only v1 (prod != reject) is a candidate; v2 rejected -> excluded
    assert len(rows) == 1
    assert rows[0]["company"] == "Cov1"
    assert rows[0]["feasibility"]["unknowns"] == ["geo unknown"]
