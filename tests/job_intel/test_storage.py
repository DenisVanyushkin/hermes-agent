import json

from job_intel.store import JobIntelStore


def test_store_bootstrap_creates_expected_tables(tmp_path) -> None:
    db_path = tmp_path / "job_intel.sqlite3"
    store = JobIntelStore(db_path)
    store.bootstrap()

    tables = store.list_tables()

    assert {"vacancies", "vacancy_evaluations", "duplicate_links", "candidate_memory", "runs", "company_intelligence", "company_intelligence_events", "strategic_signals", "strategic_predictions"}.issubset(set(tables))


def test_store_start_run_persists_runtime_provenance(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "job_intel.sqlite3"
    store = JobIntelStore(db_path)
    store.bootstrap()

    provenance = {
        "whoami": "pn",
        "hostname": "host-1",
        "pwd": "/tmp/cwd",
        "effective_workdir": "/workspace/live-hermes",
        "git_commit_hash": "abc123",
        "python_executable": "/usr/bin/python3",
        "db_path": str(db_path),
        "state_dir": "/root/.hermes/job_intel",
        "browser_profile_paths": {"linkedin": "/profiles/linkedin", "headhunter": "/profiles/hh"},
        "env_overrides": {"JOB_INTEL_DB_PATH": str(db_path)},
        "runtime_mirror_paths": {"resolved_scripts_dir": "/root/.hermes/scripts"},
        "imported_module_locations": {"job_intel.runtime": "/workspace/live-hermes/job_intel/runtime.py"},
        "sys_path": ["/workspace/live-hermes"],
    }
    monkeypatch.setattr("job_intel.store.capture_runtime_provenance", lambda **kwargs: provenance)

    run_id = store.start_run("daily", metadata={"source_statuses": {"linkedin": {"status": "ok"}}})
    row = store.latest_run()

    assert run_id == row["id"]
    assert json.loads(row["provenance_json"]) == provenance
    assert json.loads(row["metadata_json"]) == {"source_statuses": {"linkedin": {"status": "ok"}}}
