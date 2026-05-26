from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Iterable

NORMALIZATION_SCHEMA_VERSION = "1.0.0"


class NormalizationError(ValueError):
    """Raised when market input cannot be canonicalized deterministically."""


class MarketAnomaly(StrEnum):
    STALE_DATA = "stale_data"
    GAP_DETECTED = "gap_detected"
    OUT_OF_ORDER = "out_of_order"
    DUPLICATE_TICK = "duplicate_tick"
    MALFORMED_INPUT = "malformed_input"


class MarketRegime(StrEnum):
    STALE = "stale"
    RANGING = "ranging"
    UPTREND = "uptrend"
    DOWNTREND = "downtrend"
    VOLATILE = "volatile"


@dataclass(frozen=True, slots=True)
class MarketTick:
    symbol: str
    observed_at: datetime
    price: float
    volume: float
    source: str


@dataclass(frozen=True, slots=True)
class MarketBar:
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True, slots=True)
class MarketSnapshot:
    symbol: str
    observed_at: datetime
    ticks: tuple[MarketTick, ...]


@dataclass(frozen=True, slots=True)
class NormalizedMarketSnapshot:
    schema_version: str
    normalized_symbol: str
    observed_at: datetime
    bars: tuple[MarketBar, ...]
    regime: MarketRegime
    regime_reason: str
    anomalies: tuple[MarketAnomaly, ...]
    source_tick_count: int
    normalized_tick_count: int
    source_ticks: tuple[MarketTick, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "normalized_symbol": self.normalized_symbol,
            "observed_at": _to_utc_z(self.observed_at),
            "bars": [
                {
                    "open": bar.open,
                    "high": bar.high,
                    "low": bar.low,
                    "close": bar.close,
                    "volume": bar.volume,
                }
                for bar in self.bars
            ],
            "regime": self.regime.value,
            "regime_reason": self.regime_reason,
            "anomalies": [anomaly.value for anomaly in self.anomalies],
            "source_tick_count": self.source_tick_count,
            "normalized_tick_count": self.normalized_tick_count,
        }


@dataclass(frozen=True, slots=True)
class _CanonicalTick:
    observed_at: datetime
    price: float
    volume: float
    source: str


def normalize_market_snapshot(
    snapshot: MarketSnapshot,
    *,
    stale_after_seconds: int = 120,
    gap_ratio_threshold: float = 3.0,
    trend_threshold: float = 0.005,
    volatility_threshold: float = 0.02,
) -> NormalizedMarketSnapshot:
    _validate_snapshot(snapshot)
    canonical_symbol = snapshot.symbol.strip().upper()

    canonical_ticks = [_canonicalize_tick(tick, canonical_symbol) for tick in snapshot.ticks]
    canonical_ticks.sort(key=lambda tick: (tick.observed_at, tick.source, tick.price, tick.volume))
    deduped_ticks: list[_CanonicalTick] = []
    anomalies: list[MarketAnomaly] = []
    seen: set[tuple[datetime, float, float, str]] = set()
    for tick in canonical_ticks:
        key = (tick.observed_at, tick.price, tick.volume, tick.source)
        if key in seen:
            anomalies.append(MarketAnomaly.DUPLICATE_TICK)
            continue
        seen.add(key)
        deduped_ticks.append(tick)

    if canonical_ticks != sorted(canonical_ticks, key=lambda tick: (tick.observed_at, tick.source, tick.price, tick.volume)):
        anomalies.append(MarketAnomaly.OUT_OF_ORDER)

    if not deduped_ticks:
        raise NormalizationError("snapshot contains no canonical ticks")

    stale_cutoff = snapshot.observed_at.astimezone(timezone.utc).timestamp() - stale_after_seconds
    fresh_ticks = [tick for tick in deduped_ticks if tick.observed_at.astimezone(timezone.utc).timestamp() >= stale_cutoff]
    stale = len(fresh_ticks) == 0
    if stale:
        anomalies.append(MarketAnomaly.STALE_DATA)

    intervals = _intervals(deduped_ticks)
    if intervals:
        smallest_interval = min(intervals)
        if smallest_interval <= 0:
            smallest_interval = 1.0
        if max(intervals) > smallest_interval * gap_ratio_threshold:
            anomalies.append(MarketAnomaly.GAP_DETECTED)

    bar_ticks = fresh_ticks if fresh_ticks else deduped_ticks
    prices = [tick.price for tick in bar_ticks]
    volumes = [tick.volume for tick in bar_ticks]
    bar = MarketBar(
        open=prices[0],
        high=max(prices),
        low=min(prices),
        close=prices[-1],
        volume=round(sum(volumes), 8),
    )

    regime, regime_reason = _classify_regime(
        deduped_ticks,
        anomalies=tuple(anomalies),
        stale=stale,
        trend_threshold=trend_threshold,
        volatility_threshold=volatility_threshold,
    )

    return NormalizedMarketSnapshot(
        schema_version=NORMALIZATION_SCHEMA_VERSION,
        normalized_symbol=canonical_symbol,
        observed_at=snapshot.observed_at.astimezone(timezone.utc),
        bars=(bar,),
        regime=regime,
        regime_reason=regime_reason,
        anomalies=_unique_anomalies(anomalies),
        source_tick_count=len(snapshot.ticks),
        normalized_tick_count=len(deduped_ticks),
        source_ticks=tuple(deduped_ticks),
    )


