# 14-Day Pilot Launch Runbook

> Observer-only MVP. No strategy, prediction, portfolio, execution, or trading logic.

## 1) Current MVP status

### Implemented components
- **Canonical Journal** — append-only SQLite-backed event store.
- **Binance/Coinbase ingestion** — normalized market observations for:
  - `binance.spot`
  - `binance.futures`
  - `coinbase.spot`
- **Daily Market State Brief** — deterministic daily observer brief.
- **Critical Alerts** — operator-actionable alerts only.
- **Replay Validation** — checks journal replay consistency.
- **Pilot Operation** — read-only status reporting and unattended loop support.
- **CLI entrypoint** — `python -m trading_autopilot ...` dispatches pilot commands.

### Observer-only boundary
- Reads market data and journal state only.
- Writes only append-only journal events.
- Does **not** place orders.
- Does **not** maintain a live portfolio.
- Does **not** generate predictions or regime scores.
- Does **not** add new datasets.

### Known limitations
- Brief coverage is intentionally narrow and compact.
- Daily brief focuses on BTC/ETH only.
- Alerts are intentionally sparse; normal days should produce zero alerts.
- Replay validation depends on journal integrity and deterministic payloads.
- No external paid market-data dependency is required for the MVP.

---

## 2) Launch commands

### Common environment
Use the host journal path and keep the pilot read-only:

```bash
export JOB_INTEL_DB_PATH=/var/lib/job-intel/state/job_intel.sqlite3
export JOB_INTEL_WORKDIR=/home/hermes/.hermes/hermes-agent
export JOB_INTEL_ENVIRONMENT=production
export JOB_INTEL_SCRIPTS_DIR=/home/hermes/.hermes/hermes-agent/scripts
# Optional convention if a wrapper mirrors summaries to Slack:
export SLACK_TARGET_CHANNEL=C0B3JFDM6NB
```

### One-shot pilot run
Runs one observer-only cycle and appends the brief / alerts to the journal:

```bash
python -m trading_autopilot pilot-run \
  --journal "$JOB_INTEL_DB_PATH" \
  --lookback-hours 24 \
  --recent-limit 5
```

### Pilot status
Reads the latest operational snapshot without changing state:

```bash
python -m trading_autopilot pilot-status \
  --journal "$JOB_INTEL_DB_PATH" \
  --lookback-hours 24 \
  --recent-limit 5
```

### Pilot loop
Runs continuously until interrupted. Use only if you want a long-lived process instead of a timer:

```bash
python -m trading_autopilot pilot-loop \
  --journal "$JOB_INTEL_DB_PATH" \
  --lookback-hours 24 \
  --recent-limit 5 \
  --interval-seconds 86400
```

### Test run
Pilot-specific smoke test:

```bash
python -m pytest -o addopts='' tests/trading_autopilot/test_pilot_operation.py -q
```

Recommended full MVP test sweep before day 1:

```bash
python -m pytest -o addopts='' tests/trading_autopilot -q
```

### Replay validation
Run the replay validation test module directly:

```bash
python -m pytest -o addopts='' tests/trading_autopilot/test_replay_validation.py -q
```

---

## 3) Runtime configuration

### Journal path
Primary journal path for the pilot:

- `/var/lib/job-intel/state/job_intel.sqlite3`

This matches the host runtime template used by the job-intel deployment.

### Environment variables
Required or relevant for the pilot launch:

- `JOB_INTEL_DB_PATH` — pilot journal SQLite file.
- `JOB_INTEL_WORKDIR` — repository/workdir used by host wrappers.
- `JOB_INTEL_ENVIRONMENT` — should stay `production` for the launch window.
- `JOB_INTEL_SCRIPTS_DIR` — host wrapper script directory.
- `JOB_INTEL_SERVICE_USER` — service account, typically `hermes`.
- `JOB_INTEL_SERVICE_GROUP` — service group, typically `hermes`.
- `JOB_INTEL_SLACK_WEBHOOK_URL` — optional external notification hook; unset to disable Slack delivery.
- `SLACK_TARGET_CHANNEL` — operator convention for mirrored summaries; use `C0B3JFDM6NB` if you forward a copy there.

