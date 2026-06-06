from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Sequence

from .canonical_journal import CANONICAL_JOURNAL_SCHEMA_VERSION, CANONICAL_JOURNAL_SOURCE_MODULE, CanonicalJournal
from .daily_market_state_brief import (
    DAILY_MARKET_STATE_BRIEF_SCHEMA_VERSION,
    DAILY_MARKET_STATE_SOURCE_STALE_AFTER_MINUTES,
    DailyMarketStateBriefReport,
    build_daily_market_state_brief,
)
from .journal import AppendOnlyJournal, EventType, JournalEvent
from .replay_validation import REPLAY_VALIDATION_SCHEMA_VERSION, build_replay_validation_report

CRITICAL_ALERTS_SCHEMA_VERSION = "1.0.0"
CRITICAL_ALERT_LOOKBACK_HOURS = 24
CRITICAL_ALERT_SOURCE_STALE_AFTER_MINUTES = DAILY_MARKET_STATE_SOURCE_STALE_AFTER_MINUTES
SEVERE_LIQUIDATION_CLUSTER_MIN_NOTIONAL_QUOTE = 5_000_000.0
SEVERE_LIQUIDATION_CLUSTER_MIN_EVENTS = 3
EXTREME_OPEN_INTEREST_DISCONTINUITY_ABS_DELTA = 1_000.0
EXTREME_OPEN_INTEREST_DISCONTINUITY_RATIO = 0.25

CRITICAL_ALERT_TAXONOMY = (
    {
        "code": "source.stale",
        "priority": 0,
        "threshold_type": "freshness",
        "method": "observed_at age > 30 minutes",
        "rationale": "freshness is keyed to observed_at so delayed collection does not mask stale sources",
    },
    {
        "code": "source.missing",
        "priority": 1,
        "threshold_type": "coverage",
        "method": "expected source absent from latest status window",
        "rationale": "absence of one of the fixed MVP sources breaks daily report completeness",
    },
    {
        "code": "schema.malformed_payload",
        "priority": 2,
        "threshold_type": "schema",
        "method": "payload missing required normalized fields or failing coercion",
        "rationale": "invalid payloads are not trustworthy for report or replay use",
    },
    {
        "code": "replay.mismatch",
        "priority": 3,
        "threshold_type": "determinism",
        "method": "stored brief diverges from reconstructed brief",
        "rationale": "replay drift means the journal no longer reconstructs the same operator view",
    },
    {
        "code": "report.failure",
        "priority": 4,
        "threshold_type": "runtime",
        "method": "brief or replay generation raises an exception",
        "rationale": "a failed report is operator-actionable even if no market anomaly exists",
    },
    {
        "code": "liquidation.severe_cluster",
        "priority": 5,
        "threshold_type": "cluster",
        "method": ">=3 liquidation events, each >= 5,000,000 quote notional, in the lookback window",
        "rationale": "three or more large liquidations in one window is a stress signal worth operator attention",
    },
    {
        "code": "open_interest.extreme_discontinuity",
        "priority": 6,
        "threshold_type": "discontinuity",
        "method": "|delta| >= 1,000 and ratio >= 25% between consecutive observations",
        "rationale": "the combined absolute and relative filter catches structural breaks while suppressing noise",
    },
)


@dataclass(frozen=True, slots=True)
class CriticalAlertDefinition:
    code: str
    priority: int
    threshold_type: str
    method: str
    rationale: str

    def to_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "priority": self.priority,
            "threshold_type": self.threshold_type,
            "method": self.method,
            "rationale": self.rationale,
        }


@dataclass(frozen=True, slots=True)
class CriticalAlert:
    event_id: str
    observed_at: datetime
    code: str
    title: str
    message: str
    source: str
    details: dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "event_id": self.event_id,
            "observed_at": _fmt_dt(self.observed_at),
            "code": self.code,
            "title": self.title,
            "message": self.message,
            "source": self.source,
            "details": self.details,
        }


