from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterable, Sequence

import requests

from .canonical_journal import CANONICAL_JOURNAL_SOURCE_MODULE, CanonicalJournal, CanonicalJournalRecord
from .journal import AppendOnlyJournal, EventType, JournalDuplicateEventError, JournalEvent

MARKET_OBSERVATION_SCHEMA_VERSION = "1.0.0"
EXPECTED_MARKET_SOURCES = ("binance.spot", "binance.futures", "coinbase.spot")
DEFAULT_MARKET_SYMBOLS = ("BTC", "ETH")
DEFAULT_MARKET_LOOKBACK_HOURS = 24
DEFAULT_MARKET_STALE_AFTER_MINUTES = 30
DEFAULT_BINANCE_SPOT_BASE_URL = "https://api.binance.com"
DEFAULT_BINANCE_FUTURES_BASE_URL = "https://fapi.binance.com"
DEFAULT_COINBASE_BASE_URL = "https://api.exchange.coinbase.com"


class MarketObservationError(RuntimeError):
    """Base error for market ingestion normalization and collection."""


class MissingObservationFieldError(MarketObservationError):
    """Raised when a required normalized observation field is absent."""


class MalformedObservationPayloadError(MarketObservationError):
    """Raised when a source payload cannot be normalized deterministically."""


class DuplicateObservationError(MarketObservationError):
    """Raised when two identical observations map to the same canonical event id."""


class StaleObservationError(MarketObservationError):
    """Raised when a source observation is outside the allowed freshness threshold."""


@dataclass(frozen=True, slots=True)
class NormalizedMarketObservation:
    schema_version: str
    source: str
    symbol: str
    observed_at: datetime
    collected_at: datetime
    price: float
    volume: float
    quote_volume: float
    spread_bps: float | None = None
    funding: float | None = None
    open_interest: float | None = None
    liquidations: dict[str, float] | None = None
    venue_metadata: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("observed_at must be timezone-aware")
        if self.collected_at.tzinfo is None or self.collected_at.utcoffset() is None:
            raise ValueError("collected_at must be timezone-aware")
        if not self.source:
            raise ValueError("source must be set")
        if not self.symbol:
            raise ValueError("symbol must be set")
        if self.price <= 0:
            raise ValueError("price must be positive")
        if self.volume < 0:
            raise ValueError("volume must be non-negative")
        if self.quote_volume < 0:
            raise ValueError("quote_volume must be non-negative")
        if self.venue_metadata is None:
            object.__setattr__(self, "venue_metadata", {})
        if self.liquidations is not None and not isinstance(self.liquidations, dict):
            raise ValueError("liquidations must be a mapping when provided")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "source": self.source,
            "symbol": self.symbol,
            "observed_at": _fmt_dt(self.observed_at),
            "collected_at": _fmt_dt(self.collected_at),
            "price": self.price,
            "volume": self.volume,
            "quote_volume": self.quote_volume,
            "spread_bps": self.spread_bps,
            "funding": self.funding,
            "open_interest": self.open_interest,
            "liquidations": self.liquidations,
            "venue_metadata": self.venue_metadata,
        }


@dataclass(frozen=True, slots=True)
class MarketCollectionReport:
    schema_version: str
    generated_at: datetime
    journal_path: str
    symbols: tuple[str, ...]
    sources: tuple[str, ...]
    observations_written: int
    duplicate_observations: int
    counts_by_source: dict[str, int]
    counts_by_symbol: dict[str, int]
    counts_by_event_type: dict[str, int]
    source_freshness: dict[str, dict[str, object]]
    source_errors: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "generated_at": _fmt_dt(self.generated_at),
            "journal_path": self.journal_path,
            "symbols": list(self.symbols),
            "sources": list(self.sources),
            "observations_written": self.observations_written,
            "duplicate_observations": self.duplicate_observations,
            "counts_by_source": dict(self.counts_by_source),
            "counts_by_symbol": dict(self.counts_by_symbol),
            "counts_by_event_type": dict(self.counts_by_event_type),
            "source_freshness": {key: dict(value) for key, value in self.source_freshness.items()},
            "source_errors": list(self.source_errors),
        }


