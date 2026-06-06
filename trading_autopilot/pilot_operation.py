from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Sequence

from .canonical_journal import CANONICAL_JOURNAL_SCHEMA_VERSION, CANONICAL_JOURNAL_SOURCE_MODULE, CanonicalJournal, CanonicalJournalRecord
from .daily_market_state_brief import (
    DAILY_MARKET_STATE_BRIEF_SCHEMA_VERSION,
    DAILY_MARKET_STATE_SOURCE_STALE_AFTER_MINUTES,
    DailyMarketStateAssetBrief,
    DailyMarketStateBriefReport,
    DailyMarketStateSourceStatus,
    build_daily_market_state_brief,
    format_daily_market_state_brief,
)
from .journal import AppendOnlyJournal, EventType, JournalEvent
from .replay_validation import ReplayValidationReport, build_replay_validation_report

PILOT_OPERATION_SCHEMA_VERSION = "1.0.0"
PILOT_DEFAULT_LOOKBACK_HOURS = 24
PILOT_DEFAULT_RECENT_LIMIT = 5
PILOT_DEFAULT_POLL_INTERVAL_SECONDS = 24 * 60 * 60
PILOT_OPERATIONAL_MODE = "observer-only"
PILOT_READ_ONLY = True
PILOT_SOURCE_STALE_AFTER_MINUTES = DAILY_MARKET_STATE_SOURCE_STALE_AFTER_MINUTES
PILOT_EXPECTED_SOURCES = ("binance.spot", "binance.futures", "coinbase.spot")


@dataclass(frozen=True, slots=True)
class PilotMetric:
    name: str
    value: float | int | str | bool | None
    unit: str = ""
    description: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "value": self.value,
            "unit": self.unit,
            "description": self.description,
        }


@dataclass(frozen=True, slots=True)
class PilotAlertRecord:
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
class PilotOperationReport:
    schema_version: str
    generated_at: datetime
    journal_path: str
    pilot_window_start: datetime
    pilot_window_end: datetime
    operational_mode: str
    read_only: bool
    last_successful_run_at: datetime | None
    report_generation_status: str
    replay_validation_status: str
    source_freshness: tuple[DailyMarketStateSourceStatus, ...]
    metrics: tuple[PilotMetric, ...]
    recent_briefs: tuple[DailyMarketStateBriefReport, ...]
    recent_alerts: tuple[PilotAlertRecord, ...]
    replay_validation_report: ReplayValidationReport

    def metrics_by_name(self) -> dict[str, PilotMetric]:
        return {metric.name: metric for metric in self.metrics}

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "generated_at": _fmt_dt(self.generated_at),
            "journal_path": self.journal_path,
            "pilot_window_start": _fmt_dt(self.pilot_window_start),
            "pilot_window_end": _fmt_dt(self.pilot_window_end),
            "operational_mode": self.operational_mode,
            "read_only": self.read_only,
            "last_successful_run_at": None if self.last_successful_run_at is None else _fmt_dt(self.last_successful_run_at),
            "report_generation_status": self.report_generation_status,
            "replay_validation_status": self.replay_validation_status,
            "source_freshness": [status.to_dict() for status in self.source_freshness],
            "metrics": [metric.to_dict() for metric in self.metrics],
            "recent_briefs": [brief.to_dict() for brief in self.recent_briefs],
            "recent_alerts": [alert.to_dict() for alert in self.recent_alerts],
            "replay_validation_report": self.replay_validation_report.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class PilotCycleResult:
    report: PilotOperationReport
    appended_brief_event_id: str | None
    appended_alert_event_ids: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "report": self.report.to_dict(),
            "appended_brief_event_id": self.appended_brief_event_id,
            "appended_alert_event_ids": list(self.appended_alert_event_ids),
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


def _brief_from_payload(payload: dict[str, object]) -> DailyMarketStateBriefReport:
    assets = tuple(_asset_from_payload(item) for item in payload.get("assets", []))
    source_statuses = tuple(_source_status_from_payload(item) for item in payload.get("source_statuses", []))
    missing_sources = tuple(str(item) for item in payload.get("missing_sources", []))
    return DailyMarketStateBriefReport(
        schema_version=str(payload.get("schema_version", DAILY_MARKET_STATE_BRIEF_SCHEMA_VERSION)),
        generated_at=_parse_dt(payload.get("generated_at")) or datetime.now(timezone.utc),
        window_start=_parse_dt(payload.get("window_start")) or datetime.now(timezone.utc),
        window_end=_parse_dt(payload.get("window_end")) or datetime.now(timezone.utc),
        freshness_basis=str(payload.get("freshness_basis", "observed_at")),
        trust_level=str(payload.get("trust_level", "unknown")),
        assets=assets,
        source_statuses=source_statuses,
        missing_sources=missing_sources,
    )


