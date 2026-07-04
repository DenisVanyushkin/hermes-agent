"""Tests for controlled execution report DB persistence.

Covers:
- Schema creation (table exists after SessionDB init)
- Insert/upsert by report_run_id
- Lookup by report_run_id
- Duplicate persistence does not create duplicate rows
- DB persistence stores summary fields correctly
- JSON persistence still works when DB persistence fails
- CLI get/list commands
- No raw secrets/prompt fields in summary columns
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hermes_state import SessionDB


# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture()
def db(tmp_path: Path) -> SessionDB:
    return SessionDB(tmp_path / "state.db")


_SAMPLE_PAYLOAD = {
    "schema_version": "controlled_execution_report.v1",
    "run_id": "test-run-001",
    "pipeline_session_id": "test-run-001",
    "trace_id": "trace-abc-123",
    "status": "completed",
    "workspace": {"path": "/tmp/workspace-1", "basename": "workspace-1"},
    "artifacts": {"workspace_report_path": "/tmp/w.json", "durable_report_path": "/tmp/d.json"},
    "routing": {"selected_pipeline_id": "engineering_review_pipeline"},
    "execution": {
        "execution_mode": "controlled_manual",
        "actual_execution_invoked": True,
        "files_changed_in_workspace": ["foo.py", "bar.py"],
        "final_verdict": "completed",
        "reviewer_invoked": False,
    },
    "review": {"reviewer_invoked": False, "reviewer_approved": True},
    "usage": {
        "models_used": ["gpt-5.4-mini"],
        "providers_used": ["openai-codex"],
    },
    "tests": {"status": "passed", "summary": "3/3 passed"},
    "error": {"class": None, "summary": None},
    "pipeline_execution_report": {"status": "completed"},
}


# ── Schema / table creation ───────────────────────────────────────────────


def test_controlled_execution_reports_table_exists(db: SessionDB) -> None:
    """After SessionDB init, the table should exist."""
    cur = db._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='controlled_execution_reports'"
    )
    assert cur.fetchone() is not None


# ── Insert / upsert ──────────────────────────────────────────────────────


def test_persist_and_get_report(db: SessionDB) -> None:
    db.persist_controlled_execution_report(
        report_run_id="test-run-001",
        payload=_SAMPLE_PAYLOAD,
        workspace_path="/tmp/workspace-1",
        durable_report_path="/tmp/durable/test-run-001/controlled_execution_report.json",
        workspace_report_path="/tmp/workspace-1/controlled_execution_report.json",
    )
    row = db.get_controlled_execution_report("test-run-001")
    assert row is not None
    assert row["report_run_id"] == "test-run-001"
    assert row["status"] == "completed"
    assert row["pipeline_id"] == "engineering_review_pipeline"
    assert row["execution_mode"] == "controlled_manual"
    assert row["final_verdict"] == "completed"
    assert row["tests_status"] == "passed"
    assert row["tests_summary"] == "3/3 passed"
    assert row["workspace_path"] == "/tmp/workspace-1"


def test_upsert_is_idempotent(db: SessionDB) -> None:
    """Same report_run_id upserted twice should not create duplicate rows."""
    db.persist_controlled_execution_report(
        report_run_id="run-dup",
        payload={**_SAMPLE_PAYLOAD, "run_id": "run-dup", "status": "running"},
    )
    db.persist_controlled_execution_report(
        report_run_id="run-dup",
        payload={**_SAMPLE_PAYLOAD, "run_id": "run-dup", "status": "completed"},
    )
    cur = db._conn.execute(
        "SELECT COUNT(*) FROM controlled_execution_reports WHERE report_run_id = 'run-dup'"
    )
    assert cur.fetchone()[0] == 1
    row = db.get_controlled_execution_report("run-dup")
    assert row is not None
    assert row["status"] == "completed"  # latest wins


# ── Lookup returns None for missing ──────────────────────────────────────


def test_get_returns_none_for_missing_id(db: SessionDB) -> None:
    assert db.get_controlled_execution_report("nonexistent") is None


# ── Summary fields ───────────────────────────────────────────────────────


def test_summary_fields_stored_correctly(db: SessionDB) -> None:
    db.persist_controlled_execution_report(
        report_run_id="summary-test",
        payload=_SAMPLE_PAYLOAD,
    )
    row = db.get_controlled_execution_report("summary-test")
    assert row is not None
    assert row["controller_executed"] == 1  # actual_execution_invoked -> int
    assert row["report_execution_invoked"] == 1
    assert row["reviewer_invoked"] == 0
    assert row["tests_status"] == "passed"
    assert row["tests_summary"] == "3/3 passed"


def test_changed_files_parsed_from_json(db: SessionDB) -> None:
    db.persist_controlled_execution_report(
        report_run_id="files-test",
        payload=_SAMPLE_PAYLOAD,
    )
    row = db.get_controlled_execution_report("files-test")
    assert row is not None
    assert row["changed_files"] == ["foo.py", "bar.py"]
    assert row["models_used"] == ["gpt-5.4-mini"]
    assert row["providers_used"] == ["openai-codex"]


# ── No secrets in summary columns ────────────────────────────────────────


def test_no_secrets_in_summary_columns(db: SessionDB) -> None:
    """The summary columns (status, pipeline_id, etc.) should not contain
    raw secrets, prompts, or model outputs."""
    payload_with_secrets = {
        **_SAMPLE_PAYLOAD,
        "SECRET_TOKEN": "should-not-leak",
        "raw_prompt": "do-not-store",
        "output_text": "raw model output",
        "api_key": "sk-fake123",
    }
    db.persist_controlled_execution_report(
        report_run_id="secrets-test",
        payload=payload_with_secrets,
    )
    row = db.get_controlled_execution_report("secrets-test")
    assert row is not None
    # These should not appear in any summary column
    for col in ("status", "pipeline_id", "execution_mode", "final_verdict", "tests_status", "tests_summary"):
        val = row.get(col)
        if val:
            assert "SECRET_TOKEN" not in val
            assert "sk-fake123" not in val
            assert "do-not-store" not in val


# ── List reports ─────────────────────────────────────────────────────────


def test_list_reports_returns_newest_first(db: SessionDB) -> None:
    for i in range(5):
        db.persist_controlled_execution_report(
            report_run_id=f"run-{i:03d}",
            payload={**_SAMPLE_PAYLOAD, "run_id": f"run-{i:03d}"},
        )
    reports = db.list_controlled_execution_reports(limit=3)
    assert len(reports) == 3
    # All returned items should have the expected fields
    for r in reports:
        assert "report_run_id" in r
        assert "status" in r
        assert "created_at" in r
    # Verify we got 3 out of 5
    returned_ids = {r["report_run_id"] for r in reports}
    assert len(returned_ids) == 3
    all_ids = {f"run-{i:03d}" for i in range(5)}
    assert returned_ids.issubset(all_ids)


def test_list_reports_empty(db: SessionDB) -> None:
    assert db.list_controlled_execution_reports() == []


# ── DB persistence failure is non-fatal ──────────────────────────────────


def test_db_persist_failure_is_non_fatal(monkeypatch, tmp_path: Path) -> None:
    """If the DB write fails, persist_controlled_execution_report should
    log but not raise."""
    db = SessionDB(tmp_path / "state.db")

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated DB failure")

    monkeypatch.setattr(db, "_execute_write", _boom)

    # Should not raise
    db.persist_controlled_execution_report(
        report_run_id="boom-test",
        payload=_SAMPLE_PAYLOAD,
    )

    # Should return None (no data persisted)
    assert db.get_controlled_execution_report("boom-test") is None


# ── Integration: report artifacts pipeline with DB ───────────────────────


def test_persist_report_artifacts_with_db_persistence(tmp_path: Path) -> None:
    """End-to-end: persist_controlled_execution_report_artifacts with db=."""
    from hermes_cli.pipeline_report_artifacts import (
        persist_controlled_execution_report_artifacts,
    )
    from hermes_cli.pipeline_session import PipelineSessionRequest, create_pipeline_session
    from hermes_cli.pipeline_state_machine import build_pipeline_state_snapshot
    from hermes_cli.pipeline_specs import load_pipeline_specs
    from hermes_cli.pipeline_router import RouterDecision

    db = SessionDB(tmp_path / "state.db")
    decision = RouterDecision(
        pipeline_session_id="integ-test-001",
        router_subagent_id="hermes_pipeline_router",
        status="selected",
        selected_pipeline_id="engineering_review_pipeline",
        fallback_pipeline_id="default_conversation_pipeline",
        confidence=0.93,
        reasoning_summary="test",
    )
    session = create_pipeline_session(
        request=PipelineSessionRequest(
            router_decision=decision,
            execution_mode="controlled_manual",
            platform="test",
            session_id="s1",
            user_message="test task",
            created_at="2026-06-20T00:00:00+00:00",
        )
    )
    loaded = load_pipeline_specs()
    snapshot = build_pipeline_state_snapshot(
        session=session,
        pipeline_spec=loaded.pipeline_specs["engineering_review_pipeline"],
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    durable_root = tmp_path / "durable"

    controller_payload = {
        "status": "completed",
        "actual_execution_invoked": True,
        "execution_mode": "controlled_manual",
    }
    report_payload = {
        "status": "completed",
        "completion": {"final_verdict": "completed", "completion_allowed": True},
        "changed_files": ["a.py"],
        "usage_summary": {"models_used": ["gpt-5.4-mini"], "providers_used": ["openai-codex"]},
        "tests": {"status": "passed", "summary": "1/1"},
        "review": {"reviewer_invoked": False},
    }

    metadata = persist_controlled_execution_report_artifacts(
        session=session,
        state_snapshot=snapshot,
        controller_payload=controller_payload,
        pipeline_execution_report_payload=report_payload,
        workspace_path=workspace,
        durable_root=durable_root,
        db=db,
    )

    # JSON files written
    assert metadata["workspace_report_written"] is True
    assert metadata["durable_report_written"] is True
    # DB persisted
    assert metadata["db_persisted"] is True

    # Fetch from DB
    row = db.get_controlled_execution_report("integ-test-001")
    assert row is not None
    assert row["status"] == "completed"
    assert row["pipeline_id"] == "engineering_review_pipeline"


def test_persist_report_artifacts_without_db(tmp_path: Path) -> None:
    """Without db= parameter, db_persisted should be False."""
    from hermes_cli.pipeline_report_artifacts import (
        persist_controlled_execution_report_artifacts,
    )
    from hermes_cli.pipeline_session import PipelineSessionRequest, create_pipeline_session
    from hermes_cli.pipeline_state_machine import build_pipeline_state_snapshot
    from hermes_cli.pipeline_specs import load_pipeline_specs
    from hermes_cli.pipeline_router import RouterDecision

    decision = RouterDecision(
        pipeline_session_id="no-db-test",
        router_subagent_id="hermes_pipeline_router",
        status="selected",
        selected_pipeline_id="engineering_review_pipeline",
        fallback_pipeline_id="default_conversation_pipeline",
        confidence=0.93,
        reasoning_summary="test",
    )
    session = create_pipeline_session(
        request=PipelineSessionRequest(
            router_decision=decision,
            execution_mode="controlled_manual",
            platform="test",
            session_id="s2",
            user_message="test task",
            created_at="2026-06-20T00:00:00+00:00",
        )
    )
    loaded = load_pipeline_specs()
    snapshot = build_pipeline_state_snapshot(
        session=session,
        pipeline_spec=loaded.pipeline_specs["engineering_review_pipeline"],
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    metadata = persist_controlled_execution_report_artifacts(
        session=session,
        state_snapshot=snapshot,
        controller_payload={"status": "completed", "actual_execution_invoked": True},
        pipeline_execution_report_payload={"status": "completed"},
        workspace_path=workspace,
        durable_root=None,
    )

    assert metadata["db_persisted"] is False


def test_persist_report_artifacts_db_failure_non_fatal(tmp_path: Path, monkeypatch) -> None:
    """DB write failure should not prevent JSON persistence."""
    from hermes_cli.pipeline_report_artifacts import (
        persist_controlled_execution_report_artifacts,
    )
    from hermes_cli.pipeline_session import PipelineSessionRequest, create_pipeline_session
    from hermes_cli.pipeline_state_machine import build_pipeline_state_snapshot
    from hermes_cli.pipeline_specs import load_pipeline_specs
    from hermes_cli.pipeline_router import RouterDecision

    db = SessionDB(tmp_path / "state.db")
    decision = RouterDecision(
        pipeline_session_id="db-fail-test",
        router_subagent_id="hermes_pipeline_router",
        status="selected",
        selected_pipeline_id="engineering_review_pipeline",
        fallback_pipeline_id="default_conversation_pipeline",
        confidence=0.93,
        reasoning_summary="test",
    )
    session = create_pipeline_session(
        request=PipelineSessionRequest(
            router_decision=decision,
            execution_mode="controlled_manual",
            platform="test",
            session_id="s3",
            user_message="test task",
            created_at="2026-06-20T00:00:00+00:00",
        )
    )
    loaded = load_pipeline_specs()
    snapshot = build_pipeline_state_snapshot(
        session=session,
        pipeline_spec=loaded.pipeline_specs["engineering_review_pipeline"],
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated DB failure")

    monkeypatch.setattr(db, "persist_controlled_execution_report", _boom)

    metadata = persist_controlled_execution_report_artifacts(
        session=session,
        state_snapshot=snapshot,
        controller_payload={"status": "completed", "actual_execution_invoked": True},
        pipeline_execution_report_payload={"status": "completed"},
        workspace_path=workspace,
        durable_root=None,
        db=db,
    )

    # JSON should still be written
    assert metadata["workspace_report_written"] is True
    # DB persistence failed
    assert metadata["db_persisted"] is False


# ── report_json column stores full payload ───────────────────────────────


def test_report_json_stores_full_payload(db: SessionDB) -> None:
    db.persist_controlled_execution_report(
        report_run_id="json-test",
        payload=_SAMPLE_PAYLOAD,
    )
    row = db.get_controlled_execution_report("json-test")
    assert row is not None
    raw = row.get("report_json")
    assert raw is not None
    parsed = json.loads(raw)
    assert parsed["run_id"] == "test-run-001"
    assert parsed["execution"]["actual_execution_invoked"] is True
