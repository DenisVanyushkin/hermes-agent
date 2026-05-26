from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
import argparse
import json
import tempfile
from typing import Iterable

from .journal import AppendOnlyJournal, EventType, JournalEvent
from .normalization import MarketBar, MarketRegime, NormalizedMarketSnapshot
from .risk import RiskDecision, RiskDecisionStatus, RiskEngine, RiskState, TradeIntent, TradeSide
from .strategy import (
    STRATEGY_SCHEMA_VERSION,
    DeterministicStrategyProvider,
    StrategyAction,
    StrategyProposal,
    StrategyProvider,
    StrategyRunContext,
)

OBSERVER_SCHEMA_VERSION = "1.0.0"
CURRENT_FILL_MODEL_VERSION = "1.0.0"
LIVE_ORDER_PATH_ENABLED = False


@dataclass(frozen=True, slots=True)
class ShadowPosition:
    symbol: str
    quantity: float
    average_cost_quote: float
    last_mark_quote: float

    @property
    def market_value_quote(self) -> float:
        return self.quantity * self.last_mark_quote


@dataclass(frozen=True, slots=True)
class ShadowLedgerEntry:
    entry_id: str
    occurred_at: datetime
    trade_id: str
    correlation_id: str
    symbol: str
    side: TradeSide
    quantity: float
    fill_price_quote: float
    fee_quote: float
    cash_delta_quote: float
    realized_pnl_delta_quote: float
    position_qty_after: float
    reason: str
    source: str


@dataclass(frozen=True, slots=True)
class SimulatedFillModel:
    schema_version: str
    fee_rate_quote: float = 0.0
    slippage_bps: float = 0.0
    max_fill_notional_quote: float | None = None
    execution_mode: str = "simulated"

    def simulate(self, *, intent: TradeIntent, market: NormalizedMarketSnapshot) -> "SimulatedFillExecution":
        if intent.quantity <= 0:
            raise ValueError("intent.quantity must be positive")
        market_price = _mark_price(market)
        if market_price <= 0:
            raise ValueError("market price must be positive")
        slippage_ratio = self.slippage_bps / 10_000.0
        if intent.side == TradeSide.BUY:
            execution_price = round(market_price * (1.0 + slippage_ratio), 8)
            slippage_per_unit = execution_price - market_price
        else:
            execution_price = round(market_price * (1.0 - slippage_ratio), 8)
            slippage_per_unit = market_price - execution_price
        if execution_price <= 0:
            raise ValueError("execution price must be positive")
        requested_quantity = round(float(intent.quantity), 12)
        fill_status = "filled"
        filled_quantity = requested_quantity
        if self.max_fill_notional_quote is not None:
            max_qty = round(self.max_fill_notional_quote / execution_price, 12)
            if max_qty < requested_quantity:
                fill_status = "partial"
                filled_quantity = max(0.0, max_qty)
        filled_quantity = round(filled_quantity, 12)
        notional_quote = round(filled_quantity * execution_price, 8)
        fee_quote = round(notional_quote * self.fee_rate_quote, 8)
        slippage_quote = round(slippage_per_unit * filled_quantity, 8)
        return SimulatedFillExecution(
            schema_version=self.schema_version,
            execution_mode=self.execution_mode,
            fill_status=fill_status,
            requested_quantity=requested_quantity,
            filled_quantity=filled_quantity,
            market_price_quote=round(market_price, 8),
            execution_price_quote=execution_price,
            slippage_bps=self.slippage_bps,
            slippage_quote=slippage_quote,
            fee_rate_quote=self.fee_rate_quote,
            fee_quote=fee_quote,
            notional_quote=notional_quote,
            max_fill_notional_quote=self.max_fill_notional_quote,
        )


@dataclass(frozen=True, slots=True)
class SimulatedFillExecution:
    schema_version: str
    execution_mode: str
    fill_status: str
    requested_quantity: float
    filled_quantity: float
    market_price_quote: float
    execution_price_quote: float
    slippage_bps: float
    slippage_quote: float
    fee_rate_quote: float
    fee_quote: float
    notional_quote: float
    max_fill_notional_quote: float | None


