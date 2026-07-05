# Market Intelligence MVP — Pilot Archive

**Status:** RETIRED (engineering closure 2026-07-05). The pilot completed
2026-06-01 → 2026-06-14; the product hypothesis was not validated strongly
enough to keep an always-on service. Implementation is preserved as reusable
infrastructure in `trading_autopilot/` (see its README).

Closure report: `docs/reports/2026-07-05-market-intelligence-mvp-closure.md`
Post-pilot operational review: `reports/2026-07-05-post-pilot-operational-review.md`

## Contents

| Path | What it is |
|---|---|
| `reports/2026-05-31-proof-of-life-report.md` | First end-to-end proof of life |
| `reports/2026-05-31-market-ingestion-validation-report.md` | Ingestion validation |
| `reports/2026-05-31-pilot-readiness-report.md` | Go/no-go readiness check |
| `reports/2026-06-01-first-real-market-brief.md` | First real Daily Market State Brief |
| `reports/2026-06-01-operational-validation-report.md` | Live-deployment validation chain |
| `reports/2026-06-14-pilot-evaluation-report.md` | Final in-pilot evaluation (verdict: KEEP AS IS) |
| `reports/2026-07-05-post-pilot-operational-review.md` | Post-pilot operator review (verdict: RETIRE) |
| `pilot-daily/*.md` | 11 daily metrics snapshots (Jun 1–8, 12–14) |
| `runbooks/2026-05-31-pilot-launch-runbook.md` | Pilot launch runbook |
| `cron-output/market-intelligence-14day-pilot-daily/` | Raw outputs of the daily pilot cron job (a92e081e1836) |
| `cron-output/market-intelligence-14day-pilot-status/` | Raw outputs of the daily status cron job (581a263800bc) |
| `cron-output/market-intelligence-14day-pilot-final-report/` | Raw output of the final-report cron job (33323129fed8) |

The cron-output copies exist because the pilot's primary journal
(sandbox-local SQLite) did not survive the pilot; these outputs are the
authoritative surviving operational record.

## What was disabled at closure (2026-07-05)

Nothing was deleted. To re-enable, reverse the steps below.

| Component | How it was disabled |
|---|---|
| hermes cron `trading-autopilot-slack-events` (978c412657ef, every minute) | `hermes cron pause` |
| hermes cron `trading-autopilot-daily-summary` (2748b89afd0e, daily 04:00) | `hermes cron pause` |
| hermes cron `trading-autopilot-hourly-summary` (bf1bcfed2cc3) | already paused 2026-05-28 |
| hermes cron `trading-autopilot-live-read-producer` (c6d8e3f765ba) | already paused 2026-05-28 |
| Docker `monitoring-trading-autopilot-exporter` | stopped + removed; service gated behind `profiles: ["retired"]` in `~/.hermes/monitoring/docker-compose.yml` |
| Prometheus scrape job `trading-autopilot` | commented out in `~/.hermes/monitoring/prometheus/prometheus.yml` |
| Grafana dashboard "Trading Autopilot" | provisioning JSON moved to `~/.hermes/monitoring/grafana/dashboards-retired/`; auto-pruned from Grafana (source kept at `deploy/grafana/trading-autopilot-dashboard.json`) |

Config backups from before the closure edits:
`~/.hermes/monitoring/docker-compose.yml.bak-mvp-closure-20260705`,
`~/.hermes/monitoring/prometheus/prometheus.yml.bak-mvp-closure-20260705`.

Left in place intentionally (inert without the jobs above):
`~/.hermes/scripts/trading_autopilot_*.py` wrapper scripts, the dashboard
source JSON `deploy/grafana/trading-autopilot-dashboard.json`, the paper-trading
journal `~/.hermes/cron/journals/paper_trading_mvp.sqlite3` (historical data),
and the `trading_autopilot/` package + tests (reusable infrastructure).

The market-intelligence pilot cron jobs themselves (`market-intelligence-14day-pilot-*`)
expired naturally on 2026-06-14 via `repeat.times` and no longer exist in the
scheduler registry.
