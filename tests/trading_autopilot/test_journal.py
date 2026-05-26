from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from trading_autopilot.journal import (
    AppendOnlyJournal,
    EventType,
    JournalCorruptionError,
    JournalDuplicateEventError,
    JournalEvent,
    ReplayContext,
)


def _event(
    event_id: str,
    event_type: EventType,
    occurred_at: str,
    *,
    symbol: str | None = None,
    trade_id: str | None = None,
    correlation_id: str = "corr-1",
    payload: dict[str, object] | None = None,
) -> JournalEvent:
    return JournalEvent(
        event_id=event_id,
        event_type=event_type,
        schema_version="1.0.0",
        source_module="strategy_layer",
        occurred_at=datetime.fromisoformat(occurred_at.replace("Z", "+00:00")),
        correlation_id=correlation_id,
        symbol=symbol,
        trade_id=trade_id,
        payload=payload or {},
    )


def test_event_types_have_canonical_values() -> None:
    assert EventType.MARKET_TICK.value == "market.tick"
    assert EventType.TRADE_FILL.value == "trade.fill"
    assert EventType.CONTROL_COMMAND.value == "control.command"


def test_append_query_and_replay_reconstruction(tmp_path: Path) -> None:
    journal = AppendOnlyJournal(tmp_path / "journal.sqlite3")
    first = _event(
        "evt-1",
        EventType.MARKET_TICK,
        "2026-05-26T10:00:00Z",
        symbol="BTCUSDT",
        payload={"price": 100.0, "volume": 1.2},
    )
    second = _event(
        "evt-2",
        EventType.STRATEGY_PROPOSAL,
        "2026-05-26T10:00:01Z",
        symbol="BTCUSDT",
        trade_id="trade-7",
        payload={"side": "buy", "qty": 0.01},
    )
    third = _event(
        "evt-3",
        EventType.TRADE_FILL,
        "2026-05-26T10:00:02Z",
        symbol="BTCUSDT",
        trade_id="trade-7",
        payload={"filled_qty": 0.01, "fill_price": 100.5},
    )

    journal.append(first)
    journal.append(second)
    journal.append(third)

    symbol_window = journal.query(symbol="BTCUSDT", start_time=first.occurred_at, end_time=second.occurred_at)
    assert [event.event_id for event in symbol_window] == ["evt-1", "evt-2"]

    trade_window = journal.query(trade_id="trade-7", correlation_id="corr-1")
    assert [event.event_id for event in trade_window] == ["evt-2", "evt-3"]

    reconstructed = journal.replay(
        trade_id="trade-7",
        reducer=lambda state, event: {
            **state,
            "events": state["events"] + [event.event_type.value],
            "last_payload": event.payload,
        },
        initial_state={"events": [], "last_payload": {}},
    )
    assert reconstructed.state == {
        "events": ["strategy.proposal", "trade.fill"],
        "last_payload": {"filled_qty": 0.01, "fill_price": 100.5},
    }
    assert [event.event_id for event in reconstructed.events] == ["evt-2", "evt-3"]
    assert reconstructed.context == ReplayContext(
        event_count=2,
        journal_schema_version="1.0.0",
        query={"trade_id": "trade-7", "correlation_id": None, "symbol": None},
    )


def test_duplicate_event_ids_are_rejected(tmp_path: Path) -> None:
    journal = AppendOnlyJournal(tmp_path / "journal.sqlite3")
    event = _event("evt-dup", EventType.MARKET_TICK, "2026-05-26T10:00:00Z", symbol="BTCUSDT")

    assert journal.append(event) is True
    with pytest.raises(JournalDuplicateEventError, match="duplicate event_id"):
        journal.append(event)


def test_corrupt_record_raises_on_query(tmp_path: Path) -> None:
    journal = AppendOnlyJournal(tmp_path / "journal.sqlite3")
    event = _event("evt-good", EventType.MARKET_TICK, "2026-05-26T10:00:00Z", symbol="BTCUSDT")
    journal.append(event)

    with journal.connect() as conn:
        conn.execute(
            "INSERT INTO journal_events (event_id, event_type, schema_version, source_module, occurred_at, correlation_id, symbol, trade_id, payload_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "evt-corrupt",
                "market.tick",
                "1.0.0",
                "strategy_layer",
                "not-a-timestamp",
                "corr-1",
                "BTCUSDT",
                None,
                "{broken json",
                "2026-05-26T10:00:00Z",
            ),
        )

    with pytest.raises(JournalCorruptionError, match="evt-corrupt"):
        journal.query(symbol="BTCUSDT")


def test_query_can_filter_by_event_type_and_time_window(tmp_path: Path) -> None:
    journal = AppendOnlyJournal(tmp_path / "journal.sqlite3")
    journal.append(_event("evt-1", EventType.MARKET_TICK, "2026-05-26T10:00:00Z", symbol="ETHUSDT"))
    journal.append(_event("evt-2", EventType.CONTROL_COMMAND, "2026-05-26T10:05:00Z", payload={"command": "pause"}))
    journal.append(_event("evt-3", EventType.CONTROL_COMMAND, "2026-05-26T10:10:00Z", payload={"command": "resume"}))

    results = journal.query(
        event_types={EventType.CONTROL_COMMAND},
        start_time=datetime(2026, 5, 26, 10, 4, tzinfo=timezone.utc),
        end_time=datetime(2026, 5, 26, 10, 7, tzinfo=timezone.utc),
    )

    assert [event.event_id for event in results] == ["evt-2"]
    assert results[0].payload == {"command": "pause"}
