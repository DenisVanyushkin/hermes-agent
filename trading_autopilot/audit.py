from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .journal import AppendOnlyJournal, EventType, JournalEvent
from .observer import CURRENT_FILL_MODEL_VERSION, OBSERVER_SCHEMA_VERSION
from .risk import RISK_SCHEMA_VERSION, RiskDecisionStatus
from .strategy import STRATEGY_SCHEMA_VERSION


@dataclass(frozen=True, slots=True)
class AuditMismatch:
    field: str
    expected: object
    actual: object
    severity: str = "error"

    def to_dict(self) -> dict[str, object]:
        return {
            "field": self.field,
            "expected": self.expected,
            "actual": self.actual,
            "severity": self.severity,
        }


@dataclass(frozen=True, slots=True)
class TradeTrace:
    trade_id: str
    proposal_event_id: str | None
    decision_event_id: str | None
    fill_event_id: str | None
    rationale: str | None
    action: str | None
    order_type: str | None
    quantity: float | None
    limit_price: float | None
    decision_status: str | None
    fill_status: str | None
    fee_quote: float | None
    filled_quantity: float | None
    model_name: str | None
    model_version: str | None
    strategy_version: str | None
    prompt_version: str | None
    source_kind: str | None
    fill_model_version: str | None
    fill_execution_mode: str | None
    execution_price_quote: float | None
    generated_at: str | None
    filled_at: str | None

    def to_dict(self) -> dict[str, object]:
        return {
            "trade_id": self.trade_id,
            "proposal_event_id": self.proposal_event_id,
            "decision_event_id": self.decision_event_id,
            "fill_event_id": self.fill_event_id,
            "rationale": self.rationale,
            "action": self.action,
            "order_type": self.order_type,
            "quantity": self.quantity,
            "limit_price": self.limit_price,
            "decision_status": self.decision_status,
            "fill_status": self.fill_status,
            "fee_quote": self.fee_quote,
            "filled_quantity": self.filled_quantity,
            "model_name": self.model_name,
            "model_version": self.model_version,
            "strategy_version": self.strategy_version,
            "prompt_version": self.prompt_version,
            "source_kind": self.source_kind,
            "fill_model_version": self.fill_model_version,
            "fill_execution_mode": self.fill_execution_mode,
            "execution_price_quote": self.execution_price_quote,
            "generated_at": self.generated_at,
            "filled_at": self.filled_at,
        }


@dataclass(frozen=True, slots=True)
class ObserverAuditReport:
    session_id: str
    journal_path: str
    event_count: int
    strategy_proposal_count: int
    decision_count: int
    fill_count: int
    final_cash_quote: float | None
    final_equity_quote: float | None
    replay_consistent: bool
    deterministic: bool
    version_warnings: tuple[str, ...]
    mismatches: tuple[AuditMismatch, ...]
    trade_traces: tuple[TradeTrace, ...]
    event_ids: tuple[str, ...]
    event_types: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "session_id": self.session_id,
            "journal_path": self.journal_path,
            "event_count": self.event_count,
            "strategy_proposal_count": self.strategy_proposal_count,
            "decision_count": self.decision_count,
            "fill_count": self.fill_count,
            "final_cash_quote": self.final_cash_quote,
            "final_equity_quote": self.final_equity_quote,
            "replay_consistent": self.replay_consistent,
            "deterministic": self.deterministic,
            "version_warnings": list(self.version_warnings),
            "mismatches": [m.to_dict() for m in self.mismatches],
            "trade_traces": [t.to_dict() for t in self.trade_traces],
            "event_ids": list(self.event_ids),
            "event_types": list(self.event_types),
            "versions": {
                "observer": OBSERVER_SCHEMA_VERSION,
                "strategy": STRATEGY_SCHEMA_VERSION,
                "risk": RISK_SCHEMA_VERSION,
                "fill_model": CURRENT_FILL_MODEL_VERSION,
            },
        }


