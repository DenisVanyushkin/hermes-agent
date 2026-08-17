from __future__ import annotations

from copy import deepcopy
import hashlib

import pytest
from pydantic import TypeAdapter, ValidationError

from job_intel.product_search.input_materialization import (
    AdmittedIdentityOutcome,
    DiscoveryOutcome,
    DiscoveryReceipt,
    DiscoveryRootClass,
    MaterializationReason,
    OfficialLinkRelation,
    SourcePlan,
    UnresolvedIdentityOutcome,
    admit_official_domain,
    build_source_plan,
)

ROOT = "https://boards.example.test/acme/1"
BODY = '<a href="https://www.acme.test/careers">Careers</a>'
HASH = hashlib.sha256(BODY.encode()).hexdigest()


def plan() -> SourcePlan:
    return build_source_plan(selection_key="a" * 64, company_label="Acme", vacancy_uri=ROOT, root_class=DiscoveryRootClass.OFFICIAL_ATS)


def receipt(link: str = "https://www.acme.test/careers") -> dict:
    body = f'<a href="{link}">Careers</a>'
    digest = hashlib.sha256(body.encode()).hexdigest()
    return {"schema_version": "1.0.0", "root_uri": ROOT, "requests": [{"uri": ROOT, "status": 200, "content_type": "text/html", "content_bytes": len(body.encode()), "content_sha256": digest, "captured_response_text": body, "redirect_to": None}], "explicit_official_links": [{"uri": link, "relation": "official_careers", "source_request_uri": ROOT, "evidence_sha256": digest, "extraction_fragment": body, "extraction_sha256": digest}]}


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
        {"uri": ROOT, "status": 302, "content_type": "text/html", "content_bytes": 0, "content_sha256": hashlib.sha256(b"").hexdigest(), "captured_response_text": "", "redirect_to": "https://boards.example.test/2"},
        {"uri": "https://boards.example.test/2", "status": 301, "content_type": "text/html", "content_bytes": 0, "content_sha256": hashlib.sha256(b"").hexdigest(), "captured_response_text": "", "redirect_to": "https://boards.example.test/3"},
        {"uri": "https://boards.example.test/3", "status": 200, "content_type": "text/html", "content_bytes": len(BODY.encode()), "content_sha256": HASH, "captured_response_text": BODY, "redirect_to": None},
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
    conflict = receipt()
    conflict_body = BODY + '<a href="https://other.test">Other</a>'
    conflict_hash = hashlib.sha256(conflict_body.encode()).hexdigest()
    conflict["requests"][0].update(captured_response_text=conflict_body, content_bytes=len(conflict_body.encode()), content_sha256=conflict_hash)
    conflict["explicit_official_links"][0].update(extraction_fragment=conflict_body, extraction_sha256=conflict_hash, evidence_sha256=conflict_hash)
    conflict["explicit_official_links"].append({"uri": "https://other.test", "relation": "official_company", "source_request_uri": ROOT, "evidence_sha256": conflict_hash, "extraction_fragment": conflict_body, "extraction_sha256": conflict_hash})
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


def test_link_must_occur_in_captured_response_bytes() -> None:
    data = receipt()
    data["explicit_official_links"][0]["uri"] = "https://invented.test"
    data["explicit_official_links"][0]["extraction_fragment"] = BODY
    with pytest.raises(ValidationError):
        DiscoveryReceipt.model_validate(data)


def test_known_ats_or_aggregator_candidate_never_becomes_employer() -> None:
    body = '<a href="https://jobs.lever.co/acme">Careers</a>'
    data = receipt("https://jobs.lever.co/acme")
    digest = hashlib.sha256(body.encode()).hexdigest()
    data["requests"][0].update(captured_response_text=body, content_bytes=len(body), content_sha256=digest)
    data["explicit_official_links"][0].update(extraction_fragment=body, extraction_sha256=digest, evidence_sha256=digest)
    assert isinstance(admit_official_domain(plan(), DiscoveryReceipt.model_validate(data)), UnresolvedIdentityOutcome)


