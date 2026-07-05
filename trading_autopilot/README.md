# trading_autopilot — Reusable Market-Data Infrastructure

**Product status: RETIRED** (Market Intelligence MVP closure, 2026-07-05 —
see `docs/reports/2026-07-05-market-intelligence-mvp-closure.md` and
`docs/archive/market-intelligence-mvp/`). No scheduler, service, or exporter
runs this code automatically anymore.

**Code status: KEPT ON PURPOSE.** The pilot proved the data plane is solid
(11/11 executed runs with zero parsing failures, zero duplicates, zero stale
reads across three exchange sources). This package is preserved as reusable
infrastructure for any future consumer that needs market observations —
do not rebuild this from scratch.

## Reusable components

| Module | What it gives you |
|---|---|
| `market_ingestion.py` | Collector for Binance Spot / Binance Futures / Coinbase Spot (BTC, ETH); freshness tracking, per-source error isolation, duplicate suppression |
| `normalization.py` | Raw API payload → normalized observation (price, volume, quote_volume, spread_bps, funding, open_interest; dual timestamps observed_at/collected_at) |
| `journal.py` | `AppendOnlyJournal` — append-only SQLite event log (`journal_events` table) |
| `canonical_journal.py` | `CanonicalJournal` — schema-versioned canonical event layer over the journal |
| `daily_market_state_brief.py` | Windowed brief builder (per-asset price change bps, trust level, source freshness) |
| `replay_validation.py` | Journal replay/fingerprint consistency checks (see caveat below) |
| `observer.py`, `pilot_operation.py` | Observer-only orchestration used by the pilot |
| `prometheus.py` | Journal → Prometheus exporter (`python -m trading_autopilot.prometheus --journal-path … --serve`) |
| `monitoring.py`, `critical_alerts.py` | Alert persistence and monitoring helpers |
| `live_read.py`, `paper.py`, `strategy.py`, `risk.py` | Paper-trading loop (separate experiment, also dormant) |

Tests: `tests/trading_autopilot/` (12 test modules covering journal, ingestion,
normalization, replay, observer, pilot operation, risk, strategy).

## Hard-won operational caveats (from the pilot)

1. **Journal placement:** the journal MUST live on host-persistent storage.
   During the pilot it lived on a sandbox-ephemeral path and silently reset at
   least twice, then was lost entirely. Use a path like
   `/var/lib/<service>/state/` on the host, never a container-local path.
2. **`replay_validation` has a known unresolved defect:** it produced a
   persistent, slowly growing `replay.mismatch` alert (22→26 mismatches) that
   was 100 % of pilot alert volume and 0 % signal. Fix the fingerprint logic
   or do not wire it to alerting.
3. **Add a liveness (dead-man) alert** for any scheduled consumer: the pilot's
   3-day outage (Jun 9–11) produced zero alerts.
4. Dead schema fields: `liquidations` was never populated by any source;
   `funding`/`open_interest` are futures-only — label or drop them for
   spot-focused consumers.
5. `spread_bps` is the sleeper field: it exposed a ~120× cross-venue spread
   difference (Coinbase vs Binance BTC) — the pilot's only novel analytical
   insight. Any future brief should lead with it.
