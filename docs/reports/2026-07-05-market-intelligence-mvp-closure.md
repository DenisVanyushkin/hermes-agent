# Market Intelligence MVP — Closure Report

**Date:** 2026-07-05
**Action:** clean engineering shutdown of a completed experiment
**Preceding reviews:** `2026-06-14-pilot-evaluation-report.md` (in-pilot, KEEP AS IS),
`2026-07-05-post-pilot-operational-review.md` (post-pilot, RETIRE)

## Executive Summary

- **Pilot duration:** 14 calendar days (2026-06-01 → 2026-06-14), 11 executed
  daily cycles, one 3-day infrastructure gap (Jun 9–11). Observer-only,
  read-only, zero trading exposure.
- **Implementation scope:** collector + normalizer for 3 sources (Binance Spot,
  Binance Futures, Coinbase Spot) × 2 assets (BTC, ETH); append-only
  schema-versioned journal; windowed Daily Market State Brief; replay
  validation; critical-alert persistence; Prometheus exporter + Grafana
  dashboard; 12 test modules; daily pilot/status/final-report cron jobs.
- **Operational verdict:** the *implementation* succeeded — the data plane ran
  flawlessly whenever it ran. The *product hypothesis* (an always-on
  observer-only market intelligence service provides enough practical value to
  justify operating it) was **not validated**: output was information-thin,
  alerting was 100 % noise, durability failed at the platform seam, and the
  system's three-week post-pilot absence went unnoticed by any consumer.
  The service is therefore retired; the engineering investment is preserved.

## What Worked

- **Collector (`market_ingestion.py`):** 0 parsing failures, 0 duplicate
  observations, 0 stale reads across 11 runs × 3 heterogeneous sources; every
  freshness check < 1 minute.
- **Normalization (`normalization.py`):** stable schema 1.0.0 throughout;
  dual timestamps (observed_at / collected_at) made freshness measurable.
- **Journal architecture (design, not deployment):** the evidence chain
  raw → normalized → journal event → brief was fully traceable while the
  journal existed; `event_id`/`correlation_id` linking worked as designed.
- **Testing:** 12 test modules; no code-level defects surfaced during 14 days
  of live operation.
- **Worth keeping besides code:** the daily-markdown-snapshot habit (the only
  artifact tier that survived), the status job's reconstruct-from-reports
  fallback, and the strict scope discipline in the cron prompts.

## What Did Not Work

- **Product value:** no identified consumer. Once-daily brief for 2 assets
  duplicates what any exchange app shows; ~half of each brief's lines never
  changed during the pilot; nobody requested the briefs back after Jun 14.
- **Reporting usefulness:** three overlapping delivery layers (pilot stdout,
  daily metrics file, status cron) restated the same data; the one novel
  insight (`spread_bps`: Coinbase BTC spread ~120× Binance) was never surfaced
  as a headline.
- **Alert quality:** 100 % of alerts were one known `replay.mismatch`
  fingerprint defect (22→26 mismatches, never root-caused); the two real
  incidents (3-day outage, journal resets) fired nothing.
- **Operational deployment:** the append-only journal lived on a
  sandbox-ephemeral path — it silently reset at least twice mid-pilot and was
  wiped entirely on 2026-06-21; availability was ~79 % with outage detection
  only by human reading of daily statuses; time-boxed pilot jobs had no
  decommission step, leaving a per-minute zombie forwarder running for three
  weeks (54,795+ executions) and producing the Jun 21 alert-spam incident.
- **Other validated failures:** dead schema fields (`liquidations` always
  null); state file resets forced day-count reconstruction from reports.

## Assets Preserved (intentionally kept)

Code — `trading_autopilot/` package, documented in `trading_autopilot/README.md`:

- `journal.py` / `canonical_journal.py` (append-only + canonical journal)
- `market_ingestion.py` (collector), `normalization.py` (observation models)
- `daily_market_state_brief.py`, `replay_validation.py` (with documented defect),
  `observer.py`, `pilot_operation.py`, `prometheus.py`, `monitoring.py`,
  `critical_alerts.py`, paper-trading modules (`paper.py`, `strategy.py`,
  `risk.py`, `live_read.py`)
- `tests/trading_autopilot/` — full test suite (12 modules)

Artifacts — `docs/archive/market-intelligence-mvp/` (47+ files, indexed in its README):

- all 7 pilot/validation/evaluation/review reports (incl. the post-pilot
  operational review), 11 daily metrics snapshots, launch runbook, and raw
  outputs of all three pilot cron jobs (the authoritative surviving
  operational record, since the journal itself was lost)

