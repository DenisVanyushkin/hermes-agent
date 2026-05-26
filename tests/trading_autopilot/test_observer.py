from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from trading_autopilot import (
    AppendOnlyJournal,
    EventType,
    MarketSnapshot,
    MarketTick,
    ObserverRunner,
    ObserverSessionStep,
    RiskDecisionStatus,
    RiskEngine,
    RiskPolicy,
    RiskState,
    SimulatedFillModel,
    TradeIntent,
    TradeSide,
    normalize_market_snapshot,
)


def _snapshot(symbol: str, prices: list[float], ts_prefix: str = "2026-05-26T10:00") -> MarketSnapshot:
    ticks = tuple(
        MarketTick(
            symbol=symbol,
            observed_at=datetime.fromisoformat(f"{ts_prefix}:{index:02d}Z".replace("Z", "+00:00")),
            price=price,
            volume=1.0,
            source="binance",
        )
        for index, price in enumerate(prices)
    )
    return MarketSnapshot(
        symbol=symbol,
        observed_at=datetime(2026, 5, 26, 10, 0, tzinfo=timezone.utc),
        ticks=ticks,
    )


def _intent(trade_id: str, correlation_id: str, quantity: float, limit_price: float = 100.0, side: TradeSide = TradeSide.BUY) -> TradeIntent:
    return TradeIntent(
        schema_version="1.0.0",
        trade_id=trade_id,
        correlation_id=correlation_id,
        symbol="BTCUSDT",
        side=side,
        order_type="limit",
        quantity=quantity,
        limit_price=limit_price,
        strategy_id="observer-demo",
        requested_at=datetime(2026, 5, 26, 10, 0, tzinfo=timezone.utc),
    )


def _policy() -> RiskPolicy:
    return RiskPolicy(
        schema_version="1.0.0",
        allowed_symbols=("BTCUSDT",),
        max_order_notional_quote=100.0,
        max_position_notional_quote=1000.0,
        max_drawdown_pct=0.25,
        cooldown_seconds=60,
        enter_cooldown_on_veto=True,
    )


def _state() -> RiskState:
    return RiskState(
        schema_version="1.0.0",
        cooldown_until=None,
        peak_equity_quote=1000.0,
        last_evaluated_at=None,
        last_decision_id=None,
    )


def test_shadow_portfolio_ledger_tracks_realized_and_unrealized_pnl() -> None:
    runner = ObserverRunner(risk_engine=RiskEngine(_policy()))
    portfolio = runner.initial_shadow_portfolio(cash_quote=1000.0)

    portfolio = portfolio.apply_fill(
        trade_id="trade-1",
        correlation_id="corr-1",
        symbol="BTCUSDT",
        side=TradeSide.BUY,
        quantity=1.0,
        fill_price_quote=100.0,
        fee_quote=0.0,
        filled_at=datetime(2026, 5, 26, 10, 0, tzinfo=timezone.utc),
        source="observer",
    )
    marked = portfolio.mark_to_market(symbol="BTCUSDT", price_quote=110.0, marked_at=datetime(2026, 5, 26, 10, 1, tzinfo=timezone.utc))

    assert marked.cash_quote == 900.0
    assert marked.realized_pnl_quote == 0.0
    assert marked.unrealized_pnl_quote == 10.0
    assert marked.equity_quote == 1010.0
    assert marked.positions[0].quantity == 1.0
    assert marked.ledger[0].cash_delta_quote == -100.0


def test_observer_session_journals_shadow_events_and_replays_consistently(tmp_path: Path) -> None:
    journal = AppendOnlyJournal(tmp_path / "observer.sqlite3")
    runner = ObserverRunner(risk_engine=RiskEngine(_policy()), journal=journal)
    session_id = "session-1"
    market = normalize_market_snapshot(_snapshot("BTCUSDT", [100.0, 100.0, 100.0]))
    step = ObserverSessionStep(market=market, intent=_intent("trade-1", session_id, quantity=0.5))

    result = runner.run_session(session_id=session_id, steps=(step,), initial_state=_state(), initial_cash_quote=1000.0)
    replay = runner.replay_session(session_id)

    assert result.session_id == session_id
    assert result.portfolio.equity_quote == replay.portfolio.equity_quote
    assert result.portfolio == replay.portfolio
    assert result.journal_event_ids == replay.journal_event_ids
    assert any(event.event_type == EventType.OBSERVER_SESSION_START for event in result.events)
    assert any(event.event_type == EventType.OBSERVER_FILL for event in result.events)
    assert any(event.event_type == EventType.SHADOW_PORTFOLIO_SNAPSHOT for event in result.events)


