import importlib.util
import json
from datetime import datetime, timedelta
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "fam_report_context.py"


def _module():
    # Loaded by path, not imported as a package: the script must stay
    # self-contained because the agent runs it with workdir=hermes-agent,
    # where `fam` is not on sys.path.
    spec = importlib.util.spec_from_file_location("fam_report_context", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_missing_file_is_reported(tmp_path):
    out = _module().render(tmp_path / "absent.json", datetime(2026, 8, 2, 3, 0))
    assert out.startswith("DIGEST MISSING")


def test_stale_digest_is_flagged(tmp_path):
    path = tmp_path / "d.json"
    generated = datetime(2026, 8, 1, 3, 0)
    path.write_text(json.dumps({"generated_at": generated.isoformat(),
                                "sections": {}}), encoding="utf-8")
    out = _module().render(path, generated + timedelta(hours=20))
    assert out.startswith("DIGEST STALE")
    assert "age=20h" in out


def test_fresh_digest_passes_through(tmp_path):
    path = tmp_path / "d.json"
    generated = datetime(2026, 8, 1, 22, 30)
    path.write_text(json.dumps(
        {"generated_at": generated.isoformat(),
         "sections": {"errors": {"findings": [], "resolved": []}}}), encoding="utf-8")
    out = _module().render(path, generated + timedelta(hours=4))
    assert not out.startswith("DIGEST")
    assert json.loads(out)["sections"]["errors"] == {"findings": [], "resolved": []}


def test_findings_are_truncated_with_a_visible_marker(tmp_path):
    module = _module()
    findings = [{"signature": f"s{i}", "count": 1, "examples": ["x" * 500]}
                for i in range(module.MAX_FINDINGS + 5)]
    compact = module.compact_digest(
        {"generated_at": "2026-08-01T22:30:00", "sections":
            {"errors": {"findings": findings, "resolved": []}}})
    section = compact["sections"]["errors"]
    assert len(section["findings"]) == module.MAX_FINDINGS
    assert section["findings_truncated"] == 5, "silent truncation would read as full coverage"
    assert len(section["findings"][0]["examples"][0]) == module.EXAMPLE_CHARS


def test_aware_generated_at_with_naive_now_is_handled(tmp_path):
    # fam writes generated_at with a UTC offset (aware); main() calls
    # datetime.now() (naive). Subtracting aware - naive raises TypeError
    # unless render() normalizes one side. This covers the direction the
    # brief's own tests don't: aware file, naive "now".
    path = tmp_path / "d.json"
    generated_naive = datetime(2026, 8, 1, 22, 30)
    generated_aware_iso = generated_naive.isoformat() + "+00:00"
    path.write_text(json.dumps(
        {"generated_at": generated_aware_iso,
         "sections": {"errors": {"findings": [], "resolved": []}}}), encoding="utf-8")
    out = _module().render(path, generated_naive + timedelta(hours=4))
    assert not out.startswith("DIGEST")
    assert json.loads(out)["sections"]["errors"] == {"findings": [], "resolved": []}