@dataclass(frozen=True, slots=True)
class MarketIngestionStatusReport:
    schema_version: str
    generated_at: datetime
    journal_path: str
    window_start: datetime
    window_end: datetime
    events_last_24h: int
    counts_by_source: dict[str, int]
    counts_by_event_type: dict[str, int]
    oldest_observation_at: datetime | None
    newest_observation_at: datetime | None
    source_freshness: dict[str, dict[str, object]]

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "generated_at": _fmt_dt(self.generated_at),
            "journal_path": self.journal_path,
            "window_start": _fmt_dt(self.window_start),
            "window_end": _fmt_dt(self.window_end),
            "events_last_24h": self.events_last_24h,
            "counts_by_source": dict(self.counts_by_source),
            "counts_by_event_type": dict(self.counts_by_event_type),
            "oldest_observation_at": None if self.oldest_observation_at is None else _fmt_dt(self.oldest_observation_at),
            "newest_observation_at": None if self.newest_observation_at is None else _fmt_dt(self.newest_observation_at),
            "source_freshness": {key: dict(value) for key, value in self.source_freshness.items()},
        }


@dataclass(frozen=True, slots=True)
class MarketSourceFetchResult:
    source: str
    observations: tuple[NormalizedMarketObservation, ...]
    source_freshness: dict[str, object]
    error: str | None = None


FetchFunction = Callable[[Sequence[str]], Iterable[NormalizedMarketObservation]]


class MarketIngestionRunner:
    def __init__(
        self,
        *,
        journal: AppendOnlyJournal | CanonicalJournal | Path | str,
        fetchers: dict[str, Callable[[Sequence[str]], Iterable[NormalizedMarketObservation]]] | None = None,
        symbols: Sequence[str] = DEFAULT_MARKET_SYMBOLS,
        observed_at: datetime | None = None,
        collected_at: datetime | None = None,
        stale_after_minutes: int = DEFAULT_MARKET_STALE_AFTER_MINUTES,
    ) -> None:
        self._journal = _coerce_journal(journal)
        self._fetchers = fetchers or _default_fetchers()
        self._symbols = tuple(_canonical_symbol(symbol) for symbol in symbols)
        self._observed_at = observed_at.astimezone(timezone.utc) if observed_at is not None else None
        self._collected_at = collected_at.astimezone(timezone.utc) if collected_at is not None else None
        self._stale_after_minutes = stale_after_minutes

    @property
    def journal(self) -> CanonicalJournal:
        return self._journal

    def run_once(self) -> MarketCollectionReport:
        generated_at = self._collected_at or datetime.now(timezone.utc)
        collected_at = self._collected_at or generated_at
        source_results: list[MarketSourceFetchResult] = []
        observations: list[NormalizedMarketObservation] = []
        source_errors: list[str] = []

        for source in EXPECTED_MARKET_SOURCES:
            fetcher = self._fetchers.get(source)
            if fetcher is None:
                source_errors.append(f"{source}: missing fetcher")
                source_results.append(
                    MarketSourceFetchResult(
                        source=source,
                        observations=(),
                        source_freshness={
                            "source": source,
                            "freshness_basis": "observed_at",
                            "last_observed_at": None,
                            "last_collected_at": None,
                            "age_minutes": None,
                            "status": "missing",
                        },
                        error="missing fetcher",
                    )
                )
                continue
            try:
                fetched = tuple(fetcher(self._symbols))
                fetched = dedupe_observations(fetched)
                for observation in fetched:
                    if observation.source != source:
                        raise MalformedObservationPayloadError(f"fetcher for {source} returned {observation.source}")
                observations.extend(fetched)
                source_results.append(
                    MarketSourceFetchResult(
                        source=source,
                        observations=fetched,
                        source_freshness=_freshness_for_source(source, fetched, generated_at=generated_at, stale_after_minutes=self._stale_after_minutes),
                    )
                )
            except Exception as exc:
                source_errors.append(f"{source}: {exc}")
                source_results.append(
                    MarketSourceFetchResult(
                        source=source,
                        observations=(),
                        source_freshness={
                            "source": source,
                            "freshness_basis": "observed_at",
                            "last_observed_at": None,
                            "last_collected_at": None,
                            "age_minutes": None,
                            "status": "missing",
                        },
                        error=str(exc),
                    )
                )

        observations = dedupe_observations(observations)
        appended = 0
        duplicate_count = 0
        counts_by_source: dict[str, int] = {}
        counts_by_symbol: dict[str, int] = {}
        counts_by_event_type: dict[str, int] = {}

        for observation in observations:
            record = observation_to_canonical_record(observation)
            try:
                self._journal.append_record(record)
                appended += 1
                counts_by_source[observation.source] = counts_by_source.get(observation.source, 0) + 1
                counts_by_symbol[observation.symbol] = counts_by_symbol.get(observation.symbol, 0) + 1
                counts_by_event_type[record.event_type.value] = counts_by_event_type.get(record.event_type.value, 0) + 1
            except JournalDuplicateEventError:
                duplicate_count += 1

        source_freshness = {result.source: result.source_freshness for result in source_results}
        return MarketCollectionReport(
            schema_version=MARKET_OBSERVATION_SCHEMA_VERSION,
            generated_at=generated_at,
            journal_path=str(self._journal.path),
            symbols=self._symbols,
            sources=EXPECTED_MARKET_SOURCES,
            observations_written=appended,
            duplicate_observations=duplicate_count,
            counts_by_source=counts_by_source,
            counts_by_symbol=counts_by_symbol,
            counts_by_event_type=counts_by_event_type,
            source_freshness=source_freshness,
            source_errors=tuple(source_errors),
        )


