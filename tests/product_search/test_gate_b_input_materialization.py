from __future__ import annotations

from copy import deepcopy
import hashlib
import json

import pytest
from pydantic import TypeAdapter, ValidationError

from job_intel.product_search.input_materialization import (
    AdmittedIdentityOutcome,
    DiscoveryOutcome,
    DiscoveryReceipt,
    ExtractionRule,
    MaterializationReason,
    SourceFamily,
    SourcePlan,
    UnresolvedIdentityOutcome,
    admit_official_domain,
    build_source_plan,
)

ROOT = "https://boards.greenhouse.io/acme/1"
BODY = '<a href="https://www.acme.test/careers" rel="official" data-relation="official_careers">Careers</a>'
BODY_HASH = hashlib.sha256(BODY.encode()).hexdigest()


def plan(family: SourceFamily = SourceFamily.GREENHOUSE, root: str = ROOT) -> SourcePlan:
    return build_source_plan(
        selection_key="a" * 64,
        company_label="Acme",
        vacancy_uri=root,
        source_family=family,
    )


def receipt(link: str = "https://www.acme.test/careers") -> dict:
    fragment = f'<a href="{link}" rel="official" data-relation="official_careers">Careers</a>'
    payload = {
        "schema_version": "1.0.0",
        "root_uri": ROOT,
        "requests": [{
            "uri": ROOT, "status": 200, "content_type": "text/html",
            "content_bytes": len(BODY.encode()), "content_sha256": BODY_HASH,
            "capture_artifact_sha256": BODY_HASH, "redirect_to": None,
        }],
        "explicit_official_links": [{
            "uri": link, "relation": "official_careers",
            "extraction_rule": "anchor_rel_official",
            "source_request_uri": ROOT, "evidence_sha256": BODY_HASH,
            "capture_artifact_sha256": BODY_HASH,
            "extraction_fragment": fragment,
            "extraction_sha256": hashlib.sha256(fragment.encode()).hexdigest(),
            "byte_start": 0, "byte_end": len(fragment.encode()),
        }],
    }
    return payload


def test_root_class_is_derived_from_closed_gate_a_source_family() -> None:
    assert plan().root_class.value == "official_ats"
    assert plan(SourceFamily.LINKEDIN, "https://www.linkedin.com/jobs/view/1").root_class.value == "aggregator"
    official = plan(SourceFamily.COMPANY_WEBSITE, "https://acme.test/careers")
    assert official.root_class.value == "official_company"
    with pytest.raises(TypeError):
        build_source_plan(selection_key="a"*64, company_label="Acme", vacancy_uri=ROOT, source_family=SourceFamily.GREENHOUSE, root_class="official_company")


@pytest.mark.parametrize("family,root", [
    (SourceFamily.GREENHOUSE, "https://evil.test/acme"),
    (SourceFamily.LEVER, "https://foo.jobs.lever.co/acme"),
    (SourceFamily.SMARTRECRUITERS, "https://careers.smartrecruiters.com/acme"),
    (SourceFamily.TEAMTAILOR, "https://jobs.teamtailor.com/acme"),
    (SourceFamily.RECRUITEE, "https://acme.recruitee.com"),
    (SourceFamily.PERSONIO, "https://acme.jobs.personio.com"),
])
def test_source_family_and_registrable_service_domain_must_correlate(family: SourceFamily, root: str) -> None:
    if root.startswith("https://evil"):
        with pytest.raises(ValidationError): plan(family, root)
    else:
        assert plan(family, root).source_family is family


def test_official_company_root_is_positive_authority_case() -> None:
    official_plan = plan(SourceFamily.COMPANY_WEBSITE, "https://acme.test/careers")
    data = receipt("https://acme.test/about")
    data["root_uri"] = data["requests"][0]["uri"] = "https://acme.test/careers"
    data["explicit_official_links"][0]["source_request_uri"] = "https://acme.test/careers"
    result = admit_official_domain(official_plan, DiscoveryReceipt.model_validate(data))
    assert isinstance(result, AdmittedIdentityOutcome)


def test_governed_ats_subdomain_can_never_be_admitted_as_employer() -> None:
    link = "https://customer.jobs.lever.co/acme"
    data = receipt(link)
    result = admit_official_domain(plan(), DiscoveryReceipt.model_validate(data))
    assert isinstance(result, UnresolvedIdentityOutcome)