@dataclass(frozen=True, slots=True)
class ReplaySessionReport:
    session_id: str
    journal_path: str
    event_count: int
    replay_consistent: bool
    version_warnings: tuple[str, ...]
    portfolio: dict[str, object]
    decisions: tuple[dict[str, object], ...]
    strategy_proposals: tuple[dict[str, object], ...]
    trade_traces: tuple[TradeTrace, ...]
    event_ids: tuple[str, ...]
    event_types: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "session_id": self.session_id,
            "journal_path": self.journal_path,
            "event_count": self.event_count,
            "replay_consistent": self.replay_consistent,
            "version_warnings": list(self.version_warnings),
            "portfolio": self.portfolio,
            "decisions": [dict(decision) for decision in self.decisions],
            "strategy_proposals": [dict(proposal) for proposal in self.strategy_proposals],
            "trade_traces": [trace.to_dict() for trace in self.trade_traces],
            "event_ids": list(self.event_ids),
            "event_types": list(self.event_types),
        }


@dataclass(frozen=True, slots=True)
class ObserverComparisonReport:
    reference_session_id: str
    candidate_session_id: str
    match: bool
    mismatches: tuple[AuditMismatch, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "reference_session_id": self.reference_session_id,
            "candidate_session_id": self.candidate_session_id,
            "match": self.match,
            "mismatches": [m.to_dict() for m in self.mismatches],
        }


def build_observer_audit_report(journal: AppendOnlyJournal, *, session_id: str) -> ObserverAuditReport:
    events = tuple(journal.query(correlation_id=session_id))
    if not events:
        raise ValueError(f"session not found: {session_id}")

    event_ids = tuple(event.event_id for event in events)
    event_types = tuple(event.event_type.value for event in events)
    version_warnings = list(_collect_version_warnings(events))
    mismatches = list(_collect_structure_mismatches(events))

    trade_ids: list[str] = []
    for event in events:
        if event.trade_id and event.trade_id not in trade_ids:
            trade_ids.append(event.trade_id)
    trade_traces = tuple(trace_observer_trade(journal, trade_id=trade_id) for trade_id in trade_ids)

    final_event = next((event for event in reversed(events) if event.event_type == EventType.OBSERVER_SESSION_END), None)
    final_payload = final_event.payload if final_event is not None else {}
    final_portfolio = final_payload.get("final_portfolio", {}) if isinstance(final_payload, dict) else {}

    final_cash_quote = _coerce_float(final_portfolio.get("cash_quote")) if isinstance(final_portfolio, dict) else None
    final_equity_quote = _coerce_float(final_portfolio.get("equity_quote")) if isinstance(final_portfolio, dict) else None

    strategy_proposal_count = sum(1 for event in events if event.event_type == EventType.STRATEGY_PROPOSAL)
    decision_count = sum(1 for event in events if event.event_type == EventType.RISK_DECISION)
    fill_count = sum(1 for event in events if event.event_type == EventType.OBSERVER_FILL)

    deterministic = not mismatches
    replay_consistent = deterministic
    return ObserverAuditReport(
        session_id=session_id,
        journal_path=str(journal.path),
        event_count=len(events),
        strategy_proposal_count=strategy_proposal_count,
        decision_count=decision_count,
        fill_count=fill_count,
        final_cash_quote=final_cash_quote,
        final_equity_quote=final_equity_quote,
        replay_consistent=replay_consistent,
        deterministic=deterministic,
        version_warnings=tuple(version_warnings),
        mismatches=tuple(mismatches),
        trade_traces=trade_traces,
        event_ids=event_ids,
        event_types=event_types,
    )


