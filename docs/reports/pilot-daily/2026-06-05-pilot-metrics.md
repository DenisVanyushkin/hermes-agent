# Pilot Daily Metrics — 2026-06-05

- Date: 2026-06-05
- Journal path: /var/lib/job-intel/state/job_intel.sqlite3
- Journal size (bytes): 307200

## Event counts by event_type
- market.critical_alert: 2
- market.snapshot: 24
- market.state_brief: 4

## Key market event counts
- market.snapshot: 24
- market.state_brief: 4
- market.critical_alert: 2

## Source state / freshness
- binance.futures: count=8, latest_observed_at=2026-06-05T07:16:18.080000Z
- binance.spot: count=8, latest_observed_at=2026-06-05T07:16:17.309000Z
- coinbase.spot: count=8, latest_observed_at=2026-06-05T07:16:18.526020Z

## pilot-run stdout
```text
{
  "counts_by_event_type": {
    "market.snapshot": 6
  },
  "counts_by_source": {
    "binance.futures": 2,
    "binance.spot": 2,
    "coinbase.spot": 2
  },
  "counts_by_symbol": {
    "BTC": 3,
    "ETH": 3
  },
  "duplicate_observations": 0,
  "generated_at": "2026-06-05T07:15:56.268665Z",
  "journal_path": "/var/lib/job-intel/state/job_intel.sqlite3",
  "observations_written": 6,
  "schema_version": "1.0.0",
  "source_errors": [],
  "source_freshness": {
    "binance.futures": {
      "age_minutes": -0.02,
      "freshness_basis": "observed_at",
      "last_collected_at": "2026-06-05T07:15:57.168412Z",
      "last_observed_at": "2026-06-05T07:15:57.333000Z",
      "source": "binance.futures",
      "status": "fresh"
    },
    "binance.spot": {
      "age_minutes": -0.0,
      "freshness_basis": "observed_at",
      "last_collected_at": "2026-06-05T07:15:56.268811Z",
      "last_observed_at": "2026-06-05T07:15:56.508000Z",
      "source": "binance.spot",
      "status": "fresh"
    },
    "coinbase.spot": {
      "age_minutes": -0.04,
      "freshness_basis": "observed_at",
      "last_collected_at": "2026-06-05T07:15:58.966421Z",
      "last_observed_at": "2026-06-05T07:15:58.743732Z",
      "source": "coinbase.spot",
      "status": "fresh"
    }
  },
  "sources": [
    "binance.spot",
    "binance.futures",
    "coinbase.spot"
  ],
  "symbols": [
    "BTC",
    "ETH"
  ]
}
```
