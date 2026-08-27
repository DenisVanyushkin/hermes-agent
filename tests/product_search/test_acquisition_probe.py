from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3

from job_intel.product_search.acquisition_probe import (
    ProbeSourceBlocked,
    SourceIsolation,
    build_isolated_probe_environment,
    build_snapshot_queries,
    expand_queries,
    ProbeQuery,
    resolve_public_sources,
    run_probe,
    validate_probe_output_path,
)
from job_intel.product_search.search_contract import load_search_contract


ROOT = Path(__file__).resolve().parents[2]


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
