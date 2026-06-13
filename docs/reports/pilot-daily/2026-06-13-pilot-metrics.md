# Pilot Daily Metrics

- date: 2026-06-13
- journal path: /var/lib/job-intel/state/job_intel.sqlite3
- journal size bytes: 49152

## Event counts

- market.snapshot: 12
- market.state_brief: 2
- market.critical_alert: 0
- all event types: {"market.snapshot": 12, "market.state_brief": 2}

## Source state / freshness

- binance.spot: count=4 latest_observed_at=2026-06-13T07:16:16.597000Z
- binance.futures: count=4 latest_observed_at=2026-06-13T07:16:18.030000Z
- coinbase.spot: count=4 latest_observed_at=2026-06-13T07:16:18.376256Z

## pilot-run stdout

```text
Pilot Operation Status
Window: 2026-06-12T07:16:20.753094Z → 2026-06-13T07:16:20.753094Z
Mode: observer-only | Read-only: true
Last successful run: 2026-06-13T07:16:20.753094Z
Report generation: ok | Replay validation: consistent
Metrics: reports=1 alerts=0 stale_sources=0 mismatches=0 failures=0
Source freshness: binance.spot fresh (0m), binance.futures fresh (0m), coinbase.spot fresh (0m)
Recent briefs: 2 stored | 2 assets, trust=high, window=2026-06-12T07:16:20.753094Z→2026-06-13T07:16:20.753094Z
Recent alerts: 0 stored | none
```
