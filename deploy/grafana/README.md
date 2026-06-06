# Trading Autopilot Grafana dashboard

This directory contains Grafana dashboard JSON for the paper-trading / autopilot
metrics pipeline and the Job Intel observability stack.

## Data sources

The dashboards expect a Prometheus data source named `Prometheus`.

## Dashboards

- `trading-autopilot-dashboard.json` — paper-trading / autopilot metrics
- `job-intel-executive-intelligence-overview.json` — discovery funnel and opportunity quality
- `job-intel-source-effectiveness.json` — source conversion and executive density
- `job-intel-rejection-analytics.json` — rejection reasons and near-miss analysis
- `job-intel-company-intelligence.json` — company-level intelligence trend panels
- `job-intel-system-health.json` — source health, auth walls, anti-bot, and extraction failures

## Metrics exporters

- Trading Autopilot exporter: `trading_autopilot/prometheus.py`
- Job Intel exporter: `job_intel/observability.py`

## Trading Autopilot exporter

```bash
# print one snapshot to stdout
python -m trading_autopilot.prometheus --journal-path /path/to/paper_trading_mvp.sqlite3

# serve /metrics over HTTP for Prometheus to scrape
python -m trading_autopilot.prometheus --journal-path /path/to/paper_trading_mvp.sqlite3 --serve --host 0.0.0.0 --port 9898
```

## Job Intel exporter

```bash
# print metrics to stdout
python -m job_intel metrics-exporter --host 0.0.0.0 --port 9899

# or run it as a host-managed systemd service
systemctl enable --now job-intel-metrics-exporter.service
```

The Job Intel exporter derives all metrics from the SQLite `job_intel` store and
is intended to back the Grafana dashboards above.
