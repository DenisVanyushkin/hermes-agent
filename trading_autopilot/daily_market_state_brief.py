from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

from .canonical_journal import CANONICAL_JOURNAL_SCHEMA_VERSION, CANONICAL_JOURNAL_SOURCE_MODULE, CanonicalJournal
from .journal import AppendOnlyJournal, EventType, JournalEvent

DAILY_MARKET_STATE_BRIEF_SCHEMA_VERSION = "1.0.0"
DAILY_MARKET_STATE_BRIEF_TEMPLATE = """Daily Market State Brief
Window: {window_start} → {window_end}
Freshness basis: observed_at
Trust level: {trust_level}
Assets: {asset_count}
"""
EXPECTED_MARKET_SOURCES = ("binance.spot", "binance.futures", "coinbase.spot")
DAILY_MARKET_STATE_SOURCE_STALE_AFTER_MINUTES = 30


def _fmt_dt(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _parse_dt(value: object, fallback: datetime | None = None) -> datetime | None:
    if value is None:
        return fallback
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)


def _coerce_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True, slots=True)
class DailyMarketStateAssetBrief:
    symbol: str
    latest_source: str
    latest_observed_at: datetime
    latest_collected_at: datetime | None
    price: float | None
    volume: float | None
    quote_volume: float | None
    spread_bps: float | None = None
    funding: float | None = None
    open_interest: float | None = None
    liquidations: dict[str, float] | None = None
    price_change_bps: float | None = None
    evidence: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "latest_source": self.latest_source,
            "latest_observed_at": _fmt_dt(self.latest_observed_at),
            "latest_collected_at": None if self.latest_collected_at is None else _fmt_dt(self.latest_collected_at),
            "price": self.price,
            "volume": self.volume,
            "quote_volume": self.quote_volume,
            "spread_bps": self.spread_bps,
            "funding": self.funding,
            "open_interest": self.open_interest,
            "liquidations": self.liquidations,
            "price_change_bps": self.price_change_bps,
            "evidence": self.evidence,
        }


@dataclass(frozen=True, slots=True)
class DailyMarketStateSourceStatus:
    source: str
    freshness_basis: str
    last_observed_at: datetime | None
    last_collected_at: datetime | None
    age_minutes: float | None
    status: str

    def to_dict(self) -> dict[str, object]:
        return {
            "source": self.source,
            "freshness_basis": self.freshness_basis,
            "last_observed_at": None if self.last_observed_at is None else _fmt_dt(self.last_observed_at),
            "last_collected_at": None if self.last_collected_at is None else _fmt_dt(self.last_collected_at),
            "age_minutes": self.age_minutes,
            "status": self.status,
        }


@dataclass(frozen=True, slots=True)
class DailyMarketStateBriefReport:
    schema_version: str
    generated_at: datetime
    window_start: datetime
    window_end: datetime
    freshness_basis: str
    trust_level: str
    assets: tuple[DailyMarketStateAssetBrief, ...]
    source_statuses: tuple[DailyMarketStateSourceStatus, ...]
    missing_sources: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "generated_at": _fmt_dt(self.generated_at),
            "window_start": _fmt_dt(self.window_start),
            "window_end": _fmt_dt(self.window_end),
            "freshness_basis": self.freshness_basis,
            "trust_level": self.trust_level,
            "assets": [asset.to_dict() for asset in self.assets],
            "source_statuses": [status.to_dict() for status in self.source_statuses],
            "missing_sources": list(self.missing_sources),
        }


def _coerce_journal(journal: AppendOnlyJournal | CanonicalJournal | Path | str) -> AppendOnlyJournal | CanonicalJournal:
    if isinstance(journal, (AppendOnlyJournal, CanonicalJournal)):
        return journal
    return CanonicalJournal(journal)


def build_daily_market_state_brief(
    journal: AppendOnlyJournal | CanonicalJournal | Path | str,
    *,
    generated_at: datetime | None = None,
    window_hours: int = 24,
) -> DailyMarketStateBriefReport:
    canonical_journal = _coerce_journal(journal)
    generated_at = generated_at.astimezone(timezone.utc) if generated_at is not None else datetime.now(timezone.utc)
    window_end = generated_at
    window_start = window_end - timedelta(hours=window_hours)
    events = list(canonical_journal.query(start_time=window_start, end_time=window_end))
    observations = [event for event in events if event.event_type in {EventType.MARKET_TICK, EventType.MARKET_SNAPSHOT}]

    latest_by_source: dict[str, JournalEvent] = {}
    latest_by_symbol: dict[str, list[JournalEvent]] = {}
    for event in observations:
        payload = event.payload or {}
        source = _source_name(event, payload)
        symbol = _symbol_name(event, payload)
        if source and (source not in latest_by_source or _event_sort_key(event, payload) > _event_sort_key(latest_by_source[source], latest_by_source[source].payload or {})):
            latest_by_source[source] = event
        if symbol:
            latest_by_symbol.setdefault(symbol, []).append(event)

    source_statuses = tuple(
        _build_source_status(source, latest_by_source.get(source), window_end=window_end)
        for source in EXPECTED_MARKET_SOURCES
    )
    missing_sources = tuple(status.source for status in source_statuses if status.status == "missing")

    assets = tuple(
        _build_asset_brief(symbol, latest_events, window_end=window_end)
        for symbol, latest_events in sorted(latest_by_symbol.items())
    )
    trust_level = _trust_level(assets, source_statuses)
    return DailyMarketStateBriefReport(
        schema_version=DAILY_MARKET_STATE_BRIEF_SCHEMA_VERSION,
        generated_at=generated_at,
        window_start=window_start,
        window_end=window_end,
        freshness_basis="observed_at",
        trust_level=trust_level,
        assets=assets,
        source_statuses=source_statuses,
        missing_sources=missing_sources,
    )


