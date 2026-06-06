# Operational Validation Report — Market Intelligence MVP

## Verdict
**CONDITIONAL GO**

The MVP demonstrated real live market ingestion and journal-backed daily brief generation. The remaining caveat is host-level systemd restart persistence: this container does not provide `systemctl`, so automatic boot-time activation could not be verified here.

## Deployment
- **Host:** `96f7eea5e28a`
- **Platform:** `Linux-6.8.0-124-generic-x86_64-with-glibc2.41`
- **Deployment location:** `/var/lib/job-intel/state/job_intel.sqlite3`
- **Journal path:** `/var/lib/job-intel/state/job_intel.sqlite3`
- **Journal log path:** `/var/log/job-intel/trading-autopilot-market.log`
- **Service name:** `trading-autopilot-market.service`
- **Timer name:** `trading-autopilot-market.timer`
- **Service unit file:** `deploy/systemd/trading-autopilot-market.service`
- **Timer unit file:** `deploy/systemd/trading-autopilot-market.timer`
- **Scheduler status:** 3 live collection cycles were executed against the deployed journal, producing real market data. Host-level `systemctl` was unavailable in this container, so unit activation/reboot persistence could not be directly confirmed here.

## Journal
- **Path:** `/var/lib/job-intel/state/job_intel.sqlite3`
- **Size:** `53248` bytes
- **Log file size:** `4505` bytes
- **Total journal events:** 19
- **Event counts:** {'market.snapshot': 18, 'market.state_brief': 1}
- **Market snapshot events:** 18
- **Brief events:** 1

## Sources

### Binance Spot
- **First observation:** 2026-06-01T06:19:36.957000Z
- **Last observation:** 2026-06-01T06:19:46.770000Z
- **Count:** 6
- **Freshness:** fresh (`observed_at` basis, latest snapshot in window)
- **Latest observation:** ETH @ 1992.04

### Binance Futures
- **First observation:** 2026-06-01T06:19:38.276000Z
- **Last observation:** 2026-06-01T06:19:47.687000Z
- **Count:** 6
- **Freshness:** fresh (`observed_at` basis, latest snapshot in window)
- **Latest observation:** ETH @ 1991.03

### Coinbase Spot
- **First observation:** 2026-06-01T06:19:35.514286Z
- **Last observation:** 2026-06-01T06:19:47.535742Z
- **Count:** 6
- **Freshness:** fresh (`observed_at` basis, latest snapshot in window)
- **Latest observation:** BTC @ 73183.09

## Daily Brief
The brief below was generated from the journal contents and then persisted back into the journal as a `market.state_brief` event.

```text
Daily Market State Brief
Window: 2026-05-31T06:19:54.145101Z → 2026-06-01T06:19:54.145101Z
Freshness basis: observed_at
Trust level: high
Assets: 2
- BTC: BTC last seen on binance.futures at 2026-06-01T06:19:47.687000Z with price 73247.4
- ETH: ETH last seen on binance.futures at 2026-06-01T06:19:47.687000Z with price 1991.03
```

## Evidence

### 1) Raw observation
Representative live raw API sample captured during validation:
```json
{
  "askPrice": "73242.80000000",
  "askQty": "2.54987000",
  "bidPrice": "73242.79000000",
  "bidQty": "1.58205000",
  "closeTime": 1780295014005,
  "count": 1766057,
  "firstId": 6336246335,
  "highPrice": "74198.00000000",
  "lastId": 6338012391,
  "lastPrice": "73242.80000000",
  "lastQty": "0.00275000",
  "lowPrice": "73180.00000000",
  "openPrice": "74034.95000000",
  "openTime": 1780208614005,
  "prevClosePrice": "74034.94000000",
  "priceChange": "-792.15000000",
  "priceChangePercent": "-1.070",
  "quoteVolume": "599036645.46296370",
  "symbol": "BTCUSDT",
  "volume": "8125.95186000",
  "weightedAvgPrice": "73718.95081138"
}
```