@dataclass(frozen=True, slots=True)
class ShadowPortfolio:
    schema_version: str
    cash_quote: float
    realized_pnl_quote: float
    unrealized_pnl_quote: float
    positions: tuple[ShadowPosition, ...] = field(default_factory=tuple)
    ledger: tuple[ShadowLedgerEntry, ...] = field(default_factory=tuple)
    marks: tuple[tuple[str, float], ...] = field(default_factory=tuple)

    @property
    def equity_quote(self) -> float:
        return round(self.cash_quote + sum(position.market_value_quote for position in self.positions), 8)

    def apply_fill(
        self,
        *,
        trade_id: str,
        correlation_id: str,
        symbol: str,
        side: TradeSide,
        quantity: float,
        fill_price_quote: float,
        fee_quote: float,
        filled_at: datetime,
        source: str,
    ) -> "ShadowPortfolio":
        if quantity <= 0:
            raise ValueError("quantity must be positive")
        if fill_price_quote <= 0:
            raise ValueError("fill_price_quote must be positive")
        if filled_at.tzinfo is None or filled_at.utcoffset() is None:
            raise ValueError("filled_at must be timezone-aware")

        positions = {position.symbol: position for position in self.positions}
        position = positions.get(symbol, ShadowPosition(symbol=symbol, quantity=0.0, average_cost_quote=0.0, last_mark_quote=fill_price_quote))
        cash_delta = 0.0
        realized_delta = 0.0
        new_quantity = position.quantity
        new_average_cost = position.average_cost_quote

        if side == TradeSide.BUY:
            cash_delta = -(quantity * fill_price_quote + fee_quote)
            total_cost = position.quantity * position.average_cost_quote + quantity * fill_price_quote
            new_quantity = round(position.quantity + quantity, 12)
            new_average_cost = round(total_cost / new_quantity, 12)
        else:
            if quantity > position.quantity:
                raise ValueError("cannot sell more than the current shadow position quantity")
            cash_delta = quantity * fill_price_quote - fee_quote
            realized_delta = (fill_price_quote - position.average_cost_quote) * quantity - fee_quote
            new_quantity = round(position.quantity - quantity, 12)
            if new_quantity == 0:
                new_average_cost = 0.0
            else:
                new_average_cost = position.average_cost_quote

        positions[symbol] = ShadowPosition(
            symbol=symbol,
            quantity=new_quantity,
            average_cost_quote=new_average_cost,
            last_mark_quote=fill_price_quote,
        )
        if new_quantity == 0:
            positions.pop(symbol, None)

        marks = dict(self.marks)
        marks[symbol] = fill_price_quote
        ledger_entry = ShadowLedgerEntry(
            entry_id=f"ledger-{trade_id}-{len(self.ledger) + 1:04d}",
            occurred_at=filled_at.astimezone(timezone.utc),
            trade_id=trade_id,
            correlation_id=correlation_id,
            symbol=symbol,
            side=side,
            quantity=quantity,
            fill_price_quote=fill_price_quote,
            fee_quote=fee_quote,
            cash_delta_quote=cash_delta,
            realized_pnl_delta_quote=realized_delta,
            position_qty_after=new_quantity,
            reason="simulated_fill",
            source=source,
        )
        portfolio = ShadowPortfolio(
            schema_version=self.schema_version,
            cash_quote=round(self.cash_quote + cash_delta, 8),
            realized_pnl_quote=round(self.realized_pnl_quote + realized_delta, 8),
            unrealized_pnl_quote=0.0,
            positions=tuple(sorted(positions.values(), key=lambda pos: pos.symbol)),
            ledger=self.ledger + (ledger_entry,),
            marks=tuple(sorted(marks.items())),
        )
        return portfolio._recompute_unrealized()

    def mark_to_market(self, *, symbol: str, price_quote: float, marked_at: datetime) -> "ShadowPortfolio":
        if price_quote <= 0:
            raise ValueError("price_quote must be positive")
        if marked_at.tzinfo is None or marked_at.utcoffset() is None:
            raise ValueError("marked_at must be timezone-aware")

        marks = dict(self.marks)
        marks[symbol] = price_quote
        portfolio = ShadowPortfolio(
            schema_version=self.schema_version,
            cash_quote=self.cash_quote,
            realized_pnl_quote=self.realized_pnl_quote,
            unrealized_pnl_quote=0.0,
            positions=self.positions,
            ledger=self.ledger,
            marks=tuple(sorted(marks.items())),
        )
        return portfolio._recompute_unrealized()

    def _recompute_unrealized(self) -> "ShadowPortfolio":
        marks = dict(self.marks)
        unrealized = 0.0
        updated_positions = []
        for position in self.positions:
            mark = marks.get(position.symbol, position.last_mark_quote or position.average_cost_quote)
            updated_positions.append(
                ShadowPosition(
                    symbol=position.symbol,
                    quantity=position.quantity,
                    average_cost_quote=position.average_cost_quote,
                    last_mark_quote=mark,
                )
            )
            unrealized += (mark - position.average_cost_quote) * position.quantity
        return ShadowPortfolio(
            schema_version=self.schema_version,
            cash_quote=round(self.cash_quote, 8),
            realized_pnl_quote=round(self.realized_pnl_quote, 8),
            unrealized_pnl_quote=round(unrealized, 8),
            positions=tuple(sorted(updated_positions, key=lambda pos: pos.symbol)),
            ledger=self.ledger,
            marks=self.marks,
        )


