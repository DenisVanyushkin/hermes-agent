from __future__ import annotations

from enum import Enum
from pathlib import Path
import re
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator
import yaml


class CapabilityStatus(str, Enum):
    PROVEN = "proven"
    PARTIAL = "partial"
    BLOCKED = "blocked"
    UNKNOWN = "unknown"


class InspectionEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    commit: str = Field(min_length=1)
    inspected_at: str = Field(min_length=1)


class SourceCapability(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_id: str = Field(min_length=1)
    family: str = Field(min_length=1)
    status: CapabilityStatus
    public_interface: str = Field(min_length=1)
    seed_dependencies: tuple[str, ...]
    query_controls: tuple[str, ...]
    geography_controls: tuple[str, ...]
    freshness: str = Field(min_length=1)
    auth_state: str = Field(min_length=1)
    evidence_completeness: str = Field(min_length=1)
    limits: tuple[str, ...]
    failure_domain: str = Field(min_length=1)
    inspection: InspectionEvidence

    @property
    def proves_broad_market_discovery(self) -> bool:
        return (
            not self.seed_dependencies
            and "broad_market" in self.query_controls
            and self.status is CapabilityStatus.PROVEN
        )


class CoverageDimensions(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    search_cells: dict[str, CapabilityStatus]
    mandate_vocabularies: tuple[str, ...]
    industry_business_models: tuple[str, ...]


class CapabilityRegistry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: str = Field(min_length=1)
    sources: tuple[SourceCapability, ...]
    coverage_dimensions: CoverageDimensions | None = None

    @model_validator(mode="after")
    def unique_source_ids(self) -> Self:
        ids = [source.source_id for source in self.sources]
        if len(ids) != len(set(ids)):
            raise ValueError("source_id values must be unique")
        return self

    @property
    def source_ids(self) -> set[str]:
        return {source.source_id for source in self.sources}

    def independent_families(self) -> set[str]:
        return {source.family for source in self.sources}

    @property
    def independent_family_count(self) -> int:
        return len(self.independent_families())

    def by_id(self, source_id: str) -> SourceCapability:
        for source in self.sources:
            if source.source_id == source_id:
                return source
        raise KeyError(source_id)

    def unregistered_live_sources(self, live_source_ids: set[str]) -> set[str]:
        return set(live_source_ids) - self.source_ids

    def stale_pointers(self, root: Path) -> list[str]:
        stale: list[str] = []
        for source in self.sources:
            module_name, separator, attribute = source.public_interface.rpartition(".")
            if not separator:
                stale.append(source.public_interface)
                continue
            module_path = root.joinpath(*module_name.split(".")).with_suffix(".py")
            if not module_path.is_file():
                stale.append(source.public_interface)
                continue
            source_code = module_path.read_text(encoding="utf-8")
            if not re.search(rf"^(?:async\s+)?def\s+{re.escape(attribute)}\s*\(", source_code, re.MULTILINE):
                stale.append(source.public_interface)
        return stale


def load_capability_registry(path: Path | str) -> CapabilityRegistry:
    payload = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return CapabilityRegistry.model_validate(payload)
