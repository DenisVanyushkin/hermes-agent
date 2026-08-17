from __future__ import annotations

from copy import deepcopy
from html import unescape
import hashlib
import json
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import pytest
from pydantic import TypeAdapter, ValidationError

from job_intel.product_search.input_materialization import (
    AdmittedIdentityOutcome,
    DiscoveryOutcome,
    DiscoveryReceipt,
    DiscoveryRootClass,
    MaterializationReason,
    PinnedGateACorpusRow,
    RequestReceipt,
    SourceFamily,
    UnresolvedIdentityOutcome,
    admit_official_domain,
    build_discovery_receipt,
    build_source_plan,
    load_discovery_outcome,
    load_discovery_receipt,
    load_pinned_gate_a_row,
)

GATE_A_RUN_ID = "gate-a-20260816T141344Z"
ROOT = "https://job-boards.greenhouse.io/acme/jobs/1"
LINK = "https://www.acme.test/careers"
ANCHOR = f'<a class="company-link" href="{LINK}">Careers</a>'
BODY = f"<html><body>π<header>unrelated page body</header>{ANCHOR}<footer>other content</footer></body></html>".encode()


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_json(value: object) -> str:
    return _sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode()
    )


def _gate_a_canonical_url(raw: str) -> str:
    split = urlsplit(unescape(raw.strip()))
    hostname = (split.hostname or "").casefold()
    path = split.path.rstrip("/")
    is_linkedin_job = (
        hostname == "linkedin.com" or hostname.endswith(".linkedin.com")
    ) and path.startswith("/jobs/view/")
    is_headhunter_vacancy = (
        hostname == "hh.ru" or hostname.endswith(".hh.ru")
    ) and path.startswith("/vacancy/")
    filtered = (
        []
        if is_linkedin_job or is_headhunter_vacancy
        else [
            (key, value)
            for key, value in parse_qsl(split.query, keep_blank_values=True)
            if not key.casefold().startswith("utm_")
        ]
    )
    return urlunsplit((
        split.scheme.casefold(),
        split.netloc.casefold(),
        path,
        urlencode(filtered),
        "",
    ))