### 2) Normalized observation
Same sample normalized through the existing ingestion schema:
```json
{
  "collected_at": "2026-06-01T06:23:35.115033Z",
  "funding": null,
  "liquidations": null,
  "observed_at": "2026-06-01T06:23:35.115033Z",
  "open_interest": null,
  "price": 73242.8,
  "quote_volume": 599036645.4629637,
  "schema_version": "1.0.0",
  "source": "binance.spot",
  "spread_bps": 0.0013653219168728156,
  "symbol": "BTC",
  "venue_metadata": {
    "askPrice": 73242.8,
    "bidPrice": 73242.79,
    "endpoint": "/api/v3/ticker/24hr",
    "exchange_time_ms": 1780295014005,
    "venue_symbol": "BTCUSDT"
  },
  "volume": 8125.95186
}
```

### 3) Journal event
Most recent journaled brief event:
```json
{
  "assets": [
    {
      "evidence": "BTC last seen on binance.futures at 2026-06-01T06:19:47.687000Z with price 73247.4",
      "funding": 4.056e-05,
      "latest_collected_at": null,
      "latest_observed_at": "2026-06-01T06:19:47.687000Z",
      "latest_source": "binance.futures",
      "liquidations": null,
      "open_interest": 105170.117,
      "price": 73247.4,
      "price_change_bps": -4.51,
      "quote_volume": 5256979668.84,
      "spread_bps": null,
      "symbol": "BTC",
      "volume": 71359.398
    },
    {
      "evidence": "ETH last seen on binance.futures at 2026-06-01T06:19:47.687000Z with price 1991.03",
      "funding": 8.985e-05,
      "latest_collected_at": null,
      "latest_observed_at": "2026-06-01T06:19:47.687000Z",
      "latest_source": "binance.futures",
      "liquidations": null,
      "open_interest": 2276353.245,
      "price": 1991.03,
      "price_change_bps": 9.6,
      "quote_volume": 5392280761.03,
      "spread_bps": null,
      "symbol": "ETH",
      "volume": 2685334.746
    }
  ],
  "collected_at": "2026-06-01T06:19:54.145101Z",
  "freshness_basis": "observed_at",
  "generated_at": "2026-06-01T06:19:54.145101Z",
  "missing_sources": [],
  "observed_at": "2026-06-01T06:19:54.145101Z",
  "schema_version": "1.0.0",
  "source_module": "trading_autopilot.canonical_journal",
  "source_statuses": [
    {
      "age_minutes": 0.12,
      "freshness_basis": "observed_at",
      "last_collected_at": null,
      "last_observed_at": "2026-06-01T06:19:46.770000Z",
      "source": "binance.spot",
      "status": "fresh"
    },
    {
      "age_minutes": 0.11,
      "freshness_basis": "observed_at",
      "last_collected_at": null,
      "last_observed_at": "2026-06-01T06:19:47.687000Z",
      "source": "binance.futures",
      "status": "fresh"
    },
    {
      "age_minutes": 0.11,
      "freshness_basis": "observed_at",
      "last_collected_at": null,
      "last_observed_at": "2026-06-01T06:19:47.535742Z",
      "source": "coinbase.spot",
      "status": "fresh"
    }
  ],
  "trust_level": "high",
  "window_end": "2026-06-01T06:19:54.145101Z",
  "window_start": "2026-05-31T06:19:54.145101Z"
}
```

### 4) Daily brief
```text
Daily Market State Brief
Window: 2026-05-31T06:19:54.145101Z → 2026-06-01T06:19:54.145101Z
Freshness basis: observed_at
Trust level: high
Assets: 2
- BTC: BTC last seen on binance.futures at 2026-06-01T06:19:47.687000Z with price 73247.4
- ETH: ETH last seen on binance.futures at 2026-06-01T06:19:47.687000Z with price 1991.03
```

## Operational proof
- 3 live collection cycles completed successfully.
- All required sources were represented in each cycle: `binance.spot`, `binance.futures`, `coinbase.spot`.
- The journal contains 18 real `market.snapshot` events plus 1 `market.state_brief` event.
- The daily brief was generated from journal contents, not synthetic fixtures.
- The brief was persisted back into the journal.

## Deliverables
- **Operational validation report:** `/workspace/live-hermes/docs/reports/2026-06-01-operational-validation-report.md`
- **First real daily brief:** `/workspace/live-hermes/docs/reports/2026-06-01-first-real-market-brief.md`
