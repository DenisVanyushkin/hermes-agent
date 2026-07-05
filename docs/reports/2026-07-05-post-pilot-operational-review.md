# Post-Pilot Operational Review — Market Intelligence MVP

**Date:** 2026-07-05
**Reviewer:** Claude (operator-mode review, three weeks after pilot end)
**Question under review:** not "does the code work" but "does this system deserve to exist"

## Evidence Base and Its Limits

Primary evidence intended for this review — the journal SQLite
(`journal_events` table, reported path `/var/lib/job-intel/state/job_intel.sqlite3`
*inside the pilot sandbox*) — **no longer exists**. The sandbox-local journal was
never persisted to the host, and the repo-level `.artifacts/journals/` directory
was emptied during a cleanup on 2026-06-21.

The review is therefore reconstructed from surviving secondary artifacts, all cross-checked:

| Artifact | Location (host) | Coverage |
|---|---|---|
| Daily metrics snapshots (11 files) | `~/.hermes/hermes-agent/docs/reports/pilot-daily/*.md` | Jun 1–8, 12–14 |
| Daily-pilot cron outputs (14 files) | `~/.hermes/cron/output/a92e081e1836/` | Jun 1–8, 10–15 |
| Status cron outputs (14 files) | `~/.hermes/cron/output/581a263800bc/` | Jun 1–14 |
| Final evaluation report | `docs/reports/2026-06-14-pilot-evaluation-report.md` | pilot summary |
| Slack #trading history | delivered daily statuses + final report | Jun 1–14 |

The loss of the journal itself is treated as an operational finding (§6, §7), not
just a caveat.

---

## 1. Runtime Health

| Metric | Value | Basis |
|---|---|---|
| First observed market snapshot | 2026-06-01T07:22:21Z (binance.spot) | Jun 1 metrics file |
| Last observed market snapshot | 2026-06-14T07:16:14Z (coinbase.spot) | Jun 14 metrics file |
| Total runtime duration | 14 calendar days (312 h wall clock) | pilot window |
| Total collection cycles | 14 scheduled daily cycles | cron schedule |
| Successful cycles | **11** | daily snapshots with `failures=0` |
| Failed cycles | **0 explicit failures; 3 cycles simply did not run** (Jun 9–11) | missing daily reports; state file `run_count: 11` |
| Availability estimate | **~79 %** (11/14 days) | above |
| Longest observed collection gap | **~96 hours** (Jun 8 07:16Z → Jun 12 07:15Z) | snapshot timestamps |

Within a cycle the collector itself never failed: every executed run reported
`failures=0`, `stale_sources=0`, `source_errors=[]`. The 3-day outage was an
infrastructure/scheduling interruption, not a data-plane failure — and it was
detected only passively (the Jun 9 status report says "today's run has not landed
yet"; nothing alerted).

## 2. Source Health

Each successful cycle collected 2 observations per source (BTC + ETH), 6 per day.

| Metric | binance.spot | binance.futures | coinbase.spot |
|---|---|---|---|
| Total observations (11 runs × 2) | ~22 | ~22 | ~22 |
| First observation | 2026-06-01T07:22:21Z | 2026-06-01T07:22:22Z | 2026-06-01T07:22:22Z |
| Last observation | 2026-06-14T07:16:13Z | 2026-06-14T07:16:14Z | 2026-06-14T07:16:14Z |
| Stale incidents | 0 | 0 | 0 |
| Missing periods | Jun 9–11 (system-wide, not source-specific) | same | same |
| Estimated uptime (when system ran) | 100 % | 100 % | 100 % |
| Parsing failures | 0 | 0 | 0 |

Every freshness check across all 11 runs reported `fresh` with age < 1 minute.
Source-level reliability was **not** the constraint in this pilot; the sources
outperformed the platform hosting them.

## 3. Journal Statistics

This is where reconstruction hits limits, and the numbers themselves tell the story:

| Metric | Value | Note |
|---|---|---|
| Journal size (last reported) | 315,392 bytes (Jun 14) | but see below |
| Total events (cumulative, estimated) | ~80–90 (≈66 snapshots + ~13 briefs + ~7 alerts) | summed per-day counts across resets |
| Total events (max ever visible at once) | 45 (Jun 7: 36 snapshots + 6 briefs + 3 alerts) | journal reset after Jun 7 |
| Event counts by type (Jun 14, final state) | market.snapshot 18, market.state_brief 3, market.critical_alert 1 | only last 3 days present |
| Average daily growth | ~8–11 KB/day within a continuous segment | size series: 40→49→291→307→315→324 KB, then reset to 49 KB |
| Estimated yearly growth | ~3–4 MB/year at pilot cadence | trivially cheap |
| Replay performance | "consistent" verdict every run; no timing complaints | pilot stdout |
| Replay consistency | **5 of 11 runs carried a `replay.mismatch` alert** (22 → 24 → 26 mismatches) | growing, never resolved |

**The critical journal finding:** counters and the state file reset repeatedly
mid-pilot (day-counter dropped to "day 1 / run 1" on Jun 3 and Jun 8; `start_date`
flipped between Jun 1 and Jun 3). The journal lived inside an ephemeral sandbox
path and did not survive container recreation. The final evaluation report
euphemistically called this "compacted or rebased" — operationally it was **silent
data loss, at least twice during the pilot, and total loss after it** (cleanup of
2026-06-21). An append-only journal whose core promise is durable replayable
history did not durably persist anything.

The status job's state-reconstruction fallback (rebuilding day counts from
persisted daily reports) worked as designed and is the only reason the pilot
timeline is reconstructible at all.

