# Market Ingestion Validation Report

- **Journal path:** `/tmp/trading_autopilot_market_validation_cadence.sqlite3`
- **Collection cadence:** 5 minutes (`deploy/systemd/trading-autopilot-market.timer` uses `OnCalendar=*:0/5`)
- **Validation run:** 2 real collection cycles spaced 5 minutes apart
- **Events collected in last 24h:** 12
- **Counts by source:** `binance.spot=4`, `binance.futures=4`, `coinbase.spot=4`
- **Counts by event type:** `market.snapshot=12`
- **First collected observation:** `2026-05-31T17:02:03.817000Z`
- **Latest collected observation:** `2026-05-31T17:07:07.573870Z`
- **Source freshness:** all three sources `fresh` with freshness basis `observed_at`
- **Assets observed:** BTC and ETH
- **Storage growth estimate:** 12 real observations produced a 45,056-byte SQLite journal, or about 3.7 KiB per observation; projected daily growth at 5-minute cadence is about 270 KiB/day (72 observations/day)

## Notes
- Data was collected from the free public endpoints for Binance Spot, Binance Futures, and Coinbase Spot.
- The journal contains only real market observations and market.snapshot events for BTC and ETH.
- No paid market-data dependency was used.
