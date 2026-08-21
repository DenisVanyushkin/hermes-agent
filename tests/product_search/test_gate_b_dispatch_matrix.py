from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from pathlib import Path

import pytest

from tests.product_search.test_gate_b_evidence_skeleton import _manifest
from job_intel.product_search.gate_b_evidence_runner_v1 import (
    AppendOnlyJournal,
    DispatchReceipt,
    JournalState,
    TerminalOutcome,
)
from job_intel.vacancy_understanding.semantic.runtime.llm_provider import (
    GovernedPricingSchedule,
    GovernedStructuredRequest,
    LLMObservationProvider,
    _issue_structured_call_capability,
)


class FakeStore:
    def __init__(self, interrupt_after_save: bool = False) -> None:
        self.records: list[dict[str, object]] = []
        self.interrupt_after_save = interrupt_after_save

    def exists(self, _input_hash: str) -> bool:
        return False

    def save_exclusive(self, record: dict[str, object]) -> None:
        self.records.append(record)
        if self.interrupt_after_save:
            raise KeyboardInterrupt("sealed recording durable")


class FakeTransport:
    def __init__(self, callback: object) -> None:
        self.callback = callback
        self.chat = SimpleNamespace(completions=self)

    def create(self, **_kwargs: object) -> object:
        self.callback()
        return SimpleNamespace(
            model="openai/gpt-5-mini",
            usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2),
            choices=[SimpleNamespace(message=SimpleNamespace(refusal=None, content="{}"))],
        )


def _setup(tmp_path: Path, *, transport_interrupt: bool = False, store_interrupt: bool = False):
    manifest = _manifest(input_sha256="1" * 64, projection_sha256="2" * 64, raw_sha256="3" * 64)
    journal = AppendOnlyJournal.create(manifest, tmp_path / "dispatch.jsonl")
    store = FakeStore(store_interrupt)
    reservations: dict[str, str] = {}

    def reserve(input_hash: str, _amount: Decimal) -> str:
        reservation = f"reservation:{input_hash}"
        reservations[reservation] = input_hash
        return reservation

    def mark_dispatching(reservation: str) -> None:
        assert reservations[reservation] == "a" * 64
        journal.append_pre_dispatch(manifest.row_ref(0))

    def reconcile(reservation: str, cost: Decimal, outcome: str) -> None:
        assert reservations[reservation] == "a" * 64
        journal.commit_terminal(
            DispatchReceipt(manifest_ref=manifest.row_ref(0), sequence=0),
            TerminalOutcome(outcome), "c" * 64, cost, cost,
        )

    def entered() -> None:
        if transport_interrupt:
            raise KeyboardInterrupt("transport entered")

    pricing = GovernedPricingSchedule(
        version="test-pricing-v1", model_id="openai/gpt-5-mini",
        input_usd_per_mtok=Decimal("1"), output_usd_per_mtok=Decimal("1"),
        max_input_tokens=100, max_output_tokens=100,
    )
    capability = _issue_structured_call_capability(
        run_identity_sha256="b" * 64, pricing=pricing, exact_call_cap=48,
        exact_spend_cap_usd=Decimal("0.48"), metadata_seal_key=b"k" * 32,
        reserve=reserve, mark_dispatching=mark_dispatching, reconcile=reconcile,
    )
    provider = LLMObservationProvider(
        store=store, mode="record", transport=FakeTransport(entered)
    )
    request = GovernedStructuredRequest(
        input_hash="a" * 64, system_prompt="system", user_payload={"title": "test"},
        schema_name="test_schema", response_schema={"type": "object"},
        governance_identity={"run_identity_sha256": "b" * 64},
    )
    return provider, capability, request, journal, store


def test_governed_call_orders_dispatch_transport_recording_terminal(tmp_path: Path) -> None:
    provider, capability, request, journal, store = _setup(tmp_path)
    provider.governed_structured_call(request=request, capability=capability)
    assert store.records
    assert journal.state(0) is JournalState.SUCCESS


def test_transport_entered_without_recording_remains_dispatched(tmp_path: Path) -> None:
    provider, capability, request, journal, store = _setup(tmp_path, transport_interrupt=True)
    with pytest.raises(KeyboardInterrupt):
        provider.governed_structured_call(request=request, capability=capability)
    assert journal.state(0) is JournalState.DISPATCHED
    assert not store.records


def test_sealed_recording_before_reconcile_is_recoverable(tmp_path: Path) -> None:
    provider, capability, request, journal, store = _setup(tmp_path, store_interrupt=True)
    with pytest.raises(KeyboardInterrupt):
        provider.governed_structured_call(request=request, capability=capability)
    assert store.records and journal.state(0) is JournalState.DISPATCHED
    reopened = AppendOnlyJournal.open(journal.manifest, journal.path)
    reopened.commit_terminal(
        DispatchReceipt(manifest_ref=journal.manifest.row_ref(0), sequence=0),
        TerminalOutcome.SUCCESS, "c" * 64, Decimal("0.000002"), Decimal("0.000002"),
    )
    assert reopened.state(0) is JournalState.SUCCESS
