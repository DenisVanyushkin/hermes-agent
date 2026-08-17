from __future__ import annotations

from copy import deepcopy
from html import unescape
import hashlib
import json
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import pytest
from pydantic import TypeAdapter, ValidationError

from job_intel.product_search import input_materialization
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
GATE_A_COMMIT = "65d60daae16093a9a7e34a11a159e2f789dd14dd"
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
) -> tuple[Path, Path, str, str, str]:
    gate_a_root = tmp_path / "gate-a"
    raw_root = gate_a_root / "raw-evidence"
    raw_root.mkdir(parents=True)
    gate_a_manifest_bytes = (
        f"commit: {GATE_A_COMMIT}\n"
        "gate: gate-a\n"
        f"root: {gate_a_root}\n"
        "schema_version: 1.0.0\n"
    ).encode()
    (gate_a_root / "manifest.yaml").write_bytes(gate_a_manifest_bytes)
    gate_a_manifest_sha256 = _sha256(gate_a_manifest_bytes)
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
        "gate_a": {
            "commit": GATE_A_COMMIT,
            "manifest_sha256": gate_a_manifest_sha256,
            "run_id": GATE_A_RUN_ID,
        },
        "selection": {"sample_size": 1},
        "records": [record],
    }
    corpus_bytes = (
        json.dumps(corpus, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode()
    corpus_root = tmp_path / "corpus"
    corpus_root.mkdir()
    (corpus_root / "corpus-manifest.json").write_bytes(corpus_bytes)
    return (
        corpus_root,
        gate_a_root,
        _sha256(corpus_bytes),
        gate_a_manifest_sha256,
        selection_key,
    )


def _pin_fixture(
    monkeypatch: pytest.MonkeyPatch,
    *,
    corpus_root: Path,
    gate_a_root: Path,
    corpus_sha256: str,
    gate_a_manifest_sha256: str,
    record_count: int = 1,
) -> None:
    monkeypatch.setattr(
        input_materialization,
        "CANONICAL_GATE_A_ROOT",
        gate_a_root,
        raising=False,
    )
    monkeypatch.setattr(
        input_materialization,
        "CANONICAL_GATE_B_CORPUS_ROOT",
        corpus_root,
        raising=False,
    )
    monkeypatch.setattr(
        input_materialization,
        "PINNED_GATE_A_COMMIT",
        GATE_A_COMMIT,
        raising=False,
    )
    monkeypatch.setattr(
        input_materialization,
        "PINNED_GATE_A_MANIFEST_SHA256",
        gate_a_manifest_sha256,
        raising=False,
    )
    monkeypatch.setattr(
        input_materialization,
        "PINNED_GATE_B_CORPUS_SHA256",
        corpus_sha256,
        raising=False,
    )
    monkeypatch.setattr(
        input_materialization,
        "PINNED_GATE_B_CORPUS_RECORD_COUNT",
        record_count,
        raising=False,
    )


def _load_row(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    source_family: str = "greenhouse",
    vacancy_uri: str = ROOT,
    company: str = "Acme",
) -> PinnedGateACorpusRow:
    (
        corpus_root,
        gate_a_root,
        corpus_sha256,
        gate_a_manifest_sha256,
        selection_key,
    ) = _write_gate_a_fixture(
        tmp_path,
        source_family=source_family,
        vacancy_uri=vacancy_uri,
        company=company,
    )
    _pin_fixture(
        monkeypatch,
        corpus_root=corpus_root,
        gate_a_root=gate_a_root,
        corpus_sha256=corpus_sha256,
        gate_a_manifest_sha256=gate_a_manifest_sha256,
    )
    return load_pinned_gate_a_row(selection_key=selection_key)


def _write_capture(
    artifacts_root: Path,
    body: bytes = BODY,
    *,
    uri: str = ROOT,
) -> RequestReceipt:
    artifacts_root.mkdir(parents=True, exist_ok=True)
    content_sha256 = _sha256(body)
    (artifacts_root / content_sha256).write_bytes(body)
    return RequestReceipt(
        uri=uri,
        status=200,
        content_type="text/html; charset=utf-8",
        content_bytes=len(body),
        content_sha256=content_sha256,
        capture_artifact_sha256=content_sha256,
        redirect_to=None,
    )


def _materialized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    body: bytes = BODY,
) -> tuple[PinnedGateACorpusRow, Path, DiscoveryReceipt, AdmittedIdentityOutcome]:
    row = _load_row(tmp_path, monkeypatch)
    artifacts_root = tmp_path / "captures"
    request = _write_capture(artifacts_root, body)
    receipt = build_discovery_receipt(
        root_uri=ROOT,
        requests=(request,),
        artifacts_root=artifacts_root,
    )
    outcome = admit_official_domain(
        receipt,
        selection_key=row.selection_key,
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


def _reseal_model_payload(payload: dict[str, object]) -> dict[str, object]:
    payload.pop("identity_sha256", None)
    payload["identity_sha256"] = _sha256_json(payload)
    return payload


def _rewrite_corpus(
    corpus_root: Path,
    payload: dict[str, object],
) -> str:
    manifest_bytes = (
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode()
    (corpus_root / "corpus-manifest.json").write_bytes(manifest_bytes)
    return _sha256(manifest_bytes)


def _append_second_row(
    *,
    corpus_root: Path,
    gate_a_root: Path,
) -> tuple[str, str]:
    raw_payload = {
        "source_family": "ashby",
        "source_id": "source-2",
        "query_id": "query-2",
        "company": "Beta",
        "title": "Head of Product",
        "url": "https://jobs.ashbyhq.com/beta/2",
    }
    raw_bytes = (
        json.dumps(
            raw_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode()
    raw_sha256 = _sha256(raw_bytes)
    raw_reference = f"raw-evidence/{raw_sha256}.json"
    (gate_a_root / raw_reference).write_bytes(raw_bytes)
    selection_payload = {
        "run_id": GATE_A_RUN_ID,
        "source_family": "ashby",
        "source_id": "source-2",
        "raw_content_sha256": raw_sha256,
    }
    selection_key = _sha256_json(selection_payload)
    canonical_uri = _gate_a_canonical_url(raw_payload["url"])
    manifest_path = corpus_root / "corpus-manifest.json"
    corpus = json.loads(manifest_path.read_text())
    corpus["selection"]["sample_size"] = 2
    corpus["records"].append({
        **selection_payload,
        "selection_key": selection_key,
        "query_id": "query-2",
        "raw_reference": raw_reference,
        "canonical_identity_sha256": _sha256(canonical_uri.encode()),
        "company": "Beta",
        "cell_id": "uk",
        "lane": "europe_including_uk",
        "role_pattern": "head_product",
        "origin": "open_market",
        "sampling_case_type": "exploration_hypothesis",
        "decision_selection_mode": None,
    })
    corpus["records"].sort(key=lambda item: item["selection_key"])
    return _rewrite_corpus(corpus_root, corpus), selection_key


def test_source_plan_is_derived_from_a_verified_pinned_gate_a_row(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = _load_row(tmp_path, monkeypatch)
    plan = build_source_plan(selection_key=row.selection_key)
    assert plan.pinned_row == row
    assert plan.pinned_row.source_family is SourceFamily.GREENHOUSE
    assert plan.discovery_roots == (ROOT,)
    assert plan.root_class is DiscoveryRootClass.OFFICIAL_ATS
    assert plan.authority_policy_version == "1.0.0"
    assert plan.pinned_row.identity_sha256
    assert plan.identity_sha256
    with pytest.raises(TypeError):
        build_source_plan(  # type: ignore[call-arg]
            selection_key=row.selection_key,
            source_family=SourceFamily.COMPANY_WEBSITE,
            vacancy_uri="https://invented.test/jobs/1",
        )


def test_acquisition_family_is_separate_from_the_actual_vacancy_host(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = _load_row(
        tmp_path,
        monkeypatch,
        source_family="duckduckgo",
        vacancy_uri="https://acme.test/jobs/1",
    )
    plan = build_source_plan(selection_key=row.selection_key)
    assert plan.pinned_row.source_family is SourceFamily.DUCKDUCKGO
    assert plan.discovery_roots == ("https://acme.test/jobs/1",)
    assert plan.root_class is DiscoveryRootClass.UNVERIFIED_PUBLIC_RESULT


def test_pinned_loader_rejects_manifest_raw_and_selection_identity_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        corpus_root,
        gate_a_root,
        corpus_sha256,
        gate_a_manifest_sha256,
        selection_key,
    ) = _write_gate_a_fixture(tmp_path)
    _pin_fixture(
        monkeypatch,
        corpus_root=corpus_root,
        gate_a_root=gate_a_root,
        corpus_sha256=corpus_sha256,
        gate_a_manifest_sha256=gate_a_manifest_sha256,
    )
    manifest_path = corpus_root / "corpus-manifest.json"
    payload = json.loads(manifest_path.read_text())
    payload["records"][0]["source_family"] = "duckduckgo"
    manifest_path.write_text(json.dumps(payload, sort_keys=True) + "\n")
    changed_sha256 = _sha256(manifest_path.read_bytes())
    with pytest.raises(ValueError, match="corpus manifest sha256"):
        load_pinned_gate_a_row(selection_key=selection_key)
    monkeypatch.setattr(
        input_materialization,
        "PINNED_GATE_B_CORPUS_SHA256",
        changed_sha256,
    )
    with pytest.raises(ValueError, match="pinned Gate A row"):
        load_pinned_gate_a_row(selection_key=selection_key)


def test_self_consistent_source_mutation_cannot_cross_the_trusted_row_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trusted_row, artifacts_root, receipt, _ = _materialized(tmp_path, monkeypatch)
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
            receipt,
            selection_key=rogue.selection_key,
            artifacts_root=artifacts_root,
        )


def test_authority_apis_do_not_accept_caller_selected_roots_hashes_rows_or_plans(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row, artifacts_root, receipt, _ = _materialized(tmp_path, monkeypatch)
    with pytest.raises(TypeError):
        load_pinned_gate_a_row(  # type: ignore[call-arg]
            selection_key=row.selection_key,
            corpus_root=tmp_path / "alternate-corpus",
            expected_corpus_sha256="f" * 64,
            gate_a_root=tmp_path / "alternate-gate-a",
        )
    with pytest.raises(TypeError):
        admit_official_domain(  # type: ignore[call-arg]
            build_source_plan(selection_key=row.selection_key),
            receipt,
            selection_key=row.selection_key,
            artifacts_root=artifacts_root,
        )
    with pytest.raises(TypeError):
        admit_official_domain(  # type: ignore[call-arg]
            receipt,
            selection_key=row.selection_key,
            pinned_row=row,
            artifacts_root=artifacts_root,
        )


def test_alternate_self_sealed_corpus_and_raw_package_cannot_authorize(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    canonical = tmp_path / "canonical"
    canonical.mkdir()
    trusted_row = _load_row(canonical, monkeypatch)
    alternate = tmp_path / "alternate"
    alternate.mkdir()
    (
        alternate_corpus_root,
        alternate_gate_a_root,
        alternate_corpus_sha256,
        _,
        alternate_selection_key,
    ) = _write_gate_a_fixture(
        alternate,
        source_family="duckduckgo",
        company="Invented Employer",
    )
    assert alternate_corpus_root != input_materialization.CANONICAL_GATE_B_CORPUS_ROOT
    assert alternate_gate_a_root != input_materialization.CANONICAL_GATE_A_ROOT
    assert alternate_corpus_sha256 != input_materialization.PINNED_GATE_B_CORPUS_SHA256
    assert alternate_selection_key != trusted_row.selection_key
    with pytest.raises(ValueError, match="selection"):
        load_pinned_gate_a_row(selection_key=alternate_selection_key)


def test_serialized_self_sealed_row_and_outcome_are_dtos_not_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trusted_row, artifacts_root, receipt, trusted_outcome = _materialized(
        tmp_path,
        monkeypatch,
    )
    rogue_row_payload = trusted_row.model_dump(mode="json")
    rogue_row_payload.pop("identity_sha256")
    rogue_row_payload["company_label"] = "Invented Employer"
    rogue_row = PinnedGateACorpusRow.model_validate(rogue_row_payload)
    forged_payload = trusted_outcome.model_dump(mode="json")
    forged_plan = forged_payload["source_plan"]
    assert isinstance(forged_plan, dict)
    forged_plan["pinned_row"] = rogue_row.model_dump(mode="json")
    _reseal_model_payload(forged_plan)
    forged_authority = forged_payload["authority"]
    assert isinstance(forged_authority, dict)
    forged_authority["pinned_row_sha256"] = rogue_row.identity_sha256
    forged_authority["source_plan_sha256"] = forged_plan["identity_sha256"]
    forged_authority["company_label"] = "Invented Employer"
    forged = TypeAdapter(DiscoveryOutcome).validate_python(forged_payload)
    assert isinstance(forged, AdmittedIdentityOutcome)
    with pytest.raises(ValueError, match="canonical Gate A authority"):
        load_discovery_outcome(
            forged.model_dump_json(),
            selection_key=trusted_row.selection_key,
            artifacts_root=artifacts_root,
        )
    assert receipt == trusted_outcome.discovery_receipt


def test_canonical_row_rejects_vacancy_identity_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = _load_row(tmp_path, monkeypatch)
    payload = row.model_dump(mode="json")
    payload.pop("identity_sha256")
    payload["canonical_identity_sha256"] = "e" * 64
    with pytest.raises(ValidationError, match="canonical vacancy identity"):
        PinnedGateACorpusRow.model_validate(payload)


def test_rehashed_reordered_corpus_is_not_canonical_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        corpus_root,
        gate_a_root,
        _,
        gate_a_manifest_sha256,
        first_selection_key,
    ) = _write_gate_a_fixture(tmp_path)
    canonical_corpus_sha256, _ = _append_second_row(
        corpus_root=corpus_root,
        gate_a_root=gate_a_root,
    )
    _pin_fixture(
        monkeypatch,
        corpus_root=corpus_root,
        gate_a_root=gate_a_root,
        corpus_sha256=canonical_corpus_sha256,
        gate_a_manifest_sha256=gate_a_manifest_sha256,
        record_count=2,
    )
    assert load_pinned_gate_a_row(selection_key=first_selection_key)
    payload = json.loads((corpus_root / "corpus-manifest.json").read_text())
    payload["records"].reverse()
    reordered_sha256 = _rewrite_corpus(corpus_root, payload)
    monkeypatch.setattr(
        input_materialization,
        "PINNED_GATE_B_CORPUS_SHA256",
        reordered_sha256,
    )
    with pytest.raises(ValueError, match="canonical order"):
        load_pinned_gate_a_row(selection_key=first_selection_key)


def test_rehashed_mixed_run_corpus_still_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        corpus_root,
        gate_a_root,
        _,
        gate_a_manifest_sha256,
        selection_key,
    ) = _write_gate_a_fixture(tmp_path)
    payload = json.loads((corpus_root / "corpus-manifest.json").read_text())
    payload["records"][0]["run_id"] = "gate-a-other-run"
    mixed_sha256 = _rewrite_corpus(corpus_root, payload)
    _pin_fixture(
        monkeypatch,
        corpus_root=corpus_root,
        gate_a_root=gate_a_root,
        corpus_sha256=mixed_sha256,
        gate_a_manifest_sha256=gate_a_manifest_sha256,
    )
    with pytest.raises(ValueError, match="mixed Gate A run"):
        load_pinned_gate_a_row(selection_key=selection_key)


def test_rehashed_gate_a_manifest_with_alternate_commit_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        corpus_root,
        gate_a_root,
        _,
        _,
        selection_key,
    ) = _write_gate_a_fixture(tmp_path)
    manifest_path = gate_a_root / "manifest.yaml"
    manifest_path.write_text(manifest_path.read_text().replace(GATE_A_COMMIT, "f" * 40))
    changed_manifest_sha256 = _sha256(manifest_path.read_bytes())
    corpus = json.loads((corpus_root / "corpus-manifest.json").read_text())
    corpus["gate_a"]["commit"] = "f" * 40
    corpus["gate_a"]["manifest_sha256"] = changed_manifest_sha256
    changed_corpus_sha256 = _rewrite_corpus(corpus_root, corpus)
    _pin_fixture(
        monkeypatch,
        corpus_root=corpus_root,
        gate_a_root=gate_a_root,
        corpus_sha256=changed_corpus_sha256,
        gate_a_manifest_sha256=changed_manifest_sha256,
    )
    monkeypatch.setattr(input_materialization, "PINNED_GATE_A_COMMIT", GATE_A_COMMIT)
    with pytest.raises(ValueError, match="Gate A commit"):
        load_pinned_gate_a_row(selection_key=selection_key)


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


def test_closed_parser_derives_relation_from_truthful_real_html(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row, artifacts_root, receipt, outcome = _materialized(tmp_path, monkeypatch)
    assert receipt.explicit_official_links[0].relation.value == "official_careers"
    assert receipt.explicit_official_links[0].extraction_rule.value == (
        "html_anchor_text_v1"
    )
    assert receipt.explicit_official_links[0].extraction_fragment == ANCHOR
    assert outcome.authority.domain == "www.acme.test"
    assert (
        load_discovery_outcome(
            outcome.model_dump_json(),
            selection_key=row.selection_key,
            artifacts_root=artifacts_root,
        )
        == outcome
    )


def test_synthetic_relation_attributes_are_not_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = (
        f'<a href="{LINK}" rel="official" '
        'data-relation="official_careers">Unrelated</a>'
    ).encode()
    row = _load_row(tmp_path, monkeypatch)
    artifacts_root = tmp_path / "captures"
    receipt = build_discovery_receipt(
        root_uri=ROOT,
        requests=(_write_capture(artifacts_root, body),),
        artifacts_root=artifacts_root,
    )
    assert receipt.explicit_official_links == ()
    result = admit_official_domain(
        receipt,
        selection_key=row.selection_key,
        artifacts_root=artifacts_root,
    )
    assert result == UnresolvedIdentityOutcome(
        reasons=(MaterializationReason.UNRESOLVED_COMPANY_IDENTITY,)
    )


@pytest.mark.parametrize(
    ("source_family", "expected_root_class"),
    [
        ("greenhouse", DiscoveryRootClass.OFFICIAL_ATS),
        ("lever", DiscoveryRootClass.OFFICIAL_ATS),
        ("ashby", DiscoveryRootClass.OFFICIAL_ATS),
        ("smartrecruiters", DiscoveryRootClass.OFFICIAL_ATS),
        ("teamtailor", DiscoveryRootClass.OFFICIAL_ATS),
        ("recruitee", DiscoveryRootClass.OFFICIAL_ATS),
        ("personio", DiscoveryRootClass.OFFICIAL_ATS),
        ("linkedin", DiscoveryRootClass.AGGREGATOR),
        ("headhunter", DiscoveryRootClass.AGGREGATOR),
        ("remoteok", DiscoveryRootClass.AGGREGATOR),
        ("remotive", DiscoveryRootClass.AGGREGATOR),
        ("duckduckgo", DiscoveryRootClass.UNVERIFIED_PUBLIC_RESULT),
        ("company_website", DiscoveryRootClass.OFFICIAL_COMPANY),
    ],
)
def test_source_family_authority_policy_is_total_and_has_no_host_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source_family: str,
    expected_root_class: DiscoveryRootClass,
) -> None:
    row = _load_row(
        tmp_path,
        monkeypatch,
        source_family=source_family,
        vacancy_uri="https://unlisted-service.example/jobs/1",
    )
    plan = build_source_plan(selection_key=row.selection_key)
    assert plan.root_class is expected_root_class


@pytest.mark.parametrize(
    ("source_family", "root_uri", "self_promoted_uri"),
    [
        (
            "greenhouse",
            "https://jobs.workable.com/acme/1",
            "https://careers.workable.com/acme",
        ),
        (
            "duckduckgo",
            "https://jobs.jobvite.com/acme/1",
            "https://www.jobvite.com/acme/careers",
        ),
        (
            "remoteok",
            "https://acme.bamboohr.com/careers/1",
            "https://jobs.bamboohr.com/acme",
        ),
    ],
)
def test_unlisted_service_domains_cannot_self_promote_their_registrable_scope(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source_family: str,
    root_uri: str,
    self_promoted_uri: str,
) -> None:
    row = _load_row(
        tmp_path,
        monkeypatch,
        source_family=source_family,
        vacancy_uri=root_uri,
    )
    body = f'<a href="{self_promoted_uri}">Careers</a>'.encode()
    artifacts_root = tmp_path / "captures"
    receipt = build_discovery_receipt(
        root_uri=root_uri,
        requests=(_write_capture(artifacts_root, body, uri=root_uri),),
        artifacts_root=artifacts_root,
    )
    assert admit_official_domain(
        receipt,
        selection_key=row.selection_key,
        artifacts_root=artifacts_root,
    ) == UnresolvedIdentityOutcome(
        reasons=(MaterializationReason.SOURCE_NOT_ADMISSIBLE,)
    )


def test_unlisted_service_root_can_prove_an_external_official_company_link(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root_uri = "https://jobs.workable.com/acme/1"
    row = _load_row(
        tmp_path,
        monkeypatch,
        source_family="duckduckgo",
        vacancy_uri=root_uri,
    )
    body = f'<a href="{LINK}">Company Website</a>'.encode()
    artifacts_root = tmp_path / "captures"
    receipt = build_discovery_receipt(
        root_uri=root_uri,
        requests=(_write_capture(artifacts_root, body, uri=root_uri),),
        artifacts_root=artifacts_root,
    )
    outcome = admit_official_domain(
        receipt,
        selection_key=row.selection_key,
        artifacts_root=artifacts_root,
    )
    assert isinstance(outcome, AdmittedIdentityOutcome)
    assert outcome.authority.domain == "www.acme.test"


def test_explicit_company_website_family_can_prove_its_own_official_domain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root_uri = "https://www.acme.test/jobs/1"
    row = _load_row(
        tmp_path,
        monkeypatch,
        source_family="company_website",
        vacancy_uri=root_uri,
    )
    body = '<a href="https://careers.acme.test/">Company Website</a>'.encode()
    artifacts_root = tmp_path / "captures"
    receipt = build_discovery_receipt(
        root_uri=root_uri,
        requests=(_write_capture(artifacts_root, body, uri=root_uri),),
        artifacts_root=artifacts_root,
    )
    outcome = admit_official_domain(
        receipt,
        selection_key=row.selection_key,
        artifacts_root=artifacts_root,
    )
    assert isinstance(outcome, AdmittedIdentityOutcome)
    assert outcome.source_plan.root_class is DiscoveryRootClass.OFFICIAL_COMPANY
    assert outcome.authority.domain == "careers.acme.test"


def test_unknown_future_source_family_fails_closed_without_policy_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        corpus_root,
        gate_a_root,
        corpus_sha256,
        gate_a_manifest_sha256,
        selection_key,
    ) = _write_gate_a_fixture(tmp_path, source_family="workable")
    _pin_fixture(
        monkeypatch,
        corpus_root=corpus_root,
        gate_a_root=gate_a_root,
        corpus_sha256=corpus_sha256,
        gate_a_manifest_sha256=gate_a_manifest_sha256,
    )
    with pytest.raises(ValueError):
        load_pinned_gate_a_row(selection_key=selection_key)


def test_receipt_load_rehashes_actual_capture_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, artifacts_root, receipt, _ = _materialized(tmp_path, monkeypatch)
    artifact_path = artifacts_root / receipt.requests[0].capture_artifact_sha256
    artifact_path.write_bytes(BODY.replace(b"Careers", b"Careerx"))
    with pytest.raises(ValueError, match="capture artifact sha256"):
        load_discovery_receipt(
            receipt.model_dump_json(),
            artifacts_root=artifacts_root,
        )


def test_receipt_load_revalidates_exact_artifact_byte_range(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, artifacts_root, receipt, _ = _materialized(tmp_path, monkeypatch)
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


def test_admission_rejects_identical_byte_capture_inode_replacement_between_checks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = _load_row(tmp_path, monkeypatch)
    artifacts_root = tmp_path / "captures"
    request = _write_capture(artifacts_root)
    receipt = build_discovery_receipt(
        root_uri=ROOT,
        requests=(request,),
        artifacts_root=artifacts_root,
    )
    artifact_path = artifacts_root / request.capture_artifact_sha256
    original_inode = artifact_path.stat().st_ino
    real_verify = input_materialization._verify_receipt_artifacts
    calls = 0

    def replace_after_first_verification(
        receipt_to_verify: DiscoveryReceipt,
        root_to_verify: Path | str,
    ) -> object:
        nonlocal calls
        verified = real_verify(receipt_to_verify, root_to_verify)
        calls += 1
        if calls == 1:
            replacement = artifacts_root / "replacement"
            replacement.write_bytes(BODY)
            replacement.replace(artifact_path)
            assert artifact_path.stat().st_ino != original_inode
        return verified

    monkeypatch.setattr(
        input_materialization,
        "_verify_receipt_artifacts",
        replace_after_first_verification,
    )
    with pytest.raises(ValueError, match="admission authority changed"):
        admit_official_domain(
            receipt,
            selection_key=row.selection_key,
            artifacts_root=artifacts_root,
        )


def test_unrelated_sibling_activity_does_not_change_capture_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = _load_row(tmp_path, monkeypatch)
    artifacts_root = tmp_path / "captures"
    request = _write_capture(artifacts_root)
    receipt = build_discovery_receipt(
        root_uri=ROOT,
        requests=(request,),
        artifacts_root=artifacts_root,
    )
    real_verify = input_materialization._verify_receipt_artifacts
    calls = 0

    def create_sibling_after_first_verification(
        receipt_to_verify: DiscoveryReceipt,
        root_to_verify: Path | str,
    ) -> object:
        nonlocal calls
        verified = real_verify(receipt_to_verify, root_to_verify)
        calls += 1
        if calls == 1:
            (artifacts_root / "unrelated-sibling").write_text("unrelated")
        return verified

    monkeypatch.setattr(
        input_materialization,
        "_verify_receipt_artifacts",
        create_sibling_after_first_verification,
    )
    outcome = admit_official_domain(
        receipt,
        selection_key=row.selection_key,
        artifacts_root=artifacts_root,
    )
    assert isinstance(outcome, AdmittedIdentityOutcome)


def test_receipt_persists_only_the_minimal_exact_element_not_neighboring_body(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, _, receipt, _ = _materialized(tmp_path, monkeypatch)
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
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, artifacts_root, receipt, _ = _materialized(tmp_path, monkeypatch)
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
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row, artifacts_root, _, outcome = _materialized(tmp_path, monkeypatch)
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
            selection_key=row.selection_key,
            artifacts_root=artifacts_root,
        )
        == outcome
    )


def test_distinct_modern_idna_authorities_remain_ambiguous(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = '<a href="https://faß.de/jobs">Careers</a>'
    second = '<a href="https://fass.de/jobs">Careers</a>'
    body = (first + second).encode()
    row = _load_row(tmp_path, monkeypatch)
    artifacts_root = tmp_path / "captures"
    receipt = build_discovery_receipt(
        root_uri=ROOT,
        requests=(_write_capture(artifacts_root, body),),
        artifacts_root=artifacts_root,
    )
    result = admit_official_domain(
        receipt,
        selection_key=row.selection_key,
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
