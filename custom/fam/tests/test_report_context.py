import importlib.util
import json
from datetime import datetime, timedelta, timezone
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


def test_naive_generated_with_aware_now_measures_age_correctly(tmp_path):
    # The reverse of the case above: generated_at naive (already the
    # convention this module uses for "UTC"), "now" aware with a non-UTC
    # offset. Must not raise, and must convert "now" to UTC before
    # subtracting rather than silently discarding its offset.
    path = tmp_path / "d.json"
    generated_naive = datetime(2026, 8, 1, 3, 0)
    path.write_text(json.dumps(
        {"generated_at": generated_naive.isoformat(),
         "sections": {"errors": {"findings": [], "resolved": []}}}), encoding="utf-8")
    # Same UTC instant as generated_naive + 20h, expressed at +03:00.
    now_aware = (generated_naive + timedelta(hours=20)).replace(
        tzinfo=timezone.utc).astimezone(timezone(timedelta(hours=3)))
    out = _module().render(path, now_aware)
    assert out.startswith("DIGEST STALE")
    assert "age=20h" in out


def test_non_utc_offset_is_converted_before_age_is_measured(tmp_path):
    # Finding 3: dropping tzinfo without converting to UTC first keeps the
    # aware value's local wall-clock hour but discards its offset, so a
    # digest generated at +05:00 would look up to 5h fresher than it is.
    # Reproduce the reviewer's example: a digest that is actually 13h old
    # must not be reported as 8h old.
    path = tmp_path / "d.json"
    now_naive = datetime(2026, 8, 2, 3, 0)
    actual_generated_utc = now_naive - timedelta(hours=13)
    generated_aware = actual_generated_utc.replace(
        tzinfo=timezone.utc).astimezone(timezone(timedelta(hours=5)))
    path.write_text(json.dumps(
        {"generated_at": generated_aware.isoformat(),
         "sections": {"errors": {"findings": [], "resolved": []}}}), encoding="utf-8")
    out = _module().render(path, now_naive)
    assert out.startswith("DIGEST STALE")
    assert "age=13h" in out
    assert "age=8h" not in out


def test_resolved_is_truncated_with_a_visible_marker():
    module = _module()
    resolved = [{"signature": f"r{i}"} for i in range(module.MAX_RESOLVED + 3)]
    compact = module.compact_digest(
        {"generated_at": "2026-08-01T22:30:00", "sections":
            {"errors": {"findings": [], "resolved": resolved}}})
    section = compact["sections"]["errors"]
    assert len(section["resolved"]) == module.MAX_RESOLVED
    assert section["resolved_truncated"] == 3, "silent truncation would read as everything fixed"


def test_compact_digest_passes_other_sections_through_unchanged():
    module = _module()
    digest = {
        "generated_at": "2026-08-01T22:30:00",
        "sections": {
            "errors": {"findings": [], "resolved": []},
            "calendar": {"events": 3},
            "probes": {"ok": True},
            "timers": [{"name": "tick", "ok": True}],
            "backups": {"last_ok": "2026-08-01"},
            "maintenance_errors": [],
        },
    }
    compact = module.compact_digest(digest)
    for key in ("calendar", "probes", "timers", "backups", "maintenance_errors"):
        assert compact["sections"][key] == digest["sections"][key]


def test_oversized_digest_sheds_findings_and_stays_valid_json(tmp_path):
    module = _module()
    path = tmp_path / "d.json"
    generated = datetime(2026, 8, 1, 22, 30)
    # Well under MAX_FINDINGS so the shedding, not the per-section cap, is
    # what has to kick in; "context" is untouched by compact_digest's
    # example-truncation, so it is what blows the budget.
    findings = [{"signature": f"s{i}", "count": 1, "context": "y" * 2000}
                for i in range(25)]
    path.write_text(json.dumps(
        {"generated_at": generated.isoformat(),
         "sections": {"errors": {"findings": findings, "resolved": []}}}), encoding="utf-8")
    out = _module().render(path, generated + timedelta(hours=1))
    assert not out.startswith("DIGEST")
    parsed = json.loads(out)  # must still be valid JSON, not cut mid-token
    section = parsed["sections"]["errors"]
    assert len(section["findings"]) < 25
    assert section["findings_truncated"] > 0
    assert len(out) <= module.MAX_CHARS


def test_digest_that_cannot_fit_gets_an_explicit_hard_cut_marker(tmp_path):
    module = _module()
    path = tmp_path / "d.json"
    generated = datetime(2026, 8, 1, 22, 30)
    # No findings to shed, but a single oversized field elsewhere in the
    # digest — shedding alone cannot bring this under budget, so the hard
    # cut must fire and announce itself rather than emitting a
    # silently-clipped, unparseable tail.
    path.write_text(json.dumps(
        {"generated_at": generated.isoformat(),
         "window": "w" * (module.MAX_CHARS * 2),
         "sections": {}}), encoding="utf-8")
    out = _module().render(path, generated + timedelta(hours=1))
    assert not out.startswith("DIGEST")
    assert out.endswith("DIGEST TRUNCATED: output exceeded the prompt budget")
    assert len(out) <= module.MAX_CHARS
