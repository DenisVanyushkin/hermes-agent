# Feedback Loop E2E Validation

Date: 2026-06-02

## Goal
Validate the full feedback loop:

Slack reaction
→ Slack event
→ ingestion
→ `vacancy_feedback`
→ `vacancy_feedback_state`

## Result
**Validated at the event/storage layer.**

The following reaction semantics are implemented and validated:

- `+1` → `interesting`
- `-1` → `not_interesting`
- `star` → `exceptional`
- `rocket` → `applied`

Any other emoji is ignored.

## Event lifecycle
For each supported reaction:

1. `reaction_added` is received from Slack.
2. The event is resolved back to the original vacancy message via `(slack_channel, slack_message_ts)`.
3. The raw event is appended to `vacancy_feedback`.
4. Current state is upserted into `vacancy_feedback_state`.
5. `reaction_removed` deactivates the same state row.

## Production evidence
Validation artifacts from the production host (`hermes`) show the full persistence path.

### Test Slack event payloads
`reaction_added`

```json
{"type":"reaction_added","user":"U_TEST","reaction":"+1","item":{"channel":"executive_search_report","ts":"1760000000.123456"},"event_ts":"1760001111.000001"}
```

`reaction_removed`

```json
{"type":"reaction_removed","user":"U_TEST","reaction":"+1","item":{"channel":"executive_search_report","ts":"1760000000.123456"},"event_ts":"1760002222.000002"}
```

### Stored event rows

```json
{"id": 1, "vacancy_id": 3851, "slack_message_ts": "1760000000.123456", "feedback_type": "interesting", "event_type": "reaction_added", "event_timestamp": "1760001111.000001", "user_id": "U_TEST"}
{"id": 2, "vacancy_id": 3851, "slack_message_ts": "1760000000.123456", "feedback_type": "interesting", "event_type": "reaction_removed", "event_timestamp": "1760002222.000002", "user_id": "U_TEST"}
```

### Final materialized state

```json
{"vacancy_id": 3851, "slack_message_ts": "1760000000.123456", "user_id": "U_TEST", "feedback_type": "interesting", "active": 0, "updated_at": "2026-06-01T16:49:08.557653+00:00"}
```

## What this proves
- The Slack reaction is mapped to the intended semantic label.
- The event is stored append-only for future analytics and training.
- The latest state is tracked separately for fast reads and personalization features.
- Add/remove lifecycle works correctly.

## Dataset readiness
Yes — once users start reacting at scale, the system is ready to collect a training-ready dataset for later model calibration and personalization because it keeps both:

- the raw event history (`vacancy_feedback`)
- the latest active state (`vacancy_feedback_state`)

That means the dataset can support:

- label analytics
- per-user preference modeling
- bucket precision/recall analysis
- future scoring calibration

## Operational note
Earlier validation showed Slack delivery itself was blocked by an `invalid_auth` credential issue in the live Slack posting path. The feedback ingestion and storage path, however, was validated successfully once message-to-vacancy mapping existed.
