from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from statistics import median
from time import perf_counter
from typing import Any, Iterator


@dataclass
class PerformanceSpan:
    run_id: int
    span_name: str
    parent_span_name: str | None = None
    source_name: str | None = None
    started_at: str = ""
    finished_at: str = ""
    duration_ms: int = 0
    status: str = "ok"
    found_count: int | None = None
    normalized_count: int | None = None
    accepted_count: int | None = None
    duplicate_count: int | None = None
    rejected_count: int | None = None
    new_card_keys: int | None = None
    cards_sent: int | None = None
    error_count: int | None = None
    retry_count: int | None = None
    timeout_count: int | None = None
    metadata_json: str | None = None


@dataclass
class _SpanState:
    recorder: "RunPerformanceRecorder"
    run_id: int
    span_name: str
    parent_span_name: str | None
    source_name: str | None
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    finished_at: str = ""
    duration_ms: int = 0
    status: str = "ok"
    found_count: int | None = None
    normalized_count: int | None = None
    accepted_count: int | None = None
    duplicate_count: int | None = None
    rejected_count: int | None = None
    new_card_keys: int | None = None
    cards_sent: int | None = None
    error_count: int | None = None
    retry_count: int | None = None
    timeout_count: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    _started_perf: float = field(default_factory=perf_counter)

    def set_counts(self, **kwargs: Any) -> None:
        for key, value in kwargs.items():
            if hasattr(self, key):
                setattr(self, key, value)

    def add_metadata(self, **kwargs: Any) -> None:
        for key, value in kwargs.items():
            if value is not None:
                self.metadata[key] = value

    def set_status(self, status: str) -> None:
        self.status = status

    def finish(self, status: str | None = None) -> PerformanceSpan:
        if status:
            self.status = status
        self.finished_at = datetime.now(timezone.utc).isoformat()
        self.duration_ms = max(0, int(round((perf_counter() - self._started_perf) * 1000)))
        return PerformanceSpan(
            run_id=self.run_id,
            span_name=self.span_name,
            parent_span_name=self.parent_span_name,
            source_name=self.source_name,
            started_at=self.started_at,
            finished_at=self.finished_at,
            duration_ms=self.duration_ms,
            status=self.status,
            found_count=self.found_count,
            normalized_count=self.normalized_count,
            accepted_count=self.accepted_count,
            duplicate_count=self.duplicate_count,
            rejected_count=self.rejected_count,
            new_card_keys=self.new_card_keys,
            cards_sent=self.cards_sent,
            error_count=self.error_count,
            retry_count=self.retry_count,
            timeout_count=self.timeout_count,
            metadata_json=json.dumps(self.metadata, ensure_ascii=False) if self.metadata else None,
        )