def test_observer_does_not_emit_live_order_events(tmp_path: Path) -> None:
    journal = AppendOnlyJournal(tmp_path / "observer.sqlite3")
    runner = ObserverRunner(risk_engine=RiskEngine(_policy()), journal=journal)
    session_id = "session-2"
    market = normalize_market_snapshot(_snapshot("BTCUSDT", [100.0, 100.0, 100.0]))
    step = ObserverSessionStep(market=market, intent=_intent("trade-2", session_id, quantity=0.5))

    runner.run_session(session_id=session_id, steps=(step,), initial_state=_state(), initial_cash_quote=1000.0)
    event_types = [event.event_type for event in journal.query(correlation_id=session_id)]

    assert EventType.EXECUTION_INTENT not in event_types
    assert EventType.ORDER_UPDATE not in event_types
    assert set(event_types) <= {
        EventType.OBSERVER_SESSION_START,
        EventType.STRATEGY_PROPOSAL,
        EventType.RISK_DECISION,
        EventType.OBSERVER_FILL,
        EventType.SHADOW_PORTFOLIO_SNAPSHOT,
        EventType.OBSERVER_SESSION_END,
    }


def test_simulated_fill_applies_explicit_slippage_and_fee_model(tmp_path: Path) -> None:
    journal = AppendOnlyJournal(tmp_path / "observer.sqlite3")
    fill_model = SimulatedFillModel(schema_version="1.0.0", fee_rate_quote=0.01, slippage_bps=100.0, max_fill_notional_quote=None)
    runner = ObserverRunner(risk_engine=RiskEngine(_policy()), journal=journal, fill_model=fill_model)
    session_id = "session-4"
    market = normalize_market_snapshot(_snapshot("BTCUSDT", [100.0, 100.0, 100.0]))
    step = ObserverSessionStep(market=market, intent=_intent("trade-4", session_id, quantity=1.0))

    result = runner.run_session(session_id=session_id, steps=(step,), initial_state=_state(), initial_cash_quote=1000.0)
    fill_event = next(event for event in result.events if event.event_type == EventType.OBSERVER_FILL)

    assert fill_event.payload["execution_mode"] == "simulated"
    assert fill_event.payload["fill_status"] == "filled"
    assert fill_event.payload["requested_quantity"] == 1.0
    assert fill_event.payload["filled_quantity"] == 1.0
    assert fill_event.payload["market_price_quote"] == 100.0
    assert fill_event.payload["execution_price_quote"] == 101.0
    assert fill_event.payload["slippage_bps"] == 100.0
    assert fill_event.payload["slippage_quote"] == 1.0
    assert fill_event.payload["fee_rate_quote"] == 0.01
    assert fill_event.payload["fee_quote"] == 1.01
    assert result.portfolio.cash_quote == 897.99
    assert result.portfolio.positions[0].average_cost_quote == 101.0
    assert result.portfolio.ledger[0].source == "simulated"
    assert EventType.TRADE_FILL not in [event.event_type for event in result.events]


def test_simulated_fill_caps_notional_and_marks_partial_fill(tmp_path: Path) -> None:
    journal = AppendOnlyJournal(tmp_path / "observer.sqlite3")
    fill_model = SimulatedFillModel(schema_version="1.0.0", fee_rate_quote=0.0, slippage_bps=0.0, max_fill_notional_quote=75.0)
    runner = ObserverRunner(risk_engine=RiskEngine(_policy()), journal=journal, fill_model=fill_model)
    session_id = "session-5"
    market = normalize_market_snapshot(_snapshot("BTCUSDT", [100.0, 100.0, 100.0]))
    step = ObserverSessionStep(market=market, intent=_intent("trade-5", session_id, quantity=0.8))

    result = runner.run_session(session_id=session_id, steps=(step,), initial_state=_state(), initial_cash_quote=1000.0)
    fill_event = next(event for event in result.events if event.event_type == EventType.OBSERVER_FILL)

    assert fill_event.payload["fill_status"] == "partial"
    assert fill_event.payload["requested_quantity"] == 0.8
    assert fill_event.payload["filled_quantity"] == 0.75
    assert fill_event.payload["notional_quote"] == 75.0
    assert result.portfolio.positions[0].quantity == 0.75
    assert result.portfolio.ledger[0].position_qty_after == 0.75
