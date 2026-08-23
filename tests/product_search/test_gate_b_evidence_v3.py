from __future__ import annotations

import ast
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

import job_intel.product_search.evidence_synthesis as synthesis
import job_intel.product_search.gate_b_evidence_v3 as evidence
from job_intel.product_search.gate_b_evidence_v3 import (
    CandidateTupleV3,
    ReviewedFragmentAllowlistV3,
    ReviewedFragmentDecisionV3,
    ReviewedFragmentEntryV3,
    generate_reviewed_fragment_allowlist_v3,
    load_gate_b_evidence_policy_v3,
)
from job_intel.product_search.input_materialization import (
    CANONICAL_GATE_A_ROOT,
    CANONICAL_GATE_B_CORPUS_ROOT,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PINNED_ALLOWLIST_PATH = (
    REPOSITORY_ROOT / "docs/evidence/product-search-gate-b/v3-fragment-allowlist.yaml"
)
CORRECTED_TUPLE_TABLE_SHA256 = (
    "8ed0e1d719acd872a5aa931f157c7b7812a977220f8d4db721ce3aa7062e4c65"
)


def _record(selection_key: str = "a" * 64) -> dict[str, str]:
    return {"selection_key": selection_key}


def _allowlist(
    candidates: object,
    *,
    decisions: dict[str, ReviewedFragmentDecisionV3],
) -> ReviewedFragmentAllowlistV3:
    return ReviewedFragmentAllowlistV3(
        schema_version="3.0.0",
        gate_a_run_id="gate-a-20260816T141344Z",
        gate_b_corpus_sha256=(
            "b1db802dbb3d0e2a18771f32da12b901b3bb9e941ae71b785a3c71142abf2d69"
        ),
        entries=tuple(
            ReviewedFragmentEntryV3(
                selection_key=candidates.selection_key,
                vacancy_artifact_sha256=candidates.vacancy_artifact_sha256,
                source_locator=candidate.source_locator,
                text_sha256=candidate.text_sha256,
                decision=decisions[candidate.text],
                reviewer_role="independent_gate_b_evidence_reviewer",
                reviewed_at=datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc),
            )
            for candidate in candidates.description_candidates
        ),
    )


def test_candidate_tuple_serialization_is_canonical_and_newline_free() -> None:
    """Mutation caught: a reviewer receives a byte-different tuple table."""
    first_entry = CandidateTupleV3(
        selection_key="a" * 64,
        vacancy_artifact_sha256="b" * 64,
        source_locator="/description#002",
        text_sha256="c" * 64,
        section="requirements",
    )
    second_entry = CandidateTupleV3(
        selection_key="a" * 64,
        vacancy_artifact_sha256="b" * 64,
        source_locator="/description#001",
        text_sha256="d" * 64,
        section="skills",
    )

    first = evidence.serialize_candidate_tuple_table_v3(
        records=48,
        entries=(first_entry, second_entry),
    )
    second = evidence.serialize_candidate_tuple_table_v3(
        records=48,
        entries=(second_entry, first_entry),
    )

    assert first == second
    assert first[-1:] != b"\n"
    assert hashlib.sha256(first).hexdigest() == (
        "97b4cef75e03d4095c18cf3154084ebd9bdcfa41307b6196af6ad21a5f869b53"
    )
    assert json.loads(first)["entries"][0]["source_locator"] == "/description#001"


def test_v3_allowlist_is_hash_only_and_closed() -> None:
    """Mutation caught: raw vacancy text or an unreviewed decision enters v3."""
    entry = ReviewedFragmentEntryV3(
        selection_key="a" * 64,
        vacancy_artifact_sha256="b" * 64,
        source_locator="/description#000",
        text_sha256="c" * 64,
        decision=ReviewedFragmentDecisionV3.ALLOW_ROLE_RESPONSIBILITY,
        reviewer_role="independent_gate_b_evidence_reviewer",
        reviewed_at=datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc),
    )
    allowlist = ReviewedFragmentAllowlistV3(
        schema_version="3.0.0",
        gate_a_run_id="gate-a-20260816T141344Z",
        gate_b_corpus_sha256=(
            "b1db802dbb3d0e2a18771f32da12b901b3bb9e941ae71b785a3c71142abf2d69"
        ),
        entries=(entry,),
    )

    assert allowlist.entries == (entry,)
    assert "Lead quarterly roadmap" not in allowlist.model_dump_json()
    with pytest.raises(ValidationError):
        ReviewedFragmentEntryV3.model_validate({
            **entry.model_dump(mode="json"),
            "raw_text": "Lead quarterly roadmap planning with engineering and design",
        })