@dataclass(frozen=True, slots=True)
class ObserverSessionStep:
    market: NormalizedMarketSnapshot
    intent: TradeIntent | None = None
    strategy_proposal: StrategyProposal | None = None


@dataclass(frozen=True, slots=True)
class ObserverSessionResult:
    session_id: str
    portfolio: ShadowPortfolio
    strategy_proposals: tuple[StrategyProposal, ...]
    decisions: tuple[RiskDecision, ...]
    events: tuple[JournalEvent, ...]
    journal_event_ids: tuple[str, ...]

    @property
    def risk_decisions(self) -> tuple[RiskDecision, ...]:
        return self.decisions


class ObserverRunner:
    def __init__(
        self,
        *,
        risk_engine: RiskEngine,
        journal: AppendOnlyJournal | None = None,
        fill_fee_rate: float = 0.0,
        strategy_provider: StrategyProvider | None = None,
        fill_model: SimulatedFillModel | None = None,
    ):
        self.risk_engine = risk_engine
        self.journal = journal
        self.strategy_provider = strategy_provider
        self.fill_model = fill_model or SimulatedFillModel(
            schema_version=OBSERVER_SCHEMA_VERSION,
            fee_rate_quote=fill_fee_rate,
            slippage_bps=0.0,
            max_fill_notional_quote=None,
        )
        self.fill_fee_rate = self.fill_model.fee_rate_quote

    def initial_shadow_portfolio(self, *, cash_quote: float) -> ShadowPortfolio:
        return ShadowPortfolio(
            schema_version=OBSERVER_SCHEMA_VERSION,
            cash_quote=cash_quote,
            realized_pnl_quote=0.0,
            unrealized_pnl_quote=0.0,
        )

    def _strategy_context(
        self,
        *,
        session_id: str,
        portfolio: ShadowPortfolio,
        market: NormalizedMarketSnapshot,
        strategy_id: str,
        strategy_version: str,
        prompt_version: str,
        model_name: str,
        model_version: str,
        state: RiskState,
    ) -> StrategyRunContext:
        return StrategyRunContext(
            schema_version=STRATEGY_SCHEMA_VERSION,
            session_id=session_id,
            correlation_id=session_id,
            strategy_id=strategy_id,
            strategy_version=strategy_version,
            prompt_version=prompt_version,
            model_name=model_name,
            model_version=model_version,
            normalization_version=market.schema_version,
            risk_version=state.schema_version,
            market=market,
            portfolio_cash_quote=portfolio.cash_quote,
            portfolio_equity_quote=portfolio.equity_quote,
            position_notional_quote=_position_notional(portfolio, market.normalized_symbol),
        )

    @staticmethod
    def _proposal_to_intent(proposal: StrategyProposal) -> TradeIntent | None:
        if proposal.action == StrategyAction.HOLD:
            return None
        return proposal.to_trade_intent()

    def run_session(
        self,
        *,
        session_id: str,
        steps: Iterable[ObserverSessionStep],
        initial_state: RiskState,
        initial_cash_quote: float,
    ) -> ObserverSessionResult:
        steps = tuple(steps)
        session_start_at = steps[0].market.observed_at - timedelta(microseconds=1) if steps else _utc_now()
        session_end_at = steps[-1].market.observed_at + timedelta(microseconds=1) if steps else _utc_now()
        portfolio = self.initial_shadow_portfolio(cash_quote=initial_cash_quote)
        strategy_proposals: list[StrategyProposal] = []
        decisions: list[RiskDecision] = []
        events: list[JournalEvent] = []
        journal_event_ids: list[str] = []
        self._append_event(
            events,
            journal_event_ids,
            self._session_event(
                event_type=EventType.OBSERVER_SESSION_START,
                session_id=session_id,
                payload={
                    "schema_version": OBSERVER_SCHEMA_VERSION,
                    "initial_cash_quote": initial_cash_quote,
                    "fill_fee_rate": self.fill_fee_rate,
                    "fill_model": {
                        "schema_version": self.fill_model.schema_version,
                        "execution_mode": self.fill_model.execution_mode,
                        "fee_rate_quote": self.fill_model.fee_rate_quote,
                        "slippage_bps": self.fill_model.slippage_bps,
                        "max_fill_notional_quote": self.fill_model.max_fill_notional_quote,
                    },
                },
                suffix="start",
                occurred_at=session_start_at,
            ),
        )
        if self.journal is not None:
            self.journal.append(events[-1])
        state = initial_state
        for index, step in enumerate(steps, start=1):
            proposal = step.strategy_proposal
            if proposal is None and self.strategy_provider is not None:
                context = self._strategy_context(
                    session_id=session_id,
                    portfolio=portfolio,
                    market=step.market,
                    strategy_id=getattr(self.strategy_provider, "strategy_id", "observer-shadow-strategy"),
                    strategy_version=getattr(self.strategy_provider, "strategy_version", "shadow"),
                    prompt_version=getattr(self.strategy_provider, "prompt_version", "shadow"),
                    model_name=getattr(self.strategy_provider, "model_name", "shadow"),
                    model_version=getattr(self.strategy_provider, "model_version", "shadow"),
                    state=state,
                )
                raw_proposal = self.strategy_provider.propose(context)
                proposal = StrategyProposal.from_raw(
                    raw_proposal,
                    context=context,
                    source_kind=getattr(self.strategy_provider, "source_kind", "llm"),
                )
            elif proposal is None and step.intent is not None:
                context = self._strategy_context(
                    session_id=session_id,
                    portfolio=portfolio,
                    market=step.market,
                    strategy_id=step.intent.strategy_id,
                    strategy_version="manual-intent",
                    prompt_version="manual-intent",
                    model_name="manual",
                    model_version="manual",
                    state=state,
                )
                proposal = StrategyProposal.from_trade_intent(step.intent, context=context, source_kind="manual")

            intent = step.intent
            if proposal is not None:
                strategy_proposals.append(proposal)
                proposal_event = proposal.to_journal_event()
                self._append_event(events, journal_event_ids, proposal_event)
                if self.journal is not None:
                    self.journal.append(proposal_event)
                if intent is None:
                    intent = self._proposal_to_intent(proposal)

            if intent is not None:
                decision = self.risk_engine.evaluate(
                    intent=intent,
                    market=step.market,
                    account_equity_quote=portfolio.equity_quote,
                    current_position_notional_quote=_position_notional(portfolio, intent.symbol),
                    state=state,
                )
                decisions.append(decision)
                decision_event = decision.to_journal_event()
                self._append_event(events, journal_event_ids, decision_event)
                if self.journal is not None:
                    self.journal.append(decision_event)
                state = decision.next_state
                if decision.status == RiskDecisionStatus.APPROVED:
                    fill = self.fill_model.simulate(intent=intent, market=step.market)
                    portfolio = portfolio.apply_fill(
                        trade_id=intent.trade_id,
                        correlation_id=session_id,
                        symbol=intent.symbol,
                        side=intent.side,
                        quantity=fill.filled_quantity,
                        fill_price_quote=fill.execution_price_quote,
                        fee_quote=fill.fee_quote,
                        filled_at=step.market.observed_at,
                        source=self.fill_model.execution_mode,
                    )
                    fill_event = self._session_event(
                        event_type=EventType.OBSERVER_FILL,
                        session_id=session_id,
                        payload={
                            "schema_version": OBSERVER_SCHEMA_VERSION,
                            "fill_model_schema_version": fill.schema_version,
                            "fill_model_version": fill.schema_version,
                            "execution_mode": fill.execution_mode,
                            "fill_status": fill.fill_status,
                            "step_index": index,
                            "trade_id": intent.trade_id,
                            "correlation_id": intent.correlation_id,
                            "symbol": intent.symbol,
                            "side": intent.side.value,
                            "requested_quantity": fill.requested_quantity,
                            "filled_quantity": fill.filled_quantity,
                            "quantity": fill.filled_quantity,
                            "market_price_quote": fill.market_price_quote,
                            "execution_price_quote": fill.execution_price_quote,
                            "fill_price_quote": fill.execution_price_quote,
                            "slippage_bps": fill.slippage_bps,
                            "slippage_quote": fill.slippage_quote,
                            "fee_rate_quote": fill.fee_rate_quote,
                            "fee_quote": fill.fee_quote,
                            "notional_quote": fill.notional_quote,
                            "max_fill_notional_quote": fill.max_fill_notional_quote,
                            "decision_id": decision.decision_id,
                            "source": self.fill_model.execution_mode,
                        },
                        suffix=f"fill-{index:04d}",
                        occurred_at=step.market.observed_at,
                        symbol=intent.symbol,
                        trade_id=intent.trade_id,
                    )
                    self._append_event(events, journal_event_ids, fill_event)
                    if self.journal is not None:
                        self.journal.append(fill_event)
            portfolio = portfolio.mark_to_market(symbol=step.market.normalized_symbol, price_quote=_mark_price(step.market), marked_at=step.market.observed_at)
            snapshot_event = self._session_event(
                event_type=EventType.SHADOW_PORTFOLIO_SNAPSHOT,
                session_id=session_id,
                payload={
                    "schema_version": OBSERVER_SCHEMA_VERSION,
                    "step_index": index,
                    "portfolio": _portfolio_payload(portfolio),
                    "market": _market_payload(step.market),
                },
                suffix=f"snapshot-{index:04d}",
                occurred_at=step.market.observed_at,
            )
            self._append_event(events, journal_event_ids, snapshot_event)
            if self.journal is not None:
                self.journal.append(snapshot_event)
        end_event = self._session_event(
            event_type=EventType.OBSERVER_SESSION_END,
            session_id=session_id,
            payload={
                "schema_version": OBSERVER_SCHEMA_VERSION,
                "final_portfolio": _portfolio_payload(portfolio),
                "decision_count": len(decisions),
                "strategy_proposal_count": len(strategy_proposals),
                "event_count": len(events) + 1,
            },
            suffix="end",
            occurred_at=session_end_at,
        )
        self._append_event(events, journal_event_ids, end_event)
        if self.journal is not None:
            self.journal.append(end_event)
        return ObserverSessionResult(
            session_id=session_id,
            portfolio=portfolio,
            strategy_proposals=tuple(strategy_proposals),
            decisions=tuple(decisions),
            events=tuple(events),
            journal_event_ids=tuple(journal_event_ids),
        )

    def replay_session(self, session_id: str) -> ObserverSessionResult:
        if self.journal is None:
            raise ValueError("replay_session requires a journal")
        events = tuple(self.journal.query(correlation_id=session_id))
        portfolio = self.initial_shadow_portfolio(cash_quote=0.0)
        strategy_proposals: list[StrategyProposal] = []
        decisions: list[RiskDecision] = []
        started = False
        for event in events:
            if event.event_type == EventType.OBSERVER_SESSION_START:
                started = True
                portfolio = self.initial_shadow_portfolio(cash_quote=float(event.payload["initial_cash_quote"]))
            elif event.event_type == EventType.STRATEGY_PROPOSAL:
                strategy_proposals.append(StrategyProposal.from_journal_event(event))
            elif event.event_type == EventType.RISK_DECISION:
                decisions.append(RiskDecision.from_journal_event(event))
            elif event.event_type == EventType.OBSERVER_FILL:
                payload = event.payload or {}
                fill_quantity = float(payload.get("filled_quantity", payload.get("quantity", 0.0)))
                fill_price_quote = float(payload.get("execution_price_quote", payload.get("fill_price_quote", 0.0)))
                portfolio = portfolio.apply_fill(
                    trade_id=str(payload["trade_id"]),
                    correlation_id=str(payload["correlation_id"]),
                    symbol=str(payload["symbol"]),
                    side=TradeSide(str(payload["side"])),
                    quantity=fill_quantity,
                    fill_price_quote=fill_price_quote,
                    fee_quote=float(payload["fee_quote"]),
                    filled_at=event.occurred_at,
                    source=str(payload.get("source", "simulated")),
                )
            elif event.event_type == EventType.SHADOW_PORTFOLIO_SNAPSHOT:
                payload = event.payload or {}
                market = payload["market"]
                portfolio = portfolio.mark_to_market(
                    symbol=str(market["symbol"]),
                    price_quote=float(market["mark_price_quote"]),
                    marked_at=event.occurred_at,
                )
        if not started:
            raise ValueError(f"session not found: {session_id}")
        return ObserverSessionResult(
            session_id=session_id,
            portfolio=portfolio,
            strategy_proposals=tuple(strategy_proposals),
            decisions=tuple(decisions),
            events=events,
            journal_event_ids=tuple(event.event_id for event in events),
        )

    @staticmethod
    def _session_event(
        *,
        event_type: EventType,
        session_id: str,
        payload: dict[str, object],
        suffix: str,
        occurred_at: datetime,
        symbol: str | None = None,
        trade_id: str | None = None,
    ) -> JournalEvent:
        return JournalEvent(
            event_id=f"{session_id}:{suffix}",
            event_type=event_type,
            schema_version=OBSERVER_SCHEMA_VERSION,
            source_module="observer_runner",
            occurred_at=occurred_at.astimezone(timezone.utc),
            correlation_id=session_id,
            symbol=symbol,
            trade_id=trade_id,
            payload=payload,
        )

    @staticmethod
    def _append_event(events: list[JournalEvent], journal_event_ids: list[str], event: JournalEvent) -> None:
        events.append(event)
        journal_event_ids.append(event.event_id)


