from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys

import pytest
import yaml

from job_intel.product_search.acquisition_probe import (
    BOUNDED_PROOF_SELECTION_RULE,
    LinkedInGeographyMapping,
    LinkedInGeographyTarget,
    ProbeSourceBlocked,
    SourceIsolation,
    EXCLUSION_REASON_CATALOG,
    build_isolated_probe_environment,
    build_experiment_manifest,
    build_snapshot_queries,
    expand_queries,
    load_linkedin_geography_mapping,
    ProbeQuery,
    LinkedInExecutionPlan,
    resolve_public_sources,
    RuntimeCapabilityResult,
    run_probe,
    validate_gate_a_run_evidence,
    validate_experiment_manifest,
    validate_probe_output_path,
)
from job_intel.product_search.search_contract import load_search_contract
import job_intel.product_search.acquisition_probe as acquisition_probe
import job_intel.sources as job_sources
import job_intel.browser_worker as browser_worker


def test_gate_a_linkedin_source_uses_explicit_unauthenticated_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: dict[str, object] = {}

    def fake_fetch(query: str, **kwargs: object) -> list[object]:
        calls["query"] = query
        calls.update(kwargs)
        return []

    monkeypatch.setattr("job_intel.sources.fetch_linkedin_vacancies", fake_fetch)
    request = ProbeQuery(
        query_id="q1",
        cell_id="uk",
        source_family="linkedin",
        query="VP Product United Kingdom",
        keywords="VP Product",
        primary_geography="United Kingdom",
        geography_target=LinkedInGeographyTarget(
            status="verified", location="United Kingdom", verified_at="2026-08-27"
        ),
    )

    list(resolve_public_sources(run_id="run-1")["linkedin"](request))

    assert calls["allow_unauthenticated"] is True
    assert calls["run_id"] == "run-1"
    assert calls["query_id"] == "q1"
    assert calls["cell_id"] == "uk"






def test_linkedin_capture_ids_reach_browser_worker_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[str] = []

    monkeypatch.setenv("JOB_INTEL_BROWSER_PYTHON", "/usr/bin/python3")
    monkeypatch.setattr(job_sources, "browser_native_available", lambda: True)
    monkeypatch.setattr(job_sources, "_browser_config", lambda _source: object())
    monkeypatch.setattr(job_sources, "_ensure_required_browser_profile", lambda *_args: None)

    def fake_worker(*args: str, **_kwargs: object) -> dict[str, object]:
        captured.extend(args)
        return {"ok": True, "vacancies": [], "session_health": {}, "search_trace": {}}

    monkeypatch.setattr(job_sources, "_browser_worker_payload", fake_worker)
    job_sources.fetch_linkedin_vacancies(
        "VP Product",
        location="United Kingdom",
        run_id="run-1",
        query_id="q1",
        cell_id="uk",
    )

    assert captured == [
        "linkedin",
        "VP Product",
        "1",
        "--location",
        "United Kingdom",
        "--run-id",
        "run-1",
        "--query-id",
        "q1",
        "--cell-id",
        "uk",
    ]

def test_linkedin_slug_and_numeric_urls_share_one_canonical_identity() -> None:
    from job_intel.product_search.acquisition_probe import _canonical_url

    slug = (
        "https://uk.linkedin.com/jobs/view/chief-product-officer-at-acme-4459675813"
        "?position=1&refId=redacted&trackingId=redacted"
    )
    numeric = "https://www.linkedin.com/jobs/view/4459675813?eBP=other"

    assert _canonical_url(slug, collapse_linkedin_slug=True) == _canonical_url(
        numeric, collapse_linkedin_slug=True
    )


def test_canonical_url_default_preserves_gate_b_legacy_slug_identity() -> None:
    from job_intel.product_search.acquisition_probe import _canonical_url

    slug = (
        "https://uk.linkedin.com/jobs/view/chief-product-officer-at-acme-4459675813"
        "?position=1&refId=redacted&trackingId=redacted"
    )
    numeric = "https://www.linkedin.com/jobs/view/4459675813?eBP=other"

    assert _canonical_url(slug) == (
        "https://uk.linkedin.com/jobs/view/chief-product-officer-at-acme-4459675813"
    )
    assert _canonical_url(slug) != _canonical_url(numeric)


def test_gate_b_consumer_keeps_legacy_identity_while_gate_a_opts_in() -> None:
    from job_intel.product_search.acquisition_probe import _canonical_url
    from job_intel.product_search.gate_b import _canonical_url as gate_b_canonical_url

    slug = (
        "https://uk.linkedin.com/jobs/view/chief-product-officer-at-acme-4459675813"
        "?position=1&refId=redacted&trackingId=redacted"
    )

    assert gate_b_canonical_url(slug) == _canonical_url(slug)
    assert gate_b_canonical_url(slug) != _canonical_url(
        slug, collapse_linkedin_slug=True
    )


