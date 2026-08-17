from __future__ import annotations

from pathlib import Path

import pytest

from job_intel.product_search.input_materialization import (
    DiscoveryReceipt,
    MaterializationReason,
    admit_official_domain,
    build_source_plan,
)


def test_source_plan_uses_only_exact_pinned_vacancy_uri() -> None:
    plan = build_source_plan(
        selection_key="a" * 64,
        company_label="Example Co",
        vacancy_uri="https://jobs.example-ats.test/example/1",
    )
    assert plan.discovery_roots == ("https://jobs.example-ats.test/example/1",)
    assert plan.max_requests == 3
    assert plan.max_redirects == 2


def test_ats_host_alone_never_resolves_employer_identity() -> None:
    receipt = DiscoveryReceipt.model_validate(
        {
            "schema_version": "1.0.0",
            "root_uri": "https://boards.example.test/acme/1",
            "requests": [],
            "explicit_official_links": [],
        }
    )
    assert admit_official_domain("Acme", receipt) is None


def test_explicit_https_employer_link_is_required_and_preserved() -> None:
    receipt = DiscoveryReceipt.model_validate(
        {
            "schema_version": "1.0.0",
            "root_uri": "https://boards.example.test/acme/1",
            "requests": [
                {
                    "uri": "https://boards.example.test/acme/1",
                    "status": 200,
                    "content_type": "text/html",
                    "content_bytes": 100,
                    "content_sha256": "b" * 64,
                    "redirect_to": None,
                }
            ],
            "explicit_official_links": [
                {
                    "uri": "https://www.acme.test/careers",
                    "relation": "official_careers",
                    "evidence_sha256": "b" * 64,
                }
            ],
        }
    )
    admitted = admit_official_domain("Acme", receipt)
    assert admitted is not None
    assert admitted.domain == "www.acme.test"


@pytest.mark.parametrize(
    "uri",
    ["http://acme.test", "https://user:pass@acme.test", "https://acme.test/#x"],
)
def test_non_https_credentials_and_fragments_are_rejected(uri: str) -> None:
    with pytest.raises(ValueError):
        build_source_plan(
            selection_key="a" * 64,
            company_label="Acme",
            vacancy_uri=uri,
        )


def test_unresolved_identity_has_closed_reason_and_no_task10_hash() -> None:
    assert MaterializationReason.UNRESOLVED_COMPANY_IDENTITY.value == (
        "unresolved_company_identity"
    )