def normalize_binance_spot_payload(
    payload: dict[str, object],
    *,
    observed_at: datetime,
    collected_at: datetime,
) -> NormalizedMarketObservation:
    symbol = _require_string(payload, "symbol")
    price = _require_float(payload, "lastPrice")
    volume = _require_float(payload, "volume")
    quote_volume = _require_float(payload, "quoteVolume")
    bid = _maybe_float(payload.get("bidPrice"))
    ask = _maybe_float(payload.get("askPrice"))
    venue_metadata = {
        "endpoint": "/api/v3/ticker/24hr",
        "venue_symbol": symbol,
        "exchange_time_ms": _maybe_int(payload.get("closeTime")),
        "bidPrice": bid,
        "askPrice": ask,
    }
    spread_bps = _spread_bps(price, bid=bid, ask=ask)
    return _build_observation(
        source="binance.spot",
        symbol=_canonical_symbol(symbol),
        observed_at=observed_at,
        collected_at=collected_at,
        price=price,
        volume=volume,
        quote_volume=quote_volume,
        spread_bps=spread_bps,
        venue_metadata=venue_metadata,
    )


def normalize_binance_futures_payload(
    payload: dict[str, object],
    *,
    observed_at: datetime,
    collected_at: datetime,
) -> NormalizedMarketObservation:
    symbol = _require_string(payload, "symbol")
    price = _require_float(payload, "lastPrice")
    volume = _require_float(payload, "volume")
    quote_volume = _require_float(payload, "quoteVolume")
    bid = _maybe_float(payload.get("bidPrice"))
    ask = _maybe_float(payload.get("askPrice"))
    funding = _maybe_float(payload.get("lastFundingRate"))
    open_interest = _maybe_float(payload.get("openInterest"))
    venue_metadata = {
        "endpoint": "/fapi/v1/ticker/24hr",
        "premium_index_endpoint": "/fapi/v1/premiumIndex",
        "open_interest_endpoint": "/fapi/v1/openInterest",
        "venue_symbol": symbol,
        "exchange_time_ms": _maybe_int(payload.get("closeTime")),
        "bidPrice": bid,
        "askPrice": ask,
    }
    spread_bps = _spread_bps(price, bid=bid, ask=ask)
    return _build_observation(
        source="binance.futures",
        symbol=_canonical_symbol(symbol),
        observed_at=observed_at,
        collected_at=collected_at,
        price=price,
        volume=volume,
        quote_volume=quote_volume,
        spread_bps=spread_bps,
        funding=funding,
        open_interest=open_interest,
        venue_metadata=venue_metadata,
    )


