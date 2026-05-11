# WhatsApp Correspondent Sandboxes Roadmap

> **For Hermes:** Implement in small, reviewable increments. Keep this document as the source of truth for scope, order, and completion tracking. Update checkboxes and the stage status summary as work lands.

**Goal:** Make Telegram the only Hermes control channel, while WhatsApp is used only for outward third-party correspondence with isolated per-correspondent data, safe escalation to Telegram, and global internal search.

**Architecture:** Introduce a policy-and-data layer on top of the existing Hermes gateway behavior. Telegram becomes the control plane. WhatsApp becomes the operator plane. Each WhatsApp correspondent gets an isolated data container (“sandbox”) with seed context, acquired facts, transcript, policy, and status. Unknown or out-of-scope WhatsApp events escalate to Telegram instead of being auto-accepted as tasks.

**Tech Stack:** Hermes gateway/plugin hooks, WhatsApp bridge transport, Telegram delivery, local JSON/JSONL or SQLite-backed correspondent store, pytest.

---

## 0. Decisions locked in

These product decisions are already approved and should be treated as requirements.

- [x] Telegram is the **only** control channel for Hermes.
- [x] WhatsApp is used only for **outward third-party correspondence**.
- [x] If Denis sends a task in WhatsApp, Hermes must redirect control back to Telegram.
- [x] Unknown WhatsApp inbound messages must be **escalated in Telegram** with action choices.
- [x] No separate generic WhatsApp auto-reply is required.
- [x] Facts learned in WhatsApp correspondence should be **auto-saved** with source attribution.

### Operating principles

- **Control plane:** Telegram only.
- **Operator plane:** WhatsApp only.
- **Global internal search:** yes.
- **Cross-contact disclosure by default:** no.
- **Prompt-only safety is insufficient:** enforce with policy gates/hook logic.

---

## 1. Tracking model

### Stage status legend

- `planned` — not started
- `in_progress` — actively being implemented
- `blocked` — waiting on decision or prerequisite
- `done` — implemented and verified

### Current stage status

- Worktree hygiene / preflight: `done`
- MVP: `done`
- Hotfix A — outbound auto-open / inbound alias admission: `in_progress`
- Stage 1: `planned`
- Stage 2: `planned`
- Stage 3: `planned`
- Stage 4: `planned`

### Completion rule

A task checkbox should only be marked complete when:
1. code/config is in place,
2. behavior is tested or manually verified,
3. this document is updated.

---

## 2. Data model target

Each correspondent sandbox should support at least:

- stable `contact_id`
- normalized WhatsApp identity (`jid`, number, aliases)
- `profile`
- `seed_context`
- `facts`
- `transcript`
- `policy`
- `status`
- `notes`
- `audit metadata`

### Provenance model for facts

Every stored fact should support:

- `source_type`: `from_correspondent` | `from_denis` | `inferred_by_hermes`
- `source_ref`: optional message / event reference
- `timestamp`
- `confidence` (optional in MVP, required by later stages if cheap)
- `confirmation_state`: `unconfirmed` | `confirmed_by_denis` | `disputed`

---

# Preflight — Worktree hygiene and conflict prevention

**Intent:** Understand and tame the current uncommitted state before new WhatsApp sandbox changes land, so we do not accidentally overwrite useful work or build on top of conflicting experiments.

**Current observed worktree candidates:**
- `gateway/run.py`
- `tests/gateway/test_pre_gateway_dispatch.py`
- `tests/gateway/test_unknown_command.py`
- `plugins/whatsapp-policy/`
- this roadmap file

**Success criteria:**
- We know what every pre-existing uncommitted change is for.
- We decide whether to keep, adapt, split, or discard each one.
- Conflicting experiments are resolved before substantive new implementation starts.
- The roadmap and implementation order reflect those decisions.

## Preflight tasks

### A. Inventory and classification
- [x] Capture the full `git status` and relevant diffs for the current worktree.
- [x] Classify each uncommitted file as one of:
  - [x] keep as direct prerequisite
  - [x] keep but refactor/move
  - [x] superseded by this roadmap
  - [x] discard/revert
  - [x] postpone and isolate
- [x] Record a short note for each file explaining the decision.

