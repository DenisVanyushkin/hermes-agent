from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Callable, Iterable, Iterator, Sequence

JOURNAL_SCHEMA_VERSION = "1.0.0"
JOURNAL_MIGRATION_NOTE = (
    "MVP journal backend is SQLite; preserve append/query/replay semantics and event schema "
    "so the storage layer can migrate to Postgres later without changing external APIs."
)


class JournalError(RuntimeError):
    """Base error for append-only journal operations."""


class JournalDuplicateEventError(JournalError):
    """Raised when an event with an existing event_id is appended."""


class JournalCorruptionError(JournalError):
    """Raised when a stored row cannot be parsed back into a canonical event."""


class EventType(StrEnum):
    MARKET_TICK = "market.tick"
    MARKET_SNAPSHOT = "market.snapshot"
    STRATEGY_PROPOSAL = "strategy.proposal"
    RISK_DECISION = "risk.decision"
    EXECUTION_INTENT = "execution.intent"
    ORDER_UPDATE = "order.update"
    TRADE_FILL = "trade.fill"
    CONTROL_COMMAND = "control.command"
    METRICS_SNAPSHOT = "metrics.snapshot"
    JOURNAL_MARKER = "journal.marker"
    OBSERVER_SESSION_START = "observer.session_start"
    OBSERVER_FILL = "observer.fill"
    SHADOW_PORTFOLIO_SNAPSHOT = "shadow.portfolio_snapshot"
    OBSERVER_SESSION_END = "observer.session_end"


