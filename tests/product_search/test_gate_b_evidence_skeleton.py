from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import socket
import sqlite3

import pytest

from job_intel.product_search import gate_b_evidence_v3 as evidence
from job_intel.product_search.decision_v2 import (
    canonical_decision_bytes,
    load_decision_policy,
    run_decision_v2,
)
from job_intel.product_search.evidence_synthesis import (
    EvidenceClaimV1,
    EvidenceSynthesisMetadataV1,
    EvidenceSynthesisResultV1,
    EvidenceSynthesisStatus,
)
from job_intel.product_search.gate_b_benchmark_policy_v3 import (
    load_gate_b_benchmark_policy_v3,
)
from job_intel.product_search.gate_b_evidence_v3 import (
    ReviewedFragmentAllowlistV3,
    ReviewedFragmentDecisionV3,
    ReviewedFragmentEntryV3,
)
from job_intel.product_search.gate_b_evidence_runner_v1 import (
    AdjudicationSet,
    AdjudicationVerdict,
    ForegroundDispatchLedger,
    AuthorityIdentity,
    DecisionEvidenceStore,
    EvidenceManifest,
    EvidenceManifestRow,
    GateDecisionKind,
    GateEvaluator,
    Limits,
    MeasurementReport,
    NoDurableAccounting,
    RecordingStore,
    RuntimeIdentity,
    TerminalOutcome,
    run_one_row,
)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def _record() -> dict[str, str]:
    return {"selection_key": "a" * 64}


def _raw() -> dict[str, str]:
    return {
        "title": "Head of Product",
        "location": "Almaty",
        "description": (
            "<h2>Responsibilities</h2>"
            "<p>Lead quarterly roadmap planning with engineering and design.</p>"
        ),
    }


def test_foreground_ledger_requires_explicit_budget_reserver() -> None:
    manifest = _manifest(
        input_sha256=_sha256_bytes(b"request"),
        projection_sha256="2" * 64,
        raw_sha256="3" * 64,
    )
    with pytest.raises(TypeError, match="committed_budget_reserver"):
        ForegroundDispatchLedger(manifest)


def _allowlist(candidates: object) -> ReviewedFragmentAllowlistV3:
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
                source_locator=item.source_locator,
                text_sha256=item.text_sha256,
                decision=ReviewedFragmentDecisionV3.ALLOW_ROLE_RESPONSIBILITY,
                reviewer_role="independent_gate_b_evidence_reviewer",
                reviewed_at=datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc),
            )
            for item in candidates.description_candidates
        ),
    )


def _provider_payload(synthesis_input: object) -> dict[str, object]:
    claims: list[dict[str, object]] = []
    for dimension in evidence.EvidenceDimension:
        fragment = next(
            item
            for item in synthesis_input.fragments
            if any(claim.dimension is dimension for claim in item.allowed_claims)
        )
        allowed = next(
            claim for claim in fragment.allowed_claims if claim.dimension is dimension
        )
        claims.append(
            {
                "claim_id": f"claim:{dimension.value}",
                "dimension": dimension.value,
                "status": allowed.status.value,
                "claim_code": allowed.claim_code,
                "statement": allowed.statement,
                "citations": [fragment.fragment_id],
            }
        )
    return {
        "schema_version": "2.0.0",
        "claims": claims,
        "conflicts": [],
        "question_candidates": [],
    }