def test_v3_policy_has_the_exact_company_deny_prefilter() -> None:
    """Mutation caught: a company or marketing phrase bypasses the v3 prefilter."""
    policy = load_gate_b_evidence_policy_v3()

    assert policy.description_claim_admission == "reviewed_hash_allowlist_only"
    assert policy.company_fact_deny_patterns == (
        r"\bwe\b",
        r"\bour\b",
        r"\bour company\b",
        r"\bthe company\b",
        r"\bglobal leader\b",
        r"\bmarket leader\b",
        r"\bplatform\b",
        r"\bcustomers?\b",
        r"\bclients?\b",
        r"\brevenue\b",
        r"\bmerchant volume\b",
        r"\bmarket share\b",
        r"\bfunding\b",
        r"\bseries [a-z]\b",
        r"\bemployees?\b",
        r"\boffices?\b",
        r"\bexpansion\b",
        r"\bfastest[- ]growing\b",
    )


def test_evidence_module_does_not_import_benchmark_module() -> None:
    source = Path(evidence.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    imported_modules.update(
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    )
    assert "job_intel.product_search.gate_b_benchmark_v3" not in imported_modules


def test_projector_characterization_keeps_role_direct_and_unknown_dimensions() -> None:
    role_statement = "Lead quarterly roadmap planning with engineering and design."
    raw = {
        "title": "Head of Product",
        "location": "Almaty",
        "description": f"<h2>Responsibilities</h2><p>{role_statement}</p>",
    }
    candidates = evidence.build_vacancy_projection_candidates_v3(_record(), raw)
    allowlist = _allowlist(
        candidates,
        decisions={
            role_statement: ReviewedFragmentDecisionV3.ALLOW_ROLE_RESPONSIBILITY,
        },
    )

    projected = evidence.project_vacancy_evidence_v3(_record(), raw, allowlist)

    assert [fragment.source_locator for fragment in projected.fragments] == [
        "/title#000",
        "/location#000",
        "/description#000",
        "company_authority_unavailable:unresolved_company_identity",
        "candidate_profile_evidence_not_materialized",
    ]
    assert projected.assessment_input.dimensions.mandate_fit.state == (
        evidence.DimensionEvidenceState.EVIDENCE_AVAILABLE
    )
    assert projected.assessment_input.dimensions.company_fit.state == (
        evidence.DimensionEvidenceState.UNKNOWN
    )
    assert projected.prohibited_company_claim_text_sha256s == ()


def test_role_statement_needs_an_exact_independent_review_entry() -> None:
    """Mutation caught: an unreviewed or byte-mutated role fragment reaches a provider."""
    role_statement = "Lead quarterly roadmap planning with engineering and design."
    raw = {
        "title": "Head of Product",
        "location": "Almaty",
        "salary": "$100k",
        "posted_at": "2026-08-20",
        "description": (f"<h2>Responsibilities</h2><p>{role_statement}</p>"),
    }
    candidates = evidence.build_vacancy_projection_candidates_v3(_record(), raw)
    candidate = candidates.description_candidates[0]
    assert candidate.section == "responsibilities"
    assert candidate.source_locator == "/description#000"
    assert candidate.text == role_statement
    assert candidate.text_sha256 == hashlib.sha256(role_statement.encode()).hexdigest()

    allowlist = _allowlist(
        candidates,
        decisions={
            role_statement: ReviewedFragmentDecisionV3.ALLOW_ROLE_RESPONSIBILITY,
        },
    )
    result = evidence.project_vacancy_evidence_v3(_record(), raw, allowlist)

    assert any(fragment.text == role_statement for fragment in result.fragments)
    assert any(
        claim.statement == role_statement
        for fragment in result.fragments
        for claim in fragment.allowed_claims
    )

    mutated_raw = {**raw, "description": raw["description"].replace("design", "desigx")}
    with pytest.raises(evidence.ProjectionBlockedV3, match="reviewed"):
        evidence.project_vacancy_evidence_v3(_record(), mutated_raw, allowlist)


def test_company_and_marketing_fragments_are_excluded_before_provider_input() -> None:
    """Mutation caught: company facts leak through a role dimension or a missing label."""
    adversarial = (
        "We are a global leader in the Transportation and Logistics industry.",
        "More than $100 billion in merchant volume flows through our platform.",
        "Our customers span 40 markets and the company is expanding rapidly.",
        "Backed by Series C funding, we have 2,000 employees worldwide.",
    )
    raw = {
        "title": "General Manager",
        "location": "Suriname",
        "description": (
            "<h2>Requirements</h2>"
            + "".join(f"<p>{statement}</p>" for statement in adversarial)
        ),
    }
    candidates = evidence.build_vacancy_projection_candidates_v3(_record(), raw)
    assert [candidate.text for candidate in candidates.description_candidates] == list(
        adversarial
    )
    allowlist = _allowlist(
        candidates,
        decisions={
            statement: ReviewedFragmentDecisionV3.EXCLUDE_COMPANY_FACT
            for statement in adversarial
        },
    )

    result = evidence.project_vacancy_evidence_v3(_record(), raw, allowlist)
    audit = evidence.audit_vacancy_projection_v3(_record(), raw, allowlist)

    assert all(fragment.text not in adversarial for fragment in result.fragments)
    assert audit.company_fact_candidates_admitted == 0
    assert audit.ambiguous_fragments_admitted == 0

    invalid_allowlist = _allowlist(
        candidates,
        decisions={
            statement: ReviewedFragmentDecisionV3.ALLOW_ROLE_REQUIREMENT
            for statement in adversarial
        },
    )
    with pytest.raises(evidence.ProjectionBlockedV3, match="company"):
        evidence.project_vacancy_evidence_v3(_record(), raw, invalid_allowlist)


def test_v3_provider_payload_omits_all_excluded_artifact_text_and_hashes() -> None:
    """Mutation caught: local immutable artifact bytes leak into v3 provider input."""
    role_statement = "Own the product roadmap with engineering and design."
    company_statement = "We are a global leader with millions of customers."
    ambiguous_statement = "This is an exceptional opportunity for the right person."
    raw = {
        "title": "Head of Product",
        "description": (
            "<h2>Responsibilities</h2>"
            f"<p>{role_statement}</p><p>{company_statement}</p>"
            f"<p>{ambiguous_statement}</p>"
        ),
    }
    candidates = evidence.build_vacancy_projection_candidates_v3(_record(), raw)
    allowlist = _allowlist(
        candidates,
        decisions={
            role_statement: ReviewedFragmentDecisionV3.ALLOW_ROLE_RESPONSIBILITY,
            company_statement: ReviewedFragmentDecisionV3.EXCLUDE_COMPANY_FACT,
            ambiguous_statement: ReviewedFragmentDecisionV3.EXCLUDE_AMBIGUOUS,
        },
    )

    result = evidence.project_vacancy_evidence_v3(_record(), raw, allowlist)
    payload = result.provider_payload()
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))

    assert "vacancy_evidence" not in payload
    assert payload["vacancy_evidence_ref"] == result.vacancy_evidence_ref.model_dump(
        mode="json"
    )
    assert role_statement in serialized
    for excluded in (company_statement, ambiguous_statement):
        assert excluded not in serialized
        assert hashlib.sha256(excluded.encode()).hexdigest() not in serialized


