# Product Search Pre-Pilot Baseline

This directory records aggregate denominators for the Product Search pilot. It contains no Slack
message bodies, credentials, application artifacts, Candidate Facts text, or user notes.

## Evidence boundaries

- Slack category totals come from the read-only 45-day audit preserved in the execution plan at
  canonical commit `bbf2ae0f4885dcbd4be5a08692d6d757372fd0d8`. Its exact record hash is in
  `baseline-summary.json`.
- The live database was reopened with SQLite URI `mode=ro`. The extractor reads only aggregate
  `message_type`, delivery status, and opaque card-key counts. It hashes the database snapshot but
  does not publish its absolute host path.
- The later live database replay returned 103 rather than the planning audit's 104 DB-accounted
  vacancy cards. Both observations are retained. The older denominator is not silently rewritten.
- Slack identity came from `auth.test`, scope response headers, and `conversations.info`. The probe
  emitted IDs, scopes, conversation type, and membership only; it never emitted credentials.
- Unit/timer state came from read-only `systemctl list-unit-files`, `list-timers`, and `show` calls.
  Credential flags record only empty/present state.

## Reproduction

The pure calculations and privacy gate are covered by `tests/product_search/test_baseline.py`.
For a database snapshot copy, call:

```python
from job_intel.product_search.baseline import extract_read_only_database_baseline

result = extract_read_only_database_baseline(
    snapshot_path,
    since="<inclusive ISO timestamp>",
    until="<exclusive ISO timestamp>",
)
```

Reproducing the Slack totals requires the same bounded window and channel-history permission. The
collector may classify content in memory, but only aggregate category counts, root/reply totals,
identity metadata, and evidence hashes may be retained.

## Attention comparability

Historical attention is `not_computable`: there is no compatible record of completed review
sessions with measured duration. Zero is not imputed. A prospective baseline begins with the Gate C
attention prototype and must retain one measurement definition for the pilot.

## Outbound topology

The generic live adapter, standalone sender, optional webhook branch, raw Web API calls, and legacy
Job Intel callers are separate paths. There is no typed Product Search sender at this baseline. The
same Hermes Slack app receives reactions, thread replies, and registered actions over Socket Mode.