### Slack target channel
- **Preferred Slack target:** `C0B3JFDM6NB`
- The pilot itself does not require Slack.
- If a wrapper mirrors summaries, keep the message compact and observer-only.

### Source list
MVP sources only:
- Binance Spot
- Binance Futures
- Coinbase Spot

### Cadence
- **Default launch cadence:** once per day
- **Pilot loop cadence:** `86400` seconds if using the loop mode
- **Freshness checks:** based on `observed_at`

### Retention assumptions
- Keep the pilot journal intact for the full 14-day window.
- Do not truncate or vacuum away pilot events during the run.
- Keep at least one backup copy before any manual cleanup.
- Retain the journal at least 30 days beyond the pilot unless storage policy says otherwise.

---

## 4) Systemd or scheduler setup

### Recommended setup: systemd oneshot service + timer
Use a timer for the daily pilot run; keep the service read-only and short-lived.

#### Service: `trading-autopilot-pilot.service`

```ini
[Unit]
Description=Trading Autopilot pilot run
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=hermes
Group=hermes
EnvironmentFile=/etc/job-intel/job-intel.env
Environment=JOB_INTEL_RUNTIME_STRICT=1
WorkingDirectory=/home/hermes/.hermes/hermes-agent
ExecStart=/usr/bin/env bash -lc 'exec /home/hermes/.hermes/hermes-agent/venv/bin/python -m trading_autopilot pilot-run --journal "$JOB_INTEL_DB_PATH" --lookback-hours 24 --recent-limit 5'
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=/var/lib/job-intel /var/log/job-intel /etc/job-intel
UMask=0077
```

#### Timer: `trading-autopilot-pilot.timer`

```ini
[Unit]
Description=Schedule trading-autopilot pilot

[Timer]
OnCalendar=*-*-* 09:00:00
Persistent=true
Unit=trading-autopilot-pilot.service

[Install]
WantedBy=timers.target
```

### Alternative: cron
If you prefer cron, run the one-shot command once per day and redirect output to a log file.

Example:

```cron
0 9 * * * JOB_INTEL_DB_PATH=/var/lib/job-intel/state/job_intel.sqlite3 JOB_INTEL_WORKDIR=/home/hermes/.hermes/hermes-agent JOB_INTEL_ENVIRONMENT=production /home/hermes/.hermes/hermes-agent/venv/bin/python -m trading_autopilot pilot-run --journal /var/lib/job-intel/state/job_intel.sqlite3 --lookback-hours 24 --recent-limit 5 >> /var/log/job-intel/trading-autopilot-pilot.log 2>&1
```

### Restart behavior
- **Timer + oneshot service:** no restart loop; the timer schedules the next run.
- **Pilot loop service:** if you run `pilot-loop` under systemd, use `Restart=on-failure` and a short `RestartSec`.
- `Persistent=true` ensures missed timer runs fire after reboot.

### Logging location
- Primary: `journalctl -u trading-autopilot-pilot.service --no-pager`
- Optional file log if using cron or an external wrapper: `/var/log/job-intel/trading-autopilot-pilot.log`

### Failure behavior
- A non-zero exit from the pilot run should mark the service failed.
- The next timer tick should still occur.
- If the loop service is used, repeated failures should remain visible in systemd status and logs.
- On failure, investigate the journal before restarting.

---

## 5) Daily operator checklist

Each day, confirm:

1. **Was a brief generated?**
   - `pilot-status` should show `report_generation_status=ok`.
2. **Was replay consistent?**
   - `pilot-status` should show `replay_validation_status=consistent`.
3. **Were sources fresh?**
   - Check the `Source freshness` line for stale or missing sources.
4. **Were alerts generated?**
   - Normal day: zero alerts.
   - Any alert should be operator-actionable.