def test_v3_provider_serializer_rejects_a_generic_v2_input() -> None:
    """Mutation caught: a caller bypasses the v3-admitted dispatch contract."""
    role_statement = "Own the product roadmap with engineering and design."
    raw = {
        "title": "Head of Product",
        "description": f"<h2>Responsibilities</h2><p>{role_statement}</p>",
    }
    candidates = evidence.build_vacancy_projection_candidates_v3(_record(), raw)
    allowlist = _allowlist(
        candidates,
        decisions={
            role_statement: ReviewedFragmentDecisionV3.ALLOW_ROLE_RESPONSIBILITY,
        },
    )
    result = evidence.project_vacancy_evidence_v3(_record(), raw, allowlist)
    generic_v2_input = evidence.EvidenceSynthesisInputV2.model_validate(
        result.model_dump(mode="json")
    )

    with pytest.raises(TypeError, match="v3-admitted"):
        evidence.serialize_provider_payload_v3(generic_v2_input)


def test_v3_synthesis_dispatch_receives_only_admitted_provider_payload() -> None:
    """Mutation caught: the v2 runner bypasses the v3 serializer at dispatch."""
    role_statement = "Own the product roadmap with engineering and design."
    company_statement = "We are a global leader with millions of customers."
    ambiguous_statement = "This is an exceptional opportunity for the right person."
    raw = {
        "title": "Head of Product",
        "description": (
            "<h2>Responsibilities</h2>"
            f"<p>{role_statement}</p><p>{company_statement}</p>"
            f"<p>{ambiguous_statement}</p>"
        ),
    }
    candidates = evidence.build_vacancy_projection_candidates_v3(_record(), raw)
    allowlist = _allowlist(
        candidates,
        decisions={
            role_statement: ReviewedFragmentDecisionV3.ALLOW_ROLE_RESPONSIBILITY,
            company_statement: ReviewedFragmentDecisionV3.EXCLUDE_COMPANY_FACT,
            ambiguous_statement: ReviewedFragmentDecisionV3.EXCLUDE_AMBIGUOUS,
        },
    )
    result = evidence.project_vacancy_evidence_v3(_record(), raw, allowlist)
    claims = []
    for dimension in evidence.EvidenceDimension:
        fragment = next(
            item
            for item in result.fragments
            if any(claim.dimension is dimension for claim in item.allowed_claims)
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

    class CapturingProvider(synthesis.RecordedEvidenceSynthesisProviderV2):
        def __init__(self) -> None:
            policy = synthesis.load_evidence_synthesis_policy()
            self.provider_id = policy.provider_runtime
            self.provider_version = synthesis.PROVIDER_ADAPTER_VERSION_V2
            self.model_id = policy.model_id
            self.semantic_prompt_version = policy.semantic_prompt_version
            self.prompt_version = synthesis.TASK10_PROMPT_VERSION_V2
            self.output_payload_model = synthesis.ProviderEvidencePayloadV2
            self.last_call_metadata = {"latency_ms": 0, "cost_usd": "0"}
            self.input_payload: dict[str, object] | None = None

        def synthesize_evidence(self, *, input_payload: dict[str, object]) -> object:
            self.input_payload = input_payload
            return {
                "schema_version": "2.0.0",
                "claims": claims,
                "conflicts": [],
                "question_candidates": [],
            }

    provider = CapturingProvider()
    synthesis_result = synthesis.run_evidence_synthesis_v2(
        synthesis_input=result,
        provider=provider,
    )

    assert synthesis_result.status is synthesis.EvidenceSynthesisStatus.DELIVERABLE
    assert provider.input_payload is not None
    serialized = json.dumps(
        provider.input_payload, sort_keys=True, separators=(",", ":")
    )
    assert "vacancy_evidence" not in provider.input_payload
    for excluded in (company_statement, ambiguous_statement):
        assert excluded not in serialized
        assert hashlib.sha256(excluded.encode()).hexdigest() not in serialized


def test_company_section_is_not_a_candidate_and_cannot_be_allowlisted() -> None:
    """Mutation caught: an About-company sentence is reclassified as role evidence."""
    role_statement = "Lead quarterly roadmap planning with engineering and design."
    raw = {
        "title": "Head of Product",
        "location": "Almaty",
        "description": f"<h2>About the company</h2><p>{role_statement}</p>",
    }
    candidates = evidence.build_vacancy_projection_candidates_v3(_record(), raw)

    assert candidates.description_candidates == ()
    role_fragment = next(
        fragment
        for fragment in candidates.vacancy_evidence.fragments
        if fragment.text == role_statement
    )
    invented = ReviewedFragmentAllowlistV3(
        schema_version="3.0.0",
        gate_a_run_id="gate-a-20260816T141344Z",
        gate_b_corpus_sha256=(
            "b1db802dbb3d0e2a18771f32da12b901b3bb9e941ae71b785a3c71142abf2d69"
        ),
        entries=(
            ReviewedFragmentEntryV3(
                selection_key="a" * 64,
                vacancy_artifact_sha256=candidates.vacancy_artifact_sha256,
                source_locator=role_fragment.source_locator,
                text_sha256=hashlib.sha256(role_statement.encode()).hexdigest(),
                decision=ReviewedFragmentDecisionV3.ALLOW_ROLE_RESPONSIBILITY,
                reviewer_role="independent_gate_b_evidence_reviewer",
                reviewed_at=datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc),
            ),
        ),
    )

    with pytest.raises(evidence.ProjectionBlockedV3, match="non-candidate"):
        evidence.project_vacancy_evidence_v3(_record(), raw, invented)


