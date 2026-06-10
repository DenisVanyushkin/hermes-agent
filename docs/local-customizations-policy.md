# Local customizations operational policy

This Hermes install intentionally carries local behavior that is not guaranteed to exist upstream. To keep self-modification safe and maintainable, follow this operating model.

## Goals

- Keep the live checkout updateable.
- Avoid dirty working trees as the steady state.
- Make every intentional local behavior traceable to a commit.
- Allow unattended upstream updates without silent patch drift.
- Keep user-facing routing preferences stable across updates.

## Branch model

- Upstream tracking branch: `origin/main`
- Local customization branch: `local/customizations`
- Normal runtime branch: `local/customizations`

Never use `main` as the branch for live local edits.

## Steady-state invariants

At rest, all of the following should be true:

1. `git branch --show-current` is `local/customizations`
2. `git status --short` is empty
3. Local Hermes behavior changes exist as commits on `local/customizations`
4. Generated/runtime artifacts do not live as untracked files inside the repo
5. Config-only preferences are stored in `~/.hermes/config.yaml`, not hardcoded into source

## Where changes belong

### Config-only changes

Use config for:
- per-platform tool-progress visibility
- runtime footer toggles
- provider/model selection
- delivery preferences

Do not patch Python/JS source when config is enough.

### Source changes

Use source commits only for behavior that cannot be expressed in config, for example:
- custom runtime footer logic
- bridge normalization behavior
- local maintenance helpers that need repo context

### Out-of-repo artifacts

Keep these outside the repo whenever the execution environment allows it:
- cron helper scripts under `~/.hermes/scripts/`
- user caches under `~/.hermes/cache/`
- patch archives or notes under `~/.hermes/patches/`

## Procedure for future self-modification

When Hermes modifies itself intentionally:

1. Confirm whether the desired behavior can be achieved by config first.
2. If code changes are needed, edit the live repo on `local/customizations`.
3. Run the smallest relevant verification available.
4. Review `git diff` for accidental edits.
5. Commit the change to `local/customizations` with a focused message.
6. Return the repo to a clean state.
7. If the procedure or pitfall is reusable, update the relevant skill/reference.

Do not leave intentional changes uncommitted.

## Commit conventions for this branch

Prefer small, focused commits such as:
- `feat: ...` for new local behavior
- `fix: ...` for corrections to local behavior
- `chore: ...` for maintenance helpers and local automation
- `docs: ...` for operational policy and references

## Update workflow

The preferred update path is a two-step human-approved gate:

### 1) Preflight report

Before any update, run the preflight helper:

```bash
scripts/preflight-local-customizations-update.sh
```

The report must show:
- the upstream delta relative to the current local merge-base
- local uncommitted changes, if any
- likely conflict points
- likely breaking-change surfaces
- a clear recommendation whether the update should be treated as low, medium, or high risk

Do **not** update before the report is reviewed.

### 2) Explicit approval

Wait for an explicit human approval before applying any upstream changes.

Only after approval:

1. Ensure repo is clean enough to proceed.
2. `git fetch origin --prune`
3. `git rebase origin/main` while on `local/customizations`
4. Restart the gateway only if HEAD changed.
5. If rebase conflicts, stop and report clearly.
6. After the update completes, provide a post-update report summarizing what changed, what was verified, and whether any restart failed.

Avoid `hermes update` for this customized install, because its autostash behavior is a poor fit for a locally modified checkout and it does not give the approval-gated report the operator wants.

## Failure handling

### If repo is dirty

Do not auto-update.

Instead:
- inspect `git status --short`
- either commit intentional edits
- or revert accidental edits
- only then retry the update flow

### If rebase conflicts

Do not force-continue.

Instead:
- keep the conflict visible
- report which files conflicted
- resolve manually or with supervised agent help
- run verification again

### If gateway restart fails after a successful rebase

Treat the code update and service restart as separate states:
- report that code updated successfully
- report restart failure separately
- do not mislabel the whole operation as a code failure

## Messaging/routing policy

For this install:
- Telegram is the service channel for tool/progress/operational chatter
- WhatsApp is the concise semantic user-facing channel

Prefer config/platform overrides for this split instead of custom routing logic in business code.

## Recommended periodic checks

Occasionally verify:

```bash
git branch --show-current
git status --short
git log --oneline --decorate -5
git rev-list --left-right --count origin/main...local/customizations
```

A healthy system usually shows:
- current branch = `local/customizations`
- clean status
- a short, understandable stack of local commits on top of upstream

## Role-package hunk ledger (placeholder)

Planned role-package work (docs/audit/05–07) will touch the following
upstream-active files with small call-out hunks. Every hunk added there must
be listed here (file, anchor, purpose) so post-rebase repair stays mechanical:

- agent/conversation_loop.py — role context build + approval preflight + deferred pre_llm_call (present today)
- agent/turn_context.py — invoke_pre_llm_call deferral kwarg (present today)
- agent/turn_finalizer.py — requested_model field, response_pre_transformed kwarg (present today)
- hermes_cli/main.py — future `hermes role` subparser wiring
- agent/skill_utils.py — future package skill dirs
- tools/env_passthrough.py — future manifest-capped passthrough
- hermes_cli/plugins.py — future role-policy check in get_pre_tool_call_block_message
- agent/tool_executor.py — future contextvars propagation
- tools/approval.py — future role-scoped pattern table (v1+)