def build_observer_replay_report(journal: AppendOnlyJournal, *, session_id: str) -> ReplaySessionReport:
    from .observer import ObserverRunner
    from .risk import RiskEngine, RiskPolicy, RiskState

    events = tuple(journal.query(correlation_id=session_id))
    if not events:
        raise ValueError(f"session not found: {session_id}")

    runner = ObserverRunner(
        risk_engine=RiskEngine(
            RiskPolicy(
                schema_version=RISK_SCHEMA_VERSION,
                allowed_symbols=(),
                max_order_notional_quote=0.0,
                max_position_notional_quote=0.0,
                max_drawdown_pct=0.0,
                cooldown_seconds=0,
                enter_cooldown_on_veto=False,
            )
        ),
        journal=journal,
    )
    replay = runner.replay_session(session_id)

    trade_ids: list[str] = []
    for event in events:
        if event.trade_id and event.trade_id not in trade_ids:
            trade_ids.append(event.trade_id)

    strategy_proposals = tuple(
        {
            "trade_id": proposal.trade_id,
            "action": proposal.action.value,
            "order_type": proposal.order_type,
            "source_kind": proposal.source_kind,
            "strategy_version": proposal.strategy_version,
            "prompt_version": proposal.prompt_version,
            "model_name": proposal.model_name,
            "model_version": proposal.model_version,
        }
        for proposal in replay.strategy_proposals
    )
    decisions = tuple(
        {
            "decision_id": decision.decision_id,
            "status": decision.status.value,
            "reasons": [reason.value for reason in decision.reasons],
        }
        for decision in replay.decisions
    )
    trade_traces = tuple(trace_observer_trade(journal, trade_id=trade_id) for trade_id in trade_ids)
    return ReplaySessionReport(
        session_id=session_id,
        journal_path=str(journal.path),
        event_count=len(events),
        replay_consistent=True,
        version_warnings=tuple(_collect_version_warnings(events)),
        portfolio=_portfolio_summary(replay.portfolio),
        decisions=decisions,
        strategy_proposals=strategy_proposals,
        trade_traces=trade_traces,
        event_ids=tuple(replay.journal_event_ids),
        event_types=tuple(event.event_type.value for event in replay.events),
    )


def compare_observer_audit_reports(reference: ObserverAuditReport, candidate: ObserverAuditReport) -> ObserverComparisonReport:
    mismatches: list[AuditMismatch] = []
    if reference.final_equity_quote != candidate.final_equity_quote:
        mismatches.append(
            AuditMismatch(
                field="portfolio.equity_quote",
                expected=reference.final_equity_quote,
                actual=candidate.final_equity_quote,
            )
        )
    if reference.final_cash_quote != candidate.final_cash_quote:
        mismatches.append(
            AuditMismatch(
                field="portfolio.cash_quote",
                expected=reference.final_cash_quote,
                actual=candidate.final_cash_quote,
            )
        )
    if reference.event_count != candidate.event_count:
        mismatches.append(
            AuditMismatch(field="event_count", expected=reference.event_count, actual=candidate.event_count)
        )
    if reference.strategy_proposal_count != candidate.strategy_proposal_count:
        mismatches.append(
            AuditMismatch(
                field="strategy_proposal_count",
                expected=reference.strategy_proposal_count,
                actual=candidate.strategy_proposal_count,
            )
        )
    if reference.decision_count != candidate.decision_count:
        mismatches.append(
            AuditMismatch(field="decision_count", expected=reference.decision_count, actual=candidate.decision_count)
        )
    ref_traces = {trace.trade_id: trace for trace in reference.trade_traces}
    cand_traces = {trace.trade_id: trace for trace in candidate.trade_traces}
    for trade_id in sorted(ref_traces.keys() | cand_traces.keys()):
        ref_trace = ref_traces.get(trade_id)
        cand_trace = cand_traces.get(trade_id)
        if ref_trace is None or cand_trace is None:
            mismatches.append(
                AuditMismatch(
                    field=f"trade_traces[{trade_id}]",
                    expected=ref_trace.to_dict() if ref_trace else None,
                    actual=cand_trace.to_dict() if cand_trace else None,
                )
            )
            continue
        for field in (
            "action",
            "order_type",
            "quantity",
            "limit_price",
            "rationale",
            "decision_status",
            "fill_status",
            "filled_quantity",
            "fee_quote",
            "execution_price_quote",
            "fill_model_version",
            "fill_execution_mode",
        ):
            expected = getattr(ref_trace, field)
            actual = getattr(cand_trace, field)
            if expected != actual:
                mismatches.append(
                    AuditMismatch(
                        field=f"trade_traces[{trade_id}].{field}",
                        expected=expected,
                        actual=actual,
                    )
                )
    for warning in reference.version_warnings:
        if warning not in candidate.version_warnings:
            mismatches.append(AuditMismatch(field="version_warnings", expected=warning, actual=None, severity="warning"))
    for warning in candidate.version_warnings:
        if warning not in reference.version_warnings:
            mismatches.append(AuditMismatch(field="version_warnings", expected=None, actual=warning, severity="warning"))
    return ObserverComparisonReport(
        reference_session_id=reference.session_id,
        candidate_session_id=candidate.session_id,
        match=not mismatches,
        mismatches=tuple(mismatches),
    )


