from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import Iterable, Sequence

from .audit import (
    ObserverAuditReport,
    ObserverComparisonReport,
    ReplaySessionReport,
    build_observer_audit_report,
    build_observer_replay_report,
    compare_observer_audit_reports,
)
from .journal import AppendOnlyJournal, EventType, JournalEvent
from .risk import RiskDecisionStatus, RiskReason

MONITORING_SCHEMA_VERSION = "1.0.0"
DEFAULT_THROTTLE_WINDOW_SECONDS = 300


class AlertSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass(frozen=True, slots=True)
class MonitoringMetric:
    name: str
    value: float | int | str | bool | None
    unit: str = ""
    description: str = ""
    source: str = "journal"

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "value": self.value,
            "unit": self.unit,
            "description": self.description,
            "source": self.source,
        }


@dataclass(frozen=True, slots=True)
class MonitoringSection:
    title: str
    summary: str
    metric_names: tuple[str, ...] = field(default_factory=tuple)
    alert_ids: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, object]:
        return {
            "title": self.title,
            "summary": self.summary,
            "metric_names": list(self.metric_names),
            "alert_ids": list(self.alert_ids),
        }


@dataclass(frozen=True, slots=True)
class MonitoringSignal:
    observed_at: datetime
    severity: AlertSeverity
    code: str
    title: str
    message: str
    source: str
    details: dict[str, object] = field(default_factory=dict)
    critical_bypass: bool = False
    fingerprint: str | None = None


@dataclass(frozen=True, slots=True)
class MonitoringAlert:
    alert_id: str
    fingerprint: str
    severity: AlertSeverity
    code: str
    title: str
    message: str
    observed_at: datetime
    source: str
    details: dict[str, object]
    occurrence_count: int
    suppressed_count: int
    throttled: bool
    bypassed_throttle: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "alert_id": self.alert_id,
            "fingerprint": self.fingerprint,
            "severity": self.severity.value,
            "code": self.code,
            "title": self.title,
            "message": self.message,
            "observed_at": _fmt_dt(self.observed_at),
            "source": self.source,
            "details": self.details,
            "occurrence_count": self.occurrence_count,
            "suppressed_count": self.suppressed_count,
            "throttled": self.throttled,
            "bypassed_throttle": self.bypassed_throttle,
        }


@dataclass(frozen=True, slots=True)
class MonitoringReport:
    schema_version: str
    session_id: str
    journal_path: str
    operational_mode: str
    read_only: bool
    source_truth: str
    generated_at: datetime
    event_count: int
    metrics: tuple[MonitoringMetric, ...]
    sections: tuple[MonitoringSection, ...]
    alerts: tuple[MonitoringAlert, ...]
    replay_consistent: bool
    version_warnings: tuple[str, ...]
    mismatch_count: int
    throttle_window_seconds: int
    live_order_path_enabled: bool

    def metrics_by_name(self) -> dict[str, MonitoringMetric]:
        return {metric.name: metric for metric in self.metrics}

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "session_id": self.session_id,
            "journal_path": self.journal_path,
            "operational_mode": self.operational_mode,
            "read_only": self.read_only,
            "source_truth": self.source_truth,
            "generated_at": _fmt_dt(self.generated_at),
            "event_count": self.event_count,
            "metrics": [metric.to_dict() for metric in self.metrics],
            "sections": [section.to_dict() for section in self.sections],
            "alerts": [alert.to_dict() for alert in self.alerts],
            "replay_consistent": self.replay_consistent,
            "version_warnings": list(self.version_warnings),
            "mismatch_count": self.mismatch_count,
            "throttle_window_seconds": self.throttle_window_seconds,
            "live_order_path_enabled": self.live_order_path_enabled,
        }


@dataclass(frozen=True, slots=True)
class _AlertBurst:
    fingerprint: str
    severity: AlertSeverity
    code: str
    title: str
    message: str
    source: str
    first_observed_at: datetime
    last_observed_at: datetime
    details: dict[str, object]
    occurrence_count: int
    suppressed_count: int
    bypassed_throttle: bool


