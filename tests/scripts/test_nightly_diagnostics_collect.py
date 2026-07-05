import importlib.util
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
