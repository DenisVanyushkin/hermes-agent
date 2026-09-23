"""No-agent cron entry point for owner workbook imports."""

from job_intel_shortlist_tick import main


if __name__ == "__main__":
    raise SystemExit(main("poll"))
