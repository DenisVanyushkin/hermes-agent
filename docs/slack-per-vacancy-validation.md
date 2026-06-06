# Slack Per Vacancy Validation

Date: 2026-06-02

## Goal
Validate that surfaced vacancies are delivered as individual Slack messages and that each delivered vacancy is mapped to a stable Slack identity in production storage.

## Result
**Validated.**

Current delivery behavior for surfaced vacancies is:

- 1 daily digest message for executive review
- 1 Slack message per surfaced vacancy

For the validation run, surfaced vacancies produced individual messages for:

- `strong_fit`
- `potential_fit`
- `near_miss`

Rejected vacancies were not delivered as vacancy messages.

## Delivery format
Each vacancy notification includes the fields required for decision-making and feedback:

- company
- title
- location
- source
- recommendation
- score
- URL

Example message body:

```text
*Acme* — Head of Product
Location: Remote | Source: linkedin | Score: 95 (strong_fit) | Recommendation: strong_fit
Why matched: core product title
URL: https://example.com/li-1
```

## Production evidence
Validation artifacts from the production host (`hermes`) show the daily pipeline was exercised end-to-end at the delivery layer.

### Daily review summary
- Found: 4
- Strong fit: 1
- Potential fit: 1
- Near miss: 1
- Rejected: 1

### Slack delivery count observed in validation
- 1 digest message
- 3 vacancy messages

### Message-to-vacancy mapping evidence
Stored mapping rows are written to `vacancy_slack_messages` with:

- `vacancy_id`
- `run_id`
- `slack_channel`
- `slack_message_ts`
- `company`
- `title`
- `score`
- `recommendation`
- `url`
- `created_at`

Example validated row:

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

## Storage / mapping behavior
The mapping table is immutable delivery metadata and is used to resolve Slack reactions back to a specific vacancy message.

Validated constraints and indexes:

- `UNIQUE(slack_channel, slack_message_ts)`
- `INDEX(vacancy_id)`
- `INDEX(run_id)`

## Conclusion
Phase 1 delivery now supports per-vacancy Slack messages for surfaced vacancies, while preserving the daily digest for executive review. The delivery mapping is stored and ready for reaction-driven feedback ingestion.
