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


def test_render_fresh_digest(tmp_path):
    path = tmp_path / "digest-latest.json"
    path.write_text(json.dumps({"generated_at": "2026-07-05T06:40:00", "sections": {"logs": {}}}), encoding="utf-8")
    out = ctx.render(path, datetime(2026, 7, 5, 7, 10))
    assert not out.startswith("DIGEST")
    assert '"logs"' in out


def test_render_keeps_failures_when_logs_are_huge(tmp_path):
    path = tmp_path / "digest-latest.json"
    findings = [
        {
            "id": f"finding-{i}",
            "examples": [f"example-{i}-{j}-" + ("x" * 300) for j in range(3)],
        }
        for i in range(500)
    ]
    digest = {
        "generated_at": "2026-07-05T06:40:00",
        "window_hours": 24,
        "sections": {
            "cron_jobs": {
                "failed": [
                    {
                        "name": "job-intel-enrichment",
                        "output_tail": "some failure output " * 50,
                    }
                ],
                "ok": [{"name": "hermes_metrics"}],
            },
            "logs": {
                "memory": {"rss_mb": 500},
                "findings": findings,
            },
        },
    }
    path.write_text(json.dumps(digest), encoding="utf-8")
    out = ctx.render(path, datetime(2026, 7, 5, 7, 10))

    assert not out.startswith("DIGEST")
    assert "job-intel-enrichment" in out
    assert len(out) <= 24000

    cron_idx = out.index("job-intel-enrichment")
    findings_idx = out.index('"findings"')
    assert cron_idx < findings_idx
    first_finding_content_idx = out.index("finding-0")
    assert cron_idx < first_finding_content_idx


def test_render_caps_findings_and_examples(tmp_path):
    path = tmp_path / "digest-latest.json"
    findings = [
        {
            "id": f"finding-{i}",
            "examples": [f"example-{i}-{j}" for j in range(3)],
        }
        for i in range(500)
    ]
    digest = {
        "generated_at": "2026-07-05T06:40:00",
        "window_hours": 24,
        "sections": {
            "logs": {
                "memory": {"rss_mb": 500},
                "findings": findings,
            },
        },
    }
    path.write_text(json.dumps(digest), encoding="utf-8")
    out = ctx.render(path, datetime(2026, 7, 5, 7, 10))

    parsed = json.loads(out)
    surviving_findings = parsed["logs"]["findings"]
    assert len(surviving_findings) <= 30
    for finding in surviving_findings:
        assert len(finding.get("examples", [])) <= 1
    assert "findings_truncated" in parsed["logs"]