class AnomalyThrottle:
    def __init__(self, *, window_seconds: int = DEFAULT_THROTTLE_WINDOW_SECONDS):
        if window_seconds <= 0:
            raise ValueError("window_seconds must be positive")
        self.window_seconds = window_seconds

    def throttle(self, signals: Iterable[MonitoringSignal]) -> tuple[MonitoringAlert, ...]:
        ordered = sorted(signals, key=lambda signal: (signal.observed_at, signal.severity.value, signal.code, signal.title, signal.source))
        bursts: list[_AlertBurst] = []
        by_fingerprint: dict[str, list[MonitoringSignal]] = {}
        for signal in ordered:
            fingerprint = signal.fingerprint or _fingerprint_signal(signal)
            if signal.critical_bypass or signal.severity == AlertSeverity.CRITICAL:
                bursts.append(
                    _AlertBurst(
                        fingerprint=fingerprint,
                        severity=signal.severity,
                        code=signal.code,
                        title=signal.title,
                        message=signal.message,
                        source=signal.source,
                        first_observed_at=signal.observed_at,
                        last_observed_at=signal.observed_at,
                        details=signal.details,
                        occurrence_count=1,
                        suppressed_count=0,
                        bypassed_throttle=True,
                    )
                )
                continue
            bucket = by_fingerprint.setdefault(fingerprint, [])
            bucket.append(signal)

        for fingerprint, signals_for_key in by_fingerprint.items():
            signals_for_key.sort(key=lambda signal: (signal.observed_at, signal.code, signal.title, signal.source))
            current: list[MonitoringSignal] = []
            current_start: datetime | None = None
            last_seen: datetime | None = None
            for signal in signals_for_key:
                if current and last_seen is not None:
                    gap = (signal.observed_at - last_seen).total_seconds()
                    if gap > self.window_seconds:
                        bursts.append(self._burst_from_signals(fingerprint, current))
                        current = []
                        current_start = None
                if not current:
                    current_start = signal.observed_at
                current.append(signal)
                last_seen = signal.observed_at
            if current:
                bursts.append(self._burst_from_signals(fingerprint, current))

        alerts = [
            MonitoringAlert(
                alert_id=_alert_id(burst.fingerprint, burst.first_observed_at, burst.code, burst.occurrence_count, sequence_number=index),
                fingerprint=burst.fingerprint,
                severity=burst.severity,
                code=burst.code,
                title=burst.title,
                message=burst.message,
                observed_at=burst.first_observed_at,
                source=burst.source,
                details={
                    **burst.details,
                    "last_observed_at": _fmt_dt(burst.last_observed_at),
                    "window_seconds": self.window_seconds,
                },
                occurrence_count=burst.occurrence_count,
                suppressed_count=burst.suppressed_count,
                throttled=burst.suppressed_count > 0,
                bypassed_throttle=burst.bypassed_throttle,
            )
            for index, burst in enumerate(sorted(bursts, key=lambda burst: (burst.first_observed_at, burst.code, burst.title, burst.source)), start=1)
        ]
        return tuple(alerts)

    @staticmethod
    def _burst_from_signals(fingerprint: str, signals: Sequence[MonitoringSignal]) -> _AlertBurst:
        if not signals:
            raise ValueError("signals must not be empty")
        first = signals[0]
        last = signals[-1]
        return _AlertBurst(
            fingerprint=fingerprint,
            severity=first.severity,
            code=first.code,
            title=first.title,
            message=first.message,
            source=first.source,
            first_observed_at=first.observed_at,
            last_observed_at=last.observed_at,
            details=_merge_signal_details(signals),
            occurrence_count=len(signals),
            suppressed_count=max(0, len(signals) - 1),
            bypassed_throttle=False,
        )


