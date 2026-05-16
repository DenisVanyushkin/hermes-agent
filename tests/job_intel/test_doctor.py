from __future__ import annotations

from pathlib import Path

from job_intel import cli
from job_intel.models import Vacancy
from job_intel.store import JobIntelStore


def test_doctor_reports_runtime_and_source_status(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "state" / "job_intel.sqlite3"
    workdir = tmp_path / "workdir"
    scripts_dir = tmp_path / "scripts"
    workdir.mkdir(parents=True)
    (workdir / "job_intel").mkdir()
    scripts_dir.mkdir()
    for name in ["job_intel_daily.sh", "job_intel_alert.sh", "job_intel_enrichment.sh"]:
        (scripts_dir / name).write_text("#!/bin/sh\n", encoding="utf-8")

    store = JobIntelStore(db_path)
    store.bootstrap()
    run_id = store.start_run("daily")
    store.finish_run(
        run_id,
        status="ok",
        notes="found=1 accepted=1",
        metadata={"source_statuses": {"headhunter": {"status": "blocked", "error": "403"}, "duckduckgo": {"status": "ok", "hits": 2}}},
    )

    monkeypatch.setenv("JOB_INTEL_DB_PATH", str(db_path))
    monkeypatch.setenv("JOB_INTEL_WORKDIR", str(workdir))
    monkeypatch.setenv("JOB_INTEL_SCRIPTS_DIR", str(scripts_dir))
    monkeypatch.setenv("JOB_INTEL_ENVIRONMENT", "test")
    monkeypatch.setenv("JOB_INTEL_SLACK_WEBHOOK_URL", "https://hooks.slack.test/example")
    monkeypatch.setattr(cli, "_collect_source_statuses", lambda store: {"headhunter": {"status": "blocked", "error": "403"}, "duckduckgo": {"status": "ok", "hits": 2}})

    report = cli.doctor_report()

    assert "Current user:" in report
    assert f"DB path: {db_path}" in report
    assert "DB readable: yes" in report
    assert "DB writable: yes" in report
    assert "job_intel_daily.sh" in report
    assert "Slack delivery: webhook" in report
    assert "headhunter: blocked" in report
    assert "duckduckgo: ok" in report
    assert "Last run: ok" in report


def test_send_test_message_includes_runtime_context(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "job_intel.sqlite3"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    workdir = tmp_path / "workdir"
    workdir.mkdir()

    captured = {}

    def fake_deliver(message: str, channel: str | None = None, *, retries: int = 3) -> bool:
        captured["message"] = message
        captured["channel"] = channel
        return True

    monkeypatch.setenv("JOB_INTEL_DB_PATH", str(db_path))
    monkeypatch.setenv("JOB_INTEL_WORKDIR", str(workdir))
    monkeypatch.setenv("JOB_INTEL_ENVIRONMENT", "staging")
    monkeypatch.setenv("JOB_INTEL_SLACK_WEBHOOK_URL", "https://hooks.slack.test/example")
    monkeypatch.setattr(cli, "_deliver_to_slack", fake_deliver)

    result = cli.send_test_message("<#C0B42K4H4KV>")

    assert result == "sent"
    assert captured["channel"] == "<#C0B42K4H4KV>"
    assert "Runtime user:" in captured["message"]
    assert f"DB path: {db_path}" in captured["message"]
    assert "Environment: staging" in captured["message"]
    assert "Timestamp:" in captured["message"]
