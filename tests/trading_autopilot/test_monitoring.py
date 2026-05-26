from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from trading_autopilot import (
    AppendOnlyJournal,
    DeterministicStrategyProvider,
    EventType,
    MarketSnapshot,
    MarketTick,
    MonitoringSignal,
    AnomalyThrottle,
    AlertSeverity,
    ObserverRunner,
    ObserverSessionStep,
    RiskEngine,
    RiskPolicy,
    RiskState,
    TradeIntent,
    TradeSide,
    build_observer_monitoring_report,
    format_observer_monitoring_report,
    normalize_market_snapshot,
)
from trading_autopilot.audit import build_observer_audit_report, compare_observer_audit_reports
from trading_autopilot.observer import main as observer_main


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


def _snapshot(*, stale: bool = False) -> MarketSnapshot:
    observed_at = datetime(2026, 5, 26, 10, 0, tzinfo=timezone.utc)
    if stale:
        tick_times = [
            datetime(2026, 5, 26, 9, 50, 0, tzinfo=timezone.utc),
            datetime(2026, 5, 26, 9, 50, 1, tzinfo=timezone.utc),
            datetime(2026, 5, 26, 9, 50, 2, tzinfo=timezone.utc),
        ]
    else:
        tick_times = [
            datetime(2026, 5, 26, 10, 0, 0, tzinfo=timezone.utc),
            datetime(2026, 5, 26, 10, 0, 1, tzinfo=timezone.utc),
            datetime(2026, 5, 26, 10, 0, 2, tzinfo=timezone.utc),
        ]
    return MarketSnapshot(
        symbol="BTCUSDT",
        observed_at=observed_at,
        ticks=tuple(
            MarketTick(symbol="BTCUSDT", observed_at=observed_at_ts, price=100.0, volume=1.0, source="binance")
            for observed_at_ts in tick_times
        ),
    )


def _intent(trade_id: str, session_id: str, *, quantity: float = 0.5) -> TradeIntent:
    return TradeIntent(
        schema_version="1.0.0",
        trade_id=trade_id,
        correlation_id=session_id,
        symbol="BTCUSDT",
        side=TradeSide.BUY,
        order_type="limit",
        quantity=quantity,
        limit_price=100.0,
        strategy_id="monitoring-demo",
        requested_at=datetime(2026, 5, 26, 10, 0, tzinfo=timezone.utc),
    )


def _run_session(tmp_path: Path, *, session_id: str, stale: bool = False, cash: float = 1000.0):
    journal = AppendOnlyJournal(tmp_path / f"{session_id}.sqlite3")
    runner = ObserverRunner(
        risk_engine=RiskEngine(_policy()),
        journal=journal,
        strategy_provider=DeterministicStrategyProvider(),
    )
    market = normalize_market_snapshot(_snapshot(stale=stale))
    step = ObserverSessionStep(market=market, intent=_intent(f"trade-{session_id}", session_id))
    runner.run_session(session_id=session_id, steps=(step,), initial_state=_state(), initial_cash_quote=cash)
    return journal


def test_monitoring_report_is_read_only_and_uses_journal_as_source_of_truth(tmp_path: Path) -> None:
    journal = _run_session(tmp_path, session_id="session-1")
    journal.append = lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("monitoring must be read-only"))  # type: ignore[method-assign]

    report = build_observer_monitoring_report(journal, session_id="session-1")
    formatted = format_observer_monitoring_report(report)

    assert report.read_only is True
    assert report.source_truth == "journal"
    assert report.operational_mode == "observer"
    assert report.live_order_path_enabled is False
    assert report.metrics[0].name == "event_count"
    assert report.event_count == len(journal.query(correlation_id="session-1"))
    assert report.alerts == ()
    assert report.mismatch_count == 0
    assert "\"read_only\": true" in formatted
    assert "\"source_truth\": \"journal\"" in formatted