class RunPerformanceRecorder:
    def __init__(self, run_id: int) -> None:
        self.run_id = run_id
        self._spans: list[PerformanceSpan] = []

    @contextmanager
    def span(
        self,
        span_name: str,
        *,
        parent_span_name: str | None = None,
        source_name: str | None = None,
    ) -> Iterator[_SpanState]:
        state = _SpanState(
            recorder=self,
            run_id=self.run_id,
            span_name=span_name,
            parent_span_name=parent_span_name,
            source_name=source_name,
        )
        try:
            yield state
        except Exception as exc:
            state.set_status("error")
            state.add_metadata(error=str(exc))
            self._spans.append(state.finish())
            raise
        else:
            self._spans.append(state.finish())

    def record_completed(
        self,
        span_name: str,
        *,
        duration_ms: int,
        parent_span_name: str | None = None,
        source_name: str | None = None,
        status: str = "ok",
        metadata: dict[str, Any] | None = None,
        **counts: Any,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        payload = PerformanceSpan(
            run_id=self.run_id,
            span_name=span_name,
            parent_span_name=parent_span_name,
            source_name=source_name,
            started_at=now,
            finished_at=now,
            duration_ms=max(0, int(duration_ms)),
            status=status,
            found_count=counts.get("found_count"),
            normalized_count=counts.get("normalized_count"),
            accepted_count=counts.get("accepted_count"),
            duplicate_count=counts.get("duplicate_count"),
            rejected_count=counts.get("rejected_count"),
            new_card_keys=counts.get("new_card_keys"),
            cards_sent=counts.get("cards_sent"),
            error_count=counts.get("error_count"),
            retry_count=counts.get("retry_count"),
            timeout_count=counts.get("timeout_count"),
            metadata_json=json.dumps(metadata, ensure_ascii=False) if metadata else None,
        )
        self._spans.append(payload)

    def spans(self) -> list[PerformanceSpan]:
        return list(self._spans)


def _duration_label(duration_ms: int) -> str:
    seconds = max(0, int(round(duration_ms / 1000.0)))
    minutes, sec = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h{minutes:02d}m{sec:02d}s"
    if minutes:
        return f"{minutes}m{sec:02d}s"
    return f"{sec}s"


def performance_trigger_reason(
    *,
    total_runtime_ms: int,
    recent_runtime_ms: list[int],
    source_rows: list[dict[str, Any]],
) -> list[str]:
    reasons: list[str] = []
    if total_runtime_ms >= 45 * 60 * 1000:
        reasons.append("runtime_over_45m")
    if any((row.get("actionable_count") or 0) == 0 and (row.get("new_card_keys") or 0) == 0 and (row.get("cards_sent") or 0) == 0 and (row.get("duration_ms") or 0) >= 5 * 60 * 1000 for row in source_rows):
        reasons.append("high_cost_zero_output_source")
    if any((row.get("error_count") or 0) > 0 or (row.get("timeout_count") or 0) > 0 or str(row.get("status") or "") == "error" for row in source_rows):
        reasons.append("source_error_or_timeout")
    if recent_runtime_ms:
        baseline = median(recent_runtime_ms)
        if baseline > 0 and total_runtime_ms > baseline * 1.25:
            reasons.append("runtime_regression_gt_25pct")
    return reasons


def build_source_value_rows(
    *,
    source_counts: dict[str, dict[str, Any]],
    source_statuses: dict[str, dict[str, Any]],
    card_decisions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    by_source_cards: dict[str, list[dict[str, Any]]] = {}
    for row in card_decisions:
        source = str(row.get("source") or "")
        by_source_cards.setdefault(source, []).append(row)

    out: list[dict[str, Any]] = []
    for source in sorted(source_statuses.keys()):
        stats = source_counts.get(source) or {}
        status = source_statuses.get(source) or {}
        cards = by_source_cards.get(source, [])
        raw_found = int(status.get("hits") or stats.get("raw_found_count") or stats.get("found_count") or 0)
        normalized_count = int(stats.get("found_count") or 0)
        scored_count = int(stats.get("scored_count") or 0)
        accepted = int(stats.get("accepted_count") or 0)
        strong_fit = int(stats.get("strong_fit_count") or 0)
        potential_fit = int(stats.get("potential_fit_count") or 0)
        needs_review = int(stats.get("needs_review_count") or 0)
        near_miss = int(stats.get("near_miss_count") or 0)
        actionable_count = strong_fit + potential_fit + needs_review + near_miss
        deduped = int(stats.get("vacancies_deduped") or 0)
        duplicate_count = max(0, raw_found - deduped)
        duration_ms = int(round(float(status.get("runtime_seconds") or 0.0) * 1000))
        sent = sum(1 for row in cards if str(row.get("decision") or "") == "sent")
        candidate_count = len(cards)
        suppressed_count = sum(1 for row in cards if str(row.get("decision") or "") == "suppressed")
        skipped_limit_count = sum(1 for row in cards if str(row.get("decision") or "") == "skipped_limit")
        new_card_keys = sum(1 for row in cards if row.get("previous_sent_run_id") is None)
        errors = list(status.get("errors") or [])
        session = status.get("session_health") or {}
        timeout_count = sum(1 for err in errors if "timeout" in str(err).lower())
        retry_count = int(status.get("retries") or 0)
        error_count = len(errors)
        duration_minutes = duration_ms / 60000.0 if duration_ms else 0.0
        actionable_per_minute = (actionable_count / duration_minutes) if duration_minutes else None
        new_cards_per_minute = (new_card_keys / duration_minutes) if duration_minutes else None
        cost_per_actionable_ms = (duration_ms / actionable_count) if actionable_count else None
        cost_per_new_card_key_ms = (duration_ms / new_card_keys) if new_card_keys else None
        out.append(
            {
                "source": source,
                "status": str(status.get("status") or "unknown"),
                "duration_ms": duration_ms,
                "raw_found": raw_found,
                "normalized_count": normalized_count,
                "scored_count": scored_count,
                "found_count": raw_found,
                "accepted_count": accepted,
                "actionable_count": actionable_count,
                "card_candidate_count": candidate_count,
                "suppressed_count": suppressed_count,
                "skipped_limit_count": skipped_limit_count,
                "strong_fit_count": strong_fit,
                "potential_fit_count": potential_fit,
                "needs_review_count": needs_review,
                "near_miss_count": near_miss,
                "duplicate_count": duplicate_count,
                "duplicate_rate": (duplicate_count / raw_found) if raw_found else None,
                "new_card_keys": new_card_keys,
                "cards_sent": sent,
                "error_count": error_count,
                "timeout_count": timeout_count,
                "retry_count": retry_count,
                "pages_fetched": session.get("pages_fetched") if session else status.get("pages_fetched"),
                "tenants_checked": status.get("registry_companies_attempted"),
                "actionable_per_minute": actionable_per_minute,
                "new_cards_per_minute": new_cards_per_minute,
                "cost_per_actionable_ms": cost_per_actionable_ms,
                "cost_per_new_card_key_ms": cost_per_new_card_key_ms,
            }
        )
    out.sort(key=lambda row: row["duration_ms"], reverse=True)
    return out


def format_compact_performance_block(
    *,
    total_runtime_ms: int,
    source_rows: list[dict[str, Any]],
    reasons: list[str],
) -> str:
    if not reasons:
        return ""
    lines = ["*Performance*"]
    lines.append(f"• Runtime: {_duration_label(total_runtime_ms)}")
    top_rows = source_rows[:3]
    if top_rows:
        slowest = ", ".join(f"{row['source']} {_duration_label(int(row['duration_ms']))}" for row in top_rows)
        lines.append(f"• Slowest sources: {slowest}")
    high_cost = [
        row for row in source_rows
        if row["duration_ms"] >= 5 * 60 * 1000 and row["actionable_count"] == 0 and row["new_card_keys"] == 0 and row["cards_sent"] == 0
    ]
    if high_cost:
        row = high_cost[0]
        lines.append(
            f"• High-cost zero-output: {row['source']} {_duration_label(int(row['duration_ms']))} / "
            f"{row['raw_found']} raw_found / {row['actionable_count']} actionable / {row['cards_sent']} cards"
        )
    if "runtime_regression_gt_25pct" in reasons:
        lines.append("• Recommendation: runtime regressed vs recent median; inspect slowest sources")
    elif high_cost:
        lines.append("• Recommendation: consider quarantine if repeated 2 runs")
    return "\n".join(lines)


def format_detailed_performance_report(
    *,
    run_id: int,
    total_runtime_ms: int,
    phase_rows: list[dict[str, Any]],
    source_rows: list[dict[str, Any]],
    reasons: list[str],
) -> str:
    lines = [f"*Daily Run Performance* — run_id={run_id}", ""]
    lines.append(f"Runtime: {_duration_label(total_runtime_ms)}")
    if reasons:
        lines.append(f"Triggers: {', '.join(reasons)}")
    lines.append("")
    lines.append("*Runtime by Phase*")
    for row in sorted(phase_rows, key=lambda item: int(item.get('duration_ms') or 0), reverse=True)[:12]:
        lines.append(f"• {row['span_name']}: {_duration_label(int(row['duration_ms'] or 0))} [{row.get('status') or 'ok'}]")
    lines.append("")
    lines.append("*Runtime by Source*")
    for row in source_rows:
        lines.append(
            f"• {row['source']}: {_duration_label(int(row['duration_ms']))} | raw={row['raw_found']} | "
            f"normalized={row['normalized_count']} | scored={row['scored_count']} | actionable={row['actionable_count']} | "
            f"candidates={row['card_candidate_count']} | sent={row['cards_sent']} | suppressed={row['suppressed_count']} | "
            f"new_card_keys={row['new_card_keys']}"
        )
    lines.append("")
    lines.append("*High-cost Zero-output Sources*")
    high_cost = False
    for row in source_rows:
        if row["duration_ms"] >= 5 * 60 * 1000 and row["actionable_count"] == 0 and row["new_card_keys"] == 0 and row["cards_sent"] == 0:
            high_cost = True
            lines.append(
                f"• {row['source']}: {_duration_label(int(row['duration_ms']))} | raw={row['raw_found']} | "
                f"normalized={row['normalized_count']} | scored={row['scored_count']} | actionable={row['actionable_count']} | "
                f"duplicate_rate={_percent(row.get('duplicate_rate'))}"
            )
    if not high_cost:
        lines.append("• none")
    lines.append("")
    lines.append("*Recommendation*")
    if high_cost:
        lines.append("• Investigate or quarantine repeated high-cost zero-output sources first.")
    else:
        lines.append("• Runtime increase is distributed; inspect top phase/source spans before tuning.")
    return "\n".join(lines).rstrip()


def _percent(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value * 100:.1f}%"


def summarize_spans(spans: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    phase_rows = [row for row in spans if not str(row.get("span_name") or "").startswith("source_acquisition.")]
    source_rows = [row for row in spans if str(row.get("span_name") or "").startswith("source_acquisition.")]
    return phase_rows, source_rows


def rows_to_text_table(rows: list[dict[str, Any]]) -> str:
    lines = []
    for row in rows:
        source = str(row.get("source") or row.get("span_name") or "unknown")
        duration = _duration_label(int(row.get("duration_ms") or 0))
        lines.append(f"- {source}: {duration}")
    return "\n".join(lines)


def build_cli_performance_report(*, runs: list[dict[str, Any]], spans_by_run: dict[int, list[dict[str, Any]]]) -> str:
    lines = ["Job Intel Performance Trend", ""]
    lines.append("*Runtime trend*")
    for run in runs:
        run_id = int(run["id"])
        daily = next((row for row in spans_by_run.get(run_id, []) if row.get("span_name") == "daily_run_total"), None)
        if not daily:
            continue
        lines.append(f"• run_id={run_id}: {_duration_label(int(daily.get('duration_ms') or 0))} [{run.get('status')}]")
    lines.append("")
    lines.append("*Source runtime trend*")
    for run in runs:
        run_id = int(run["id"])
        source_rows = [row for row in spans_by_run.get(run_id, []) if str(row.get("span_name") or "").startswith("source_acquisition.")]
        if not source_rows:
            continue
        top = sorted(source_rows, key=lambda row: int(row.get("duration_ms") or 0), reverse=True)[:3]
        lines.append(f"• run_id={run_id}: " + ", ".join(
            f"{str(row.get('source_name') or row.get('span_name')).replace('source_acquisition.', '')} {_duration_label(int(row.get('duration_ms') or 0))}"
            for row in top
        ))
    return "\n".join(lines).rstrip()
