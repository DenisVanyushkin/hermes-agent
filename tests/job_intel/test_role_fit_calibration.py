"""The calibration report must surface the classes an owner can actually dispute."""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[2] / "scripts" / "job_intel_role_fit_calibration.py"
spec = importlib.util.spec_from_file_location("job_intel_role_fit_calibration", MODULE_PATH)
assert spec and spec.loader
calibration = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = calibration
spec.loader.exec_module(calibration)


def _db(tmp_path: Path) -> Path:
    path = tmp_path / "calibration.sqlite3"
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE vacancy_observability (
            run_id INTEGER, vacancy_key TEXT, company TEXT, title TEXT, location TEXT,
            source TEXT, canonical_url TEXT, role_fit_verdict TEXT,
            role_fit_rules_json TEXT, accepted INTEGER
        )
        """
    )
    rows = [
        (7, "k1", "Acme", "VP Product", "London", "LinkedIn", "u1", "accept",
         json.dumps({"ruleset_version": "rf1-test", "rule_ids": ["software_product_leadership"]}), 1),
        (7, "k2", "Beta", "Product Marketing Lead", "Berlin", "Greenhouse", "u2", "reject",
         json.dumps({"ruleset_version": "rf1-test", "rule_ids": ["adjacent_product_function"]}), 1),
        (7, "k3", "Gamma", "Head of Product", "Remote - USA", "Ashby", "u3", "blocked",
         json.dumps({"ruleset_version": "rf1-test", "rule_ids": ["us_remote_eligibility_unknown"]}), 0),
        (7, "k4", "Delta", "Head of Product", "Paris", "Lever", "u4", "reject",
         json.dumps({"ruleset_version": "rf1-test",
                     "rule_ids": ["description_language_not_supported", "short_contract"]}), 0),
        (6, "k5", "Older", "VP Product", "Madrid", "LinkedIn", "u5", "accept",
         json.dumps({"ruleset_version": "rf1-test", "rule_ids": ["software_product_leadership"]}), 1),
    ]
    conn.executemany("INSERT INTO vacancy_observability VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()
    return path


def test_latest_run_is_chosen_when_none_is_given(tmp_path) -> None:
    conn = calibration.connect_read_only(_db(tmp_path))
    assert calibration.latest_run_id(conn) == 7


def test_report_separates_delivery_single_rule_rejects_and_blocked(tmp_path) -> None:
    conn = calibration.connect_read_only(_db(tmp_path))
    report = calibration.build_report(conn, 7)

    assert [item["title"] for item in report["would_deliver"]] == ["VP Product"]
    # A reject decided by one rule is disputable; one decided by two is not the
    # cheapest place to ask, so it stays out of the sample.
    assert [item["title"] for item in report["single_rule_rejects"]] == ["Product Marketing Lead"]
    assert [item["title"] for item in report["blocked_unresolved"]] == ["Head of Product"]
    assert report["counts"]["verdicts"] == {"accept": 1, "reject": 2, "blocked": 1}
    assert report["ruleset_versions"] == {"rf1-test": 4}


def test_disagreements_with_the_legacy_pipeline_are_counted(tmp_path) -> None:
    conn = calibration.connect_read_only(_db(tmp_path))
    report = calibration.build_report(conn, 7)
    # k2 was accepted by the legacy pipeline and rejected here.
    assert report["counts"]["disagreements_with_legacy"] == 1


def test_rendered_text_names_every_section_the_owner_must_answer(tmp_path) -> None:
    conn = calibration.connect_read_only(_db(tmp_path))
    text = calibration.render_text(calibration.build_report(conn, 7))
    for heading in ("WOULD DELIVER", "SINGLE-RULE REJECTS", "BLOCKED, UNRESOLVED", "TOP RULES"):
        assert heading in text
    assert "rf1-test" in text


def test_legacy_list_shaped_rules_json_is_still_readable(tmp_path) -> None:
    """Rows written before the versioned payload must not crash the report."""
    path = _db(tmp_path)
    conn = sqlite3.connect(path)
    conn.execute(
        "INSERT INTO vacancy_observability VALUES (7,'k6','Legacy','VP Product','Oslo','LinkedIn','u6','accept',?,1)",
        (json.dumps(["software_product_leadership"]),),
    )
    conn.commit()
    conn.close()

    report = calibration.build_report(calibration.connect_read_only(path), 7)
    assert report["ruleset_versions"] == {"rf1-test": 4}
    assert len(report["would_deliver"]) == 2
