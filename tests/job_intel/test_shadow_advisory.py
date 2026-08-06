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


def test_bare_uncertain_verdict_without_statements_is_not_advisory():
    """Live preview finding: 38 of 39 selected roles were `uncertain` with NO
    concrete blocker/unknown text — an advisory line with nothing to say is
    noise that would destroy trust in the feature. Only roles with an actual
    statement qualify."""
    rows = [
        _row("A", "T1", {"verdict": "uncertain", "blockers": [], "unknowns": []}),
        _row("B", "T2", {"verdict": "infeasible", "blockers": [], "unknowns": []}),
        _row("C", "T3", {"verdict": "uncertain", "blockers": [], "unknowns": ["geo unclear"]}),
    ]
    adv = build_feasibility_advisory(rows)
    assert [a["company"] for a in adv] == ["C"]


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


def test_describe_never_claims_delivery_on_failure():
    """Live enablement finding: the entrypoint printed 'posted 1 caveat(s)'
    while delivery had actually failed. A false success report is worse than a
    failure — the operator believes Slack got it."""
    from job_intel.shadow_advisory import describe_post_result
    ok = describe_post_result({"posted": True}, run_id=1, count=2)
    assert "posted" in ok.lower() and "fail" not in ok.lower()
    bad = describe_post_result({"posted": False, "error": "boom"}, run_id=1, count=2)
    assert "boom" in bad
    low = bad.lower()
    assert "not posted" in low or "failed" in low
    assert not low.startswith("[advisory] run 1: posted")
    dry = describe_post_result({"posted": False, "dry_run": True}, run_id=1, count=2)
    assert "dry-run" in dry.lower()


def test_post_advisory_uses_gateway_not_webhook(monkeypatch):
    """Cards are delivered through the hermes gateway (the webhook env var is
    empty in production), so the advisory must use the same path."""
    from job_intel import shadow_advisory as mod
    sent = {}

    def fake_send(payload):
        sent.update(payload)
        return '{"success": true, "ts": "1.2"}'

    monkeypatch.setattr(mod, "_gateway_send", fake_send)
    res = mod.post_advisory("hello", dry_run=False, channel="C123")
    assert res["posted"] is True
    assert sent["target"] == "slack:C123"
    assert sent["message"] == "hello"


def test_post_advisory_reports_gateway_error(monkeypatch):
    from job_intel import shadow_advisory as mod
    monkeypatch.setattr(mod, "_gateway_send", lambda p: '{"error": "nope"}')
    res = mod.post_advisory("hello", dry_run=False, channel="C123")
    assert res["posted"] is False
    assert "nope" in str(res["error"])


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