@dataclass(frozen=True, slots=True)
class CriticalAlertReport:
    schema_version: str
    generated_at: datetime
    journal_path: str
    window_start: datetime
    window_end: datetime
    alerts: tuple[CriticalAlert, ...]
    source_freshness: tuple[dict[str, object], ...]
    replay_consistent: bool
    version_warnings: tuple[str, ...]
    mismatches: tuple[dict[str, object], ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "generated_at": _fmt_dt(self.generated_at),
            "journal_path": self.journal_path,
            "window_start": _fmt_dt(self.window_start),
            "window_end": _fmt_dt(self.window_end),
            "alerts": [alert.to_dict() for alert in self.alerts],
            "source_freshness": [dict(item) for item in self.source_freshness],
            "replay_consistent": self.replay_consistent,
            "version_warnings": list(self.version_warnings),
            "mismatches": [dict(item) for item in self.mismatches],
        }


def _fmt_dt(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _parse_dt(value: object | None, fallback: datetime | None = None) -> datetime | None:
    if value is None:
        return fallback
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)


def _maybe_float(value: object | None) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _stable_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _event_id(prefix: str, payload: object) -> str:
    return f"{prefix}-{hashlib.sha256(_stable_json(payload).encode('utf-8')).hexdigest()[:24]}"


def _coerce_journal(journal: AppendOnlyJournal | CanonicalJournal | Path | str) -> CanonicalJournal:
    if isinstance(journal, CanonicalJournal):
        return journal
    if isinstance(journal, AppendOnlyJournal):
        return CanonicalJournal(journal)
    return CanonicalJournal(journal)


def _event_payload(event: JournalEvent) -> dict[str, object]:
    payload = dict(event.payload or {})
    for key in ("observed_at", "collected_at", "schema_version", "source_module"):
        payload.pop(key, None)
    return payload


def _format_alert_message(code: str, details: dict[str, object]) -> tuple[str, str]:
    if code == "source.stale":
        source = str(details.get("source", "unknown"))
        return source, f"{source} is stale in the latest pilot window"
    if code == "source.missing":
        source = str(details.get("source", "unknown"))
        return source, f"{source} is missing from the latest pilot window"
    if code == "replay.mismatch":
        return "journal", f"Replay validation found {details.get('mismatch_count', 0)} mismatch(s)"
    if code == "report.failure":
        return "pilot.runner", "Pilot report generation failed"
    if code == "liquidation.severe_cluster":
        symbol = str(details.get("symbol", "unknown"))
        return symbol, f"Severe liquidation cluster detected on {symbol}"
    if code == "open_interest.extreme_discontinuity":
        symbol = str(details.get("symbol", "unknown"))
        return symbol, f"Extreme open-interest discontinuity detected on {symbol}"
    return "journal", code


def _priority_for_code(code: str) -> int:
    for item in CRITICAL_ALERT_TAXONOMY:
        if item["code"] == code:
            return int(item["priority"])
    return 99


def _build_alert(code: str, *, observed_at: datetime, details: dict[str, object]) -> CriticalAlert:
    source, message = _format_alert_message(code, details)
    title = code.replace(".", " ").title()
    payload = {"code": code, "observed_at": _fmt_dt(observed_at), "source": source, "message": message, "details": details}
    return CriticalAlert(
        event_id=_event_id(f"ca-{code.replace('.', '-')}", payload),
        observed_at=observed_at,
        code=code,
        title=title,
        message=message,
        source=source,
        details=details,
    )


def build_critical_alerts(
    journal: AppendOnlyJournal | CanonicalJournal | Path | str,
    *,
    generated_at: datetime | None = None,
    lookback_hours: int = CRITICAL_ALERT_LOOKBACK_HOURS,
) -> tuple[CriticalAlert, ...]:
    report = build_critical_alert_report(journal, generated_at=generated_at, lookback_hours=lookback_hours)
    return report.alerts


