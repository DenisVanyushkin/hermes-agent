from job_intel.store import JobIntelStore


def test_store_bootstrap_creates_expected_tables(tmp_path) -> None:
    db_path = tmp_path / "job_intel.sqlite3"
    store = JobIntelStore(db_path)
    store.bootstrap()

    tables = store.list_tables()

    assert {"vacancies", "vacancy_evaluations", "duplicate_links", "candidate_memory", "runs"}.issubset(set(tables))
