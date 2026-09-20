"""Every observed role-fit verdict carries the version of the rules that produced it."""

from __future__ import annotations

import json
from typing import Any

from job_intel.observability import record_daily_observability
from job_intel.product_search.role_fit import ROLE_FIT_RULESET_VERSION
from job_intel.store import JobIntelStore

from tests.job_intel.test_role_fit_observability import (
    classification,
    evaluation,
    vacancy,
)


def test_ruleset_version_is_derived_from_the_rule_source() -> None:
    """A hand-maintained constant drifts silently; this one is a digest of the rules."""
    import hashlib
    from pathlib import Path

    import job_intel.product_search.role_fit as role_fit

    source = Path(role_fit.__file__).read_bytes()
    digest = hashlib.sha256(source).hexdigest()[:12]
    assert ROLE_FIT_RULESET_VERSION == f"rf1-{digest}"


def test_observed_verdict_records_the_ruleset_version(tmp_path) -> None:
    store = JobIntelStore(tmp_path / "ruleset-version.sqlite3")
    store.bootstrap()
    run_id = store.start_run("test")

    record_daily_observability(
        store,
        run_id,
        [(vacancy(), evaluation(), classification(), 1, False)],
    )

    with store.connect(read_only=True) as connection:
        row = connection.execute(
            "SELECT role_fit_rules_json FROM vacancy_observability"
        ).fetchone()
    payload = json.loads(row[0])
    assert payload["ruleset_version"] == ROLE_FIT_RULESET_VERSION
    assert isinstance(payload["rule_ids"], list)


def test_failed_evaluation_still_records_the_ruleset_version(tmp_path, monkeypatch) -> None:
    """An error row without a version cannot be attributed to a rule revision."""
    store = JobIntelStore(tmp_path / "ruleset-version-error.sqlite3")
    store.bootstrap()
    run_id = store.start_run("test")

    def fail_evaluate(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("role-fit fixture failure")

    monkeypatch.setattr("job_intel.observability.evaluate_role_fit", fail_evaluate)
    record_daily_observability(
        store,
        run_id,
        [(vacancy(), evaluation(), classification(), 1, False)],
    )

    with store.connect(read_only=True) as connection:
        row = connection.execute(
            "SELECT role_fit_verdict, role_fit_rules_json FROM vacancy_observability"
        ).fetchone()
    payload = json.loads(row[1])
    assert row[0] == "error"
    assert payload["ruleset_version"] == ROLE_FIT_RULESET_VERSION
    assert payload["error"] == "role-fit fixture failure"
