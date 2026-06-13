---
name: hermes-review-gate-enforcement
description: Hermes-specific workflow for material engineering changes that must stop at a code-review gate before final completion when review_gate=enforce.
---

# Hermes Review Gate Enforcement

Use this skill when a Hermes engineer task changes repo/runtime code or configuration in a way that counts as a material engineering change.

## Intent

- keep read-only investigation and planning unblocked
- require a review packet for material engineering changes
- separate code review from production approval

## Rules

1. Classify the task first:
   - read-only inspection: no review gate
   - planning only: no review gate
   - material engineering change: review gate candidate
2. Run relevant tests before claiming completion.
3. Produce a review packet:
   - changed paths
   - tests run
   - risks or residual concerns
4. If `review_gate.mode=observe`, continue but state that review would be required.
5. If `review_gate.mode=enforce`, do not present the work as finally done until review is approved or explicitly waived.

## Important Separation

- Code review approval does not grant deploy permission.
- Code review approval does not grant restart permission.
- Code review approval does not grant secrets access.
- Production mutation approval remains a separate gate.

