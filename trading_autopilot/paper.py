from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from pathlib import Path
from typing import Iterable

from .journal import AppendOnlyJournal, EventType, JournalEvent
from .normalization import MarketSnapshot, MarketTick, NormalizedMarketSnapshot, normalize_market_snapshot
from .observer import ShadowPortfolio, SimulatedFillExecution, SimulatedFillModel
from .risk import RISK_SCHEMA_VERSION, RiskDecision, RiskDecisionStatus, RiskEngine, RiskPolicy, RiskState, TradeIntent, TradeSide
from .strategy import STRATEGY_SCHEMA_VERSION, DeterministicStrategyProvider, StrategyAction, StrategyProposal, StrategyProvider, StrategyRunContext

PAPER_TRADING_SCHEMA_VERSION = "1.0.0"
PAPER_TRADING_SOURCE_KIND = "paper_trading"
PAPER_TRADING_DEFAULT_SLACK_TARGET = "C0B66CQ49SS"
PAPER_TRADING_DEFAULT_ALLOWED_ASSETS = ("BTC", "ETH", "BNB", "SOL", "LINK")
PAPER_TRADING_DEFAULT_QUOTE_ASSET = "USDT"
PAPER_TRADING_DEFAULT_INITIAL_CASH_QUOTE = 100_000.0
PAPER_TRADING_DEFAULT_FEE_RATE = 0.0004
PAPER_TRADING_DEFAULT_SLIPPAGE_BPS = 2.0
PAPER_TRADING_DEFAULT_MAX_ORDER_NOTIONAL_QUOTE = 50_000.0
PAPER_TRADING_DEFAULT_MAX_POSITION_NOTIONAL_QUOTE = 50_000.0


class PaperOrderState(StrEnum):
    CREATED = "created"
    RISK_APPROVED = "risk_approved"
    RISK_REJECTED = "risk_rejected"
    QUEUED = "queued"
    ACTIVE = "active"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    REJECTED = "rejected"
    SETTLED = "settled"


PaperTradingRiskPolicy = RiskPolicy


@dataclass(frozen=True, slots=True)
class PaperTradingRunRequest:
    session_id: str
    journal: AppendOnlyJournal
    client: object
    observed_at: datetime | None = None
    allowed_assets: tuple[str, ...] = PAPER_TRADING_DEFAULT_ALLOWED_ASSETS
    quote_asset: str = PAPER_TRADING_DEFAULT_QUOTE_ASSET
    initial_cash_quote: float = PAPER_TRADING_DEFAULT_INITIAL_CASH_QUOTE
    strategy_provider: StrategyProvider | None = None
    risk_engine: RiskEngine | None = None
    risk_state: RiskState | None = None
    fill_model: SimulatedFillModel | None = None
    slack_target: str = PAPER_TRADING_DEFAULT_SLACK_TARGET


@dataclass(frozen=True, slots=True)
class PaperOrder:
    order_id: str
    trade_id: str
    session_id: str
    asset: str
    symbol: str
    side: TradeSide
    order_type: str
    requested_quantity: float
    limit_price: float | None
    state: PaperOrderState
    created_at: datetime
    updated_at: datetime
    decision_id: str | None = None
    filled_quantity: float = 0.0
    execution_price_quote: float | None = None
    fee_quote: float = 0.0
    status_reason: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "order_id": self.order_id,
            "trade_id": self.trade_id,
            "session_id": self.session_id,
            "asset": self.asset,
            "symbol": self.symbol,
            "side": self.side.value,
            "order_type": self.order_type,
            "requested_quantity": self.requested_quantity,
            "limit_price": self.limit_price,
            "state": self.state.value,
            "created_at": _fmt_dt(self.created_at),
            "updated_at": _fmt_dt(self.updated_at),
            "decision_id": self.decision_id,
            "filled_quantity": self.filled_quantity,
            "execution_price_quote": self.execution_price_quote,
            "fee_quote": self.fee_quote,
            "status_reason": self.status_reason,
        }


@dataclass(frozen=True, slots=True)
class PaperTradingPnLSnapshot:
    schema_version: str
    session_id: str
    initial_cash_quote: float
    cash_quote: float
    equity_quote: float
    realized_pnl_quote: float
    unrealized_pnl_quote: float
    total_pnl_quote: float
    drawdown_quote: float
    drawdown_pct: float

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "session_id": self.session_id,
            "initial_cash_quote": self.initial_cash_quote,
            "cash_quote": self.cash_quote,
            "equity_quote": self.equity_quote,
            "realized_pnl_quote": self.realized_pnl_quote,
            "unrealized_pnl_quote": self.unrealized_pnl_quote,
            "total_pnl_quote": self.total_pnl_quote,
            "drawdown_quote": self.drawdown_quote,
            "drawdown_pct": self.drawdown_pct,
        }