### B. Conflict analysis
- [x] Compare existing `gateway/run.py` edits against the planned WhatsApp control/sandbox architecture.
- [x] Compare existing gateway test edits against the new expected behavior.
- [x] Inspect `plugins/whatsapp-policy/` and determine whether it should be the implementation home, a temporary experiment, or removed.
- [x] Identify any overlapping assumptions that could cause rework or merge conflicts later.

### C. Decision and cleanup
- [x] Decide what changes stay on the path to implementation.
- [x] Revert or isolate changes that are unrelated or conflicting.
- [x] If useful experiments exist, fold them into the staged plan as explicit prerequisites/subtasks.
- [x] Ensure the working tree is in a known-good state before MVP coding proceeds.

### D. Verification
- [x] The kept files and rationale are documented in the roadmap work log.
- [x] No ambiguous leftover edits remain in core implementation paths.
- [x] We have a clear starting point for MVP task execution.

### Preflight decision notes

- `gateway/run.py` -> **keep as direct prerequisite**. The edits add two things already aligned with the roadmap: hook-level `bypass_auth` support for scoped external WhatsApp admission, and plugin-command session-context setup so WhatsApp policy commands can reliably identify the current sender/session.
- `tests/gateway/test_pre_gateway_dispatch.py` -> **keep as direct prerequisite**. The added test covers the exact `rewrite + bypass_auth` path needed for safe scoped-correspondent admission.
- `tests/gateway/test_unknown_command.py` -> **keep as direct prerequisite**. The added test verifies plugin slash commands receive gateway session context, which the WhatsApp policy plugin depends on.
- `plugins/whatsapp-policy/` -> **keep but refactor/move forward carefully**. This is a useful prototype implementing owner-only WhatsApp control, scoped correspondent admission, and tool gating. It is not yet the final sandbox system, but it is a strong foundation for MVP and early Stage 2 work rather than something to discard.
- `docs/plans/2026-05-11-whatsapp-correspondent-sandboxes-roadmap.md` -> **keep as source of truth**.
- `discard/revert` bucket -> **none identified right now**.
- `postpone and isolate` bucket -> **none identified right now**.

### Preflight conflict notes

- The current uncommitted `gateway/run.py` edits are enabling infrastructure, not conflicting product behavior.
- The plugin prototype currently focuses on scoped-thread policy state in `~/.hermes/policy/whatsapp_delegations.json`; later sandbox storage may outgrow that shape, so we should avoid overcommitting to this exact file format as the final data model.
- The plugin already auto-opens/refreshes scoped threads on outbound WhatsApp sends. That behavior is useful, but during MVP implementation we must verify it matches the desired Telegram-controlled task-opening flow.
- The plugin currently blocks many tools for external correspondents and allows only narrow actions. That aligns well with Stage 2 guardrails, but MVP should treat this as an implementation aid, not proof that the final sandbox UX is complete.

### Preflight exit criteria
- [x] The worktree has been triaged.
- [x] The roadmap reflects what is being kept vs discarded.
- [x] New implementation work can proceed without hidden conflicts.

---

# MVP

**Intent:** Get the safe operating model working end-to-end with minimal but durable structure.

**Success criteria:**
- Hermes no longer accepts operational control through WhatsApp.
- WhatsApp unknown inbound messages escalate to Telegram.
- Active external correspondences can be associated with isolated sandboxes.
- New facts from WhatsApp can be auto-saved with provenance.
- Missing information for an active WA thread is requested from Denis in Telegram.

## MVP tasks

### A. Channel policy
- [x] Add a Telegram-only control rule for task intake.
- [x] Detect Denis-originated WhatsApp task/control attempts.
- [x] Route those attempts into Telegram instead of executing in WhatsApp.
- [x] Ensure WhatsApp inbound is treated as correspondence events, not generic agent commands.

### B. Unknown inbound escalation
- [x] Detect WhatsApp inbound from unknown or unclassified correspondents.
- [x] Create a Telegram escalation payload containing:
  - [x] sender identity
  - [x] raw/cleaned message preview
  - [x] timestamp
  - [x] recommended classification/status
  - [x] action options (ignore/ban, open scoped conversation, attach to existing task, ask Denis)
- [x] Prevent automatic progression of unknown inbound into active task execution.