def _asset_from_payload(payload: object) -> DailyMarketStateAssetBrief:
    if not isinstance(payload, dict):
        raise TypeError("asset payload must be a mapping")
    liquidations = payload.get("liquidations")
    if isinstance(liquidations, dict):
        liquidations = {str(key): float(value) for key, value in liquidations.items() if value is not None}
    else:
        liquidations = None
    return DailyMarketStateAssetBrief(
        symbol=str(payload.get("symbol", "")),
        latest_source=str(payload.get("latest_source", "")),
        latest_observed_at=_parse_dt(payload.get("latest_observed_at")) or datetime.now(timezone.utc),
        latest_collected_at=_parse_dt(payload.get("latest_collected_at")),
        price=_maybe_float(payload.get("price")),
        volume=_maybe_float(payload.get("volume")),
        quote_volume=_maybe_float(payload.get("quote_volume")),
        spread_bps=_maybe_float(payload.get("spread_bps")),
        funding=_maybe_float(payload.get("funding")),
        open_interest=_maybe_float(payload.get("open_interest")),
        liquidations=liquidations,
        price_change_bps=_maybe_float(payload.get("price_change_bps")),
        evidence=str(payload.get("evidence", "")),
    )


def _source_status_from_payload(payload: object) -> DailyMarketStateSourceStatus:
    if not isinstance(payload, dict):
        raise TypeError("source status payload must be a mapping")
    return DailyMarketStateSourceStatus(
        source=str(payload.get("source", "")),
        freshness_basis=str(payload.get("freshness_basis", "observed_at")),
        last_observed_at=_parse_dt(payload.get("last_observed_at")),
        last_collected_at=_parse_dt(payload.get("last_collected_at")),
        age_minutes=_maybe_float(payload.get("age_minutes")),
        status=str(payload.get("status", "unknown")),
    )


def _maybe_float(value: object | None) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _journal_event_payload(event: JournalEvent) -> dict[str, object]:
    return dict(event.payload or {})


def _build_alert_record(event: JournalEvent) -> PilotAlertRecord:
    payload = _journal_event_payload(event)
    return PilotAlertRecord(
        event_id=event.event_id,
        observed_at=_parse_dt(payload.get("observed_at"), fallback=event.occurred_at) or event.occurred_at,
        code=str(payload.get("code", event.event_type.value)),
        title=str(payload.get("title", "")),
        message=str(payload.get("message", "")),
        source=str(payload.get("source", "journal")),
        details=dict(payload.get("details", {})) if isinstance(payload.get("details"), dict) else {},
    )


def _build_metrics(
    *,
    brief: DailyMarketStateBriefReport | None,
    replay_report: ReplayValidationReport | None,
    alerts_generated: int,
    report_failures: int,
) -> tuple[PilotMetric, ...]:
    stale_sources = 0
    missing_sources = 0
    if brief is not None:
        stale_sources = sum(1 for status in brief.source_statuses if status.status == "stale")
        missing_sources = sum(1 for status in brief.source_statuses if status.status == "missing")
    replay_mismatches = 0 if replay_report is None else len(replay_report.mismatches)
    reports_generated = 1 if brief is not None else 0
    return (
        PilotMetric(name="reports_generated", value=reports_generated, unit="count", description="Daily market-state briefs generated in this run"),
        PilotMetric(name="alerts_generated", value=alerts_generated, unit="count", description="Critical alerts emitted in this run"),
        PilotMetric(name="stale_source_incidents", value=stale_sources + missing_sources, unit="count", description="Sources that were stale or missing in the latest status snapshot"),
        PilotMetric(name="replay_mismatches", value=replay_mismatches, unit="count", description="Replay mismatches observed in the latest validation run"),
        PilotMetric(name="report_failures", value=report_failures, unit="count", description="Report generation failures in this run"),
    )


