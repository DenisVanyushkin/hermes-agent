# Hermes Engineer Core

Shadow package capturing the built-in `engineer` role contract.

## Source Built-in Role

Built-in role ID: `engineer` (canonical_id: `engineer`)  
Profile family: `engineering`

## What Was Migrated

- Role purpose: code, tests, repo config, debugging, runtime diagnostics, engineering fixes
- Persona: pragmatic senior engineer / SRE — small diffs, tests where relevant, no silent production mutation
- Tool categories: `read_only_inspection`, `repo_edit`
- Boundary mode: `observe_warn`
- Routing triggers: unique shadow triggers (not the built-in infra-domain keywords)

## What Was Intentionally Not Migrated

- `shell_after_approval` and `deploy_execution_after_approval` (built-in approval-gated tools; not mapped to package categories in MVP)
- `production_deploy` / `service_restart` confirmation requirements (built-in profile policy)
- `may_write_paths` for code paths (built-in profile path enforcement)
- Security-auditor and scribe escalation overlay rules (built-in routing overlay; not in package routing)
- Approval gates for production mutations (built-in policy; not enforced in MVP packages)

## MVP Limitations

- Package routing is validated but **not active**; built-in `engineer` routing via infra-domain keywords remains authoritative.
- `observe_warn` boundary mode logs would-be denials but does not block.
- `repo_edit` category is advisory only in MVP.

## Why Package Routing Is Not Active

The Hermes Role Packages MVP explicitly defers routing activation to v1. Built-in infra-domain routing (deploy, docker, systemd, etc. → engineer) continues to handle all live traffic.

## Expected Future Migration Path

1. Add `shell_general` category to calibrate observe_warn logging against real engineer tasks.
2. Activate `hermes_engineer_core` routing triggers in v1.
3. After calibration, enable enforced_tools with the approved category set.
4. Retire built-in engineer profile once package routing is proven stable.
