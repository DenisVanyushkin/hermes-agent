from __future__ import annotations

from copy import deepcopy
import hashlib
import importlib
import importlib.abc
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from job_intel.product_search.company_evidence import load_company_evidence_bundle
from job_intel.product_search.contracts import (
    AssessmentInputV1,
    CareerProfileV2,
    ImmutableArtifactRef,
)
from job_intel.product_search.evidence_synthesis import (
    EvidenceSynthesisInputV1,
    EvidenceSynthesisStatus,
    RecordedEvidenceSynthesisProvider,
    load_evidence_synthesis_policy,
    run_evidence_synthesis,
    synthesis_input_sha256,
)
from job_intel.vacancy_understanding.semantic.runtime.llm_provider import (
    DEFAULT_MODEL_ID,
    LLM_PROMPT_VERSION,
    LLMObservationProvider,
    RecordingStore,
)


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests/product_search/fixtures/evidence_synthesis"
COMPANY_BUNDLE = (
    ROOT
    / "tests/product_search/fixtures/company_evidence/company-evidence-bundle.v1.yaml"
)
PROFILE = ROOT / "config/product_search/career_profile.v2.yaml"
POLICY = ROOT / "config/product_search/evidence_synthesis.v1.yaml"
VACANCY_ARTIFACT_SHA256 = (
    "652764df4ebc272fdc96b966cac79551df4ec2af7dcc0b365f8085374174306e"
)
GOLDEN_INPUT_HASH = "dbde1d7461fa4a22878da6af6736ab1da35720d2b596c4d467ac2668ba192601"
GOLDEN_OUTPUT_HASH = "d20c91b80ea13da0f5123dab03cc5b4b179b9b00c13d14942c2bf01e51460751"


def _ref(artifact_id: str, version: str, sha256: str) -> dict[str, str]:
    return {"artifact_id": artifact_id, "version": version, "sha256": sha256}


def _assessment() -> AssessmentInputV1:
    return AssessmentInputV1.model_validate(
        {
            "schema_version": "1.0.0",
            "assessment_id": "assessment-redacted-001",
            "references": {
                "profile_ref": _ref(
                    "career-profile-v2",
                    "2.0.0",
                    "19d63f738bf5317ef51ee676851c50e0085c970269a3b25e3df9e86c1f6d7651",
                ),
                "candidate_facts_ref": _ref(
                    "candidate-facts-structured-resume-v1.1.0",
                    "1.1.0",
                    "7219eea2fbf04c92291254f83a76b8d2d1ef53e6004ac64ff4601c726eb9fac9",
                ),
                "semantic_contract_ref": _ref(
                    "semantic-fact-contract", "1.0.0", "b" * 64
                ),
                "search_contract_ref": _ref(
                    "product-search-contract-v1",
                    "1.0.0",
                    "faf9a81564d29b3b71b67908f47e54d2c6bbbf416db19914176f410e24df4ab1",
                ),
                "policy_ref": _ref(
                    "PS-SOT-2026-08-10-v1",
                    "1.0.0",
                    "430340de2613ee733926d73ce276c93676fe64b1841bb2f68f3f9303b61fc3a8",
                ),
                "evidence_snapshot_ref": _ref(
                    "evidence-snapshot:redacted-001", "1.0.0", "e" * 64
                ),
            },
            "dimensions": {
                "feasibility": {
                    "state": "evidence_available",
                    "evidence_refs": [
                        "vacancy:remote-format",
                        "vacancy:office-format",
                    ],
                    "unknown_reasons": [],
                },
                "mandate_fit": {
                    "state": "evidence_available",
                    "evidence_refs": [
                        "vacancy:mandate-pnl",
                        "vacancy:title-platform",
                    ],
                    "unknown_reasons": [],
                },
                "company_fit": {
                    "state": "evidence_available",
                    "evidence_refs": [
                        "company:operating-model-need",
                        "vacancy:hq-new-york",
                        "vacancy:b2b-market",
                        "vacancy:crypto-product",
                    ],
                    "unknown_reasons": [],
                },
                "transferability": {
                    "state": "evidence_available",
                    "evidence_refs": ["candidate:pnl-ownership"],
                    "unknown_reasons": [],
                },
                "career_value": {
                    "state": "evidence_available",
                    "evidence_refs": ["vacancy:new-business-line"],
                    "unknown_reasons": [],
                },
                "evidence_confidence": {
                    "state": "unknown",
                    "evidence_refs": [],
                    "unknown_reasons": ["reporting_line_not_stated"],
                },
            },
        }
    )


