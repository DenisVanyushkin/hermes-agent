from __future__ import annotations

from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

from trading_autopilot import AppendOnlyJournal, CanonicalJournal, EventType
from trading_autopilot.market_ingestion import (
    MarketCollectionReport,
    MarketIngestionRunner,
    MarketIngestionStatusReport,
    NormalizedMarketObservation,
    build_market_ingestion_status,
    format_market_ingestion_status,
    normalize_binance_futures_payload,
    normalize_binance_spot_payload,
    normalize_coinbase_spot_payload,
)


COLLECTED_AT = datetime(2026, 5, 31, 12, 5, tzinfo=timezone.utc)
OBSERVED_AT = datetime(2026, 5, 31, 12, 0, tzinfo=timezone.utc)


BINANCE_SPOT_PAYLOAD = {
    "symbol": "BTCUSDT",
    "lastPrice": "101500.12",
    "volume": "12.5",
    "quoteVolume": "1268751.5",
    "bidPrice": "101499.9",
    "askPrice": "101500.3",
    "closeTime": 1717156800000,
}

BINANCE_FUTURES_PAYLOAD = {
    "symbol": "ETHUSDT",
    "lastPrice": "2450.25",
    "volume": "80.1",
    "quoteVolume": "196264.5",
    "bidPrice": "2450.1",
    "askPrice": "2450.4",
    "lastFundingRate": "0.0001",
    "openInterest": "12345.6",
    "closeTime": 1717156800000,
}

COINBASE_SPOT_PAYLOAD = {
    "trade_id": 123456,
    "price": "2401.50",
    "size": "3.2",
    "bid": "2401.0",
    "ask": "2402.0",
    "time": "2026-05-31T12:00:00Z",
    "product_id": "ETH-USD",
    "volume": "3.2",
}


def test_normalizers_produce_required_fields_and_optional_metadata() -> None:
    spot = normalize_binance_spot_payload(BINANCE_SPOT_PAYLOAD, observed_at=OBSERVED_AT, collected_at=COLLECTED_AT)
    futures = normalize_binance_futures_payload(BINANCE_FUTURES_PAYLOAD, observed_at=OBSERVED_AT, collected_at=COLLECTED_AT)
    coinbase = normalize_coinbase_spot_payload(COINBASE_SPOT_PAYLOAD, observed_at=OBSERVED_AT, collected_at=COLLECTED_AT)

    assert spot.source == "binance.spot"
    assert spot.symbol == "BTC"
    assert spot.price == pytest.approx(101500.12)
    assert spot.volume == pytest.approx(12.5)
    assert spot.quote_volume == pytest.approx(1268751.5)
    assert spot.spread_bps == pytest.approx((101500.3 - 101499.9) / 101500.12 * 10000)
    assert spot.venue_metadata["endpoint"] == "/api/v3/ticker/24hr"

    assert futures.source == "binance.futures"
    assert futures.symbol == "ETH"
    assert futures.funding == pytest.approx(0.0001)
    assert futures.open_interest == pytest.approx(12345.6)
    assert futures.venue_metadata["endpoint"] == "/fapi/v1/ticker/24hr"

    assert coinbase.source == "coinbase.spot"
    assert coinbase.symbol == "ETH"
    assert coinbase.price == pytest.approx(2401.50)
    assert coinbase.quote_volume == pytest.approx(2401.50 * 3.2)
    assert coinbase.venue_metadata["endpoint"] == "/products/{product_id}/ticker"

    for observation in (spot, futures, coinbase):
        payload = observation.to_dict()
        assert payload["source"] in {"binance.spot", "binance.futures", "coinbase.spot"}
        assert payload["observed_at"].endswith("Z")
        assert payload["collected_at"].endswith("Z")
        assert payload["price"] is not None
        assert payload["volume"] is not None
        assert payload["quote_volume"] is not None


