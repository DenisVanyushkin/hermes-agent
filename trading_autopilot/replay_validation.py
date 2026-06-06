from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

from .canonical_journal import CANONICAL_JOURNAL_SCHEMA_VERSION, CANONICAL_JOURNAL_SOURCE_MODULE, CanonicalJournal
from .daily_market_state_brief import (
    DAILY_MARKET_STATE_BRIEF_SCHEMA_VERSION,
    DailyMarketStateBriefReport,
    build_daily_market_state_brief,
)
from .journal import AppendOnlyJournal, EventType, JournalEvent

REPLAY_VALIDATION_SCHEMA_VERSION = "1.0.0"
REPLAY_VALIDATION_LOOKBACK_HOURS = 24


def _fmt_dt(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _stable_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _fingerprint(value: object) -> str:
    return hashlib.sha256(_stable_json(value).encode("utf-8")).hexdigest()


def _coerce_journal(journal: AppendOnlyJournal | CanonicalJournal | Path | str) -> AppendOnlyJournal | CanonicalJournal:
    if isinstance(journal, (AppendOnlyJournal, CanonicalJournal)):
        return journal
    return CanonicalJournal(journal)


def _event_payload(event: JournalEvent) -> dict[str, object]:
    return dict(event.payload or {})


@dataclass(frozen=True, slots=True)
class ReplayMismatch:
    field: str
    expected: object
    actual: object
    severity: str = "error"

    def to_dict(self) -> dict[str, object]:
        return {
            "field": self.field,
            "expected": self.expected,
            "actual": self.actual,
            "severity": self.severity,
        }


@dataclass(frozen=True, slots=True)
class ReplayValidationReport:
    schema_version: str
    generated_at: datetime
    journal_path: str
    event_count: int
    replay_consistent: bool
    reproducible: bool
    version_warnings: tuple[str, ...]
    mismatches: tuple[ReplayMismatch, ...]
    reconstructed_state: dict[str, object]
    reconstructed_brief: DailyMarketStateBriefReport
    state_fingerprint: str
    replay_fingerprint: str
    replay_duration_ms: float

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "generated_at": _fmt_dt(self.generated_at),
            "journal_path": self.journal_path,
            "event_count": self.event_count,
            "replay_consistent": self.replay_consistent,
            "reproducible": self.reproducible,
            "version_warnings": list(self.version_warnings),
            "mismatches": [mismatch.to_dict() for mismatch in self.mismatches],
            "reconstructed_state": self.reconstructed_state,
            "reconstructed_brief": self.reconstructed_brief.to_dict(),
            "state_fingerprint": self.state_fingerprint,
            "replay_fingerprint": self.replay_fingerprint,
            "replay_duration_ms": self.replay_duration_ms,
        }


def build_replay_validation_report(
    journal: AppendOnlyJournal | CanonicalJournal | Path | str,
    *,
    generated_at: datetime | None = None,
    lookback_hours: int = REPLAY_VALIDATION_LOOKBACK_HOURS,
) -> ReplayValidationReport:
    start = time.perf_counter()
    canonical_journal = _coerce_journal(journal)
    generated_at = generated_at.astimezone(timezone.utc) if generated_at is not None else datetime.now(timezone.utc)
    window_end = generated_at
    window_start = window_end - timedelta(hours=lookback_hours)

    events = list(canonical_journal.query(start_time=window_start, end_time=window_end))
    observations = [event for event in events if event.event_type in {EventType.MARKET_TICK, EventType.MARKET_SNAPSHOT}]
    brief_events = [event for event in events if event.event_type == EventType.MARKET_STATE_BRIEF]

    reconstructed_brief = build_daily_market_state_brief(canonical_journal, generated_at=generated_at, window_hours=lookback_hours)
    reconstructed_state = _reconstruct_state(observations, generated_at=generated_at, window_start=window_start, window_end=window_end)

    version_warnings = list(_collect_version_warnings(events))
    mismatches = list(_collect_mismatches(brief_events, reconstructed_brief))
    replay_consistent = not mismatches
    state_fingerprint = _fingerprint(reconstructed_state)
    replay_fingerprint = _fingerprint(reconstructed_brief.to_dict())
    reproducible = _fingerprint(_reconstruct_state(observations, generated_at=generated_at, window_start=window_start, window_end=window_end)) == state_fingerprint and _fingerprint(
        build_daily_market_state_brief(canonical_journal, generated_at=generated_at, window_hours=lookback_hours).to_dict()
    ) == replay_fingerprint
    duration_ms = round((time.perf_counter() - start) * 1000.0, 3)

    return ReplayValidationReport(
        schema_version=REPLAY_VALIDATION_SCHEMA_VERSION,
        generated_at=generated_at,
        journal_path=str(canonical_journal.path),
        event_count=len(events),
        replay_consistent=replay_consistent,
        reproducible=reproducible,
        version_warnings=tuple(version_warnings),
        mismatches=tuple(mismatches),
        reconstructed_state=reconstructed_state,
        reconstructed_brief=reconstructed_brief,
        state_fingerprint=state_fingerprint,
        replay_fingerprint=replay_fingerprint,
        replay_duration_ms=duration_ms,
    )