@dataclass(frozen=True, slots=True)
class PaperTradingStepReport:
    asset: str
    symbol: str
    market_price_quote: float
    proposal_id: str | None
    decision_id: str | None
    decision_status: str | None
    order_id: str | None
    order_state: str | None
    fill_status: str | None
    filled_quantity: float | None
    execution_price_quote: float | None
    fee_quote: float | None
    portfolio_cash_quote: float
    portfolio_equity_quote: float
    pnl_quote: float

    def to_dict(self) -> dict[str, object]:
        return {
            "asset": self.asset,
            "symbol": self.symbol,
            "market_price_quote": self.market_price_quote,
            "proposal_id": self.proposal_id,
            "decision_id": self.decision_id,
            "decision_status": self.decision_status,
            "order_id": self.order_id,
            "order_state": self.order_state,
            "fill_status": self.fill_status,
            "filled_quantity": self.filled_quantity,
            "execution_price_quote": self.execution_price_quote,
            "fee_quote": self.fee_quote,
            "portfolio_cash_quote": self.portfolio_cash_quote,
            "portfolio_equity_quote": self.portfolio_equity_quote,
            "pnl_quote": self.pnl_quote,
        }


@dataclass(frozen=True, slots=True)
class PaperTradingRunReport:
    schema_version: str
    session_id: str
    journal_path: str
    status: str
    failure_reason: str | None
    generated_at: datetime
    allowed_assets: tuple[str, ...]
    quote_asset: str
    strategy_proposal_count: int
    approved_decision_count: int
    rejected_decision_count: int
    paper_order_count: int
    fill_count: int
    portfolio: ShadowPortfolio
    pnl_snapshot: PaperTradingPnLSnapshot
    steps: tuple[PaperTradingStepReport, ...]
    live_execution_proof: LiveExecutionProof
    slack_target: str
    slack_message: str
    replay_consistent: bool
    event_ids: tuple[str, ...]
    event_types: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "session_id": self.session_id,
            "journal_path": self.journal_path,
            "status": self.status,
            "failure_reason": self.failure_reason,
            "generated_at": _fmt_dt(self.generated_at),
            "allowed_assets": list(self.allowed_assets),
            "quote_asset": self.quote_asset,
            "strategy_proposal_count": self.strategy_proposal_count,
            "approved_decision_count": self.approved_decision_count,
            "rejected_decision_count": self.rejected_decision_count,
            "paper_order_count": self.paper_order_count,
            "fill_count": self.fill_count,
            "portfolio": _portfolio_payload(self.portfolio),
            "pnl_snapshot": self.pnl_snapshot.to_dict(),
            "steps": [step.to_dict() for step in self.steps],
            "live_execution_proof": self.live_execution_proof.to_dict(),
            "slack_target": self.slack_target,
            "slack_message": self.slack_message,
            "replay_consistent": self.replay_consistent,
            "event_ids": list(self.event_ids),
            "event_types": list(self.event_types),
        }


@dataclass(frozen=True, slots=True)
class PaperTradingReplayReport:
    schema_version: str
    session_id: str
    journal_path: str
    allowed_assets: tuple[str, ...]
    quote_asset: str
    event_count: int
    strategy_proposal_count: int
    approved_decision_count: int
    rejected_decision_count: int
    paper_order_count: int
    fill_count: int
    portfolio: ShadowPortfolio
    pnl_snapshot: PaperTradingPnLSnapshot
    replay_consistent: bool
    event_ids: tuple[str, ...]
    event_types: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "session_id": self.session_id,
            "journal_path": self.journal_path,
            "allowed_assets": list(self.allowed_assets),
            "quote_asset": self.quote_asset,
            "event_count": self.event_count,
            "strategy_proposal_count": self.strategy_proposal_count,
            "approved_decision_count": self.approved_decision_count,
            "rejected_decision_count": self.rejected_decision_count,
            "paper_order_count": self.paper_order_count,
            "fill_count": self.fill_count,
            "portfolio": _portfolio_payload(self.portfolio),
            "pnl_snapshot": self.pnl_snapshot.to_dict(),
            "replay_consistent": self.replay_consistent,
            "event_ids": list(self.event_ids),
            "event_types": list(self.event_types),
        }


def default_paper_risk_policy(allowed_symbols: Iterable[str]) -> RiskPolicy:
    return RiskPolicy(
        schema_version=RISK_SCHEMA_VERSION,
        allowed_symbols=tuple(allowed_symbols),
        max_order_notional_quote=PAPER_TRADING_DEFAULT_MAX_ORDER_NOTIONAL_QUOTE,
        max_position_notional_quote=PAPER_TRADING_DEFAULT_MAX_POSITION_NOTIONAL_QUOTE,
        max_drawdown_pct=0.30,
        cooldown_seconds=0,
        enter_cooldown_on_veto=False,
    )