def test_structured_qualified_requirement_heading_is_not_coerced_to_responsibility() -> (
    None
):
    """Mutation caught: a literal qualified heading inherits the prior role section."""
    responsibility = "Lead quarterly roadmap planning with engineering and design."
    heading = "Minimum Qualifications"
    requirement = "Seven years of product management experience are required."
    raw = {
        "title": "Head of Product",
        "description": (
            "<h2>Responsibilities</h2><p>"
            f"{responsibility}</p>"
            f"<p><strong>{heading}</strong></p>"
            f"<ul><li>{requirement}</li></ul>"
        ),
    }
    candidates = evidence.build_vacancy_projection_candidates_v3(_record(), raw)

    assert [candidate.section for candidate in candidates.description_candidates] == [
        "responsibilities",
        "responsibilities",
        "qualifications",
    ]
    allowlist = _allowlist(
        candidates,
        decisions={
            responsibility: ReviewedFragmentDecisionV3.ALLOW_ROLE_RESPONSIBILITY,
            heading: ReviewedFragmentDecisionV3.EXCLUDE_AMBIGUOUS,
            requirement: ReviewedFragmentDecisionV3.ALLOW_ROLE_REQUIREMENT,
        },
    )

    result = evidence.project_vacancy_evidence_v3(_record(), raw, allowlist)

    assert any(fragment.text == requirement for fragment in result.fragments)
    assert all(fragment.text != heading for fragment in result.fragments)