@pytest.mark.parametrize("suffix", ["#", "?access_token=secret", "?api_key=secret", "?x=eyJhbGciOiJIUzI1NiJ9.aaaa.bbbb"])
def test_empty_fragment_and_sensitive_queries_rejected_everywhere(suffix: str) -> None:
    with pytest.raises(ValidationError):
        SourcePlan.model_validate({**plan().model_dump(mode="json"), "discovery_roots": [ROOT + suffix]})
    data = receipt()
    data["explicit_official_links"][0]["uri"] = "https://acme.test/careers" + suffix
    with pytest.raises(ValidationError):
        DiscoveryReceipt.model_validate(data)


@pytest.mark.parametrize("field", ["root", "request", "redirect", "source_request"])
def test_sensitive_query_rejected_in_every_receipt_uri(field: str) -> None:
    unsafe = "https://boards.example.test/acme?signature=secret"
    data = receipt()
    if field == "root":
        data["root_uri"] = unsafe
    elif field == "request":
        data["requests"][0]["uri"] = unsafe
    elif field == "redirect":
        data["requests"][0].update(status=302, redirect_to=unsafe)
    else:
        data["explicit_official_links"][0]["source_request_uri"] = unsafe
    with pytest.raises(ValidationError):
        DiscoveryReceipt.model_validate(data)


def test_direct_admitted_deserialization_revalidates_all_authority_binding() -> None:
    admitted = admit_official_domain(plan(), DiscoveryReceipt.model_validate(receipt()))
    payload = admitted.model_dump(mode="json")
    for mutation in (
        {**deepcopy(payload), "schema_version": "2.0.0"},
        {**deepcopy(payload), "authority": {**payload["authority"], "domain": "wrong.test"}},
        {**deepcopy(payload), "authority": {**payload["authority"], "canonical_uri": "http://acme.test"}},
        {**deepcopy(payload), "authority": {**payload["authority"], "selection_key": "c" * 64}},
        {**deepcopy(payload), "authority": {**payload["authority"], "evidence_display_uri": "https://acme.test?token=secret"}},
    ):
        with pytest.raises(ValidationError):
            TypeAdapter(DiscoveryOutcome).validate_python(mutation)


def test_idna2008_keeps_sharp_s_distinct_and_rejects_bad_alabel_and_extra_dot() -> None:
    sharp = admit_official_domain(plan(), DiscoveryReceipt.model_validate(receipt("https://faß.de/careers")))
    plain_body = BODY.replace("www.acme.test", "fass.de")
    plain_data = receipt("https://fass.de/careers")
    digest = hashlib.sha256(plain_body.encode()).hexdigest()
    plain_data["requests"][0].update(captured_response_text=plain_body, content_bytes=len(plain_body.encode()), content_sha256=digest)
    plain_data["explicit_official_links"][0].update(extraction_fragment=plain_body, extraction_sha256=digest, evidence_sha256=digest)
    plain = admit_official_domain(plan(), DiscoveryReceipt.model_validate(plain_data))
    assert sharp.authority.domain != plain.authority.domain
    for uri in ("https://xn--invalid-.de", "https://acme.test../careers"):
        with pytest.raises(ValidationError):
            DiscoveryReceipt.model_validate(receipt(uri))


def test_outcome_versions_and_unique_unresolved_reasons() -> None:
    unresolved = UnresolvedIdentityOutcome(reasons=(MaterializationReason.UNRESOLVED_COMPANY_IDENTITY,))
    assert unresolved.schema_version == "1.0.0"
    with pytest.raises(ValidationError):
        UnresolvedIdentityOutcome.model_validate({**unresolved.model_dump(mode="json"), "schema_version": "2"})
    with pytest.raises(ValidationError):
        UnresolvedIdentityOutcome(reasons=(MaterializationReason.SOURCE_FETCH_FAILED, MaterializationReason.SOURCE_FETCH_FAILED))