def run_paper_trading_mvp(request: PaperTradingRunRequest) -> PaperTradingRunReport:
    session_id = request.session_id.strip()
    if not session_id:
        raise ValueError("session_id must be set")
    observed_at = _require_timezone_aware(request.observed_at or datetime.now(timezone.utc), name="observed_at")
    allowed_assets = tuple(asset.strip().upper() for asset in request.allowed_assets if asset and asset.strip())
    if not allowed_assets:
        raise ValueError("allowed_assets must not be empty")
    quote_asset = request.quote_asset.strip().upper() or PAPER_TRADING_DEFAULT_QUOTE_ASSET
    allowed_symbols = tuple(f"{asset}{quote_asset}" for asset in allowed_assets)
    strategy_provider = request.strategy_provider or DeterministicStrategyProvider()
    fill_model = request.fill_model or SimulatedFillModel(
        schema_version=PAPER_TRADING_SCHEMA_VERSION,
        fee_rate_quote=PAPER_TRADING_DEFAULT_FEE_RATE,
        slippage_bps=PAPER_TRADING_DEFAULT_SLIPPAGE_BPS,
        max_fill_notional_quote=None,
        execution_mode="paper",
    )
    risk_engine = request.risk_engine or RiskEngine(default_paper_risk_policy(allowed_symbols))
    risk_state = request.risk_state or RiskState(
        schema_version=RISK_SCHEMA_VERSION,
        cooldown_until=None,
        peak_equity_quote=request.initial_cash_quote,
        last_evaluated_at=None,
        last_decision_id=None,
    )
    portfolio = ShadowPortfolio(
        schema_version=PAPER_TRADING_SCHEMA_VERSION,
        cash_quote=request.initial_cash_quote,
        realized_pnl_quote=0.0,
        unrealized_pnl_quote=0.0,
    )

    events: list[JournalEvent] = []
    steps: list[PaperTradingStepReport] = []
    paper_orders: list[PaperOrder] = []
    strategy_proposals: list[StrategyProposal] = []
    decisions: list[RiskDecision] = []
    fill_executions: list[SimulatedFillExecution] = []
    journal = request.journal
    event_ids: list[str] = []
    slack_at = observed_at + _microseconds(len(allowed_assets) + 1)
    session_end_at = observed_at + _microseconds(len(allowed_assets) + 2)

    _append_event(
        journal,
        events,
        event_ids,
        _paper_event(
            event_type=EventType.PAPER_TRADING_SESSION_START,
            session_id=session_id,
            suffix="start",
            occurred_at=observed_at,
            payload={
                "schema_version": PAPER_TRADING_SCHEMA_VERSION,
                "operational_mode": PAPER_TRADING_SOURCE_KIND,
                "execution_enabled": False,
                "allowed_assets": list(allowed_assets),
                "quote_asset": quote_asset,
                "initial_cash_quote": request.initial_cash_quote,
                "strategy_provider": _strategy_provider_payload(strategy_provider),
                "fill_model": _fill_model_payload(fill_model),
                "risk_policy": _risk_policy_payload(risk_engine.policy),
                "slack_target": request.slack_target,
            },
        ),
    )

    for index, asset in enumerate(allowed_assets, start=1):
        symbol = f"{asset}{quote_asset}"
        tick_payload = request.client.get_ticker_price(symbol)
        price_text = str(tick_payload["price"])
        market_price_quote = float(price_text)
        market_observed_at = observed_at + _microseconds(index)
        normalized_market = _normalize_single_tick_market(symbol=symbol, observed_at=market_observed_at, price=market_price_quote)
        market_event = _paper_event(
            event_type=EventType.PAPER_TRADING_MARKET_SNAPSHOT,
            session_id=session_id,
            symbol=symbol,
            suffix=f"market-{index:04d}",
            occurred_at=market_observed_at,
            payload={
                "schema_version": PAPER_TRADING_SCHEMA_VERSION,
                "asset": asset,
                "symbol": symbol,
                "market_price_quote": market_price_quote,
                "raw_ticker": tick_payload,
                "normalized_market": normalized_market.to_dict(),
            },
        )
        _append_event(journal, events, event_ids, market_event)

        strategy_context = StrategyRunContext(
            schema_version=STRATEGY_SCHEMA_VERSION,
            session_id=session_id,
            correlation_id=session_id,
            strategy_id=getattr(strategy_provider, "strategy_id", "paper-trading-strategy"),
            strategy_version=getattr(strategy_provider, "strategy_version", PAPER_TRADING_SCHEMA_VERSION),
            prompt_version=getattr(strategy_provider, "prompt_version", "paper-trading-v1"),
            model_name=getattr(strategy_provider, "model_name", "gpt-5.5"),
            model_version=getattr(strategy_provider, "model_version", "xhigh"),
            normalization_version=normalized_market.schema_version,
            risk_version=risk_state.schema_version,
            market=normalized_market,
            portfolio_cash_quote=portfolio.cash_quote,
            portfolio_equity_quote=portfolio.equity_quote,
            position_notional_quote=_position_notional(portfolio, symbol),
        )
        raw_proposal = strategy_provider.propose(strategy_context)
        proposal = StrategyProposal.from_raw(raw_proposal, context=strategy_context, source_kind=getattr(strategy_provider, "source_kind", PAPER_TRADING_SOURCE_KIND))
        strategy_proposals.append(proposal)
        proposal_event = proposal.to_journal_event()
        _append_event(journal, events, event_ids, proposal_event)

        step_order: PaperOrder | None = None
        step_fill: SimulatedFillExecution | None = None
        step_decision: RiskDecision | None = None
        if proposal.action != StrategyAction.HOLD:
            intent = proposal.to_trade_intent()
            step_order = PaperOrder(
                order_id=_paper_order_id(session_id=session_id, trade_id=intent.trade_id, symbol=symbol),
                trade_id=intent.trade_id,
                session_id=session_id,
                asset=asset,
                symbol=symbol,
                side=intent.side,
                order_type=intent.order_type,
                requested_quantity=intent.quantity,
                limit_price=intent.limit_price,
                state=PaperOrderState.CREATED,
                created_at=market_observed_at,
                updated_at=market_observed_at,
            )
            paper_orders.append(step_order)
            _append_event(
                journal,
                events,
                event_ids,
                _paper_event(
                    event_type=EventType.PAPER_ORDER_CREATED,
                    session_id=session_id,
                    symbol=symbol,
                    trade_id=step_order.trade_id,
                    suffix=f"order-created-{index:04d}",
                    occurred_at=market_observed_at,
                    payload={"schema_version": PAPER_TRADING_SCHEMA_VERSION, "order": step_order.to_dict()},
                ),
            )

            step_decision = risk_engine.evaluate(
                intent=intent,
                market=normalized_market,
                account_equity_quote=portfolio.equity_quote,
                current_position_notional_quote=_position_notional(portfolio, symbol),
                state=risk_state,
            )
            decisions.append(step_decision)
            _append_event(journal, events, event_ids, step_decision.to_journal_event())
            risk_state = step_decision.next_state

            if step_decision.status == RiskDecisionStatus.APPROVED:
                step_order = _transition_order(
                    journal,
                    events,
                    event_ids,
                    step_order,
                    PaperOrderState.RISK_APPROVED,
                    market_observed_at,
                    session_id=session_id,
                    suffix=f"order-risk-approved-{index:04d}",
                    reason=step_decision.decision_id,
                )
                step_order = _transition_order(
                    journal,
                    events,
                    event_ids,
                    step_order,
                    PaperOrderState.QUEUED,
                    market_observed_at,
                    session_id=session_id,
                    suffix=f"order-queued-{index:04d}",
                    reason="deterministic queue",
                )
                step_order = _transition_order(
                    journal,
                    events,
                    event_ids,
                    step_order,
                    PaperOrderState.ACTIVE,
                    market_observed_at,
                    session_id=session_id,
                    suffix=f"order-active-{index:04d}",
                    reason="simulated activation",
                )
                step_fill = fill_model.simulate(intent=intent, market=normalized_market)
                fill_executions.append(step_fill)
                fill_event = _paper_event(
                    event_type=EventType.PAPER_ORDER_FILL,
                    session_id=session_id,
                    symbol=symbol,
                    trade_id=step_order.trade_id,
                    suffix=f"fill-{index:04d}",
                    occurred_at=market_observed_at,
                    payload={
                        "schema_version": PAPER_TRADING_SCHEMA_VERSION,
                        "order_id": step_order.order_id,
                        "trade_id": step_order.trade_id,
                        "asset": asset,
                        "symbol": symbol,
                        "side": intent.side.value,
                        "decision_id": step_decision.decision_id,
                        "fill_model_schema_version": step_fill.schema_version,
                        "execution_mode": step_fill.execution_mode,
                        "fill_status": step_fill.fill_status,
                        "requested_quantity": step_fill.requested_quantity,
                        "filled_quantity": step_fill.filled_quantity,
                        "execution_price_quote": step_fill.execution_price_quote,
                        "market_price_quote": step_fill.market_price_quote,
                        "slippage_bps": step_fill.slippage_bps,
                        "slippage_quote": step_fill.slippage_quote,
                        "fee_rate_quote": step_fill.fee_rate_quote,
                        "fee_quote": step_fill.fee_quote,
                        "notional_quote": step_fill.notional_quote,
                    },
                )
                _append_event(journal, events, event_ids, fill_event)
                portfolio = portfolio.apply_fill(
                    trade_id=step_order.trade_id,
                    correlation_id=session_id,
                    symbol=symbol,
                    side=intent.side,
                    quantity=step_fill.filled_quantity,
                    fill_price_quote=step_fill.execution_price_quote,
                    fee_quote=step_fill.fee_quote,
                    filled_at=market_observed_at,
                    source=step_fill.execution_mode,
                )
                portfolio = portfolio.mark_to_market(
                    symbol=symbol,
                    price_quote=market_price_quote,
                    marked_at=market_observed_at,
                )
                order_state = PaperOrderState.PARTIALLY_FILLED if step_fill.fill_status == "partial" else PaperOrderState.FILLED
                step_order = _transition_order(
                    journal,
                    events,
                    event_ids,
                    step_order,
                    order_state,
                    market_observed_at,
                    session_id=session_id,
                    suffix=f"order-{order_state.value}-{index:04d}",
                    reason=step_fill.fill_status,
                    filled_quantity=step_fill.filled_quantity,
                    execution_price_quote=step_fill.execution_price_quote,
                    fee_quote=step_fill.fee_quote,
                )
                step_order = _transition_order(
                    journal,
                    events,
                    event_ids,
                    step_order,
                    PaperOrderState.SETTLED,
                    market_observed_at,
                    session_id=session_id,
                    suffix=f"order-settled-{index:04d}",
                    reason="portfolio updated",
                )
                step_order = replace(step_order, updated_at=market_observed_at, status_reason=step_fill.fill_status)
                _append_event(
                    journal,
                    events,
                    event_ids,
                    _paper_event(
                        event_type=EventType.PAPER_PORTFOLIO_SNAPSHOT,
                        session_id=session_id,
                        symbol=symbol,
                        trade_id=step_order.trade_id,
                        suffix=f"portfolio-{index:04d}",
                        occurred_at=market_observed_at,
                        payload={
                            "schema_version": PAPER_TRADING_SCHEMA_VERSION,
                            "step_index": index,
                            "asset": asset,
                            "symbol": symbol,
                            "portfolio": _portfolio_payload(portfolio),
                            "market": _market_payload(normalized_market),
                            "order": step_order.to_dict(),
                        },
                    ),
                )
                pnl_snapshot = _pnl_snapshot(session_id, portfolio, request.initial_cash_quote)
                _append_event(
                    journal,
                    events,
                    event_ids,
                    _paper_event(
                        event_type=EventType.PAPER_PNL_SNAPSHOT,
                        session_id=session_id,
                        symbol=symbol,
                        trade_id=step_order.trade_id,
                        suffix=f"pnl-{index:04d}",
                        occurred_at=market_observed_at,
                        payload={
                            "schema_version": PAPER_TRADING_SCHEMA_VERSION,
                            "step_index": index,
                            "pnl_snapshot": pnl_snapshot.to_dict(),
                            "portfolio": _portfolio_payload(portfolio),
                        },
                    ),
                )
            else:
                step_order = _transition_order(
                    journal,
                    events,
                    event_ids,
                    step_order,
                    PaperOrderState.RISK_REJECTED,
                    market_observed_at,
                    session_id=session_id,
                    suffix=f"order-risk-rejected-{index:04d}",
                    reason=step_decision.reasons[0].value if step_decision.reasons else "risk_rejected",
                )
                step_order = _transition_order(
                    journal,
                    events,
                    event_ids,
                    step_order,
                    PaperOrderState.REJECTED,
                    market_observed_at,
                    session_id=session_id,
                    suffix=f"order-rejected-{index:04d}",
                    reason=step_decision.decision_id,
                )
                _append_event(
                    journal,
                    events,
                    event_ids,
                    _paper_event(
                        event_type=EventType.PAPER_ORDER_REJECTED,
                        session_id=session_id,
                        symbol=symbol,
                        trade_id=step_order.trade_id,
                        suffix=f"order-rejected-event-{index:04d}",
                        occurred_at=market_observed_at,
                        payload={
                            "schema_version": PAPER_TRADING_SCHEMA_VERSION,
                            "order_id": step_order.order_id,
                            "order": step_order.to_dict(),
                            "decision_id": step_decision.decision_id,
                            "reasons": [reason.value for reason in step_decision.reasons],
                        },
                    ),
                )
        else:
            step_decision = None

        pnl_snapshot = _pnl_snapshot(session_id, portfolio, request.initial_cash_quote)
        steps.append(
            PaperTradingStepReport(
                asset=asset,
                symbol=symbol,
                market_price_quote=market_price_quote,
                proposal_id=proposal.trade_id,
                decision_id=None if step_decision is None else step_decision.decision_id,
                decision_status=None if step_decision is None else step_decision.status.value,
                order_id=None if step_order is None else step_order.order_id,
                order_state=None if step_order is None else step_order.state.value,
                fill_status=None if step_fill is None else step_fill.fill_status,
                filled_quantity=None if step_fill is None else step_fill.filled_quantity,
                execution_price_quote=None if step_fill is None else step_fill.execution_price_quote,
                fee_quote=None if step_fill is None else step_fill.fee_quote,
                portfolio_cash_quote=portfolio.cash_quote,
                portfolio_equity_quote=portfolio.equity_quote,
                pnl_quote=pnl_snapshot.total_pnl_quote,
            )
        )

    report_pre = PaperTradingRunReport(
        schema_version=PAPER_TRADING_SCHEMA_VERSION,
        session_id=session_id,
        journal_path=str(request.journal.path),
        status="ok",
        failure_reason=None,
        generated_at=observed_at,
        allowed_assets=allowed_assets,
        quote_asset=quote_asset,
        strategy_proposal_count=len(strategy_proposals),
        approved_decision_count=sum(1 for decision in decisions if decision.status == RiskDecisionStatus.APPROVED),
        rejected_decision_count=sum(1 for decision in decisions if decision.status == RiskDecisionStatus.DENIED),
        paper_order_count=len(paper_orders),
        fill_count=len(fill_executions),
        portfolio=portfolio,
        pnl_snapshot=_pnl_snapshot(session_id, portfolio, request.initial_cash_quote),
        steps=tuple(steps),
        live_execution_proof=_live_execution_proof(request.client),
        slack_target=request.slack_target,
        slack_message="",
        replay_consistent=False,
        event_ids=tuple(event.event_id for event in events),
        event_types=tuple(event.event_type.value for event in events),
    )
    replay = replay_paper_trading_run(request.journal.path, session_id=session_id)
    if replay.portfolio != report_pre.portfolio or replay.pnl_snapshot != report_pre.pnl_snapshot:
        raise RuntimeError("paper trading replay drift detected")

    final_report = replace(report_pre, replay_consistent=True)
    slack_message = format_paper_trading_report(final_report)

    _append_event(
        journal,
        events,
        event_ids,
        _paper_event(
            event_type=EventType.PAPER_TRADING_SLACK_REPORT,
            session_id=session_id,
            suffix="slack-report",
            occurred_at=slack_at,
            payload={
                "schema_version": PAPER_TRADING_SCHEMA_VERSION,
                "slack_target": request.slack_target,
                "message": slack_message,
                "status": "ok",
            },
        ),
    )
    _append_event(
        journal,
        events,
        event_ids,
        _paper_event(
            event_type=EventType.PAPER_TRADING_SESSION_END,
            session_id=session_id,
            suffix="end",
            occurred_at=session_end_at,
            payload={
                "schema_version": PAPER_TRADING_SCHEMA_VERSION,
                "status": "ok",
                "failure_reason": None,
                "final_portfolio": _portfolio_payload(portfolio),
                "pnl_snapshot": _pnl_snapshot(session_id, portfolio, request.initial_cash_quote).to_dict(),
                "allowed_assets": list(allowed_assets),
                "quote_asset": quote_asset,
                "strategy_proposal_count": len(strategy_proposals),
                "approved_decision_count": sum(1 for decision in decisions if decision.status == RiskDecisionStatus.APPROVED),
                "rejected_decision_count": sum(1 for decision in decisions if decision.status == RiskDecisionStatus.DENIED),
                "paper_order_count": len(paper_orders),
                "fill_count": len(fill_executions),
                "slack_target": request.slack_target,
                "order_endpoints_called": _live_execution_proof(request.client).order_endpoints_called,
                "blocked_methods": list(_live_execution_proof(request.client).blocked_methods),
                "slack_message": slack_message,
            },
        ),
    )

    return replace(
        final_report,
        slack_message=slack_message,
        event_ids=tuple(event.event_id for event in events),
        event_types=tuple(event.event_type.value for event in events),
    )