def _input() -> EvidenceSynthesisInputV1:
    fragments = yaml.safe_load(
        (FIXTURES / "evidence-fragments.v1.yaml").read_text(encoding="utf-8")
    )["fragments"]
    profile = CareerProfileV2.model_validate(
        yaml.safe_load(PROFILE.read_text(encoding="utf-8"))
    )
    bundle_payload = yaml.safe_load(COMPANY_BUNDLE.read_text(encoding="utf-8"))
    bundle_payload["vacancy_evidence_refs"][0]["sha256"] = VACANCY_ARTIFACT_SHA256
    bundle_payload["content_sha256"] = hashlib.sha256(
        json.dumps(
            {
                key: value
                for key, value in bundle_payload.items()
                if key != "content_sha256"
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            default=lambda item: item.isoformat().replace("+00:00", "Z"),
        ).encode("utf-8")
    ).hexdigest()
    company_bundle = load_company_evidence_bundle(
        bundle_payload,
        artifacts_root=COMPANY_BUNDLE.parent / "sources",
    )
    return EvidenceSynthesisInputV1(
        schema_version="1.0.0",
        assessment_input=_assessment(),
        career_profile=profile,
        company_evidence_bundle=company_bundle,
        fragments=fragments,
        vacancy_artifacts_root=FIXTURES / "vacancy-artifacts",
    )


def _golden_output() -> dict[str, Any]:
    return json.loads(
        (FIXTURES / "golden-provider-output.v1.json").read_text(encoding="utf-8")
    )


class _ArbitraryProvider:
    provider_id = "semantic-contract-recording"
    provider_version = "semantic-runtime-recording/1.0"
    model_id = "fixture-model-1"
    prompt_version = "product-search-evidence-synthesis-1.0.0"

    def __init__(self) -> None:
        self.payload = _golden_output()
        self.last_call_metadata = {
            "latency_ms": 37,
            "cost_usd": "0.000321",
        }

    def synthesize_evidence(self, *, input_payload: dict[str, Any]) -> object:
        return deepcopy(self.payload)


def _adapter(
    store: RecordingStore,
    *,
    policy=None,
) -> RecordedEvidenceSynthesisProvider:
    policy = policy or load_evidence_synthesis_policy(POLICY)
    semantic_provider = LLMObservationProvider(
        store=store,
        mode="replay",
        model_id=DEFAULT_MODEL_ID,
        prompt_version=LLM_PROMPT_VERSION,
    )
    return RecordedEvidenceSynthesisProvider(
        semantic_provider=semantic_provider,
        policy=policy,
    )


def _run(
    payload: object | None = None,
    *,
    synthesis_input: EvidenceSynthesisInputV1 | None = None,
    recorded_error: str | None = None,
):
    synthesis_input = synthesis_input or _input()
    policy = load_evidence_synthesis_policy(POLICY)
    with tempfile.TemporaryDirectory() as temp_dir:
        store = RecordingStore(temp_dir)
        provider = _adapter(store, policy=policy)
        input_hash = synthesis_input_sha256(
            synthesis_input.provider_payload(), provider=provider
        )
        response_payload = _golden_output() if payload is None else payload
        raw = json.dumps(
            response_payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        store.save(
            {
                "recording_format_version": "1.0",
                "input_hash": input_hash,
                "provider_id": provider.provider_id,
                "provider_version": provider.provider_version,
                "model_id": provider.model_id,
                "semantic_prompt_version": provider.semantic_prompt_version,
                "prompt_version": provider.prompt_version,
                "raw_response_text": raw if recorded_error is None else "",
                "response_hash": hashlib.sha256(
                    (raw if recorded_error is None else "").encode("utf-8")
                ).hexdigest(),
                "latency_ms": 37,
                "cost_usd": "0.000321",
                "error": recorded_error,
            }
        )
        return run_evidence_synthesis(
            synthesis_input=synthesis_input,
            provider=provider,
            policy=policy,
        )


def _input_with_remote_confirmation() -> EvidenceSynthesisInputV1:
    payload = _input().model_dump(mode="json")
    confirmation = deepcopy(payload["fragments"][0])
    confirmation["fragment_id"] = "vacancy:remote-format-confirmation"
    confirmation["source_locator"] = "vacancy:work-format-confirmation"
    payload["fragments"].append(confirmation)
    payload["assessment_input"]["dimensions"]["feasibility"][
        "evidence_refs"
    ].append(confirmation["fragment_id"])
    return EvidenceSynthesisInputV1.model_validate(payload)


def test_six_dimension_result_is_cited_bounded_and_audit_complete() -> None:
    """Mutation caught: a provider claim bypasses evidence or audit metadata."""
    result = _run()

    assert result.status is EvidenceSynthesisStatus.DELIVERABLE
    assert result.deliverable is True
    assert {claim.dimension.value for claim in result.claims} == {
        "feasibility",
        "mandate_fit",
        "company_fit",
        "transferability",
        "career_value",
        "evidence_confidence",
    }
    assert {claim.status.value for claim in result.claims} == {
        "explicit",
        "inferred",
        "unknown",
    }
    assert result.conflicts[0].claim_ids == (
        "claim-feasibility-remote",
        "claim-feasibility-office",
    )
    assert [question.question_code for question in result.question_candidates] == [
        "clarify_reporting_line",
        "clarify_work_format",
    ]
    assert result.metadata.model_dump(mode="json") == {
        "provider_id": "llm-observation",
        "provider_version": "product-search-evidence-replay/1.0",
        "model_id": "openai/gpt-5-mini",
        "semantic_prompt_version": "llm-obs-1.0.0",
        "prompt_version": "product-search-evidence-synthesis-1.0.0",
        "schema_version": "1.0.0",
        "latency_ms": 37,
        "cost_usd": "0.000321",
        "input_sha256": GOLDEN_INPUT_HASH,
        "output_sha256": GOLDEN_OUTPUT_HASH,
    }


@pytest.mark.parametrize(
    "forbidden_payload",
    [
        {"system_verdict": "Priority"},
        {"analysis": {"SystemVerdict": "Priority"}},
        {"analysis": {"selection_mode": "Core"}},
        {"wrapper": [{"SelectionMode": "Core"}]},
        {"wrapper": [{"deliveryInstruction": "send"}]},
        {"wrapper": [{"hardGateOutcome": "pass"}]},
        {"wrapper": [{"stage4": {"urgency": "high"}}]},
        {"wrapper": [{"delivery_instruction": "send to Slack"}]},
        {"wrapper": {"stage_4": {"urgency": "high"}}},
        {"crm": {"user_decision": "Pursue"}},
        {"company_transition": {"company_action": "promote"}},
        {"hard_gate": {"outcome": "pass"}},
    ],
)
def test_normative_fields_are_rejected_recursively(
    forbidden_payload: dict[str, Any],
) -> None:
    """Mutation caught: recursive provider authority is checked only at top level."""
    payload = _golden_output()
    payload["provider_commentary"] = forbidden_payload

    result = _run(payload)

    assert result.status is EvidenceSynthesisStatus.FORBIDDEN_FIELD
    assert result.deliverable is False
    assert result.claims == ()


def test_closed_schema_rejects_unknown_fields_at_each_result_level() -> None:
    """Mutation caught: schema drift becomes accepted evidence."""
    for mutate in (
        lambda payload: payload.update({"commentary": "looks good"}),
        lambda payload: payload["claims"][0].update({"confidence": 0.99}),
        lambda payload: payload["conflicts"][0].update({"severity": "high"}),
        lambda payload: payload["question_candidates"][0].update(
            {"recommended_answer": "VP Product"}
        ),
    ):
        payload = _golden_output()
        mutate(payload)
        result = _run(payload)
        assert result.status is EvidenceSynthesisStatus.INVALID_SCHEMA
        assert result.deliverable is False


def test_forbidden_alias_matching_does_not_use_broad_substrings() -> None:
    """Mutation caught: substring matching misclassifies descriptive extra fields."""
    payload = _golden_output()
    payload["provider_commentary"] = {
        "SystemVerdictExplanation": "descriptive extra, still schema-invalid"
    }

    result = _run(payload)

    assert result.status is EvidenceSynthesisStatus.INVALID_SCHEMA
    assert result.deliverable is False


def test_missing_and_foreign_dimension_citations_fail_closed() -> None:
    """Mutation caught: a missing or cross-dimension citation supports a claim."""
    missing = _golden_output()
    missing["claims"][0]["citations"] = ["vacancy:does-not-exist"]
    assert _run(missing).status is EvidenceSynthesisStatus.MISSING_CITATION

    foreign = _golden_output()
    foreign["claims"][0]["citations"] = ["vacancy:hq-new-york"]
    assert _run(foreign).status is EvidenceSynthesisStatus.FOREIGN_CITATION


def test_every_claim_citation_must_authorize_the_claim() -> None:
    """Mutation caught: existential support admits an irrelevant extra citation."""
    payload = _golden_output()
    payload["claims"][0]["citations"] = [
        "vacancy:remote-format",
        "vacancy:office-format",
    ]

    result = _run(payload)

    assert result.status is EvidenceSynthesisStatus.UNSUPPORTED_CLAIM
    assert result.deliverable is False


def test_conflict_must_cite_support_for_each_referenced_claim() -> None:
    """Mutation caught: aggregate citation union omits support for one conflict claim."""
    synthesis_input = _input_with_remote_confirmation()
    payload = _golden_output()
    payload["claims"][0]["citations"] = [
        "vacancy:remote-format",
        "vacancy:remote-format-confirmation",
    ]
    payload["conflicts"][0]["citations"] = [
        "vacancy:remote-format",
        "vacancy:remote-format-confirmation",
    ]

    result = _run(payload, synthesis_input=synthesis_input)

    assert result.status is EvidenceSynthesisStatus.UNSUPPORTED_CLAIM
    assert result.deliverable is False


def test_multi_citation_claim_and_conflict_are_valid_when_each_is_supported() -> None:
    """Mutation caught: universal checks reject a fully supported multi-citation result."""
    synthesis_input = _input_with_remote_confirmation()
    payload = _golden_output()
    payload["claims"][0]["citations"] = [
        "vacancy:remote-format",
        "vacancy:remote-format-confirmation",
    ]
    payload["conflicts"][0]["citations"] = [
        "vacancy:remote-format-confirmation",
        "vacancy:office-format",
    ]

    result = _run(payload, synthesis_input=synthesis_input)

    assert result.status is EvidenceSynthesisStatus.DELIVERABLE
    assert result.deliverable is True


def test_adversarial_fact_substitutions_never_become_deliverable() -> None:
    """Mutation caught: title/company/industry cues or unknown broaden evidence."""
    cases = yaml.safe_load(
        (FIXTURES / "adversarial-cases.v1.yaml").read_text(encoding="utf-8")
    )["cases"]
    for case in cases:
        payload = _golden_output()
        index = next(
            i
            for i, claim in enumerate(payload["claims"])
            if claim["claim_id"] == case["target_claim_id"]
        )
        payload["claims"][index] = case["replacement"]
        result = _run(payload)
        assert result.status.value == case["expected_status"], case["name"]
        assert result.deliverable is False, case["name"]


def test_six_dimensions_and_question_limits_are_enforced() -> None:
    """Mutation caught: incomplete or unbounded synthesis becomes deliverable."""
    incomplete = _golden_output()
    incomplete["claims"] = [
        claim for claim in incomplete["claims"] if claim["dimension"] != "company_fit"
    ]
    assert _run(incomplete).status is EvidenceSynthesisStatus.INCOMPLETE_DIMENSIONS

    unbounded = _golden_output()
    template = unbounded["question_candidates"][0]
    unbounded["question_candidates"] = [
        {**template, "question_id": f"question-{index}"} for index in range(7)
    ]
    assert _run(unbounded).status is EvidenceSynthesisStatus.BOUNDS_EXCEEDED


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        ("timeout: provider timed out", EvidenceSynthesisStatus.TIMEOUT),
        ("provider_outage: unavailable", EvidenceSynthesisStatus.PROVIDER_OUTAGE),
        ("refusal: policy refusal", EvidenceSynthesisStatus.REFUSAL),
        (
            "schema_invalid: structured output mismatch",
            EvidenceSynthesisStatus.INVALID_SCHEMA,
        ),
    ],
)
def test_provider_failures_are_explicit_non_deliverable_results(
    error: str,
    expected: EvidenceSynthesisStatus,
) -> None:
    """Mutation caught: provider failure falls back to a legacy evaluator."""
    result = _run(recorded_error=error)
    assert result.status is expected
    assert result.deliverable is False
    assert result.claims == ()
    assert result.conflicts == ()
    assert result.question_candidates == ()
    assert len(result.metadata.input_sha256) == 64
    assert len(result.metadata.output_sha256) == 64