## 4. Report Quality

11 Daily Market State Briefs were generated (1 per successful cycle; `market.state_brief`
journal counts per day confirm 1/day plus window-overlap copies).

| Metric | Value |
|---|---|
| Number of briefs | 11 |
| Average size | ~15 lines / ~600 bytes rendered (2 assets, 3 sources, freshness block) |
| Duplicated reports | 0 observed |
| Empty sections | none structurally, but see dead fields below |
| Fields that never changed | `trust=high` (11/11), `missing_sources: none` (11/11), `Mode: observer-only`, `Read-only: true`, freshness always `fresh (0m)` |
| Fields that changed meaningfully | `price`, `price_change_bps`, `volume`/`quote_volume`, `spread_bps` |
| Dead fields (never populated) | `liquidations` (always null), `latest_collected_at` in brief assets |

Evidence the briefs captured real market state: prices tracked the actual market
(BTC $64,227.62 −3.19 bps, ETH $1,674.26 −5.31 bps on Jun 14), and `spread_bps`
surfaced a genuine, non-obvious microstructure fact — Coinbase BTC spread ~120×
wider than Binance (0.1792 vs 0.0016 bps for the paired assets).

The honest operator assessment, however: a once-daily brief for 2 assets contains
information an operator gets faster from any exchange app. Roughly half of each
brief's lines never changed across the entire pilot (trust, mode, freshness,
missing-sources), so information density was low. The one field with unique
analytical value (`spread_bps` cross-venue comparison) was never highlighted or
trended. Nobody asked for the briefs back after Jun 14 — the strongest report-quality
signal available.

## 5. Alert Quality

| Metric | Value |
|---|---|
| Total alerts | 7 cumulative `market.critical_alert` events (max simultaneous count 3) |
| Alerts by category | `replay.mismatch`: 100 % of all alerts |
| False/noisy alerts | **100 %** — every alert was the same pre-existing replay-fingerprint mismatch, count growing 22→24→26, acknowledged as noise in 5 daily statuses and the final report |
| Alerts that would have been useful to an operator | the ones that never fired: 3-day collection outage (Jun 9–11) produced **zero alerts**; journal resets produced **zero alerts** |

Alerting achieved the worst possible combination: it cried wolf on a known
non-issue every other day and stayed silent on both real incidents.

## 6. Data Quality

| Aspect | Finding |
|---|---|
| Malformed payloads | 0 reported across all runs |
| Duplicate observations | 0 (`duplicate_observations: 0` on every collector output) |
| Replay mismatches | 22–26 persistent mismatches; never root-caused; growing slowly |
| Schema/version drift | none — `schema_version: 1.0.0` throughout |
| Stale observations | 0 (all freshness checks < 1 min) |
| Missing observations | 3 full days (Jun 9–11) ≈ 18 snapshots never collected; plus post-pilot **total loss of the journal itself** (Jun 21 cleanup + non-persistent sandbox path) |

Point-in-time data quality was excellent. Data *durability* was the failure mode:
the system reliably collected data it could not reliably keep.

## 7. Architecture Retrospective

**What worked better than expected**
- The collector/normalizer core: zero parsing failures, zero duplicates, zero stale reads across 3 heterogeneous sources for 11 runs.
- The evidence chain (raw → normalized → journal event → brief) was fully traceable while the journal existed and made the pilot auditable.
- The status job's state-file fallback (reconstruct progress from persisted reports) — the defensive design that saved this very review.
- Persisting daily markdown snapshots to the repo — the only durable artifact tier, at zero engineering cost.

**What turned out to be unnecessary**
- Futures-specific fields (`funding`, `open_interest`) in a 2-asset spot-focused brief — null on 2 of 3 sources.
- `liquidations` — dead schema weight, never populated.
- Three delivery/reporting layers (pilot stdout + daily metrics file + status cron summarizing the metrics file) saying the same thing daily.

