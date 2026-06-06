from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

from trading_autopilot import (
    AppendOnlyJournal,
    EventType,
    CANONICAL_JOURNAL_SCHEMA_VERSION,
    CANONICAL_JOURNAL_SOURCE_MODULE,
    DAILY_MARKET_STATE_BRIEF_SCHEMA_VERSION,
    DailyMarketStateBriefReport,
    ReplayValidationReport,
    build_daily_market_state_brief,
    build_replay_validation_report,
    format_replay_validation_report,
)
from trading_autopilot.journal import JournalEvent


def _record(
    event_id: str,
    *,
    symbol: str,
    source: str,
    observed_at: datetime,
    collected_at: datetime,
    price: float,
    volume: float,
    quote_volume: float,
    event_type: EventType = EventType.MARKET_TICK,
    schema_version: str = CANONICAL_JOURNAL_SCHEMA_VERSION,
    payload_overrides: dict[str, object] | None = None,
) -> JournalEvent:
    payload: dict[str, object] = {
        "symbol": symbol,
        "source": source,
        "observed_at": observed_at.isoformat().replace("+00:00", "Z"),
        "collected_at": collected_at.isoformat().replace("+00:00", "Z"),
        "price": price,
        "volume": volume,
        "quote_volume": quote_volume,
    }
    if payload_overrides:
        payload.update(payload_overrides)
    return JournalEvent(
        event_id=event_id,
        event_type=event_type,
        schema_version=schema_version,
        source_module=CANONICAL_JOURNAL_SOURCE_MODULE,
        occurred_at=observed_at,
        correlation_id="market-replay",
        symbol=symbol,
        trade_id=None,
        payload=payload,
    )


def _brief_event(event_id: str, brief: DailyMarketStateBriefReport, *, schema_version: str = DAILY_MARKET_STATE_BRIEF_SCHEMA_VERSION) -> JournalEvent:
    return JournalEvent(
        event_id=event_id,
        event_type=EventType.MARKET_STATE_BRIEF,
        schema_version=schema_version,
        source_module=CANONICAL_JOURNAL_SOURCE_MODULE,
        occurred_at=brief.generated_at,
        correlation_id="market-replay",
        symbol=None,
        trade_id=None,
        payload=brief.to_dict(),
    )


def test_replay_validation_uses_observed_at_for_freshness(tmp_path: Path) -> None:
    journal = AppendOnlyJournal(tmp_path / "replay.sqlite3")
    generated_at = datetime(2026, 5, 31, 12, 45, tzinfo=timezone.utc)

    journal.append(
        _record(
            "evt-1",
            symbol="BTCUSDT",
            source="binance.spot",
            observed_at=datetime(2026, 5, 31, 12, 0, tzinfo=timezone.utc),
            collected_at=datetime(2026, 5, 31, 12, 44, tzinfo=timezone.utc),
            price=101500.0,
            volume=12.0,
            quote_volume=1218000.0,
        )
    )
    report = build_replay_validation_report(journal, generated_at=generated_at)

    assert report.replay_consistent is True
    assert report.reconstructed_state["source_statuses"]["binance.spot"]["freshness_basis"] == "observed_at"
    assert report.reconstructed_state["source_statuses"]["binance.spot"]["age_minutes"] == 45.0
    assert report.reconstructed_state["source_statuses"]["binance.spot"]["status"] == "stale"


