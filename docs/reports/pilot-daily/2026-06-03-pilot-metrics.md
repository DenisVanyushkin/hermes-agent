# Daily Market State Brief Metrics — 2026-06-03

- Date: 2026-06-03
- Journal path: /var/lib/job-intel/state/job_intel.sqlite3
- Journal size (bytes): 40960

## Event counts by event_type
- market.snapshot: 6
- market.state_brief: 1

## Key event counts
- market.snapshot: 6
- market.state_brief: 1
- market.critical_alert: 0

## Source state / freshness
- binance.spot: count=2, latest_observed_at=2026-06-03T07:15:47.755000Z
- binance.futures: count=2, latest_observed_at=2026-06-03T07:15:48.540000Z
- coinbase.spot: count=2, latest_observed_at=2026-06-03T07:15:48.249620Z

## pilot-run stdout
```
Pilot Operation Status
Window: 2026-06-02T07:15:50.492246Z → 2026-06-03T07:15:50.492246Z
Mode: observer-only | Read-only: true
Last successful run: 2026-06-03T07:15:50.492246Z
Report generation: ok | Replay validation: consistent
Metrics: reports=1 alerts=0 stale_sources=0 mismatches=0 failures=0
Source freshness: binance.spot fresh (0m), binance.futures fresh (0m), coinbase.spot fresh (0m)
Recent briefs: 1 stored | 2 assets, trust=high, window=2026-06-02T07:15:50.492246Z→2026-06-03T07:15:50.492246Z
Recent alerts: 0 stored | none
```
