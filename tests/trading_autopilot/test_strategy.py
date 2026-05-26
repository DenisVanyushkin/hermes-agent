from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

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
    StrategyAction,
    StrategyProposal,
    StrategyProposalValidationError,
    StrategyRunContext,
    ShadowLLMStrategyProvider,
    TradeIntent,
    TradeSide,
    normalize_market_snapshot,
)


def _snapshot(symbol: str = "BTCUSDT") -> MarketSnapshot:
    return MarketSnapshot(
        symbol=symbol,
        observed_at=datetime(2026, 5, 26, 10, 0, tzinfo=timezone.utc),
        ticks=(
            MarketTick(symbol=symbol, observed_at=datetime(2026, 5, 26, 10, 0, 0, tzinfo=timezone.utc), price=100.0, volume=1.0, source="binance"),
            MarketTick(symbol=symbol, observed_at=datetime(2026, 5, 26, 10, 0, 1, tzinfo=timezone.utc), price=101.0, volume=1.0, source="binance"),
            MarketTick(symbol=symbol, observed_at=datetime(2026, 5, 26, 10, 0, 2, tzinfo=timezone.utc), price=102.0, volume=1.0, source="binance"),
        ),
    )


def _context() -> StrategyRunContext:
    return StrategyRunContext(
        schema_version="1.0.0",
        session_id="session-1",
        correlation_id="session-1",
        strategy_id="mean-reversion-shadow",
        strategy_version="2.1.0",
        prompt_version="2026-05-26a",
        model_name="gpt-5.5",
        model_version="xhigh",
        normalization_version="1.0.0",
        risk_version="1.0.0",
        market=normalize_market_snapshot(_snapshot()),
        portfolio_cash_quote=1000.0,
        portfolio_equity_quote=1000.0,
        position_notional_quote=0.0,
    )


def test_strategy_proposal_roundtrips_with_version_metadata() -> None:
    proposal = StrategyProposal.from_raw(
        {
            "action": "buy",
            "order_type": "limit",
            "quantity": 0.5,
            "limit_price": 100.0,
            "rationale": "shadow demo",
            "confidence": 0.73,
        },
        context=_context(),
    )

    event = proposal.to_journal_event()
    restored = StrategyProposal.from_journal_event(event)

    assert restored == proposal
    assert event.event_type == EventType.STRATEGY_PROPOSAL
    assert restored.prompt_version == "2026-05-26a"
    assert restored.model_name == "gpt-5.5"
    assert restored.model_version == "xhigh"


def test_shadow_llm_strategy_provider_rejects_malformed_output() -> None:
    provider = ShadowLLMStrategyProvider(lambda context: {"action": "buy", "quantity": "oops"})

    with pytest.raises(StrategyProposalValidationError):
        provider.propose(_context())


def test_observer_journals_strategy_proposal_and_replays_without_recalling_provider(tmp_path: Path) -> None:
    class CountingProvider:
        def __init__(self) -> None:
            self.calls = 0
            self.source_kind = "llm"
            self.strategy_id = "llm-shadow-strategy"
            self.strategy_version = "2.0.0"
            self.prompt_version = "2026-05-26a"
            self.model_name = "gpt-5.5"
            self.model_version = "xhigh"

        def propose(self, context: StrategyRunContext):
            self.calls += 1
            return {
                "action": "buy",
                "order_type": "limit",
                "quantity": 0.5,
                "limit_price": 100.0,
                "rationale": "shadow buy",
                "confidence": 0.9,
            }

    provider = CountingProvider()
    journal = AppendOnlyJournal(tmp_path / "strategy-shadow.sqlite3")
    runner = ObserverRunner(risk_engine=RiskEngine(_policy()), journal=journal, strategy_provider=provider)
    step = ObserverSessionStep(market=normalize_market_snapshot(_snapshot()))

    result = runner.run_session(session_id="session-1", steps=(step,), initial_state=_state(), initial_cash_quote=1000.0)
    replay = runner.replay_session("session-1")

    assert provider.calls == 1
    assert result.strategy_proposals == replay.strategy_proposals
    assert result.strategy_proposals[0].action == StrategyAction.BUY
    assert any(event.event_type == EventType.STRATEGY_PROPOSAL for event in result.events)
    assert result.risk_decisions[0].status == RiskDecisionStatus.APPROVED
    assert replay.risk_decisions[0].status == RiskDecisionStatus.APPROVED
    assert journal.query(event_types=(EventType.STRATEGY_PROPOSAL,))[0].payload["model_name"] == "gpt-5.5"


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
