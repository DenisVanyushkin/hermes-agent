from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3

from job_intel.product_search.acquisition_probe import (
    ProbeSourceBlocked,
    SourceIsolation,
    expand_queries,
    resolve_public_sources,
    run_probe,
)
from job_intel.product_search.search_contract import load_search_contract


ROOT = Path(__file__).resolve().parents[2]


def test_query_expansion_is_deterministic_and_preserves_cell_family_identity() -> None:
    contract = load_search_contract(ROOT / "config/product_search/search_contract.v1.yaml")

    first = expand_queries(contract, role_terms=("VP Product", "Chief Product Officer"))
    second = expand_queries(contract, role_terms=("VP Product", "Chief Product Officer"))

    assert first == second
    assert first
    assert len({query.query_id for query in first}) == len(first)
    assert all(query.cell_id and query.source_family and query.query for query in first)
    assert [query.query_id for query in first] == sorted(query.query_id for query in first)


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
        isolation={"fake": SourceIsolation(mode="exclusive_lock", path=tmp_path / "fake.lock")},
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
        isolation={"blocked": SourceIsolation(mode="exclusive_lock", path=tmp_path / "blocked.lock")},
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
    isolation = {"fake": SourceIsolation(mode="exclusive_lock", path=tmp_path / "lock")}

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


def test_probe_registry_exposes_only_existing_public_scraper_interfaces() -> None:
    sources = resolve_public_sources()

    assert set(sources) == {"linkedin", "headhunter", "duckduckgo", "remoteok", "remotive"}
    assert all(callable(source) for source in sources.values())
