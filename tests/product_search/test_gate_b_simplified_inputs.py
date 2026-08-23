from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
import hashlib
import inspect
import json
import os
from pathlib import Path
from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError

import job_intel.product_search.contracts as contracts
import job_intel.product_search.decision_v2 as decision_v2
import job_intel.product_search.evidence_synthesis as synthesis
import job_intel.product_search.gate_b as gate_b
from job_intel.product_search.gate_b import GateBPreflightError


GATE_A_ROOT = Path(
    "/home/hermes/.hermes/job_intel/experiments/gate-a/"
    "65d60daae16093a9a7e34a11a159e2f789dd14dd"
)
CORPUS_SHA256 = "b1db802dbb3d0e2a18771f32da12b901b3bb9e941ae71b785a3c71142abf2d69"
OWNER_CAPABILITY = "owner-random-fixture-capability-task12b"


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _preflight(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    monkeypatch.setattr(gate_b, "GATE_B_EXPERIMENT_ROOT", tmp_path)
    return gate_b.build_dry_run_preflight(gate_a_root=GATE_A_ROOT)


def _approval(preflight: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "2.0.0",
        "status": "approved",
        "run_identity_sha256": preflight["record_identity_sha256"],
        "capability_sha256": _sha256(OWNER_CAPABILITY.encode()),
        "exact_call_cap": 48,
        "exact_spend_cap_usd": "0.48",
        "max_cost_per_call_usd": "0.01",
        "pricing_sha256": preflight["record_identity"]["pricing_sha256"],
        "corpus_manifest_sha256": preflight["corpus"]["manifest_sha256"],
        "input_manifest_sha256": preflight["inputs"]["manifest_sha256"],
        "ordered_input_hashes_sha256": preflight["inputs"][
            "ordered_input_hashes_sha256"
        ],
        "max_output_tokens": preflight["record_identity"]["max_output_tokens"],
    }


def _claim_test_runner(authorization: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    witness = gate_b.export_gate_b_launch_witness_request(authorization)
    monkeypatch.setattr(gate_b, "_read_privileged_launch_witness", lambda: witness)
    gate_b._claim_privileged_launch(authorization)


def _load_first_input(preflight: dict[str, Any]) -> object:
    manifest = gate_b.load_gate_b_run_manifest(
        Path(preflight["inputs"]["manifest_path"]),
        expected_sha256=preflight["inputs"]["manifest_sha256"],
        expected_corpus_sha256=CORPUS_SHA256,
    )
    record = manifest["records"][0]
    return gate_b.load_gate_b_task10_input(
        package_root=Path(preflight["inputs"]["package_root"]),
        record=record,
        gate_a_root=GATE_A_ROOT,
    )


def test_company_authority_v2_is_a_closed_available_or_unavailable_union() -> None:
    unavailable_cls = getattr(synthesis, "CompanyAuthorityUnavailableV2")
    union = getattr(synthesis, "CompanyAuthorityInputV2")
    unavailable = unavailable_cls(
        status="unavailable",
        reason="unresolved_company_identity",
        company_evidence_bundle=None,
        official_domain_claim=None,
        company_facts=(),
        citations=(),
    )
    assert unavailable.model_dump(mode="json") == {
        "schema_version": "2.0.0",
        "status": "unavailable",
        "reason": "unresolved_company_identity",
        "company_evidence_bundle": None,
        "official_domain_claim": None,
        "company_facts": [],
        "citations": [],
    }
    schema = TypeAdapter(union).json_schema()
    assert schema["discriminator"]["mapping"].keys() == {"available", "unavailable"}

    for reason in (
        "unresolved_company_identity",
        "company_evidence_unavailable",
        "no_admissible_public_evidence",
    ):
        assert (
            unavailable_cls(status="unavailable", reason=reason).reason.value == reason
        )
    with pytest.raises(ValidationError):
        unavailable_cls(
            status="unavailable",
            reason="unresolved_company_identity",
            company_facts=("Wolt is a technology company",),
        )
    with pytest.raises(ValidationError):
        unavailable_cls(
            status="unavailable",
            reason="unresolved_company_identity",
            citations=("vacancy:company-label",),
        )
    with pytest.raises(ValidationError):
        unavailable_cls(
            status="unavailable",
            reason="unresolved_company_identity",
            official_domain_claim="wolt.com",
        )


def test_assessment_v2_requires_company_unknown_when_authority_is_unavailable() -> None:
    assessment_cls = getattr(contracts, "AssessmentInputV2")
    schema = assessment_cls.model_json_schema()
    assert schema["properties"]["schema_version"]["const"] == "2.0.0"
    assert "company_authority_status" in schema["properties"]


def test_dry_preflight_materializes_48_truthful_v2_inputs_in_canonical_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    preflight = _preflight(tmp_path, monkeypatch)
    assert preflight["corpus"]["manifest_sha256"] == CORPUS_SHA256
    package = preflight["inputs"]
    assert package["status"] == "materialized"
    assert package["record_count"] == 48
    assert package["vacancy_artifact_count"] == 48
    assert package["company_authority_status"] == "unavailable"
    assert package["company_authority_reason"] == "company_evidence_unavailable"
    assert package["ordered_input_sha256s"] == sorted(
        package["ordered_input_sha256s"],
        key=lambda value: package["ordered_input_sha256s"].index(value),
    )
    assert len(set(package["ordered_input_sha256s"])) == 48
    assert package["ordered_input_hashes_sha256"] == _sha256(
        _canonical_bytes(package["ordered_input_sha256s"])
    )

    manifest = gate_b.load_gate_b_run_manifest(
        Path(package["manifest_path"]),
        expected_sha256=package["manifest_sha256"],
        expected_corpus_sha256=CORPUS_SHA256,
    )
    assert manifest["schema_version"] == "2.0.0"
    assert manifest["authorization_constraints"] == {
        "aggregate_maximum_usd": "0.48",
        "call_cap": 48,
        "maximum_output_tokens": 2000,
        "per_call_maximum_usd": "0.01",
        "provider_allowlist": package["ordered_input_sha256s"],
    }
    assert [record["ordinal"] for record in manifest["records"]] == list(range(48))
    assert [record["task10_input_sha256"] for record in manifest["records"]] == (
        package["ordered_input_sha256s"]
    )

    package_root = Path(package["package_root"])
    corpus = json.loads(Path(preflight["corpus"]["manifest_path"]).read_text())
    company_by_selection = {
        item["selection_key"]: item["company"] for item in corpus["records"]
    }
    seen_artifacts: set[str] = set()
    for record in manifest["records"]:
        task_input = gate_b.load_gate_b_task10_input(
            package_root=package_root,
            record=record,
            gate_a_root=GATE_A_ROOT,
        )
        assert isinstance(task_input, getattr(synthesis, "EvidenceSynthesisInputV2"))
        assert task_input.schema_version == "2.0.0"
        assert task_input.company_authority.status == "unavailable"
        assert (
            task_input.company_authority.reason.value == "company_evidence_unavailable"
        )
        assert (
            task_input.assessment_input.dimensions.company_fit.state.value == "unknown"
        )
        assert task_input.assessment_input.dimensions.company_fit.evidence_refs == ()
        dumped = task_input.provider_payload()
        assert dumped["company_authority"] == {
            "schema_version": "2.0.0",
            "status": "unavailable",
            "reason": "company_evidence_unavailable",
            "company_evidence_bundle": None,
            "official_domain_claim": None,
            "company_facts": [],
            "citations": [],
        }
        assert "company" not in dumped
        assert "official_domain" not in dumped
        assert all(
            fragment["source_kind"] != "company" for fragment in dumped["fragments"]
        )
        company_label = company_by_selection[record["selection_key"]].casefold()
        company_bearing_hashes = {
            _sha256(fragment.text.encode())
            for fragment in task_input.vacancy_evidence.fragments
            if company_label in fragment.text.casefold()
        }
        assert set(task_input.prohibited_company_claim_text_sha256s) == (
            company_bearing_hashes
        )
        assert all(
            fragment.text_sha256 not in company_bearing_hashes
            for fragment in task_input.fragments
            if fragment.source_kind.value == "vacancy"
        )
        seen_artifacts.add(record["vacancy_artifact_sha256"])
    assert len(seen_artifacts) == 48


def test_v2_input_requires_explicit_company_claim_exclusion_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    task_input = _load_first_input(_preflight(tmp_path, monkeypatch))
    payload = task_input.model_dump(mode="json")
    payload.pop("prohibited_company_claim_text_sha256s")

    with pytest.raises(ValidationError, match="prohibited_company_claim_text_sha256s"):
        synthesis.EvidenceSynthesisInputV2.model_validate(payload)


def test_gate_b_input_package_rejects_any_non_exact_call_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(gate_b, "GATE_B_EXPERIMENT_ROOT", tmp_path)
    with pytest.raises(GateBPreflightError, match="exact_sample_size"):
        gate_b.build_dry_run_preflight(gate_a_root=GATE_A_ROOT, sample_size=47)


def test_v2_validator_rejects_invented_company_claims_and_unavailable_citations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    task_input = _load_first_input(_preflight(tmp_path, monkeypatch))
    validator = getattr(synthesis, "validate_provider_payload_v2")
    invented = {
        "schema_version": "2.0.0",
        "claims": [
            {
                "claim_id": "claim:invented-company",
                "dimension": "company_fit",
                "status": "explicit",
                "claim_code": "company_growth",
                "statement": "The employer has strong growth.",
                "citations": ["vacancy:title"],
            }
        ],
        "conflicts": [],
        "question_candidates": [],
    }
    assert validator(invented, synthesis_input=task_input) == (
        synthesis.EvidenceSynthesisStatus.UNSUPPORTED_CLAIM
    )
    unavailable_citation = deepcopy(invented)
    unavailable_citation["claims"][0]["citations"] = ["company:unavailable"]
    assert validator(unavailable_citation, synthesis_input=task_input) in {
        synthesis.EvidenceSynthesisStatus.FOREIGN_CITATION,
        synthesis.EvidenceSynthesisStatus.UNSUPPORTED_CLAIM,
    }


def test_v2_validator_rejects_company_bearing_claim_in_non_company_dimension(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    task_input = _load_first_input(_preflight(tmp_path, monkeypatch))
    company_artifact_fragment = next(
        fragment
        for fragment in task_input.vacancy_evidence.fragments
        if "wolt" in fragment.text.casefold()
    )
    company_hash = _sha256(company_artifact_fragment.text.encode())
    existing = next(
        (
            fragment
            for fragment in task_input.fragments
            if fragment.source_locator == company_artifact_fragment.source_locator
            and synthesis.EvidenceDimension.MANDATE_FIT in fragment.permitted_dimensions
        ),
        None,
    )
    company_fragment = existing or synthesis.EvidenceFragmentV1(
        fragment_id="vacancy:company-bearing-mandate-mutation",
        artifact_ref=task_input.vacancy_evidence_ref,
        source_kind="vacancy",
        source_locator=company_artifact_fragment.source_locator,
        permitted_dimensions=("mandate_fit",),
        text=company_artifact_fragment.text,
        text_sha256=company_hash,
        allowed_claims=(
            synthesis.AllowedEvidenceClaimV1(
                claim_code="vacancy_description_mandate_fit_explicit",
                dimension="mandate_fit",
                status="explicit",
                statement=company_artifact_fragment.text,
            ),
        ),
    )
    unsafe_input = task_input.model_copy(deep=True)
    if existing is None:
        object.__setattr__(
            unsafe_input,
            "fragments",
            (*unsafe_input.fragments, company_fragment),
        )
    object.__setattr__(
        unsafe_input,
        "prohibited_company_claim_text_sha256s",
        (company_hash,),
    )

    claims: list[dict[str, object]] = []
    for dimension in synthesis.EvidenceDimension:
        fragment = (
            company_fragment
            if dimension is synthesis.EvidenceDimension.MANDATE_FIT
            else next(
                item
                for item in unsafe_input.fragments
                if any(claim.dimension is dimension for claim in item.allowed_claims)
            )
        )
        allowed = next(
            claim for claim in fragment.allowed_claims if claim.dimension is dimension
        )
        claims.append({
            "claim_id": f"claim:{dimension.value}",
            "dimension": dimension.value,
            "status": allowed.status.value,
            "claim_code": allowed.claim_code,
            "statement": allowed.statement,
            "citations": [fragment.fragment_id],
        })
    payload = {
        "schema_version": "2.0.0",
        "claims": claims,
        "conflicts": [],
        "question_candidates": [],
    }

    assert (
        synthesis.validate_provider_payload_v2(
            payload,
            synthesis_input=unsafe_input,
        )
        is synthesis.EvidenceSynthesisStatus.UNSUPPORTED_CLAIM
    )


def test_readiness_summary_uses_v2_provider_schema_hash_consistently() -> None:
    summary_path = (
        Path(gate_b.__file__).resolve().parents[2]
        / "docs/evidence/product-search-gate-b/benchmark-summary.json"
    )
    summary = json.loads(summary_path.read_text())
    expected = "428586420dd32c64343c5ac8d59466870319037ab84702b0c7ee1866bf5274ab"
    assert summary["record_identity"]["provider_output_schema_sha256"] == expected
    assert summary["candidate_hashes"]["provider_output_schema_sha256"] == expected


def test_decision_adapter_never_elevates_unavailable_company_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    task_input = _load_first_input(_preflight(tmp_path, monkeypatch))
    result_cls = getattr(synthesis, "EvidenceSynthesisResultV2")
    metadata_cls = getattr(synthesis, "EvidenceSynthesisMetadataV2")
    result = result_cls(
        schema_version="2.0.0",
        status="deliverable",
        deliverable=True,
        claims=(),
        conflicts=(),
        question_candidates=(),
        failure_reason=None,
        company_authority_status="unavailable",
        metadata=metadata_cls(
            provider_id="llm-observation",
            provider_version="product-search-evidence-replay/2.0",
            model_id="openai/gpt-5-mini",
            semantic_prompt_version="llm-obs-1.0.0",
            prompt_version="product-search-evidence-synthesis-2.0.0",
            schema_version="2.0.0",
            latency_ms=1,
            cost_usd="0.000001",
            input_sha256="1" * 64,
            output_sha256="2" * 64,
        ),
    )
    decision = getattr(decision_v2, "consume_synthesis_v2_fail_closed")(
        synthesis_input=task_input,
        synthesis_result=result,
    )
    assert decision.status.value == "fail_closed"
    assert decision.assessment is None
    assert decision.failure_reason == (
        "company_authority_unavailable:company_evidence_unavailable"
    )


@pytest.mark.parametrize(
    "mutation", ["duplicate", "reorder", "mutable_path", "mixed_gate_a_run"]
)
def test_run_manifest_rejects_duplicate_reordered_or_mutable_inputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    preflight = _preflight(tmp_path, monkeypatch)
    source = Path(preflight["inputs"]["manifest_path"])
    payload = json.loads(source.read_text(encoding="utf-8"))
    if mutation == "duplicate":
        payload["records"][1] = deepcopy(payload["records"][0])
        payload["records"][1]["ordinal"] = 1
    elif mutation == "reorder":
        payload["records"][0], payload["records"][1] = (
            payload["records"][1],
            payload["records"][0],
        )
    elif mutation == "mutable_path":
        payload["records"][0]["task10_input_path"] = str(source)
    else:
        payload["records"][0]["run_id"] = "gate-a-other-run"
    mutated = tmp_path / f"{mutation}.json"
    mutated.write_bytes(_canonical_bytes(payload))
    with pytest.raises(GateBPreflightError):
        gate_b.load_gate_b_run_manifest(
            mutated,
            expected_sha256=_sha256(mutated.read_bytes()),
            expected_corpus_sha256=CORPUS_SHA256,
        )


def test_run_manifest_and_input_loaders_reject_symlinks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    preflight = _preflight(tmp_path / "package", monkeypatch)
    source = Path(preflight["inputs"]["manifest_path"])
    link = tmp_path / "manifest-link.json"
    link.symlink_to(source)
    with pytest.raises(GateBPreflightError, match="symlink"):
        gate_b.load_gate_b_run_manifest(
            link,
            expected_sha256=preflight["inputs"]["manifest_sha256"],
            expected_corpus_sha256=CORPUS_SHA256,
        )

    package_root = Path(preflight["inputs"]["package_root"])
    relocated = tmp_path / "relocated-package"
    package_root.rename(relocated)
    package_root.symlink_to(relocated, target_is_directory=True)
    manifest = json.loads((relocated / "run-manifest.v2.json").read_text())
    with pytest.raises(GateBPreflightError, match="root_symlink"):
        gate_b.load_gate_b_task10_input(
            package_root=package_root,
            record=manifest["records"][0],
            gate_a_root=GATE_A_ROOT,
        )


def test_authorization_binds_ordered_allowlist_and_ledger_rejects_foreign_hash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    preflight = _preflight(tmp_path, monkeypatch)
    approval = _approval(preflight)
    authorization = gate_b.authorize_record_run(
        preflight,
        approval_record=approval,
        owner_capability=OWNER_CAPABILITY,
    )
    _claim_test_runner(authorization, monkeypatch)
    assert authorization.ordered_input_sha256s == tuple(
        preflight["inputs"]["ordered_input_sha256s"]
    )
    ledger = gate_b.GateBBudgetLedger(
        authorization.experiment_root / "run-ledger.sqlite3", authorization
    )
    with pytest.raises(GateBPreflightError, match="input_not_allowlisted"):
        ledger.reserve("f" * 64, Decimal("0.010000"))


def test_authorization_rejects_input_manifest_or_allowlist_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    preflight = _preflight(tmp_path, monkeypatch)
    approval = _approval(preflight)
    for field in ("input_manifest_sha256", "ordered_input_hashes_sha256"):
        mutated = dict(approval)
        mutated[field] = "0" * 64
        with pytest.raises(GateBPreflightError, match="input"):
            gate_b.authorize_record_run(
                preflight,
                approval_record=mutated,
                owner_capability=OWNER_CAPABILITY,
            )


def test_single_public_record_runner_owns_live_factory_and_input_loader(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    signature = inspect.signature(gate_b.run_gate_b_record)
    assert set(signature.parameters) == {"authorization"}
    preflight = _preflight(tmp_path, monkeypatch)
    authorization = gate_b.authorize_record_run(
        preflight,
        approval_record=_approval(preflight),
        owner_capability=OWNER_CAPABILITY,
    )
    with pytest.raises(GateBPreflightError, match="launch_witness_unavailable"):
        gate_b.run_gate_b_record(authorization=authorization)
    assert not (authorization.experiment_root / "run-ledger.sqlite3").exists()