@pytest.mark.parametrize("heading", ["What you'll bring", "Who are you"])
def test_structured_requirement_heading_aliases_are_canonicalized(
    heading: str,
) -> None:
    """Mutation caught: reviewed requirement lists inherit a prior action section."""
    raw = {
        "title": "Head of Product",
        "description": (
            "<h2>What you'll do</h2>"
            f"<p><strong>{heading}</strong></p>"
            "<ul><li>Seven years of product management experience are required.</li></ul>"
        ),
    }

    candidates = evidence.build_vacancy_projection_candidates_v3(_record(), raw)

    assert candidates.description_candidates[-1].section == "requirements"


def test_who_you_are_heading_maps_to_qualifications_without_relabeling_review() -> None:
    """Mutation caught: a reviewed requirement is coerced to a responsibility label."""
    heading = "Who You Are"
    requirement = "Seven years of product management experience are required."
    raw = {
        "title": "Head of Product",
        "description": (
            "<h2>What you'll do</h2>"
            f"<p><strong>{heading}</strong></p>"
            f"<ul><li>{requirement}</li></ul>"
        ),
    }
    candidates = evidence.build_vacancy_projection_candidates_v3(_record(), raw)

    assert candidates.description_candidates[-1].section == "qualifications"
    allowlist = _allowlist(
        candidates,
        decisions={
            heading: ReviewedFragmentDecisionV3.EXCLUDE_AMBIGUOUS,
            requirement: ReviewedFragmentDecisionV3.ALLOW_ROLE_REQUIREMENT,
        },
    )

    result = evidence.project_vacancy_evidence_v3(_record(), raw, allowlist)

    assert any(fragment.text == requirement for fragment in result.fragments)


