from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3

import pytest

from job_intel.product_search.acquisition_probe import (
    ProbeSourceBlocked,
    SourceIsolation,
    EXCLUSION_REASON_CATALOG,
    build_isolated_probe_environment,
    build_experiment_manifest,
    build_snapshot_queries,
    expand_queries,
    ProbeQuery,
    LinkedInExecutionPlan,
    resolve_public_sources,
    run_probe,
    validate_gate_a_run_evidence,
    validate_experiment_manifest,
    validate_probe_output_path,
)
from job_intel.product_search.search_contract import load_search_contract
import job_intel.product_search.acquisition_probe as acquisition_probe


ROOT = Path(__file__).resolve().parents[2]


def test_experiment_manifest_binds_exclusion_reason_catalog(tmp_path: Path) -> None:
    root = tmp_path / "experiment"
    runtime = root / "runtime"
    python_runtime = root / "python-runtime"
    stdlib_root = root / "stdlib"
    runtime.mkdir(parents=True)
    python_runtime.mkdir()
    stdlib_root.mkdir()
    executable = python_runtime / "bin/python"
    executable.parent.mkdir()
    executable.write_text("python", encoding="utf-8")
    (runtime / "uv.lock").write_text("lock", encoding="utf-8")
    (python_runtime / "installed-distributions.txt").write_text("", encoding="utf-8")

    manifest = build_experiment_manifest(
        root=root,
        commit="a" * 40,
        python_executable=executable,
        python_version="3.12.0",
        stdlib_root=stdlib_root,
        sys_path=(str(runtime),),
    )

    assert manifest["exclusion_reason_codes"] == {
        "version": EXCLUSION_REASON_CATALOG.version,
        "sha256": EXCLUSION_REASON_CATALOG.sha256,
    }

    wrong_version = dict(manifest)
    wrong_version["exclusion_reason_codes"] = {
        **manifest["exclusion_reason_codes"],
        "version": "unreviewed",
    }
    with pytest.raises(ValueError, match="exclusion reason catalog"):
        validate_experiment_manifest(wrong_version)


def _ready_checks(*families: str) -> dict[str, object]:
    """Browser families need a capability answer before dispatch.

    These tests are about deduplication and failure recording, not about the
    runtime gate, so they declare the runtime ready explicitly rather than
    relying on an absent check being treated as permission — which it is not.
    """
    from job_intel.product_search.acquisition_probe import RuntimeCapabilityResult

    return {family: (lambda: RuntimeCapabilityResult(state="ready")) for family in families}

def test_query_expansion_is_deterministic_and_preserves_cell_family_identity() -> None:
    contract = load_search_contract(ROOT / "config/product_search/search_contract.v1.yaml")

    first = expand_queries(contract, role_terms=("VP Product", "Chief Product Officer"))
    second = expand_queries(contract, role_terms=("VP Product", "Chief Product Officer"))

    assert first == second
    assert first
    assert len({query.query_id for query in first}) == len(first)
    assert all(query.cell_id and query.source_family and query.query for query in first)
    assert [query.query_id for query in first] == sorted(query.query_id for query in first)


def test_ats_snapshot_expansion_runs_each_available_family_once() -> None:
    queries = build_snapshot_queries()

    assert {query.source_family for query in queries} == {
        "ashby",
        "greenhouse",
        "lever",
        "personio",
        "recruitee",
        "smartrecruiters",
        "teamtailor",
    }
    assert len(queries) == 7
    assert {query.cell_id for query in queries} == {"ats_global_snapshot"}


