# Pilot Metrics Snapshot — 2026-06-04

- date: 2026-06-04
- journal_path: /var/lib/job-intel/state/job_intel.sqlite3
- journal_size_bytes: 290816

## Event counts by event_type
- market.snapshot: 12
- market.state_brief: 2

## Selected event counts
- market.snapshot: 12
- market.state_brief: 2
- market.critical_alert: 0

## Source state / freshness
- binance.spot: count=4, latest_observed_at=2026-06-04T07:16:24.438000Z
- binance.futures: count=4, latest_observed_at=2026-06-04T07:16:25.182000Z
- coinbase.spot: count=4, latest_observed_at=2026-06-04T07:16:25.438343Z

## pilot-run stdout
```text
Pilot Operation Status
Window: 2026-06-03T07:16:27.201817Z → 2026-06-04T07:16:27.201817Z
Mode: observer-only | Read-only: true
Last successful run: 2026-06-04T07:16:27.201817Z
Report generation: ok | Replay validation: consistent
Metrics: reports=1 alerts=0 stale_sources=0 mismatches=0 failures=0
Source freshness: binance.spot fresh (0m), binance.futures fresh (0m), coinbase.spot fresh (0m)
Recent briefs: 2 stored | 2 assets, trust=high, window=2026-06-03T07:16:27.201817Z→2026-06-04T07:16:27.201817Z
Recent alerts: 0 stored | none
```