def test_gate_a_persists_collapsed_slug_identity_in_evidence(tmp_path: Path) -> None:
    slug = (
        "https://uk.linkedin.com/jobs/view/chief-product-officer-at-acme-4459675813"
        "?position=1&refId=redacted&trackingId=redacted"
    )
    query = ProbeQuery(
        query_id="q-slug",
        cell_id="uk",
        source_family="linkedin",
        query="VP Product United Kingdom",
        minimum_independent_families=1,
    )

    result = run_probe(
        run_id="run-slug",
        queries=[query],
        sources={
            "linkedin": lambda _query: [
                {
                    "source_id": "linkedin-slug-1",
                    "company": "Acme",
                    "title": "Chief Product Officer",
                    "location": "United Kingdom",
                    "url": slug,
                }
            ]
        },
        output_dir=tmp_path / "probe",
        isolation={
            "linkedin": SourceIsolation(
                mode="exclusive_lock", path=tmp_path / "linkedin.lock", collection_method="browser"
            )
        },
        runtime_capability_checks={"linkedin": lambda: RuntimeCapabilityResult(state="ready")},
    )

    evidence_path = tmp_path / "probe" / result.evidence[0].raw_reference
    payload = json.loads(evidence_path.read_text(encoding="utf-8"))
    assert payload["canonical_url"] == "https://www.linkedin.com/jobs/view/4459675813"


def test_pair_evidence_distinguishes_unauthenticated_completion_and_carries_a1_counts(
    tmp_path: Path,
) -> None:
    class Source:
        last_trace = {
            "session_observation": "without_session",
            "extraction_counts": {
                "dom": 3,
                "parsed_before_filter": 2,
                "returned": 2,
                "duplicate_canonical": 0,
                "duplicate_canonical_returned": 0,
                "excluded": 0,
                "unexplained": 1,
                "vacancies_extracted": 2,
            },
            "pages": [{"artifact_ref": "diagnostics/a1-page.json"}],
            "scroll_checkpoints": [
                {"step": 1, "cumulative_unique_dom_id_count": 3},
                {"step": 2, "cumulative_unique_dom_id_count": 5},
                {"step": 3, "cumulative_unique_dom_id_count": 5},
            ],
        }
        last_health = {"session_state": "session_missing_cookie"}

        def __call__(self, _request: object) -> list[dict[str, object]]:
            return [
                {
                    "source": "linkedin",
                    "source_id": "101",
                    "company": "Spark",
                    "title": "VP Product",
                    "location": "United Kingdom",
                    "url": "https://www.linkedin.com/jobs/view/101",
                    "description": "Own monetization",
                }
            ]

    query = ProbeQuery(
        query_id="q1",
        cell_id="uk",
        source_family="linkedin",
        query="VP Product United Kingdom",
        minimum_independent_families=1,
    )
    result = run_probe(
        run_id="run-1",
        queries=[query],
        sources={"linkedin": Source()},
        output_dir=tmp_path,
        isolation={"linkedin": SourceIsolation(mode="exclusive_lock", path=tmp_path / "lock", collection_method="browser")},
        runtime_capability_checks={"linkedin": lambda: RuntimeCapabilityResult(state="ready")},
        minimum_independent_families_by_cell={"uk": 1},
    )

    pair = result.model_dump(mode="json")["cell_family_attempts"][0]
    assert pair["outcome"] == "completed"
    assert pair["session_observation"] == "without_session"
    assert pair["extraction_counts"]["dom"] == 3
    assert pair["extraction_counts"]["parsed_before_filter"] == 2
    assert pair["extraction_artifact_references"] == ["diagnostics/a1-page.json"]
    assert pair["scroll_checkpoints"] == [
        {"step": 1, "cumulative_unique_dom_id_count": 3},
        {"step": 2, "cumulative_unique_dom_id_count": 5},
        {"step": 3, "cumulative_unique_dom_id_count": 5},
    ]


ROOT = Path(__file__).resolve().parents[2]


def _c1a_manifest(root: Path) -> dict[str, object]:
    mapping_version = _c1a_mapping().version
    return {
        "gate": "gate-a",
        "environment_id": "product-search-gate-a",
        "root": str(root),
        "paths": {
            name: str(root / name)
            for name in (
                "runtime",
                "experiment.sqlite3",
                "raw-evidence",
                "logs",
                "locks",
                "browser-profile",
                "cache",
                "tmp",
            )
        },
        "python": {
            "executable_path": str(root / "python-runtime/bin/python"),
        },
        "environment": {
            "import_root": str(root / "runtime"),
        },
        "source_isolation": {
            family: {
                "mode": "exclusive_lock",
                "path": str(root / "locks" / f"{family}.lock"),
                "collection_method": "browser",
            }
            for family in ("linkedin", "duckduckgo")
        },
        "bounded_proof": {
            "cell_ids": ["uk", "singapore", "kazakhstan"],
            "include_ats_snapshot": False,
            "negative_control": {
                "selection_rule": (
                    "first_alphabetical_unsupported_excluding_bounded_v1"
                ),
                "cell_id": "synthetic_c1a_unsupported",
                "status": "unsupported",
                "location": "C1A nonexistent geography target",
                "mapping_version": mapping_version,
            },
        },
    }


def _c1a_mapping() -> LinkedInGeographyMapping:
    mapping = load_linkedin_geography_mapping(
        ROOT / "config/product_search/linkedin_geography.v1.yaml"
    )
    historical = {
        cell_id: target.model_copy(
            update={
                "status": "verified" if cell_id in {"uk", "singapore", "kazakhstan"} else "unverified",
                "verified_at": "2026-08-27" if cell_id in {"uk", "singapore", "kazakhstan"} else None,
            }
        )
        for cell_id, target in mapping.items()
    }
    verified = {
        **historical,
        **{
            cell_id: historical[cell_id].model_copy(
                update={"status": "verified", "location": historical[cell_id].location}
            )
            for cell_id in ("uk", "singapore", "kazakhstan")
        },
    }
    return LinkedInGeographyMapping(
        verified,
        version="1.0",
        normalization_rule_version=mapping.normalization_rule_version,
        contamination_formula_version=mapping.contamination_formula_version,
        contamination_threshold=mapping.contamination_threshold,
        city_country_codes=mapping.city_country_codes,
    )