def test_structured_heading_requires_an_exact_review_before_provider_admission() -> (
    None
):
    """Mutation caught: a genuine role section bypasses the exact-review gate."""
    requirement = "Seven years of product management experience are required."
    raw = {
        "title": "Head of Product",
        "description": (
            f"<p><strong>Qualifications</strong></p><ul><li>{requirement}</li></ul>"
        ),
    }

    candidates = evidence.build_vacancy_projection_candidates_v3(_record(), raw)

    assert candidates.description_candidates[0].section == "qualifications"
    empty_allowlist = ReviewedFragmentAllowlistV3(
        schema_version="3.0.0",
        gate_a_run_id="gate-a-20260816T141344Z",
        gate_b_corpus_sha256=(
            "b1db802dbb3d0e2a18771f32da12b901b3bb9e941ae71b785a3c71142abf2d69"
        ),
        entries=(),
    )

    with pytest.raises(evidence.ProjectionBlockedV3, match="reviewed decision"):
        evidence.project_vacancy_evidence_v3(_record(), raw, empty_allowlist)


def test_emphasized_company_heading_excludes_the_following_description() -> None:
    """Mutation caught: a company section inherits a preceding role classification."""
    statement = "Lead quarterly roadmap planning with engineering and design."
    raw = {
        "title": "Head of Product",
        "description": (
            "<h2>What you'll do</h2>"
            "<p><strong>About the company</strong></p>"
            f"<p>{statement}</p>"
        ),
    }

    candidates = evidence.build_vacancy_projection_candidates_v3(_record(), raw)

    assert all(
        candidate.text != statement for candidate in candidates.description_candidates
    )


def test_long_description_bullet_is_preserved_as_bounded_exact_continuations() -> None:
    """Mutation caught: a long role bullet blocks a 48-record projection or loses text."""
    long_bullet = "Lead product delivery across engineering and design " * 14
    raw = {
        "title": "Head of Product",
        "location": "Almaty",
        "description": f"<h2>Responsibilities</h2><li>{long_bullet}</li>",
    }

    candidates = evidence.build_vacancy_projection_candidates_v3(_record(), raw)

    assert len(candidates.description_candidates) == 2
    assert [
        candidate.source_locator for candidate in candidates.description_candidates
    ] == [
        "/description#000",
        "/description#001",
    ]
    assert all(
        len(candidate.text) <= 500 for candidate in candidates.description_candidates
    )
    assert " ".join(
        candidate.text for candidate in candidates.description_candidates
    ) == (" ".join(long_bullet.split()))