def normalize_coinbase_spot_payload(
    payload: dict[str, object],
    *,
    observed_at: datetime,
    collected_at: datetime,
) -> NormalizedMarketObservation:
    product_id = _require_string(payload, "product_id")
    price = _require_float(payload, "price")
    volume = _maybe_float(payload.get("volume"))
    if volume is None:
        volume = _maybe_float(payload.get("size"))
    if volume is None:
        raise MissingObservationFieldError("volume")
    bid = _maybe_float(payload.get("bid"))
    ask = _maybe_float(payload.get("ask"))
    quote_volume = price * volume
    venue_metadata = {
        "endpoint": "/products/{product_id}/ticker",
        "venue_symbol": product_id,
        "trade_id": payload.get("trade_id"),
        "exchange_time": payload.get("time"),
        "bid": bid,
        "ask": ask,
    }
    spread_bps = _spread_bps(price, bid=bid, ask=ask)
    return _build_observation(
        source="coinbase.spot",
        symbol=_canonical_symbol(product_id),
        observed_at=observed_at,
        collected_at=collected_at,
        price=price,
        volume=volume,
        quote_volume=quote_volume,
        spread_bps=spread_bps,
        venue_metadata=venue_metadata,
    )


def dedupe_observations(observations: Iterable[NormalizedMarketObservation]) -> tuple[NormalizedMarketObservation, ...]:
    unique: list[NormalizedMarketObservation] = []
    seen: set[tuple[object, ...]] = set()
    for observation in observations:
        key = (
            observation.source,
            observation.symbol,
            observation.observed_at,
            observation.collected_at,
            observation.price,
            observation.volume,
            observation.quote_volume,
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(observation)
    unique.sort(key=lambda item: (item.observed_at, item.source, item.symbol, item.price))
    return tuple(unique)


def observation_to_canonical_record(observation: NormalizedMarketObservation) -> CanonicalJournalRecord:
    payload = observation.to_dict()
    return CanonicalJournalRecord(
        event_type=EventType.MARKET_SNAPSHOT,
        observed_at=observation.observed_at,
        collected_at=observation.collected_at,
        correlation_id=f"market-ingestion:{observation.source}:{observation.symbol}:{_fmt_dt(observation.observed_at)}",
        source_module=CANONICAL_JOURNAL_SOURCE_MODULE,
        symbol=observation.symbol,
        payload=payload,
    )


def build_market_ingestion_status(
    journal: AppendOnlyJournal | CanonicalJournal | Path | str,
    *,
    generated_at: datetime | None = None,
    window_hours: int = DEFAULT_MARKET_LOOKBACK_HOURS,
) -> MarketIngestionStatusReport:
    canonical_journal = _coerce_journal(journal)
    generated_at = generated_at.astimezone(timezone.utc) if generated_at is not None else datetime.now(timezone.utc)
    window_end = generated_at
    window_start = window_end - timedelta(hours=window_hours)
    events = list(canonical_journal.query(start_time=window_start, end_time=window_end))
    observations = [event for event in events if event.event_type == EventType.MARKET_SNAPSHOT]

    counts_by_source: dict[str, int] = {}
    counts_by_event_type: dict[str, int] = {}
    oldest_observation_at: datetime | None = None
    newest_observation_at: datetime | None = None
    latest_by_source: dict[str, JournalEvent] = {}

    for event in observations:
        payload = dict(event.payload or {})
        source = _source_name(event, payload)
        counts_by_source[source] = counts_by_source.get(source, 0) + 1
        counts_by_event_type[event.event_type.value] = counts_by_event_type.get(event.event_type.value, 0) + 1
        observed_at = _parse_datetime(payload.get("observed_at") or event.occurred_at)
        if oldest_observation_at is None or observed_at < oldest_observation_at:
            oldest_observation_at = observed_at
        if newest_observation_at is None or observed_at > newest_observation_at:
            newest_observation_at = observed_at
        current_latest = latest_by_source.get(source)
        if current_latest is None or (event.occurred_at, event.event_id) > (current_latest.occurred_at, current_latest.event_id):
            latest_by_source[source] = event

    source_freshness = {
        source: _source_freshness(source, latest_by_source.get(source), generated_at=generated_at)
        for source in EXPECTED_MARKET_SOURCES
    }
    return MarketIngestionStatusReport(
        schema_version=MARKET_OBSERVATION_SCHEMA_VERSION,
        generated_at=generated_at,
        journal_path=str(canonical_journal.path),
        window_start=window_start,
        window_end=window_end,
        events_last_24h=len(observations),
        counts_by_source=counts_by_source,
        counts_by_event_type=counts_by_event_type,
        oldest_observation_at=oldest_observation_at,
        newest_observation_at=newest_observation_at,
        source_freshness=source_freshness,
    )


def format_market_ingestion_status(report: MarketIngestionStatusReport) -> str:
    lines = [
        "Market Ingestion Status",
        f"journal_path={report.journal_path}",
        f"window={_fmt_dt(report.window_start)} -> {_fmt_dt(report.window_end)}",
        f"events_last_24h={report.events_last_24h}",
        f"counts_by_source={_format_counts(report.counts_by_source)}",
        f"counts_by_event_type={_format_counts(report.counts_by_event_type)}",
        f"oldest_observation={_fmt_dt(report.oldest_observation_at) if report.oldest_observation_at else 'none'}",
        f"newest_observation={_fmt_dt(report.newest_observation_at) if report.newest_observation_at else 'none'}",
    ]
    for source in EXPECTED_MARKET_SOURCES:
        freshness = report.source_freshness[source]
        lines.append(
            f"freshness[{source}]=status={freshness['status']}, freshness_basis={freshness['freshness_basis']}, age_minutes={freshness['age_minutes']}"
        )
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="trading_autopilot.market_ingestion")
    subparsers = parser.add_subparsers(dest="command", required=True)

    collect = subparsers.add_parser("market-collect", help="Collect BTC/ETH market data and append market.snapshot events")
    _add_common_arguments(collect)
    collect.set_defaults(handler=_handle_collect)

    status = subparsers.add_parser("market-status", help="Summarize the real market journal")
    _add_common_arguments(status)
    status.set_defaults(handler=_handle_status)

    args = parser.parse_args(list(argv) if argv is not None else None)
    return int(args.handler(args))