### C. Basic sandbox storage
- [x] Create a durable storage layout for per-correspondent sandboxes.
- [x] Define the minimal schema for `profile`, `policy`, `facts`, `transcript`, and `status`.
- [x] Implement sandbox lookup by normalized WhatsApp identity.
- [x] Implement sandbox creation for newly approved correspondents.

### D. Fact capture
- [x] Save inbound/outbound WhatsApp transcript entries into the sandbox.
- [x] Extract/store key facts with source attribution.
- [x] Preserve enough raw transcript context to re-derive facts later if needed.

### E. Telegram clarification loop
- [x] Detect when an active WA conversation needs missing data.
- [x] Escalate the missing-data question to Telegram.
- [x] Attach the answer back to the correct sandbox.
- [x] Resume the WA thread using the newly saved data.

### F. Verification
- [x] Test: Denis sends a task in WA -> not executed there; Telegram control path engaged.
- [x] Test: unknown WA sender -> Telegram escalation created.
- [x] Test: approved correspondent -> sandbox lookup/create works.
- [x] Test: new fact from correspondent -> saved with provenance.
- [x] Test: missing fact -> Telegram clarification -> saved -> conversation can continue.

### MVP implementation notes

- The current MVP implementation lives primarily in `plugins/whatsapp-policy/` plus enabling gateway hook support in `gateway/run.py`.
- Telegram control attempts in WhatsApp are converted into Telegram-side escalation notifications and never reach the normal agent execution path in WhatsApp.
- Unknown WhatsApp inbound now creates a pending event and a Telegram escalation instead of silently entering agent flow.
- Basic correspondent sandboxes are stored under `~/.hermes/policy/whatsapp_sandboxes/<contact_id>/` with:
  - `profile.json`
  - `policy.json`
  - `status.json`
  - `transcript.jsonl`
  - `facts.jsonl`
  - `notes.md`
- Pending inbox / clarification items are tracked in `~/.hermes/policy/whatsapp_delegations.json`.
- Telegram control commands currently include at least: `status`, `pending`, `open`, `approve`, `reject`, `answer`, `note`, `close`, and `alias`.

### MVP exit criteria
- [x] All MVP verification checks pass.
- [x] Storage format is documented in this file or adjacent docs.
- [x] A basic operator demo path works end-to-end.

---

# Hotfix A — Outbound auto-open vs inbound alias admission

**Intent:** Fix the live bug where Hermes can send the first WhatsApp message to an external contact, the contact replies, the gateway receives the reply, but `whatsapp-policy` drops it as `non-owner sender without active scoped thread`.

**Observed live evidence:**
- WhatsApp transport is healthy: outbound sends succeed and inbound replies reach `gateway.run`.
- Gateway logs show repeated skips for the external correspondent:
  - `pre_gateway_dispatch skip: reason=non-owner sender without active scoped thread platform=whatsapp chat=100820196565244@lid`
- The plugin already auto-opens a scoped thread in `on_post_tool_call(...send_message...)`, so the failure is not “no auto-open exists”, but more likely an identity-match gap between:
  - outbound target opened by phone identity (e.g. `77782110625`)
  - inbound reply surfaced by the bridge under LID identity (e.g. `100820196565244@lid`)

**Working hypothesis:** The scoped thread is created successfully on outbound, but later inbound matching is too strict or too early relative to bridge alias discovery (`lid-mapping-*.json`). As a result, a valid reply is treated as an unrelated non-owner sender.

**Success criteria:**
- A `send_message` to `whatsapp:+<number>` opens enough scoped state for the reply to be admitted reliably.
- A reply from the same human is admitted whether the bridge surfaces it as phone-JID or LID, once alias information is available.
- If the reply still cannot be admitted, Telegram receives a diagnostic escalation that explains the mismatch instead of making the reply appear “lost”.
- Plugin and gateway tests cover the failure mode explicitly.

## Hotfix A tasks

### A. Reproduce the live failure in tests
- [x] Add a focused plugin test for: outbound `send_message` opens thread by phone target, inbound reply arrives under a different WhatsApp alias shape, and current matching fails.
- [ ] Add a companion test for the intended success path when alias mapping is available.
- [x] Keep these tests narrow enough that they explain whether the failure is in thread creation, identity normalization, or admission matching.