def _classify_regime(
    ticks: Sequence[_CanonicalTick],
    *,
    anomalies: tuple[MarketAnomaly, ...],
    stale: bool,
    trend_threshold: float,
    volatility_threshold: float,
) -> tuple[MarketRegime, str]:
    if stale:
        return MarketRegime.STALE, "all ticks stale"

    first = ticks[0].price
    last = ticks[-1].price
    high = max(tick.price for tick in ticks)
    low = min(tick.price for tick in ticks)
    mid = (high + low) / 2 if (high + low) else 1.0
    net_change = (last - first) / first if first else 0.0
    range_pct = (high - low) / mid if mid else 0.0

    if net_change >= trend_threshold:
        return MarketRegime.UPTREND, f"net_change={net_change:.6f}"
    if net_change <= -trend_threshold:
        return MarketRegime.DOWNTREND, f"net_change={net_change:.6f}"
    if range_pct >= volatility_threshold:
        return MarketRegime.VOLATILE, f"range_pct={range_pct:.6f}"
    return MarketRegime.RANGING, f"net_change={net_change:.6f}; range_pct={range_pct:.6f}; anomalies={len(anomalies)}"


def _validate_snapshot(snapshot: MarketSnapshot) -> None:
    if not snapshot.symbol or not snapshot.symbol.strip():
        raise NormalizationError("symbol must be set")
    if snapshot.observed_at.tzinfo is None or snapshot.observed_at.utcoffset() is None:
        raise NormalizationError("observed_at must be timezone-aware")
    if not snapshot.ticks:
        raise NormalizationError("ticks must not be empty")


def _canonicalize_tick(tick: MarketTick, expected_symbol: str) -> _CanonicalTick:
    if tick.symbol.strip().upper() != expected_symbol:
        raise NormalizationError("tick symbol does not match snapshot symbol")
    if tick.observed_at.tzinfo is None or tick.observed_at.utcoffset() is None:
        raise NormalizationError("tick observed_at must be timezone-aware")
    if not _is_finite_positive(tick.price):
        raise NormalizationError("price must be finite and positive")
    if not _is_finite_non_negative(tick.volume):
        raise NormalizationError("volume must be finite and non-negative")
    if not tick.source or not tick.source.strip():
        raise NormalizationError("source must be set")
    return _CanonicalTick(
        observed_at=tick.observed_at.astimezone(timezone.utc),
        price=round(float(tick.price), 6),
        volume=round(float(tick.volume), 8),
        source=tick.source.strip().lower(),
    )


def _intervals(ticks: Sequence[_CanonicalTick]) -> list[float]:
    if len(ticks) < 2:
        return []
    intervals = []
    for previous, current in zip(ticks, ticks[1:]):
        delta = (current.observed_at - previous.observed_at).total_seconds()
        intervals.append(max(delta, 0.0))
    return intervals


def _unique_anomalies(anomalies: Iterable[MarketAnomaly]) -> tuple[MarketAnomaly, ...]:
    unique: list[MarketAnomaly] = []
    seen: set[MarketAnomaly] = set()
    for anomaly in anomalies:
        if anomaly not in seen:
            seen.add(anomaly)
            unique.append(anomaly)
    return tuple(unique)


def _is_finite_positive(value: float) -> bool:
    return isinstance(value, (int, float)) and value == value and value not in {float("inf"), float("-inf")} and float(value) > 0


def _is_finite_non_negative(value: float) -> bool:
    return isinstance(value, (int, float)) and value == value and value not in {float("inf"), float("-inf")} and float(value) >= 0


def _to_utc_z(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