def test_closed_extraction_rule_rejects_unlabeled_or_fabricated_link() -> None:
    data = receipt()
    data["explicit_official_links"][0]["extraction_rule"] = "caller_claimed"
    with pytest.raises(ValidationError): DiscoveryReceipt.model_validate(data)
    data = receipt(); data["explicit_official_links"][0]["extraction_fragment"] = '<a href="https://invented.test">x</a>'
    with pytest.raises(ValidationError): DiscoveryReceipt.model_validate(data)
    data = receipt(); data["explicit_official_links"][0]["relation"] = "official_company"
    with pytest.raises(ValidationError): DiscoveryReceipt.model_validate(data)
    assert ExtractionRule.ANCHOR_REL_OFFICIAL.value == "anchor_rel_official"


def test_receipt_and_capture_proof_identity_are_sealed_into_outcome() -> None:
    parsed = DiscoveryReceipt.model_validate(receipt())
    result = admit_official_domain(plan(), parsed)
    assert isinstance(result, AdmittedIdentityOutcome)
    assert result.discovery_receipt == parsed
    assert result.receipt_sha256 == parsed.identity_sha256
    payload = result.model_dump(mode="json")
    for field, value in (("source_request_uri", "https://unrelated.test"), ("evidence_sha256", "c"*64), ("extraction_sha256", "d"*64), ("relation", "official_company")):
        mutation = deepcopy(payload); mutation["authority"][field] = value
        with pytest.raises(ValidationError): TypeAdapter(DiscoveryOutcome).validate_python(mutation)
    mutation = deepcopy(payload); mutation["receipt_sha256"] = "f"*64
    with pytest.raises(ValidationError): TypeAdapter(DiscoveryOutcome).validate_python(mutation)


@pytest.mark.parametrize("query", [
    "?token=secret", "?%74oken=secret", "?x=%65yJhbGciOiJIUzI1NiJ9.aaaa.bbbb",
    "?X-Amz-Credential=x", "?X-Amz-Signature=x", "?X-Goog-Signature=x",
    "?Signature=x", "?Policy=x", "?Key-Pair-Id=x", "?Expires=1", "?x=Bearer%20secret",
    "?x=%ZZ", "?%=x",
])
def test_percent_decoded_and_signed_query_credentials_fail_closed(query: str) -> None:
    with pytest.raises(ValidationError):
        SourcePlan.model_validate({**plan().model_dump(mode="json"), "discovery_roots": [ROOT + query]})


def test_receipt_serialization_contains_no_raw_response_body_or_secret() -> None:
    parsed = DiscoveryReceipt.model_validate(receipt())
    serialized = parsed.model_dump_json()
    assert "captured_response_text" not in serialized
    assert "<html>unrelated raw page body</html>" not in serialized
    assert "fake-secret" not in serialized
    assert parsed.requests[0].content_sha256 == BODY_HASH


@pytest.mark.parametrize("marker", [
    "Bearer fake-secret", "access_token=fake-secret", "hermes-private://candidate",
    "private resume", "candidate profile", "api_key=fake-secret",
])
def test_minimal_extraction_proof_rejects_private_candidate_credential_markers(marker: str) -> None:
    data = receipt()
    fragment = data["explicit_official_links"][0]["extraction_fragment"] + marker
    data["explicit_official_links"][0].update(extraction_fragment=fragment, extraction_sha256=hashlib.sha256(fragment.encode()).hexdigest(), byte_end=len(fragment.encode()))
    with pytest.raises(ValidationError): DiscoveryReceipt.model_validate(data)


def test_round_trip_preserves_sealed_binding_and_unresolved_has_no_task10_hash() -> None:
    admitted = admit_official_domain(plan(), DiscoveryReceipt.model_validate(receipt()))
    assert TypeAdapter(DiscoveryOutcome).validate_json(admitted.model_dump_json()) == admitted
    unresolved = UnresolvedIdentityOutcome(reasons=(MaterializationReason.UNRESOLVED_COMPANY_IDENTITY,))
    dumped = json.loads(unresolved.model_dump_json())
    assert not any("task10" in key or "input_hash" in key for key in dumped)
