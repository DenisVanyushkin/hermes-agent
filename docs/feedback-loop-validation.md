# Feedback Loop Validation

Date: 2026-06-01
Validation run context: post-implementation check on production host (`hermes` user).

## Scope
Implemented and validated:
- Slack feedback event ingest (`reaction_added`, `reaction_removed`).
- Event persistence in `vacancy_feedback`.
- Materialized state in `vacancy_feedback_state`.
- Vacancy message mapping storage in `vacancy_slack_messages`.

Not changed:
- acquisition
- registry
- scoring
- recommendation logic

## Evidence 1 — Slack delivery path
Command:
```bash
python -m job_intel send-test --channel executive_search_report
```
Observed output (runtime user `hermes`):
```text
failed: Slack API error: invalid_auth
```

Interpretation:
- Delivery code path is executed.
- Current production token is invalid; real Slack posting is blocked until credential fix.

## Evidence 2 — Reaction event payloads
Used payloads:

`/tmp/evt_add.json`
```json
{"type":"reaction_added","user":"U_TEST","reaction":"+1","item":{"channel":"executive_search_report","ts":"1760000000.123456"},"event_ts":"1760001111.000001"}
```

`/tmp/evt_remove.json`
```json
{"type":"reaction_removed","user":"U_TEST","reaction":"+1","item":{"channel":"executive_search_report","ts":"1760000000.123456"},"event_ts":"1760002222.000002"}
```

CLI responses:
```json
{"status": "ok", "vacancy_id": 3851, "feedback_type": "interesting", "event_type": "reaction_added", "channel": "executive_search_report", "slack_message_ts": "1760000000.123456", "user_id": "U_TEST"}
{"status": "ok", "vacancy_id": 3851, "feedback_type": "interesting", "event_type": "reaction_removed", "channel": "executive_search_report", "slack_message_ts": "1760000000.123456", "user_id": "U_TEST"}
```

## Evidence 3 — Message mapping row (`vacancy_slack_messages`)
Mapped test row used for E2E:
```json
{
  "vacancy_id": 3851,
  "run_id": 277,
  "slack_channel": "executive_search_report",
  "slack_message_ts": "1760000000.123456",
  "company": "Adyen",
  "title": "Head of Product",
  "score": 88,
  "recommendation": "strong_fit",
  "url": "https://www.linkedin.com/jobs/view/123"
}
```

## Evidence 4 — Event rows (`vacancy_feedback`)
Stored rows:
```json
{"id": 1, "vacancy_id": 3851, "slack_message_ts": "1760000000.123456", "feedback_type": "interesting", "event_type": "reaction_added", "event_timestamp": "1760001111.000001", "user_id": "U_TEST"}
{"id": 2, "vacancy_id": 3851, "slack_message_ts": "1760000000.123456", "feedback_type": "interesting", "event_type": "reaction_removed", "event_timestamp": "1760002222.000002", "user_id": "U_TEST"}
```

## Evidence 5 — Final state (`vacancy_feedback_state`)
Final materialized state after add+remove:
```json
{"vacancy_id": 3851, "slack_message_ts": "1760000000.123456", "user_id": "U_TEST", "feedback_type": "interesting", "active": 0, "updated_at": "2026-06-01T16:49:08.557653+00:00"}
```

Interpretation:
- `reaction_added` created active state.
- `reaction_removed` deactivated the same feedback state (`active=0`).
- Lifecycle behavior is correct.

## Implemented schema
Added tables:
- `vacancy_slack_messages`
- `vacancy_feedback`
- `vacancy_feedback_state`

Extended table:
- `production_observation_daily`
  - `vacancies_sent`
  - `vacancies_reacted`
  - `reaction_rate`
  - `positive_rate`
  - `applied_rate`

## Status
- Feedback loop data path is implemented and validated end-to-end at DB/event level.
- Slack delivery validation is currently blocked by `invalid_auth` and requires credential fix for full live proof.
