from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import job_intel.vacancy_understanding.semantic.runtime.llm_provider as runtime

from job_intel.vacancy_understanding.semantic.runtime.llm_provider import (
    DEFAULT_MODEL_ID,
    GovernedPricingSchedule,
    GovernedStructuredRequest,
    LLMObservationProvider,
    LLMProviderError,
    RecordingStore,
    _issue_structured_call_capability,
)


class _Completions:
    def __init__(self, response: object | None = None, error: Exception | None = None):
        self.response = response
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> object:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.response


class _Transport:
    def __init__(self, completions: _Completions):
        self.chat = SimpleNamespace(completions=completions)


def _pricing() -> GovernedPricingSchedule:
    return GovernedPricingSchedule(
        version="openrouter-openai-gpt5-mini-2026-08-17",
        model_id=DEFAULT_MODEL_ID,
        input_usd_per_mtok=Decimal("0.25"),
        output_usd_per_mtok=Decimal("2.00"),
        max_input_tokens=24_000,
        max_output_tokens=2_000,
    )


def _request() -> GovernedStructuredRequest:
    return GovernedStructuredRequest(
        input_hash="1" * 64,
        system_prompt="bounded fixture prompt",
        user_payload={"redacted": "fixture"},
        schema_name="fixture_schema",
        response_schema={
            "type": "object",
            "properties": {"ok": {"type": "boolean"}},
            "required": ["ok"],
            "additionalProperties": False,
        },
        governance_identity={"run_identity_sha256": "2" * 64},
        forbidden_markers=("hermes-private://", "private resume"),
    )


def _capability(events: list[tuple]) -> object:
    def reserve(input_hash: str, amount: Decimal) -> str:
        events.append(("reserve", input_hash, str(amount)))
        return "reservation-1"

    def reconcile(
        reservation_id: str, actual_cost: Decimal, outcome: str
    ) -> None:
        events.append(("reconcile", reservation_id, str(actual_cost), outcome))

    return _issue_structured_call_capability(
        run_identity_sha256="2" * 64,
        pricing=_pricing(),
        exact_call_cap=48,
        exact_spend_cap_usd=Decimal("0.48"),
        metadata_seal_key=b"fixture-owner-bound-seal-key",
        reserve=reserve,
        reconcile=reconcile,
    )


def test_public_structured_call_owns_transport_usage_cost_and_atomic_record(
    tmp_path: Path,
) -> None:
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content='{"ok":true}', refusal=None))],
        usage=SimpleNamespace(
            prompt_tokens=1_000, completion_tokens=500, total_tokens=1_500
        ),
        model=DEFAULT_MODEL_ID,
    )
    completions = _Completions(response=response)
    provider = LLMObservationProvider(
        store=RecordingStore(tmp_path),
        mode="record",
        model_id=DEFAULT_MODEL_ID,
        transport=_Transport(completions),
    )
    events: list[tuple] = []

    result = provider.governed_structured_call(
        request=_request(), capability=_capability(events)
    )

    assert json.loads(result.raw_response_text) == {"ok": True}
    assert result.usage == {
        "prompt_tokens": 1_000,
        "completion_tokens": 500,
        "total_tokens": 1_500,
    }
    assert result.cost_usd == Decimal("0.001250")
    assert completions.calls[0]["max_tokens"] == 2_000
    assert events == [
        ("reserve", "1" * 64, "0.010000"),
        ("reconcile", "reservation-1", "0.001250", "success"),
    ]
    recording = provider.store.load("1" * 64)
    assert recording["metadata_sha256"]
    assert recording["metadata_hmac_sha256"]
    assert recording["pricing"] == _pricing().as_record()
    assert recording["cost_usd"] == "0.001250"
    assert (tmp_path.stat().st_mode & 0o777) == 0o700
    assert (provider.store.path_for("1" * 64).stat().st_mode & 0o777) == 0o600


@pytest.mark.parametrize(
    "usage",
    [
        {"prompt_tokens": True, "completion_tokens": 1, "total_tokens": 2},
        {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 3},
        {"prompt_tokens": 24_001, "completion_tokens": 1, "total_tokens": 24_002},
        {"prompt_tokens": 1, "completion_tokens": 2_001, "total_tokens": 2_002},
    ],
)
def test_structured_call_rejects_inconsistent_or_out_of_bounds_usage(
    tmp_path: Path, usage: dict[str, object]
) -> None:
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content='{"ok":true}', refusal=None))],
        usage=usage,
        model=DEFAULT_MODEL_ID,
    )
    provider = LLMObservationProvider(
        store=RecordingStore(tmp_path),
        mode="record",
        transport=_Transport(_Completions(response=response)),
    )
    with pytest.raises(LLMProviderError, match="usage_invalid"):
        provider.governed_structured_call(
            request=_request(), capability=_capability([])
        )


