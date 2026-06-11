# Hermes Scribe Core

Shadow package capturing the built-in `scribe` role contract.

## Source Built-in Role

Built-in role ID: `scribe` (canonical_id: `scribe`)  
Profile family: `documentation`

## What Was Migrated

- Role purpose: durable memory, decisions, handoffs, state, open questions
- Persona: precise archivist — concise, factual, future-reader oriented
- Tool categories: `read_only_inspection`, `repo_edit`
- Boundary mode: `observe_warn`
- Routing triggers: unique shadow triggers (not the built-in docs-domain keywords)

## What Was Intentionally Not Migrated

- `docs_write` as an explicit tool category (mapped to `repo_edit` as the closest package analogue)
- Scribe-hook mechanics (built-in only; no package equivalent in MVP)
- `may_write_paths` enforcement (built-in profile config; not in package manifest schema)
- Approval gates for deleting/overwriting canonical docs (built-in policy; not enforced in MVP packages)
- Output-style injection (rendered by `profile_context.py`; not available to packages in MVP)

## MVP Limitations

- Package routing is validated but **not active**; built-in `scribe` routing via docs-domain keywords remains authoritative.
- `observe_warn` boundary mode logs what would be blocked but does not block.
- Tool enforcement is not active in MVP.

## Why Package Routing Is Not Active

The Hermes Role Packages MVP explicitly defers routing activation to v1. Built-in domain-keyword routing (docs → scribe) continues to handle all live traffic.

## Expected Future Migration Path

1. observe_warn calibration: run with logging to surface false-positive tool denials.
2. v1 routing activation: promote `hermes_scribe_core` triggers into the live routing layer alongside built-ins.
3. Enforced-tools gate: enable `enforced_tools` after calibration period shows near-zero false positives.
4. Built-in retirement: once package routing is stable, the built-in scribe profile can be soft-deprecated.