def _handle_collect(args: argparse.Namespace) -> int:
    runner = MarketIngestionRunner(
        journal=args.journal_path,
        symbols=args.symbols,
        observed_at=args.observed_at,
        collected_at=args.collected_at,
    )
    report = runner.run_once()
    if args.json:
        print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    else:
        print(_format_collection_report(report))
    return 0


def _handle_status(args: argparse.Namespace) -> int:
    report = build_market_ingestion_status(args.journal_path, generated_at=args.generated_at, window_hours=args.window_hours)
    if args.json:
        print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    else:
        print(format_market_ingestion_status(report))
    return 0


def _add_common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--journal-path", default=Path.home() / ".hermes" / "cron" / "journals" / "trading_autopilot_market.sqlite3", type=Path)
    parser.add_argument("--symbols", nargs="+", default=list(DEFAULT_MARKET_SYMBOLS))
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--observed-at", type=_parse_cli_datetime)
    parser.add_argument("--collected-at", type=_parse_cli_datetime)
    parser.add_argument("--generated-at", type=_parse_cli_datetime)
    parser.add_argument("--window-hours", type=int, default=DEFAULT_MARKET_LOOKBACK_HOURS)


def _format_collection_report(report: MarketCollectionReport) -> str:
    return "\n".join(
        [
            "Market Ingestion Collection",
            f"journal_path={report.journal_path}",
            f"generated_at={_fmt_dt(report.generated_at)}",
            f"observations_written={report.observations_written}",
            f"duplicate_observations={report.duplicate_observations}",
            f"counts_by_source={_format_counts(report.counts_by_source)}",
            f"counts_by_symbol={_format_counts(report.counts_by_symbol)}",
            f"counts_by_event_type={_format_counts(report.counts_by_event_type)}",
        ]
    )


def _format_counts(counts: dict[str, int]) -> str:
    return ", ".join(f"{key}={value}" for key, value in sorted(counts.items())) or "none"


def _coerce_journal(journal: AppendOnlyJournal | CanonicalJournal | Path | str) -> CanonicalJournal:
    if isinstance(journal, CanonicalJournal):
        return journal
    if isinstance(journal, AppendOnlyJournal):
        return CanonicalJournal(journal)
    return CanonicalJournal(journal)


def _default_fetchers() -> dict[str, Callable[[Sequence[str]], Iterable[NormalizedMarketObservation]]]:
    return {
        "binance.spot": lambda symbols: tuple(_fetch_binance_spot(symbols)),
        "binance.futures": lambda symbols: tuple(_fetch_binance_futures(symbols)),
        "coinbase.spot": lambda symbols: tuple(_fetch_coinbase_spot(symbols)),
    }