def test_probe_writes_content_addressed_evidence_and_deduplicates_canonical_urls(tmp_path: Path) -> None:
    calls: list[str] = []

    def fake_source(query: str):
        calls.append(query)
        return [
            {
                "source_id": "1",
                "url": "https://example.com/jobs/1?utm_source=test",
                "title": "VP Product",
                "company": "Acme",
                "description": "Own product and P&L",
                "captured_at": "2026-08-11T00:00:00Z",
            },
            {
                "source_id": "2",
                "url": "https://example.com/jobs/1#duplicate",
                "title": "VP Product",
                "company": "Acme",
                "description": "Own product and P&L",
                "captured_at": "2026-08-11T00:00:00Z",
            },
        ]

    result = run_probe(
        run_id="run-1",
        queries=(
            {
                "query_id": "q1",
                "cell_id": "kazakhstan",
                "source_family": "fake",
                "query": "VP Product Kazakhstan",
            },
        ),
        sources={"fake": fake_source},
        output_dir=tmp_path / "probe",
        isolation={"fake": SourceIsolation(mode="exclusive_lock", path=tmp_path / "fake.lock", collection_method="api")},
        now=lambda: datetime(2026, 8, 11, tzinfo=timezone.utc),
    )

    assert calls == ["VP Product Kazakhstan"]
    assert result.stage_counts == {"raw_observed": 2, "canonical_current": 1, "minimum_evidence_sufficient": 1}
    assert result.duplicates == 1
    assert result.provisional_labels == {"provisionally_eligible": 1}
    assert "hard_gate_eligible" not in result.model_dump_json()
    assert "verdict" not in result.model_dump_json()
    with sqlite3.connect(tmp_path / "probe" / "experiment.sqlite3") as conn:
        assert conn.execute("SELECT COUNT(*) FROM probe_evidence").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM probe_runs").fetchone()[0] == 1
    for evidence in result.evidence:
        raw_path = tmp_path / "probe" / evidence.raw_reference
        assert raw_path.is_file()
        assert raw_path.stem == evidence.raw_content_sha256
        assert evidence.query_id == "q1"
        assert evidence.source_family == "fake"
        assert evidence.redaction_class == "vacancy_public_evidence"


def test_probe_deduplicates_linkedin_and_headhunter_tracking_urls(tmp_path: Path) -> None:
    records = {
        "linkedin": (
            {
                "source_id": "linkedin-1",
                "url": (
                    "https://www.linkedin.com/jobs/view/4450161759/"
                    "?eBP=FIRST&refId=one&trackingId=one&trk=search"
                ),
                "title": "Head of Product",
                "company": "Acme",
                "description": "Own product strategy",
            },
            {
                "source_id": "linkedin-2",
                "url": (
                    "https://www.linkedin.com/jobs/view/4450161759/"
                    "?eBP=SECOND&refId=two&trackingId=two&trk=search"
                ),
                "title": "Head of Product",
                "company": "Acme",
                "description": "Own product strategy",
            },
        ),
        "headhunter": (
            {
                "source_id": "hh-1",
                "url": (
                    "https://hh.ru/vacancy/135365534"
                    "?query=Chief+Product+Officer&amp;hhtmFrom=vacancy_search_list"
                ),
                "title": "Chief Product Officer",
                "company": "Acme",
                "description": "Own product strategy",
            },
            {
                "source_id": "hh-2",
                "url": (
                    "https://hh.ru/vacancy/135365534"
                    "?query=Head+of+Product&amp;hhtmFrom=vacancy_search_list"
                ),
                "title": "Chief Product Officer",
                "company": "Acme",
                "description": "Own product strategy",
            },
        ),
    }
    queries = tuple(
        {
            "query_id": f"q-{family}",
            "cell_id": "kazakhstan",
            "source_family": family,
            "query": "product leader",
        }
        for family in records
    )
    sources = {family: (lambda _query, rows=rows: rows) for family, rows in records.items()}
    isolation = {
        family: SourceIsolation(mode="exclusive_lock", path=tmp_path / f"{family}.lock", collection_method="api")
        for family in records
    }

    result = run_probe(
        run_id="run-tracking-urls",
        queries=queries,
        sources=sources,
        runtime_capability_checks=_ready_checks(*records),
        output_dir=tmp_path / "probe",
        isolation=isolation,
    )

    assert result.stage_counts["raw_observed"] == 4
    assert result.stage_counts["canonical_current"] == 2
    assert result.duplicates == 2


