import importlib.util
import sqlite3
from datetime import datetime
from pathlib import Path

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
            ("run-new", "salary_unknown", "unknown", "low"),
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
    assert summary["top_rejections"][0] == {"reason": "low_seniority", "count": 2}


def test_job_intel_summary_missing_db_reports_error(tmp_path):
    summary = collect.job_intel_summary(tmp_path / "absent.sqlite3")
    assert "error" in summary
