from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator
import yaml

from job_intel.product_search.contracts import SelectionMode


class ObservabilityState(str, Enum):
    QUALIFIED_RESULTS_FOUND = "qualified_results_found"
    SEARCHED_NO_QUALIFIED_RESULTS = "searched_no_qualified_results"
    BLOCKED = "blocked"
    NOT_OBSERVED = "not_observed"

    @property
    def meaningfully_observed(self) -> bool:
        return self in {
            self.QUALIFIED_RESULTS_FOUND,
            self.SEARCHED_NO_QUALIFIED_RESULTS,
        }


class SearchCell(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    cell_id: str = Field(min_length=1)
    primary_geography: str = Field(min_length=1)
    independent: bool = True
    role_families: tuple[str, ...] = Field(min_length=1)
    search_window_days: int = Field(gt=0)
    minimum_independent_families: int = Field(gt=0)
    source_families: tuple[str, ...] = ()
    capability_gap: str | None = None

    @model_validator(mode="after")
    def invocation_or_gap(self) -> Self:
        if bool(self.source_families) == bool(self.capability_gap):
            raise ValueError("cell requires exactly one invocation plan or capability gap")
        return self


class SearchLane(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    cells: dict[str, SearchCell]
    minimum_delivery: int | None = None
    fallback: bool = False
    lowered_bar: bool = False


class PortfolioPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    weekly_delivery_cap: int
    daily_working_range: tuple[int, int]
    exploration_weekly_range: tuple[int, int]
    geographic_delivery_quota: dict[str, int] | None
    minimum_fill: int | None
    concentration_diagnostics: dict[str, float | tuple[float, float]]


class SearchContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: str
    product_authority_id: str
    selection_modes: tuple[SelectionMode, ...]
    observability_states: tuple[ObservabilityState, ...]
    discovery_origins: tuple[str, ...]
    mandate_role_families: tuple[str, ...]
    transferable_patterns: tuple[str, ...]
    industry_families: tuple[str, ...]
    primary_business_models: tuple[str, ...]
    family_attempt_contract: tuple[str, ...]
    lanes: dict[str, SearchLane]
    portfolio: PortfolioPolicy


def resolve_selection_mode(
    *,
    core_qualified: bool,
    uncertain_hypothesis: str | None,
    unfamiliar_company: bool = False,
    unfamiliar_geography: bool = False,
    unfamiliar_industry: bool = False,
) -> SelectionMode:
    del unfamiliar_company, unfamiliar_geography, unfamiliar_industry
    if core_qualified:
        return SelectionMode.CORE
    if not (uncertain_hypothesis or "").strip():
        raise ValueError("Exploration requires a named uncertain hypothesis")
    return SelectionMode.EXPLORATION


def load_search_contract(path: Path | str) -> SearchContract:
    return SearchContract.model_validate(
        yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    )
