# Controlled Execution Report DB Persistence

## Overview

Controlled execution reports are now persisted into both JSON files **and** the
Hermes SQLite state database (`state.db`), keyed by `report_run_id`. The DB is
the canonical lookup source; JSON files are the fallback.

## Where Reports Are Stored

| Store | Location | Key |
|-------|----------|-----|
| **DB (canonical)** | `~/.hermes/state.db` → `controlled_execution_reports` table | `report_run_id` |
| **Durable JSON** | `~/.hermes/controlled-runs/<report_run_id>/controlled_execution_report.json` | filesystem path |
| **Workspace JSON** | `<workspace>/controlled_execution_report.json` | filesystem path |

## How to Retrieve by report_run_id

### CLI

```bash
# Summary (default view)
hermes controlled-report get <report_run_id>

# Full sanitized JSON
hermes controlled-report get <report_run_id> --json

# File paths only
hermes controlled-report get <report_run_id> --path

# List recent reports
hermes controlled-report list

# List with limit
hermes controlled-report list --limit 5

# List as JSON
hermes controlled-report list --json
```

### Programmatic (Python)

```python
from hermes_state import SessionDB

db = SessionDB()
report = db.get_controlled_execution_report("your-report-run-id")
# Returns dict with summary fields + report_json, or None if not found

reports = db.list_controlled_execution_reports(limit=10)
# Returns list of recent reports, newest first
```

## DB Schema

```sql
CREATE TABLE IF NOT EXISTS controlled_execution_reports (
    report_run_id TEXT PRIMARY KEY,
    pipeline_session_id TEXT,
    trace_id TEXT,
    status TEXT,
    pipeline_id TEXT,
    execution_mode TEXT,
    final_verdict TEXT,
    controller_executed INTEGER,
    report_execution_invoked INTEGER,
    reviewer_invoked INTEGER,
    changed_files_json TEXT,
    models_used_json TEXT,
    providers_used_json TEXT,
    tests_status TEXT,
    tests_summary TEXT,
    workspace_path TEXT,
    durable_report_path TEXT,
    workspace_report_path TEXT,
    report_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
```

## Failure Behavior

- **JSON persistence is primary.** If JSON write fails, it's logged as WARNING.
- **DB persistence is best-effort.** If DB write fails, it's logged as WARNING
  and the pipeline execution continues normally. The `db_persisted` field in the
  metadata dict indicates whether DB persistence succeeded.
- **No secrets in summary columns.** The summary columns (`status`, `pipeline_id`,
  etc.) contain only sanitized metadata. The full report JSON (which is the same
  already-sanitized payload written to the JSON file) is stored in `report_json`.

## Design Notes

- Uses SQLite's `ON CONFLICT DO UPDATE` (upsert) for idempotency — same
  `report_run_id` always produces exactly one row with the latest payload.
- Added to the existing `state.db` following the declarative reconciliation
  pattern: `SCHEMA_SQL` is the source of truth, `_reconcile_columns()` handles
  column additions on existing databases.
- Schema version bumped from 15 to 16.
- DB write failure is non-fatal: JSON files remain the fallback.