class PilotRunner:
    def __init__(
        self,
        journal: AppendOnlyJournal | CanonicalJournal | Path | str,
        *,
        lookback_hours: int = PILOT_DEFAULT_LOOKBACK_HOURS,
        recent_limit: int = PILOT_DEFAULT_RECENT_LIMIT,
        stale_after_minutes: int = PILOT_SOURCE_STALE_AFTER_MINUTES,
    ) -> None:
        if lookback_hours <= 0:
            raise ValueError("lookback_hours must be positive")
        if recent_limit <= 0:
            raise ValueError("recent_limit must be positive")
        if stale_after_minutes <= 0:
            raise ValueError("stale_after_minutes must be positive")
        self._journal = _coerce_journal(journal)
        self.lookback_hours = lookback_hours
        self.recent_limit = recent_limit
        self.stale_after_minutes = stale_after_minutes

    @property
    def journal(self) -> CanonicalJournal:
        return self._journal

    def run_once(self, *, generated_at: datetime | None = None) -> PilotOperationReport:
        generated_at = generated_at.astimezone(timezone.utc) if generated_at is not None else datetime.now(timezone.utc)
        cycle = self._execute_cycle(generated_at=generated_at)
        return cycle.report

    def run_cycle(self, *, generated_at: datetime | None = None) -> PilotCycleResult:
        generated_at = generated_at.astimezone(timezone.utc) if generated_at is not None else datetime.now(timezone.utc)
        return self._execute_cycle(generated_at=generated_at)

    def run_forever(self, *, interval_seconds: int = PILOT_DEFAULT_POLL_INTERVAL_SECONDS, stop_event: threading.Event | None = None) -> None:
        if interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive")
        stop_event = stop_event or threading.Event()
        while not stop_event.is_set():
            self.run_once()
            if stop_event.wait(interval_seconds):
                break

    def _execute_cycle(self, *, generated_at: datetime) -> PilotCycleResult:
        report_failures = 0
        appended_alert_ids: list[str] = []
        appended_brief_event_id: str | None = None
        fresh_brief: DailyMarketStateBriefReport | None = None
        fresh_replay: ReplayValidationReport | None = None
        try:
            fresh_brief = build_daily_market_state_brief(self._journal, generated_at=generated_at, window_hours=self.lookback_hours)
            fresh_replay = build_replay_validation_report(self._journal, generated_at=generated_at, lookback_hours=self.lookback_hours)
        except Exception:
            report_failures = 1
        else:
            appended_brief_event_id = self._append_brief(fresh_brief)
            appended_alert_ids.extend(self._append_critical_alerts(fresh_brief=fresh_brief, replay_report=fresh_replay, generated_at=generated_at, report_failures=report_failures))
        report = build_pilot_operation_report(
            self._journal,
            generated_at=generated_at,
            lookback_hours=self.lookback_hours,
            recent_limit=self.recent_limit,
        )
        return PilotCycleResult(report=report, appended_brief_event_id=appended_brief_event_id, appended_alert_event_ids=tuple(appended_alert_ids))

    def _append_brief(self, brief: DailyMarketStateBriefReport) -> str:
        payload = brief.to_dict()
        event_id = _event_id("pilot-brief", payload)
        self._journal.append_record(
            CanonicalJournalRecord(
                event_id=event_id,
                event_type=EventType.MARKET_STATE_BRIEF,
                observed_at=brief.generated_at,
                collected_at=brief.generated_at,
                correlation_id="pilot-operation",
                payload=payload,
            )
        )
        return event_id

    def _append_critical_alerts(
        self,
        *,
        fresh_brief: DailyMarketStateBriefReport,
        replay_report: ReplayValidationReport,
        generated_at: datetime,
        report_failures: int,
    ) -> list[str]:
        alert_ids: list[str] = []
        for status in fresh_brief.source_statuses:
            if status.status not in {"stale", "missing"}:
                continue
            code = "source.missing" if status.status == "missing" else "source.stale"
            title = "Source missing" if status.status == "missing" else "Source stale"
            message = (
                f"{status.source} has no fresh observation in the latest {self.lookback_hours}h window "
                f"(freshness basis: observed_at, threshold: {self.stale_after_minutes}m)"
            )
            payload = {
                "code": code,
                "title": title,
                "message": message,
                "observed_at": _fmt_dt(generated_at),
                "source": status.source,
                "details": status.to_dict(),
            }
            alert_ids.append(self._append_alert(payload, generated_at=generated_at))

        if replay_report.mismatches:
            payload = {
                "code": "replay.mismatch",
                "title": "Replay validation mismatch",
                "message": f"Replay validation found {len(replay_report.mismatches)} mismatch(s)",
                "observed_at": _fmt_dt(generated_at),
                "source": "journal",
                "details": {
                    "mismatch_count": len(replay_report.mismatches),
                    "state_fingerprint": replay_report.state_fingerprint,
                    "replay_fingerprint": replay_report.replay_fingerprint,
                },
            }
            alert_ids.append(self._append_alert(payload, generated_at=generated_at))

        if report_failures:
            payload = {
                "code": "report.failure",
                "title": "Pilot report generation failure",
                "message": "Pilot report generation failed during the latest cycle",
                "observed_at": _fmt_dt(generated_at),
                "source": "pilot.runner",
                "details": {"report_failures": report_failures},
            }
            alert_ids.append(self._append_alert(payload, generated_at=generated_at))

        return alert_ids

    def _append_alert(self, payload: dict[str, object], *, generated_at: datetime) -> str:
        event_id = _event_id("pilot-alert", payload)
        self._journal.append_record(
            CanonicalJournalRecord(
                event_id=event_id,
                event_type=EventType.CRITICAL_ALERT,
                observed_at=generated_at,
                collected_at=generated_at,
                correlation_id="pilot-operation",
                payload=payload,
            )
        )
        return event_id