def _fetch_binance_spot(symbols: Sequence[str]) -> Iterable[NormalizedMarketObservation]:
    session = requests.Session()
    collected_at = datetime.now(timezone.utc)
    observed_at = _binance_server_time(session, f"{DEFAULT_BINANCE_SPOT_BASE_URL}/api/v3/time")
    for symbol in symbols:
        venue_symbol = f"{symbol}USDT"
        payload = _get_json(session, f"{DEFAULT_BINANCE_SPOT_BASE_URL}/api/v3/ticker/24hr", params={"symbol": venue_symbol})
        if not isinstance(payload, dict):
            raise MalformedObservationPayloadError("binance spot ticker payload must be an object")
        yield normalize_binance_spot_payload(payload, observed_at=observed_at, collected_at=collected_at)


def _fetch_binance_futures(symbols: Sequence[str]) -> Iterable[NormalizedMarketObservation]:
    session = requests.Session()
    collected_at = datetime.now(timezone.utc)
    observed_at = _binance_server_time(session, f"{DEFAULT_BINANCE_FUTURES_BASE_URL}/fapi/v1/time")
    for symbol in symbols:
        venue_symbol = f"{symbol}USDT"
        payload = _get_json(session, f"{DEFAULT_BINANCE_FUTURES_BASE_URL}/fapi/v1/ticker/24hr", params={"symbol": venue_symbol})
        if not isinstance(payload, dict):
            raise MalformedObservationPayloadError("binance futures ticker payload must be an object")
        premium_index = _get_json(session, f"{DEFAULT_BINANCE_FUTURES_BASE_URL}/fapi/v1/premiumIndex", params={"symbol": venue_symbol})
        open_interest = _get_json(session, f"{DEFAULT_BINANCE_FUTURES_BASE_URL}/fapi/v1/openInterest", params={"symbol": venue_symbol})
        if isinstance(premium_index, dict):
            payload = {
                **payload,
                "lastFundingRate": premium_index.get("lastFundingRate"),
            }
        if isinstance(open_interest, dict):
            payload = {
                **payload,
                "openInterest": open_interest.get("openInterest"),
            }
        yield normalize_binance_futures_payload(payload, observed_at=observed_at, collected_at=collected_at)


def _fetch_coinbase_spot(symbols: Sequence[str]) -> Iterable[NormalizedMarketObservation]:
    session = requests.Session()
    collected_at = datetime.now(timezone.utc)
    for symbol in symbols:
        product_id = f"{symbol}-USD"
        payload = _get_json(session, f"{DEFAULT_COINBASE_BASE_URL}/products/{product_id}/ticker")
        if not isinstance(payload, dict):
            raise MalformedObservationPayloadError("coinbase ticker payload must be an object")
        payload = {**payload, "product_id": product_id}
        observed_at_raw = payload.get("time")
        observed_at = _parse_datetime(observed_at_raw) if observed_at_raw else collected_at
        yield normalize_coinbase_spot_payload(payload, observed_at=observed_at, collected_at=collected_at)


def _get_json(session: requests.Session, url: str, params: dict[str, object] | None = None) -> object:
    response = session.get(url, params=params, timeout=10)
    response.raise_for_status()
    return response.json()


def _binance_server_time(session: requests.Session, url: str) -> datetime:
    payload = _get_json(session, url)
    if not isinstance(payload, dict):
        raise MalformedObservationPayloadError("binance server time payload must be an object")
    server_time = _maybe_int(payload.get("serverTime"))
    if server_time is None:
        raise MissingObservationFieldError("serverTime")
    return datetime.fromtimestamp(server_time / 1000.0, tz=timezone.utc)


def _build_observation(
    *,
    source: str,
    symbol: str,
    observed_at: datetime,
    collected_at: datetime,
    price: float,
    volume: float,
    quote_volume: float,
    spread_bps: float | None = None,
    funding: float | None = None,
    open_interest: float | None = None,
    liquidations: dict[str, float] | None = None,
    venue_metadata: dict[str, object] | None = None,
) -> NormalizedMarketObservation:
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise ValueError("observed_at must be timezone-aware")
    if collected_at.tzinfo is None or collected_at.utcoffset() is None:
        raise ValueError("collected_at must be timezone-aware")
    if observed_at > collected_at + timedelta(minutes=5):
        raise StaleObservationError(f"{source} observation is future-dated relative to collection")
    return NormalizedMarketObservation(
        schema_version=MARKET_OBSERVATION_SCHEMA_VERSION,
        source=source,
        symbol=_canonical_symbol(symbol),
        observed_at=observed_at.astimezone(timezone.utc),
        collected_at=collected_at.astimezone(timezone.utc),
        price=float(price),
        volume=float(volume),
        quote_volume=float(quote_volume),
        spread_bps=spread_bps,
        funding=funding,
        open_interest=open_interest,
        liquidations=liquidations,
        venue_metadata=dict(venue_metadata or {}),
    )


