# Current operational state — Hermes role architecture

Date: 2026-06-09

## Snapshot
- Role-profile architecture is documented and active in the repo as the source of truth for role intent.
- Hermes roles runtime MVP is **GO** after live smoke verification.
- Scribe handoffs have a canonical storage location under `docs/profile-handoffs/`.
- `docs/state/` is the durable snapshot location for future state updates.
- Current working HEAD: `6406746e83bbc76089a7a0fb880bb474179cd640`
- Gateway is restarted and running.
- `HERMES_PROFILE_DEBUG_HEADER=1` is enabled.

## What changed today
- Live smoke validated the runtime role split:
  - Cloudflare/public exposure prompt routed to `engineer` with `security_auditor` reviewer and required approval; hard-stop occurred before file writes, runtime mutations, or external side effects.
  - Haircut prompt routed to `general_operator`, used no reviewer, asked for missing details, and did not crash or leak a Working/clarify path.
  - WebUI status/logs prompt routed to `engineer`, used no reviewer, marked approval as not required, and proceeded with read-only diagnostics.
- Working tree stayed clean for tracked files; only untracked docs remain.
- No code changes, deploys, or gateway restarts were performed for this final state update.

## Open follow-up
- None for the roles runtime MVP based on this smoke run.