def build_pilot_operation_report(
    journal: AppendOnlyJournal | CanonicalJournal | Path | str,
    *,
    generated_at: datetime | None = None,
    lookback_hours: int = PILOT_DEFAULT_LOOKBACK_HOURS,
    recent_limit: int = PILOT_DEFAULT_RECENT_LIMIT,
) -> PilotOperationReport:
    canonical_journal = _coerce_journal(journal)
    generated_at = generated_at.astimezone(timezone.utc) if generated_at is not None else datetime.now(timezone.utc)
    window_end = generated_at
    window_start = window_end - timedelta(hours=lookback_hours)

    brief_report = build_daily_market_state_brief(canonical_journal, generated_at=generated_at, window_hours=lookback_hours)
    replay_report = build_replay_validation_report(canonical_journal, generated_at=generated_at, lookback_hours=lookback_hours)
    source_freshness = tuple(brief_report.source_statuses)
    recent_briefs = tuple(_brief_from_payload(dict(event.payload or {})) for event in _latest_events(canonical_journal, EventType.MARKET_STATE_BRIEF, recent_limit))
    recent_alerts = tuple(_build_alert_record(event) for event in _latest_events(canonical_journal, EventType.CRITICAL_ALERT, recent_limit))
    last_successful_run_at = recent_briefs[0].generated_at if recent_briefs else None
    report_generation_status = "ok" if recent_briefs else "idle"
    replay_validation_status = "consistent" if replay_report.replay_consistent and not replay_report.version_warnings else "mismatch" if replay_report.mismatches else "warning"
    metrics = _build_metrics(
        brief=brief_report,
        replay_report=replay_report,
        alerts_generated=len(recent_alerts),
        report_failures=sum(1 for alert in recent_alerts if alert.code == "report.failure"),
    )
    return PilotOperationReport(
        schema_version=PILOT_OPERATION_SCHEMA_VERSION,
        generated_at=generated_at,
        journal_path=str(canonical_journal.path),
        pilot_window_start=window_start,
        pilot_window_end=window_end,
        operational_mode=PILOT_OPERATIONAL_MODE,
        read_only=PILOT_READ_ONLY,
        last_successful_run_at=last_successful_run_at,
        report_generation_status=report_generation_status,
        replay_validation_status=replay_validation_status,
        source_freshness=source_freshness,
        metrics=metrics,
        recent_briefs=recent_briefs,
        recent_alerts=recent_alerts,
        replay_validation_report=replay_report,
    )


def _latest_events(journal: CanonicalJournal, event_type: EventType, limit: int) -> Sequence[JournalEvent]:
    events = journal.query(event_types=[event_type])
    return tuple(sorted(events, key=lambda event: (event.occurred_at, event.event_id), reverse=True))[:limit]


def format_pilot_operation_report(report: PilotOperationReport) -> str:
    freshness_summary = ", ".join(
        f"{status.source} {status.status} ({'n/a' if status.age_minutes is None else f'{status.age_minutes:.0f}m'})"
        for status in report.source_freshness
    ) or "none"
    recent_brief_summary = "none"
    if report.recent_briefs:
        latest_brief = report.recent_briefs[0]
        recent_brief_summary = f"{len(latest_brief.assets)} assets, trust={latest_brief.trust_level}, window={_fmt_dt(latest_brief.window_start)}→{_fmt_dt(latest_brief.window_end)}"
    recent_alert_summary = "none"
    if report.recent_alerts:
        latest_alert = report.recent_alerts[0]
        recent_alert_summary = f"{latest_alert.code} on {latest_alert.source}: {latest_alert.message}"
    metrics = report.metrics_by_name()
    lines = [
        "Pilot Operation Status",
        f"Window: {_fmt_dt(report.pilot_window_start)} → {_fmt_dt(report.pilot_window_end)}",
        f"Mode: {report.operational_mode} | Read-only: {str(report.read_only).lower()}",
        f"Last successful run: {None if report.last_successful_run_at is None else _fmt_dt(report.last_successful_run_at)}",
        f"Report generation: {report.report_generation_status} | Replay validation: {report.replay_validation_status}",
        f"Metrics: reports={metrics['reports_generated'].value} alerts={metrics['alerts_generated'].value} stale_sources={metrics['stale_source_incidents'].value} mismatches={metrics['replay_mismatches'].value} failures={metrics['report_failures'].value}",
        f"Source freshness: {freshness_summary}",
        f"Recent briefs: {len(report.recent_briefs)} stored | {recent_brief_summary}",
        f"Recent alerts: {len(report.recent_alerts)} stored | {recent_alert_summary}",
    ]
    return "\n".join(lines)


