from __future__ import annotations

from datetime import datetime, timezone

import pytest

from trading_autopilot.normalization import (
    MarketAnomaly,
    MarketBar,
    MarketRegime,
    MarketSnapshot,
    MarketTick,
    NormalizationError,
    normalize_market_snapshot,
)


def _tick(ts: str, price: float, volume: float = 1.0, symbol: str = "BTCUSDT") -> MarketTick:
    return MarketTick(
        symbol=symbol,
        observed_at=datetime.fromisoformat(ts.replace("Z", "+00:00")),
        price=price,
        volume=volume,
        source="binance",
    )


def test_normalization_is_deterministic_for_equivalent_inputs() -> None:
    snapshot = MarketSnapshot(
        symbol="BTCUSDT",
        observed_at=datetime(2026, 5, 26, 10, 0, tzinfo=timezone.utc),
        ticks=(
            _tick("2026-05-26T10:00:05Z", 100.0000004, 2.0),
            _tick("2026-05-26T10:00:01Z", 99.9999999, 1.0),
            _tick("2026-05-26T10:00:03Z", 100.0, 0.5),
        ),
    )

    first = normalize_market_snapshot(snapshot)
    second = normalize_market_snapshot(snapshot)

    assert first == second
    assert first.normalized_symbol == "BTCUSDT"
    assert first.regime == MarketRegime.RANGING
    assert first.anomalies == ()
    assert first.bars[0] == MarketBar(open=100.0, high=100.0, low=100.0, close=100.0, volume=3.5)


def test_stale_and_gap_handling_marks_anomalies_without_guessing_direction() -> None:
    snapshot = MarketSnapshot(
        symbol="BTCUSDT",
        observed_at=datetime(2026, 5, 26, 10, 0, tzinfo=timezone.utc),
        ticks=(
            _tick("2026-05-26T09:57:00Z", 100.0, 1.0),
            _tick("2026-05-26T09:57:01Z", 100.2, 1.0),
            _tick("2026-05-26T09:57:05Z", 100.1, 1.0),
        ),
    )

    normalized = normalize_market_snapshot(snapshot, stale_after_seconds=60, gap_ratio_threshold=2.5)

    assert normalized.regime == MarketRegime.STALE
    assert MarketAnomaly.STALE_DATA in normalized.anomalies
    assert MarketAnomaly.GAP_DETECTED in normalized.anomalies
    assert normalized.regime_reason == "all ticks stale"


def test_malformed_input_is_rejected() -> None:
    with pytest.raises(NormalizationError, match="price"):
        normalize_market_snapshot(
            MarketSnapshot(
                symbol="BTCUSDT",
                observed_at=datetime(2026, 5, 26, 10, 0, tzinfo=timezone.utc),
                ticks=(
                    MarketTick(
                        symbol="BTCUSDT",
                        observed_at=datetime(2026, 5, 26, 10, 0, tzinfo=timezone.utc),
                        price=float("nan"),
                        volume=1.0,
                        source="binance",
                    ),
                ),
            )
        )


def test_regime_labeling_is_simple_and_strategy_neutral() -> None:
    uptrend = MarketSnapshot(
        symbol="ETHUSDT",
        observed_at=datetime(2026, 5, 26, 10, 0, tzinfo=timezone.utc),
        ticks=(
            _tick("2026-05-26T09:59:55Z", 100.0, symbol="ETHUSDT"),
            _tick("2026-05-26T09:59:56Z", 102.0, symbol="ETHUSDT"),
            _tick("2026-05-26T09:59:57Z", 104.0, symbol="ETHUSDT"),
            _tick("2026-05-26T09:59:58Z", 106.0, symbol="ETHUSDT"),
        ),
    )
    downtrend = MarketSnapshot(
        symbol="ETHUSDT",
        observed_at=datetime(2026, 5, 26, 10, 0, tzinfo=timezone.utc),
        ticks=(
            _tick("2026-05-26T09:59:55Z", 106.0, symbol="ETHUSDT"),
            _tick("2026-05-26T09:59:56Z", 104.0, symbol="ETHUSDT"),
            _tick("2026-05-26T09:59:57Z", 102.0, symbol="ETHUSDT"),
            _tick("2026-05-26T09:59:58Z", 100.0, symbol="ETHUSDT"),
        ),
    )

    assert normalize_market_snapshot(uptrend).regime == MarketRegime.UPTREND
    assert normalize_market_snapshot(downtrend).regime == MarketRegime.DOWNTREND