def build_observer_monitoring_report(
    journal: AppendOnlyJournal,
    *,
    session_id: str,
    operational_mode: str = "observer",
    audit_report: ObserverAuditReport | None = None,
    replay_report: ReplaySessionReport | None = None,
    comparison_report: ObserverComparisonReport | None = None,
    throttle: AnomalyThrottle | None = None,
) -> MonitoringReport:
    events = tuple(journal.query(correlation_id=session_id))
    if not events:
        raise ValueError(f"session not found: {session_id}")

    from .observer import LIVE_ORDER_PATH_ENABLED

    audit_report = audit_report or build_observer_audit_report(journal, session_id=session_id)
    replay_report = replay_report or build_observer_replay_report(journal, session_id=session_id)
    version_warnings = tuple(audit_report.version_warnings)

    metrics = _build_metrics(events, audit_report=audit_report, replay_report=replay_report, comparison_report=comparison_report)
    alerts = (throttle or AnomalyThrottle()).throttle(
        _build_alert_signals(
            events,
            audit_report=audit_report,
            replay_report=replay_report,
            comparison_report=comparison_report,
            operational_mode=operational_mode,
            live_order_path_enabled=LIVE_ORDER_PATH_ENABLED,
        )
    )

    sections = _build_sections(metrics=metrics, alerts=alerts, operational_mode=operational_mode, replay_consistent=audit_report.replay_consistent)
    return MonitoringReport(
        schema_version=MONITORING_SCHEMA_VERSION,
        session_id=session_id,
        journal_path=str(journal.path),
        operational_mode=operational_mode,
        read_only=True,
        source_truth="journal",
        generated_at=_utc_now(),
        event_count=len(events),
        metrics=metrics,
        sections=sections,
        alerts=alerts,
        replay_consistent=audit_report.replay_consistent,
        version_warnings=version_warnings,
        mismatch_count=0 if comparison_report is None else len(comparison_report.mismatches),
        throttle_window_seconds=(throttle or AnomalyThrottle()).window_seconds,
        live_order_path_enabled=LIVE_ORDER_PATH_ENABLED,
    )


def format_observer_monitoring_report(report: MonitoringReport) -> str:
    return json.dumps(report.to_dict(), indent=2, sort_keys=True)


def render_observer_monitoring_dashboard(report: MonitoringReport) -> str:
    lines = [
        "# Observer Monitoring Dashboard",
        "",
        f"- session_id: `{report.session_id}`",
        f"- operational_mode: `{report.operational_mode}`",
        f"- read_only: `{report.read_only}`",
        f"- source_truth: `{report.source_truth}`",
        f"- replay_consistent: `{report.replay_consistent}`",
        f"- mismatch_count: `{report.mismatch_count}`",
        f"- live_order_path_enabled: `{report.live_order_path_enabled}`",
        "",
        "## Metrics",
    ]
    for metric in report.metrics:
        unit = f" {metric.unit}" if metric.unit else ""
        lines.append(f"- {metric.name}: {metric.value}{unit}")
    lines.append("")
    lines.append("## Alerts")
    if report.alerts:
        for alert in report.alerts:
            suffix = f" (x{alert.occurrence_count}, suppressed {alert.suppressed_count})" if alert.throttled else ""
            bypass = " [throttle bypass]" if alert.bypassed_throttle else ""
            lines.append(f"- [{alert.severity.value}] {alert.code}{suffix}{bypass}: {alert.message}")
    else:
        lines.append("- none")
    lines.append("")
    lines.append("## Sections")
    for section in report.sections:
        lines.append(f"- {section.title}: {section.summary}")
    return "\n".join(lines)


