# Hermes Security Auditor Core

Shadow package capturing the built-in `security_auditor` role contract.

## Source Built-in Role

Built-in role ID: `security_auditor` (canonical_id: `security_auditor`)  
Profile family: `security`

## What Was Migrated

- Role purpose: review sensitive diffs, exposure, permissions, auth, secrets, public access
- Persona: paranoid but practical — distinguishes real risk from noise, not a universal blocker, gives clear pass/conditional_pass/fail
- Tool categories: `read_only_inspection`
- Boundary mode: `observe_warn`
- Routing triggers: unique shadow triggers (not the built-in security-domain keywords)

## What Was Intentionally Not Migrated

- `threat_modeling` and `exposure_review` tool declarations (built-in profile config; no package category equivalent yet)
- `secrets_read` with confirmation (built-in policy; deliberately excluded from MVP package to avoid secrets access surface)
- `security_review_hook: not_required` semantics (built-in hook wiring; not available to packages)
- Engineer-escalation overlay (built-in routing overlay rule; not in package routing)
- `critical` model tier (built-in default_model: critical; package uses `standard` as safe default)

## MVP Limitations

- Package routing is validated but **not active**; built-in `security_auditor` routing via security-domain keywords remains authoritative.
- `observe_warn` boundary mode logs would-be denials but does not block.
- The `critical` model tier is not mapped to this package in MVP.

## Why Package Routing Is Not Active

The Hermes Role Packages MVP explicitly defers routing activation to v1. Built-in security-domain routing (auth, secrets, firewall, etc. → security_auditor) continues to handle all live traffic.

## Expected Future Migration Path

1. Define a `security_review` tool category in the taxonomy when the category list matures.
2. Map `model_tier_request: critical` once package model-tier machinery supports it.
3. Activate routing and enable enforced_tools after calibration.
4. Retire built-in security_auditor profile once stable.
