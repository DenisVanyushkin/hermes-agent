---
name: hermes-sliced-review-delivery
description: Hermes-adapted slice delivery workflow for backlog-first, compact implementation batches with explicit review packets and stop/go checkpoints.
---

# Hermes Sliced Review Delivery

Use this skill when implementation is intentionally split into slices and the user wants each slice reviewable on its own.

## Workflow

1. Keep the slice boundary narrow.
2. State the files and acceptance criteria up front.
3. Implement only the current slice.
4. Run slice-relevant tests immediately.
5. Produce a compact review packet:
   - objective
   - files touched
   - tests run
   - open risks
   - next blocked step

## Hermes Notes

- Do not treat a reviewed backlog as approval for the next slice.
- If the slice includes material engineering changes, pair this workflow with the Hermes review gate.
- If the slice includes production mutation, the separate production approval gate still applies.