def _build_metrics(
    events: tuple[JournalEvent, ...],
    *,
    audit_report: ObserverAuditReport,
    replay_report: ReplaySessionReport,
    comparison_report: ObserverComparisonReport | None,
) -> tuple[MonitoringMetric, ...]:
    initial_cash_quote = _extract_initial_cash(events)
    final_equity_quote = audit_report.final_equity_quote if audit_report.final_equity_quote is not None else _extract_final_equity(replay_report)
    final_cash_quote = audit_report.final_cash_quote if audit_report.final_cash_quote is not None else _extract_final_cash(replay_report)
    realized_pnl_quote = _extract_realized_pnl(replay_report)
    unrealized_pnl_quote = _extract_unrealized_pnl(replay_report)

    proposals = audit_report.strategy_proposal_count
    decisions = audit_report.decision_count
    fills = audit_report.fill_count
    approved = sum(1 for event in events if event.event_type == EventType.RISK_DECISION and _decision_status(event) == RiskDecisionStatus.APPROVED)
    denied = sum(1 for event in events if event.event_type == EventType.RISK_DECISION and _decision_status(event) == RiskDecisionStatus.DENIED)
    stale_count = _count_reasons(events, RiskReason.STALE_MARKET_DATA)
    drawdown_count = _count_reasons(events, RiskReason.DRAWDOWN_LIMIT_BREACHED)
    max_peak_equity = _max_peak_equity(events, initial_cash_quote=initial_cash_quote)
    drawdown_pct = 0.0 if max_peak_equity <= 0 or final_equity_quote is None else max((max_peak_equity - final_equity_quote) / max_peak_equity, 0.0)
    fill_rate = 0.0 if proposals <= 0 else round(fills / proposals, 6)
    veto_rate = 0.0 if decisions <= 0 else round(denied / decisions, 6)
    comparison_match = None if comparison_report is None else comparison_report.match
    replay_consistent = replay_report.replay_consistent
    replay_warning_count = len(replay_report.version_warnings)

    metric_list = [
        MonitoringMetric("event_count", len(events), description="Total journaled events", source="journal"),
        MonitoringMetric("strategy_proposal_count", proposals, description="Strategy proposals in session", source="journal"),
        MonitoringMetric("risk_decision_count", decisions, description="Risk decisions in session", source="journal"),
        MonitoringMetric("approved_decision_count", approved, description="Approved risk decisions", source="journal"),
        MonitoringMetric("denied_decision_count", denied, description="Denied risk decisions", source="journal"),
        MonitoringMetric("fill_count", fills, description="Simulated fills recorded", source="journal"),
        MonitoringMetric("fill_rate", fill_rate, unit="ratio", description="Fills per proposal", source="journal+replay"),
        MonitoringMetric("veto_rate", veto_rate, unit="ratio", description="Denied decisions per risk decision", source="journal+replay"),
        MonitoringMetric("stale_data_count", stale_count, description="Risk denials caused by stale market data", source="journal"),
        MonitoringMetric("drawdown_breach_count", drawdown_count, description="Risk denials caused by drawdown limits", source="journal"),
        MonitoringMetric("version_warning_count", len(audit_report.version_warnings), description="Audit/replay version warnings", source="journal+replay"),
        MonitoringMetric("replay_version_warning_count", replay_warning_count, description="Replay version warnings", source="journal+replay"),
        MonitoringMetric("replay_consistent", replay_consistent, description="Replay health from journal replay", source="replay"),
        MonitoringMetric("replay_mismatch_count", 0 if comparison_report is None else len(comparison_report.mismatches), description="Comparison mismatches", source="journal+replay"),
        MonitoringMetric("initial_cash_quote", initial_cash_quote, unit="quote", description="Initial shadow cash", source="journal"),
        MonitoringMetric("final_cash_quote", final_cash_quote, unit="quote", description="Final shadow cash", source="journal+replay"),
        MonitoringMetric("final_equity_quote", final_equity_quote, unit="quote", description="Final shadow equity", source="journal+replay"),
        MonitoringMetric("realized_pnl_quote", realized_pnl_quote, unit="quote", description="Realized shadow PnL", source="journal+replay"),
        MonitoringMetric("unrealized_pnl_quote", unrealized_pnl_quote, unit="quote", description="Unrealized shadow PnL", source="journal+replay"),
        MonitoringMetric("max_drawdown_pct", round(drawdown_pct, 6), unit="ratio", description="Peak-to-final-equity drawdown", source="journal+replay"),
        MonitoringMetric("comparison_match", comparison_match, description="Optional comparison result", source="journal+replay"),
    ]
    return tuple(metric_list)