def build_critical_alert_report(
    journal: AppendOnlyJournal | CanonicalJournal | Path | str,
    *,
    generated_at: datetime | None = None,
    lookback_hours: int = CRITICAL_ALERT_LOOKBACK_HOURS,
) -> CriticalAlertReport:
    canonical_journal = _coerce_journal(journal)
    generated_at = generated_at.astimezone(timezone.utc) if generated_at is not None else datetime.now(timezone.utc)
    window_end = generated_at
    window_start = window_end - timedelta(hours=lookback_hours)

    alerts: list[CriticalAlert] = []
    source_freshness: tuple[dict[str, object], ...] = ()
    version_warnings: tuple[str, ...] = ()
    mismatches: tuple[dict[str, object], ...] = ()
    replay_consistent = True
    try:
        brief_report = build_daily_market_state_brief(canonical_journal, generated_at=generated_at, window_hours=lookback_hours)
        replay_report = build_replay_validation_report(canonical_journal, generated_at=generated_at, lookback_hours=lookback_hours)
        source_freshness = tuple(status.to_dict() for status in brief_report.source_statuses)
        version_warnings = replay_report.version_warnings
        mismatches = tuple(mismatch.to_dict() for mismatch in replay_report.mismatches)
        replay_consistent = replay_report.replay_consistent
        alerts.extend(_alerts_from_source_statuses(brief_report, generated_at=generated_at))
        alerts.extend(_alerts_from_replay(replay_report, generated_at=generated_at))
        alerts.extend(_alerts_from_liquidations(canonical_journal, generated_at=generated_at, window_start=window_start, window_end=window_end))
        alerts.extend(_alerts_from_open_interest(canonical_journal, generated_at=generated_at, window_start=window_start, window_end=window_end))
    except Exception as exc:
        alerts.append(
            _build_alert(
                "report.failure",
                observed_at=generated_at,
                details={"error": type(exc).__name__, "message": str(exc)},
            )
        )
        replay_consistent = False

    ordered = tuple(sorted(alerts, key=lambda alert: (_priority_for_code(alert.code), alert.observed_at, alert.code, alert.source, alert.details.get("symbol", ""))))
    return CriticalAlertReport(
        schema_version=CRITICAL_ALERTS_SCHEMA_VERSION,
        generated_at=generated_at,
        journal_path=str(canonical_journal.path),
        window_start=window_start,
        window_end=window_end,
        alerts=ordered,
        source_freshness=source_freshness,
        replay_consistent=replay_consistent,
        version_warnings=version_warnings,
        mismatches=mismatches,
    )


def format_critical_alert_report(report: CriticalAlertReport) -> str:
    lines = [
        "Critical Alerts",
        f"Window: {_fmt_dt(report.window_start)} → {_fmt_dt(report.window_end)}",
        f"Replay consistent: {str(report.replay_consistent).lower()}",
        f"Alerts: {len(report.alerts)}",
    ]
    if report.alerts:
        for alert in report.alerts[:7]:
            lines.append(f"- {alert.code}: {alert.message}")
    else:
        lines.append("- none")
    return "\n".join(lines)


def _alerts_from_source_statuses(brief_report: DailyMarketStateBriefReport, *, generated_at: datetime) -> list[CriticalAlert]:
    alerts: list[CriticalAlert] = []
    for status in brief_report.source_statuses:
        if status.status == "fresh":
            continue
        code = "source.missing" if status.status == "missing" else "source.stale"
        details = status.to_dict()
        details["observed_at"] = _fmt_dt(generated_at)
        alerts.append(_build_alert(code, observed_at=generated_at, details=details))
    return alerts


def _alerts_from_replay(replay_report, *, generated_at: datetime) -> list[CriticalAlert]:
    alerts: list[CriticalAlert] = []
    if getattr(replay_report, "mismatches", None):
        for mismatch in replay_report.mismatches:
            alerts.append(
                _build_alert(
                    "replay.mismatch",
                    observed_at=generated_at,
                    details={
                        "field": mismatch.field,
                        "expected": mismatch.expected,
                        "actual": mismatch.actual,
                        "mismatch_count": len(replay_report.mismatches),
                        "state_fingerprint": replay_report.state_fingerprint,
                        "replay_fingerprint": replay_report.replay_fingerprint,
                    },
                )
            )
            break
    return alerts