def test_c1a_run_manifest_passes_mapping_and_preserves_bounded_artifact(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "experiment"
    (root / "runtime/config/product_search").mkdir(parents=True)
    shutil.copy2(
        ROOT / "config/product_search/search_contract.v1.yaml",
        root / "runtime/config/product_search/search_contract.v1.yaml",
    )
    manifest = _c1a_manifest(root)
    manifest_path = tmp_path / "manifest.yaml"
    manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=True), encoding="utf-8")

    mapping = _c1a_mapping()
    calls: list[object] = []
    synthetic_transport_calls = 0

    def fake_source(request: object) -> list[dict[str, object]]:
        nonlocal synthetic_transport_calls
        calls.append(request)
        if isinstance(request, ProbeQuery) and request.cell_id == "synthetic_c1a_unsupported":
            synthetic_transport_calls += 1
        query_id = (
            request.query_id
            if isinstance(request, ProbeQuery)
            else str(request).replace(" ", "-")
        )
        return [
            {
                "source_id": f"c1a-{query_id}",
                "url": f"https://example.test/jobs/{query_id}",
                "structural_url": (
                    "https://www.linkedin.com/jobs/search/?keywords=VP+Product"
                    f"&location={query_id}"
                ),
                "dom_unique_job_ids": [query_id],
                "parsed_unique_job_ids_before_role_filter": [query_id],
                "dom_count": 1,
                "parser_before_filter_count": 1,
                "title": "VP Product",
                "company": "C1A Example",
                "description": "Own product strategy and P&L",
                "location": "United Kingdom",
            }
        ]

    source_families = {
        "linkedin",
        "duckduckgo",
        "ashby",
        "greenhouse",
        "lever",
        "personio",
        "recruitee",
        "smartrecruiters",
        "teamtailor",
    }
    monkeypatch.setattr(
        acquisition_probe,
        "verify_experiment_runtime",
        lambda _manifest: None,
    )
    monkeypatch.setattr(
        acquisition_probe,
        "load_linkedin_geography_mapping",
        lambda _path=None: mapping,
    )
    monkeypatch.setattr(
        acquisition_probe,
        "resolve_public_sources",
        lambda **_kwargs: {family: fake_source for family in source_families},
    )
    monkeypatch.setattr(
        acquisition_probe,
        "resolve_runtime_capability_checks",
        lambda _isolation: {
            "linkedin": lambda: RuntimeCapabilityResult(state="ready"),
            "duckduckgo": lambda: RuntimeCapabilityResult(state="ready"),
        },
    )
    monkeypatch.setattr(
        sys, "argv", ["acquisition_probe", "run-manifest", str(manifest_path)]
    )

    environment_before = dict(os.environ)
    try:
        assert acquisition_probe.main() == 0
    finally:
        os.environ.clear()
        os.environ.update(environment_before)

    summary = json.loads((root / "summary.json").read_text(encoding="utf-8"))
    selected = {"uk", "singapore", "kazakhstan"}
    assert set(summary["acquisition_outcomes"]) == selected | {
        "synthetic_c1a_unsupported"
    }
    assert summary["geography_summary"]["mapping_version"] == mapping.version
    assert summary["geography_summary"]["cells"]
    assert all(
        summary["credited_records_provenance"][cell_id] == "attributed"
        for cell_id in selected
    )
    assert all(
        not (
            isinstance(request, ProbeQuery)
            and request.cell_id == "synthetic_c1a_unsupported"
        )
        for request in calls
    )
    assert all(
        not (
            isinstance(request, str)
            and request.startswith("Chief Product Officer OR")
        )
        for request in calls
    )
    assert all(
        {"cell_id", "source_family", "timestamp", "outcome", "received_records", "credited_records"}
        <= set(attempt)
        for attempt in summary["cell_family_attempts"]
    )
    assert summary["geography_summary"]["pairwise"]
    raw = json.loads(
        next((root / "raw-evidence").glob("*.json")).read_text(encoding="utf-8")
    )
    assert raw["structural_url"]
    assert raw["dom_count"] == 1
    assert raw["parser_before_filter_count"] == 1


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        ("cell_id", "synthetic_c1a_wrong", "does not follow the mapping rule"),
        ("selection_rule", "arbitrary_selection", "selection rule is invalid"),
        ("mapping_version", "0.9", "mapping version is stale"),
        ("status", "verified", "must be unsupported"),
        ("location", "another nonexistent target", "location is invalid"),
    ],
    ids=["cell-id", "selection-rule", "mapping-version", "status", "location"],
)
def test_c1a_negative_control_rejects_each_untrusted_manifest_field(
    field: str, value: str, error: str
) -> None:
    mapping = _c1a_mapping()
    declared = {
        "selection_rule": "first_alphabetical_unsupported_excluding_bounded_v1",
        "cell_id": "synthetic_c1a_unsupported",
        "status": "unsupported",
        "location": "C1A nonexistent geography target",
        "mapping_version": mapping.version,
    }
    declared[field] = value

    with pytest.raises(ValueError, match=error):
        acquisition_probe.select_bounded_negative_control(
            mapping,
            excluded_cell_ids=("uk", "singapore", "kazakhstan"),
            declared=declared,
        )