def _simulated_fill_price(market: NormalizedMarketSnapshot, intent: TradeIntent) -> float:
    if intent.order_type.lower() == "market":
        return _mark_price(market)
    if intent.limit_price is not None:
        return float(intent.limit_price)
    return _mark_price(market)


def _mark_price(market: NormalizedMarketSnapshot) -> float:
    if market.bars:
        return float(market.bars[-1].close)
    return float(market.source_ticks[-1].price)


def _portfolio_payload(portfolio: ShadowPortfolio) -> dict[str, object]:
    return {
        "schema_version": portfolio.schema_version,
        "cash_quote": portfolio.cash_quote,
        "realized_pnl_quote": portfolio.realized_pnl_quote,
        "unrealized_pnl_quote": portfolio.unrealized_pnl_quote,
        "equity_quote": portfolio.equity_quote,
        "positions": [
            {
                "symbol": position.symbol,
                "quantity": position.quantity,
                "average_cost_quote": position.average_cost_quote,
                "last_mark_quote": position.last_mark_quote,
            }
            for position in portfolio.positions
        ],
        "ledger_entries": len(portfolio.ledger),
    }


def _market_payload(market: NormalizedMarketSnapshot) -> dict[str, object]:
    return {
        "schema_version": market.schema_version,
        "symbol": market.normalized_symbol,
        "observed_at": _fmt_dt(market.observed_at),
        "regime": market.regime.value,
        "anomalies": [anomaly.value for anomaly in market.anomalies],
        "mark_price_quote": _mark_price(market),
    }