def test_probe_names_auth_antibot_rate_limit_and_unresolved_evidence(tmp_path: Path) -> None:
    attempts = 0

    def blocked(_query: str):
        nonlocal attempts
        attempts += 1
        raise ProbeSourceBlocked("anti_bot", "challenge page")

    result = run_probe(
        run_id="run-2",
        queries=(
            {"query_id": "q1", "cell_id": "uk", "source_family": "blocked", "query": "CPO UK"},
        ),
        sources={"blocked": blocked},
        output_dir=tmp_path / "probe",
        isolation={"blocked": SourceIsolation(mode="exclusive_lock", path=tmp_path / "blocked.lock", collection_method="api")},
        max_attempts=2,
    )

    assert attempts == 2
    assert result.source_states == {"blocked": "blocked_anti_bot"}
    assert result.stage_counts == {"raw_observed": 0, "canonical_current": 0, "minimum_evidence_sufficient": 0}


def test_source_without_clone_or_exclusive_lock_is_blocked_without_invocation(tmp_path: Path) -> None:
    called = False

    def source(_query: str):
        nonlocal called
        called = True
        return []

    result = run_probe(
        run_id="run-3",
        queries=(
            {"query_id": "q1", "cell_id": "uk", "source_family": "unsafe", "query": "VP Product"},
        ),
        sources={"unsafe": source},
        output_dir=tmp_path / "probe",
        isolation={"unsafe": SourceIsolation(mode="blocked", path=None)},
    )

    assert called is False
    assert result.source_states == {"unsafe": "blocked_no_safe_isolation"}


def test_probe_rejects_production_paths_and_slack_credentials(tmp_path: Path) -> None:
    query = ({"query_id": "q1", "cell_id": "uk", "source_family": "fake", "query": "VP Product"},)
    source = {"fake": lambda _query: []}
    isolation = {"fake": SourceIsolation(mode="exclusive_lock", path=tmp_path / "lock", collection_method="api")}

    for forbidden in (
        Path("/var/lib/job-intel/state"),
        Path("/home/hermes/.hermes/job_intel"),
        Path("/home/hermes/.hermes/hermes-agent/.worktrees/job-intel-product-search"),
    ):
        try:
            run_probe(run_id="x", queries=query, sources=source, output_dir=forbidden, isolation=isolation)
        except ValueError as exc:
            assert "forbidden probe path" in str(exc)
        else:
            raise AssertionError(f"accepted production path: {forbidden}")

    try:
        run_probe(
            run_id="x",
            queries=query,
            sources=source,
            output_dir=tmp_path / "probe",
            isolation=isolation,
            environment={"SLACK_BOT_TOKEN": "secret"},
        )
    except ValueError as exc:
        assert "Slack credentials are forbidden" in str(exc)
    else:
        raise AssertionError("accepted Slack credentials")


def test_output_path_allows_only_direct_gate_a_commit_root() -> None:
    approved = Path("/home/hermes/.hermes/job_intel/experiments/gate-a") / ("a" * 40)

    validate_probe_output_path(approved)

    for forbidden in (
        Path("/home/hermes/.hermes/job_intel/experiments/gate-a/not-a-commit"),
        approved / "nested",
        Path("/home/hermes/.hermes/job_intel/job_intel.sqlite3"),
    ):
        try:
            validate_probe_output_path(forbidden)
        except ValueError as exc:
            assert "forbidden probe path" in str(exc)
        else:
            raise AssertionError(f"accepted unsafe Gate A path: {forbidden}")


def test_probe_registry_exposes_only_existing_public_scraper_interfaces() -> None:
    sources = resolve_public_sources()

    assert set(sources) == {
        "ashby",
        "duckduckgo",
        "greenhouse",
        "headhunter",
        "lever",
        "linkedin",
        "personio",
        "recruitee",
        "remoteok",
        "remotive",
        "smartrecruiters",
        "teamtailor",
    }
    assert all(callable(source) for source in sources.values())