**What should be simplified**
- Replay validation: either fix the fingerprint logic or delete it. A validation layer that is 100 % noise is negative value.
- The brief itself: drop never-changing lines, keep price/bps/volume/spread, add cross-venue spread as the headline.

**What should never have been implemented (as built)**
- The journal at a sandbox-ephemeral path. An append-only durability layer inside an ephemeral container is a contradiction; it silently reset at least twice mid-pilot and was wiped entirely a week after. Any future iteration must write to host-persistent storage (the pattern exists: `/var/lib/job-intel/state/` on the host, like job-intel does).

**Next highest-value capability (if continuing at all)**
- Not a data capability — a durability + real-alerting fix: host-persistent journal, dead-man's-switch alert on missed cycles, replay.mismatch fixed or removed. Only after that, the first *analytical* candidate is cross-venue spread/divergence trending, the single field that produced novel insight.

**Post-pilot zombie load (found during this review)**
- `trading-autopilot-slack-events` still fires **every minute** (54,795+ completions) and `trading-autopilot-daily-summary` daily, reading journal directories that have been empty since Jun 21. They cost little but are pure waste and were the source of the Jun 21 alert-spam incident.

## 8. Final Recommendation

### RETIRE

(the current deployment; salvage the collector core and the lessons)

Operational evidence, not opinion:

1. **The system has already been de facto retired for three weeks and nobody noticed.** It stopped on Jun 14, its journal was deleted on Jun 21, and no consumer, dashboard, decision, or person surfaced the absence until this review. A system whose disappearance is invisible does not justify its operational cost.
2. **Its unique architectural promise — durable replayable history — was not delivered.** The journal silently reset at least twice mid-pilot and is now entirely gone. What survived is exactly what a plain "write a daily markdown report" cron would have produced.
3. **Alerting was 100 % noise and 0 % coverage** — every fired alert was the known replay.mismatch; neither real incident (3-day outage, journal resets) fired anything.
4. **The user-facing output was information-thin**: once-daily, 2 assets, half the lines constant, all of it available faster in any exchange app. No evidence anyone acted on a brief.
5. **What did work is small and portable**: the collector/normalizer (0 errors, 0 duplicates, fresh 100 % of runs) is worth keeping as an on-demand skill/library, not as an always-on service.

Immediate housekeeping regardless of decision: remove/pause the two zombie
trading-autopilot cron jobs and archive the pilot artifacts (daily reports +
cron outputs) into the repo before the next cleanup deletes the remaining evidence.

If a future iteration is ever justified by an actual consumer (e.g., the paper-trading
loop resumes and needs market state), rebuild from the collector core with:
host-persistent journal, dead-man alerting, no replay validation until it has an
owner, and cross-venue spread as the first-class output.

---

## Lessons Learned

**What surprised the most:** the sharpest failure was not in market data handling
(flawless) but in the platform seam — an append-only journal placed on an
ephemeral filesystem. The system's weakest link was where it touched Hermes
infrastructure, not where it touched exchanges.

**Design decisions that aged well:**
- Observer-only / read-only mode — zero risk surface for the entire pilot.
- Persisting human-readable daily snapshots to the git repo — the only tier that survived.
- State reconstruction from persisted reports when the state file vanished — defensive design that paid for itself.
- Fixed scope discipline (BTC+ETH, 3 sources, explicit "do not add features" in every prompt) — the pilot ended evaluable instead of sprawling.

**Design decisions that aged poorly:**
- Journal path inside the sandbox (`/var/lib/job-intel/state/...` resolved per-container) — silent resets, then total loss.
- Replay validation shipped without an owner: it produced a permanent unresolved alert that trained everyone to ignore alerts.
- Alert taxonomy with exactly one alert type and no liveness alert — inverted coverage.
- Time-boxed cron jobs (`times: 14`) with no post-pilot decommission step — left per-minute zombie jobs running for 3 weeks.

**What to do completely differently if starting today:** start from the consumer,
not the collector. The pilot proved collection is easy; it never identified who
reads the brief and what decision it changes. One paying consumer (even an internal
loop) would have defined which fields matter, forced durability, and made the
retire/keep question answer itself.

**Single highest-ROI improvement for a next iteration:** host-persistent storage
plus a dead-man's-switch alert on missed cycles. Everything else in the pilot
worked; these two failures account for the outage blindness, the counter resets,
and the eventual total data loss.

**Feature that looked important but provided almost no value:** replay validation
(100 % of alert volume, 0 actionable signals), with honorable mention to
`funding`/`open_interest`/`liquidations` fields in a spot-focused 2-asset brief.

**Feature considered secondary that became unexpectedly valuable:** `spread_bps`
— included as a routine microstructure field, it produced the pilot's only novel
analytical insight (Coinbase BTC spread ~120× Binance). Second place: the boring
daily markdown snapshots, which turned out to be the system's real durability layer.
