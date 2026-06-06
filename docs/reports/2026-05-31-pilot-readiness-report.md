# Pilot Readiness Report

> Audit date: 2026-05-31 UTC
>
> Verdict: **NO-GO** for the 14-day pilot at this time.

## 1) Data Collection Architecture

### Intended path
Raw Source → Collector → Normalizer → Canonical Journal → Daily Brief → Alerts

### What is actually present in this repo / runtime

| Stage | Module | Entry point | How it starts | Health control |
|---|---|---|---|---|
| Raw Source | Binance live-read client | `trading_autopilot.live_read.BinanceLiveReadOnlyClient.get_ticker_price()`, `get_symbol_metadata()`, `get_account_snapshot()` | Library calls only; no installed production daemon found | `LiveReadOnlyReport.status`, `failure_reason`, `live_execution_proof.order_endpoints_called` |
| Collector | **No deployed market collector found** | N/A | No `trading-autopilot` systemd unit or cron job was found in standard locations | N/A |
| Normalizer | `trading_autopilot.normalization.normalize_market_snapshot()` | Called from `build_live_read_only_report()` and the paper-trading runner | Library call | `MarketAnomaly` flags and `NormalizationError` |
| Canonical Journal | `trading_autopilot.journal.AppendOnlyJournal` / `CanonicalJournal` | `append()`, `query()`, `replay()` | File-backed SQLite journal | Append-only event count, query/replay determinism |
| Daily Brief | `trading_autopilot.daily_market_state_brief.build_daily_market_state_brief()` | `format_daily_market_state_brief()` / `PilotRunner` | Library call | source freshness statuses, trust level |
| Alerts | `trading_autopilot.critical_alerts.build_critical_alert_report()` | `format_critical_alert_report()` / `PilotRunner` | Library call | replay consistency, alert count, alert taxonomy |

### Important observation
The **only populated production journal** I found is:

- `/root/.hermes/cron/journals/paper_trading_mvp.sqlite3`

That journal contains **paper trading events**, not Binance Spot / Binance Futures / Coinbase Spot market-observation events.

There are **zero** events from the required source names in that journal.

---

## 2) Collection Cadence Verification

### Required sources

| Source | Facts from journal | Actual cadence in production |
|---|---|---|
| Binance Spot | 0 events tagged `binance.spot` | **0 / day** (no evidence of live collection) |
| Binance Futures | 0 events tagged `binance.futures` | **0 / day** (no evidence of live collection) |
| Coinbase Spot | 0 events tagged `coinbase.spot` | **0 / day** (no evidence of live collection) |

### Where cadence is defined
- The repo contains a **job-intel market timer**: `deploy/systemd/job-intel-market.timer`
- It is scheduled at **08:20 daily**:

```ini
OnCalendar=*-*-* 08:20
```

### What process is responsible for launch
- `deploy/systemd/job-intel-market.service` → `scripts/job_intel_host_wrapper.sh market`
- However, this is **not** a proven Binance/Coinbase market-observation collector in the current journal evidence.

### What happens on source failure
- For the live-read client, failures raise `BinanceApiError` / `LiveReadOnlySessionError`.
- For the daily brief logic, missing or stale sources are marked in the brief and would become alerts.
- In the actual journal I audited, there are **no live source events**, so there is no observed recovery path to validate.

### Real collection cadence in production
**Not 5 minutes.**

Why:
1. I found **no deployed production collector** for the required Binance/Coinbase sources.
2. The only visible scheduler in repo is a **daily** job-intel market timer.
3. The populated journal is a **paper-trading** journal, not a live market-source journal.

So the real production cadence for the required sources is effectively **none**.

---

## 3) Journal Population Verification

### Evidence window
- Audit time: `2026-05-31T16:30:33Z`
- Journal path: `/root/.hermes/cron/journals/paper_trading_mvp.sqlite3`

### Counts for the last 24 hours
- **Total events:** 0
- **market.snapshot:** 0
- **market.state_brief:** 0
- **market.critical_alert:** 0
- **Binance Spot events:** 0
- **Binance Futures events:** 0
- **Coinbase Spot events:** 0

### All-time journal counts
- `paper.order.snapshot` — 125
- `paper.order.state_changed` — 125
- `paper.order.created` — 25
- `paper.order.fill` — 25
- `paper.pnl_snapshot` — 25
- `paper.portfolio_snapshot` — 25
- `paper.trading.market_snapshot` — 25
- `risk.decision` — 25
- `strategy.proposal` — 25
- `paper.trading.session_end` — 5
- `paper.trading.session_start` — 5
- `paper.trading.slack_report` — 5

### All-time by source module
- `paper_trading_runner` — 390
- `risk_engine` — 25
- `strategy_layer` — 25