def test_isolated_environment_overrides_ambient_production_paths(tmp_path: Path) -> None:
    root = tmp_path / "gate-a" / ("a" * 40)
    manifest = {
        "root": str(root),
        "paths": {
            "experiment.sqlite3": str(root / "experiment.sqlite3"),
            "browser-profile": str(root / "browser-profile"),
            "cache": str(root / "cache"),
            "logs": str(root / "logs"),
            "tmp": str(root / "tmp"),
        },
        "python": {"executable_path": str(root / "python-runtime/venv/bin/python")},
        "source_isolation": {
            "linkedin": {
                "mode": "cloned_profile",
                "path": str(root / "browser-profile/linkedin"),
            },
        },
    }

    environment = build_isolated_probe_environment(
        manifest,
        ambient={
            "JOB_INTEL_DB_PATH": "/var/lib/job-intel/state/job_intel.sqlite3",
            "JOB_INTEL_BROWSER_PROFILE_DIR": "/var/lib/browser-desktop/profiles",
            "SLACK_BOT_TOKEN": "must-still-fail-closed",
        },
    )

    assert environment["JOB_INTEL_DB_PATH"] == str(root / "experiment.sqlite3")
    assert environment["JOB_INTEL_BROWSER_PROFILE_DIR_LINKEDIN"] == str(
        root / "browser-profile/linkedin"
    )
    assert "JOB_INTEL_BROWSER_PROFILE_DIR_HH" not in environment
    assert environment["JOB_INTEL_BROWSER_PYTHON"] == str(
        root / "python-runtime/venv/bin/python"
    )
    assert environment["XDG_CACHE_HOME"] == str(root / "cache")
    assert environment["TMPDIR"] == str(root / "tmp")
    assert environment["SLACK_BOT_TOKEN"] == "must-still-fail-closed"
    for key, value in environment.items():
        if key.startswith("JOB_INTEL_") and key.endswith(("_PATH", "_DIR")):
            assert not value.startswith("/var/lib/job-intel/state")
            assert not value.startswith("/var/lib/browser-desktop")


def test_owner_approved_shared_profiles_require_experiment_local_backups(tmp_path: Path) -> None:
    root = tmp_path / "gate-a" / ("a" * 40)
    linkedin_backup = root / "browser-profile-backup/linkedin"
    linkedin_backup.mkdir(parents=True)
    manifest = {
        "root": str(root),
        "paths": {
            "experiment.sqlite3": str(root / "experiment.sqlite3"),
            "browser-profile": str(root / "browser-profile"),
            "cache": str(root / "cache"),
            "logs": str(root / "logs"),
            "tmp": str(root / "tmp"),
        },
        "python": {"executable_path": str(root / "python-runtime/venv/bin/python")},
        "source_isolation": {
            "linkedin": {
                "mode": "exclusive_lock",
                "path": str(root / "locks/linkedin-profile.lock"),
                "shared_profile_path": "/var/lib/browser-desktop/profiles/linkedin",
                "backup_path": str(linkedin_backup),
            },
        },
    }

    environment = build_isolated_probe_environment(manifest)

    assert environment["JOB_INTEL_BROWSER_PROFILE_DIR_LINKEDIN"] == (
        "/var/lib/browser-desktop/profiles/linkedin"
    )
    assert "JOB_INTEL_BROWSER_PROFILE_DIR_HH" not in environment

    linkedin_backup.rmdir()
    with __import__("pytest").raises(ValueError, match="shared profile backup"):
        build_isolated_probe_environment(manifest)


def test_probe_records_unexpected_source_failure_without_aborting_run(tmp_path: Path) -> None:
    def unavailable(_query: str):
        raise RuntimeError("Playwright is unavailable")

    result = run_probe(
        runtime_capability_checks=_ready_checks("linkedin"),
        run_id="run-failure",
        queries=(
            {
                "query_id": "q1",
                "cell_id": "uk",
                "source_family": "linkedin",
                "query": "VP Product UK",
            },
        ),
        sources={"linkedin": unavailable},
        output_dir=tmp_path / "probe",
        isolation={
            "linkedin": SourceIsolation(
                mode="cloned_profile", path=tmp_path / "profiles/linkedin",
                collection_method="browser",
            )
        },
    )

    assert result.source_states == {"linkedin": "blocked_extraction_failure"}
    assert result.stage_counts["raw_observed"] == 0


