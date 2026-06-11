# Hermes Researcher Core

Shadow package capturing the built-in `researcher` role contract.

## Source Built-in Role

Built-in role ID: `researcher` (canonical_id: `researcher`)  
Profile family: `research`

## What Was Migrated

- Role purpose: external research, source evaluation, synthesis
- Persona: skeptical analyst — cites sources, separates facts from inference, notes uncertainty
- Tool categories: `read_only_inspection`
- Boundary mode: `observe_warn`
- Routing triggers: unique shadow triggers (not the built-in research-domain keywords)

## What Was Intentionally Not Migrated

- `web_search` and `browser` tool categories (research-domain built-in tools; not yet mapped to package categories)
- `report_synthesis` and `citation_collection` tool declarations (built-in profile config)
- `may_write_paths` enforcement (docs/reports/ write access is built-in policy)
- Researcher-scribe escalation mechanics (built-in overlay rules; not in package routing)
- Source trust and external content risk hooks (built-in security review hook)

## MVP Limitations

- Package routing is validated but **not active**; built-in `researcher` routing via research-domain keywords remains authoritative.
- `observe_warn` boundary mode logs would-be denials but does not block.

## Why Package Routing Is Not Active

The Hermes Role Packages MVP explicitly defers routing activation to v1. Built-in research-domain routing continues to handle all live traffic.

## Expected Future Migration Path

1. Expand `allowed_categories` to include web-research equivalents when category taxonomy matures.
2. Activate `hermes_researcher_core` routing triggers in v1 alongside built-ins.
3. Retire the built-in researcher profile after calibration.