def test_monitoring_report_emits_stale_data_alert(tmp_path: Path) -> None:
    journal = _run_session(tmp_path, session_id="stale-session", stale=True)

    report = build_observer_monitoring_report(journal, session_id="stale-session")

    assert any(alert.code == "stale-data" for alert in report.alerts)
    stale_alert = next(alert for alert in report.alerts if alert.code == "stale-data")
    assert stale_alert.severity == AlertSeverity.WARNING
    assert stale_alert.throttled is False
    assert report.metrics_by_name()["stale_data_count"].value == 1


def test_monitoring_report_surfaces_replay_mismatch_visibility(tmp_path: Path) -> None:
    reference = _run_session(tmp_path, session_id="reference", cash=1000.0)
    candidate = _run_session(tmp_path, session_id="candidate", cash=1500.0)

    reference_report = build_observer_audit_report(reference, session_id="reference")
    candidate_report = build_observer_audit_report(candidate, session_id="candidate")
    comparison = compare_observer_audit_reports(reference_report, candidate_report)

    report = build_observer_monitoring_report(
        candidate,
        session_id="candidate",
        comparison_report=comparison,
    )

    assert report.mismatch_count > 0
    assert report.replay_consistent is True
    assert any(alert.code == "comparison-mismatch" for alert in report.alerts)
    mismatch_alert = next(alert for alert in report.alerts if alert.code == "comparison-mismatch")
    assert mismatch_alert.severity == AlertSeverity.CRITICAL
    assert mismatch_alert.bypassed_throttle is True


def test_anomaly_throttle_suppresses_duplicate_warning_alerts_but_bypasses_critical_alerts() -> None:
    throttle = AnomalyThrottle(window_seconds=300)
    base_time = datetime(2026, 5, 26, 10, 0, tzinfo=timezone.utc)
    warning_signals = [
        MonitoringSignal(
            observed_at=base_time,
            severity=AlertSeverity.WARNING,
            code="stale-data",
            title="Stale data",
            message="stale market data",
            source="journal",
            fingerprint="warning-fingerprint",
        ),
        MonitoringSignal(
            observed_at=base_time.replace(minute=1),
            severity=AlertSeverity.WARNING,
            code="stale-data",
            title="Stale data",
            message="stale market data",
            source="journal",
            fingerprint="warning-fingerprint",
        ),
    ]
    critical_signals = [
        MonitoringSignal(
            observed_at=base_time,
            severity=AlertSeverity.CRITICAL,
            code="replay-mismatch",
            title="Replay mismatch",
            message="critical mismatch",
            source="replay",
            critical_bypass=True,
            fingerprint="critical-fingerprint",
        ),
        MonitoringSignal(
            observed_at=base_time.replace(minute=1),
            severity=AlertSeverity.CRITICAL,
            code="replay-mismatch",
            title="Replay mismatch",
            message="critical mismatch",
            source="replay",
            critical_bypass=True,
            fingerprint="critical-fingerprint",
        ),
    ]

    alerts = throttle.throttle([*warning_signals, *critical_signals])
    warning_alerts = [alert for alert in alerts if alert.code == "stale-data"]
    critical_alerts = [alert for alert in alerts if alert.code == "replay-mismatch"]

    assert len(warning_alerts) == 1
    assert warning_alerts[0].occurrence_count == 2
    assert warning_alerts[0].suppressed_count == 1
    assert warning_alerts[0].throttled is True
    assert len(critical_alerts) == 2
    assert all(alert.bypassed_throttle is True for alert in critical_alerts)
    assert all(alert.throttled is False for alert in critical_alerts)


def test_monitoring_cli_monitor_command_outputs_json(tmp_path: Path, capsys) -> None:
    journal = _run_session(tmp_path, session_id="cli-session")

    observer_main(["monitor", "--journal-path", str(journal.path), "--session-id", "cli-session"])
    payload = json.loads(capsys.readouterr().out)

    assert payload["session_id"] == "cli-session"
    assert payload["read_only"] is True
    assert payload["source_truth"] == "journal"
    assert payload["operational_mode"] == "observer"