def replay_paper_trading_run(journal: AppendOnlyJournal | str | Path, *, session_id: str) -> PaperTradingReplayReport:
    journal_obj = journal if isinstance(journal, AppendOnlyJournal) else AppendOnlyJournal(journal)
    events = tuple(journal_obj.query(correlation_id=session_id))
    if not events:
        raise ValueError(f"session not found: {session_id}")

    allowed_assets: tuple[str, ...] = PAPER_TRADING_DEFAULT_ALLOWED_ASSETS
    quote_asset = PAPER_TRADING_DEFAULT_QUOTE_ASSET
    initial_cash_quote = PAPER_TRADING_DEFAULT_INITIAL_CASH_QUOTE
    portfolio = ShadowPortfolio(
        schema_version=PAPER_TRADING_SCHEMA_VERSION,
        cash_quote=initial_cash_quote,
        realized_pnl_quote=0.0,
        unrealized_pnl_quote=0.0,
    )
    strategy_proposal_count = 0
    approved_decision_count = 0
    rejected_decision_count = 0
    paper_order_count = 0
    fill_count = 0
    for event in events:
        if event.event_type == EventType.PAPER_TRADING_SESSION_START:
            payload = event.payload or {}
            allowed_assets = tuple(str(item) for item in payload.get("allowed_assets", allowed_assets))
            quote_asset = str(payload.get("quote_asset", quote_asset))
            initial_cash_quote = float(payload.get("initial_cash_quote", initial_cash_quote))
            portfolio = ShadowPortfolio(
                schema_version=PAPER_TRADING_SCHEMA_VERSION,
                cash_quote=initial_cash_quote,
                realized_pnl_quote=0.0,
                unrealized_pnl_quote=0.0,
            )
        elif event.event_type == EventType.STRATEGY_PROPOSAL:
            strategy_proposal_count += 1
        elif event.event_type == EventType.RISK_DECISION:
            decision = RiskDecision.from_journal_event(event)
            if decision.status == RiskDecisionStatus.APPROVED:
                approved_decision_count += 1
            else:
                rejected_decision_count += 1
        elif event.event_type == EventType.PAPER_ORDER_CREATED:
            paper_order_count += 1
        elif event.event_type == EventType.PAPER_ORDER_FILL:
            payload = event.payload or {}
            portfolio = portfolio.apply_fill(
                trade_id=str(payload["trade_id"]),
                correlation_id=session_id,
                symbol=str(payload["symbol"]),
                side=TradeSide(str(payload["side"])),
                quantity=float(payload["filled_quantity"]),
                fill_price_quote=float(payload["execution_price_quote"]),
                fee_quote=float(payload["fee_quote"]),
                filled_at=event.occurred_at,
                source=str(payload.get("execution_mode", "paper")),
            )
            fill_count += 1
        elif event.event_type == EventType.PAPER_PORTFOLIO_SNAPSHOT:
            payload = event.payload or {}
            market = payload.get("market", {})
            if isinstance(market, dict):
                portfolio = portfolio.mark_to_market(
                    symbol=str(market.get("symbol")),
                    price_quote=float(market.get("mark_price_quote")),
                    marked_at=event.occurred_at,
                )

    pnl_snapshot = _pnl_snapshot(session_id, portfolio, initial_cash_quote)
    return PaperTradingReplayReport(
        schema_version=PAPER_TRADING_SCHEMA_VERSION,
        session_id=session_id,
        journal_path=str(journal_obj.path),
        allowed_assets=allowed_assets,
        quote_asset=quote_asset,
        event_count=len(events),
        strategy_proposal_count=strategy_proposal_count,
        approved_decision_count=approved_decision_count,
        rejected_decision_count=rejected_decision_count,
        paper_order_count=paper_order_count,
        fill_count=fill_count,
        portfolio=portfolio,
        pnl_snapshot=pnl_snapshot,
        replay_consistent=True,
        event_ids=tuple(event.event_id for event in events),
        event_types=tuple(event.event_type.value for event in events),
    )


