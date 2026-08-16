import importlib.util
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "nightly_diagnostics_collect.py"
SPEC = importlib.util.spec_from_file_location("nightly_diagnostics_collect", SCRIPT_PATH)
collect = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(collect)


def test_parse_log_line_extracts_ts_level_rest():
    line = "2026-07-04 23:41:41,123 ERROR gateway.router: upstream timed out"
    parsed = collect.parse_log_line(line)
    assert parsed["ts"] == datetime(2026, 7, 4, 23, 41, 41)
    assert parsed["level"] == "ERROR"
    assert parsed["rest"] == "gateway.router: upstream timed out"


def test_parse_log_line_rejects_non_log_lines():
    assert collect.parse_log_line("Traceback (most recent call last):") is None
    assert collect.parse_log_line("") is None


def test_normalize_signature_masks_volatile_parts():
    a = collect.normalize_signature("session abc123def456 failed after 34 retries")
    b = collect.normalize_signature("session 9f8e7d6c5b4a failed after 2 retries")
    assert a == b
    assert "<hex>" in a and "<n>" in a


def test_extract_log_findings_groups_and_counts():
    since = datetime(2026, 7, 4, 0, 0, 0)
    lines = [
        "2026-07-03 10:00:00,000 ERROR mod: old error 111",          # before window
        "2026-07-04 10:00:00,000 ERROR mod: timeout after 30s",
        "2026-07-04 11:00:00,000 ERROR mod: timeout after 45s",
        "2026-07-04 12:00:00,000 WARNING mod: disk almost full",
        "2026-07-04 12:00:01,000 INFO mod: all fine",                 # not error/warning
    ]
    findings = collect.extract_log_findings(lines, since)
    assert len(findings) == 2
    assert findings[0]["count"] == 2 and findings[0]["level"] == "ERROR"
    assert findings[1]["count"] == 1 and findings[1]["level"] == "WARNING"
    assert len(findings[0]["examples"]) == 2


def test_memory_trend_computes_min_max_delta():
    since = datetime(2026, 7, 4, 0, 0, 0)
    lines = [
        "2026-07-04 10:00:00,000 INFO agent: [MEMORY] rss=480mb",
        "2026-07-04 12:00:00,000 INFO agent: [MEMORY] rss=530mb",
        "2026-07-04 14:00:00,000 INFO agent: [MEMORY] rss=510mb",
    ]
    trend = collect.memory_trend(lines, since)
    assert trend == {"min_mb": 480, "max_mb": 530, "last_mb": 510, "delta_mb": 30, "samples": 3}


def test_memory_trend_returns_none_without_samples():
    assert collect.memory_trend([], datetime(2026, 7, 4)) is None


def test_summarize_cron_jobs_splits_ok_failed_paused(tmp_path):
    out_root = tmp_path / "output"
    failed_dir = out_root / "badjob01"
    failed_dir.mkdir(parents=True)
    (failed_dir / "2026-07-01_10-00-00.md").write_text("older run", encoding="utf-8")
    (failed_dir / "2026-07-04_10-00-00.md").write_text("boom: module not found", encoding="utf-8")
    jobs = [
        {"id": "okjob001", "name": "weather", "enabled": True, "last_status": "ok",
         "last_run_at": "2026-07-04T10:00:00", "schedule_display": "0 4 * * *"},
        {"id": "badjob01", "name": "enrichment", "enabled": True,
         "last_status": "error: Script exited with code 1",
         "last_error": "No module named job_intel.__main__",
         "last_run_at": "2026-07-01T10:00:28", "schedule_display": "0 10 */14 * *"},
        {"id": "paused01", "name": "old-thing", "enabled": False},
    ]
    summary = collect.summarize_cron_jobs(jobs, out_root)
    assert [j["name"] for j in summary["ok"]] == ["weather"]
    assert [j["name"] for j in summary["paused"]] == ["old-thing"]
    failed = summary["failed"]
    assert len(failed) == 1
    assert failed[0]["last_error"] == "No module named job_intel.__main__"
    assert "boom: module not found" in failed[0]["output_tail"]


