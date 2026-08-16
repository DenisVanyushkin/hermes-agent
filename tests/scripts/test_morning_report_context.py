import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "morning_report_context.py"
SPEC = importlib.util.spec_from_file_location("morning_report_context", SCRIPT_PATH)
ctx = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(ctx)


def _write_status(path: Path, *, state="ok", run_id="run-1", generated_at="2026-07-05T06:40:00+00:00", started_at=None, finished_at=None, reason_code=None):
    started_at = started_at or generated_at
    finished_at = finished_at or generated_at
    payload = {
        "schema_version": "collector-status.v1",
        "state": state,
        "run_id": run_id,
        "started_at": started_at,
        "finished_at": finished_at,
        "exit_code": 0 if state == "ok" else 1,
        "reason_code": reason_code,
        "digest_generated_at": generated_at,
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_render_missing_digest(tmp_path):
    out = ctx.render(tmp_path / "digest-latest.json", datetime(2026, 7, 5, 7, 10))
    assert out.startswith("DIGEST MISSING")


def test_render_stale_digest(tmp_path):
    path = tmp_path / "digest-latest.json"
    generated = "2026-07-04T06:40:00+00:00"
    path.write_text(json.dumps({"run_id": "run-old", "generated_at": generated, "sections": {}}), encoding="utf-8")
    _write_status(path.with_name("collector-status.json"), run_id="run-old", generated_at=generated)
    out = ctx.render(path, datetime(2026, 7, 5, 7, 10, tzinfo=timezone.utc))
    assert out.startswith("DIGEST STALE")
    assert '"generated_at"' in out  # stale digest is still included
    assert len(out) <= ctx.MAX_CHARS


def test_render_fresh_digest(tmp_path):
    path = tmp_path / "digest-latest.json"
    generated = "2026-07-05T06:40:00+00:00"
    path.write_text(json.dumps({"run_id": "run-fresh", "generated_at": generated, "sections": {"logs": {}}}), encoding="utf-8")
    _write_status(path.with_name("collector-status.json"), run_id="run-fresh", generated_at=generated)
    out = ctx.render(path, datetime(2026, 7, 5, 7, 10, tzinfo=timezone.utc))
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
    digest = _huge_digest()
    digest["run_id"] = "run-huge"
    digest["generated_at"] = "2026-07-05T06:40:00+00:00"
    path.write_text(json.dumps(digest), encoding="utf-8")
    _write_status(path.with_name("collector-status.json"), run_id="run-huge")
    out = ctx.render(path, datetime(2026, 7, 5, 7, 10, tzinfo=timezone.utc))
    assert "job-intel-enrichment" in out
    assert len(out) <= 24000
    assert out.index("job-intel-enrichment") < out.index("log-finding-pattern")


def test_render_caps_findings_and_examples(tmp_path):
    path = tmp_path / "digest-latest.json"
    digest = _huge_digest()
    digest["run_id"] = "run-huge"
    digest["generated_at"] = "2026-07-05T06:40:00+00:00"
    path.write_text(json.dumps(digest), encoding="utf-8")
    _write_status(path.with_name("collector-status.json"), run_id="run-huge")
    out = ctx.render(path, datetime(2026, 7, 5, 7, 10, tzinfo=timezone.utc))
    compact = ctx.compact_digest(digest)
    logs = compact["sections"]["logs"]
    assert len(logs["findings"]) <= 30
    assert all(len(f.get("examples", [])) <= 1 for f in logs["findings"])
    assert all(len(ex) <= 200 for f in logs["findings"] for ex in f.get("examples", []))
    assert logs["findings_truncated"] == 470
    assert '"findings_truncated"' in out


def test_render_missing_or_corrupt_collector_status_is_explicit(tmp_path):
    path = tmp_path / "digest-latest.json"
    generated = "2026-07-05T06:40:00+00:00"
    path.write_text(json.dumps({"run_id": "run-1", "generated_at": generated, "sections": {}}), encoding="utf-8")

    assert ctx.render(path, datetime(2026, 7, 5, 7, 10, tzinfo=timezone.utc)).startswith(
        "COLLECTOR STATUS MISSING"
    )
    path.with_name("collector-status.json").write_text("{broken", encoding="utf-8")
    assert ctx.render(path, datetime(2026, 7, 5, 7, 10, tzinfo=timezone.utc)).startswith(
        "COLLECTOR STATUS MISSING"
    )


def test_render_failed_collector_is_explicit(tmp_path):
    path = tmp_path / "digest-latest.json"
    generated = "2026-07-05T06:40:00+00:00"
    path.write_text(json.dumps({"run_id": "run-failed", "generated_at": generated, "sections": {}}), encoding="utf-8")
    _write_status(
        path.with_name("collector-status.json"),
        state="failed",
        run_id="run-failed",
        generated_at=generated,
        reason_code="collector_exception",
    )

    out = ctx.render(path, datetime(2026, 7, 5, 7, 10, tzinfo=timezone.utc))
    assert out.startswith("COLLECTOR FAILED")
    assert "collector_exception" in out


def test_render_stuck_running_collector_is_explicit(tmp_path):
    path = tmp_path / "digest-latest.json"
    generated = "2026-07-05T01:00:00+00:00"
    path.write_text(json.dumps({"run_id": "run-stuck", "generated_at": generated, "sections": {}}), encoding="utf-8")
    _write_status(
        path.with_name("collector-status.json"),
        state="running",
        run_id="run-stuck",
        generated_at=generated,
        started_at="2026-07-05T01:00:00+00:00",
        finished_at=None,
    )
    # A running status is not healthy even when its digest happens to be fresh.
    status_path = path.with_name("collector-status.json")
    payload = json.loads(status_path.read_text())
    payload.pop("finished_at", None)
    status_path.write_text(json.dumps(payload), encoding="utf-8")

    out = ctx.render(path, datetime(2026, 7, 5, 7, 10, tzinfo=timezone.utc))
    assert out.startswith("COLLECTOR STUCK")


def test_render_rejects_fresh_digest_with_mismatched_run_metadata(tmp_path):
    path = tmp_path / "digest-latest.json"
    generated = "2026-07-05T06:40:00+00:00"
    path.write_text(json.dumps({"run_id": "run-digest", "generated_at": generated, "sections": {}}), encoding="utf-8")
    _write_status(path.with_name("collector-status.json"), run_id="run-status", generated_at=generated)

    out = ctx.render(path, datetime(2026, 7, 5, 7, 10, tzinfo=timezone.utc))
    assert out.startswith("DIGEST STALE")
    assert "run metadata" in out


def test_render_uses_timezone_offsets_and_two_hour_schedule_window(tmp_path):
    path = tmp_path / "digest-latest.json"
    generated = "2026-07-05T08:40:00+02:00"  # same instant as 06:40 UTC
    path.write_text(json.dumps({"run_id": "run-offset", "generated_at": generated, "sections": {}}), encoding="utf-8")
    _write_status(path.with_name("collector-status.json"), run_id="run-offset", generated_at=generated)

    fresh = ctx.render(path, datetime(2026, 7, 5, 7, 10, tzinfo=timezone.utc))
    assert not fresh.startswith("DIGEST")
    stale = ctx.render(path, datetime(2026, 7, 5, 9, 11, tzinfo=timezone.utc))
    assert stale.startswith("DIGEST STALE")


def test_main_passes_timezone_aware_local_time_to_renderer(monkeypatch, tmp_path):
    captured = {}

    class FakeDateTime:
        @classmethod
        def now(cls):
            return datetime(2026, 7, 5, 9, 10, tzinfo=timezone(timedelta(hours=2)))

    def fake_render(path, now):
        captured["path"] = path
        captured["now"] = now
        return "ok"

    monkeypatch.setattr(ctx, "datetime", FakeDateTime)
    monkeypatch.setattr(ctx, "render", fake_render)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    assert ctx.main() == 0
    assert captured["now"].tzinfo is not None
    # ``astimezone()`` may render the host's local offset (UTC in CI), but it
    # must preserve the same instant as the supplied +02:00 local clock.
    expected = datetime(2026, 7, 5, 9, 10, tzinfo=timezone(timedelta(hours=2)))
    assert captured["now"].timestamp() == expected.timestamp()
