# Pilot Daily Metrics — 2026-06-07

- date: 2026-06-07
- journal_path: /var/lib/job-intel/state/job_intel.sqlite3
- journal_size_bytes: 323584

## Event counts
- market.critical_alert: 3
- market.snapshot: 36
- market.state_brief: 6

## Specific event counts
- market.snapshot: 36
- market.state_brief: 6
- market.critical_alert: 3

## Source state / freshness
- binance.spot: count=12, latest_observed_at=2026-06-07T07:16:10.278000Z
- binance.futures: count=12, latest_observed_at=2026-06-07T07:16:11.114000Z
- coinbase.spot: count=12, latest_observed_at=2026-06-07T07:16:11.503263Z

## Pilot run stdout
```text
Pilot Operation Status
Window: 2026-06-06T07:16:13.515767Z → 2026-06-07T07:16:13.515767Z
Mode: observer-only | Read-only: true
Last successful run: 2026-06-07T07:16:13.515767Z
Report generation: ok | Replay validation: consistent
Metrics: reports=1 alerts=3 stale_sources=0 mismatches=0 failures=0
Source freshness: binance.spot fresh (0m), binance.futures fresh (0m), coinbase.spot fresh (0m)
Recent briefs: 5 stored | 2 assets, trust=high, window=2026-06-06T07:16:13.515767Z→2026-06-07T07:16:13.515767Z
Recent alerts: 3 stored | replay.mismatch on journal: Replay validation found 24 mismatch(s)
```