def test_replay_validation_reconstructs_brief_and_is_deterministic(tmp_path: Path) -> None:
    journal = AppendOnlyJournal(tmp_path / "replay.sqlite3")
    generated_at = datetime(2026, 5, 31, 12, 45, tzinfo=timezone.utc)

    journal.append(
        _record(
            "evt-1",
            symbol="BTCUSDT",
            source="binance.spot",
            observed_at=datetime(2026, 5, 31, 12, 0, tzinfo=timezone.utc),
            collected_at=datetime(2026, 5, 31, 12, 0, 2, tzinfo=timezone.utc),
            price=101500.0,
            volume=12.0,
            quote_volume=1218000.0,
        )
    )
    journal.append(
        _record(
            "evt-2",
            symbol="ETHUSDT",
            source="coinbase.spot",
            observed_at=datetime(2026, 5, 31, 12, 5, tzinfo=timezone.utc),
            collected_at=datetime(2026, 5, 31, 12, 5, 3, tzinfo=timezone.utc),
            price=2400.0,
            volume=55.0,
            quote_volume=132000.0,
        )
    )

    brief = build_daily_market_state_brief(journal, generated_at=generated_at)
    journal.append(_brief_event("evt-brief", brief))

    first = build_replay_validation_report(journal, generated_at=generated_at)
    second = build_replay_validation_report(journal, generated_at=generated_at)
    formatted = format_replay_validation_report(first)

    assert first.replay_consistent is True
    assert first.reproducible is True
    assert first.state_fingerprint == second.state_fingerprint
    assert first.replay_fingerprint == second.replay_fingerprint
    assert first.reconstructed_brief.to_dict() == brief.to_dict()
    assert first.reconstructed_state["latest_by_symbol"]["BTCUSDT"]["source"] == "binance.spot"
    assert first.reconstructed_state["latest_by_symbol"]["ETHUSDT"]["source"] == "coinbase.spot"
    assert "freshness_basis" in formatted
    assert "observed_at" in formatted


def test_replay_validation_flags_mismatch_and_schema_drift(tmp_path: Path) -> None:
    journal = AppendOnlyJournal(tmp_path / "replay.sqlite3")
    generated_at = datetime(2026, 5, 31, 12, 45, tzinfo=timezone.utc)

    journal.append(
        _record(
            "evt-1",
            symbol="BTCUSDT",
            source="binance.spot",
            observed_at=datetime(2026, 5, 31, 12, 0, tzinfo=timezone.utc),
            collected_at=datetime(2026, 5, 31, 12, 0, 2, tzinfo=timezone.utc),
            price=101500.0,
            volume=12.0,
            quote_volume=1218000.0,
        )
    )
    brief = build_daily_market_state_brief(journal, generated_at=generated_at)
    mutated_payload = brief.to_dict()
    mutated_payload["assets"][0]["price"] = 99999.0
    journal.append(
        JournalEvent(
            event_id="evt-brief",
            event_type=EventType.MARKET_STATE_BRIEF,
            schema_version="0.9.0",
            source_module=CANONICAL_JOURNAL_SOURCE_MODULE,
            occurred_at=generated_at,
            correlation_id="market-replay",
            symbol=None,
            trade_id=None,
            payload=mutated_payload,
        )
    )

    report = build_replay_validation_report(journal, generated_at=generated_at)

    assert report.replay_consistent is False
    assert any(mismatch.field == "daily_market_state_brief.assets[0].price" for mismatch in report.mismatches)
    assert any("schema drift" in warning for warning in report.version_warnings)
    assert any("market.state_brief" in warning for warning in report.version_warnings)


def test_replay_validation_report_round_trips_through_json(tmp_path: Path) -> None:
    journal = AppendOnlyJournal(tmp_path / "replay.sqlite3")
    generated_at = datetime(2026, 5, 31, 12, 45, tzinfo=timezone.utc)
    journal.append(
        _record(
            "evt-1",
            symbol="BTCUSDT",
            source="binance.spot",
            observed_at=datetime(2026, 5, 31, 12, 0, tzinfo=timezone.utc),
            collected_at=datetime(2026, 5, 31, 12, 0, 2, tzinfo=timezone.utc),
            price=101500.0,
            volume=12.0,
            quote_volume=1218000.0,
        )
    )

    report = build_replay_validation_report(journal, generated_at=generated_at)
    payload = json.loads(format_replay_validation_report(report))

    assert payload["schema_version"] == report.schema_version
    assert payload["replay_consistent"] is True
    assert payload["reconstructed_brief"]["schema_version"] == DAILY_MARKET_STATE_BRIEF_SCHEMA_VERSION
