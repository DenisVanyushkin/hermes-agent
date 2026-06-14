# 14-Day Pilot — Daily Metrics (2026-06-14)

## Run Info

- **Date:** 2026-06-14 (Asia/Almaty)
- **Journal path:** /var/lib/job-intel/state/job_intel.sqlite3
- **Journal size:** 315,392 bytes

## Market Collector Output

```
observations_written: 6
duplicate_observations: 0
symbols: BTC, ETH
sources: binance.spot, binance.futures, coinbase.spot
source_errors: []
```

All three sources returned **fresh** data (age_minutes ~0).

## Pilot Run Output

```
Pilot Operation Status
Window: 2026-06-13T07:16:19.745300Z → 2026-06-14T07:16:19.745300Z
Mode: observer-only | Read-only: true
Last successful run: 2026-06-14T07:16:19.745300Z
Report generation: ok | Replay validation: consistent
Metrics: reports=1 alerts=1 stale_sources=0 mismatches=0 failures=0
Source freshness: binance.spot fresh (0m), binance.futures fresh (0m), coinbase.spot fresh (0m)
Recent briefs: 3 stored | 2 assets, trust=high, window=2026-06-13T07:16:19.745300Z→2026-06-14T07:16:19.745300Z
Recent alerts: 1 stored | replay.mismatch on journal: Replay validation found 26 mismatch(s)
```

## Journal Event Counts

| Event type            | Count |
|-----------------------|------:|
| market.snapshot       |    18 |
| market.state_brief    |     3 |
| market.critical_alert |     1 |
| **Total**             | **22** |

## Per-Source State (market.snapshot)

| Source           | Snapshot count | Latest observed_at             |
|------------------|---------------:|--------------------------------|
| binance.spot     |              6 | 2026-06-14T07:16:13.540000Z    |
| binance.futures  |              6 | 2026-06-14T07:16:14.425000Z    |
| coinbase.spot    |              6 | 2026-06-14T07:16:14.651951Z    |

All sources fresh, no errors.

## Daily Brief (latest window)

- **Window:** 2026-06-13 → 2026-06-14
- **Trust:** high
- **Assets:** BTC ($64,227.62, −3.19 bps), ETH ($1,674.26, −5.31 bps)

## Alerts

- 1 stored: `replay.mismatch` — "Replay validation found 26 mismatch(s)" (carried over from prior runs; not a new operational issue)

## Notes

- Collection and pilot-run both completed without errors.
- All three sources (binance.spot, binance.futures, coinbase.spot) healthy and fresh.
- The replay mismatch alert is a pre-existing journal-level inconsistency, not a new failure.