def test_latest_output_tail_missing_dir_returns_none(tmp_path):
    assert collect.latest_output_tail(tmp_path / "nope") is None


def test_summarize_cron_jobs_never_ran_job_counts_as_ok(tmp_path):
    jobs = [{"id": "newjob01", "name": "brand-new", "enabled": True}]
    summary = collect.summarize_cron_jobs(jobs, tmp_path / "output")
    assert summary["failed"] == [] and summary["paused"] == []
    assert summary["ok"][0]["last_status"] == "never-ran"


def test_diff_known_issues_marks_new_known_resolved():
    now = datetime(2026, 7, 5, 6, 40, 0)
    state = {
        "old sig": {"first_seen": "2026-07-01T06:40:00", "last_seen": "2026-07-04T06:40:00", "count": 2},
        "gone sig": {"first_seen": "2026-06-20T06:40:00", "last_seen": "2026-07-04T06:40:00", "count": 1},
    }
    findings = [
        {"level": "ERROR", "signature": "old sig", "count": 3, "examples": []},
        {"level": "ERROR", "signature": "fresh sig", "count": 1, "examples": []},
    ]
    annotated, resolved, new_state = collect.diff_known_issues(state, findings, now)
    by_sig = {f["signature"]: f for f in annotated}
    assert by_sig["old sig"]["status"] == "known"
    assert by_sig["old sig"]["age_days"] == 4
    assert by_sig["old sig"]["first_seen"] == "2026-07-01T06:40:00"
    assert by_sig["fresh sig"]["status"] == "new"
    assert by_sig["fresh sig"]["age_days"] == 0
    assert [r["signature"] for r in resolved] == ["gone sig"]
    assert set(new_state) == {"old sig", "fresh sig"}
    assert new_state["old sig"]["last_seen"] == "2026-07-05T06:40:00"
    assert new_state["fresh sig"]["first_seen"] == "2026-07-05T06:40:00"


def _make_job_intel_db(path):
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE vacancy_observability (run_id TEXT, vacancy_key TEXT,"
        " accepted INTEGER, notified INTEGER, created_at TEXT)"
    )
    conn.execute(
        "CREATE TABLE vacancy_rejection_events (run_id TEXT, rejection_reason TEXT,"
        " reason_type TEXT, severity TEXT)"
    )
    rows = [
        ("run-old", "v1", 1, 1, "2026-07-03T10:00:00"),
        ("run-new", "v2", 1, 0, "2026-07-04T10:00:00"),
        ("run-new", "v3", 0, 0, "2026-07-04T10:00:01"),
        ("run-new", "v4", 0, 0, "2026-07-04T10:00:02"),
    ]
    conn.executemany("INSERT INTO vacancy_observability VALUES (?,?,?,?,?)", rows)
    conn.executemany(
        "INSERT INTO vacancy_rejection_events VALUES (?,?,?,?)",
        [
            ("run-new", "low_seniority", "blocker", "high"),
            ("run-new", "low_seniority", "blocker", "high"),
            ("run-new", "onsite_requirement_mismatch", "blocker", "high"),
            # Data gaps outnumber every blocker in every real run: they are
            # emitted per vacancy regardless of why it was rejected.
            ("run-new", "salary_unknown", "unknown", "low"),
            ("run-new", "salary_unknown", "unknown", "low"),
            ("run-new", "salary_unknown", "unknown", "low"),
            ("run-new", "low_confidence", "warning", "low"),
            ("run-new", "low_confidence", "warning", "low"),
            ("run-new", "low_confidence", "warning", "low"),
            ("run-new", "low_confidence", "warning", "low"),
        ],
    )
    conn.commit()
    conn.close()