def format_paper_trading_report(report: PaperTradingRunReport) -> str:
    lines = []
    status_icon = "🟢" if report.status == "ok" else "⛔"
    lines.append(f"{status_icon} Paper trading {report.status.upper()} · {report.session_id}")
    lines.append(f"- universe: {', '.join(report.allowed_assets)} / {report.quote_asset}")
    lines.append(f"- symbols: {', '.join(f'{asset}{report.quote_asset}' for asset in report.allowed_assets)}")
    lines.append(f"- session: {report.session_id}")
    lines.append(f"- journal: {Path(report.journal_path).name}")
    lines.append(
        f"- events: proposals={report.strategy_proposal_count} | approved={report.approved_decision_count} | rejected={report.rejected_decision_count} | orders={report.paper_order_count} | fills={report.fill_count}"
    )
    lines.append(
        f"- portfolio: cash={report.portfolio.cash_quote:,.2f} | equity={report.portfolio.equity_quote:,.2f} | realized_pnl={report.portfolio.realized_pnl_quote:,.2f} | unrealized_pnl={report.portfolio.unrealized_pnl_quote:,.2f}"
    )
    lines.append(
        f"- PnL: total={report.pnl_snapshot.total_pnl_quote:+,.2f} | drawdown={report.pnl_snapshot.drawdown_quote:,.2f} ({report.pnl_snapshot.drawdown_pct:.2%})"
    )
    lines.append(f"- slack: target={report.slack_target}")
    lines.append(f"- proof: order_endpoints_called={'yes' if report.live_execution_proof.order_endpoints_called else 'no'}")
    if report.live_execution_proof.blocked_methods:
        lines.append(f"- blocked: {', '.join(report.live_execution_proof.blocked_methods)}")
    lines.append(f"- replay: {'consistent' if report.replay_consistent else 'drift'}")
    if report.failure_reason:
        lines.append(f"- failure: {report.failure_reason}")
    return "\n".join(lines)


