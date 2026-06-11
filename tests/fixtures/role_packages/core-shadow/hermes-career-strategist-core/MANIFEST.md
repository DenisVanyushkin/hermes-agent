# Hermes Career Strategist Core

Shadow package capturing the built-in `career_strategist` role contract.

## Source Built-in Role

Built-in role ID: `career_strategist` (canonical_id: `career_strategist`)  
Profile family: `career`

## What Was Migrated

- Role purpose: vacancy evaluation, CV/cover-letter strategy, application decisions, recruiter messaging
- Persona: executive career advisor — direct, commercial, thesis-driven, no fake experience
- Tool categories: `read_only_inspection`
- Boundary mode: `observe_warn`
- Routing triggers: unique shadow triggers (not the built-in career-domain keywords)

## What Was Intentionally Not Migrated

- `job_intel_read` tool category (built-in profile config; not in package category taxonomy)
- `email_draft` / `email_send` / `slack_send` confirmation requirements (built-in profile policy)
- Researcher and scribe escalation overlay rules (built-in routing overlay)
- `apply_watchlist_or_reject_recommendations` action contract semantics (built-in only)
- Auto-submit prohibition (built-in policy; preserved in persona text but not enforced in MVP)

## MVP Limitations

- Package routing is validated but **not active**; built-in `career_strategist` routing via career-domain keywords remains authoritative.
- `observe_warn` boundary mode logs would-be denials but does not block.
- `job_intel_read` is not a declared package category in MVP.

## Why Package Routing Is Not Active

The Hermes Role Packages MVP explicitly defers routing activation to v1. Built-in career-domain routing (vacancy, cv, recruiter, etc. → career_strategist) continues to handle all live traffic.

## Expected Future Migration Path

1. Add `job_intel_read` to the tool category taxonomy.
2. Add messaging categories (`email_draft`, `slack_send`) when the category list matures.
3. Activate routing and calibrate via observe_warn.
4. Retire built-in career_strategist profile once package routing is stable.