5. **Was the report useful?**
   - In under 30 seconds, can you tell whether the market state is stable, which source is degraded, and whether anything needs attention?

Suggested command:

```bash
python -m trading_autopilot pilot-status \
  --journal /var/lib/job-intel/state/job_intel.sqlite3 \
  --lookback-hours 24 \
  --recent-limit 5
```

---

## 6) 14-day evaluation criteria

At the end of the pilot, review:

- **Successful daily briefs**
  - Target: 14/14
- **Failed runs**
  - Target: 0
- **Stale-source incidents**
  - Target: as close to 0 as possible
  - Investigate any repeated source outage
- **Replay mismatches**
  - Target: 0
- **Alerts generated**
  - Target: low volume, only actionable issues
- **False alert rate**
  - Target: 0 or near 0
  - Any non-actionable alert counts as a false alert
- **Brief usefulness**
  - Target: the daily brief should reliably answer, in one glance, whether the sources are healthy and whether the day needs attention

Decision rule:
- If the brief helps you make a yes/no operational decision most days, the pilot is doing useful work.
- If you still need the raw journal to understand the day, the brief is too weak.

---

## 7) Stop / rollback procedure

### Stop the loop or timer

If using systemd timer:

```bash
systemctl stop trading-autopilot-pilot.timer
systemctl disable trading-autopilot-pilot.timer
systemctl stop trading-autopilot-pilot.service
```

If using a loop service:

```bash
systemctl stop trading-autopilot-pilot.service
```

If run manually in a shell, interrupt it with `Ctrl-C`.

### Disable Slack notifications
The pilot does not require Slack. If you have an external wrapper that mirrors summaries:
- remove or blank `JOB_INTEL_SLACK_WEBHOOK_URL`
- stop any Slack-forwarding wrapper or cron job
- do not mirror to `C0B3JFDM6NB`

### Archive the journal
Create a timestamped SQLite backup before changing anything:

```bash
python - <<'PY'
import datetime
import pathlib
import sqlite3

src = pathlib.Path('/var/lib/job-intel/state/job_intel.sqlite3')
dst_dir = pathlib.Path('/var/lib/job-intel/archive')
dst_dir.mkdir(parents=True, exist_ok=True)
dst = dst_dir / f"job_intel-{datetime.datetime.now(datetime.timezone.utc):%Y%m%dT%H%M%SZ}.sqlite3"
with sqlite3.connect(src) as source, sqlite3.connect(dst) as target:
    source.backup(target)
print(dst)
PY
```

Optional integrity check:

```bash
sha256sum /var/lib/job-intel/archive/job_intel-*.sqlite3
```

### Restore from backup
If needed, stop the service first, then restore a known-good archive:

```bash
systemctl stop trading-autopilot-pilot.timer trading-autopilot-pilot.service
cp /var/lib/job-intel/archive/job_intel-YYYYMMDDTHHMMSSZ.sqlite3 /var/lib/job-intel/state/job_intel.sqlite3
systemctl start trading-autopilot-pilot.service
```

Do not restore over a live writer.

---

## 8) Final pilot decision framework

After 14 days, classify the MVP as one of:

### Keep frozen
Choose this if:
- briefs are generated consistently
- replay validation stays clean
- alerts remain low-noise
- the brief is useful enough to stay in daily use
- the observer-only boundary is intact

### Simplify
Choose this if:
- the pilot works, but some output is redundant or noisy
- you can reduce fields, lines, or alert categories without losing value
- the system is useful but too heavy for daily operator use

### Expand carefully
Choose this if:
- the pilot is stable and useful
- source health is good
- you have a specific, bounded next requirement
- expansion does not violate observer-only discipline

### Shut down
Choose this if:
- briefs are not useful
- replay validation keeps failing
- alerts are noisy or misleading
- the system cannot remain observer-only
- maintenance cost outweighs operational value

### Default recommendation
Start from **Keep frozen** unless the 14-day evidence clearly supports simplification or a tightly scoped expansion.
