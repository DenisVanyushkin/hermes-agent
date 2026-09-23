"""No-agent cron entry point for frozen discrepancy reports."""

from job_intel_shortlist_tick import main


if __name__ == "__main__":
    raise SystemExit(main("report"))
