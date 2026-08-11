from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from job_intel.product_search.search_contract import (
    ObservabilityState,
    SelectionMode,
    load_search_contract,
    resolve_selection_mode,
)


ROOT = Path(__file__).resolve().parents[2]
CONTRACT_PATH = ROOT / "config/product_search/search_contract.v1.yaml"


def test_contract_has_exact_lanes_and_country_cells() -> None:
    contract = load_search_contract(CONTRACT_PATH)

    assert set(contract.lanes) == {
        "europe_including_uk",
        "apac_excluding_australia_new_zealand",
        "gcc",
        "americas",
        "global_remote",
        "australia_new_zealand",
        "kazakhstan",
        "other_central_asia",
    }
    assert set(contract.lanes["europe_including_uk"].cells) == {
        "uk", "dach", "benelux", "nordics", "cee", "remaining_europe"
    }
    assert set(contract.lanes["gcc"].cells) == {
        "bahrain", "kuwait", "oman", "qatar", "saudi_arabia", "united_arab_emirates"
    }
    assert set(contract.lanes["americas"].cells) == {"canada", "latin_america", "us_feasibility"}
    assert set(contract.lanes["australia_new_zealand"].cells) == {"australia", "new_zealand"}
    assert set(contract.lanes["other_central_asia"].cells) == {
        "kyrgyzstan", "tajikistan", "turkmenistan", "uzbekistan"
    }


def test_kazakhstan_is_normal_and_central_asia_is_independent() -> None:
    contract = load_search_contract(CONTRACT_PATH)
    kz = contract.lanes["kazakhstan"]
    central_asia = contract.lanes["other_central_asia"]

    assert kz.minimum_delivery == 0
    assert kz.fallback is False
    assert kz.lowered_bar is False
    assert all(cell.independent for cell in central_asia.cells.values())
    assert len({cell.primary_geography for cell in central_asia.cells.values()}) == 4


def test_selection_mode_is_closed_and_not_inferred_from_unfamiliar_context() -> None:
    assert {mode.value for mode in SelectionMode} == {"Core", "Exploration"}
    assert resolve_selection_mode(core_qualified=True, uncertain_hypothesis=None) is SelectionMode.CORE
    assert resolve_selection_mode(
        core_qualified=True,
        uncertain_hypothesis=None,
        unfamiliar_company=True,
        unfamiliar_geography=True,
        unfamiliar_industry=True,
    ) is SelectionMode.CORE
    assert resolve_selection_mode(
        core_qualified=False, uncertain_hypothesis="test telecom transferability"
    ) is SelectionMode.EXPLORATION
    with pytest.raises(ValueError, match="named uncertain hypothesis"):
        resolve_selection_mode(core_qualified=False, uncertain_hypothesis=None)


def test_observability_states_distinguish_no_results_from_no_observation() -> None:
    assert ObservabilityState.SEARCHED_NO_QUALIFIED_RESULTS.meaningfully_observed is True
    assert ObservabilityState.QUALIFIED_RESULTS_FOUND.meaningfully_observed is True
    assert ObservabilityState.BLOCKED.meaningfully_observed is False
    assert ObservabilityState.NOT_OBSERVED.meaningfully_observed is False


def test_every_cell_has_existing_invocation_plan_or_named_gap() -> None:
    contract = load_search_contract(CONTRACT_PATH)

    for lane in contract.lanes.values():
        for cell in lane.cells.values():
            assert bool(cell.source_families) ^ bool(cell.capability_gap), cell.cell_id
            assert cell.role_families
            assert cell.search_window_days > 0
            assert cell.minimum_independent_families > 0


def test_contract_uses_ceiling_not_minimum_fill_rules() -> None:
    contract = load_search_contract(CONTRACT_PATH)

    assert contract.portfolio.weekly_delivery_cap == 35
    assert contract.portfolio.daily_working_range == (5, 7)
    assert contract.portfolio.exploration_weekly_range == (5, 7)
    assert contract.portfolio.geographic_delivery_quota is None
    assert contract.portfolio.minimum_fill is None
    assert contract.portfolio.concentration_diagnostics == {
        "employer_max_share": 0.20,
        "financial_services_working_range": (0.30, 0.40),
    }


def test_contract_is_versioned_hashed_and_subordinate_to_product_sot() -> None:
    contract = load_search_contract(CONTRACT_PATH)
    authority = __import__("yaml").safe_load(
        (ROOT / "docs/authority-manifest.yaml").read_text(encoding="utf-8")
    )
    record = authority["technical_contracts"]["search_contract_v1"]

    assert contract.version == "1.0.0"
    assert contract.product_authority_id == "PS-SOT-2026-08-10-v1"
    assert record["status"] == "technical_execution_policy"
    assert record["subordinate_to"] == "PS-SOT-2026-08-10-v1"
    assert hashlib.sha256(CONTRACT_PATH.read_bytes()).hexdigest() == record["sha256"]