def _position_notional(portfolio: ShadowPortfolio, symbol: str) -> float:
    for position in portfolio.positions:
        if position.symbol == symbol:
            return position.quantity * position.last_mark_quote
    return 0.0


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _fmt_dt(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Observer replay and audit tooling.")
    subparsers = parser.add_subparsers(dest="command")

    demo_parser = subparsers.add_parser("demo", help="Run the built-in synthetic session")
    demo_parser.add_argument("--journal-path", type=Path, default=None, help="Optional path for the observer journal")

    for name in ("replay", "audit"):
        sub = subparsers.add_parser(name, help=f"Replay and audit an observer session ({name} is an alias)")
        sub.add_argument("--journal-path", type=Path, required=True, help="Path to the observer journal")
        sub.add_argument("--session-id", required=True, help="Session correlation id to replay")

    trace_parser = subparsers.add_parser("trace", help="Reconstruct one trade from journaled artifacts")
    trace_parser.add_argument("--journal-path", type=Path, required=True, help="Path to the observer journal")
    trace_parser.add_argument("--trade-id", required=True, help="Trade id to reconstruct")

    compare_parser = subparsers.add_parser("compare", help="Compare two observer runs")
    compare_parser.add_argument("--reference-journal-path", type=Path, required=True)
    compare_parser.add_argument("--reference-session-id", required=True)
    compare_parser.add_argument("--candidate-journal-path", type=Path, required=True)
    compare_parser.add_argument("--candidate-session-id", required=True)

    monitor_parser = subparsers.add_parser("monitor", help="Build a read-only monitoring dashboard from journaled observer data")
    monitor_parser.add_argument("--journal-path", type=Path, required=True, help="Path to the observer journal")
    monitor_parser.add_argument("--session-id", required=True, help="Session correlation id to analyze")
    monitor_parser.add_argument(
        "--operational-mode",
        choices=("observer", "entry-disabled", "live"),
        default="observer",
        help="Explicit operational mode indicator to surface in the dashboard",
    )
    monitor_parser.add_argument("--format", choices=("json", "markdown"), default="json", help="Render format for the dashboard")
    monitor_parser.add_argument("--compare-reference-journal-path", type=Path, default=None, help="Optional reference journal for comparison")
    monitor_parser.add_argument("--compare-reference-session-id", default=None, help="Reference session id for comparison")
    monitor_parser.add_argument("--compare-candidate-journal-path", type=Path, default=None, help="Optional candidate journal for comparison")
    monitor_parser.add_argument("--compare-candidate-session-id", default=None, help="Candidate session id for comparison")

    args = parser.parse_args(argv)

    if args.command in {"audit", "replay"}:
        from .audit import build_observer_audit_report, build_observer_replay_report, format_observer_audit_report

        journal = AppendOnlyJournal(args.journal_path)
        if args.command == "replay":
            report = build_observer_replay_report(journal, session_id=args.session_id)
        else:
            report = build_observer_audit_report(journal, session_id=args.session_id)
        print(format_observer_audit_report(report))
        return
    if args.command == "trace":
        from .audit import trace_observer_trade

        journal = AppendOnlyJournal(args.journal_path)
        trace = trace_observer_trade(journal, trade_id=args.trade_id)
        print(json.dumps(trace.to_dict(), indent=2, sort_keys=True))
        return
    if args.command == "compare":
        from .audit import (
            build_observer_audit_report,
            compare_observer_audit_reports,
            format_observer_comparison_report,
        )

        reference = build_observer_audit_report(AppendOnlyJournal(args.reference_journal_path), session_id=args.reference_session_id)
        candidate = build_observer_audit_report(AppendOnlyJournal(args.candidate_journal_path), session_id=args.candidate_session_id)
        comparison = compare_observer_audit_reports(reference, candidate)
        print(format_observer_comparison_report(comparison))
        return
    if args.command == "monitor":
        from .audit import build_observer_audit_report, build_observer_replay_report, compare_observer_audit_reports
        from .monitoring import build_observer_monitoring_report, format_observer_monitoring_report, render_observer_monitoring_dashboard

        journal = AppendOnlyJournal(args.journal_path)
        audit_report = build_observer_audit_report(journal, session_id=args.session_id)
        replay_report = build_observer_replay_report(journal, session_id=args.session_id)
        comparison_report = None
        if args.compare_reference_journal_path is not None or args.compare_candidate_journal_path is not None:
            if not all(
                [
                    args.compare_reference_journal_path,
                    args.compare_reference_session_id,
                    args.compare_candidate_journal_path,
                    args.compare_candidate_session_id,
                ]
            ):
                raise SystemExit("comparison requires both reference and candidate journal/session arguments")
            reference = build_observer_audit_report(AppendOnlyJournal(args.compare_reference_journal_path), session_id=args.compare_reference_session_id)
            candidate = build_observer_audit_report(AppendOnlyJournal(args.compare_candidate_journal_path), session_id=args.compare_candidate_session_id)
            comparison_report = compare_observer_audit_reports(reference, candidate)
        report = build_observer_monitoring_report(
            journal,
            session_id=args.session_id,
            operational_mode=args.operational_mode,
            audit_report=audit_report,
            replay_report=replay_report,
            comparison_report=comparison_report,
        )
        if args.format == "markdown":
            print(render_observer_monitoring_dashboard(report))
        else:
            print(format_observer_monitoring_report(report))
        return

    policy = _demo_policy()
    state = _demo_state()
    market = _demo_market()
    step = ObserverSessionStep(market=market)

    if args.command == "demo" and args.journal_path is not None:
        journal = AppendOnlyJournal(args.journal_path)
        runner = ObserverRunner(risk_engine=RiskEngine(policy), journal=journal, strategy_provider=DeterministicStrategyProvider())
        result = runner.run_session(session_id="observer-demo", steps=(step,), initial_state=state, initial_cash_quote=1000.0)
    else:
        with tempfile.TemporaryDirectory() as tmpdir:
            journal = AppendOnlyJournal(Path(tmpdir) / "observer.sqlite3")
            runner = ObserverRunner(risk_engine=RiskEngine(policy), journal=journal, strategy_provider=DeterministicStrategyProvider())
            result = runner.run_session(session_id="observer-demo", steps=(step,), initial_state=state, initial_cash_quote=1000.0)
            replay = runner.replay_session("observer-demo")
            _print_demo_summary(result, replay, journal_path=journal.path)
            return

    replay = runner.replay_session("observer-demo")
    _print_demo_summary(result, replay, journal_path=journal.path)


def _print_demo_summary(result: ObserverSessionResult, replay: ObserverSessionResult, *, journal_path: Path) -> None:
    summary = {
        "session_id": result.session_id,
        "journal_path": str(journal_path),
        "event_types": [event.event_type.value for event in result.events],
        "replay_consistent": result.portfolio == replay.portfolio and result.journal_event_ids == replay.journal_event_ids,
        "portfolio": _portfolio_payload(result.portfolio),
        "decisions": [
            {
                "decision_id": decision.decision_id,
                "status": decision.status.value,
                "reasons": [reason.value for reason in decision.reasons],
            }
            for decision in result.decisions
        ],
        "strategy_proposals": [
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
            for proposal in result.strategy_proposals
        ],
        "live_order_path_enabled": LIVE_ORDER_PATH_ENABLED,
    }
    print(json.dumps(summary, indent=2, sort_keys=True))


def _demo_policy() -> "RiskPolicy":
    from .risk import RiskPolicy

    return RiskPolicy(
        schema_version="1.0.0",
        allowed_symbols=("BTCUSDT",),
        max_order_notional_quote=100.0,
        max_position_notional_quote=1000.0,
        max_drawdown_pct=0.25,
        cooldown_seconds=60,
        enter_cooldown_on_veto=True,
    )


def _demo_state() -> RiskState:
    return RiskState(
        schema_version="1.0.0",
        cooldown_until=None,
        peak_equity_quote=1000.0,
        last_evaluated_at=None,
        last_decision_id=None,
    )


def _demo_market() -> NormalizedMarketSnapshot:
    from .normalization import MarketSnapshot, MarketTick, normalize_market_snapshot

    snapshot = MarketSnapshot(
        symbol="BTCUSDT",
        observed_at=datetime(2026, 5, 26, 10, 0, tzinfo=timezone.utc),
        ticks=(
            MarketTick("BTCUSDT", datetime(2026, 5, 26, 10, 0, 0, tzinfo=timezone.utc), 100.0, 1.0, "binance"),
            MarketTick("BTCUSDT", datetime(2026, 5, 26, 10, 0, 1, tzinfo=timezone.utc), 100.0, 1.0, "binance"),
            MarketTick("BTCUSDT", datetime(2026, 5, 26, 10, 0, 2, tzinfo=timezone.utc), 100.0, 1.0, "binance"),
        ),
    )
    return normalize_market_snapshot(snapshot)


def _demo_intent() -> "TradeIntent":
    from .risk import TradeIntent, TradeSide

    return TradeIntent(
        schema_version="1.0.0",
        trade_id="trade-demo",
        correlation_id="observer-demo",
        symbol="BTCUSDT",
        side=TradeSide.BUY,
        order_type="limit",
        quantity=0.5,
        limit_price=100.0,
        strategy_id="observer-demo",
        requested_at=datetime(2026, 5, 26, 10, 0, tzinfo=timezone.utc),
    )


if __name__ == "__main__":
    main()
