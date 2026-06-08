# docs/state

This directory holds the current durable state snapshots for Hermes profile-architecture work.

PR-1 only bootstraps the directory so future Scribe handoffs have a canonical place to land.

Rules:
- keep it evidence-backed;
- do not stash secrets here;
- do not treat it as runtime state for routing or execution.