def format_replay_validation_report(report: ReplayValidationReport) -> str:
    return json.dumps(report.to_dict(), indent=2, sort_keys=True)


def _reconstruct_state(
    events: Iterable[JournalEvent],
    *,
    generated_at: datetime,
    window_start: datetime,
    window_end: datetime,
) -> dict[str, object]:
    latest_by_symbol: dict[str, dict[str, object]] = {}
    latest_by_source: dict[str, dict[str, object]] = {}
    for event in sorted(events, key=lambda item: (item.occurred_at, item.event_id)):
        payload = _event_payload(event)
        symbol = str(payload.get("symbol") or event.symbol or "").strip().upper()
        source = str(payload.get("source") or event.source_module or "").strip().lower()
        observed_at = payload.get("observed_at") or _fmt_dt(event.occurred_at)
        collected_at = payload.get("collected_at") or observed_at
        if symbol:
            latest_by_symbol[symbol] = {
                "event_id": event.event_id,
                "source": source,
                "observed_at": observed_at,
                "collected_at": collected_at,
                "price": payload.get("price"),
                "volume": payload.get("volume"),
                "quote_volume": payload.get("quote_volume") or payload.get("quoteVolume"),
            }
        if source:
            latest_by_source[source] = {
                "event_id": event.event_id,
                "symbol": symbol,
                "observed_at": observed_at,
                "collected_at": collected_at,
            }
    source_statuses: dict[str, dict[str, object]] = {}
    for source in ("binance.spot", "binance.futures", "coinbase.spot"):
        source_statuses[source] = _source_status(latest_by_source.get(source), source=source, generated_at=generated_at)
    return {
        "window_start": _fmt_dt(window_start),
        "window_end": _fmt_dt(window_end),
        "generated_at": _fmt_dt(generated_at),
        "latest_by_symbol": latest_by_symbol,
        "latest_by_source": latest_by_source,
        "source_statuses": source_statuses,
    }


def _source_status(entry: dict[str, object] | None, *, source: str, generated_at: datetime) -> dict[str, object]:
    if entry is None:
        return {
            "source": source,
            "freshness_basis": "observed_at",
            "last_observed_at": None,
            "last_collected_at": None,
            "age_minutes": None,
            "status": "missing",
        }
    observed_at = datetime.fromisoformat(str(entry["observed_at"]).replace("Z", "+00:00")).astimezone(timezone.utc)
    collected_at_raw = entry.get("collected_at")
    collected_at = (
        datetime.fromisoformat(str(collected_at_raw).replace("Z", "+00:00")).astimezone(timezone.utc)
        if collected_at_raw is not None
        else None
    )
    age_minutes = round((generated_at - observed_at).total_seconds() / 60.0, 2)
    return {
        "source": source,
        "freshness_basis": "observed_at",
        "last_observed_at": _fmt_dt(observed_at),
        "last_collected_at": None if collected_at is None else _fmt_dt(collected_at),
        "age_minutes": age_minutes,
        "status": "stale" if age_minutes > 30 else "fresh",
    }


