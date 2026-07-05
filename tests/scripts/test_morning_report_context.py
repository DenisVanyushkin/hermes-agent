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