def _build_alert_signals(
    events: tuple[JournalEvent, ...],
    *,
    audit_report: ObserverAuditReport,
    replay_report: ReplaySessionReport,
    comparison_report: ObserverComparisonReport | None,
    operational_mode: str,
    live_order_path_enabled: bool,
) -> tuple[MonitoringSignal, ...]:
    signals: list[MonitoringSignal] = []

    stale_events = [event for event in events if event.event_type == EventType.RISK_DECISION and _decision_has_reason(event, RiskReason.STALE_MARKET_DATA)]
    if stale_events:
        signals.append(
            MonitoringSignal(
                observed_at=stale_events[0].occurred_at,
                severity=AlertSeverity.WARNING,
                code="stale-data",
                title="Stale market data vetoes observed",
                message=f"{len(stale_events)} risk decision(s) denied because market data was stale",
                source="journal",
                details={
                    "trade_ids": [event.trade_id for event in stale_events if event.trade_id],
                    "decision_ids": [event.event_id for event in stale_events],
                    "reason": RiskReason.STALE_MARKET_DATA.value,
                },
                fingerprint=f"stale-data:{_session_id_from_events(events)}",
            )
        )

    drawdown_events = [event for event in events if event.event_type == EventType.RISK_DECISION and _decision_has_reason(event, RiskReason.DRAWDOWN_LIMIT_BREACHED)]
    if drawdown_events:
        signals.append(
            MonitoringSignal(
                observed_at=drawdown_events[0].occurred_at,
                severity=AlertSeverity.WARNING,
                code="drawdown-limit",
                title="Drawdown limit breached",
                message=f"{len(drawdown_events)} risk decision(s) were denied by drawdown control",
                source="journal",
                details={
                    "decision_ids": [event.event_id for event in drawdown_events],
                    "reason": RiskReason.DRAWDOWN_LIMIT_BREACHED.value,
                },
                fingerprint=f"drawdown-limit:{_session_id_from_events(events)}",
            )
        )

    if audit_report.version_warnings:
        signals.append(
            MonitoringSignal(
                observed_at=_session_start_time(events),
                severity=AlertSeverity.WARNING,
                code="version-warning",
                title="Replay version warning",
                message=f"{len(audit_report.version_warnings)} version warning(s) detected during audit/replay",
                source="replay",
                details={"warnings": list(audit_report.version_warnings)},
                fingerprint=f"version-warning:{_session_id_from_events(events)}",
            )
        )

    if not replay_report.replay_consistent:
        signals.append(
            MonitoringSignal(
                observed_at=_session_end_time(events),
                severity=AlertSeverity.CRITICAL,
                code="replay-mismatch",
                title="Replay inconsistency detected",
                message="Replay report marked the session as replay-inconsistent",
                source="replay",
                details={
                    "mismatch_count": len(audit_report.mismatches),
                    "trade_ids": [trace.trade_id for trace in audit_report.trade_traces],
                    "replay_version_warnings": list(replay_report.version_warnings),
                },
                critical_bypass=True,
                fingerprint=f"replay-mismatch:{_session_id_from_events(events)}",
            )
        )

    if comparison_report is not None and comparison_report.mismatches:
        for mismatch in comparison_report.mismatches:
            severity = AlertSeverity.CRITICAL if mismatch.severity == "error" else AlertSeverity.WARNING
            signals.append(
                MonitoringSignal(
                    observed_at=_session_end_time(events),
                    severity=severity,
                    code="comparison-mismatch",
                    title=f"Comparison mismatch: {mismatch.field}",
                    message=f"reference={mismatch.expected!r}; candidate={mismatch.actual!r}",
                    source="comparison",
                    details={
                        "field": mismatch.field,
                        "expected": mismatch.expected,
                        "actual": mismatch.actual,
                        "severity": mismatch.severity,
                    },
                    critical_bypass=severity == AlertSeverity.CRITICAL,
                    fingerprint=_comparison_fingerprint(mismatch, session_id=_session_id_from_events(events)),
                )
            )

    if operational_mode == "live" and not live_order_path_enabled:
        signals.append(
            MonitoringSignal(
                observed_at=_session_end_time(events),
                severity=AlertSeverity.CRITICAL,
                code="live-mode-disabled",
                title="Live mode requested while live order path is disabled",
                message="Operational mode is live but the runtime still has live_order_path_enabled=false",
                source="control-plane",
                details={
                    "operational_mode": operational_mode,
                    "live_order_path_enabled": live_order_path_enabled,
                },
                critical_bypass=True,
                fingerprint=f"live-mode-disabled:{_session_id_from_events(events)}",
            )
        )

    return tuple(signals)


def _build_sections(
    *,
    metrics: tuple[MonitoringMetric, ...],
    alerts: tuple[MonitoringAlert, ...],
    operational_mode: str,
    replay_consistent: bool,
) -> tuple[MonitoringSection, ...]:
    alert_ids = tuple(alert.alert_id for alert in alerts)
    critical_ids = tuple(alert.alert_id for alert in alerts if alert.severity == AlertSeverity.CRITICAL)
    warning_ids = tuple(alert.alert_id for alert in alerts if alert.severity == AlertSeverity.WARNING)
    sections = (
        MonitoringSection(
            title="Session health",
            summary=f"Operational mode={operational_mode}; replay_consistent={replay_consistent}; alerts={len(alerts)}",
            metric_names=("event_count", "replay_consistent", "comparison_match", "replay_mismatch_count"),
            alert_ids=alert_ids,
        ),
        MonitoringSection(
            title="Risk controls",
            summary="Risk vetoes, stale-data protection, and drawdown limits",
            metric_names=("risk_decision_count", "approved_decision_count", "denied_decision_count", "veto_rate", "stale_data_count", "drawdown_breach_count", "max_drawdown_pct"),
            alert_ids=warning_ids,
        ),
        MonitoringSection(
            title="Execution and PnL",
            summary="Fill behavior and shadow portfolio accounting",
            metric_names=("strategy_proposal_count", "fill_count", "fill_rate", "final_cash_quote", "final_equity_quote", "realized_pnl_quote", "unrealized_pnl_quote"),
            alert_ids=critical_ids,
        ),
        MonitoringSection(
            title="Data quality and replayability",
            summary="Version warnings, replay mismatches, and throttled anomalies",
            metric_names=("version_warning_count", "replay_version_warning_count", "replay_mismatch_count", "comparison_match"),
            alert_ids=alert_ids,
        ),
    )
    return sections