def test_c1a_synthetic_negative_control_is_deterministic_and_has_zero_transport() -> None:
    mapping = _c1a_mapping()
    unsupported = sorted(
        cell_id
        for cell_id, target in mapping.items()
        if target.status == "unsupported"
        and cell_id not in {"uk", "singapore", "kazakhstan"}
    )
    assert unsupported == []
    control = {
        "selection_rule": (
            "first_alphabetical_unsupported_excluding_bounded_v1"
        ),
        "cell_id": "synthetic_c1a_unsupported",
        "status": "unsupported",
        "location": "C1A nonexistent geography target",
        "mapping_version": mapping.version,
    }
    selected = acquisition_probe.select_bounded_negative_control(
        mapping,
        excluded_cell_ids=("uk", "singapore", "kazakhstan"),
        declared=control,
    )
    assert selected == control
    assert selected["cell_id"] == "synthetic_c1a_unsupported"



def test_write_manifest_and_run_manifest_are_ring_compatible(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "experiment"
    (root / "runtime/config/product_search").mkdir(parents=True)
    shutil.copy2(
        ROOT / "config/product_search/search_contract.v1.yaml",
        root / "runtime/config/product_search/search_contract.v1.yaml",
    )
    mapping = _c1a_mapping()
    mapping = LinkedInGeographyMapping(
        {
            **mapping,
            "aaa_control": LinkedInGeographyTarget(
                location="Synthetic unsupported target",
                status="unsupported",
                country_codes=(),
            ),
        },
        version=mapping.version,
        normalization_rule_version=mapping.normalization_rule_version,
        contamination_formula_version=mapping.contamination_formula_version,
        contamination_threshold=mapping.contamination_threshold,
        city_country_codes=mapping.city_country_codes,
    )
    monkeypatch.setattr(
        acquisition_probe,
        "load_linkedin_geography_mapping",
        lambda _path=None: mapping,
    )
    monkeypatch.setattr(sys, "executable", str(root / "python-runtime/bin/python"))
    monkeypatch.setattr(
        sys,
        "argv",
        ["acquisition_probe", "write-manifest", str(root), "a" * 40],
    )

    assert acquisition_probe.main() == 0
    generated_path = root / "manifest.yaml"
    generated = yaml.safe_load(generated_path.read_text(encoding="utf-8"))
    assert generated["bounded_proof"]["cell_ids"] == ["uk", "singapore", "kazakhstan"]
    assert generated["bounded_proof"]["include_ats_snapshot"] is False
    assert generated["bounded_proof"]["negative_control"]["cell_id"] == "aaa_control"
    assert generated["bounded_proof"]["negative_control"]["mapping_version"] == mapping.version

    execution_plan = LinkedInExecutionPlan(
        page_offsets=(0, 25, 50), max_scroll_checkpoints=3
    )
    monkeypatch.setattr(acquisition_probe, "LinkedInExecutionPlan", lambda: execution_plan)

    captured: dict[str, object] = {}
    monkeypatch.setattr(
        acquisition_probe,
        "verify_experiment_runtime",
        lambda _manifest: None,
    )
    monkeypatch.setattr(
        acquisition_probe,
        "build_isolated_probe_environment",
        lambda _manifest, ambient: {},
    )
    monkeypatch.setattr(
        acquisition_probe,
        "run_probe",
        lambda **kwargs: captured.update(kwargs),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["acquisition_probe", "run-manifest", str(generated_path)],
    )

    assert acquisition_probe.main() == 0
    assert captured["geography_mapping"] is mapping
    linkedin_queries = [
        query
        for query in captured["queries"]
        if isinstance(query, ProbeQuery) and query.source_family == "linkedin"
    ]
    assert linkedin_queries
    assert {query.execution_plan for query in linkedin_queries} == {execution_plan}
    assert execution_plan.max_scroll_checkpoints != 2
    assert all(query.execution_plan is execution_plan for query in linkedin_queries)


def test_full_run_manifest_is_explicit_and_includes_seeded_ats_snapshot(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "experiment"
    (root / "runtime/config/product_search").mkdir(parents=True)
    shutil.copy2(
        ROOT / "config/product_search/search_contract.v1.yaml",
        root / "runtime/config/product_search/search_contract.v1.yaml",
    )
    manifest = _c1a_manifest(root)
    manifest["run_mode"] = "full"
    manifest["full_run"] = {
        "include_ats_snapshot": True,
        "negative_control": manifest["bounded_proof"]["negative_control"],
    }
    manifest_path = tmp_path / "manifest.yaml"
    manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=True), encoding="utf-8")

    mapping = _c1a_mapping()
    captured: dict[str, object] = {}
    monkeypatch.setattr(acquisition_probe, "verify_experiment_runtime", lambda _manifest: None)
    monkeypatch.setattr(
        acquisition_probe, "load_linkedin_geography_mapping", lambda _path=None: mapping
    )
    monkeypatch.setattr(
        acquisition_probe, "build_isolated_probe_environment", lambda _manifest, ambient: {}
    )
    monkeypatch.setattr(acquisition_probe, "resolve_public_sources", lambda **_kwargs: {})
    monkeypatch.setattr(
        acquisition_probe, "run_probe", lambda **kwargs: captured.update(kwargs)
    )
    monkeypatch.setattr(
        sys, "argv", ["acquisition_probe", "run-manifest", str(manifest_path)]
    )

    assert acquisition_probe.main() == 0

    queries = captured["queries"]
    assert any(query.cell_id == "ats_global_snapshot" for query in queries)
    assert any(query.is_synthetic_control for query in queries)
    assert {query.cell_id for query in queries} >= {
        cell_id
        for lane in load_search_contract(
            ROOT / "config/product_search/search_contract.v1.yaml"
        ).lanes.values()
        for cell_id in lane.cells
    }
    assert captured["isolation"]["ashby"].mode == "api"
    assert captured["isolation"]["greenhouse"].mode == "api"


