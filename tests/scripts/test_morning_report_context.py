import importlib.util
import json
from datetime import datetime
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "morning_report_context.py"
SPEC = importlib.util.spec_from_file_location("morning_report_context", SCRIPT_PATH)
ctx = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(ctx)


def test_render_missing_digest(tmp_path):
    out = ctx.render(tmp_path / "digest-latest.json", datetime(2026, 7, 5, 7, 10))
    assert out.startswith("DIGEST MISSING")


def test_render_stale_digest(tmp_path):
    path = tmp_path / "digest-latest.json"
    path.write_text(json.dumps({"generated_at": "2026-07-04T06:40:00", "sections": {}}), encoding="utf-8")
    out = ctx.render(path, datetime(2026, 7, 5, 7, 10))
    assert out.startswith("DIGEST STALE")
    assert '"generated_at"' in out  # stale digest is still included
    assert len(out) <= ctx.MAX_CHARS


def test_render_fresh_digest(tmp_path):
    path = tmp_path / "digest-latest.json"
    path.write_text(json.dumps({"generated_at": "2026-07-05T06:40:00", "sections": {"logs": {}}}), encoding="utf-8")
    out = ctx.render(path, datetime(2026, 7, 5, 7, 10))
    assert not out.startswith("DIGEST")
    assert '"logs"' in out


def _huge_digest():
    findings = [
        {
            "pattern": f"log-finding-pattern-{i}",
            "count": i,
            "examples": ["x" * 300, "y" * 300, "z" * 300],
        }
        for i in range(500)
    ]
    return {
        "generated_at": "2026-07-05T06:40:00",
        "window_hours": 24,
        "section_errors": {},
        "sections": {
            "cron_jobs": {
                "ok": [{"name": "some-ok-job", "id": "abc"}],
                "failed": [{"name": "job-intel-enrichment", "status": "error", "output_tail": "e" * 2000}],
                "paused": [],
            },
            "logs": {"memory": {"trend": "flat"}, "findings": findings, "resolved": [f"r{i}" for i in range(50)]},
        },
    }


def test_render_keeps_failures_when_logs_are_huge(tmp_path):
    path = tmp_path / "digest-latest.json"
    path.write_text(json.dumps(_huge_digest()), encoding="utf-8")
    out = ctx.render(path, datetime(2026, 7, 5, 7, 10))
    assert "job-intel-enrichment" in out
    assert len(out) <= 24000
    assert out.index("job-intel-enrichment") < out.index("log-finding-pattern")


def test_render_caps_findings_and_examples(tmp_path):
    path = tmp_path / "digest-latest.json"
    path.write_text(json.dumps(_huge_digest()), encoding="utf-8")
    out = ctx.render(path, datetime(2026, 7, 5, 7, 10))
    compact = ctx.compact_digest(_huge_digest())
    logs = compact["sections"]["logs"]
    assert len(logs["findings"]) <= 30
    assert all(len(f.get("examples", [])) <= 1 for f in logs["findings"])
    assert all(len(ex) <= 200 for f in logs["findings"] for ex in f.get("examples", []))
    assert logs["findings_truncated"] == 470
    assert '"findings_truncated"' in out