def _merge_signal_details(signals: Sequence[MonitoringSignal]) -> dict[str, object]:
    merged: dict[str, object] = {}
    details_list = [signal.details for signal in signals if signal.details]
    if details_list:
        merged["details_samples"] = details_list[:3]
    merged["sources"] = sorted({signal.source for signal in signals})
    merged["codes"] = sorted({signal.code for signal in signals})
    merged["titles"] = sorted({signal.title for signal in signals})
    return merged


def _fingerprint_signal(signal: MonitoringSignal) -> str:
    canonical = json.dumps(
        {
            "severity": signal.severity.value,
            "code": signal.code,
            "title": signal.title,
            "message": signal.message,
            "source": signal.source,
            "details": signal.details,
        },
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:20]


def _alert_id(fingerprint: str, observed_at: datetime, code: str, occurrence_count: int, sequence_number: int) -> str:
    canonical = f"{fingerprint}|{_fmt_dt(observed_at)}|{code}|{occurrence_count}|{sequence_number}"
    return f"alert-{hashlib.sha256(canonical.encode('utf-8')).hexdigest()[:16]}"


def _comparison_fingerprint(mismatch, *, session_id: str) -> str:
    canonical = json.dumps(
        {
            "session_id": session_id,
            "field": mismatch.field,
            "expected": mismatch.expected,
            "actual": mismatch.actual,
            "severity": mismatch.severity,
        },
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:20]


def _session_id_from_events(events: Sequence[JournalEvent]) -> str:
    if not events:
        return "unknown-session"
    return events[0].correlation_id


def _session_start_time(events: Sequence[JournalEvent]) -> datetime:
    if not events:
        return _utc_now()
    return min(event.occurred_at for event in events)


def _session_end_time(events: Sequence[JournalEvent]) -> datetime:
    if not events:
        return _utc_now()
    return max(event.occurred_at for event in events)


def _count_reasons(events: Sequence[JournalEvent], reason: RiskReason) -> int:
    return sum(1 for event in events if event.event_type == EventType.RISK_DECISION and _decision_has_reason(event, reason))


def _decision_has_reason(event: JournalEvent, reason: RiskReason) -> bool:
    payload = event.payload or {}
    return reason.value in {str(item) for item in payload.get("reasons", [])}


def _decision_status(event: JournalEvent) -> RiskDecisionStatus:
    payload = event.payload or {}
    return RiskDecisionStatus(str(payload["status"]))


def _extract_initial_cash(events: Sequence[JournalEvent]) -> float:
    start_event = next((event for event in events if event.event_type == EventType.OBSERVER_SESSION_START), None)
    if start_event is None:
        return 0.0
    return float((start_event.payload or {}).get("initial_cash_quote", 0.0))


def _extract_final_cash(report: ReplaySessionReport) -> float:
    return float(report.portfolio.get("cash_quote", 0.0))


def _extract_final_equity(report: ReplaySessionReport) -> float:
    return float(report.portfolio.get("equity_quote", 0.0))


def _extract_realized_pnl(report: ReplaySessionReport) -> float:
    return float(report.portfolio.get("realized_pnl_quote", 0.0))


def _extract_unrealized_pnl(report: ReplaySessionReport) -> float:
    return float(report.portfolio.get("unrealized_pnl_quote", 0.0))


def _max_peak_equity(events: Sequence[JournalEvent], *, initial_cash_quote: float) -> float:
    peak = initial_cash_quote
    for event in events:
        if event.event_type != EventType.RISK_DECISION:
            continue
        payload = event.payload or {}
        for state_key in ("state_before", "next_state"):
            state = payload.get(state_key)
            if isinstance(state, dict):
                peak = max(peak, float(state.get("peak_equity_quote", peak)))
    return peak


def _fmt_dt(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)