def test_probe_summary_keeps_open_market_and_seeded_ats_denominators_separate(
    tmp_path: Path,
) -> None:
    queries = (
        ProbeQuery(
            query_id="open-query",
            cell_id="uk",
            source_family="duckduckgo",
            query="VP Product United Kingdom",
        ),
        next(query for query in build_snapshot_queries() if query.source_family == "ashby"),
    )

    def open_source(_request: object) -> list[dict[str, str]]:
        return [
            {
                "source_id": "one",
                "url": "https://example.test/jobs/one",
                "title": "VP Product",
                "company": "One Company",
                "description": "Own product strategy",
            }
        ]

    def seeded_source(_request: object) -> list[dict[str, str]]:
        return [
            {
                "source_id": "seeded-one",
                "url": "https://example.test/jobs/one",
                "title": "VP Product",
                "company": "One Company",
                "description": "Own product strategy",
            }
        ]

    result = run_probe(
        run_id="c2-denominators",
        queries=queries,
        sources={"duckduckgo": open_source, "ashby": seeded_source},
        output_dir=tmp_path,
        isolation={
            "duckduckgo": SourceIsolation(
                mode="exclusive_lock", path=tmp_path / "duck.lock", collection_method="browser"
            ),
            "ashby": SourceIsolation(mode="api", path=None, collection_method="api"),
        },
        runtime_capability_checks={
            "duckduckgo": lambda: RuntimeCapabilityResult(state="ready")
        },
    )

    denominators = json.loads(
        (tmp_path / "summary.json").read_text(encoding="utf-8")
    )["denominators"]
    assert denominators["open_market"]["unique_canonical_vacancies"] == 1
    assert denominators["seeded_ats_snapshot"]["unique_canonical_vacancies"] == 1
    assert denominators["combined_diagnostic"]["unique_canonical_vacancies"] == 2
    assert result.denominators == denominators


def test_c2_mapping_records_verified_and_unsupported_ui_results() -> None:
    mapping = load_linkedin_geography_mapping(
        ROOT / "config/product_search/linkedin_geography.v1.yaml"
    )
    assert mapping.version == "1.1"
    assert {
        cell_id for cell_id, target in mapping.items() if target.status == "verified"
    } == {
        "australia",
        "bahrain",
        "benelux",
        "canada",
        "dach",
        "india",
        "japan",
        "kuwait",
        "kyrgyzstan",
        "latin_america",
        "new_zealand",
        "nordics",
        "oman",
        "qatar",
        "saudi_arabia",
        "south_korea",
        "southeast_asia_other",
        "tajikistan",
        "turkmenistan",
        "united_arab_emirates",
        "uzbekistan",
        "uk",
        "singapore",
        "kazakhstan",
    }
    assert {
        cell_id for cell_id, target in mapping.items() if target.status == "unsupported"
    } == {
        "cee",
        "east_asia_other",
        "genuinely_location_independent",
        "remaining_europe",
        "us_feasibility",
    }
    control = acquisition_probe.select_bounded_negative_control(
        mapping,
        excluded_cell_ids=(),
        declared={
            "selection_rule": "first_alphabetical_unsupported_excluding_bounded_v1",
            "cell_id": "cee",
            "status": "unsupported",
            "mapping_version": "1.1",
        },
    )
    assert control["cell_id"] == "cee"


def test_run_manifest_wires_runtime_capability_checks_from_composition_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "experiment"
    (root / "runtime/config/product_search").mkdir(parents=True)
    shutil.copy2(
        ROOT / "config/product_search/search_contract.v1.yaml",
        root / "runtime/config/product_search/search_contract.v1.yaml",
    )
    manifest = _c1a_manifest(root)
    manifest["source_isolation"] = {
        "linkedin": {
            **manifest["source_isolation"]["linkedin"],
            "mode": "cloned_profile",
            "path": str(root / "browser-profile"),
            "cdp_url": "http://127.0.0.1:19222",
        },
    }
    manifest_path = tmp_path / "manifest.yaml"
    manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=True), encoding="utf-8")

    mapping = _c1a_mapping()
    calls: list[tuple[str, str | None, Path | None]] = []
    captured: dict[str, object] = {}

    def fake_browser_ready(
        source: str,
        *,
        cdp_url: str | None = None,
        profile: Path | None = None,
        force_recycle: bool = False,
    ) -> str:
        calls.append((source, cdp_url, profile))
        return cdp_url or "http://127.0.0.1:19222"

    monkeypatch.setattr(
        acquisition_probe,
        "verify_experiment_runtime",
        lambda _manifest: None,
    )
    monkeypatch.setattr(
        acquisition_probe,
        "load_linkedin_geography_mapping",
        lambda _path=None: mapping,
    )
    monkeypatch.setattr(
        acquisition_probe,
        "resolve_public_sources",
        lambda **_kwargs: {"linkedin": lambda _request: []},
    )
    monkeypatch.setattr(browser_worker, "_ensure_browser_desktop", fake_browser_ready)

    def capture_run_probe(**kwargs: object) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(acquisition_probe, "run_probe", capture_run_probe)
    monkeypatch.setattr(
        sys, "argv", ["acquisition_probe", "run-manifest", str(manifest_path)]
    )

    environment_before = dict(os.environ)
    try:
        assert acquisition_probe.main() == 0
    finally:
        os.environ.clear()
        os.environ.update(environment_before)

    checks = captured["runtime_capability_checks"]
    assert set(checks) == {"linkedin"}
    assert checks["linkedin"]().state == "ready"
    assert calls == [
        ("linkedin", "http://127.0.0.1:19222", root / "browser-profile")
    ]