def _collect_version_warnings(events: Iterable[JournalEvent]) -> list[str]:
    warnings: list[str] = []
    for event in events:
        if event.source_module != CANONICAL_JOURNAL_SOURCE_MODULE:
            warnings.append(
                f"schema drift for {event.event_type.value} {event.event_id}: source_module={event.source_module!r} expected={CANONICAL_JOURNAL_SOURCE_MODULE!r}"
            )
        if event.schema_version != CANONICAL_JOURNAL_SCHEMA_VERSION and event.event_type in {EventType.MARKET_TICK, EventType.MARKET_SNAPSHOT}:
            warnings.append(
                f"schema drift for {event.event_type.value} {event.event_id}: recorded={event.schema_version!r} expected={CANONICAL_JOURNAL_SCHEMA_VERSION!r}"
            )
        if event.event_type == EventType.MARKET_STATE_BRIEF and event.schema_version != DAILY_MARKET_STATE_BRIEF_SCHEMA_VERSION:
            warnings.append(
                f"schema drift for {event.event_type.value} {event.event_id}: recorded={event.schema_version!r} expected={DAILY_MARKET_STATE_BRIEF_SCHEMA_VERSION!r}"
            )
    return warnings


def _collect_mismatches(
    brief_events: list[JournalEvent],
    reconstructed_brief: DailyMarketStateBriefReport,
) -> list[ReplayMismatch]:
    mismatches: list[ReplayMismatch] = []
    if not brief_events:
        return mismatches
    stored_payload = _event_payload(brief_events[-1])
    for key in ("observed_at", "collected_at", "schema_version", "source_module"):
        stored_payload.pop(key, None)
    stored = _normalize_jsonable(stored_payload)
    expected_payload = reconstructed_brief.to_dict()
    expected_payload.pop("schema_version", None)
    expected = _normalize_jsonable(expected_payload)
    mismatches.extend(_diff_json(expected, stored, prefix="daily_market_state_brief"))
    return mismatches


def _normalize_jsonable(value: object) -> object:
    if isinstance(value, dict):
        return {str(key): _normalize_jsonable(inner) for key, inner in sorted(value.items(), key=lambda item: str(item[0]))}
    if isinstance(value, list):
        return [_normalize_jsonable(item) for item in value]
    if isinstance(value, tuple):
        return [_normalize_jsonable(item) for item in value]
    return value


def _diff_json(expected: object, actual: object, *, prefix: str) -> list[ReplayMismatch]:
    mismatches: list[ReplayMismatch] = []
    if isinstance(expected, dict) and isinstance(actual, dict):
        keys = sorted(set(expected) | set(actual))
        for key in keys:
            child_prefix = f"{prefix}.{key}"
            if key not in expected:
                mismatches.append(ReplayMismatch(field=child_prefix, expected=None, actual=actual[key]))
            elif key not in actual:
                mismatches.append(ReplayMismatch(field=child_prefix, expected=expected[key], actual=None))
            else:
                mismatches.extend(_diff_json(expected[key], actual[key], prefix=child_prefix))
        return mismatches
    if isinstance(expected, list) and isinstance(actual, list):
        limit = min(len(expected), len(actual))
        for index in range(limit):
            mismatches.extend(_diff_json(expected[index], actual[index], prefix=f"{prefix}[{index}]"))
        for index in range(limit, len(expected)):
            mismatches.append(ReplayMismatch(field=f"{prefix}[{index}]", expected=expected[index], actual=None))
        for index in range(limit, len(actual)):
            mismatches.append(ReplayMismatch(field=f"{prefix}[{index}]", expected=None, actual=actual[index]))
        return mismatches
    if expected != actual:
        mismatches.append(ReplayMismatch(field=prefix, expected=expected, actual=actual))
    return mismatches