def test_source_state_preserves_observation_when_later_query_fails(tmp_path: Path) -> None:
    def partial(query: str):
        if query == "works":
            return [
                {
                    "source_id": "one",
                    "url": "https://example.com/jobs/one",
                    "title": "VP Product",
                    "company": "Acme",
                    "description": "Own product strategy",
                }
            ]
        raise RuntimeError("later extraction failure")

    result = run_probe(
        run_id="run-partial",
        queries=(
            {
                "query_id": "q1",
                "cell_id": "uk",
                "source_family": "duckduckgo",
                "query": "works",
            },
            {
                "query_id": "q2",
                "cell_id": "canada",
                "source_family": "duckduckgo",
                "query": "fails",
            },
        ),
        sources={"duckduckgo": partial},
        output_dir=tmp_path / "probe",
        isolation={
            "duckduckgo": SourceIsolation(
                mode="exclusive_lock", path=tmp_path / "locks/duckduckgo.lock",
                collection_method="api",
            )
        },
    )

    assert result.source_states == {"duckduckgo": "observed_with_failures"}
    assert result.stage_counts["raw_observed"] == 1

def test_linkedin_queries_keep_keywords_and_geography_structurally_separate() -> None:
    from job_intel.product_search.acquisition_probe import load_linkedin_geography_mapping

    contract = load_search_contract(ROOT / "config/product_search/search_contract.v1.yaml")
    mapping = load_linkedin_geography_mapping(
        ROOT / "config/product_search/linkedin_geography.v1.yaml"
    )
    queries = expand_queries(
        contract,
        role_terms=("VP Product",),
        geography_mapping=mapping,
    )

    uk = next(query for query in queries if query.cell_id == "uk" and query.source_family == "linkedin")
    singapore = next(
        query for query in queries if query.cell_id == "singapore" and query.source_family == "linkedin"
    )
    assert uk.keywords == "VP Product"
    assert uk.primary_geography == "United Kingdom"
    assert uk.query != uk.keywords
    assert uk.geography_target is not None
    assert uk.geography_target.location == "United Kingdom"
    assert singapore.primary_geography == "Singapore"
    assert uk.query_id != singapore.query_id


def test_linkedin_query_identity_changes_when_canonical_mapping_changes() -> None:
    from job_intel.product_search.acquisition_probe import load_linkedin_geography_mapping

    contract = load_search_contract(ROOT / "config/product_search/search_contract.v1.yaml")
    mapping = load_linkedin_geography_mapping(
        ROOT / "config/product_search/linkedin_geography.v1.yaml"
    )
    changed = dict(mapping)
    changed["uk"] = mapping["uk"].model_copy(update={"location": "United Kingdom (remote)"})

    original = next(
        query
        for query in expand_queries(contract, role_terms=("VP Product",), geography_mapping=mapping)
        if query.cell_id == "uk" and query.source_family == "linkedin"
    )
    remapped = next(
        query
        for query in expand_queries(contract, role_terms=("VP Product",), geography_mapping=changed)
        if query.cell_id == "uk" and query.source_family == "linkedin"
    )
    assert original.query_id != remapped.query_id


def test_every_linkedin_enabled_cell_has_mapping_or_explicit_block() -> None:
    from job_intel.product_search.acquisition_probe import load_linkedin_geography_mapping

    contract = load_search_contract(ROOT / "config/product_search/search_contract.v1.yaml")
    mapping = load_linkedin_geography_mapping(
        ROOT / "config/product_search/linkedin_geography.v1.yaml"
    )
    linkedin_cells = {
        cell.cell_id
        for lane in contract.lanes.values()
        for cell in lane.cells.values()
        if "linkedin" in cell.source_families
    }
    assert linkedin_cells <= set(mapping)
    assert all(
        mapping[cell_id].status in {"verified", "unverified", "unsupported", "blocked"}
        for cell_id in linkedin_cells
    )