def test_synthetic_control_does_not_change_linkedin_source_state(tmp_path: Path) -> None:
    isolation = {
        "linkedin": SourceIsolation(
            mode="exclusive_lock",
            path=tmp_path / "locks/linkedin.lock",
            collection_method="browser",
        )
    }
    ordinary = ProbeQuery(
        query_id="ordinary",
        cell_id="uk",
        source_family="linkedin",
        query="VP Product United Kingdom",
    )
    control = ProbeQuery(
        query_id="synthetic-control",
        cell_id="synthetic_c1a_unsupported",
        source_family="linkedin",
        query="C1A nonexistent geography target",
        primary_geography="C1A nonexistent geography target",
        geography_target=LinkedInGeographyTarget(
            location="C1A nonexistent geography target",
            status="unsupported",
        ),
        is_synthetic_control=True,
    )

    def run(queries: tuple[ProbeQuery, ...]):
        return run_probe(
            run_id="synthetic-state-isolation",
            queries=queries,
            sources={"linkedin": lambda _request: []},
            output_dir=tmp_path / ("with-control" if len(queries) == 2 else "without-control"),
            runtime_capability_checks={
                "linkedin": lambda: RuntimeCapabilityResult(state="ready")
            },
            isolation=isolation,
            max_attempts=1,
        )

    without_control = run((ordinary,))
    with_control = run((ordinary, control))

    assert with_control.source_states["linkedin"] == without_control.source_states["linkedin"]
    assert with_control.source_states == {"linkedin": "observed"}


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


@pytest.mark.parametrize(
    ("events", "expected"),
    (
        (("completed", "blocked"), "blocked"),
        (("completed", "degraded"), "degraded"),
        (("blocked", "degraded"), "degraded"),
        (("completed", "completed"), "completed"),
        (("blocked", "blocked"), "blocked"),
        (("degraded", "degraded"), "degraded"),
    ),
)
def test_b3_pair_outcome_conflict_resolution_reaches_summary(
    tmp_path: Path,
    events: tuple[str, str],
    expected: str,
) -> None:
    class SequenceSource:
        def __init__(self) -> None:
            self._events = list(events)
            self.last_errors: tuple[str, ...] = ()

        def __call__(self, _query: str) -> list[dict[str, str]]:
            event = self._events.pop(0)
            if event == "blocked":
                raise ProbeSourceBlocked("anti_bot", "challenge page")
            self.last_errors = ("planned page failed",) if event == "degraded" else ()
            return [_b1_record(f"row-{event}-{len(self._events)}")]

    run_probe(
        run_id="b3-conflicts",
        queries=(
            {
                "query_id": "q-one",
                "cell_id": "b3-cell",
                "source_family": "alpha",
                "query": "first role",
            },
            {
                "query_id": "q-two",
                "cell_id": "b3-cell",
                "source_family": "alpha",
                "query": "second role",
            },
        ),
        sources={"alpha": SequenceSource()},
        output_dir=tmp_path,
        isolation={
            "alpha": SourceIsolation(
                mode="api",
                path=tmp_path / "alpha.lock",
                collection_method="api",
            )
        },
        max_attempts=1,
    )

    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))

    assert summary["cell_family_attempts"] == [
        {
            **summary["cell_family_attempts"][0],
            "outcome": expected,
        }
    ]


def test_b3_pair_attempt_round_trips_from_summary_file(tmp_path: Path) -> None:
    class DegradedSource:
        last_errors = ("second planned page failed",)

        def __call__(self, _query: str) -> list[dict[str, str]]:
            return [_b1_record("alpha-1"), _b1_record("alpha-2")]

    run_probe(
        run_id="b3-run",
        queries=(
            {
                "query_id": "q-alpha",
                "cell_id": "b3-cell",
                "source_family": "alpha",
                "query": "Head of Product",
            },
        ),
        sources={"alpha": DegradedSource()},
        output_dir=tmp_path,
        isolation={
            "alpha": SourceIsolation(
                mode="api",
                path=tmp_path / "alpha.lock",
                collection_method="api",
            )
        },
        minimum_independent_families_by_cell={"b3-cell": 1},
        credited_records_by_cell={"b3-cell": 1},
        max_attempts=1,
        now=lambda: datetime(2026, 8, 27, 12, 34, 56, tzinfo=timezone.utc),
    )

    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    pair = summary["cell_family_attempts"][0]

    assert pair["cell_id"] == "b3-cell"
    assert pair["source_family"] == "alpha"
    assert pair["outcome"] == "degraded"
    assert pair["timestamp"] == "2026-08-27T12:34:56+00:00"
    assert pair["received_records"] == 2
    assert pair["credited_records"] == 1
    assert pair["credited_records_status"] == "determined"
    assert pair["credited_records_reason"] is None
    assert len(pair["artifact_references"]) == 2
    assert all((tmp_path / reference).is_file() for reference in pair["artifact_references"])