def test_task8_and_task9_references_and_fragment_hashes_remain_authoritative() -> None:
    """Mutation caught: synthesis weakens upstream hashes, identity, or redaction."""
    synthesis_input = _input()
    assert synthesis_input.assessment_input.references.candidate_facts_ref == (
        synthesis_input.career_profile.authorities.candidate_facts_ref
    )
    assert synthesis_input.company_evidence_bundle.content_sha256 == (
        "dbcd9c0ec0fb621b6a949fca9d3df2ea2b3b925ea1a2b84908b4602e0f7db41e"
    )
    assert synthesis_input.company_evidence_bundle.vacancy_evidence_refs == (
        ImmutableArtifactRef(
            artifact_id="vacancy-evidence:redacted-001",
            version="1.0.0",
            sha256=VACANCY_ARTIFACT_SHA256,
        ),
    )

    payload = synthesis_input.model_dump(mode="json")
    payload["fragments"][0]["text"] = "tampered text"
    with pytest.raises(ValidationError, match="text_sha256"):
        EvidenceSynthesisInputV1.model_validate(payload)

    payload = synthesis_input.model_dump(mode="json")
    payload["fragments"][4]["source_locator"] = "evidence:another-company:need"
    with pytest.raises(ValidationError, match="company evidence"):
        EvidenceSynthesisInputV1.model_validate(payload)

    payload = synthesis_input.model_dump(mode="json")
    payload["fragments"][5]["text"] = "Invented global scope."
    payload["fragments"][5]["text_sha256"] = hashlib.sha256(
        b"Invented global scope."
    ).hexdigest()
    payload["fragments"][5]["allowed_claims"][0]["statement"] = (
        "Invented global scope."
    )
    with pytest.raises(ValidationError, match="candidate profile"):
        EvidenceSynthesisInputV1.model_validate(payload)