### B. Make scoped-thread matching alias-aware
- [x] Review `_find_thread_by_target(...)`, `_thread_matches_token(...)`, and related helpers in `plugins/whatsapp-policy/__init__.py`.
- [x] Change admission matching to use alias sets, not only a single canonical identifier string.
- [x] Ensure a thread opened via phone target can later match an inbound sender surfaced as LID.
- [x] Refresh or persist thread aliases as new bridge mappings become available.

### C. Strengthen outbound auto-open bookkeeping
- [x] Review `on_post_tool_call(...send_message...)` and `_open_or_refresh_thread(...)`.
- [x] Ensure outbound auto-open records enough identity metadata for later inbound admission.
- [x] Confirm that Telegram-originated outbound sends and WhatsApp-originated scoped replies produce the right `source_type` and thread refresh behavior.

### D. Improve operator diagnostics for unmatched replies
- [ ] When an external inbound message cannot be matched to an active thread, distinguish:
  - [ ] truly unknown sender
  - [ ] likely known sender with alias mismatch
  - [ ] expired/closed thread
- [ ] Escalate the mismatch to Telegram with enough context to debug quickly:
  - [ ] raw inbound chat id
  - [ ] normalized/canonical sender
  - [ ] candidate active thread target if any
  - [ ] thread purpose/status if recoverable

### E. Verification
- [x] Test: outbound Telegram `send_message` to `whatsapp:+777****0625` auto-opens a thread and writes transcript/sandbox state.
- [x] Test: inbound reply from the same human under alternate alias shape is admitted into the scoped thread.
- [ ] Test: when alias resolution is still impossible, Telegram gets a clear escalation instead of silent “missing reply” behavior.
- [ ] Live verification: send to a real external number, receive a reply, and confirm that the reply is either admitted or explicitly escalated with diagnostics.

### Hotfix A exit criteria
- [ ] The specific live bug “I receive your WhatsApp messages and reply, but Hermes never sees my reply” is no longer reproducible.
- [ ] Admission behavior is explainable from logs and tests.
- [ ] The roadmap work log records the exact fix and verification evidence.

---

# Stage 1 — Operational sandboxes

**Intent:** Turn basic per-contact storage into a practical operating model with statuses, seed context, and explicit disclosure rules.

**Success criteria:**
- Every active external conversation has a clear sandbox lifecycle.
- Seed context is distinct from acquired facts.
- Disclosure policy can explicitly allow or forbid data exposure.
- Telegram can be used to review and amend sandbox state.

## Stage 1 tasks

### A. Sandbox lifecycle
- [ ] Add explicit statuses: `unclassified`, `active`, `waiting_on_denis`, `waiting_on_contact`, `resolved`, `blocked`, `archived`.
- [ ] Define allowed status transitions.
- [ ] Update sandbox state automatically from message flow where safe.
- [ ] Keep a human-readable notes field for operator context.

### B. Seed context vs acquired facts
- [ ] Separate seed context from learned facts in storage.
- [ ] Allow seed context to be created when Denis opens a task.
- [ ] Make it possible to update/correct seed context without corrupting fact history.

### C. Disclosure policy
- [ ] Define per-sandbox allowed disclosures.
- [ ] Define per-sandbox restricted disclosures.
- [ ] Mark high-risk categories requiring Telegram approval:
  - [ ] payments
  - [ ] calendar commitments
  - [ ] identity/docs
  - [ ] phone numbers/extra contacts
  - [ ] media/files
- [ ] Ensure policy is consulted before outbound WA responses are sent.

### D. Telegram management UX
- [ ] Add a simple management flow to inspect a sandbox summary from Telegram.
- [ ] Add a way to approve/classify an unknown sender from Telegram.
- [ ] Add a way to amend seed context from Telegram.
- [ ] Add a way to close/archive a sandbox from Telegram.

### E. Verification
- [ ] Test: seed context can be added before first outreach.
- [ ] Test: a forbidden disclosure is blocked and escalated.
- [ ] Test: a sandbox can transition through active/waiting/resolved cleanly.
- [ ] Test: Telegram management actions update the right sandbox.

### Stage 1 exit criteria
- [ ] Operators can inspect and manage sandbox state from Telegram.
- [ ] Disclosure policy affects real reply behavior.
- [ ] Sandbox lifecycle is stable under normal usage.