def test_b3_pair_credited_records_come_from_b2_attribution(tmp_path: Path) -> None:
    run_probe(
        run_id="b3-b2-run",
        queries=(
            {
                "query_id": "q-alpha",
                "cell_id": "b3-cell",
                "source_family": "alpha",
                "query": "Head of Product",
            },
        ),
        sources={
            "alpha": lambda _query: [
                {
                    **_b1_record("alpha-1"),
                    "location": "Kazakhstan",
                }
            ]
        },
        output_dir=tmp_path,
        isolation={
            "alpha": SourceIsolation(
                mode="api",
                path=tmp_path / "alpha.lock",
                collection_method="api",
            )
        },
        geography_mapping={
            "b3-cell": {
                "location": "Kazakhstan",
                "status": "verified",
                "country_codes": ["KZ"],
            }
        },
        max_attempts=1,
    )

    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))

    assert summary["cell_family_attempts"][0]["credited_records"] == 1


def test_b3_summary_keeps_pair_fields_in_one_record(tmp_path: Path) -> None:
    result = _b1_run(tmp_path, {"alpha": [_b1_record("alpha-1")]})
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))

    assert result.cell_family_attempts
    assert len(summary["cell_family_attempts"]) == 1
    pair = summary["cell_family_attempts"][0]
    assert {
        "cell_id",
        "source_family",
        "outcome",
        "timestamp",
        "received_records",
        "credited_records",
        "artifact_references",
    } <= set(pair)
    assert "cell_attempts" not in summary
    assert "source_family_attempts" not in summary




































def test_b3_multi_pair_cell_credit_is_unknown_not_zero(tmp_path: Path) -> None:
    families = {
        "linkedin": [_b1_record("linkedin-1")],
        "duckduckgo": [_b1_record("duckduckgo-1")],
    }

    result = _b1_run(tmp_path, families, credited=2)
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    pairs = summary["cell_family_attempts"]

    assert result.credited_records_provenance == {
        "b1-cell": "caller_supplied"
    }
    assert len(pairs) == 2
    assert all(pair["credited_records"] is None for pair in pairs)
    assert all(
        pair["credited_records_status"] == "undetermined"
        and pair["credited_records_reason"]
        == "cell_level_credit_not_attributable_to_multiple_pairs"
        for pair in pairs
    )
    assert all(
        pair["credited_records"] is None for pair in pairs
    ) or sum(pair["credited_records"] for pair in pairs) == 2


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


def _b2_mapping(**cells: tuple[str, ...]):
    return {
        cell_id: {
            "location": cell_id,
            "status": "verified",
            "country_codes": list(country_codes),
        }
        for cell_id, country_codes in cells.items()
    }


def _b2_record(
    source_id: str,
    cell_id: str,
    location: str,
    url: str | None = None,
    source_family: str = "linkedin",
):
    return {
        "source_id": source_id,
        "cell_id": cell_id,
        "source_family": source_family,
        "url": url or f"https://www.linkedin.com/jobs/view/{source_id}/?trackingId={source_id}",
        "location": location,
        "title": "Head of Product",
        "company": "Acme",
        "description": "Own product strategy and P&L",
    }


def _b2_summary(records, mapping, **kwargs):
    builder = getattr(acquisition_probe, "build_geography_summary", None)
    assert callable(builder), "B2 geography summary builder is missing"
    return builder(records, mapping, **kwargs)


def test_critical_degradation_reaches_pair_evidence_and_blocks_completion(
    tmp_path: Path,
) -> None:
    class CriticalSource:
        last_errors: tuple[str, ...] = ()
        last_trace = {
            "stop_reason": "critical_degradation",
            "failure_reason": "login_wall_or_auth_redirect",
        }
        last_health = {"critical_degradation": True, "status": "blocked"}

        def __call__(self, _query: object) -> list[dict[str, str]]:
            return [{
                "source_id": "partial-row",
                "url": "https://example.test/jobs/partial-row",
                "title": "Head of Product",
                "company": "Acme",
            }]

    result = run_probe(
        run_id="critical-degradation",
        queries=({
            "query_id": "q-critical",
            "cell_id": "uk",
            "source_family": "linkedin",
            "query": "Head of Product United Kingdom",
        },),
        sources={"linkedin": CriticalSource()},
        output_dir=tmp_path,
        isolation={
            "linkedin": SourceIsolation(
                mode="exclusive_lock",
                path=tmp_path / "linkedin.lock",
                collection_method="browser",
            )
        },
        runtime_capability_checks={
            "linkedin": lambda: RuntimeCapabilityResult(state="ready")
        },
        max_attempts=1,
    )

    pair = result.model_dump(mode="json")["cell_family_attempts"][0]
    assert pair["outcome"] == "degraded"
    assert pair["critical_degradation"] is True
    assert pair["critical_degradation_reason"] == "login_wall_or_auth_redirect"
    assert result.source_states == {"linkedin": "observed_with_failures"}


def test_linkedin_source_forwards_critical_degradation_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_fetch(_query: str, **_kwargs: object) -> list[object]:
        fake_fetch.last_trace = {
            "stop_reason": "critical_degradation",
            "failure_reason": "login_wall_or_auth_redirect",
        }
        fake_fetch.last_health = {"critical_degradation": True, "status": "blocked"}
        fake_fetch.last_errors = ("login_wall_or_auth_redirect",)
        return []

    monkeypatch.setattr(job_sources, "fetch_linkedin_vacancies", fake_fetch)
    source = acquisition_probe.resolve_public_sources(run_id="critical-source")["linkedin"]

    source("Head of Product United Kingdom")

    assert source.last_errors == ("login_wall_or_auth_redirect",)