def _write_gate_a_fixture(
    tmp_path: Path,
    *,
    source_family: str = "greenhouse",
    vacancy_uri: str = ROOT,
    company: str = "Acme",
) -> tuple[Path, Path, str, str]:
    gate_a_root = tmp_path / "gate-a"
    raw_root = gate_a_root / "raw-evidence"
    raw_root.mkdir(parents=True)
    raw_payload = {
        "source_family": source_family,
        "source_id": "source-1",
        "query_id": "query-1",
        "company": company,
        "title": "VP Product",
        "url": vacancy_uri,
    }
    raw_bytes = (
        json.dumps(
            raw_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        + "\n"
    ).encode()
    raw_sha256 = _sha256(raw_bytes)
    raw_reference = f"raw-evidence/{raw_sha256}.json"
    (gate_a_root / raw_reference).write_bytes(raw_bytes)
    selection_payload = {
        "run_id": GATE_A_RUN_ID,
        "source_family": source_family,
        "source_id": "source-1",
        "raw_content_sha256": raw_sha256,
    }
    selection_key = _sha256_json(selection_payload)
    canonical_uri = _gate_a_canonical_url(vacancy_uri)
    record = {
        **selection_payload,
        "selection_key": selection_key,
        "query_id": "query-1",
        "raw_reference": raw_reference,
        "canonical_identity_sha256": _sha256(canonical_uri.encode()),
        "company": company,
        "cell_id": "uk",
        "lane": "europe_including_uk",
        "role_pattern": "vp_product",
        "origin": "open_market",
        "sampling_case_type": "core_hypothesis",
        "decision_selection_mode": None,
    }
    corpus = {
        "schema_version": "1.0.0",
        "gate": "gate-b",
        "gate_a": {"run_id": GATE_A_RUN_ID},
        "selection": {"sample_size": 1},
        "records": [record],
    }
    corpus_bytes = (
        json.dumps(corpus, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode()
    corpus_root = tmp_path / "corpus"
    corpus_root.mkdir()
    (corpus_root / "corpus-manifest.json").write_bytes(corpus_bytes)
    return corpus_root, gate_a_root, _sha256(corpus_bytes), selection_key


def _load_row(
    tmp_path: Path,
    *,
    source_family: str = "greenhouse",
    vacancy_uri: str = ROOT,
    company: str = "Acme",
) -> PinnedGateACorpusRow:
    corpus_root, gate_a_root, corpus_sha256, selection_key = _write_gate_a_fixture(
        tmp_path,
        source_family=source_family,
        vacancy_uri=vacancy_uri,
        company=company,
    )
    return load_pinned_gate_a_row(
        corpus_root=corpus_root,
        expected_corpus_sha256=corpus_sha256,
        gate_a_root=gate_a_root,
        selection_key=selection_key,
    )


def _write_capture(artifacts_root: Path, body: bytes = BODY) -> RequestReceipt:
    artifacts_root.mkdir(parents=True, exist_ok=True)
    content_sha256 = _sha256(body)
    (artifacts_root / content_sha256).write_bytes(body)
    return RequestReceipt(
        uri=ROOT,
        status=200,
        content_type="text/html; charset=utf-8",
        content_bytes=len(body),
        content_sha256=content_sha256,
        capture_artifact_sha256=content_sha256,
        redirect_to=None,
    )


def _materialized(
    tmp_path: Path,
    *,
    body: bytes = BODY,
) -> tuple[PinnedGateACorpusRow, Path, DiscoveryReceipt, AdmittedIdentityOutcome]:
    row = _load_row(tmp_path)
    artifacts_root = tmp_path / "captures"
    request = _write_capture(artifacts_root, body)
    receipt = build_discovery_receipt(
        root_uri=ROOT,
        requests=(request,),
        artifacts_root=artifacts_root,
    )
    outcome = admit_official_domain(
        build_source_plan(row),
        receipt,
        pinned_row=row,
        artifacts_root=artifacts_root,
    )
    assert isinstance(outcome, AdmittedIdentityOutcome)
    return row, artifacts_root, receipt, outcome


def _request_payload(uri: str) -> dict[str, object]:
    empty_sha256 = _sha256(b"")
    return {
        "uri": uri,
        "status": 200,
        "content_type": "text/html; charset=utf-8",
        "content_bytes": 0,
        "content_sha256": empty_sha256,
        "capture_artifact_sha256": empty_sha256,
        "redirect_to": None,
    }


def test_source_plan_is_derived_from_a_verified_pinned_gate_a_row(
    tmp_path: Path,
) -> None:
    row = _load_row(tmp_path)
    plan = build_source_plan(row)
    assert plan.pinned_row == row
    assert plan.pinned_row.source_family is SourceFamily.GREENHOUSE
    assert plan.discovery_roots == (ROOT,)
    assert plan.root_class is DiscoveryRootClass.OFFICIAL_ATS
    assert plan.pinned_row.identity_sha256
    assert plan.identity_sha256
    with pytest.raises(TypeError):
        build_source_plan(  # type: ignore[call-arg]
            row,
            source_family=SourceFamily.COMPANY_WEBSITE,
            vacancy_uri="https://invented.test/jobs/1",
        )


def test_acquisition_family_is_separate_from_the_actual_vacancy_host(
    tmp_path: Path,
) -> None:
    row = _load_row(
        tmp_path,
        source_family="duckduckgo",
        vacancy_uri="https://acme.test/jobs/1",
    )
    plan = build_source_plan(row)
    assert plan.pinned_row.source_family is SourceFamily.DUCKDUCKGO
    assert plan.discovery_roots == ("https://acme.test/jobs/1",)
    assert plan.root_class is DiscoveryRootClass.UNVERIFIED_PUBLIC_RESULT


def test_pinned_loader_rejects_manifest_raw_and_selection_identity_drift(
    tmp_path: Path,
) -> None:
    corpus_root, gate_a_root, corpus_sha256, selection_key = _write_gate_a_fixture(
        tmp_path
    )
    manifest_path = corpus_root / "corpus-manifest.json"
    payload = json.loads(manifest_path.read_text())
    payload["records"][0]["source_family"] = "duckduckgo"
    manifest_path.write_text(json.dumps(payload, sort_keys=True) + "\n")
    changed_sha256 = _sha256(manifest_path.read_bytes())
    with pytest.raises(ValueError, match="pinned Gate A row"):
        load_pinned_gate_a_row(
            corpus_root=corpus_root,
            expected_corpus_sha256=changed_sha256,
            gate_a_root=gate_a_root,
            selection_key=selection_key,
        )
    with pytest.raises(ValueError, match="corpus manifest sha256"):
        load_pinned_gate_a_row(
            corpus_root=corpus_root,
            expected_corpus_sha256=corpus_sha256,
            gate_a_root=gate_a_root,
            selection_key=selection_key,
        )


def test_self_consistent_source_mutation_cannot_cross_the_trusted_row_boundary(
    tmp_path: Path,
) -> None:
    trusted_row, artifacts_root, receipt, _ = _materialized(tmp_path)
    rogue_payload = trusted_row.model_dump(mode="json")
    rogue_payload.pop("identity_sha256")
    rogue_payload["source_family"] = "duckduckgo"
    rogue_payload["selection_key"] = _sha256_json({
        "run_id": rogue_payload["run_id"],
        "source_family": "duckduckgo",
        "source_id": rogue_payload["source_id"],
        "raw_content_sha256": rogue_payload["raw_content_sha256"],
    })
    rogue = PinnedGateACorpusRow.model_validate(rogue_payload)
    with pytest.raises(ValueError, match="pinned Gate A row"):
        admit_official_domain(
            build_source_plan(rogue),
            receipt,
            pinned_row=trusted_row,
            artifacts_root=artifacts_root,
        )


@pytest.mark.parametrize(
    "query",
    [
        "?token=secret",
        "?%74oken=secret",
        "?refresh_token=x",
        "?id_token=x",
        "?client_secret=x",
        "?session_id=x",
        "?session-token=x",
        "?token%5B%5D=x",
        "?q%5Brefresh_token%5D=x",
        "?ref%5Bclient_secret%5D=x",
        "?source%5Bsession_id%5D=x",
        "?%20token=x",
        "?passwd=x",
        "?pwd=x",
        "?x=%65yJhbGciOiJIUzI1NiJ9.aaaa.bbbb",
        "?X-Amz-Credential=x",
        "?X-Amz-Signature=x",
        "?X-Goog-Signature=x",
        "?Signature=x",
        "?Policy=x",
        "?Key-Pair-Id=x",
        "?Expires=1",
        "?x=Bearer%20secret",
        "?redirect=https%3A%2F%2Fexample.test%2F%3Fclient_secret%3Dx",
        "?q=session%3Dx",
        "?q=authorization%3Dx",
        "?q=X-Amz-Credential%3Dx",
        "?q=Key-Pair-Id%3Dx",
        "?q=private%20resume",
        "?q=candidate%20profile",
        "?q=candidate%20facts",
        "?q=hermes-private%3A%2F%2Fcandidate",
        "?q=person%40example.test",
        "?unknown_public_parameter=x",
        "?x=%ZZ",
        "?%=x",
    ],
)
def test_closed_percent_decoded_query_policy_rejects_credentials_and_unknowns(
    query: str,
) -> None:
    with pytest.raises(ValidationError):
        RequestReceipt.model_validate(_request_payload(ROOT + query))


def test_closed_query_policy_keeps_only_benign_public_parameters() -> None:
    parsed = RequestReceipt.model_validate(
        _request_payload(ROOT + "?jid=123&lang=en&utm_source=public")
    )
    assert parsed.uri.endswith("?jid=123&lang=en&utm_source=public")


def test_closed_parser_derives_relation_from_truthful_real_html(tmp_path: Path) -> None:
    row, artifacts_root, receipt, outcome = _materialized(tmp_path)
    assert receipt.explicit_official_links[0].relation.value == "official_careers"
    assert receipt.explicit_official_links[0].extraction_rule.value == (
        "html_anchor_text_v1"
    )
    assert receipt.explicit_official_links[0].extraction_fragment == ANCHOR
    assert outcome.authority.domain == "www.acme.test"
    assert (
        load_discovery_outcome(
            outcome.model_dump_json(),
            pinned_row=row,
            artifacts_root=artifacts_root,
        )
        == outcome
    )


def test_synthetic_relation_attributes_are_not_authority(tmp_path: Path) -> None:
    body = (
        f'<a href="{LINK}" rel="official" '
        'data-relation="official_careers">Unrelated</a>'
    ).encode()
    row = _load_row(tmp_path)
    artifacts_root = tmp_path / "captures"
    receipt = build_discovery_receipt(
        root_uri=ROOT,
        requests=(_write_capture(artifacts_root, body),),
        artifacts_root=artifacts_root,
    )
    assert receipt.explicit_official_links == ()
    result = admit_official_domain(
        build_source_plan(row),
        receipt,
        pinned_row=row,
        artifacts_root=artifacts_root,
    )
    assert result == UnresolvedIdentityOutcome(
        reasons=(MaterializationReason.UNRESOLVED_COMPANY_IDENTITY,)
    )


def test_receipt_load_rehashes_actual_capture_bytes(tmp_path: Path) -> None:
    _, artifacts_root, receipt, _ = _materialized(tmp_path)
    artifact_path = artifacts_root / receipt.requests[0].capture_artifact_sha256
    artifact_path.write_bytes(BODY.replace(b"Careers", b"Careerx"))
    with pytest.raises(ValueError, match="capture artifact sha256"):
        load_discovery_receipt(
            receipt.model_dump_json(),
            artifacts_root=artifacts_root,
        )


def test_receipt_load_revalidates_exact_artifact_byte_range(tmp_path: Path) -> None:
    _, artifacts_root, receipt, _ = _materialized(tmp_path)
    payload = receipt.model_dump(mode="json")
    payload.pop("identity_sha256")
    payload["explicit_official_links"][0]["byte_start"] += 1
    payload["explicit_official_links"][0]["byte_end"] += 1
    mutated = DiscoveryReceipt.model_validate(payload)
    with pytest.raises(ValueError, match="parsed artifact extraction proof"):
        load_discovery_receipt(
            mutated.model_dump_json(),
            artifacts_root=artifacts_root,
        )


def test_content_addressed_capture_reader_rejects_symlinks(tmp_path: Path) -> None:
    artifacts_root = tmp_path / "captures"
    artifacts_root.mkdir()
    target = tmp_path / "target"
    target.write_bytes(BODY)
    content_sha256 = _sha256(BODY)
    (artifacts_root / content_sha256).symlink_to(target)
    request_payload = _request_payload(ROOT)
    request_payload.update(
        content_bytes=len(BODY),
        content_sha256=content_sha256,
        capture_artifact_sha256=content_sha256,
    )
    request = RequestReceipt.model_validate(request_payload)
    with pytest.raises(ValueError, match="capture artifact"):
        build_discovery_receipt(
            root_uri=ROOT,
            requests=(request,),
            artifacts_root=artifacts_root,
        )


def test_receipt_persists_only_the_minimal_exact_element_not_neighboring_body(
    tmp_path: Path,
) -> None:
    _, _, receipt, _ = _materialized(tmp_path)
    link = receipt.explicit_official_links[0]
    assert BODY[link.byte_start : link.byte_end] == ANCHOR.encode()
    serialized = receipt.model_dump_json()
    assert "unrelated page body" not in serialized
    assert "other content" not in serialized
    assert "captured_response_text" not in serialized


@pytest.mark.parametrize(
    "marker",
    [
        "Authorization: Bearer short",
        "refresh_token=short",
        "client%5Fsecret=short",
        "session_id=short",
        "pwd=x",
        "private resume",
        "candidate profile",
        "candidate facts",
        "hermes-private://candidate",
        "person@example.test",
    ],
)
def test_exact_extraction_element_rejects_credentials_private_markers_and_pii(
    tmp_path: Path,
    marker: str,
) -> None:
    body = f'<a data-note="{marker}" href="{LINK}">Careers</a>'.encode()
    artifacts_root = tmp_path / "captures"
    request = _write_capture(artifacts_root, body)
    with pytest.raises(ValueError, match="prohibited"):
        build_discovery_receipt(
            root_uri=ROOT,
            requests=(request,),
            artifacts_root=artifacts_root,
        )


def test_defaulted_canonical_model_identities_survive_all_construction_forms(
    tmp_path: Path,
) -> None:
    _, artifacts_root, receipt, _ = _materialized(tmp_path)
    payload = {
        "root_uri": receipt.root_uri,
        "requests": receipt.requests,
        "explicit_official_links": receipt.explicit_official_links,
    }
    from_nested_models = DiscoveryReceipt.model_validate(payload)
    from_json = load_discovery_receipt(
        from_nested_models.model_dump_json(),
        artifacts_root=artifacts_root,
    )
    assert from_nested_models.identity_sha256 == receipt.identity_sha256
    assert from_json == from_nested_models
    assert (
        DiscoveryReceipt.model_validate_json(from_json.model_dump_json()) == from_json
    )


def test_admitted_outcome_seals_receipt_and_rejects_field_mutations(
    tmp_path: Path,
) -> None:
    row, artifacts_root, _, outcome = _materialized(tmp_path)
    payload = outcome.model_dump(mode="json")
    for field, value in (
        ("source_request_uri", "https://unrelated.test/"),
        ("evidence_sha256", "c" * 64),
        ("extraction_sha256", "d" * 64),
        ("relation", "official_company"),
    ):
        mutation = deepcopy(payload)
        mutation["authority"][field] = value
        with pytest.raises(ValidationError):
            TypeAdapter(DiscoveryOutcome).validate_python(mutation)
    mutation = deepcopy(payload)
    mutation["receipt_sha256"] = "f" * 64
    with pytest.raises(ValidationError):
        TypeAdapter(DiscoveryOutcome).validate_python(mutation)
    mutation = deepcopy(payload)
    mutation["authority"]["canonical_uri"] = "https://www.acme.test/other"
    mutation["authority"]["evidence_display_uri"] = "https://www.acme.test/other"
    with pytest.raises(ValidationError):
        TypeAdapter(DiscoveryOutcome).validate_python(mutation)
    assert (
        load_discovery_outcome(
            json.dumps(payload),
            pinned_row=row,
            artifacts_root=artifacts_root,
        )
        == outcome
    )


def test_distinct_modern_idna_authorities_remain_ambiguous(tmp_path: Path) -> None:
    first = '<a href="https://faß.de/jobs">Careers</a>'
    second = '<a href="https://fass.de/jobs">Careers</a>'
    body = (first + second).encode()
    row = _load_row(tmp_path)
    artifacts_root = tmp_path / "captures"
    receipt = build_discovery_receipt(
        root_uri=ROOT,
        requests=(_write_capture(artifacts_root, body),),
        artifacts_root=artifacts_root,
    )
    result = admit_official_domain(
        build_source_plan(row),
        receipt,
        pinned_row=row,
        artifacts_root=artifacts_root,
    )
    assert result == UnresolvedIdentityOutcome(
        reasons=(MaterializationReason.AMBIGUOUS_COMPANY_IDENTITY,)
    )


@pytest.mark.parametrize(
    "uri",
    [
        "https://localhost/jobs",
        "https://127.0.0.1/jobs",
        "https://[::1]/jobs",
        "https://bad..test/jobs",
        "https://xn--/jobs",
        "https://acme.test.:444/jobs",
        "https://user:pass@acme.test/jobs",
        "https://acme.test/jobs#",
    ],
)
def test_strict_origin_and_idna_contract_remains_fail_closed(uri: str) -> None:
    with pytest.raises(ValidationError):
        RequestReceipt.model_validate(_request_payload(uri))


def test_versioned_unresolved_contract_remains_closed_and_task10_free() -> None:
    unresolved = UnresolvedIdentityOutcome(
        reasons=(MaterializationReason.UNRESOLVED_COMPANY_IDENTITY,)
    )
    dumped = unresolved.model_dump(mode="json")
    assert dumped["schema_version"] == "1.0.0"
    assert not any("task10" in key or "input_hash" in key for key in dumped)
    with pytest.raises(ValidationError):
        UnresolvedIdentityOutcome.model_validate({**dumped, "schema_version": "2.0.0"})
    with pytest.raises(ValidationError):
        UnresolvedIdentityOutcome.model_validate({
            **dumped,
            "reasons": [
                "unresolved_company_identity",
                "unresolved_company_identity",
            ],
        })
    with pytest.raises(ValidationError):
        UnresolvedIdentityOutcome.model_validate({
            **dumped,
            "task10_input_hash": "a" * 64,
        })
    with pytest.raises(ValidationError):
        unresolved.reasons = (MaterializationReason.SOURCE_FETCH_FAILED,)