def test_unverified_linkedin_target_blocks_before_market_dispatch(tmp_path: Path) -> None:
    calls: list[object] = []

    query = ProbeQuery(
        query_id="q-unsupported-geography",
        cell_id="uk",
        source_family="linkedin",
        query="VP Product United Kingdom",
        keywords="VP Product",
        primary_geography="United Kingdom",
    )
    from job_intel.product_search.acquisition_probe import RuntimeCapabilityResult

    result = run_probe(
        run_id="run-unsupported-geography",
        queries=(query,),
        sources={"linkedin": lambda request: calls.append(request) or []},
        output_dir=tmp_path / "probe",
        runtime_capability_checks={"linkedin": lambda: RuntimeCapabilityResult(state="ready")},
        isolation={
            "linkedin": SourceIsolation(
                mode="cloned_profile",
                path=tmp_path / "profile",
                collection_method="browser",
                cdp_url="http://127.0.0.1:19222",
            )
        },
    )

    assert result.source_states == {"linkedin": "blocked_unsupported_geography"}
    assert calls == []
    assert result.cost["market_query_dispatch_count"] == 0


def _b1_record(source_id: str) -> dict[str, str]:
    return {
        "source_id": source_id,
        "url": f"https://example.test/jobs/{source_id}",
        "title": "Head of Product",
        "company": "Acme",
        "description": "Own product strategy and P&L",
    }


class _B1DegradedSource:
    last_errors = ("one planned page failed",)

    def __call__(self, _query: str) -> list[dict[str, str]]:
        return []


def _b1_run(
    tmp_path: Path,
    families: dict[str, object],
    *,
    blocked: frozenset[str] = frozenset(),
    minimum: int = 2,
    credited: int | None = None,
):
    queries = tuple(
        {
            "query_id": f"q-{family}",
            "cell_id": "b1-cell",
            "source_family": family,
            "query": "Head of Product",
        }
        for family in families
    )
    sources = {
        family: (source if callable(source) else (lambda _query, rows=source: rows))
        for family, source in families.items()
    }
    isolation = {
        family: SourceIsolation(
            mode="blocked" if family in blocked else "api",
            path=None if family in blocked else tmp_path / f"{family}.lock",
            collection_method="api",
        )
        for family in families
    }
    kwargs = {
        "minimum_independent_families_by_cell": {"b1-cell": minimum},
    }
    if credited is not None:
        kwargs["credited_records_by_cell"] = {"b1-cell": credited}
    return run_probe(
        run_id="b1-run",
        queries=queries,
        sources=sources,
        output_dir=tmp_path,
        isolation=isolation,
        max_attempts=1,
        **kwargs,
    )


def test_b1_one_completed_family_below_minimum_is_insufficient_breadth(tmp_path: Path) -> None:
    result = _b1_run(tmp_path, {"alpha": [_b1_record("alpha-1")]})

    assert result.acquisition_outcomes["b1-cell"] == "insufficient_breadth"


def test_b1_product_state_is_absent_until_stage_four_evidence(tmp_path: Path) -> None:
    result = _b1_run(
        tmp_path,
        {"alpha": [_b1_record("alpha-1")], "beta": [_b1_record("beta-1")]},
    )

    assert result.acquisition_outcomes["b1-cell"] == "candidate_records_found"
    assert result.product_observability_state["b1-cell"] is None
    assert result.product_observability_reason["b1-cell"] == "stage_4_evidence_absent"


def test_b1_single_linkedin_family_with_many_rows_is_still_insufficient_breadth(tmp_path: Path) -> None:
    rows = [_b1_record(f"turkmenistan-{index}") for index in range(21)]
    result = _b1_run(tmp_path, {"linkedin": rows})

    assert result.acquisition_outcomes["b1-cell"] == "insufficient_breadth"


def test_b1_two_completed_independent_families_can_credit_candidate_records(tmp_path: Path) -> None:
    result = _b1_run(
        tmp_path,
        {"alpha": [_b1_record("alpha-1")], "beta": [_b1_record("beta-1")]},
        credited=1,
    )

    assert result.acquisition_outcomes["b1-cell"] == "candidate_records_found"