---

# Stage 2 — Policy enforcement and guardrails

**Intent:** Move from “good structure” to “hard safety boundaries”.

**Success criteria:**
- Out-of-scope WA actions are blocked by policy logic, not just prompt instructions.
- Unknown senders and active correspondents are handled under separate rules.
- High-risk actions reliably escalate to Telegram.

## Stage 2 tasks

### A. Admission policy
- [ ] Introduce a clear inbound admission path for WhatsApp events.
- [ ] Distinguish unknown sender, approved correspondent, and Denis-in-WA cases.
- [ ] Ensure unknown inbound never silently becomes an active task thread.

### B. Tool/action gating
- [ ] Restrict WA-context actions to a narrow safe subset.
- [ ] Block at minimum:
  - [ ] new task intake
  - [ ] unrelated third-party outreach
  - [ ] calendar mutation without approval
  - [ ] payments / commitments
  - [ ] sensitive file/doc transmission
  - [ ] broad memory/skill/cron mutation
- [ ] Allow at minimum:
  - [ ] reply to same correspondent
  - [ ] ask clarifying question to same correspondent
  - [ ] ask Denis in Telegram
  - [ ] save facts and transcript
  - [ ] read sandbox/policy state

### C. Audit trail
- [ ] Log policy decisions for allow/block/escalate actions.
- [ ] Log why a Telegram escalation was triggered.
- [ ] Preserve message-to-decision traceability.

### D. Verification
- [ ] Test: out-of-scope ask from correspondent is blocked/escalated.
- [ ] Test: high-risk disclosure requires Telegram approval.
- [ ] Test: admitted safe actions continue to work.
- [ ] Test: audit trail explains what happened.

### Stage 2 exit criteria
- [ ] WA safety does not depend solely on model obedience.
- [ ] Policy allow/block/escalate behavior is reproducible and inspectable.

---

# Stage 3 — Global search and knowledge retrieval

**Intent:** Make the accumulated sandbox knowledge genuinely useful without breaking isolation.

**Success criteria:**
- Hermes can search across all correspondents internally.
- Search results can inform operator reasoning.
- Outbound replies remain scoped to the active sandbox and policy.

## Stage 3 tasks

### A. Search index
- [ ] Choose/store a searchable index over profile/facts/notes/transcripts.
- [ ] Support lookup by name, alias, phone, jid, topic, and fact text.
- [ ] Include status and recency filters if cheap.

### B. Retrieval rules
- [ ] Distinguish internal retrieval from outbound disclosure.
- [ ] Ensure search results cannot be leaked directly unless active sandbox policy allows it.
- [ ] Add summary views for operator use in Telegram.

### C. Telegram UX for search
- [ ] Add a way to search correspondents from Telegram.
- [ ] Add a concise result view showing match reason and sandbox status.
- [ ] Add drill-down into a specific sandbox summary.

### D. Verification
- [ ] Test: search finds a correspondent by fact text.
- [ ] Test: search finds a correspondent by normalized identity.
- [ ] Test: internal retrieval does not bypass disclosure policy in WA replies.

### Stage 3 exit criteria
- [ ] Cross-sandbox knowledge is easy to query.
- [ ] Isolation at reply time remains intact.

---

# Stage 4 — Production ergonomics and maintenance

**Intent:** Make the system robust for sustained daily use.

**Success criteria:**
- Active correspondences are easy to monitor.
- Old sandboxes can be archived without losing searchability.
- The system is documented well enough to maintain safely.

## Stage 4 tasks

### A. Operator overview
- [ ] Add an active-sandboxes summary view.
- [ ] Show waiting-on-Denis vs waiting-on-contact counts.
- [ ] Surface recent unknown inbound escalations.

### B. Archival and cleanup
- [ ] Add archive behavior for resolved/inactive sandboxes.
- [ ] Keep archived sandboxes searchable.
- [ ] Define retention policy for raw transcript vs extracted facts.

### C. Documentation
- [ ] Document storage layout and policy model.
- [ ] Document how to recover/correct a bad classification.
- [ ] Document how to inspect audit history.

### D. Verification
- [ ] Test archive/restore flow if implemented.
- [ ] Test operator summary views.
- [ ] Confirm archived data remains searchable.