### Required source strings in payloads
- `binance.spot` — 0
- `binance.futures` — 0
- `coinbase.spot` — 0

### Conclusion
The journal is populated, but **not with the required real market-source events**.

---

## 4) Expected vs Actual Volume

### Formula
Assuming the target cadence is **5 minutes** for **3 sources**:

```text
expected_observations_per_day = 3 × (24 × 60 / 5)
                              = 3 × 288
                              = 864
```

### Actual
- Actual required-source observations per day: **0**

### Coverage
```text
coverage = actual / expected × 100
         = 0 / 864 × 100
         = 0%
```

### Interpretation
Coverage is zero because the required live market sources are not producing journaled observations in production.

---

## 5) End-to-End Verification

### What I could verify with real journal data
The only real end-to-end evidence in the current journal is the **paper-trading** pipeline, not the required market-source pipeline.

#### Example: latest paper market snapshot
- Event type: `paper.trading.market_snapshot`
- Timestamp: `2026-05-29T12:28:38.286627Z`
- Payload excerpt:

```json
{
  "asset": "LINK",
  "market_price_quote": 8.925,
  "raw_ticker": {
    "price": "8.92500000",
    "symbol": "LINKUSDT"
  },
  "normalized_market": {
    "normalized_symbol": "LINKUSDT",
    "regime": "ranging",
    "source_tick_count": 1,
    "normalized_tick_count": 1,
    "anomalies": []
  }
}
```

That demonstrates:
1. raw ticker data existed
2. normalization ran
3. the result was written to the journal

#### Example: latest session end
- Event type: `paper.trading.session_end`
- Timestamp: `2026-05-29T12:28:38.286629Z`
- Status: `ok`
- Replay: `consistent`
- `order_endpoints_called: false`

### What I could **not** verify
I could **not** verify the required live-market pipeline end-to-end because the journal contains:
- no `market.snapshot` events
- no `market.state_brief` events
- no `market.critical_alert` events
- no Binance / Coinbase source-attributed observations

### Bottom line
The system can demonstrate a paper-trading cycle, but **not** the required production market-data cycle.

---

## 6) Operational Readiness

### systemd units present in repo
The repo contains job-intel units such as:
- `deploy/systemd/job-intel-market.service`
- `deploy/systemd/job-intel-market.timer`
- `deploy/systemd/job-intel-daily.service`
- `deploy/systemd/job-intel-daily.timer`
- `deploy/systemd/job-intel-alert.service`
- `deploy/systemd/job-intel-alert.timer`
- `deploy/systemd/job-intel-health.service`
- `deploy/systemd/job-intel-health.timer`

### What I found on the live filesystem
- No `trading-autopilot` unit files in:
  - `/etc/systemd/system`
  - `/lib/systemd/system`
  - `/usr/lib/systemd/system`
- No cron entries for `root` or `hermes`
- Live `systemctl` status was not available in this container environment

### Commands that would run the pilot if it were wired up
```bash
python -m trading_autopilot pilot-run --journal /root/.hermes/cron/journals/paper_trading_mvp.sqlite3 --lookback-hours 24 --recent-limit 5
python -m trading_autopilot pilot-status --journal /root/.hermes/cron/journals/paper_trading_mvp.sqlite3 --lookback-hours 24 --recent-limit 5
python -m trading_autopilot pilot-loop --journal /root/.hermes/cron/journals/paper_trading_mvp.sqlite3 --lookback-hours 24 --recent-limit 5 --interval-seconds 86400
```

### Last successful execution log available
From the journal:
- `paper.trading.session_end` at `2026-05-29T12:28:38.286629Z`
- status: `ok`
- failure reason: `None`

### Last unsuccessful execution log available
- **None found**
- All five `paper.trading.session_end` records are `ok`

---

## 7) Pilot Go / No-Go Assessment

### GO criteria
- data collected automatically
- journal populated with real market-source data
- replay works
- daily brief generated
- cadence matches expectations

### NO-GO criteria
- data not collected automatically
- ingestion exists only as a library or test path
- journal not populated with real market-source data
- only test/paper data exists
- no production scheduler for the required sources

### Verdict
**NO-GO**

### Why
1. The required Binance Spot / Binance Futures / Coinbase Spot data is **not present** in the production journal.
2. The only populated journal is **paper trading**, not live market collection.
3. There is **no evidence of an active production scheduler** collecting the required sources at 5-minute cadence.
4. The daily brief and critical-alert paths are implemented, but they are **not being exercised by real market-source data**.

### Decision
Do **not** start the 14-day pilot yet.
First, wire the real source collection path into production and prove that the journal fills with the required live market observations.