def test_b1_degraded_attempt_beats_breadth_shortfall(tmp_path: Path) -> None:
    result = _b1_run(
        tmp_path,
        {"alpha": [_b1_record("alpha-1")], "beta": _B1DegradedSource()},
    )

    assert result.acquisition_outcomes["b1-cell"] == "degraded"


def test_b1_completed_and_blocked_below_breadth_is_blocked(tmp_path: Path) -> None:
    result = _b1_run(
        tmp_path,
        {"alpha": [_b1_record("alpha-1")], "beta": []},
        blocked=frozenset({"beta"}),
    )

    assert result.acquisition_outcomes["b1-cell"] == "blocked"


def test_b1_completed_blocked_and_degraded_is_unambiguously_degraded(tmp_path: Path) -> None:
    result = _b1_run(
        tmp_path,
        {
            "alpha": [_b1_record("alpha-1")],
            "beta": [],
            "gamma": _B1DegradedSource(),
        },
        blocked=frozenset({"beta"}),
    )

    assert result.acquisition_outcomes["b1-cell"] == "degraded"


def test_b1_breadth_threshold_preserves_blocked_and_degraded_diagnostics(tmp_path: Path) -> None:
    result = _b1_run(
        tmp_path,
        {
            "alpha": [_b1_record("alpha-1")],
            "beta": [_b1_record("beta-1")],
            "gamma": [],
            "delta": _B1DegradedSource(),
        },
        blocked=frozenset({"gamma"}),
    )

    assert result.acquisition_outcomes["b1-cell"] == "candidate_records_found"
    assert result.degraded_families["b1-cell"] == ("delta",)
    assert result.blocked_families["b1-cell"] == ("gamma",)


def test_b1_transition_table_is_complete_and_non_overlapping() -> None:
    transition = getattr(acquisition_probe, "resolve_acquisition_outcome", None)
    assert callable(transition), "B1 transition function is missing"

    for completed in range(4):
        for blocked in range(4):
            for degraded in range(4):
                matches = acquisition_probe.matching_acquisition_rules(
                    completed=completed,
                    blocked=blocked,
                    degraded=degraded,
                    minimum_independent_families=2,
                )
                assert len(matches) == 1, (
                    completed,
                    blocked,
                    degraded,
                    matches,
                )
                decision = transition(
                    completed=completed,
                    blocked=blocked,
                    degraded=degraded,
                    minimum_independent_families=2,
                    credited=1 if matches[0] == "candidate_records_found" else 0,
                )
                assert decision.acquisition_outcome == matches[0]


def test_b1_zero_attempts_are_not_attempted() -> None:
    decision = acquisition_probe.resolve_acquisition_outcome(
        completed=0,
        blocked=0,
        degraded=0,
        minimum_independent_families=2,
        credited=0,
    )

    assert decision.acquisition_outcome == "not_attempted"
    assert decision.product_observability_state == "not_observed"


def test_b1_only_blocked_attempts_are_blocked() -> None:
    decision = acquisition_probe.resolve_acquisition_outcome(
        completed=0,
        blocked=1,
        degraded=0,
        minimum_independent_families=2,
        credited=0,
    )

    assert decision.acquisition_outcome == "blocked"
    assert decision.product_observability_state == "blocked"


def test_b1_completed_breadth_without_credited_rows_has_no_candidate_records() -> None:
    decision = acquisition_probe.resolve_acquisition_outcome(
        completed=2,
        blocked=0,
        degraded=0,
        minimum_independent_families=2,
        credited=0,
    )

    assert decision.acquisition_outcome == "no_candidate_records"
    assert decision.product_observability_reason == "stage_4_evidence_absent"


