# Production Proof of Life Report

Generated at: `2026-05-31T17:25:06Z`

## Scope and limitation

This report uses only real runtime data available in this container.

Important limitation: the live system here does **not** expose `systemctl`, and the intended market-ingestion journal path from the unit file does not exist on this runtime. The only accessible SQLite journal is the paper-trading journal at `/root/.hermes/cron/journals/paper_trading_mvp.sqlite3`. As a result, the requested market-collector proof-of-life evidence is **not present** in this environment and is marked explicitly as missing rather than invented.

---

## 1. Environment

- **Hostname:** `3c0436d83603`
- **Current UTC time:** `2026-05-31T17:25:06Z`
- **Git commit hash:** `77fc88a83b6347b44a7877af833ed11b896c5577`
- **Git branch:** `local/customizations`
- **Intended market journal path (from service/unit config):** `/var/lib/trading-autopilot/market.sqlite3`
- **Accessible journal path in this runtime:** `/root/.hermes/cron/journals/paper_trading_mvp.sqlite3`
- **Accessible journal size:** `630784` bytes
- **Report generation time:** `2026-05-31T17:25:06Z`

Notes:
- `/var/lib/trading-autopilot/market.sqlite3` does **not** exist in this runtime.
- `/var/lib/trading-autopilot/` and `/var/log/trading-autopilot/` do **not** exist in this runtime.

---

## 2. Scheduler Verification

### 2.1 systemd status

`systemctl` is **not available** in this runtime (`systemctl: command not found`). PID 1 is `docker-init`, so this container does not provide live systemd status.

That means the requested live commands cannot be executed here:

- `systemctl status trading-autopilot-market.service`
- `systemctl status trading-autopilot-market.timer`
- `systemctl list-timers | grep trading-autopilot`

### 2.2 Actual scheduler visible in this runtime

The active scheduler that is visible here is **Hermes cron** (`cronjob list`). Relevant trading-autopilot entries:

- `trading-autopilot-slack-events`
  - state: `scheduled`
  - enabled: `true`
  - schedule: `*/1 * * * *`
  - last run: `2026-05-31T19:27:56.821309+02:00`
  - last status: `ok`
- `trading-autopilot-daily-summary`
  - state: `scheduled`
  - enabled: `true`
  - schedule: `0 4 * * *`
  - last run: `2026-05-31T04:00:18.500605+02:00`
  - last status: `ok`
- `trading-autopilot-paper-trading-mvp`
  - state: `scheduled`
  - enabled: `true`
  - schedule: `12 * * * *`
  - last run: `2026-05-31T19:12:54.205551+02:00`
  - last status: `error`
- `trading-autopilot-live-read-producer`
  - state: `paused`
  - enabled: `false`
  - last run: `2026-05-28T15:05:41.701286+02:00`
  - last status: `ok`

### 2.3 Scheduler conclusion

There is **no visible scheduled market-ingestion job** in the active Hermes cron list. I did **not** find a live `trading-autopilot-market` job here.

The repo does contain systemd unit files for market ingestion:
- `deploy/systemd/trading-autopilot-market.service`
- `deploy/systemd/trading-autopilot-market.timer`

Those files define a 5-minute timer, but they are **configuration**, not live scheduler status in this runtime.

---

## 3. Collection Cadence Verification

### 3.1 Requested market sources

Requested sources:
- `binance.spot`
- `binance.futures`
- `coinbase.spot`

### 3.2 Evidence found in the accessible runtime

No events for the requested market-collector sources exist in the accessible journal.

Counts in the accessible journal:
- `market.snapshot`: `0`
- `market.state_brief`: `0`
- `market.critical_alert`: `0`
- `paper.trading.market_snapshot`: `25`

### 3.3 Cadence verdict

Because there are **no market collector observations** in the accessible runtime journal, the actual 5-minute cadence for `binance.spot`, `binance.futures`, and `coinbase.spot` **cannot be verified** from this environment.

That means I cannot honestly answer “yes, the actual cadence matches the intended 5-minute cadence” for the requested market collector pipeline.

---

## 4. Journal Population Verification

### 4.1 Accessible journal totals

- **Total events:** `440`
- **First event created_at:** `2026-05-28T12:30:26.136323Z`
- **Last event created_at:** `2026-05-29T12:28:46.603811Z`
- **Events in last hour:** `0`
- **Events in last 24 hours:** `0`
- **Events all time:** `440`

### 4.2 Event counts by type

| Event type | Count |
|---|---:|
| `paper.order.created` | 25 |
| `paper.order.fill` | 25 |
| `paper.order.snapshot` | 125 |
| `paper.order.state_changed` | 125 |
| `paper.pnl_snapshot` | 25 |
| `paper.portfolio_snapshot` | 25 |
| `paper.trading.market_snapshot` | 25 |
| `paper.trading.session_end` | 5 |
| `paper.trading.session_start` | 5 |
| `paper.trading.slack_report` | 5 |
| `risk.decision` | 25 |
| `strategy.proposal` | 25 |