def _append_event(journal: AppendOnlyJournal, events: list[JournalEvent], event_ids: list[str], event: JournalEvent) -> None:
    journal.append(event)
    events.append(event)
    event_ids.append(event.event_id)


def _paper_event(*, event_type: EventType, session_id: str, suffix: str, occurred_at: datetime, payload: dict[str, object], symbol: str | None = None, trade_id: str | None = None) -> JournalEvent:
    return JournalEvent(
        event_id=f"{session_id}:{suffix}",
        event_type=event_type,
        schema_version=PAPER_TRADING_SCHEMA_VERSION,
        source_module="paper_trading_runner",
        occurred_at=occurred_at.astimezone(timezone.utc),
        correlation_id=session_id,
        symbol=symbol,
        trade_id=trade_id,
        payload=payload,
    )


def _transition_order(
    journal: AppendOnlyJournal,
    events: list[JournalEvent],
    event_ids: list[str],
    order: PaperOrder,
    new_state: PaperOrderState,
    occurred_at: datetime,
    *,
    session_id: str,
    suffix: str,
    reason: str,
    filled_quantity: float | None = None,
    execution_price_quote: float | None = None,
    fee_quote: float | None = None,
) -> PaperOrder:
    transitioned = replace(
        order,
        state=new_state,
        updated_at=occurred_at,
        filled_quantity=order.filled_quantity if filled_quantity is None else filled_quantity,
        execution_price_quote=order.execution_price_quote if execution_price_quote is None else execution_price_quote,
        fee_quote=order.fee_quote if fee_quote is None else fee_quote,
        status_reason=reason,
    )
    _append_event(
        journal,
        events,
        event_ids,
        _paper_event(
            event_type=EventType.PAPER_ORDER_STATE_CHANGED,
            session_id=session_id,
            trade_id=order.trade_id,
            symbol=order.symbol,
            suffix=suffix,
            occurred_at=occurred_at,
            payload={
                "schema_version": PAPER_TRADING_SCHEMA_VERSION,
                "order_id": order.order_id,
                "from_state": order.state.value,
                "to_state": new_state.value,
                "reason": reason,
                "order": transitioned.to_dict(),
            },
        ),
    )
    _append_event(
        journal,
        events,
        event_ids,
        _paper_event(
            event_type=EventType.PAPER_ORDER_SNAPSHOT,
            session_id=session_id,
            trade_id=order.trade_id,
            symbol=order.symbol,
            suffix=f"{suffix}-snapshot",
            occurred_at=occurred_at,
            payload={
                "schema_version": PAPER_TRADING_SCHEMA_VERSION,
                "order": transitioned.to_dict(),
            },
        ),
    )
    return transitioned