def test_recomputed_hash_cannot_admit_invented_vacancy_text() -> None:
    """Mutation caught: caller rewrites vacancy text and recomputes local hashes."""
    payload = _input().model_dump(mode="json")
    invented = "Role guarantees global P&L ownership."
    payload["fragments"][0]["text"] = invented
    payload["fragments"][0]["text_sha256"] = hashlib.sha256(
        invented.encode("utf-8")
    ).hexdigest()
    payload["fragments"][0]["allowed_claims"][0]["statement"] = invented

    with pytest.raises(ValidationError, match="vacancy evidence artifact"):
        EvidenceSynthesisInputV1.model_validate(payload)


def test_golden_recording_replays_offline_with_exact_hashes(tmp_path: Path) -> None:
    """Mutation caught: replay performs network I/O or accepts altered recording bytes."""
    raw = json.dumps(
        _golden_output(), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    policy = load_evidence_synthesis_policy(POLICY)
    store = RecordingStore(tmp_path)
    provider = _adapter(store, policy=policy)
    store.save(
        {
            "recording_format_version": "1.0",
            "input_hash": GOLDEN_INPUT_HASH,
            "provider_id": provider.provider_id,
            "provider_version": provider.provider_version,
            "model_id": provider.model_id,
            "semantic_prompt_version": provider.semantic_prompt_version,
            "prompt_version": provider.prompt_version,
            "raw_response_text": raw,
            "response_hash": GOLDEN_OUTPUT_HASH,
            "latency_ms": 37,
            "cost_usd": "0.000321",
            "error": None,
        }
    )
    result = run_evidence_synthesis(
        synthesis_input=_input(),
        provider=provider,
        policy=policy,
    )

    assert result.status is EvidenceSynthesisStatus.DELIVERABLE
    assert result.metadata.input_sha256 == GOLDEN_INPUT_HASH
    assert result.metadata.output_sha256 == GOLDEN_OUTPUT_HASH


def test_recorded_refusal_keeps_its_explicit_failure_status(tmp_path: Path) -> None:
    """Mutation caught: replay collapses a recorded refusal into provider_error."""
    policy = load_evidence_synthesis_policy(POLICY)
    store = RecordingStore(tmp_path)
    provider = _adapter(store, policy=policy)
    store.save(
        {
            "recording_format_version": "1.0",
            "input_hash": GOLDEN_INPUT_HASH,
            "provider_id": provider.provider_id,
            "provider_version": provider.provider_version,
            "model_id": provider.model_id,
            "semantic_prompt_version": provider.semantic_prompt_version,
            "prompt_version": provider.prompt_version,
            "raw_response_text": "",
            "response_hash": hashlib.sha256(b"").hexdigest(),
            "latency_ms": 41,
            "cost_usd": "0.000111",
            "error": "refusal: provider declined the request",
        }
    )
    result = run_evidence_synthesis(
        synthesis_input=_input(),
        provider=provider,
        policy=policy,
    )

    assert result.status is EvidenceSynthesisStatus.REFUSAL
    assert result.deliverable is False
    assert result.metadata.provider_id == "llm-observation"
    assert result.metadata.model_id == "openai/gpt-5-mini"
    assert result.metadata.latency_ms == 41
    assert result.metadata.cost_usd == "0.000111"
    assert result.metadata.input_sha256 == GOLDEN_INPUT_HASH
    assert result.metadata.output_sha256 == hashlib.sha256(b"").hexdigest()


def test_arbitrary_provider_object_is_not_an_authorized_boundary() -> None:
    """Mutation caught: a duck-typed callable spoofs governed provider metadata."""
    with pytest.raises(TypeError, match="governed Semantic provider"):
        run_evidence_synthesis(
            synthesis_input=_input(),
            provider=_ArbitraryProvider(),  # type: ignore[arg-type]
            policy=load_evidence_synthesis_policy(POLICY),
        )


def test_live_semantic_provider_cannot_enter_replay_adapter() -> None:
    """Mutation caught: governed type check accidentally admits live transport mode."""
    policy = load_evidence_synthesis_policy(POLICY)
    live_provider = LLMObservationProvider(
        store=RecordingStore(FIXTURES / "not-used"),
        mode="record",
        model_id=DEFAULT_MODEL_ID,
        transport=object(),
        prompt_version=LLM_PROMPT_VERSION,
    )
    with pytest.raises(ValueError, match="only offline Semantic replay"):
        RecordedEvidenceSynthesisProvider(
            semantic_provider=live_provider,
            policy=policy,
        )


def test_policy_is_closed_and_pins_six_dimensions_and_provider_versions() -> None:
    """Mutation caught: config expands authority or silently changes replay identity."""
    policy = load_evidence_synthesis_policy(POLICY)
    assert tuple(dimension.value for dimension in policy.dimensions) == (
        "feasibility",
        "mandate_fit",
        "company_fit",
        "transferability",
        "career_value",
        "evidence_confidence",
    )
    assert policy.provider_runtime == "llm-observation"
    assert policy.provider_adapter_version == "product-search-evidence-replay/1.0"
    assert policy.semantic_prompt_version == "llm-obs-1.0.0"
    assert policy.model_id == "openai/gpt-5-mini"
    assert policy.prompt_version == "product-search-evidence-synthesis-1.0.0"
    assert policy.output_schema_version == "1.0.0"
    assert policy.max_questions_total == 6

    payload = yaml.safe_load(POLICY.read_text(encoding="utf-8"))
    payload["provider_authority"] = "may_decide"
    with pytest.raises(ValidationError, match="extra_forbidden"):
        load_evidence_synthesis_policy(payload)


def test_import_boundary_has_no_second_llm_or_network_client() -> None:
    """Mutation caught: Task 10 imports a new client instead of the semantic seam."""
    target = ROOT / "job_intel/product_search/evidence_synthesis.py"
    script = r'''
import importlib.abc
import importlib.util
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
target = pathlib.Path(sys.argv[2])
blocked = ("openai", "requests", "httpx", "agent.auxiliary_client")

class Blocker(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in blocked or fullname.startswith(tuple(name + "." for name in blocked)):
            raise RuntimeError("forbidden provider client imported: " + fullname)
        return None

sys.path.insert(0, str(root))
sys.meta_path.insert(0, Blocker())
spec = importlib.util.spec_from_file_location(
    "job_intel.product_search.evidence_synthesis", target
)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
for forbidden in blocked:
    if forbidden in sys.modules:
        raise RuntimeError("forbidden provider client cached: " + forbidden)
print("provider-boundary-ok")
'''
    result = subprocess.run(
        [sys.executable, "-I", "-c", script, str(ROOT), str(target)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "provider-boundary-ok"
