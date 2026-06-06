from __future__ import annotations

from datetime import datetime, timedelta, timezone

from trading_autopilot import (
    CANONICAL_JOURNAL_SOURCE_MODULE,
    CanonicalJournal,
    CanonicalJournalRecord,
    EventType,
)


def _append_observation(journal: CanonicalJournal, *, event_id: str, source: str, symbol: str, observed_at: datetime, price: float) -> None:
    journal.append_record(
        CanonicalJournalRecord(
            event_id=event_id,
            event_type=EventType.MARKET_SNAPSHOT,
            source_module=CANONICAL_JOURNAL_SOURCE_MODULE,
            observed_at=observed_at,
            collected_at=observed_at + timedelta(minutes=2),
            correlation_id="pilot-session",
            symbol=symbol,
            payload={
                "source": source,
                "symbol": symbol,
                "observed_at": observed_at.isoformat().replace("+00:00", "Z"),
                "collected_at": (observed_at + timedelta(minutes=2)).isoformat().replace("+00:00", "Z"),
                "price": price,
                "volume": 123.45,
                "quote_volume": 6789.01,
            },
        )
    )


def test_pilot_runner_generates_status_report_and_alerts(tmp_path):
    from trading_autopilot.pilot_operation import PilotRunner, build_pilot_operation_report

    journal = CanonicalJournal(tmp_path / "pilot.sqlite3")
    generated_at = datetime(2026, 5, 31, 12, 0, tzinfo=timezone.utc)
    _append_observation(journal, event_id="obs-1", source="binance.spot", symbol="BTC", observed_at=generated_at - timedelta(minutes=10), price=100.0)
    _append_observation(journal, event_id="obs-2", source="binance.futures", symbol="BTC", observed_at=generated_at - timedelta(minutes=50), price=101.0)
    _append_observation(journal, event_id="obs-3", source="coinbase.spot", symbol="ETH", observed_at=generated_at - timedelta(minutes=5), price=200.0)

    runner = PilotRunner(journal)
    run_report = runner.run_once(generated_at=generated_at)

    assert run_report.read_only is True
    assert run_report.operational_mode == "observer-only"
    assert run_report.last_successful_run_at == generated_at
    assert run_report.report_generation_status == "ok"
    assert run_report.replay_validation_status == "consistent"
    assert any(item.status == "stale" for item in run_report.source_freshness)
    assert run_report.metrics_by_name()["reports_generated"].value == 1
    assert run_report.metrics_by_name()["alerts_generated"].value >= 1
    assert run_report.metrics_by_name()["stale_source_incidents"].value >= 1

    stored_briefs = journal.query(event_types=[EventType.MARKET_STATE_BRIEF])
    stored_alerts = journal.query(event_types=[EventType.CRITICAL_ALERT])
    assert len(stored_briefs) == 1
    assert len(stored_alerts) >= 1

    status_report = build_pilot_operation_report(journal, generated_at=generated_at)
    assert status_report.last_successful_run_at == generated_at
    assert status_report.replay_validation_status == "consistent"
    assert status_report.recent_briefs
    assert status_report.recent_alerts
    assert status_report.recent_alerts[0].code in {"source.stale", "replay.mismatch", "report.failure", "source.missing"}


def test_pilot_summary_output_is_concise_and_inspectable(tmp_path):
    from trading_autopilot.pilot_operation import PilotRunner, format_pilot_operation_report

    journal = CanonicalJournal(tmp_path / "pilot.sqlite3")
    generated_at = datetime(2026, 5, 31, 12, 0, tzinfo=timezone.utc)
    _append_observation(journal, event_id="obs-1", source="binance.spot", symbol="BTC", observed_at=generated_at - timedelta(minutes=10), price=100.0)
    _append_observation(journal, event_id="obs-2", source="coinbase.spot", symbol="ETH", observed_at=generated_at - timedelta(minutes=5), price=200.0)

    report = PilotRunner(journal).run_once(generated_at=generated_at)
    rendered = format_pilot_operation_report(report)

    assert len(rendered.splitlines()) <= 15
    assert "Pilot Operation Status" in rendered
    assert "Last successful run" in rendered
    assert "Replay validation" in rendered
    assert "Recent briefs" in rendered
    assert "Recent alerts" in rendered
    assert "observer-only" in rendered


def test_pilot_run_preserves_observer_only_boundary(tmp_path):
    from trading_autopilot.pilot_operation import PilotRunner

    journal = CanonicalJournal(tmp_path / "pilot.sqlite3")
    generated_at = datetime(2026, 5, 31, 12, 0, tzinfo=timezone.utc)
    _append_observation(journal, event_id="obs-1", source="binance.spot", symbol="BTC", observed_at=generated_at - timedelta(minutes=10), price=100.0)

    report = PilotRunner(journal).run_once(generated_at=generated_at)
    event_types = {event.event_type for event in journal.query()}

    assert EventType.STRATEGY_PROPOSAL not in event_types
    assert EventType.RISK_DECISION not in event_types
    assert EventType.EXECUTION_INTENT not in event_types
    assert EventType.ORDER_UPDATE not in event_types
    assert EventType.TRADE_FILL not in event_types


def test_package_main_dispatches_pilot_subcommand(monkeypatch):
    import trading_autopilot.__main__ as module

    captured = {}

    def fake_pilot_main(argv):
        captured["argv"] = list(argv)
        return 0

    monkeypatch.setattr(module, "pilot_main", fake_pilot_main)
    monkeypatch.setattr(module, "runtime_main", lambda: (_ for _ in ()).throw(AssertionError("runtime_main should not run")))
    monkeypatch.setattr(module.sys, "argv", ["trading_autopilot", "pilot-status", "--journal", "/tmp/pilot.sqlite3"])

    try:
        module.main()
    except SystemExit as exc:
        assert exc.code == 0
    assert captured["argv"][0] == "pilot-status"
    assert captured["argv"][2] == "/tmp/pilot.sqlite3"