def test_job_intel_summary_reads_latest_run(tmp_path):
    db = tmp_path / "job_intel.sqlite3"
    _make_job_intel_db(db)
    summary = collect.job_intel_summary(db)
    assert summary["run_id"] == "run-new"
    assert summary["found"] == 3
    assert summary["accepted"] == 1
    assert summary["notified"] == 0
    assert summary["top_blockers"] == [
        {"reason": "low_seniority", "count": 2},
        {"reason": "onsite_requirement_mismatch", "count": 1},
    ]


def test_job_intel_summary_keeps_data_gaps_out_of_the_blocker_list(tmp_path):
    """salary_unknown outnumbers every blocker but is not why a vacancy was rejected."""
    db = tmp_path / "job_intel.sqlite3"
    _make_job_intel_db(db)
    summary = collect.job_intel_summary(db)
    blockers = {row["reason"] for row in summary["top_blockers"]}
    assert "salary_unknown" not in blockers
    assert "low_confidence" not in blockers
    assert summary["top_data_gaps"] == [
        {"reason": "low_confidence", "count": 4},
        {"reason": "salary_unknown", "count": 3},
    ]


def test_job_intel_summary_prefers_latest_completed_daily_run_over_scrape_timestamp_and_id(tmp_path):
    db = tmp_path / "job_intel.sqlite3"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE runs (id INTEGER PRIMARY KEY, mode TEXT, status TEXT, run_type TEXT,"
        " started_at TEXT, finished_at TEXT)"
    )
    conn.execute(
        "CREATE TABLE vacancy_observability (run_id INTEGER, vacancy_key TEXT,"
        " accepted INTEGER, notified INTEGER, created_at TEXT)"
    )
    conn.execute(
        "CREATE TABLE vacancy_rejection_events (run_id INTEGER, rejection_reason TEXT,"
        " reason_type TEXT, severity TEXT)"
    )
    conn.execute(
        "CREATE TABLE vacancy_card_decisions (run_id INTEGER, decision TEXT)"
    )
    conn.execute(
        "CREATE TABLE notifications (run_id INTEGER, vacancy_id INTEGER, delivery_status TEXT)"
    )
    conn.executemany(
        "INSERT INTO runs VALUES (?,?,?,?,?,?)",
        [
            (1, "daily", "ok", "production", "2026-07-05T09:00:00", "2026-07-05T09:10:00"),
            (2, "daily", "ok", "production", "2026-07-04T09:00:00", "2026-07-04T09:10:00"),
        ],
    )
    # Reused vacancy data can retain an older scrape timestamp. The higher-ID
    # run 2 also completed earlier, so neither value can select it over run 1.
    conn.executemany(
        "INSERT INTO vacancy_observability VALUES (?,?,?,?,?)",
        [
            (1, "new-accepted", 1, 0, "2026-06-01T09:00:00"),
            (1, "new-rejected", 0, 0, "2026-06-01T09:00:00"),
            (2, "old", 1, 1, "2026-07-06T09:00:00"),
        ],
    )
    conn.executemany(
        "INSERT INTO vacancy_card_decisions VALUES (?,?)",
        [(1, "suppressed_recently_sent"), (1, "suppressed_recently_sent")],
    )
    conn.execute("INSERT INTO notifications VALUES (?,?,?)", (1, 42, "failed"))
    conn.commit()
    conn.close()

    summary = collect.job_intel_summary(db)

    assert summary["run_id"] == 1
    assert summary["run_at"] == "2026-07-05T09:10:00"
    assert summary["run_selection"] == "daily_production_run"
    assert summary["found"] == 2
    assert summary["accepted"] == 1
    assert summary["notified"] == 0
    assert summary["notification_diagnostics"] == {
        "card_decisions": {"suppressed_recently_sent": 2},
        "delivery_statuses": {"failed": 1},
    }


def test_job_intel_summary_missing_db_reports_error(tmp_path):
    summary = collect.job_intel_summary(tmp_path / "absent.sqlite3")
    assert "error" in summary