### Stage 4 exit criteria
- [ ] The system is operationally maintainable.
- [ ] Long-term data hygiene and observability are acceptable.

---

## 3. Open design choices to resolve during implementation

These are not blockers for MVP, but must be explicitly chosen as code is written.

- [ ] Storage backend final choice: JSON/JSONL sidecars vs SQLite vs hybrid.
- [ ] Where policy enforcement lives: gateway core patch vs plugin/hook layer.
- [ ] Exact Telegram UX for escalation actions.
- [ ] Whether sandbox summaries are pure files or backed by a structured index.
- [ ] Fact extraction approach: explicit rules only vs lightweight heuristic extraction.

---

## 4. Suggested implementation order

1. Preflight / triage existing uncommitted worktree changes
2. MVP / channel policy and unknown inbound escalation
3. MVP / basic sandbox store and transcript capture
4. MVP / Telegram clarification loop
5. Hotfix A / outbound auto-open + inbound alias admission
6. Stage 1 / lifecycle + disclosure policy
7. Stage 2 / hard policy gates + audit
8. Stage 3 / search index + Telegram search UX
9. Stage 4 / operator overview, archive, docs

---

## 5. How to use this file during execution

For every completed chunk of work:

1. mark the relevant checkboxes,
2. update the relevant stage status,
3. add a brief note under a work log section or commit reference,
4. only then move to the next task.

### Work log

- 2026-05-11: roadmap created and saved; no implementation started yet.
- 2026-05-11: added explicit preflight stage to triage existing uncommitted worktree changes before MVP implementation.
- 2026-05-11: completed preflight triage of current uncommitted worktree. Kept `gateway/run.py` and related gateway tests as direct prerequisites; kept `plugins/whatsapp-policy/` as a useful prototype to refactor forward rather than discard.
- 2026-05-11: post-pause review. Re-read entire plugin (1273 lines, schema v2). Ran all directly relevant tests:
  - `tests/plugins/test_whatsapp_policy_plugin.py` — 4/4 passed
  - `tests/gateway/test_pre_gateway_dispatch.py` — 6/6 passed (including new `rewrite + bypass_auth` test)
  - `tests/gateway/test_unknown_command.py` — 12/12 passed (including new plugin-command session-context test)
  - Full suite has 169 collection errors due to missing venv deps (expected in sandbox); our changes are clean.
- 2026-05-11: completed MVP implementation. Added Telegram-only control redirection for WhatsApp, pending-event escalation to Telegram, basic per-correspondent sandbox storage under `~/.hermes/policy/whatsapp_sandboxes/`, transcript/fact capture, and a Telegram-driven clarification/answer flow in `plugins/whatsapp-policy/`.
- 2026-05-11: added Hotfix A stage after live validation showed a post-MVP bug: outbound first-contact messaging works, but replies from the external correspondent can still be dropped as `non-owner sender without active scoped thread` when inbound identity arrives under a different WhatsApp alias shape (notably LID vs phone identity).
- 2026-05-11: implemented the first Hotfix A slice. Added a focused plugin regression test for inbound LID chat aliases, switched scoped-thread matching from exact canonical-target equality to alias-set matching, and now persist learned aliases into both thread state and sandbox profile metadata. Verification in local uv venv: `tests/plugins/test_whatsapp_policy_plugin.py` 5/5 passed, `tests/gateway/test_pre_gateway_dispatch.py` 6/6 passed, `tests/gateway/test_unknown_command.py` 12/12 passed.
- 2026-05-11: completed the second Hotfix A slice for outbound alias capture. `send_message` on WhatsApp now returns bridge alias metadata (`bridge_chat_id`, `normalized_chat_id`, `remote_jid`), `whatsapp-policy` records those aliases during `on_post_tool_call(...send_message...)`, and new regression tests cover both alias extraction and LID-reply admission without waiting for `lid-mapping-*.json`. Final local verification: `tests/plugins/test_whatsapp_policy_plugin.py` 8/8 passed, `tests/gateway/test_pre_gateway_dispatch.py` + `tests/gateway/test_unknown_command.py` 19/19 passed, `node --check scripts/whatsapp-bridge/bridge.js` passed. Independent LLM review rerun after fixes reported no blocking issues.