### 4.3 Event counts by source module

| Source module | Count |
|---|---:|
| `paper_trading_runner` | 390 |
| `risk_engine` | 25 |
| `strategy_layer` | 25 |

### 4.4 Market-specific event counts

- `market.%` events: `0`
- `market.snapshot`: `0`
- `market.state_brief`: `0`
- `market.critical_alert`: `0`

This is the clearest evidence that the accessible runtime does **not** contain the requested live market collector output.

---

## 5. First 10 and Last 10 Events

### 5.1 First 10 journal events

| Timestamp (UTC) | Event type | Source | Symbol |
|---|---|---|---|
| `2026-05-28T12:30:26.136323Z` | `paper.trading.session_start` | `paper_trading_runner` | `None` |
| `2026-05-28T12:30:26.465602Z` | `paper.trading.market_snapshot` | `paper_trading_runner` | `BTCUSDT` |
| `2026-05-28T12:30:26.592493Z` | `strategy.proposal` | `strategy_layer` | `BTCUSDT` |
| `2026-05-28T12:30:26.663489Z` | `paper.order.created` | `paper_trading_runner` | `BTCUSDT` |
| `2026-05-28T12:30:26.672885Z` | `risk.decision` | `risk_engine` | `BTCUSDT` |
| `2026-05-28T12:30:26.692391Z` | `paper.order.state_changed` | `paper_trading_runner` | `BTCUSDT` |
| `2026-05-28T12:30:26.785995Z` | `paper.order.snapshot` | `paper_trading_runner` | `BTCUSDT` |
| `2026-05-28T12:30:26.841306Z` | `paper.order.state_changed` | `paper_trading_runner` | `BTCUSDT` |
| `2026-05-28T12:30:26.851188Z` | `paper.order.snapshot` | `paper_trading_runner` | `BTCUSDT` |
| `2026-05-28T12:30:26.856700Z` | `paper.order.state_changed` | `paper_trading_runner` | `BTCUSDT` |

### 5.2 Last 10 journal events

| Timestamp (UTC) | Event type | Source | Symbol |
|---|---|---|---|
| `2026-05-29T12:28:46.274865Z` | `paper.order.snapshot` | `paper_trading_runner` | `LINKUSDT` |
| `2026-05-29T12:28:46.335633Z` | `paper.order.fill` | `paper_trading_runner` | `LINKUSDT` |
| `2026-05-29T12:28:46.389247Z` | `paper.order.state_changed` | `paper_trading_runner` | `LINKUSDT` |
| `2026-05-29T12:28:46.415626Z` | `paper.order.snapshot` | `paper_trading_runner` | `LINKUSDT` |
| `2026-05-29T12:28:46.441354Z` | `paper.order.state_changed` | `paper_trading_runner` | `LINKUSDT` |
| `2026-05-29T12:28:46.479351Z` | `paper.order.snapshot` | `paper_trading_runner` | `LINKUSDT` |
| `2026-05-29T12:28:46.509119Z` | `paper.portfolio_snapshot` | `paper_trading_runner` | `LINKUSDT` |
| `2026-05-29T12:28:46.528916Z` | `paper.pnl_snapshot` | `paper_trading_runner` | `LINKUSDT` |
| `2026-05-29T12:28:46.579480Z` | `paper.trading.slack_report` | `paper_trading_runner` | `None` |
| `2026-05-29T12:28:46.603811Z` | `paper.trading.session_end` | `paper_trading_runner` | `None` |

---

## 6. Latest Observation Verification

Requested sources:
- Binance Spot
- Binance Futures
- Coinbase Spot

Result in this runtime:
- **Binance Spot:** no live observation found
- **Binance Futures:** no live observation found
- **Coinbase Spot:** no live observation found

What I checked:
- There are no `market.snapshot` rows in the accessible journal.
- The intended market journal path `/var/lib/trading-autopilot/market.sqlite3` does not exist here.

Because there are no source observations in the accessible runtime, I cannot provide real values for:
- `observed_at`
- `collected_at`
- `symbol`
- `price`
- `freshness`

### Source health

| Source | Health | Freshness | Error count | Last successful fetch | Last failed fetch |
|---|---|---|---:|---|---|
| `binance.spot` | `broken` | unavailable | `0` tracked here | unavailable | unavailable |
| `binance.futures` | `broken` | unavailable | `0` tracked here | unavailable | unavailable |
| `coinbase.spot` | `broken` | unavailable | `0` tracked here | unavailable | unavailable |

Failure tracking for these sources does **not** exist in the accessible runtime because there are no market-source records to evaluate.

---

## 7. End-to-End Path Verification

### Requested market collector path

I cannot demonstrate the requested path:

`Raw source payload → normalized observation → journal event → daily brief usage`