def test_build_digest_isolates_section_failures(tmp_path, monkeypatch):
    # No logs, no jobs.json, no DB, doctor commands fail -> digest still produced
    monkeypatch.setattr(collect, "run_command", lambda *a, **k: (127, "boom: not found"))
    now = datetime(2026, 7, 5, 6, 40, 0)
    digest = collect.build_digest(tmp_path, tmp_path, tmp_path / "no.sqlite3", now)
    assert digest["generated_at"] == "2026-07-05T06:40:00+00:00"
    assert digest["window_hours"] == collect.WINDOW_HOURS
    assert "logs" in digest["sections"] or "logs" in digest["section_errors"]
    assert "job_intel" in digest["sections"]  # returns {"error": ...} rather than raising
    assert isinstance(digest["section_errors"], dict)


def test_build_digest_reads_logs_and_known_issues(tmp_path, monkeypatch):
    monkeypatch.setattr(collect, "run_command", lambda *a, **k: (0, ""))
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "gateway.log").write_text(
        "2026-07-05 01:00:00,000 ERROR mod: timeout after 30s\n"
        "2026-07-05 02:00:00,000 INFO agent: [MEMORY] rss=500mb\n",
        encoding="utf-8",
    )
    (logs / "errors.log").write_text("", encoding="utf-8")
    (tmp_path / "cron").mkdir()
    (tmp_path / "cron" / "jobs.json").write_text(
        '[{"id": "j1", "name": "ok-job", "enabled": true, "last_status": "ok"}]',
        encoding="utf-8",
    )
    now = datetime(2026, 7, 5, 6, 40, 0)
    digest = collect.build_digest(tmp_path, tmp_path, tmp_path / "no.sqlite3", now)
    log_section = digest["sections"]["logs"]
    assert log_section["findings"][0]["status"] == "new"
    assert log_section["memory"]["last_mb"] == 500
    assert digest["sections"]["cron_jobs"]["ok"][0]["name"] == "ok-job"
    # known-issues state persisted
    state = json.loads((tmp_path / "diagnostics" / "known-issues.json").read_text())
    assert any("timeout" in sig for sig in state)


def test_write_digest_rotates_old_copies(tmp_path):
    diag = tmp_path / "diagnostics"
    diag.mkdir()
    old = diag / "digest-2026-06-01.json"
    old.write_text("{}", encoding="utf-8")
    now = datetime(2026, 7, 5, 6, 40, 0)
    collect.write_digest({"generated_at": "x"}, diag, now)
    assert (diag / "digest-latest.json").exists()
    assert (diag / "digest-2026-07-05.json").exists()
    assert not old.exists()


def test_write_digest_accepts_timezone_aware_now_and_rotates(tmp_path):
    diag = tmp_path / "diagnostics"
    diag.mkdir()
    old = diag / "digest-2026-06-01.json"
    old.write_text("{}", encoding="utf-8")
    now = datetime(2026, 7, 5, 6, 40, tzinfo=timezone.utc)
    collect.write_digest({"generated_at": now.isoformat()}, diag, now)
    assert (diag / "digest-latest.json").exists()
    assert not old.exists()


def test_run_collection_publishes_atomic_running_then_ok_and_correlates_digest(tmp_path, monkeypatch):
    now = datetime(2026, 8, 16, 5, 0, tzinfo=timezone.utc)
    run_id = "20260816T050000Z-test"
    digest = {
        "run_id": run_id,
        "generated_at": now.isoformat(timespec="seconds"),
        "sections": {},
        "section_errors": {},
    }
    monkeypatch.setattr(collect, "build_digest", lambda *args, **kwargs: digest)
    monkeypatch.setattr(collect, "write_digest", lambda payload, directory, stamp: collect._atomic_write_json(
        directory / "digest-latest.json", payload
    ))

    assert collect.run_collection(tmp_path, tmp_path, tmp_path / "db.sqlite3", now=now, run_id=run_id) == 0

    status = json.loads((tmp_path / "diagnostics" / "collector-status.json").read_text())
    assert status["schema_version"] == "collector-status.v1"
    assert status["state"] == "ok"
    assert status["run_id"] == run_id
    assert status["exit_code"] == 0
    assert status["digest_generated_at"] == digest["generated_at"]
    assert datetime.fromisoformat(status["started_at"]).tzinfo is not None
    assert datetime.fromisoformat(status["finished_at"]).tzinfo is not None
    assert json.loads((tmp_path / "diagnostics" / "digest-latest.json").read_text())["run_id"] == run_id