def _normalize_single_tick_market(*, symbol: str, observed_at: datetime, price: float) -> NormalizedMarketSnapshot:
    market_snapshot = MarketSnapshot(
        symbol=symbol,
        observed_at=observed_at,
        ticks=(MarketTick(symbol=symbol, observed_at=observed_at, price=price, volume=0.0, source=PAPER_TRADING_SOURCE_KIND),),
    )
    return normalize_market_snapshot(market_snapshot)


def _pnl_snapshot(session_id: str, portfolio: ShadowPortfolio, initial_cash_quote: float) -> PaperTradingPnLSnapshot:
    equity_quote = portfolio.equity_quote
    drawdown_quote = max(initial_cash_quote - equity_quote, 0.0)
    drawdown_pct = 0.0 if initial_cash_quote <= 0 else drawdown_quote / initial_cash_quote
    return PaperTradingPnLSnapshot(
        schema_version=PAPER_TRADING_SCHEMA_VERSION,
        session_id=session_id,
        initial_cash_quote=initial_cash_quote,
        cash_quote=portfolio.cash_quote,
        equity_quote=equity_quote,
        realized_pnl_quote=portfolio.realized_pnl_quote,
        unrealized_pnl_quote=portfolio.unrealized_pnl_quote,
        total_pnl_quote=equity_quote - initial_cash_quote,
        drawdown_quote=drawdown_quote,
        drawdown_pct=drawdown_pct,
    )


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


