"""Tests for the explicit, auditable source-lifecycle control CLI."""

from __future__ import annotations

import importlib.util
from datetime import datetime, timezone
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "idea_source_health.py"
SPEC = importlib.util.spec_from_file_location("idea_source_health", SCRIPT_PATH)
health = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(health)

NOW = datetime(2026, 8, 13, 5, tzinfo=timezone.utc)


def test_manual_suspend_records_reason_and_never_deletes_history():
    state = {"apa": {"effective_status": "active", "runs": 6, "accepted_items": 4}}

    updated, event = health.apply_transition(state, "apa", "suspend", "parser changed", now=NOW)

    assert updated["apa"]["effective_status"] == "suspended"
    assert updated["apa"]["runs"] == 6
    assert event == {
        "source_id": "apa", "event": "suspended", "previous_status": "active",
        "reason": "parser changed", "observed_at": NOW.isoformat(),
    }


def test_reactivate_returns_suspended_source_to_probation_and_resets_only_trial_counters():
    state = {"apa": {"effective_status": "suspended", "runs": 6, "accepted_items": 4, "recent_results": [False, False, False]}}

    updated, event = health.apply_transition(state, "apa", "reactivate", "endpoint repaired", now=NOW)

    assert updated["apa"] == {
        "effective_status": "probation", "runs": 0, "successful_runs": 0, "items_seen": 0,
        "valid_date_items": 0, "accepted_items": 0, "duplicate_items": 0, "recent_results": [],
    }
    assert event["event"] == "reactivated"
    assert event["reason"] == "endpoint repaired"


def test_reactivate_refuses_candidate_not_promoted_in_reviewed_registry(tmp_path):
    registry = tmp_path / "sources.yaml"
    registry.write_text(
        "sources:\n  - id: future_source\n    title: Future\n    status: candidate\n",
        encoding="utf-8",
    )
    state_dir = tmp_path / "state"
    health.main([
        "--registry", str(registry), "--state-dir", str(state_dir), "suspend", "future_source",
        "--reason", "technical endpoint unavailable",
    ])

    with pytest.raises(SystemExit) as exc_info:
        health.main([
            "--registry", str(registry), "--state-dir", str(state_dir), "reactivate", "future_source",
            "--reason", "endpoint repaired",
        ])

    assert exc_info.value.code == 2


def test_reviewed_candidate_demotion_resets_stale_active_admission():
    registry_state = {
        "apa": {
            "reviewed_status": "candidate",
            "effective_status": "candidate",
            "runs": 0,
            "successful_runs": 0,
            "items_seen": 0,
            "valid_date_items": 0,
            "accepted_items": 0,
            "duplicate_items": 0,
            "recent_results": [],
        }
    }
    runtime_state = {
        "apa": {
            "reviewed_status": "active",
            "effective_status": "active",
            "runs": 17,
            "successful_runs": 17,
            "items_seen": 34,
            "valid_date_items": 34,
            "accepted_items": 20,
            "duplicate_items": 4,
            "recent_results": [True] * 10,
        }
    }

    merged = health.merge_registry_and_runtime_state(registry_state, runtime_state)

    assert merged["apa"] == registry_state["apa"]


def test_transition_requires_nonempty_reason_and_known_source():
    with pytest.raises(ValueError, match="reason"):
        health.apply_transition({"apa": {}}, "apa", "suspend", "", now=NOW)
    with pytest.raises(ValueError, match="unknown source"):
        health.apply_transition({}, "missing", "suspend", "why", now=NOW)


def test_registry_state_seeds_never_run_candidate_for_manual_suspension(tmp_path):
    registry = tmp_path / "sources.yaml"
    registry.write_text(
        "sources:\n  - id: future_source\n    title: Future\n    status: candidate\n",
        encoding="utf-8",
    )

    seeded = health.load_registry_source_states(registry)

    assert seeded["future_source"]["effective_status"] == "candidate"
    updated, event = health.apply_transition(seeded, "future_source", "suspend", "quality policy", now=NOW)
    assert updated["future_source"]["effective_status"] == "suspended"
    assert event["previous_status"] == "candidate"
