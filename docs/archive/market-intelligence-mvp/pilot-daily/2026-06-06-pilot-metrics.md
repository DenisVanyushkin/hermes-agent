# Pilot Daily Metrics — 2026-06-06

- Date: 2026-06-06
- Journal path: /var/lib/job-intel/state/job_intel.sqlite3
- Journal size (bytes): 315392

## Event counts by event_type
- market.critical_alert: 2
- market.snapshot: 30
- market.state_brief: 5

## Key market event counts
- market.snapshot: 30
- market.state_brief: 5
- market.critical_alert: 2

## Source state / freshness
- binance.spot: count=10, latest_observed_at=2026-06-06T07:19:16.203000Z
- binance.futures: count=10, latest_observed_at=2026-06-06T07:19:17.003000Z
- coinbase.spot: count=10, latest_observed_at=2026-06-06T07:19:17.747254Z

## pilot-run stdout
```text
Pilot Operation Status
Window: 2026-06-05T07:19:20.342346Z → 2026-06-06T07:19:20.342346Z
Mode: observer-only | Read-only: true
Last successful run: 2026-06-06T07:19:20.342346Z
Report generation: ok | Replay validation: consistent
Metrics: reports=1 alerts=2 stale_sources=0 mismatches=0 failures=0
Source freshness: binance.spot fresh (0m), binance.futures fresh (0m), coinbase.spot fresh (0m)
Recent briefs: 5 stored | 2 assets, trust=high, window=2026-06-05T07:19:20.342346Z→2026-06-06T07:19:20.342346Z
Recent alerts: 2 stored | replay.mismatch on journal: Replay validation found 22 mismatch(s)
```