def test_run_collection_marks_failed_when_digest_build_raises(tmp_path, monkeypatch):
    now = datetime(2026, 8, 16, 5, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(collect, "build_digest", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("boom")))

    assert collect.run_collection(tmp_path, tmp_path, tmp_path / "db.sqlite3", now=now, run_id="run-failed") == 1

    status = json.loads((tmp_path / "diagnostics" / "collector-status.json").read_text())
    assert status["state"] == "failed"
    assert status["run_id"] == "run-failed"
    assert status["exit_code"] == 1
    assert status["reason_code"] == "collector_exception"
    assert "digest_generated_at" not in status


def test_status_write_failure_preserves_previous_valid_status(tmp_path, monkeypatch):
    diagnostics = tmp_path / "diagnostics"
    diagnostics.mkdir()
    path = diagnostics / "collector-status.json"
    previous = {"schema_version": "collector-status.v1", "state": "ok", "run_id": "old"}
    path.write_text(json.dumps(previous), encoding="utf-8")
    monkeypatch.setattr(collect, "_atomic_write_json", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("disk full")))

    assert collect.run_collection(tmp_path, tmp_path, tmp_path / "db.sqlite3", run_id="new") == 1
    assert json.loads(path.read_text()) == previous


def test_atomic_status_replace_failure_keeps_previous_file(tmp_path, monkeypatch):
    diagnostics = tmp_path / "diagnostics"
    diagnostics.mkdir()
    path = diagnostics / "collector-status.json"
    previous_text = '{"schema_version":"collector-status.v1","state":"ok"}\n'
    path.write_text(previous_text, encoding="utf-8")

    monkeypatch.setattr(collect.os, "replace", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("rename failed")))
    with pytest.raises(OSError, match="rename failed"):
        collect.write_collector_status(diagnostics, {"state": "running"})

    assert path.read_text(encoding="utf-8") == previous_text
    assert list(diagnostics.glob(".collector-status.json.*.tmp")) == []


def test_atomic_status_fsyncs_temp_file_and_parent_directory(tmp_path, monkeypatch):
    diagnostics = tmp_path / "diagnostics"
    fsync_fds = []
    monkeypatch.setattr(collect.os, "fsync", lambda fd: fsync_fds.append(fd))

    collect.write_collector_status(diagnostics, {"state": "running"})

    assert len(fsync_fds) == 2


def test_section_errors_still_finish_collector_ok(tmp_path, monkeypatch):
    now = datetime(2026, 8, 16, 5, 0, tzinfo=timezone.utc)
    digest = {
        "run_id": "run-sections",
        "generated_at": now.isoformat(timespec="seconds"),
        "sections": {"logs": {}},
        "section_errors": {"docker": "RuntimeError: unavailable"},
    }
    monkeypatch.setattr(collect, "build_digest", lambda *args, **kwargs: digest)
    monkeypatch.setattr(collect, "write_digest", lambda payload, directory, stamp: collect._atomic_write_json(
        directory / "digest-latest.json", payload
    ))

    assert collect.run_collection(tmp_path, tmp_path, tmp_path / "db.sqlite3", now=now, run_id="run-sections") == 0
    status = json.loads((tmp_path / "diagnostics" / "collector-status.json").read_text())
    assert status["state"] == "ok"
    assert status["reason_code"] == "section_errors"
