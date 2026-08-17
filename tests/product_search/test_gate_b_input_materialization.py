from __future__ import annotations

from copy import deepcopy

import pytest
from pydantic import TypeAdapter, ValidationError

from job_intel.product_search.input_materialization import (
    AdmittedIdentityOutcome,
    DiscoveryOutcome,
    DiscoveryReceipt,
    MaterializationReason,
    OfficialLinkRelation,
    SourcePlan,
    UnresolvedIdentityOutcome,
    admit_official_domain,
    build_source_plan,
)

ROOT = "https://boards.example.test/acme/1"
HASH = "b" * 64


def plan() -> SourcePlan:
    return build_source_plan(selection_key="a" * 64, company_label="Acme", vacancy_uri=ROOT)


def receipt(link: str = "https://www.acme.test/careers") -> dict:
    return {"schema_version": "1.0.0", "root_uri": ROOT, "requests": [{"uri": ROOT, "status": 200, "content_type": "text/html", "content_bytes": 100, "content_sha256": HASH, "redirect_to": None}], "explicit_official_links": [{"uri": link, "relation": "official_careers", "source_request_uri": ROOT, "evidence_sha256": HASH}]}


def test_direct_models_enforce_version_exact_root_and_caps() -> None:
    base = plan().model_dump(mode="json")
    for mutation in ({**base, "schema_version": "2"}, {**base, "discovery_roots": []}, {**base, "discovery_roots": [ROOT, "https://other.test"]}, {**base, "max_requests": 4}, {**base, "max_redirects": 3}):
        with pytest.raises(ValidationError):
            SourcePlan.model_validate(mutation)


@pytest.mark.parametrize("path,value", [("root_uri", "https://u:p@boards.test/1"), ("request_uri", "http://boards.test/1"), ("redirect_to", "https://u:p@boards.test/2"), ("link_uri", "https://acme.test/#secret")])
def test_all_nested_urls_reject_unsafe_values(path: str, value: str) -> None:
    data = receipt()
    if path == "root_uri": data[path] = value
    elif path == "request_uri": data["requests"][0]["uri"] = value
    elif path == "redirect_to": data["requests"][0].update(status=302, redirect_to=value)
    else: data["explicit_official_links"][0]["uri"] = value
    with pytest.raises(ValidationError): DiscoveryReceipt.model_validate(data)


def test_contiguous_chain_caps_and_terminal_redirect() -> None:
    data = receipt()
    data["requests"] = [
        {"uri": ROOT, "status": 302, "content_type": "text/html", "content_bytes": 1, "content_sha256": "1"*64, "redirect_to": "https://boards.example.test/2"},
        {"uri": "https://boards.example.test/2", "status": 301, "content_type": "text/html", "content_bytes": 1, "content_sha256": "2"*64, "redirect_to": "https://boards.example.test/3"},
        {"uri": "https://boards.example.test/3", "status": 200, "content_type": "text/html", "content_bytes": 1, "content_sha256": HASH, "redirect_to": None},
    ]
    DiscoveryReceipt.model_validate(data)
    bad = deepcopy(data); bad["requests"][1]["uri"] = "https://wrong.test"
    with pytest.raises(ValidationError): DiscoveryReceipt.model_validate(bad)
    bad = deepcopy(data); bad["requests"][-1].update(status=302, redirect_to="https://four.test")
    with pytest.raises(ValidationError): DiscoveryReceipt.model_validate(bad)
    bad = deepcopy(data); bad["requests"].append(deepcopy(bad["requests"][-1]))
    with pytest.raises(ValidationError): DiscoveryReceipt.model_validate(bad)