def trace_observer_trade(journal: AppendOnlyJournal, *, trade_id: str) -> TradeTrace:
    events = tuple(journal.query(trade_id=trade_id))
    if not events:
        raise ValueError(f"trade not found: {trade_id}")

    proposal_event = next((event for event in events if event.event_type == EventType.STRATEGY_PROPOSAL), None)
    decision_event = next((event for event in events if event.event_type == EventType.RISK_DECISION), None)
    fill_event = next((event for event in events if event.event_type == EventType.OBSERVER_FILL), None)

    proposal_payload = proposal_event.payload if proposal_event else {}
    decision_payload = decision_event.payload if decision_event else {}
    fill_payload = fill_event.payload if fill_event else {}

    return TradeTrace(
        trade_id=trade_id,
        proposal_event_id=proposal_event.event_id if proposal_event else None,
        decision_event_id=decision_event.event_id if decision_event else None,
        fill_event_id=fill_event.event_id if fill_event else None,
        rationale=_coerce_str(proposal_payload.get("rationale")) if proposal_event else None,
        action=_coerce_str(proposal_payload.get("action")) if proposal_event else None,
        order_type=_coerce_str(proposal_payload.get("order_type")) if proposal_event else None,
        quantity=_coerce_float(proposal_payload.get("quantity")) if proposal_event else None,
        limit_price=_coerce_float(proposal_payload.get("limit_price")) if proposal_event else None,
        decision_status=_coerce_str(decision_payload.get("status")) if decision_event else None,
        fill_status=_coerce_str(fill_payload.get("fill_status") or fill_payload.get("status")) if fill_event else None,
        fee_quote=_coerce_float(fill_payload.get("fee_quote")) if fill_event else None,
        filled_quantity=_coerce_float(fill_payload.get("filled_quantity") or fill_payload.get("quantity")) if fill_event else None,
        model_name=_coerce_str(proposal_payload.get("model_name")) if proposal_event else None,
        model_version=_coerce_str(proposal_payload.get("model_version")) if proposal_event else None,
        strategy_version=_coerce_str(proposal_payload.get("strategy_version")) if proposal_event else None,
        prompt_version=_coerce_str(proposal_payload.get("prompt_version")) if proposal_event else None,
        source_kind=_coerce_str(proposal_payload.get("source_kind")) if proposal_event else None,
        fill_model_version=_coerce_str(fill_payload.get("fill_model_version")) if fill_event else None,
        fill_execution_mode=_coerce_str(fill_payload.get("execution_mode")) if fill_event else None,
        execution_price_quote=_coerce_float(fill_payload.get("execution_price_quote")) if fill_event else None,
        generated_at=_coerce_str(proposal_payload.get("generated_at")) if proposal_event else None,
        filled_at=_coerce_str(fill_event.occurred_at.isoformat()) if fill_event else None,
    )


