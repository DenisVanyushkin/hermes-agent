# Daily Market State Brief Metrics

- Date: 2026-06-01
- Journal path: `/var/lib/job-intel/state/job_intel.sqlite3`
- Journal size: 40960 bytes

## Event counts by event_type

| event_type | count |
|---|---:|
| market.snapshot | 6 |
| market.state_brief | 1 |

## Key market event counts

- market.snapshot: 6
- market.state_brief: 1
- market.critical_alert: 0

## Source state / freshness

| source | count | latest_observed_at |
|---|---:|---|
| binance.spot | 2 | 2026-06-01T07:22:21.347000Z |
| binance.futures | 2 | 2026-06-01T07:22:22.173000Z |
| coinbase.spot | 2 | 2026-06-01T07:22:22.333673Z |

## pilot-run stdout

```text
Pilot Operation Status
Window: 2026-05-31T07:22:24.327223Z → 2026-06-01T07:22:24.327223Z
Mode: observer-only | Read-only: true
Last successful run: 2026-06-01T07:22:24.327223Z
Report generation: ok | Replay validation: consistent
Metrics: reports=1 alerts=0 stale_sources=0 mismatches=0 failures=0
Source freshness: binance.spot fresh (0m), binance.futures fresh (0m), coinbase.spot fresh (0m)
Recent briefs: 1 stored | 2 assets, trust=high, window=2026-05-31T07:22:24.327223Z→2026-06-01T07:22:24.327223Z
Recent alerts: 0 stored | none
```
