from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
import json
from pathlib import Path
from typing import Any

from job_intel.ats_sources import (
    AtsSourceResult,
    fetch_ashby,
    fetch_greenhouse,
    fetch_lever,
    fetch_personio,
    fetch_recruitee,
    fetch_smartrecruiters,
    fetch_teamtailor,
)


ATS_FAMILIES = (
    "ashby",
    "greenhouse",
    "lever",
    "personio",
    "recruitee",
    "smartrecruiters",
    "teamtailor",
)

Fetcher = Callable[..., AtsSourceResult]

DEFAULT_FETCHERS: Mapping[str, Fetcher] = {
    "ashby": fetch_ashby,
    "greenhouse": fetch_greenhouse,
    "lever": fetch_lever,
    "personio": fetch_personio,
    "recruitee": fetch_recruitee,
    "smartrecruiters": fetch_smartrecruiters,
    "teamtailor": fetch_teamtailor,
}


def _registry_seeds(path: Path) -> dict[str, tuple[str, ...]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    seeds: dict[str, list[str]] = {family: [] for family in ATS_FAMILIES}
    for row in payload:
        if not isinstance(row, dict) or row.get("acquisition_enabled") is not True:
            continue
        family = str(row.get("ats_vendor") or "").strip().casefold()
        slug = str(row.get("ats_slug") or "").strip()
        if family in seeds and slug and slug.casefold() not in {
            item.casefold() for item in seeds[family]
        }:
            seeds[family].append(slug)
    return {family: tuple(values) for family, values in seeds.items()}


class AtsSnapshotSource:
    def __init__(self, family: str, fetcher: Fetcher, seeds: tuple[str, ...]) -> None:
        self.family = family
        self.fetcher = fetcher
        self.seeds = seeds
        self.last_errors: tuple[str, ...] = ()

    def __call__(self, query: str) -> Iterable[Any]:
        result = self.fetcher(
            [query],
            companies=list(self.seeds) or None,
        )
        self.last_errors = tuple(result.errors)
        if not result.vacancies and self.last_errors:
            from job_intel.product_search.acquisition_probe import ProbeSourceBlocked

            raise ProbeSourceBlocked("extraction_failure", "; ".join(self.last_errors[:3]))
        return result.vacancies


def build_ats_snapshot_sources(
    registry_path: Path,
    *,
    fetchers: Mapping[str, Fetcher] = DEFAULT_FETCHERS,
) -> dict[str, AtsSnapshotSource]:
    seeds = _registry_seeds(registry_path)
    return {
        family: AtsSnapshotSource(family, fetchers[family], seeds[family])
        for family in ATS_FAMILIES
    }
