# Pilot Daily Metrics

- date: 2026-06-12
- journal path: /var/lib/job-intel/state/job_intel.sqlite3
- journal size bytes: 40960

## Event counts

- market.snapshot: 6
- market.state_brief: 1
- market.critical_alert: 0
- all event types: {"market.critical_alert": 0, "market.snapshot": 6, "market.state_brief": 1}

## Source state / freshness

- binance.spot: count=2 latest_observed_at=2026-06-12T07:15:49.089000Z
- binance.futures: count=2 latest_observed_at=2026-06-12T07:15:49.874000Z
- coinbase.spot: count=2 latest_observed_at=2026-06-12T07:15:50.441448Z

## pilot-run stdout

```text
Pilot Operation Status
Window: 2026-06-11T07:15:51.937313Z → 2026-06-12T07:15:51.937313Z
Mode: observer-only | Read-only: true
Last successful run: 2026-06-12T07:15:51.937313Z
Report generation: ok | Replay validation: consistent
Metrics: reports=1 alerts=0 stale_sources=0 mismatches=0 failures=0
Source freshness: binance.spot fresh (0m), binance.futures fresh (0m), coinbase.spot fresh (0m)
Recent briefs: 1 stored | 2 assets, trust=high, window=2026-06-11T07:15:51.937313Z→2026-06-12T07:15:51.937313Z
Recent alerts: 0 stored | none
```