def _mark_price(market: NormalizedMarketSnapshot) -> float:
    if market.bars:
        return float(market.bars[-1].close)
    return float(market.source_ticks[-1].price)


def _position_notional(portfolio: ShadowPortfolio, symbol: str) -> float:
    for position in portfolio.positions:
        if position.symbol == symbol:
            return position.quantity * position.last_mark_quote
    return 0.0


def _paper_order_id(*, session_id: str, trade_id: str, symbol: str) -> str:
    return f"paper-order-{session_id}-{symbol}-{trade_id[-12:]}"


def _strategy_provider_payload(provider: StrategyProvider) -> dict[str, object]:
    return {
        "source_kind": getattr(provider, "source_kind", PAPER_TRADING_SOURCE_KIND),
        "strategy_id": getattr(provider, "strategy_id", "paper-trading-strategy"),
        "strategy_version": getattr(provider, "strategy_version", PAPER_TRADING_SCHEMA_VERSION),
        "prompt_version": getattr(provider, "prompt_version", "paper-trading-v1"),
        "model_name": getattr(provider, "model_name", "gpt-5.5"),
        "model_version": getattr(provider, "model_version", "xhigh"),
    }


def _fill_model_payload(fill_model: SimulatedFillModel) -> dict[str, object]:
    return {
        "schema_version": fill_model.schema_version,
        "execution_mode": fill_model.execution_mode,
        "fee_rate_quote": fill_model.fee_rate_quote,
        "slippage_bps": fill_model.slippage_bps,
        "max_fill_notional_quote": fill_model.max_fill_notional_quote,
    }


def _risk_policy_payload(policy: RiskPolicy) -> dict[str, object]:
    return {
        "schema_version": policy.schema_version,
        "allowed_symbols": list(policy.allowed_symbols),
        "max_order_notional_quote": policy.max_order_notional_quote,
        "max_position_notional_quote": policy.max_position_notional_quote,
        "max_drawdown_pct": policy.max_drawdown_pct,
        "cooldown_seconds": policy.cooldown_seconds,
        "enter_cooldown_on_veto": policy.enter_cooldown_on_veto,
    }


def _live_execution_proof(client: object) -> LiveExecutionProof:
    from .live_read import LiveExecutionProof

    order_endpoints_called = bool(getattr(client, "order_endpoints_called", False))
    blocked_methods = tuple(getattr(client, "blocked_methods", ()) or ())
    return LiveExecutionProof(order_endpoints_called=order_endpoints_called, blocked_methods=blocked_methods)


def _microseconds(index: int) -> timedelta:
    return timedelta(microseconds=index)


def _require_timezone_aware(value: datetime, *, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _fmt_dt(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