@dataclass(frozen=True, slots=True)
class JournalEvent:
    event_id: str
    event_type: EventType
    schema_version: str
    source_module: str
    occurred_at: datetime
    correlation_id: str
    symbol: str | None = None
    trade_id: str | None = None
    payload: dict[str, object] | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type.value,
            "schema_version": self.schema_version,
            "source_module": self.source_module,
            "occurred_at": _to_utc_z(self.occurred_at),
            "correlation_id": self.correlation_id,
            "symbol": self.symbol,
            "trade_id": self.trade_id,
            "payload": self.payload or {},
        }

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "JournalEvent":
        try:
            payload = json.loads(row["payload_json"])
            if not isinstance(payload, dict):
                raise TypeError("payload_json must decode to an object")
            return cls(
                event_id=str(row["event_id"]),
                event_type=EventType(str(row["event_type"])),
                schema_version=str(row["schema_version"]),
                source_module=str(row["source_module"]),
                occurred_at=_from_utc_z(str(row["occurred_at"])),
                correlation_id=str(row["correlation_id"]),
                symbol=row["symbol"],
                trade_id=row["trade_id"],
                payload=payload,
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            event_id = None
            try:
                event_id = str(row["event_id"])
            except Exception:  # pragma: no cover - defensive
                pass
            raise JournalCorruptionError(
                f"Corrupt journal record {event_id or '<unknown>'}: {exc}"
            ) from exc


@dataclass(frozen=True, slots=True)
class ReplayContext:
    event_count: int
    journal_schema_version: str
    query: dict[str, object | None]


@dataclass(frozen=True, slots=True)
class ReplayResult:
    state: object
    events: tuple[JournalEvent, ...]
    context: ReplayContext


@dataclass(frozen=True, slots=True)
class _QuerySpec:
    event_types: tuple[EventType, ...] = ()
    trade_id: str | None = None
    symbol: str | None = None
    correlation_id: str | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    limit: int | None = None


class AppendOnlyJournal:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            self._ensure_schema(conn)
            yield conn
            conn.commit()
        finally:
            conn.close()

    def append(self, event: JournalEvent) -> bool:
        self._validate_event(event)
        payload_json = json.dumps(event.payload or {}, sort_keys=True, separators=(",", ":"))
        with self.connect() as conn:
            try:
                conn.execute(
                    """
                    INSERT INTO journal_events (
                        event_id,
                        event_type,
                        schema_version,
                        source_module,
                        occurred_at,
                        correlation_id,
                        symbol,
                        trade_id,
                        payload_json,
                        created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event.event_id,
                        event.event_type.value,
                        event.schema_version,
                        event.source_module,
                        _to_utc_z(event.occurred_at),
                        event.correlation_id,
                        event.symbol,
                        event.trade_id,
                        payload_json,
                        _to_utc_z(datetime.now(timezone.utc)),
                    ),
                )
                return True
            except sqlite3.IntegrityError as exc:
                raise JournalDuplicateEventError(f"duplicate event_id: {event.event_id}") from exc

    def query(
        self,
        *,
        event_types: Iterable[EventType] | None = None,
        trade_id: str | None = None,
        symbol: str | None = None,
        correlation_id: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        limit: int | None = None,
    ) -> list[JournalEvent]:
        spec = _QuerySpec(
            event_types=tuple(event_types or ()),
            trade_id=trade_id,
            symbol=symbol,
            correlation_id=correlation_id,
            start_time=start_time,
            end_time=end_time,
            limit=limit,
        )
        sql, params = self._build_query(spec)
        with self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [JournalEvent.from_row(row) for row in rows]

    def replay(
        self,
        *,
        reducer: Callable[[object, JournalEvent], object] | None = None,
        initial_state: object = None,
        event_types: Iterable[EventType] | None = None,
        trade_id: str | None = None,
        symbol: str | None = None,
        correlation_id: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        limit: int | None = None,
    ) -> ReplayResult:
        events = tuple(
            self.query(
                event_types=event_types,
                trade_id=trade_id,
                symbol=symbol,
                correlation_id=correlation_id,
                start_time=start_time,
                end_time=end_time,
                limit=limit,
            )
        )
        state = initial_state
        if reducer is not None:
            for event in events:
                state = reducer(state, event)
        context = ReplayContext(
            event_count=len(events),
            journal_schema_version=JOURNAL_SCHEMA_VERSION,
            query={
                "trade_id": trade_id,
                "correlation_id": correlation_id,
                "symbol": symbol,
            },
        )
        return ReplayResult(state=state, events=events, context=context)

    def _ensure_schema(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS journal_events (
                event_id TEXT PRIMARY KEY,
                event_type TEXT NOT NULL,
                schema_version TEXT NOT NULL,
                source_module TEXT NOT NULL,
                occurred_at TEXT NOT NULL,
                correlation_id TEXT NOT NULL,
                symbol TEXT,
                trade_id TEXT,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_journal_event_type ON journal_events(event_type)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_journal_symbol ON journal_events(symbol)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_journal_trade_id ON journal_events(trade_id)")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_journal_correlation_id ON journal_events(correlation_id)"
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_journal_occurred_at ON journal_events(occurred_at)")

    def _build_query(self, spec: _QuerySpec) -> tuple[str, tuple[object, ...]]:
        clauses: list[str] = []
        params: list[object] = []

        if spec.event_types:
            placeholders = ", ".join("?" for _ in spec.event_types)
            clauses.append(f"event_type IN ({placeholders})")
            params.extend(event_type.value for event_type in spec.event_types)
        if spec.trade_id is not None:
            clauses.append("trade_id = ?")
            params.append(spec.trade_id)
        if spec.symbol is not None:
            clauses.append("symbol = ?")
            params.append(spec.symbol)
        if spec.correlation_id is not None:
            clauses.append("correlation_id = ?")
            params.append(spec.correlation_id)
        if spec.start_time is not None:
            clauses.append("occurred_at >= ?")
            params.append(_to_utc_z(spec.start_time))
        if spec.end_time is not None:
            clauses.append("occurred_at <= ?")
            params.append(_to_utc_z(spec.end_time))

        sql = "SELECT * FROM journal_events"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY occurred_at ASC, rowid ASC"
        if spec.limit is not None:
            sql += " LIMIT ?"
            params.append(spec.limit)
        return sql, tuple(params)

    @staticmethod
    def _validate_event(event: JournalEvent) -> None:
        if not event.event_id:
            raise ValueError("event_id must be set")
        if not event.schema_version:
            raise ValueError("schema_version must be set")
        if not event.source_module:
            raise ValueError("source_module must be set")
        if not event.correlation_id:
            raise ValueError("correlation_id must be set")
        if event.occurred_at.tzinfo is None or event.occurred_at.utcoffset() is None:
            raise ValueError("occurred_at must be timezone-aware")
        if event.payload is not None and not isinstance(event.payload, dict):
            raise ValueError("payload must be a mapping")


def _to_utc_z(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _from_utc_z(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