def _manifest(*, input_sha256: str, projection_sha256: str, raw_sha256: str) -> EvidenceManifest:
    rows = tuple(
        EvidenceManifestRow(
            ordinal=ordinal,
            corpus_key=("a" * 63 + "b") if ordinal == 0 else f"{ordinal:064x}",
            raw_sha256=raw_sha256 if ordinal == 0 else f"{ordinal + 100:064x}",
            input_sha256=input_sha256 if ordinal == 0 else f"{ordinal + 200:064x}",
            projection_sha256=(
                projection_sha256 if ordinal == 0 else f"{ordinal + 300:064x}"
            ),
        )
        for ordinal in range(48)
    )
    payload: dict[str, object] = {
        "schema_version": "gate-b-evidence-manifest-v1",
        "run_id": "gate-b-evidence-v1-0123456789abcdef",
        "created_at": "2026-08-21T12:00:00Z",
        "decision_clock": "2026-08-21T12:00:00Z",
        "benchmark_kind": "gate_b_description_evidence",
        "row_count": 48,
        "rows": [row.model_dump(mode="json") for row in rows],
        "runtime": RuntimeIdentity(
            artifact_sha256="1" * 64,
            artifact_tree_sha256="0" * 64,
            shim_sha256="0" * 64,
            interpreter_sha256="2" * 64,
            stdlib_inventory_sha256="3" * 64,
            installed_distributions_sha256="4" * 64,
            installed_files_sha256="5" * 64,
            sys_path_sha256="6" * 64,
            native_extensions_sha256="7" * 64,
            shared_libraries_sha256="8" * 64,
        ).model_dump(mode="json"),
        "authorities": AuthorityIdentity(
            model_sha256="9" * 64,
            prompt_sha256="a" * 64,
            response_schema_sha256="b" * 64,
            profile_sha256="c" * 64,
            policy_sha256="d" * 64,
            decision_v2_sha256="e" * 64,
            pricing_sha256="f" * 64,
            source_authority_sha256s={"gate_a": "1" * 64},
        ).model_dump(mode="json"),
        "limits": Limits(
            ordered_call_cap=48,
            per_call_maximum_usd=Decimal("0.01"),
            aggregate_maximum_usd=Decimal("0.48"),
        ).model_dump(mode="json"),
    }
    identity_body = dict(payload)
    identity_body.pop("created_at")
    payload["manifest_sha256"] = _sha256_bytes(_canonical_bytes(identity_body))
    return EvidenceManifest.model_validate(payload)


def test_foreground_ledger_refuses_the_forty_ninth_dispatch() -> None:
    manifest = _manifest(
        input_sha256=_sha256_bytes(b"request"),
        projection_sha256="2" * 64,
        raw_sha256="3" * 64,
    )
    ledger = ForegroundDispatchLedger(
        manifest, committed_budget_reserver=NoDurableAccounting()
    )
    for ordinal in range(48):
        receipt = ledger.append_pre_dispatch(manifest.row_ref(ordinal))
        ledger.commit_terminal(
            receipt,
            TerminalOutcome.SUCCESS,
            recording_sha256="a" * 64,
            measured_cost_usd=Decimal("0"),
            conservative_cost_usd=Decimal("0.01"),
        )

    with pytest.raises(ValueError, match="call_cap_exhausted"):
        ledger.append_pre_dispatch(manifest.row_ref(0))


def test_foreground_ledger_refuses_dispatch_past_spend_ceiling() -> None:
    manifest = _manifest(
        input_sha256=_sha256_bytes(b"request"),
        projection_sha256="2" * 64,
        raw_sha256="3" * 64,
    )
    object.__setattr__(manifest.limits, "aggregate_maximum_usd", Decimal("0.47"))
    ledger = ForegroundDispatchLedger(
        manifest, committed_budget_reserver=NoDurableAccounting()
    )
    for ordinal in range(47):
        ledger.append_pre_dispatch(manifest.row_ref(ordinal))

    with pytest.raises(ValueError, match="spend_cap_exhausted"):
        ledger.append_pre_dispatch(manifest.row_ref(47))


def _decision_result(payload: dict[str, object], input_sha256: str) -> object:
    claims = tuple(EvidenceClaimV1.model_validate(item) for item in payload["claims"])
    output_payload = {
        "schema_version": "1.0.0",
        "claims": [item.model_dump(mode="json") for item in claims],
        "conflicts": [],
        "question_candidates": [],
    }
    output_sha256 = _sha256_bytes(_canonical_bytes(output_payload))
    synthesis = EvidenceSynthesisResultV1(
        schema_version="1.0.0",
        status=EvidenceSynthesisStatus.DELIVERABLE,
        deliverable=True,
        claims=claims,
        conflicts=(),
        question_candidates=(),
        failure_reason=None,
        metadata=EvidenceSynthesisMetadataV1(
            provider_id="llm-observation",
            provider_version="product-search-evidence-replay/1.0",
            model_id="openai/gpt-5-mini",
            semantic_prompt_version="llm-obs-1.0.0",
            prompt_version="product-search-evidence-synthesis-1.0.0",
            schema_version="1.0.0",
            latency_ms=0,
            cost_usd="0",
            input_sha256=input_sha256,
            output_sha256=output_sha256,
        ),
    )
    # The existing Decision v2 characterization fixture supplies a complete,
    # real DecisionRequestV2 around this deterministic provider result.
    from tests.product_search.test_decision_v2 import _references, _request

    return _request(
        synthesis=synthesis,
        references=_references(
            provider_input_sha256=input_sha256,
            provider_output_sha256=output_sha256,
        ),
    )


