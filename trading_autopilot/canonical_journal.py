from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator, Sequence

from .journal import AppendOnlyJournal, EventType, JournalEvent, ReplayContext, ReplayResult

CANONICAL_JOURNAL_SCHEMA_VERSION = "1.0.0"
CANONICAL_JOURNAL_SOURCE_MODULE = "trading_autopilot.canonical_journal"


def _fmt_dt(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _stable_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _event_id_for_record(record: "CanonicalJournalRecord") -> str:
    payload = {
        "event_type": record.event_type.value,
        "schema_version": record.schema_version,
        "source_module": record.source_module,
        "symbol": record.symbol,
        "trade_id": record.trade_id,
        "correlation_id": record.correlation_id,
        "observed_at": _fmt_dt(record.observed_at),
        "collected_at": _fmt_dt(record.collected_at),
        "payload": record.payload,
    }
    digest = hashlib.sha256(_stable_json(payload).encode("utf-8")).hexdigest()[:24]
    return f"cj-{digest}"


@dataclass(frozen=True, slots=True)
class CanonicalJournalRecord:
    event_type: EventType
    observed_at: datetime
    collected_at: datetime
    correlation_id: str
    schema_version: str = CANONICAL_JOURNAL_SCHEMA_VERSION
    source_module: str = CANONICAL_JOURNAL_SOURCE_MODULE
    symbol: str | None = None
    trade_id: str | None = None
    payload: dict[str, object] = field(default_factory=dict)
    event_id: str = ""

    def __post_init__(self) -> None:
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("observed_at must be timezone-aware")
        if self.collected_at.tzinfo is None or self.collected_at.utcoffset() is None:
            raise ValueError("collected_at must be timezone-aware")
        if not self.correlation_id:
            raise ValueError("correlation_id must be set")
        if not self.schema_version:
            raise ValueError("schema_version must be set")
        if not self.source_module:
            raise ValueError("source_module must be set")
        if self.payload is None:
            object.__setattr__(self, "payload", {})
        elif not isinstance(self.payload, dict):
            raise ValueError("payload must be a mapping")
        if not self.event_id:
            object.__setattr__(self, "event_id", _event_id_for_record(self))

    @property
    def occurred_at(self) -> datetime:
        return self.observed_at

    def to_event(self) -> JournalEvent:
        payload = dict(self.payload)
        payload.setdefault("observed_at", _fmt_dt(self.observed_at))
        payload.setdefault("collected_at", _fmt_dt(self.collected_at))
        payload.setdefault("schema_version", self.schema_version)
        payload.setdefault("source_module", self.source_module)
        return JournalEvent(
            event_id=self.event_id,
            event_type=self.event_type,
            schema_version=self.schema_version,
            source_module=self.source_module,
            occurred_at=self.observed_at,
            correlation_id=self.correlation_id,
            symbol=self.symbol,
            trade_id=self.trade_id,
            payload=payload,
        )

    @classmethod
    def from_event(cls, event: JournalEvent) -> "CanonicalJournalRecord":
        payload = dict(event.payload or {})
        observed_at = _parse_dt(str(payload.get("observed_at") or event.occurred_at.isoformat()))
        collected_at_raw = payload.get("collected_at")
        collected_at = _parse_dt(str(collected_at_raw)) if collected_at_raw is not None else observed_at
        return cls(
            event_id=event.event_id,
            event_type=event.event_type,
            schema_version=event.schema_version,
            source_module=event.source_module,
            observed_at=observed_at,
            collected_at=collected_at,
            correlation_id=event.correlation_id,
            symbol=event.symbol,
            trade_id=event.trade_id,
            payload={k: v for k, v in payload.items() if k not in {"observed_at", "collected_at", "schema_version", "source_module"}},
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type.value,
            "schema_version": self.schema_version,
            "source_module": self.source_module,
            "observed_at": _fmt_dt(self.observed_at),
            "collected_at": _fmt_dt(self.collected_at),
            "correlation_id": self.correlation_id,
            "symbol": self.symbol,
            "trade_id": self.trade_id,
            "payload": self.payload,
        }


class CanonicalJournal:
    def __init__(self, path: str | Path | AppendOnlyJournal):
        if isinstance(path, AppendOnlyJournal):
            self._journal = path
        else:
            self._journal = AppendOnlyJournal(path)

    @property
    def path(self) -> Path:
        return self._journal.path

    def append_record(self, record: CanonicalJournalRecord) -> bool:
        return self._journal.append(record.to_event())

    def append_records(self, records: Iterable[CanonicalJournalRecord]) -> int:
        count = 0
        for record in records:
            self.append_record(record)
            count += 1
        return count

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
    ) -> list[CanonicalJournalRecord]:
        events = self._journal.query(
            event_types=event_types,
            trade_id=trade_id,
            symbol=symbol,
            correlation_id=correlation_id,
            start_time=start_time,
            end_time=end_time,
            limit=limit,
        )
        return [CanonicalJournalRecord.from_event(event) for event in events if event.source_module == CANONICAL_JOURNAL_SOURCE_MODULE]

    def replay(
        self,
        *,
        reducer=None,
        initial_state: object = None,
        event_types: Iterable[EventType] | None = None,
        trade_id: str | None = None,
        symbol: str | None = None,
        correlation_id: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        limit: int | None = None,
    ) -> ReplayResult:
        events = self._journal.query(
            event_types=event_types,
            trade_id=trade_id,
            symbol=symbol,
            correlation_id=correlation_id,
            start_time=start_time,
            end_time=end_time,
            limit=limit,
        )
        filtered = [event for event in events if event.source_module == CANONICAL_JOURNAL_SOURCE_MODULE]
        state = initial_state
        if reducer is not None:
            for event in filtered:
                state = reducer(state, event)
        context = ReplayContext(
            event_count=len(filtered),
            journal_schema_version=CANONICAL_JOURNAL_SCHEMA_VERSION,
            query={
                "trade_id": trade_id,
                "correlation_id": correlation_id,
                "symbol": symbol,
            },
        )
        return ReplayResult(state=state, events=tuple(filtered), context=context)