def format_daily_market_state_brief(report: DailyMarketStateBriefReport) -> str:
    lines = [
        "Daily Market State Brief",
        f"Window: {_fmt_dt(report.window_start)} → {_fmt_dt(report.window_end)}",
        f"Freshness basis: {report.freshness_basis}",
        f"Trust level: {report.trust_level}",
        f"Assets: {len(report.assets)}",
    ]
    for asset in report.assets:
        lines.append(f"- {asset.symbol}: {asset.evidence}")
    return "\n".join(lines)


def _source_name(event: JournalEvent, payload: dict[str, object]) -> str:
    source = payload.get("source") or payload.get("venue") or event.source_module
    return str(source).strip().lower()


def _symbol_name(event: JournalEvent, payload: dict[str, object]) -> str:
    symbol = payload.get("symbol") or event.symbol or ""
    return str(symbol).strip().upper()


def _event_sort_key(event: JournalEvent, payload: dict[str, object]) -> tuple[datetime, datetime, str]:
    observed_at = _parse_dt(payload.get("observed_at"), fallback=event.occurred_at)
    collected_at = _parse_dt(payload.get("collected_at"), fallback=observed_at)
    return observed_at or event.occurred_at, collected_at or event.occurred_at, event.event_id


def _build_source_status(source: str, event: JournalEvent | None, *, window_end: datetime) -> DailyMarketStateSourceStatus:
    if event is None:
        return DailyMarketStateSourceStatus(
            source=source,
            freshness_basis="observed_at",
            last_observed_at=None,
            last_collected_at=None,
            age_minutes=None,
            status="missing",
        )
    payload = event.payload or {}
    last_observed_at = _parse_dt(payload.get("observed_at"), fallback=event.occurred_at)
    last_collected_at = _parse_dt(payload.get("collected_at"), fallback=None)
    age_minutes = None if last_observed_at is None else round((window_end - last_observed_at).total_seconds() / 60.0, 2)
    status = "fresh" if age_minutes is not None and age_minutes <= DAILY_MARKET_STATE_SOURCE_STALE_AFTER_MINUTES else "stale"
    return DailyMarketStateSourceStatus(
        source=source,
        freshness_basis="observed_at",
        last_observed_at=last_observed_at,
        last_collected_at=last_collected_at,
        age_minutes=age_minutes,
        status=status,
    )


def _build_asset_brief(symbol: str, events: list[JournalEvent], *, window_end: datetime) -> DailyMarketStateAssetBrief:
    ordered = sorted(events, key=lambda event: _event_sort_key(event, event.payload or {}))
    latest = ordered[-1]
    latest_payload = latest.payload or {}
    first_payload = ordered[0].payload or {}
    latest_observed_at = _parse_dt(latest_payload.get("observed_at"), fallback=latest.occurred_at) or latest.occurred_at
    latest_collected_at = _parse_dt(latest_payload.get("collected_at"), fallback=None)
    first_price = _coerce_float(first_payload.get("price"))
    latest_price = _coerce_float(latest_payload.get("price"))
    price_change_bps = None
    if first_price and latest_price:
        price_change_bps = round(((latest_price - first_price) / first_price) * 10000.0, 2)
    liquidations = latest_payload.get("liquidations")
    if isinstance(liquidations, dict):
        liquidations = {str(key): _coerce_float(value) for key, value in liquidations.items() if _coerce_float(value) is not None}
    else:
        liquidations = None
    evidence = (
        f"{symbol} last seen on {_source_name(latest, latest_payload)} at {_fmt_dt(latest_observed_at)} "
        f"with price {latest_price if latest_price is not None else 'n/a'}"
    )
    return DailyMarketStateAssetBrief(
        symbol=symbol,
        latest_source=_source_name(latest, latest_payload),
        latest_observed_at=latest_observed_at,
        latest_collected_at=latest_collected_at,
        price=latest_price,
        volume=_coerce_float(latest_payload.get("volume")),
        quote_volume=_coerce_float(latest_payload.get("quote_volume") or latest_payload.get("quoteVolume")),
        spread_bps=_coerce_float(latest_payload.get("spread_bps")),
        funding=_coerce_float(latest_payload.get("funding")),
        open_interest=_coerce_float(latest_payload.get("open_interest") or latest_payload.get("openInterest")),
        liquidations=liquidations,
        price_change_bps=price_change_bps,
        evidence=evidence,
    )


def _trust_level(assets: tuple[DailyMarketStateAssetBrief, ...], source_statuses: tuple[DailyMarketStateSourceStatus, ...]) -> str:
    present_sources = {status.source for status in source_statuses if status.status != "missing"}
    if len(assets) >= 2 and len(present_sources) >= 2:
        return "high"
    if assets:
        return "medium"
    return "low"