def test_structured_call_persists_only_sanitized_failure_codes(
    tmp_path: Path,
) -> None:
    secret = (
        "Bearer fake-secret https://private.invalid vacancy raw text "
        "hermes-private://candidate private resume"
    )
    provider = LLMObservationProvider(
        store=RecordingStore(tmp_path),
        mode="record",
        transport=_Transport(_Completions(error=RuntimeError(secret))),
    )
    events: list[tuple] = []
    with pytest.raises(LLMProviderError, match="transport_error"):
        provider.governed_structured_call(
            request=_request(), capability=_capability(events)
        )
    assert events == [
        ("reserve", "1" * 64, "0.010000"),
        ("reconcile", "reservation-1", "0.000000", "failure"),
    ]
    persisted = provider.store.load("1" * 64)
    serialized = json.dumps(persisted, sort_keys=True)
    assert persisted["failure_code"] == "transport_error"
    assert persisted["failure_diagnostic"] == "RuntimeError"
    assert persisted["usage"] is None
    assert persisted["cost_usd"] == "0.000000"
    assert persisted["raw_response_text"] == ""
    assert secret not in serialized
    assert "private.invalid" not in serialized
    assert "hermes-private://" not in serialized


def test_refusal_without_usage_reconciles_zero_measured_cost(tmp_path: Path) -> None:
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=None, refusal="fixture refusal")
            )
        ],
        usage=None,
        model=DEFAULT_MODEL_ID,
    )
    provider = LLMObservationProvider(
        store=RecordingStore(tmp_path),
        mode="record",
        transport=_Transport(_Completions(response=response)),
    )
    events: list[tuple] = []

    with pytest.raises(LLMProviderError, match="refusal"):
        provider.governed_structured_call(
            request=_request(), capability=_capability(events)
        )

    assert events[-1] == (
        "reconcile",
        "reservation-1",
        "0.000000",
        "failure",
    )
    recording = provider.store.load("1" * 64)
    assert recording["failure_code"] == "refusal"
    assert recording["usage"] is None
    assert recording["cost_usd"] == "0.000000"


def test_structured_call_validates_task_payload_before_success_accounting(
    tmp_path: Path,
) -> None:
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(message=SimpleNamespace(content='{"ok":true}', refusal=None))
        ],
        usage=SimpleNamespace(
            prompt_tokens=1_000, completion_tokens=500, total_tokens=1_500
        ),
        model=DEFAULT_MODEL_ID,
    )
    provider = LLMObservationProvider(
        store=RecordingStore(tmp_path),
        mode="record",
        transport=_Transport(_Completions(response=response)),
    )
    events: list[tuple] = []
    request = replace(
        _request(),
        response_validator=lambda payload: (
            "unsupported_claim" if payload == {"ok": True} else "invalid_schema"
        ),
    )

    with pytest.raises(LLMProviderError, match="unsupported_claim"):
        provider.governed_structured_call(
            request=request,
            capability=_capability(events),
        )

    assert events == [
        ("reserve", "1" * 64, "0.010000"),
        ("reconcile", "reservation-1", "0.001250", "failure"),
    ]
    recording = provider.store.load("1" * 64)
    assert recording["status"] == "failure"
    assert recording["failure_code"] == "unsupported_claim"
    assert recording["usage"] == {
        "prompt_tokens": 1_000,
        "completion_tokens": 500,
        "total_tokens": 1_500,
    }
    assert recording["cost_usd"] == "0.001250"
    assert recording["raw_response_text"] == ""


def test_exclusive_record_write_has_no_partial_target_after_link_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = RecordingStore(tmp_path)

    def crash_before_publish(*args: object, **kwargs: object) -> None:
        raise OSError("fixture publish crash")

    monkeypatch.setattr(runtime.os, "link", crash_before_publish)
    with pytest.raises(OSError, match="publish crash"):
        store.save_exclusive({"input_hash": "1" * 64})

    assert not store.path_for("1" * 64).exists()
    assert list(tmp_path.glob(f".{('1' * 64)}.json.*.tmp")) == []


def test_product_search_never_reads_private_semantic_members() -> None:
    source = (
        Path(__file__).resolve().parents[2]
        / "job_intel/product_search/evidence_synthesis.py"
    ).read_text(encoding="utf-8")
    assert "semantic_provider._transport" not in source
    assert "semantic_provider._prompt" not in source


def test_capability_issuer_is_not_a_public_bypass_api() -> None:
    assert not hasattr(runtime, "issue_structured_call_capability")