def _alerts_from_liquidations(
    journal: CanonicalJournal,
    *,
    generated_at: datetime,
    window_start: datetime,
    window_end: datetime,
) -> list[CriticalAlert]:
    alerts: list[CriticalAlert] = []
    by_symbol: dict[str, list[tuple[datetime, float]]] = {}
    for event in journal.query(event_types=[EventType.MARKET_TICK, EventType.MARKET_SNAPSHOT], start_time=window_start, end_time=window_end):
        payload = _event_payload(event)
        symbol = str(payload.get("symbol") or event.symbol or "").strip().upper()
        if not symbol:
            continue
        observed_at = _parse_dt(payload.get("observed_at"), fallback=event.occurred_at) or event.occurred_at
        liquidations = payload.get("liquidations")
        notional = None
        if isinstance(liquidations, dict):
            notional = _maybe_float(liquidations.get("quote_notional") or liquidations.get("notional_quote"))
        if notional is None:
            notional = _maybe_float(payload.get("liquidations_notional_quote"))
        if notional is None:
            continue
        by_symbol.setdefault(symbol, []).append((observed_at, notional))
    for symbol, entries in by_symbol.items():
        large = [(observed_at, notional) for observed_at, notional in entries if notional >= SEVERE_LIQUIDATION_CLUSTER_MIN_NOTIONAL_QUOTE]
        if len(large) >= SEVERE_LIQUIDATION_CLUSTER_MIN_EVENTS:
            alerts.append(
                _build_alert(
                    "liquidation.severe_cluster",
                    observed_at=max(observed_at for observed_at, _ in large),
                    details={
                        "symbol": symbol,
                        "event_count": len(large),
                        "threshold_count": SEVERE_LIQUIDATION_CLUSTER_MIN_EVENTS,
                        "threshold_notional_quote": SEVERE_LIQUIDATION_CLUSTER_MIN_NOTIONAL_QUOTE,
                    },
                )
            )
    return alerts


def _alerts_from_open_interest(
    journal: CanonicalJournal,
    *,
    generated_at: datetime,
    window_start: datetime,
    window_end: datetime,
) -> list[CriticalAlert]:
    alerts: list[CriticalAlert] = []
    by_symbol: dict[str, list[tuple[datetime, float]]] = {}
    for event in journal.query(event_types=[EventType.MARKET_TICK, EventType.MARKET_SNAPSHOT], start_time=window_start, end_time=window_end):
        payload = _event_payload(event)
        symbol = str(payload.get("symbol") or event.symbol or "").strip().upper()
        if not symbol:
            continue
        open_interest = _maybe_float(payload.get("open_interest"))
        if open_interest is None:
            continue
        observed_at = _parse_dt(payload.get("observed_at"), fallback=event.occurred_at) or event.occurred_at
        by_symbol.setdefault(symbol, []).append((observed_at, open_interest))
    for symbol, entries in by_symbol.items():
        entries.sort(key=lambda item: item[0])
        for previous, current in zip(entries, entries[1:]):
            prev_at, prev_oi = previous
            curr_at, curr_oi = current
            delta = abs(curr_oi - prev_oi)
            ratio = delta / max(abs(prev_oi), 1.0)
            if delta >= EXTREME_OPEN_INTEREST_DISCONTINUITY_ABS_DELTA and ratio >= EXTREME_OPEN_INTEREST_DISCONTINUITY_RATIO:
                alerts.append(
                    _build_alert(
                        "open_interest.extreme_discontinuity",
                        observed_at=curr_at,
                        details={
                            "symbol": symbol,
                            "previous_open_interest": prev_oi,
                            "current_open_interest": curr_oi,
                            "abs_delta": delta,
                            "ratio": ratio,
                            "threshold_abs_delta": EXTREME_OPEN_INTEREST_DISCONTINUITY_ABS_DELTA,
                            "threshold_ratio": EXTREME_OPEN_INTEREST_DISCONTINUITY_RATIO,
                        },
                    )
                )
                break
    return alerts
