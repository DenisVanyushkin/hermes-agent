# Pilot Metrics Snapshot — 2026-06-02

- Date: 2026-06-02
- Journal path: /var/lib/job-intel/state/job_intel.sqlite3
- Journal size (bytes): 49152

## Event counts
- market.critical_alert: 1
- market.snapshot: 12
- market.state_brief: 2

## Key market event counts
- market.snapshot: 12
- market.state_brief: 2
- market.critical_alert: 1

## Source state / freshness
- binance.spot: count=4, latest_observed_at=2026-06-02T07:16:57.599000Z
- binance.futures: count=4, latest_observed_at=2026-06-02T07:16:58.361000Z
- coinbase.spot: count=4, latest_observed_at=2026-06-02T07:16:59.615544Z

## pilot-run stdout
```text
Pilot Operation Status
Window: 2026-06-01T07:17:00.473221Z → 2026-06-02T07:17:00.473221Z
Mode: observer-only | Read-only: true
Last successful run: 2026-06-02T07:17:00.473221Z
Report generation: ok | Replay validation: consistent
Metrics: reports=1 alerts=1 stale_sources=0 mismatches=0 failures=0
Source freshness: binance.spot fresh (0m), binance.futures fresh (0m), coinbase.spot fresh (0m)
Recent briefs: 2 stored | 2 assets, trust=high, window=2026-06-01T07:17:00.473221Z→2026-06-02T07:17:00.473221Z
Recent alerts: 1 stored | replay.mismatch on journal: Replay validation found 24 mismatch(s)
```

