from __future__ import annotations

from pathlib import Path

import pytest

import job_intel.product_search.gate_b as gate_b


@pytest.fixture(autouse=True)
def _isolate_product_search_job_intel_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep product-search tests away from the concurrently live Job Intel DB."""
    isolated_db = tmp_path / "job_intel.sqlite3"
    isolated_db.touch()
    monkeypatch.setenv("JOB_INTEL_DB_PATH", str(isolated_db))
    monkeypatch.setattr(gate_b, "PRODUCTION_DATABASE_PATH", isolated_db)