def test_bounded_run_executes_a_declared_unsupported_mapping_control(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "experiment"
    (root / "runtime/config/product_search").mkdir(parents=True)
    shutil.copy2(
        ROOT / "config/product_search/search_contract.v1.yaml",
        root / "runtime/config/product_search/search_contract.v1.yaml",
    )
    base_mapping = _c1a_mapping()
    mapping = LinkedInGeographyMapping(
        {
            **base_mapping,
            "cee": LinkedInGeographyTarget(
                location="Central and Eastern Europe",
                status="unsupported",
                country_codes=(),
            ),
        },
        version=base_mapping.version,
        normalization_rule_version=base_mapping.normalization_rule_version,
        contamination_formula_version=base_mapping.contamination_formula_version,
        contamination_threshold=base_mapping.contamination_threshold,
        city_country_codes=base_mapping.city_country_codes,
    )
    manifest = _c1a_manifest(root)
    manifest["bounded_proof"]["negative_control"] = {
        "selection_rule": BOUNDED_PROOF_SELECTION_RULE,
        "cell_id": "cee",
        "status": "unsupported",
        "location": "Central and Eastern Europe",
        "mapping_version": mapping.version,
    }
    manifest_path = tmp_path / "manifest.yaml"
    manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=True), encoding="utf-8")

    queries = (
        ProbeQuery(
            query_id="uk-query",
            cell_id="uk",
            source_family="linkedin",
            query="VP Product United Kingdom",
        ),
        ProbeQuery(
            query_id="cee-control",
            cell_id="cee",
            source_family="linkedin",
            query="Central and Eastern Europe",
            primary_geography="Central and Eastern Europe",
            geography_target=mapping["cee"],
        ),
    )
    captured: dict[str, object] = {}
    monkeypatch.setattr(acquisition_probe, "verify_experiment_runtime", lambda _manifest: None)
    monkeypatch.setattr(acquisition_probe, "load_linkedin_geography_mapping", lambda _path=None: mapping)
    monkeypatch.setattr(acquisition_probe, "expand_queries", lambda *args, **kwargs: queries)
    monkeypatch.setattr(acquisition_probe, "resolve_public_sources", lambda **_kwargs: {})
    monkeypatch.setattr(acquisition_probe, "resolve_runtime_capability_checks", lambda _isolation: {})
    monkeypatch.setattr(acquisition_probe, "run_probe", lambda **kwargs: captured.update(kwargs))
    monkeypatch.setattr(sys, "argv", ["acquisition_probe", "run-manifest", str(manifest_path)])

    environment_before = dict(os.environ)
    try:
        assert acquisition_probe.main() == 0
    finally:
        os.environ.clear()
        os.environ.update(environment_before)

    executed_queries = captured["queries"]
    assert any(
        query.cell_id == "cee" and query.is_synthetic_control
        for query in executed_queries
    )


def test_successful_retry_replaces_failure_for_the_same_query(
    tmp_path: Path,
) -> None:
    class RetrySource:
        def __init__(self) -> None:
            self.calls = 0
            self.last_errors: tuple[str, ...] = ()

        def __call__(self, _query: object) -> list[dict[str, str]]:
            self.calls += 1
            if self.calls == 1:
                raise TimeoutError("first attempt timed out")
            return [{
                "source_id": "retry-row",
                "url": "https://example.test/jobs/retry-row",
                "title": "Head of Product",
                "company": "Acme",
            }]

    result = run_probe(
        run_id="retry-recovery",
        queries=({
            "query_id": "q-retry",
            "cell_id": "uk",
            "source_family": "alpha",
            "query": "Head of Product",
        },),
        sources={"alpha": RetrySource()},
        output_dir=tmp_path,
        isolation={
            "alpha": SourceIsolation(
                mode="api", path=tmp_path / "alpha.lock", collection_method="api"
            )
        },
        max_attempts=2,
    )

    pair = result.model_dump(mode="json")["cell_family_attempts"][0]
    assert pair["outcome"] == "completed"


def test_successful_retry_is_query_scoped_and_preserves_other_role_failure(
    tmp_path: Path,
) -> None:
    class MultiRoleSource:
        def __init__(self) -> None:
            self.vp_calls = 0
            self.last_errors: tuple[str, ...] = ()

        def __call__(self, query: object) -> list[dict[str, str]]:
            if query == "Chief Product Officer":
                raise ProbeSourceBlocked("anti_bot")
            if query == "VP Product":
                self.vp_calls += 1
                if self.vp_calls == 1:
                    raise TimeoutError("transient timeout")
                return [{
                    "source_id": "vp-row",
                    "url": "https://example.test/jobs/vp-row",
                    "title": "VP Product",
                    "company": "Acme",
                }]
            raise AssertionError(f"unexpected role query: {query}")

    result = run_probe(
        run_id="retry-query-scope",
        queries=(
            {
                "query_id": "q-chief",
                "cell_id": "uk",
                "source_family": "alpha",
                "query": "Chief Product Officer",
            },
            {
                "query_id": "q-vp",
                "cell_id": "uk",
                "source_family": "alpha",
                "query": "VP Product",
            },
        ),
        sources={"alpha": MultiRoleSource()},
        output_dir=tmp_path,
        isolation={
            "alpha": SourceIsolation(
                mode="api", path=tmp_path / "alpha.lock", collection_method="api"
            )
        },
        max_attempts=2,
    )

    pair = result.model_dump(mode="json")["cell_family_attempts"][0]
    assert pair["outcome"] == "blocked"
