from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from trading_autopilot import (
    AppendOnlyJournal,
    EventType,
    ObserverRunner,
    ObserverSessionStep,
    RiskEngine,
    RiskPolicy,
    RiskState,
    DeterministicStrategyProvider,
    normalize_market_snapshot,
)
from trading_autopilot.audit import (
    CURRENT_FILL_MODEL_VERSION,
    build_observer_audit_report,
    compare_observer_audit_reports,
    format_observer_audit_report,
    trace_observer_trade,
)
from trading_autopilot.observer import main as observer_main


def _snapshot(symbol: str = "BTCUSDT"):
    from trading_autopilot import MarketSnapshot, MarketTick

    return MarketSnapshot(
        symbol=symbol,
        observed_at=datetime(2026, 5, 26, 10, 0, tzinfo=timezone.utc),
        ticks=(
            MarketTick(symbol=symbol, observed_at=datetime(2026, 5, 26, 10, 0, 0, tzinfo=timezone.utc), price=100.0, volume=1.0, source="binance"),
            MarketTick(symbol=symbol, observed_at=datetime(2026, 5, 26, 10, 0, 1, tzinfo=timezone.utc), price=100.0, volume=1.0, source="binance"),
            MarketTick(symbol=symbol, observed_at=datetime(2026, 5, 26, 10, 0, 2, tzinfo=timezone.utc), price=100.0, volume=1.0, source="binance"),
        ),
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


def _session(tmp_path: Path, session_id: str = "session-1", cash: float = 1000.0):
    journal = AppendOnlyJournal(tmp_path / f"{session_id}.sqlite3")
    runner = ObserverRunner(
        risk_engine=RiskEngine(_policy()),
        journal=journal,
        strategy_provider=DeterministicStrategyProvider(),
    )
    step = ObserverSessionStep(market=normalize_market_snapshot(_snapshot()))
    runner.run_session(session_id=session_id, steps=(step,), initial_state=_state(), initial_cash_quote=cash)
    return journal


def _mutate_fill_model_version(journal_path: Path, version: str) -> None:
    with sqlite3.connect(journal_path) as conn:
        row = conn.execute(
            "SELECT event_id, payload_json FROM journal_events WHERE event_type = ? ORDER BY occurred_at ASC LIMIT 1",
            (EventType.OBSERVER_FILL.value,),
        ).fetchone()
        assert row is not None
        payload = json.loads(row[1])
        payload["fill_model_version"] = version
        conn.execute(
            "UPDATE journal_events SET payload_json = ? WHERE event_id = ?",
            (json.dumps(payload, sort_keys=True, separators=(",", ":")), row[0]),
        )
        conn.commit()


def test_audit_report_reconstructs_trade_and_is_deterministic(tmp_path: Path) -> None:
    journal = _session(tmp_path)

    report = build_observer_audit_report(journal, session_id="session-1")
    trace = trace_observer_trade(journal, trade_id=report.trade_traces[0].trade_id)
    formatted = format_observer_audit_report(report)

    assert report.deterministic is True
    assert report.version_warnings == ()
    assert report.mismatches == ()
    assert report.event_count == 6
    assert report.strategy_proposal_count == 1
    assert report.decision_count == 1
    assert trace.rationale == report.trade_traces[0].rationale
    assert trace.fill_model_version == CURRENT_FILL_MODEL_VERSION
    assert trace.proposal_event_id == report.trade_traces[0].proposal_event_id
    assert trace.fill_event_id == report.trade_traces[0].fill_event_id
    assert "\"replay_consistent\": true" in formatted
    assert "strategy.proposal" in formatted


def test_audit_report_flags_fill_semantics_version_mismatch(tmp_path: Path) -> None:
    journal = _session(tmp_path)
    _mutate_fill_model_version(journal.path, "0.9.0")

    report = build_observer_audit_report(journal, session_id="session-1")

    assert report.deterministic is True
    assert any("fill_model_version" in warning for warning in report.version_warnings)
    assert report.mismatches == ()


def test_compare_observer_runs_reports_portfolio_mismatch(tmp_path: Path) -> None:
    reference = build_observer_audit_report(_session(tmp_path, session_id="reference", cash=1000.0), session_id="reference")
    candidate = build_observer_audit_report(_session(tmp_path, session_id="candidate", cash=1500.0), session_id="candidate")

    comparison = compare_observer_audit_reports(reference, candidate)

    assert comparison.match is False
    assert any(mismatch.field == "portfolio.equity_quote" for mismatch in comparison.mismatches)


def test_observer_cli_replay_command_prints_replay_report(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    journal = _session(tmp_path)

    observer_main(["replay", "--journal-path", str(journal.path), "--session-id", "session-1"])
    payload = json.loads(capsys.readouterr().out)

    assert payload["session_id"] == "session-1"
    assert payload["replay_consistent"] is True
    assert payload["strategy_proposals"][0]["trade_id"]
    assert payload["trade_traces"][0]["fill_model_version"] == CURRENT_FILL_MODEL_VERSION


def test_observer_cli_compare_command_reports_mismatch(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    reference = _session(tmp_path, session_id="reference", cash=1000.0)
    candidate = _session(tmp_path, session_id="candidate", cash=1500.0)

    observer_main([
        "compare",
        "--reference-journal-path",
        str(reference.path),
        "--reference-session-id",
        "reference",
        "--candidate-journal-path",
        str(candidate.path),
        "--candidate-session-id",
        "candidate",
    ])
    payload = json.loads(capsys.readouterr().out)

    assert payload["match"] is False
    assert any(mismatch["field"] == "portfolio.equity_quote" if isinstance(mismatch, dict) else mismatch["field"] == "portfolio.equity_quote" for mismatch in payload["mismatches"])


def test_observer_cli_audit_command_prints_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    journal = _session(tmp_path)

    observer_main(["audit", "--journal-path", str(journal.path), "--session-id", "session-1"])
    payload = json.loads(capsys.readouterr().out)

    assert payload["session_id"] == "session-1"
    assert payload["deterministic"] is True
    assert payload["version_warnings"] == []