def test_admission_requires_plan_root_capture_and_non_ats_unambiguous_domain() -> None:
    admitted = admit_official_domain(plan(), DiscoveryReceipt.model_validate(receipt()))
    assert isinstance(admitted, AdmittedIdentityOutcome)
    wrong = receipt(); wrong["explicit_official_links"][0]["evidence_sha256"] = "c"*64
    assert isinstance(admit_official_domain(plan(), DiscoveryReceipt.model_validate(wrong)), UnresolvedIdentityOutcome)
    wrong = receipt(); wrong["explicit_official_links"][0]["source_request_uri"] = "https://uncaptured.test"
    assert isinstance(admit_official_domain(plan(), DiscoveryReceipt.model_validate(wrong)), UnresolvedIdentityOutcome)
    assert isinstance(admit_official_domain(plan(), DiscoveryReceipt.model_validate(receipt(ROOT))), UnresolvedIdentityOutcome)
    conflict = receipt(); conflict["explicit_official_links"].append({"uri": "https://other.test", "relation": "official_company", "source_request_uri": ROOT, "evidence_sha256": HASH})
    result = admit_official_domain(plan(), DiscoveryReceipt.model_validate(conflict))
    assert MaterializationReason.AMBIGUOUS_COMPANY_IDENTITY in result.reasons
    other = receipt(); other["root_uri"] = other["requests"][0]["uri"] = "https://unrelated.test"
    assert isinstance(admit_official_domain(plan(), DiscoveryReceipt.model_validate(other)), UnresolvedIdentityOutcome)


@pytest.mark.parametrize("uri,domain", [("https://BÜCHER.example./careers", "xn--bcher-kva.example"), ("https://acme.test:443/careers", "acme.test")])
def test_canonical_authority(uri: str, domain: str) -> None:
    result = admit_official_domain(plan(), DiscoveryReceipt.model_validate(receipt(uri)))
    assert result.authority.domain == domain
    assert result.authority.canonical_uri.startswith("https://" + domain + "/")


@pytest.mark.parametrize("uri", ["https://127.0.0.1", "https://[::1]", "https://acme.test:8443", "https://bad_host.test", "https://-bad.test", "https://localhost"])
def test_disallowed_authorities_fail_deserialization(uri: str) -> None:
    with pytest.raises(ValidationError): DiscoveryReceipt.model_validate(receipt(uri))


def test_closed_contract_mutations_roundtrip_and_unresolved_shape() -> None:
    base = receipt()
    for mutation in ({**base, "schema_version": "9"}, {**base, "extra": True}, {**base, "explicit_official_links": [{**base["explicit_official_links"][0], "relation": "affiliate"}]}, {**base, "requests": [{**base["requests"][0], "status": 99}]}, {**base, "requests": [{**base["requests"][0], "content_bytes": -1}]}, {**base, "requests": [{**base["requests"][0], "content_type": ""}]}):
        with pytest.raises(ValidationError): DiscoveryReceipt.model_validate(mutation)
    parsed = DiscoveryReceipt.model_validate(base)
    assert DiscoveryReceipt.model_validate_json(parsed.model_dump_json()) == parsed
    assert OfficialLinkRelation.OFFICIAL_CAREERS.value == "official_careers"
    unresolved = admit_official_domain(plan(), DiscoveryReceipt.model_validate({**receipt(), "explicit_official_links": []}))
    assert isinstance(unresolved, UnresolvedIdentityOutcome)
    dumped = unresolved.model_dump(mode="json")
    assert dumped["status"] == "unresolved" and dumped["reasons"]
    assert not any("task10" in key or "input_hash" in key for key in dumped)
    assert TypeAdapter(DiscoveryOutcome).validate_json(unresolved.model_dump_json()) == unresolved
    admitted = admit_official_domain(plan(), DiscoveryReceipt.model_validate(receipt()))
    assert TypeAdapter(DiscoveryOutcome).validate_json(admitted.model_dump_json()) == admitted
    with pytest.raises(ValidationError):
        UnresolvedIdentityOutcome.model_validate({**dumped, "task10_input_hash": "a"*64})
    with pytest.raises(ValidationError):
        plan().max_requests = 2


def test_non_redirect_3xx_status_cannot_claim_a_redirect_target() -> None:
    data = receipt()
    data["requests"][0].update(status=304, redirect_to="https://boards.example.test/2")
    with pytest.raises(ValidationError):
        DiscoveryReceipt.model_validate(data)