def test_v3_provider_validation_binds_non_unknown_claims_to_reviewed_fragments() -> (
    None
):
    """Mutation caught: a provider relabels an excluded company fact as mandate evidence."""
    role_statement = "Lead quarterly roadmap planning with engineering and design."
    company_statement = (
        "More than $100 billion in merchant volume flows through our platform."
    )
    raw = {
        "title": "Head of Product",
        "location": "Almaty",
        "description": (
            "<h2>Responsibilities</h2><p>"
            f"{role_statement}</p><p>{company_statement}</p>"
        ),
    }
    candidates = evidence.build_vacancy_projection_candidates_v3(_record(), raw)
    allowlist = _allowlist(
        candidates,
        decisions={
            role_statement: ReviewedFragmentDecisionV3.ALLOW_ROLE_RESPONSIBILITY,
            company_statement: ReviewedFragmentDecisionV3.EXCLUDE_COMPANY_FACT,
        },
    )
    synthesis_input = evidence.project_vacancy_evidence_v3(_record(), raw, allowlist)
    claims = []
    for dimension in evidence.EvidenceDimension:
        fragment = next(
            item
            for item in synthesis_input.fragments
            if any(claim.dimension is dimension for claim in item.allowed_claims)
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
        evidence.validate_provider_payload_v3(
            payload,
            synthesis_input=synthesis_input,
            reviewed_allowlist=allowlist,
        )
        is None
    )
    reviewed_description_claims = {
        (entry.source_locator, entry.text_sha256): entry.decision.value
        for entry in allowlist.entries
    }
    assert (
        synthesis.validate_provider_payload_v3(
            payload,
            synthesis_input=synthesis_input,
            reviewed_description_claims=reviewed_description_claims,
        )
        is None
    )

    invented = deepcopy(payload)
    invented["claims"][0]["statement"] = "Invented product mandate."
    assert (
        evidence.validate_provider_payload_v3(
            invented,
            synthesis_input=synthesis_input,
            reviewed_allowlist=allowlist,
        )
        is evidence.EvidenceSynthesisStatus.UNSUPPORTED_CLAIM
    )

    company_fragment = next(
        item
        for item in synthesis_input.vacancy_evidence.fragments
        if item.text == company_statement
    )
    unsafe_fragment = evidence.EvidenceFragmentV1(
        fragment_id="vacancy-v3:unsafe-company",
        artifact_ref=synthesis_input.vacancy_evidence_ref,
        source_kind="vacancy",
        source_locator=company_fragment.source_locator,
        permitted_dimensions=("mandate_fit",),
        text=company_statement,
        text_sha256=hashlib.sha256(company_statement.encode()).hexdigest(),
        allowed_claims=(
            evidence.AllowedEvidenceClaimV1(
                claim_code="vacancy_description_requirements_mandate_fit_explicit",
                dimension="mandate_fit",
                status="explicit",
                statement=company_statement,
            ),
        ),
    )
    unsafe_input = synthesis_input.model_copy(deep=True)
    object.__setattr__(
        unsafe_input, "fragments", (*unsafe_input.fragments, unsafe_fragment)
    )
    unsafe = deepcopy(payload)
    mandate_claim = next(
        claim for claim in unsafe["claims"] if claim["dimension"] == "mandate_fit"
    )
    mandate_claim.update({
        "claim_code": "vacancy_description_requirements_mandate_fit_explicit",
        "statement": company_statement,
        "citations": [unsafe_fragment.fragment_id],
    })

    assert (
        evidence.validate_provider_payload_v3(
            unsafe,
            synthesis_input=unsafe_input,
            reviewed_allowlist=allowlist,
        )
        is evidence.EvidenceSynthesisStatus.UNSUPPORTED_CLAIM
    )
    assert (
        synthesis.validate_provider_payload_v3(
            unsafe,
            synthesis_input=unsafe_input,
            reviewed_description_claims=reviewed_description_claims,
        )
        is evidence.EvidenceSynthesisStatus.UNSUPPORTED_CLAIM
    )


def _pinned_raw_records() -> tuple[tuple[dict[str, object], dict[str, object]], ...]:
    manifest = json.loads(
        (CANONICAL_GATE_B_CORPUS_ROOT / "corpus-manifest.json").read_text(
            encoding="utf-8"
        )
    )
    return tuple(
        (
            record,
            json.loads(
                (CANONICAL_GATE_A_ROOT / record["raw_reference"]).read_text(
                    encoding="utf-8"
                )
            ),
        )
        for record in manifest["records"]
    )


