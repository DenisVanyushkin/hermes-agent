"""Step 5B Slice 5B-3: benchmark dataset builders + deterministic baseline.

All offline. The eligible-corpus builder is tested against a tiny synthetic
sqlite DB, never the live one.
"""
from __future__ import annotations

import json
import sqlite3

from job_intel.vacancy_understanding.semantic.benchmark.baseline import (
    run_deterministic_baseline,
)
from job_intel.vacancy_understanding.semantic.benchmark.datasets import (
    control_cases,
    decision_cases,
    eligible_cases,
    export_cases_jsonl,
    golden_cases,
    load_cases_jsonl,
)
from job_intel.vacancy_understanding.semantic.benchmark.runner import dataset_hash
from job_intel.vacancy_understanding.semantic.runtime.calibration import (
    iter_control_cases,
    run_synthetic_controls,
)


# --- controls ---------------------------------------------------------------

def test_control_case_enumeration_matches_calibration_runner():
    executed = [c for c in iter_control_cases() if c["runnable"]]
    calib = run_synthetic_controls()
    assert len(executed) == calib["pass"] + calib["fail"]
    assert len(executed) == 158  # frozen baseline size; change = new dataset id


def test_calibration_controls_still_all_pass():
    calib = run_synthetic_controls()
    assert calib["fail"] == 0
    assert calib["pass"] == 158


def test_control_dataset_is_deterministic():
    ds_id_a, cases_a = control_cases()
    ds_id_b, cases_b = control_cases()
    assert ds_id_a == ds_id_b
    assert cases_a == cases_b
    assert dataset_hash(cases_a) == dataset_hash(cases_b)
    assert len(cases_a) == 158
    assert len({c["case_id"] for c in cases_a}) == 158
    for c in cases_a:
        assert c["title"] and c["text"] is not None and c["vacancy_key"]


# --- golden fixtures --------------------------------------------------------

def test_golden_dataset_loads_all_fixtures():
    ds_id, cases = golden_cases()
    assert len(cases) == 21
    assert len({c["case_id"] for c in cases}) == 21
    for c in cases:
        assert c["title"]
        assert c["vacancy_key"]


# --- decision cases ---------------------------------------------------------

def test_decision_dataset_resolves_fixture_refs_and_skips_policy_only():
    ds_id, cases = decision_cases()
    assert len(cases) == 20  # 27 total minus 7 policy_only
    for c in cases:
        assert c["case_id"].startswith("gd_")
        assert c["title"]


# --- eligible corpus (synthetic DB) -----------------------------------------

def _make_db(path, rows):
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE vacancies (id INTEGER PRIMARY KEY, vacancy_key TEXT,"
                 " source TEXT, source_id TEXT, company TEXT, title TEXT,"
                 " location TEXT, description TEXT)")
    conn.execute("CREATE TABLE vacancy_feedback_state (vacancy_key TEXT,"
                 " feedback_type TEXT, active INTEGER, user_id TEXT)")
    conn.executemany(
        "INSERT INTO vacancies (vacancy_key, source, source_id, company, title,"
        " location, description) VALUES (?,?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()


LONG_TEXT = "Own the growth product roadmap end to end. " * 30


def test_eligible_dataset_filters_and_is_order_independent(tmp_path):
    db = tmp_path / "test.sqlite3"
    _make_db(db, [
        ("k1", "ashby", "s1", "Acme", "Head of Product", "Remote", LONG_TEXT),
        ("k2", "synthetic_control", "s2", "Test", "Fake", "Remote", LONG_TEXT),
        ("k3", "ashby", "s3", "Acme", "Head of Product", "Remote", LONG_TEXT),  # dup
        ("k4", "lever", "s4", "Beta", "VP Product", "Remote", "short"),
    ])
    ds_id, cases = eligible_cases(db_path=str(db))
    keys = {c["vacancy_key"] for c in cases}
    assert keys == {"k1"}  # k2 synthetic, k3 duplicate, k4 too short
    assert cases[0]["case_id"].startswith("e-")
    # deterministic: same DB -> identical dataset, sorted by case_id
    _, cases2 = eligible_cases(db_path=str(db))
    assert cases == cases2
    assert cases == sorted(cases, key=lambda c: c["case_id"])


def test_cases_jsonl_roundtrip_preserves_hash(tmp_path):
    db = tmp_path / "test.sqlite3"
    _make_db(db, [
        ("k1", "ashby", "s1", "Acme", "Head of Product", "Remote", LONG_TEXT),
        ("k5", "lever", "s5", "Beta", "VP Product", "Remote", LONG_TEXT),
    ])
    _, cases = eligible_cases(db_path=str(db))
    snap = tmp_path / "snapshot.jsonl"
    export_cases_jsonl(cases, snap)
    reloaded = load_cases_jsonl(snap)
    assert reloaded == cases
    assert dataset_hash(reloaded) == dataset_hash(cases)


# --- baseline orchestration -------------------------------------------------

def test_baseline_runs_selected_datasets_through_common_runner(tmp_path):
    out_root = tmp_path / "baseline"
    outcome = run_deterministic_baseline(out_root, datasets=("decision",))
    assert set(outcome) == {"decision"}
    run_dir = out_root / "decision"
    assert (run_dir / "manifest.json").exists()
    summary = json.loads((run_dir / "provider_benchmark_summary.json").read_text())
    assert summary["provider_id"] == "deterministic-phrase"
    assert summary["cases_total"] == 20
    assert summary["cases_failed"] == 0
    assert summary["cost_usd_total"]["state"] == "known_zero"
    assert summary["latency_by_mode"]["deterministic"]["case_count"] == 20
    assert outcome["decision"]["cases_total"] == 20