def test_one_row_skeleton_is_offline_replayable_and_never_opens_live_db(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden_sqlite(*args: object, **kwargs: object) -> None:
        raise AssertionError("live sqlite must not be opened")

    def forbidden_socket(*args: object, **kwargs: object) -> None:
        raise AssertionError("network socket must not be opened")

    monkeypatch.setattr(sqlite3, "connect", forbidden_sqlite)
    monkeypatch.setattr(socket, "socket", forbidden_socket)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    record = _record()
    raw = _raw()
    candidates = evidence.build_vacancy_projection_candidates_v3(record, raw)
    allowlist = _allowlist(candidates)
    projected = evidence.project_vacancy_evidence_v3(record, raw, allowlist)
    input_sha256 = _sha256_bytes(_canonical_bytes(projected.provider_payload()))
    projection_sha256 = _sha256_bytes(_canonical_bytes(projected.model_dump(mode="json")))
    manifest = _manifest(
        input_sha256=input_sha256,
        projection_sha256=projection_sha256,
        raw_sha256=candidates.vacancy_artifact_sha256,
    )

    ledger = ForegroundDispatchLedger(
        manifest, committed_budget_reserver=NoDurableAccounting()
    )
    recordings = RecordingStore(tmp_path / "recordings")
    decision_evidence = DecisionEvidenceStore(tmp_path / "decisions")
    calls: list[dict[str, object]] = []

    class FakeGovernedProvider:
        provider_record_sha256 = "a" * 64

        def dispatch(self, payload: dict[str, object]) -> dict[str, object]:
            assert ledger.state(0).value == "dispatched"
            calls.append(payload)
            return _provider_payload(projected)

    result = run_one_row(
        manifest=manifest,
        ordinal=0,
        record=record,
        raw=raw,
        reviewed_allowlist=allowlist,
        provider=FakeGovernedProvider(),
        ledger=ledger,
        recordings=recordings,
        decision_evidence=decision_evidence,
        decision_request_factory=lambda payload, row: _decision_result(
            payload, row.input_sha256
        ),
        decision_policy=load_decision_policy(),
    )

    assert len(calls) == 1
    assert result.validation_status is None
    assert result.decision.status.value == "assessed"
    assert result.decision_ref.manifest_ref == result.manifest_ref
    assert result.decision_ref.decision_sha256 == _sha256_bytes(result.decision_bytes)
    assert decision_evidence.bytes_for(result.decision_ref) == result.decision_bytes
    assert ledger.state(0).value == "success"

    replay = recordings.replay(result.recording_ref, manifest, ledger.entries()[0])
    assert replay.manifest_ref == result.manifest_ref
    assert replay.request_bytes == _canonical_bytes(projected.provider_payload())
    assert replay.response_bytes == _canonical_bytes(_provider_payload(projected))
    assert result.recording_ref.recording_sha256 == _sha256_bytes(
        result.recording_bytes
    )
    replayed_request = _decision_result(
        json.loads(replay.response_bytes), replay.manifest_ref.input_sha256
    )
    replayed_decision = run_decision_v2(
        replayed_request,
        policy=load_decision_policy(),
    )
    assert canonical_decision_bytes(replayed_decision) == canonical_decision_bytes(
        result.decision
    )


def test_gate_evaluator_distinguishes_complete_negative_from_incomplete() -> None:
    manifest = _manifest(
        input_sha256="1" * 64,
        projection_sha256="2" * 64,
        raw_sha256="3" * 64,
    )
    complete = MeasurementReport(
        run_id=manifest.run_id,
        manifest_sha256=manifest.manifest_sha256,
        expected_row_count=48,
        observed_row_count=48,
        deliverable_count=42,
        terminal_unknown_count=0,
        adjudicated_count=48,
        adjudication_denominator=48,
        adjudicated_correct=48,
        recording_sha256s=tuple(f"{index:064x}" for index in range(48)),
        decision_sha256s=tuple(f"{index + 100:064x}" for index in range(48)),
    )
    adjudication = AdjudicationSet.from_verdicts(
        tuple(
            AdjudicationVerdict(
                manifest_ref=manifest.row_ref(index),
                decision_sha256=f"{index + 100:064x}",
                correct=True,
            )
            for index in range(48)
        )
    )
    decision = GateEvaluator.evaluate(
        manifest, complete, adjudication, policy=load_gate_b_benchmark_policy_v3()
    )
    assert decision.measurement_status == "complete"
    assert decision.decision is GateDecisionKind.REFUSE
    assert decision.violated_rules == ("minimum_deliverable_results",)
