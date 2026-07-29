"""End-to-end wiring: run_one_job must route a failed job's content
according to resolve_cron_audience — withheld from the job's own target
plus one operator alert for `audience: "end_user"`, delivered as before
(no alert) for the default/operator audience — and must NOT withhold a
SUCCESSFUL end_user job's normal delivery.

Unlike a test that calls plan_cron_failure_delivery / _deliver_result by
hand (which only re-proves the T3 contract), these tests drive
cron.scheduler.run_one_job itself, following the _patch_pipeline pattern
in test_run_one_job.py. That means a revert of the call-site wiring in
run_one_job (the `deliver_content = final_response if success else ...`
line and its follow-up) makes these tests fail, whereas hand-calling the
helpers would keep passing.
"""
from __future__ import annotations

import cron.scheduler as s


def _patch_pipeline(monkeypatch, *, success=True, final="final response", error=None):
    """Patch run_one_job's collaborators and capture what would have been
    delivered to the job's own target vs. alerted to the operator."""
    delivered = []
    alerted = []

    def fake_run_job(job, *, defer_agent_teardown=None):
        return (success, "out", final, error)

    monkeypatch.setattr(s, "run_job", fake_run_job)
    monkeypatch.setattr(s, "save_job_output", lambda jid, out: f"/tmp/{jid}.txt")
    monkeypatch.setattr(s, "mark_job_run", lambda *a, **k: None)
    # Deterministic: no end_user_targets config, so only the job's own
    # explicit `audience` field decides resolve_cron_audience's outcome.
    monkeypatch.setattr(s, "load_config", lambda: {})
    monkeypatch.setattr(
        s, "_deliver_result",
        lambda job, content, adapters=None, loop=None: delivered.append((job["id"], content)),
    )
    monkeypatch.setattr(
        s, "_send_cron_operator_alert",
        lambda text, cfg=None, adapters=None, loop=None: alerted.append(text),
    )
    return delivered, alerted


def test_run_one_job_failed_end_user_job_delivers_nothing_and_alerts_once(monkeypatch):
    """The incident this fix exists for: a failing end_user job must not
    reach _deliver_result at all, and must trigger exactly one operator
    alert instead."""
    delivered, alerted = _patch_pipeline(
        monkeypatch, success=False, final="", error="ImportError: boom"
    )

    s.run_one_job({"id": "j1", "name": "t", "audience": "end_user"})

    assert delivered == []
    assert len(alerted) == 1
    assert "j1" in alerted[0] or "t" in alerted[0]


def test_run_one_job_failed_operator_job_still_delivers_and_no_alert(monkeypatch):
    """No-regression guarantee: every existing operator-facing cron job
    (default audience, no explicit flag) keeps delivering its failure
    summary to its own chat target, with no separate alert."""
    delivered, alerted = _patch_pipeline(
        monkeypatch, success=False, final="", error="ImportError: boom"
    )

    s.run_one_job({"id": "j2", "name": "t"})

    assert len(delivered) == 1
    assert delivered[0][0] == "j2"
    assert alerted == []


def test_run_one_job_successful_end_user_job_still_delivers(monkeypatch):
    """The policy withholds FAILURES, not output: a successful end_user
    job must deliver its content normally, with no alert."""
    delivered, alerted = _patch_pipeline(
        monkeypatch, success=True, final="• завтра: солнечно, без осадков"
    )

    s.run_one_job({"id": "j3", "name": "t", "audience": "end_user"})

    assert len(delivered) == 1
    assert delivered[0][0] == "j3"
    assert "завтра" in delivered[0][1]
    assert alerted == []
