from __future__ import annotations

import json

from job_intel.ats_sources import AtsSourceResult
from job_intel.models import Vacancy
from job_intel.product_search.acquisition_plugins.ats_snapshot import (
    ATS_FAMILIES,
    build_ats_snapshot_sources,
)


def test_snapshot_sources_use_enabled_registry_seeds_and_expose_every_ats(tmp_path) -> None:
    registry = tmp_path / "registry.json"
    registry.write_text(
        json.dumps(
            [
                {
                    "ats_vendor": "greenhouse",
                    "ats_slug": "acme",
                    "acquisition_enabled": True,
                },
                {
                    "ats_vendor": "greenhouse",
                    "ats_slug": "disabled",
                    "acquisition_enabled": False,
                },
            ]
        ),
        encoding="utf-8",
    )
    calls = []

    def fake_fetcher(queries, *, companies=None):
        calls.append((tuple(queries), tuple(companies or ())))
        return AtsSourceResult(
            vacancies=[
                Vacancy(
                    source="greenhouse",
                    source_id="one",
                    company="Acme",
                    title="VP Product",
                    location="Remote",
                    url="https://example.com/jobs/one",
                    description="Own product strategy and P&L",
                )
            ],
            errors=[],
            discovered_companies=len(companies or ()),
            pages_fetched=1,
        )

    sources = build_ats_snapshot_sources(
        registry,
        fetchers={family: fake_fetcher for family in ATS_FAMILIES},
    )

    assert set(sources) == set(ATS_FAMILIES)
    assert list(sources["greenhouse"]("executive product roles"))
    assert calls[-1] == (("executive product roles",), ("acme",))


def test_snapshot_source_preserves_partial_results_and_exposes_errors(tmp_path) -> None:
    registry = tmp_path / "registry.json"
    registry.write_text("[]", encoding="utf-8")

    def partial(queries, *, companies=None):
        del queries, companies
        return AtsSourceResult(
            vacancies=[
                Vacancy(
                    source="lever",
                    source_id="one",
                    company="Acme",
                    title="Head of Product",
                    location="London",
                    url="https://example.com/jobs/one",
                    description="Lead the product organisation",
                )
            ],
            errors=["429 rate_limited lever site=other"],
            discovered_companies=2,
            pages_fetched=2,
        )

    source = build_ats_snapshot_sources(
        registry,
        fetchers={family: partial for family in ATS_FAMILIES},
    )["lever"]

    assert len(list(source("executive product roles"))) == 1
    assert source.last_errors == ("429 rate_limited lever site=other",)