def test_corrected_review_allowlist_projects_all_48_without_company_claims() -> None:
    """Mutation caught: global reviewed decisions are rejected as non-candidates per row."""
    allowlist = generate_reviewed_fragment_allowlist_v3(
        tuple(_pinned_raw_records()),
        corpus_sha256="b" * 64,
        gate_a_run_id="gate-a-20260816T141344Z",
        classified_at=datetime(2026, 8, 23, tzinfo=timezone.utc),
        policy=evidence.load_gate_b_evidence_policy_v3(),
    )
    raw_allowlist = json.dumps(allowlist.model_dump(mode="json"))
    review_keys = {
        (
            entry.selection_key,
            entry.vacancy_artifact_sha256,
            entry.source_locator,
            entry.text_sha256,
        )
        for entry in allowlist.entries
    }
    candidate_tuples: list[CandidateTupleV3] = []
    audits = []

    assert len(allowlist.entries) == 171
    assert Counter(entry.decision.value for entry in allowlist.entries) == {
        "allow_role_responsibility": 40,
        "allow_role_requirement": 80,
        "exclude_company_fact": 51,
    }
    assert "raw_text:" not in raw_allowlist

    for record, raw in _pinned_raw_records():
        candidates = evidence.build_vacancy_projection_candidates_v3(record, raw)
        candidate_tuples.extend(
            CandidateTupleV3(
                selection_key=candidates.selection_key,
                vacancy_artifact_sha256=candidates.vacancy_artifact_sha256,
                source_locator=candidate.source_locator,
                text_sha256=candidate.text_sha256,
                section=candidate.section,
            )
            for candidate in candidates.description_candidates
        )
        result = evidence.project_vacancy_evidence_v3(record, raw, allowlist)
        audit = evidence.audit_vacancy_projection_v3(record, raw, allowlist)
        audits.append(audit)

        assert audit.company_fact_candidates_admitted == 0
        assert audit.ambiguous_fragments_admitted == 0
        assert result.assessment_input.company_authority_status == "unavailable"
        for fragment in result.fragments:
            for claim in fragment.allowed_claims:
                if claim.status is evidence.EvidenceClaimStatus.EXPLICIT:
                    assert claim.statement == fragment.text
            if fragment.source_locator.startswith("/description#"):
                key = (
                    candidates.selection_key,
                    candidates.vacancy_artifact_sha256,
                    fragment.source_locator,
                    fragment.text_sha256,
                )
                reviewed = next(
                    entry
                    for entry in allowlist.entries
                    if (
                        entry.selection_key,
                        entry.vacancy_artifact_sha256,
                        entry.source_locator,
                        entry.text_sha256,
                    )
                    == key
                )
                assert reviewed.decision in {
                    ReviewedFragmentDecisionV3.ALLOW_ROLE_RESPONSIBILITY,
                    ReviewedFragmentDecisionV3.ALLOW_ROLE_REQUIREMENT,
                }

    candidate_keys = {
        (
            entry.selection_key,
            entry.vacancy_artifact_sha256,
            entry.source_locator,
            entry.text_sha256,
        )
        for entry in candidate_tuples
    }
    tuple_bytes = evidence.serialize_candidate_tuple_table_v3(
        records=48,
        entries=candidate_tuples,
    )

    assert len(candidate_tuples) == 171
    assert candidate_keys == review_keys
    assert hashlib.sha256(tuple_bytes).hexdigest() == CORRECTED_TUPLE_TABLE_SHA256
    assert len(audits) == 48


def test_legacy_review_hashes_remain_noncandidate_exclusion_provenance() -> None:
    """Mutation caught: historical reviewed text is substituted into the v3 allowlist."""
    legacy_hashes = {
        "9f0ef9851c55e972cdaf41f49b2e8b5cbee1e18fe6febc974d914ab55ba96dae",
        "8f058070a2bdd763d589914e272eadf1fdda76244a25a20638a768661231791c",
    }
    allowlist = evidence.load_reviewed_fragment_allowlist_v3(PINNED_ALLOWLIST_PATH)
    candidate_hashes = {
        candidate.text_sha256
        for record, raw in _pinned_raw_records()
        for candidate in evidence.build_vacancy_projection_candidates_v3(
            record, raw
        ).description_candidates
    }

    assert legacy_hashes.isdisjoint(candidate_hashes)
    assert legacy_hashes.isdisjoint({entry.text_sha256 for entry in allowlist.entries})