def test_b1_same_family_is_accounted_per_cell_not_globally(tmp_path: Path) -> None:
    class SplitFamily:
        def __call__(self, query: str) -> list[dict[str, str]]:
            if query == "blocked-cell":
                raise ProbeSourceBlocked("anti_bot", "blocked in one cell")
            return [_b1_record("shared-family-row")]

    result = run_probe(
        run_id="b1-per-cell",
        queries=(
            {
                "query_id": "q-blocked",
                "cell_id": "blocked-cell",
                "source_family": "shared",
                "query": "blocked-cell",
            },
            {
                "query_id": "q-completed",
                "cell_id": "completed-cell",
                "source_family": "shared",
                "query": "completed-cell",
            },
        ),
        sources={"shared": SplitFamily()},
        output_dir=tmp_path,
        isolation={
            "shared": SourceIsolation(
                mode="api", path=tmp_path / "shared.lock", collection_method="api"
            )
        },
        minimum_independent_families_by_cell={
            "blocked-cell": 1,
            "completed-cell": 1,
        },
        max_attempts=1,
    )

    assert result.acquisition_outcomes == {
        "blocked-cell": "blocked",
        "completed-cell": "candidate_records_found",
    }


def test_b1_acquisition_outcomes_never_use_product_qualified_label(tmp_path: Path) -> None:
    result = _b1_run(tmp_path, {"alpha": [_b1_record("alpha-1")]})

    assert all("qualified" not in outcome for outcome in result.acquisition_outcomes.values())


def test_b1_legacy_summary_refuses_to_reconstruct_attempt_outcomes() -> None:
    legacy = Path(
        "/home/hermes/.hermes/job_intel/experiments/gate-a/"
        "65d60daae16093a9a7e34a11a159e2f789dd14dd/summary.json"
    )
    if not legacy.is_file():
        pytest.skip("legacy Gate A corpus exists only on the VPS")

    report = acquisition_probe.classify_legacy_attempt_evidence(legacy)

    assert report["acquisition_outcomes"] == {}
    assert set(report["cell_outcomes"].values()) == {
        "legacy_attempt_evidence_insufficient"
    }
    assert len(report["cell_outcomes"]) == 30


def test_b1_rejects_legacy_cell_states_even_next_to_new_outcomes() -> None:
    evidence = {
        "stage_counts": {
            "raw_observed": 0,
            "canonical_current": 0,
            "minimum_evidence_sufficient": 0,
        },
        "provisional_labels": {},
        "scheduled_attempts": 1,
        "completed_attempts": 1,
        "missed_attempts": 0,
        "family_attempts": {"alpha": 1},
        "acquisition_outcomes": {"uk": "blocked"},
        "cell_states": {"uk": "qualified_results_found"},
        "evidence_hashes_verified": True,
        "isolated_paths": {},
        "side_effects": {},
    }

    with pytest.raises(ValueError, match="legacy cell_states"):
        validate_gate_a_run_evidence(evidence)


def test_b1_summary_exposes_credited_records_provenance(tmp_path: Path) -> None:
    families = {
        "alpha": [_b1_record("alpha-1")],
        "beta": [_b1_record("beta-1")],
    }

    fallback = _b1_run(tmp_path / "fallback", families)
    attributed = _b1_run(tmp_path / "attributed", families, credited=1)

    assert fallback.credited_records_provenance == {
        "b1-cell": "received_rows_fallback"
    }
    assert attributed.credited_records_provenance == {
        "b1-cell": "attributed"
    }


def test_gate_a_query_carries_the_versioned_execution_plan() -> None:
    from job_intel.product_search.acquisition_probe import load_linkedin_geography_mapping

    contract = load_search_contract(ROOT / "config/product_search/search_contract.v1.yaml")
    mapping = {
        "uk": load_linkedin_geography_mapping(
            ROOT / "config/product_search/linkedin_geography.v1.yaml"
        )["uk"]
    }
    plan = LinkedInExecutionPlan(page_offsets=(0, 25), max_scroll_checkpoints=2)

    queries = expand_queries(
        contract,
        role_terms=("VP Product",),
        geography_mapping=mapping,
        execution_plan=plan,
    )

    query = next(
        item for item in queries if item.cell_id == "uk" and item.source_family == "linkedin"
    )
    assert query.execution_plan == plan