Reason: there is no live market-collector observation data in the accessible runtime.

### Closest real runtime path available

The closest real end-to-end path in this runtime is from the paper-trading pipeline, not the market-collector pipeline.

Example real event chain for session `paper-20260528T123026Z-d724ad3e`:

1. **Raw payload** (`paper.trading.market_snapshot`)
   ```json
   {
     "asset": "BTC",
     "market_price_quote": 73455.63,
     "normalized_market": {
       "anomalies": [],
       "bars": [{"close": 73455.63, "high": 73455.63, "low": 73455.63, "open": 73455.63, "volume": 0.0}],
       "normalized_symbol": "BTCUSDT",
       "normalized_tick_count": 1,
       "observed_at": "2026-05-28T12:30:26.065021Z",
       "regime": "ranging",
       "regime_reason": "net_change=0.000000; range_pct=0.000000; anomalies=0",
       "schema_version": "1.0.0",
       "source_tick_count": 1
     },
     "raw_ticker": {"price": "73455.63000000", "symbol": "BTCUSDT"},
     "schema_version": "1.0.0",
     "symbol": "BTCUSDT"
   }
   ```

2. **Journal events that followed**
   - `strategy.proposal`
   - `risk.decision`
   - `paper.order.created`
   - `paper.order.state_changed`
   - `paper.order.snapshot`
   - `paper.order.fill`
   - `paper.portfolio_snapshot`
   - `paper.pnl_snapshot`
   - `paper.trading.slack_report`
   - `paper.trading.session_end`

3. **Daily-brief / downstream usage evidence**
   The downstream summary for the same session was captured in `paper.trading.slack_report`:
   ```json
   {
     "message": "🟢 Paper trading OK · paper-20260528T123026Z-d724ad3e\n- universe: BTC, ETH, BNB, SOL, LINK / USDT\n- symbols: BTCUSDT, ETHUSDT, BNBUSDT, SOLUSDT, LINKUSDT\n- session: paper-20260528T123026Z-d724ad3e\n- journal: paper_trading_mvp.sqlite3\n- events: proposals=5 | approved=5 | rejected=0 | orders=5 | fills=5\n- portfolio: cash=61,894.44 | equity=99,977.15 | realized_pnl=0.00 | unrealized_pnl=-7.62\n- PnL: total=-22.85 | drawdown=22.85 (0.02%)\n- slack: target=C0B66CQ49SS\n- proof: order_endpoints_called=no\n- replay: consistent",
     "schema_version": "1.0.0",
     "slack_target": "C0B66CQ49SS",
     "status": "ok"
   }
   ```

This is real runtime data, but it is **not** the requested market-collector proof.

---

## 8. Storage Growth

### Accessible journal
- Total events: `440`
- Journal size: `630784` bytes
- Average bytes per event: `630784 / 440 = 1433.6 bytes/event`

### Live growth at report time
- Events in last hour: `0`
- Events in last 24 hours: `0`
- **Current live growth rate:** `0 events/day`

### Historical growth over the populated span
The accessible journal contains `440` events across roughly one day of activity (from `2026-05-28T12:30:26Z` to `2026-05-29T12:28:46Z`).

If that historical pace were extrapolated:

- **Events/day:** `440`
- **Storage/day:** `440 × 1433.6 = 630784 bytes/day`
- **Projected storage/month (30d):** `18,923,520 bytes`
- **Projected storage/year (365d):** `230,236,160 bytes`

Formula used:

- `bytes_per_event = journal_size_bytes / total_events`
- `storage_per_day = events_per_day × bytes_per_event`
- `storage_per_month = storage_per_day × 30`
- `storage_per_year = storage_per_day × 365`

---

## 9. Production Readiness Verdict

## **NO-GO**

Reasons:
1. **Scheduler proof is incomplete** in this runtime because `systemctl` is unavailable and live systemd status cannot be queried.
2. **No live market collector job is visible** in the active Hermes cron list.
3. **The intended market journal path does not exist** (`/var/lib/trading-autopilot/market.sqlite3`).
4. **No `market.snapshot` / `market.state_brief` / `market.critical_alert` events exist** in the accessible runtime journal.
5. **No latest observations are available** for Binance Spot, Binance Futures, or Coinbase Spot.
6. **The journal is not growing now**: `0` events in the last hour and `0` events in the last 24 hours.

Bottom line: this runtime does **not** provide objective proof that the market pipeline is alive, automatic, and continuously operating without human intervention.

---

## 10. Summary for the approver

- The environment contains real trading-autopilot-related history, but it is a **paper-trading journal**, not the requested live market-collector journal.
- The currently visible scheduler is **Hermes cron**, and it does **not** show a market-ingestion job.
- There is **no live market-source evidence** here for Binance Spot, Binance Futures, or Coinbase Spot.
- Therefore, the correct production verdict from this runtime is **NO-GO**.
