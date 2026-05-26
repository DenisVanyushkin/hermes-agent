from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from trading_autopilot import (
    AppendOnlyJournal,
    EventType,
    JournalEvent,
    MarketAnomaly,
    MarketRegime,
    MarketSnapshot,
    MarketTick,
    NormalizedMarketSnapshot,
    RiskDecision,
    RiskDecisionStatus,
    RiskEngine,
    RiskPolicy,
    RiskReason,
    RiskState,
    TradeIntent,
    TradeSide,
    normalize_market_snapshot,
)


def _normalized_snapshot(*, symbol: str = "BTCUSDT", regime: MarketRegime = MarketRegime.RANGING, anomalies: tuple[MarketAnomaly, ...] = ()) -> NormalizedMarketSnapshot:
    return NormalizedMarketSnapshot(
        schema_version="1.0.0",
        normalized_symbol=symbol,
        observed_at=datetime(2026, 5, 26, 10, 0, tzinfo=timezone.utc),
        bars=(),
        regime=regime,
        regime_reason="test",
        anomalies=anomalies,
        source_tick_count=2,
        normalized_tick_count=2,
        source_ticks=(),
    )


def _policy() -> RiskPolicy:
    return RiskPolicy(
        schema_version="1.0.0",
        allowed_symbols=("BTCUSDT",),
        max_order_notional_quote=100.0,
        max_position_notional_quote=250.0,
        max_drawdown_pct=0.20,
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


def _intent(notional: float, *, symbol: str = "BTCUSDT", side: TradeSide = TradeSide.BUY) -> TradeIntent:
    quantity = notional / 100.0
    return TradeIntent(
        schema_version="1.0.0",
        trade_id="trade-1",
        correlation_id="corr-1",
        symbol=symbol,
        side=side,
        order_type="limit",
        quantity=quantity,
        limit_price=100.0,
        strategy_id="fake-strategy",
        requested_at=datetime(2026, 5, 26, 10, 0, tzinfo=timezone.utc),
    )


def test_risk_engine_accepts_simple_valid_intent() -> None:
    engine = RiskEngine(_policy())
    decision = engine.evaluate(
        intent=_intent(50.0),
        market=_normalized_snapshot(),
        account_equity_quote=1000.0,
        current_position_notional_quote=0.0,
        state=_state(),
    )

    assert decision.status == RiskDecisionStatus.APPROVED
    assert decision.reasons == ()
    assert decision.next_state.cooldown_until is None


def test_risk_engine_rejects_stale_market_data_and_enters_cooldown() -> None:
    engine = RiskEngine(_policy())
    decision = engine.evaluate(
        intent=_intent(50.0),
        market=_normalized_snapshot(anomalies=(MarketAnomaly.STALE_DATA,)),
        account_equity_quote=1000.0,
        current_position_notional_quote=0.0,
        state=_state(),
    )

    assert decision.status == RiskDecisionStatus.DENIED
    assert RiskReason.STALE_MARKET_DATA in decision.reasons
    assert decision.next_state.cooldown_until is not None
    assert decision.next_state.cooldown_until > datetime(2026, 5, 26, 10, 0, tzinfo=timezone.utc)


def test_risk_engine_rejects_when_cooldown_is_active() -> None:
    engine = RiskEngine(_policy())
    active_state = RiskState(
        schema_version="1.0.0",
        cooldown_until=datetime(2026, 5, 26, 10, 1, tzinfo=timezone.utc),
        peak_equity_quote=1000.0,
        last_evaluated_at=datetime(2026, 5, 26, 10, 0, tzinfo=timezone.utc),
        last_decision_id="decision-1",
    )

    decision = engine.evaluate(
        intent=_intent(50.0),
        market=_normalized_snapshot(),
        account_equity_quote=1000.0,
        current_position_notional_quote=0.0,
        state=active_state,
    )

    assert decision.status == RiskDecisionStatus.DENIED
    assert decision.reasons == (RiskReason.COOLDOWN_ACTIVE,)


def test_risk_engine_rejects_oversized_orders() -> None:
    engine = RiskEngine(_policy())
    decision = engine.evaluate(
        intent=_intent(150.0),
        market=_normalized_snapshot(),
        account_equity_quote=1000.0,
        current_position_notional_quote=0.0,
        state=_state(),
    )

    assert decision.status == RiskDecisionStatus.DENIED
    assert RiskReason.EXCEEDS_MAX_ORDER_NOTIONAL in decision.reasons


def test_risk_decision_round_trips_through_journal(tmp_path: Path) -> None:
    engine = RiskEngine(_policy())
    decision = engine.evaluate(
        intent=_intent(50.0),
        market=_normalized_snapshot(),
        account_equity_quote=1000.0,
        current_position_notional_quote=0.0,
        state=_state(),
    )
    journal = AppendOnlyJournal(tmp_path / "journal.sqlite3")
    journal.append(decision.to_journal_event())

    replayed = journal.replay(event_types=(EventType.RISK_DECISION,)).events[0]
    reconstructed = RiskDecision.from_journal_event(replayed)

    assert reconstructed == decision


def test_risk_engine_rejects_regime_and_symbol_mismatch() -> None:
    engine = RiskEngine(_policy())
    decision = engine.evaluate(
        intent=_intent(50.0, symbol="ETHUSDT"),
        market=_normalized_snapshot(symbol="ETHUSDT", regime=MarketRegime.STALE),
        account_equity_quote=1000.0,
        current_position_notional_quote=0.0,
        state=_state(),
    )

    assert decision.status == RiskDecisionStatus.DENIED
    assert RiskReason.SYMBOL_NOT_ALLOWED in decision.reasons
    assert RiskReason.STALE_MARKET_DATA in decision.reasons