def _source_name(event: JournalEvent, payload: dict[str, object]) -> str:
    source = payload.get("source") or event.source_module
    return str(source).strip().lower()


def _source_freshness(
    source: str,
    event: JournalEvent | None,
    *,
    generated_at: datetime,
    stale_after_minutes: int = DEFAULT_MARKET_STALE_AFTER_MINUTES,
) -> dict[str, object]:
    if event is None:
        return {
            "source": source,
            "freshness_basis": "observed_at",
            "last_observed_at": None,
            "last_collected_at": None,
            "age_minutes": None,
            "status": "missing",
        }
    payload = dict(event.payload or {})
    observed_at = _parse_datetime(payload.get("observed_at") or event.occurred_at)
    collected_at = _parse_datetime(payload.get("collected_at") or observed_at)
    age_minutes = round((generated_at - observed_at).total_seconds() / 60.0, 2)
    return {
        "source": source,
        "freshness_basis": "observed_at",
        "last_observed_at": _fmt_dt(observed_at),
        "last_collected_at": None if collected_at is None else _fmt_dt(collected_at),
        "age_minutes": age_minutes,
        "status": "stale" if age_minutes > stale_after_minutes else "fresh",
    }


def _freshness_for_source(
    source: str,
    observations: Sequence[NormalizedMarketObservation],
    *,
    generated_at: datetime,
    stale_after_minutes: int = DEFAULT_MARKET_STALE_AFTER_MINUTES,
) -> dict[str, object]:
    if not observations:
        return {
            "source": source,
            "freshness_basis": "observed_at",
            "last_observed_at": None,
            "last_collected_at": None,
            "age_minutes": None,
            "status": "missing",
        }
    latest = max(observations, key=lambda item: (item.observed_at, item.collected_at, item.symbol, item.price))
    age_minutes = round((generated_at - latest.observed_at).total_seconds() / 60.0, 2)
    return {
        "source": source,
        "freshness_basis": "observed_at",
        "last_observed_at": _fmt_dt(latest.observed_at),
        "last_collected_at": _fmt_dt(latest.collected_at),
        "age_minutes": age_minutes,
        "status": "stale" if age_minutes > stale_after_minutes else "fresh",
    }


def _canonical_symbol(symbol: str) -> str:
    value = str(symbol).strip().upper()
    if not value:
        raise MissingObservationFieldError("symbol")
    if "-" in value:
        value = value.replace("-", "")
    if value.endswith("USDT"):
        return value[:-4]
    if value.endswith("USD"):
        return value[:-3]
    return value


def _spread_bps(price: float, *, bid: float | None, ask: float | None) -> float | None:
    if bid is None or ask is None or price <= 0:
        return None
    return ((ask - bid) / price) * 10000.0


def _parse_cli_datetime(value: str) -> datetime:
    return _parse_datetime(value)


def _parse_datetime(value: object) -> datetime:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value)
    return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)


def _require_string(payload: dict[str, object], key: str) -> str:
    value = payload.get(key)
    if value is None:
        raise MissingObservationFieldError(key)
    text = str(value).strip()
    if not text:
        raise MissingObservationFieldError(key)
    return text


def _require_float(payload: dict[str, object], key: str) -> float:
    value = _maybe_float(payload.get(key))
    if value is None:
        raise MissingObservationFieldError(key)
    return value


def _maybe_float(value: object | None) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _maybe_int(value: object | None) -> int | None:
    if value is None:
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _fmt_dt(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _stable_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _event_id_for_observation(observation: NormalizedMarketObservation) -> str:
    payload = observation.to_dict()
    digest = hashlib.sha256(_stable_json(payload).encode("utf-8")).hexdigest()[:24]
    return f"mo-{digest}"


# Export helper for deterministic IDs in reports/validation if needed.
def observation_event_id(observation: NormalizedMarketObservation) -> str:
    return _event_id_for_observation(observation)


if __name__ == "__main__":
    raise SystemExit(main())