def _parse_iso_datetime(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="trading-autopilot-pilot")
    subparsers = parser.add_subparsers(dest="command")

    run_parser = subparsers.add_parser("pilot-run", help="Run one observer-only pilot cycle")
    run_parser.add_argument("--journal", required=True, help="Path to the pilot journal SQLite file")
    run_parser.add_argument("--generated-at", dest="generated_at", default=None, help="ISO-8601 timestamp to use for the run")
    run_parser.add_argument("--lookback-hours", type=int, default=PILOT_DEFAULT_LOOKBACK_HOURS)
    run_parser.add_argument("--recent-limit", type=int, default=PILOT_DEFAULT_RECENT_LIMIT)
    run_parser.add_argument("--interval-seconds", type=int, default=PILOT_DEFAULT_POLL_INTERVAL_SECONDS, help="Unused for one-shot runs; kept for scheduling symmetry")

    status_parser = subparsers.add_parser("pilot-status", help="Render the latest pilot operational status")
    status_parser.add_argument("--journal", required=True, help="Path to the pilot journal SQLite file")
    status_parser.add_argument("--generated-at", dest="generated_at", default=None, help="ISO-8601 timestamp to use for the snapshot")
    status_parser.add_argument("--lookback-hours", type=int, default=PILOT_DEFAULT_LOOKBACK_HOURS)
    status_parser.add_argument("--recent-limit", type=int, default=PILOT_DEFAULT_RECENT_LIMIT)

    loop_parser = subparsers.add_parser("pilot-loop", help="Run the pilot continuously until interrupted")
    loop_parser.add_argument("--journal", required=True, help="Path to the pilot journal SQLite file")
    loop_parser.add_argument("--generated-at", dest="generated_at", default=None, help="Optional ISO-8601 timestamp for the first cycle")
    loop_parser.add_argument("--lookback-hours", type=int, default=PILOT_DEFAULT_LOOKBACK_HOURS)
    loop_parser.add_argument("--recent-limit", type=int, default=PILOT_DEFAULT_RECENT_LIMIT)
    loop_parser.add_argument("--interval-seconds", type=int, default=PILOT_DEFAULT_POLL_INTERVAL_SECONDS)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.command == "pilot-run":
        runner = PilotRunner(args.journal, lookback_hours=args.lookback_hours, recent_limit=args.recent_limit)
        report = runner.run_once(generated_at=_parse_iso_datetime(args.generated_at))
        print(format_pilot_operation_report(report))
        return 0
    if args.command == "pilot-status":
        report = build_pilot_operation_report(
            args.journal,
            generated_at=_parse_iso_datetime(args.generated_at),
            lookback_hours=args.lookback_hours,
            recent_limit=args.recent_limit,
        )
        print(format_pilot_operation_report(report))
        return 0
    if args.command == "pilot-loop":
        runner = PilotRunner(args.journal, lookback_hours=args.lookback_hours, recent_limit=args.recent_limit)
        stop_event = threading.Event()

        def _handle_signal(signum: int, frame: object) -> None:  # pragma: no cover - signal wiring
            stop_event.set()

        previous_int = signal.signal(signal.SIGINT, _handle_signal)
        previous_term = signal.signal(signal.SIGTERM, _handle_signal)
        try:
            if args.generated_at is not None:
                runner.run_once(generated_at=_parse_iso_datetime(args.generated_at))
            runner.run_forever(interval_seconds=args.interval_seconds, stop_event=stop_event)
        finally:
            signal.signal(signal.SIGINT, previous_int)
            signal.signal(signal.SIGTERM, previous_term)
        return 0

    parser.print_help()
    return 0