def test_market_collector_persists_real_market_snapshot_events(tmp_path: Path) -> None:
    journal = CanonicalJournal(tmp_path / "market.sqlite3")

    runner = MarketIngestionRunner(
        journal=journal,
        fetchers={
            "binance.spot": lambda symbols, *, observed_at=None, collected_at=None: (
                normalize_binance_spot_payload(BINANCE_SPOT_PAYLOAD, observed_at=OBSERVED_AT, collected_at=COLLECTED_AT),
                normalize_binance_spot_payload({**BINANCE_SPOT_PAYLOAD, "symbol": "ETHUSDT", "lastPrice": "2501.00"}, observed_at=OBSERVED_AT, collected_at=COLLECTED_AT),
            ),
            "binance.futures": lambda symbols, *, observed_at=None, collected_at=None: (
                normalize_binance_futures_payload({**BINANCE_FUTURES_PAYLOAD, "symbol": "BTCUSDT", "lastPrice": "101800.50"}, observed_at=OBSERVED_AT, collected_at=COLLECTED_AT),
                normalize_binance_futures_payload(BINANCE_FUTURES_PAYLOAD, observed_at=OBSERVED_AT, collected_at=COLLECTED_AT),
            ),
            "coinbase.spot": lambda symbols, *, observed_at=None, collected_at=None: (
                normalize_coinbase_spot_payload({**COINBASE_SPOT_PAYLOAD, "product_id": "BTC-USD", "price": "101600.25"}, observed_at=OBSERVED_AT, collected_at=COLLECTED_AT),
                normalize_coinbase_spot_payload(COINBASE_SPOT_PAYLOAD, observed_at=OBSERVED_AT, collected_at=COLLECTED_AT),
            ),
        },
        symbols=("BTC", "ETH"),
        observed_at=OBSERVED_AT,
        collected_at=COLLECTED_AT,
    )

    report = runner.run_once()

    assert isinstance(report, MarketCollectionReport)
    assert report.observations_written == 6
    assert report.counts_by_source == {"binance.spot": 2, "binance.futures": 2, "coinbase.spot": 2}
    assert report.counts_by_symbol == {"BTC": 3, "ETH": 3}
    assert report.counts_by_event_type == {EventType.MARKET_SNAPSHOT.value: 6}

    events = journal.query(event_types={EventType.MARKET_SNAPSHOT})
    assert len(events) == 6
    assert {event.payload["source"] for event in events} == {"binance.spot", "binance.futures", "coinbase.spot"}
    assert {event.payload["symbol"] for event in events} == {"BTC", "ETH"}
    assert all(event.source_module == "trading_autopilot.canonical_journal" for event in events)


def test_market_status_report_answers_journal_volume_and_freshness(tmp_path: Path) -> None:
    journal = AppendOnlyJournal(tmp_path / "status.sqlite3")
    canonical = CanonicalJournal(journal)
    runner = MarketIngestionRunner(
        journal=canonical,
        fetchers={
            "binance.spot": lambda symbols, *, observed_at=None, collected_at=None: (
                normalize_binance_spot_payload(BINANCE_SPOT_PAYLOAD, observed_at=OBSERVED_AT - timedelta(minutes=3), collected_at=COLLECTED_AT),
            ),
            "binance.futures": lambda symbols, *, observed_at=None, collected_at=None: (
                normalize_binance_futures_payload(BINANCE_FUTURES_PAYLOAD, observed_at=OBSERVED_AT - timedelta(minutes=2), collected_at=COLLECTED_AT),
            ),
            "coinbase.spot": lambda symbols, *, observed_at=None, collected_at=None: (
                normalize_coinbase_spot_payload(COINBASE_SPOT_PAYLOAD, observed_at=OBSERVED_AT - timedelta(minutes=1), collected_at=COLLECTED_AT),
            ),
        },
        symbols=("BTC", "ETH"),
        observed_at=OBSERVED_AT,
        collected_at=COLLECTED_AT,
    )
    runner.run_once()

    status = build_market_ingestion_status(canonical, generated_at=COLLECTED_AT)
    formatted = format_market_ingestion_status(status)

    assert isinstance(status, MarketIngestionStatusReport)
    assert status.events_last_24h == 3
    assert status.counts_by_source == {"binance.spot": 1, "binance.futures": 1, "coinbase.spot": 1}
    assert status.counts_by_event_type == {EventType.MARKET_SNAPSHOT.value: 3}
    assert status.oldest_observation_at == OBSERVED_AT - timedelta(minutes=3)
    assert status.newest_observation_at == OBSERVED_AT - timedelta(minutes=1)
    assert status.source_freshness["binance.spot"]["age_minutes"] == pytest.approx(8.0)
    assert status.source_freshness["coinbase.spot"]["freshness_basis"] == "observed_at"
    assert "events_last_24h=3" in formatted
    assert "binance.spot" in formatted
    assert "freshness_basis=observed_at" in formatted


def test_systemd_units_point_to_five_minute_collection_cycle() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    service = (repo_root / "deploy/systemd/trading-autopilot-market.service").read_text(encoding="utf-8")
    timer = (repo_root / "deploy/systemd/trading-autopilot-market.timer").read_text(encoding="utf-8")

    assert "python -m trading_autopilot.market_ingestion market-collect" in service
    assert "WorkingDirectory=__TRADING_AUTOPILOT_REPO_ROOT__" in service
    assert "OnCalendar=*:0/5" in timer
    assert "Persistent=true" in timer