def format_observer_audit_report(report: ObserverAuditReport) -> str:
    return json.dumps(report.to_dict(), indent=2, sort_keys=True)


def format_observer_comparison_report(report: ObserverComparisonReport) -> str:
    return json.dumps(report.to_dict(), indent=2, sort_keys=True)


def _collect_version_warnings(events: Iterable[JournalEvent]) -> list[str]:
    warnings: list[str] = []
    for event in events:
        if event.event_type in {EventType.OBSERVER_SESSION_START, EventType.OBSERVER_FILL, EventType.OBSERVER_SESSION_END}:
            if event.schema_version != OBSERVER_SCHEMA_VERSION:
                warnings.append(
                    f"observer schema mismatch for {event.event_id}: recorded={event.schema_version!r} current={OBSERVER_SCHEMA_VERSION!r}"
                )
        elif event.event_type == EventType.STRATEGY_PROPOSAL and event.schema_version != STRATEGY_SCHEMA_VERSION:
            warnings.append(
                f"strategy schema mismatch for {event.event_id}: recorded={event.schema_version!r} current={STRATEGY_SCHEMA_VERSION!r}"
            )
        elif event.event_type == EventType.RISK_DECISION and event.schema_version != RISK_SCHEMA_VERSION:
            warnings.append(
                f"risk schema mismatch for {event.event_id}: recorded={event.schema_version!r} current={RISK_SCHEMA_VERSION!r}"
            )
        if event.event_type == EventType.OBSERVER_FILL:
            payload = event.payload or {}
            recorded = payload.get("fill_model_version") or payload.get("fill_model_schema_version")
            if recorded != CURRENT_FILL_MODEL_VERSION:
                warnings.append(
                    f"fill_model_version mismatch for {event.event_id}: recorded={recorded!r} current={CURRENT_FILL_MODEL_VERSION!r}"
                )
    return warnings


def _collect_structure_mismatches(events: tuple[JournalEvent, ...]) -> list[AuditMismatch]:
    mismatches: list[AuditMismatch] = []
    if events[0].event_type != EventType.OBSERVER_SESSION_START:
        mismatches.append(
            AuditMismatch(field="first_event_type", expected=EventType.OBSERVER_SESSION_START.value, actual=events[0].event_type.value)
        )
    if events[-1].event_type != EventType.OBSERVER_SESSION_END:
        mismatches.append(
            AuditMismatch(field="last_event_type", expected=EventType.OBSERVER_SESSION_END.value, actual=events[-1].event_type.value)
        )
    for index, event in enumerate(events):
        if index > 0 and event.event_id == events[index - 1].event_id:
            mismatches.append(AuditMismatch(field="duplicate_event_id", expected="unique sequence", actual=event.event_id))
    return mismatches


def _portfolio_summary(portfolio: object) -> dict[str, object]:
    positions = getattr(portfolio, "positions", ())
    ledger = getattr(portfolio, "ledger", ())
    return {
        "schema_version": getattr(portfolio, "schema_version", None),
        "cash_quote": getattr(portfolio, "cash_quote", None),
        "realized_pnl_quote": getattr(portfolio, "realized_pnl_quote", None),
        "unrealized_pnl_quote": getattr(portfolio, "unrealized_pnl_quote", None),
        "equity_quote": getattr(portfolio, "equity_quote", None),
        "positions": [
            {
                "symbol": getattr(position, "symbol", None),
                "quantity": getattr(position, "quantity", None),
                "average_cost_quote": getattr(position, "average_cost_quote", None),
                "last_mark_quote": getattr(position, "last_mark_quote", None),
            }
            for position in positions
        ],
        "ledger_entries": len(tuple(ledger)),
    }


def _coerce_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _coerce_str(value: object) -> str | None:
    if value is None:
        return None
    return str(value)
