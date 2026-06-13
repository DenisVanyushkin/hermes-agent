# Review Gate Live Enablement

## Current State

Implementation is present in code and tests.

Default intended runtime posture:

```yaml
review_gate:
  mode: observe
  reviewer_tier: code_review
```

This task does **not** enable live `enforce`.

## Enable `enforce`

1. Update live `~/.hermes/config.yaml`:

```yaml
review_gate:
  mode: enforce
  reviewer_tier: code_review
```

2. Verify the config shape before restart.

3. Restart the relevant Hermes gateway process during an approved maintenance window.

4. Run a controlled engineer repo-change task and confirm:

- the final completion is blocked
- the response names the reviewer tier/model
- `observe`-style warnings are replaced by a real block

5. Approve or waive the review through the chosen operator workflow before considering broader rollout complete.

## Rollback

Fast rollback:

```yaml
review_gate:
  mode: observe
  reviewer_tier: code_review
```

or

```yaml
review_gate:
  mode: disabled
  reviewer_tier: code_review
```

Then restart the gateway.

## Failure Modes

- If review-gate classification fails, current implementation logs a warning and does not hard-crash the turn.
- `observe` is the safe rollout mode because it preserves production behavior while surfacing review-required events.
- Review approval is intentionally not equivalent to production-mutation approval.