Also kept in place (inert): `~/.hermes/scripts/trading_autopilot_*.py`
wrappers, paper-trading journal
`~/.hermes/cron/journals/paper_trading_mvp.sqlite3`, paused cron job
definitions (for reversibility), and the dashboard source JSON
`deploy/grafana/trading-autopilot-dashboard.json` (live Grafana copy retired,
see below).

## Assets Retired (intentionally disabled, not deleted)

| Asset | Disable mechanism | Date |
|---|---|---|
| cron `trading-autopilot-slack-events` (978c412657ef, `*/1 * * * *`) | `hermes cron pause` | 2026-07-05 |
| cron `trading-autopilot-daily-summary` (2748b89afd0e, `0 4 * * *`) | `hermes cron pause` | 2026-07-05 |
| cron `trading-autopilot-hourly-summary` (bf1bcfed2cc3) | paused (pre-existing) | 2026-05-28 |
| cron `trading-autopilot-live-read-producer` (c6d8e3f765ba) | paused (pre-existing) | 2026-05-28 |
| Docker `monitoring-trading-autopilot-exporter` | container stopped+removed; compose service gated behind `profiles: ["retired"]` | 2026-07-05 |
| Prometheus scrape job `trading-autopilot` | commented out, Prometheus reloaded | 2026-07-05 |
| Grafana dashboard "Trading Autopilot" | provisioning JSON moved to `~/.hermes/monitoring/grafana/dashboards-retired/`; auto-pruned from Grafana (`disableDeletion: false`) | 2026-07-05 |
| cron `market-intelligence-14day-pilot-{daily,status,final-report}` | expired naturally via `repeat.times` on 2026-06-14; no longer in registry | 2026-06-14 |

No systemd services or timers existed for this system (verified).
Config backups: `docker-compose.yml.bak-mvp-closure-20260705`,
`prometheus.yml.bak-mvp-closure-20260705` (both in `~/.hermes/monitoring/`).

## Follow-up Recommendations

### Keep forever (reusable infrastructure)
- Collector + normalization + observation models (`market_ingestion.py`,
  `normalization.py`) — proven flawless; reuse for any future market-data need.
- Append-only journal + canonical layer (`journal.py`, `canonical_journal.py`)
  — sound design; **must** be deployed on host-persistent storage
  (`/var/lib/<service>/state/` pattern), never a sandbox path.
- The test suite and the daily-markdown-snapshot reporting habit.
- Operational lesson as policy: every scheduled consumer gets a dead-man
  (liveness) alert, and every time-boxed pilot gets an explicit decommission
  step for its supporting jobs.

### Revisit only with a new hypothesis
- Market state briefs — only if a concrete consumer exists first (e.g. a
  resumed paper-trading loop that reads the brief programmatically). Start
  from "which decision does this change", not from collection.
- Cross-venue spread/divergence monitoring — the pilot's one novel insight;
  could be a hypothesis of its own, with alerting on divergence, not on
  internal bookkeeping.
- The paper-trading loop (`paper.py`/`strategy.py`/`risk.py`) — separate
  experiment, dormant since 2026-05-31; needs its own justification.

### Do not revive
- The always-on observer-only daily-brief service as piloted — disproven:
  no consumer, information-thin output, absence unnoticed for three weeks.
- `replay_validation` wired to alerting in its current form — 100 % noise;
  fix the fingerprint logic first or keep it as an offline debugging tool.
- Human-facing per-minute/hourly Slack forwarding of journal events —
  produced only spam (Jun 21 incident: 20+ identical failure messages).

## Final Repository Status

- No active Market Intelligence / trading-autopilot schedulers remain:
  all 4 cron jobs paused (`enabled=false, state=paused` in
  `~/.hermes/cron/jobs.json`); pilot jobs expired; no systemd units/timers;
  system crontabs clean.
- No zombie jobs remain: the per-minute Slack forwarder and daily summary —
  the two jobs identified by the post-pilot review — are paused; the
  trading-autopilot exporter container is stopped and gated.
- Prometheus target list verified post-reload: `cadvisor`, `job-intel`,
  `node_exporter`, `prometheus`, `promtail` — no trading-autopilot target.
- Repository is internally consistent: code + tests untouched and passing
  ownership to "reusable infrastructure" via `trading_autopilot/README.md`;
  originals of all reports remain in `docs/reports/`; archive is additive.
- Archived documentation is complete: 47+ files indexed in
  `docs/archive/market-intelligence-mvp/README.md`, including the raw cron
  outputs that substitute for the lost journal.
