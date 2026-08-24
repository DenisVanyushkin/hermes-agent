# Job Intel Search Product Redesign Implementation Plan

> **Execution contract:** execute task by task with `superpowers:executing-plans`; use test-driven development for every behavior change and `superpowers:verification-before-completion` before every completion claim. Complete, verify, and commit one bounded slice before starting the next. Stop at every owner gate. A passing test suite never implies product or production approval.

**Plan revision:** 5.1, revised after the fourth execution-contract review on 2026-08-11

**Goal:** implement approved Product Search SoT `PS-SOT-2026-08-10-v1` and make Slack channel `C0B4MM6D52A` receive only messages authorized by that SoT or another explicitly recognized SoT.

**Primary Product SoT:** `docs/superpowers/specs/2026-08-10-job-intel-search-product-redesign-design.md`, version `1.0.0`, status `Approved`, SHA-256 `430340de2613ee733926d73ce276c93676fe64b1841bb2f68f3f9303b61fc3a8`.

**Second review input:** SHA-256 `0ae9718d99f8b5bbac5b73a88f92e27568cecc1a0c4b31de62ee98fe964a2e88`; its disposition is preserved below. The ephemeral attachment path is deliberately not a runtime or execution dependency.

**Third review input:** SHA-256 `51b563bd9f41b0bd12dfa3cab7369bfa323d9566d6d539e1c180440df5e4cecb`; its disposition is preserved below. The ephemeral attachment path is deliberately not a runtime or execution dependency.

**Fourth review input:** SHA-256 `a66c6e08ea8eed1ba919ab6ade0d0f4f02e6172ff18c7f85579bd47a351065dd`; its disposition is preserved below. The ephemeral attachment path is deliberately not a runtime or execution dependency.

**Implementation order:** isolated worktree and authority → owner-gated canonical checkpoint for the production-host acquisition experiment → Decision v2 → versioned persistence → offline UX/orchestration → owner-gated canonical checkpoint for production-host shadow → direct Hermes Slack delivery → single-gateway staging checkpoint → owner-gated deployment rehearsal and cutover.

**Runtime stack:** Python 3.12, Pydantic v2, PyYAML, SQLite/WAL, pytest, ruff, systemd, existing Semantic Contract runtime, existing Hermes Slack Bolt/Socket Mode adapter, Slack Web API, Prometheus/Grafana.

## Overview

The redesign is complete only when:

- real market acquisition is demonstrated before production product infrastructure is built;
- existing vacancy scrapers are reused unchanged through their current public interfaces;
- model-assisted evidence interpretation and deterministic product policy remain separate;
- every assessment and delivery is reproducible from immutable evidence and contract hashes;
- the exact nine-stage funnel is preserved while user, action, and CRM events remain separate;
- Hermes writes to and receives from Slack directly through its existing Slack app identity;
- one typed Product Search publisher inside the Hermes Slack gateway is the only allowed application path to `C0B4MM6D52A`;
- generic live-adapter sends, standalone sends, cron/manual sends, and legacy Job Intel paths are denied for the protected channel and leave privacy-safe logs;
- reactions, thread replies, and Block Kit actions arrive through the same Hermes Slack Socket Mode adapter and all visible responses return through the typed publisher;
- rollback keeps the protected channel silent and never re-enables legacy noise;
- all code authoring stays isolated in the feature worktree, while explicitly approved Gate A, Gate C, Gate D, and Gate E integration tests may run from a final post-rebase commit approved before the old Job Intel runtime is stopped and then merged unchanged into the canonical production checkout.

This is an application/runtime policy, not a claim against Slack workspace administrators, authorized humans, host `root`, or a compromised Hermes Slack credential. Those actors remain outside the guarantee and must be named in the cutover record.

## Confirmed live context

The runtime was rechecked read-only on 2026-08-11 through `ssh hermes-agent`.

| Evidence | Confirmed state |
|---|---|
| Canonical checkout | `/home/hermes/.hermes/hermes-agent` |
| Observed branch / HEAD (planning snapshot) | `local/customizations` / `925a1a0093e4fabd53d4b2acfa7e93690aef1637`; Task 1 must recheck for drift |
| Development isolation | Canonical checkout is the main worktree; no Product Search worktree exists yet |
| Worktree directory | `.worktrees/` exists and is ignored by `.gitignore:266` |
| Direct outbound flow | `job_intel/cli.py:_deliver_to_slack()` → `tools/send_message_tool.py` → live `SlackAdapter.send()` or Slack plugin `_standalone_send()` |
| Direct inbound flow | `plugins/platforms/slack/adapter.py` receives messages, `reaction_added`/`reaction_removed`, thread replies, and registered Block Kit actions over Socket Mode |
| Existing scraper modules | `job_intel/sources.py`, `job_intel/ats_sources.py`, `job_intel/browser_sourcing.py`, `job_intel/browser_worker.py` |
| Current webhook fallback | `JOB_INTEL_SLACK_WEBHOOK_URL` is empty; production falls through to Hermes Slack delivery |
| Current scheduled Job Intel surface | `daily`, `health`, `semantic-shadow`, `universe`, and `weekly-kpi` timers are enabled and can be stopped/masked for an experiment window |
| Current Slack gateway topology | One live `hermes_cli.main gateway run` process owns the Socket Mode connection |

The same Hermes Slack app must remain a channel member because it both posts Product Search messages and receives the related reactions/interactions. The previous plan's Notification Manager, separate receive-only app, removal of the gateway's Slack credentials, and channel-membership removal were factually wrong and are removed.

## Mandatory development-isolation contract

All code authoring, review-fix cycles, commits, fixture tests, and pre-integration verification run in a dedicated linked worktree on `hermes-agent`:

```text
repository: /home/hermes/.hermes/hermes-agent
base branch: local/customizations
feature branch: codex/job-intel-product-search
worktree: /home/hermes/.hermes/hermes-agent/.worktrees/job-intel-product-search
```

Task 1 must:

1. detect whether that worktree/branch already exists and resume it safely if it does;
2. otherwise create it from the rechecked `local/customizations` HEAD;
3. prove `.worktrees/` is ignored before creation;
4. install/reuse dependencies inside the worktree without modifying the production checkout;
5. run and record baseline tests before any code edit.

Every implementation task begins by asserting the worktree root and feature branch. No task may author or patch code directly in `/home/hermes/.hermes/hermes-agent`. Production systemd units execute the canonical checkout; temporary experiment units execute an immutable runtime export created from an already-integrated canonical commit. No unit executes `.worktrees/`.

The owner explicitly authorizes controlled integration testing on the production host because the current Job Intel system may be stopped completely during these tests. Gate A, Gate C, Gate D, and Gate E therefore use bounded integration checkpoints:

1. finish and commit the relevant slice in the feature worktree;
2. recheck `local/customizations`, tracked cleanliness, other worktrees/stashes, and concurrent Hermes activity;
3. rebase the feature branch onto that current canonical HEAD;
4. run the approved integration suite and scope/diff verification in the worktree;
5. pin the final post-rebase candidate commit and content-hashed checkpoint manifest;
6. present that exact commit, manifest, maintenance/experiment window, services/processes to stop, state/backup paths, and proposed hold/rollback outcomes; receive explicit owner approval before the first production mutation;
7. stop/mask the current Job Intel services, timers, cron/manual callbacks, and any overlapping collector named in the approved stop list;
8. integrate the exact approved commit into `local/customizations` by the reviewed fast-forward/merge method—never by copying worktree files into the production checkout;
9. verify canonical HEAD/execution hashes and export that exact integrated commit into the content-addressed gate runtime directory; only explicitly named experiment units may execute that export;
10. fail the runner closed if the exported runtime, Python/dependency identity, source/config hashes, environment identity, or expected DB schema drift. Later unrelated canonical-branch movement is recorded but cannot change the running export;
11. stop/remove temporary units or transition the gateway exactly as the gate requires, then record the closed hold outcome when the gate closes;
12. continue subsequent development in the feature worktree only when the gate record permits it, rebasing again at the next checkpoint.

Checkpoint integration is not product cutover authority: Product Search Slack delivery remains disabled until its later gate. The legacy Job Intel system is never restarted automatically after an experiment; restoration requires an explicit owner decision.

No rebase, amendment, or commit substitution is allowed after checkpoint approval. Any candidate or manifest drift voids the approval and returns execution to steps 2–6. The owner's approval of this plan authorizes the topology only; each checkpoint still requires post-rebase approval of its exact candidate before any production mutation.

**Gate A owner-authorized exception (2026-08-11):** the owner approved Gate A as a bounded program and explicitly waived repeated human approval for each technical candidate commit or manifest produced while completing this gate. The executing agent may repin, integrate, and run successive Gate A candidates without pausing for another owner response only when every iteration remains within the existing Gate A scope, keeps legacy Job Intel masked and paused, keeps Slack and production DB/state unreachable, leaves protected scraper and production source-configuration paths unchanged, and repeats the required rebase, test, scope-guard, deterministic-export, manifest-hash, backup, and drift checks before mutation. Any SoT amendment, source-capability expansion, protected-path change, legacy-runtime restoration, Slack access, production-state access, transition beyond Gate A, or weakening of these safeguards is outside this exception and still requires explicit owner approval. The exception expires when Gate A is closed or torn down; all later gates retain their exact-candidate approval requirements.

## Production-host experiment execution contract

Gate A and Gate C use temporary system-level units with `User=hermes`, installed only for the bounded experiment and removed afterward:

```text
job-intel-product-search-probe-experiment.service
job-intel-product-search-probe-experiment.timer

job-intel-product-search-shadow-experiment.service
job-intel-product-search-shadow-experiment.timer
```

Each experiment pins one clean canonical commit and one content-hashed manifest under:

```text
/home/hermes/.hermes/job_intel/experiments/<gate>/<commit>/
├── manifest.yaml
├── runtime/
├── python-runtime/
│   └── venv/
├── experiment.sqlite3
├── raw-evidence/
├── logs/
├── locks/
├── browser-profile/
├── cache/
└── tmp/
```

The immutable `runtime/` export is built only from tracked files at the integrated canonical commit and is verified against its manifest before every run; it is never refreshed in place. Each gate also builds a dedicated read-only experiment venv with a copied interpreter and non-editable dependencies from the approved lock. A shared mutable production/worktree venv is not executable by an experiment unit.

The manifest pins at least:

```yaml
python:
  executable_path: <experiment-local absolute path>
  executable_sha256: <sha256>
  version: <exact version>
  implementation: <implementation>
  stdlib_root: <absolute path>
  stdlib_tree_sha256: <sha256>
environment:
  dependency_lock_sha256: <sha256>
  installed_distributions_sha256: <sha256>
  import_root: <immutable runtime path>
  sys_path_sha256: <sha256>
  editable_installs: []
```

The wrapper sets `PYTHONNOUSERSITE=1` and `PYTHONDONTWRITEBYTECODE=1`, sets the import root only to the immutable export, and before every run verifies the interpreter, stdlib, dependency lock, installed distributions, `sys.path`, and absence of editable installs pointing to the worktree or mutable canonical checkout. Task 7 dependency changes require a rebuilt venv, new hashes, a new manifest, and a new exact-candidate approval.

The experiment DB, evidence, logs, locks, browser profile, cache, temp directory, reporting periods, and run IDs are disjoint from production. Units receive no Slack token, app token, webhook, production DB path, production outbox, or standalone-send configuration. Their wrapper checks runtime/Python/config/source hashes and a gate-specific `environment_id` before every run.

All existing Job Intel acquisition/delivery units remain stopped and masked for the full multi-day window, so there is no collector overlap. A browser-backed source uses a verified cloned profile; if safe cloning is unsupported, it requires a source-level exclusive lock plus a backed-up shared profile while every production collector using it remains stopped. If neither isolation is possible, that source is marked `blocked` and is not invoked. Source rate limits, cookies, authentication-session mutations, browser processes, cache writes, and anti-bot risk are experiment evidence—not assumed side-effect-free.

Gate evidence records installed unit hashes, every scheduled/missed/overlap attempt, pinned commit/config, state paths, source locks, and teardown. Temporary units are disabled and removed after the gate; immutable evidence remains under the bounded retention policy.

## Checkpoint hold-state contract

Every Gate A/C/D/E record must close both axes below when the gate returns `revise`, `stop`, `sot_amendment_required`, or otherwise cannot proceed:

```text
canonical_hold = keep_dormant_candidate | revert_to_pre_checkpoint_guarded_commit
runtime_hold   = remain_masked_and_stop_program | restore_legacy_runtime_by_separate_owner_approval
```

`revert_to_pre_checkpoint_guarded_commit` is legal only when the recorded target preserves every protected-channel guard already installed; before such a guarded commit exists, a code revert may occur only while all legacy senders remain masked. `restore_legacy_runtime_by_separate_owner_approval` requires a new explicit approval naming the exact legacy commit, unit/process set, expected channel behavior, and window; no gate outcome restarts it implicitly.

Until the owner records both axes, the automatic fail-safe posture is `keep_dormant_candidate` plus `remain_masked_and_stop_program`: experiment/product units are stopped, publisher/interactions/reconciler are disabled, guards remain active where implemented, and no further feature checkpoint begins. This posture is temporary safety behavior, not a fabricated owner decision.

The gate record stores current canonical commit, chosen holds, running gateway commit/runtime authority, publisher/interactions/reconciler states, unit masks, DB/state paths, permission to continue feature development, and exact rollback/restore commands.

## Existing-scraper freeze

This plan does **not** rewrite, refactor, repair, or tune the existing vacancy scrapers.

Protected read-only implementation files are:

```text
job_intel/sources.py
job_intel/ats_sources.py
job_intel/browser_sourcing.py
job_intel/browser_worker.py
```

Their current source-specific extraction behavior, browser behavior, production seeds, and production source configuration remain unchanged. Phase 1 may inspect them and invoke their public interfaces from an isolated probe. It may add Product Search orchestration, evidence capture, and query-contract configuration outside those files.

If Gate A shows a source capability gap:

- first attempt to close it with already-supported parameters and Product Search query configuration;
- an owner-approved additive source may be implemented as a new isolated Product Search plugin/module without modifying the protected scraper files;
- if an existing scraper or its production configuration must change, stop and prepare a separate reviewed amendment. This plan provides no implicit authority to do so.

A scope-guard check runs before every commit and fails when the feature diff from its current merge-base changes a protected scraper file. It also records absolute hashes per base commit. At each rebase checkpoint, the old record is retained, upstream changes are reviewed, and the baseline is repinned to the new `local/customizations` base; a legitimate upstream change cannot be mistaken for a Product Search edit.

## Review disposition

The second review was evaluated against the final SoT, the corrected direct-Slack architecture, and the live runtime.

| Review item | Disposition | Plan response |
|---|---|---|
| Replace `Standard` with canonical `Core` | **Accept** | All enums, YAML, fixtures, selection tests, and metrics use only `Core | Exploration`. |
| Normalize watchlist status/review/action vocabularies | **Accept** | The plan uses the exact SoT closed sets and exact bootstrap transitions; absence of a decision creates no action. |
| Gate A cannot claim canonical stage 4 before Decision v2 | **Accept** | Gate A stops at stages 1–3 and uses a separate provisional prefilter with `provisionally_eligible`, `known_hard_block`, and `unresolved_for_decision_v2`. |
| `approve_named_source_expansion` has no continuation | **Accept** | Task 7 is an explicit conditional loop. Task 8 remains blocked until a repeated Gate A decision says `proceed`. Any SoT-narrowing requires a versioned SoT amendment. |
| Runtime orchestrator is missing | **Accept** | Task 19 adds `pipeline.py`, commands, locks, resume/replay, shadow and production modes, and exact CLI entry points before Gate C. |
| Urgent unit is missing | **Accept** | Task 27 explicitly creates `job-intel-product-search-urgent.service` and `.timer` with an independent lock/cap. |
| Separate receive-only Slack app is wrong for NM-authored buttons | **Superseded by corrected runtime fact; underlying constraint accepted** | NM is removed. The existing Hermes Slack app both publishes and receives through Bolt/Socket Mode. Block actions are handled by that same app identity. |
| Schema mutation occurs before migration framework | **Accept** | Company evidence remains domain/content-addressed through Gate B; Task 13 introduces the migration framework before any production-oriented persistence mutation. |
| Clarify root-delivered/detail-failed semantics | **Accept** | Successful daily/urgent root delivery advances every included item to stage 7 and consumes its slot; detail failure remains independently retryable and never reselects the item. |
| Bind NM source to principal | **Not applicable** | There is no NM principal/source contract. Direct Slack authorization is a typed in-process publisher plus protected-channel guards and runtime credential scoping. |
| Attachment review path is ephemeral | **Accept** | The attachment path is removed. This plan carries the stable disposition and input hash only. |
| NM tasks are too large | **Superseded; principle accepted** | NM tasks are removed. Direct Slack work is split into envelope/outbox, guard, publisher/receipt, interactions, staging, and observability tasks. |
| Close NM and direct-Slack deny planes before sender | **Superseded** | The two real Hermes planes—live adapter and standalone/direct writer—are both denied before the Product Search publisher is enabled. |
| Use a separate host worktree | **Owner correction accepted** | Task 1 creates/resumes the dedicated ignored worktree and all code authoring occurs there; only reviewed gate checkpoints integrate pinned commits into canonical `local/customizations` for production-host testing. |
| Existing scrapers should not be modified | **Owner correction accepted** | The scraper freeze and per-commit scope guard are normative; conditional expansion is additive or stops for a new amendment. |

The third review was evaluated against the final SoT, live systemd/gateway topology, and the owner's explicit permission to stop the broken legacy Job Intel runtime and test pinned integrations on the production host.

| Third-review item | Disposition | Plan response |
|---|---|---|
| Gate A/Gate C have no physical multi-day runner | **Accept with owner-selected topology** | Development remains in the worktree; reviewed checkpoints merge into canonical `local/customizations`, legacy Job Intel is fully stopped/masked, and temporary production-host units run an immutable export of that pinned canonical commit with isolated state. |
| Task 19 depends on the later outbox | **Accept** | Task 19 ends at non-sendable `DeliveryIntentV1`; Task 21 alone adds envelope/outbox production mode. |
| Gate C asks for interaction kinds not implemented yet | **Accept** | Gate C covers four root/detail surfaces and decision-control mocks only; prompts, acknowledgements, evaluations, and packages move to Gate D. |
| Gate D Socket Mode topology is ambiguous | **Accept with owner-selected topology** | No parallel gateway runtime authority is allowed. A maintenance checkpoint stops the old gateway, deploys the candidate canonically, and starts one pinned candidate process/revision with production publishing off and isolated staging state. |
| Publisher must be default-off | **Accept** | Startup requires an explicit enable flag plus exact environment/schema/authority/app/team/channel checks and no cutover lock; absence/mismatch starts no worker and performs no Slack call. |
| Old gateway remains active during cutover | **Accept** | The old gateway is quiesced before integration/migration; the replacement starts with publisher disabled, guards are proved first, and only then is production publishing enabled. |
| Thread replies need deterministic item correlation | **Accept** | Item actions live on tracked detail messages and carry opaque item/assessment/nonce values through Block Kit/modal state; ambiguous free text creates no item action or visible reply. |
| Require modern upload and bounded metadata reconciliation | **Accept** | `files_upload_v2`/external upload sequence is mandatory, legacy `files.upload` is rejected, artifact receipts are independent, and history/replies use `include_all_metadata=true`, bounded windows, and `Retry-After`. |
| Scope guard should survive rebases | **Accept** | It checks the feature diff from the current merge-base and repins recorded upstream hashes at each reviewed checkpoint. |
| “Full suite” is ambiguous | **Accept** | The plan says “full approved Product Search integration suite”; repository-wide production-host runs require a separate resource review. |
| Attention actions may need `chat.update` | **Accept with simpler behavior** | `Start review`/`Done` use silent protocol acknowledgement and durable state only; no visible update or new message is added to the closed allowlist. |
| Record the Slack app scope manifest | **Accept with existing-app boundary** | Gates record the conversation-type-derived Product Search scopes/events and the existing app's total scopes/events. Product Search may add no unnecessary scope, but unrelated scopes already required by the shared Hermes Slack app are documented rather than falsely prohibited. |

The fourth review was evaluated against revision 5.0, the live clean canonical host checkout, and the owner's approved worktree-plus-production-checkpoint model.

| Fourth-review item | Disposition | Plan response |
|---|---|---|
| Exact-commit approval precedes rebase | **Accept** | Every checkpoint now rebases, tests, pins the final candidate/manifest, and only then requests owner approval before the first production mutation. Any later drift voids approval. |
| Immutable export omits Python/dependency identity | **Accept** | Gate A/C use a dedicated read-only experiment venv and pin interpreter, stdlib, dependency lock, installed distributions, import root, `sys.path`, and empty editable-install state. |
| Only publisher is default-off | **Accept** | Publisher, Product Search interactions, and reconciler have independent default-off flags under one shared startup gate and state namespace. Enable/disable uses an explicit config/lock transition plus full gateway stop/start. |
| Failed gates leave canonical/runtime state ambiguous | **Accept with two-axis normalization** | Every non-proceeding Gate A/C/D/E closes explicit `canonical_hold` and `runtime_hold` values; while awaiting owner choice, dormant candidate plus masked runtime is the fail-safe posture. |
| Exactly one WebSocket is too literal | **Accept** | The invariant is one gateway process, pinned revision, Product Search runtime authority, and state boundary; SDK-managed reconnect overlap is allowed inside that authority and must remain replay-safe. |
| Scope manifest assumes public channels | **Accept** | Gate D resolves production/staging conversation types from authenticated Slack evidence, derives the matching history scope/message event, and fails closed on missing scope/subscription. Channel ID prefix is not evidence. |

## Authority and conflict rules

1. Candidate Facts are canonical for Denis's experience and constraints; no model or profile may broaden them.
2. Official vacancy evidence is canonical for vacancy facts; title, source, company reputation, or model inference cannot replace it.
3. Product Search SoT v1 governs product behavior and supersedes only artifacts named in its Appendix B.
4. Semantic Contract v1 remains immutable unless a separately reviewed versioned gap migration is approved.
5. Decision Contract v1 and the legacy evaluator remain immutable, non-user-facing counterfactual artifacts.
6. CRM state is authoritative for application/outreach lifecycle. A verdict, reaction, or `Pursue` intent cannot imply `application_submitted`.
7. Reaction behavior comes from `docs/superpowers/specs/2026-07-06-vacancy-reaction-triggers-design.md`; Product Search changes correlation and delivery control, not the approved 🔍/👍 actions.
8. Feedback behavior comes from `docs/negative_feedback_loop_scoring_calibration_prd.md`, except failures may never create a new root in the protected channel.
9. Every typed message names one recognized authority ID. Unknown or conflicting authority suppresses delivery and records an operational error outside the product channel.
10. Runtime/audit documents are evidence, not message authority.

## Current protected-channel baseline

The prior read-only Slack audit of `C0B4MM6D52A` over 45 days found:

| Current traffic | Top-level messages |
|---|---:|
| Individual vacancy cards | 487 |
| Daily Executive Review | 45 |
| System Health Warning | 45 |
| Weekly Source Quality | 7 |
| Strategic reports | 3 |
| Other top-level traffic | 47 |
| **Total** | **634** |

There were also 336 thread replies. The Job Intel database accounted for only 104 sent `vacancy_card` notifications in the same window. Formatter replacement alone therefore cannot close all send paths.

## Target product-channel allowlist

The channel is closed to these ten application message kinds:

| `message_kind` | Placement | Cadence/cap | Authority |
|---|---|---|---|
| `daily_digest` | Root | One per active Monday–Friday Almaty date; 5–7 when supply qualifies, fewer or zero otherwise; 35/week hard cap | Product SoT §10.1 |
| `urgent_exception` | Root | Default zero; max one/Almaty date; externally evidenced urgency | Product SoT §10.3 |
| `weekly_market_company_review` | Root | One per completed review week | Product SoT §10.4 |
| `monthly_strategy_review` | Root | One per completed review month | Product SoT §10.5 |
| `opportunity_detail` | Thread reply | One canonical detail per delivered item/assessment | Product SoT §10.2 |
| `review_detail` | Thread reply | Bounded support under its weekly/monthly root | Product SoT §§10.4–10.5 |
| `user_decision_prompt` | Item-correlated thread interaction/reply | At most one unresolved prompt per user/item | Product SoT §11 |
| `user_decision_ack` | Item-correlated thread interaction/reply | Idempotent acknowledgement | Product SoT §11 |
| `vacancy_evaluation` | Item-correlated thread reply | Approved 🔍 trigger only | Reaction-trigger SoT |
| `application_package` | Item-correlated thread reply/files | Approved 👍 trigger only | Reaction-trigger SoT |

No health report, source-search update, KPI dump, standalone vacancy card, generic Hermes answer, test message, manual free-form send, or legacy report is allowed as a root or reply.

## Direct Hermes Slack architecture

```text
Search Contract v1
       ↓
existing scrapers (unchanged) → immutable run/query/raw evidence
       ↓
canonical vacancy → Semantic snapshot → company evidence
       ↓                                  ↓
Candidate Facts/Profile hash → provider-assisted evidence synthesis
       └──────────────────────────────┬───┘
                                      ↓
                   deterministic Decision v2 policy
                                      ↓
                 immutable assessment + funnel projection
                                      ↓
                  portfolio reservation + renderer
                                      ↓
                    transactional Product Search outbox
                                      ↓
             Hermes gateway ProductSearchSlackPublisher
                                      ↓
             guarded Slack Web API + persisted receipt
                                      ↓
                            C0B4MM6D52A

Slack reaction / thread reply / Block Kit action
       ↓ same Hermes Slack app + Socket Mode
validated Product Search interaction handler
       ↓ user/action ledger + outbox
ProductSearchSlackPublisher → correlated thread reply

generic SlackAdapter.send / standalone sender / legacy Job Intel / manual tool
       ─X→ shared protected-channel guard + structured denial log
```

Job Intel scheduled services create product state and outbox rows but do not receive Slack credentials. The long-running Hermes gateway already owns `SLACK_BOT_TOKEN` and `SLACK_APP_TOKEN`; it gains one Product Search outbox consumer/publisher. The generic adapter keeps serving other Slack conversations, but protected-channel writes require a validated typed Product Search envelope.

The live adapter and standalone sender are separate real code paths. Both must call one shared protected-channel policy. Standalone delivery to `C0B4MM6D52A` is always denied; there is no fallback when the gateway is unavailable. Pending Product Search outbox rows wait for the gateway.

## Typed Slack delivery contract

`ProtectedChannelEnvelopeV1` is closed and immutable. It contains:

- `message_kind` from the ten-kind allowlist;
- `authority_id` and authority-manifest hash;
- `outbox_id`, deterministic `transport_request_id`, `correlation_id`, and payload hash;
- `reporting_period_id` or `delivery_item_id` plus immutable `assessment_id` where applicable;
- `placement=root|thread` and `parent_delivery_id` for replies;
- renderer/profile/policy versions and Almaty reporting date;
- accessible fallback text, validated Block Kit blocks, and controlled artifact references;
- no arbitrary target: the publisher supplies `C0B4MM6D52A` server-side.

The publisher adds Slack message metadata containing only opaque correlation identifiers and the message kind—never vacancy text, user notes, Candidate Facts, secrets, or application content. This metadata supports timeout reconciliation and protected-channel audits. Slack documents that message metadata is workspace-visible, so private content is prohibited.

Thread placement resolves only from a successfully persisted parent root receipt. A missing or ambiguous parent fails closed; a reply never falls back to a root. Artifacts must live in an allowlisted content-addressed spool, match basename/type/size/SHA-256, and be deleted under a bounded retention policy; arbitrary paths and URLs are forbidden.

## Delivered semantics

- A successful daily or urgent **root** receipt advances every included vacancy to funnel stage 7 and consumes its weekly slot because the root already shows its compact decision content.
- A failed or ambiguous root does not advance items until reconciliation proves presence.
- `root delivered + opportunity_detail failed` keeps the item delivered, keeps the slot consumed, leaves the detail retryable, and forbids the item from appearing in another daily root.
- Weekly/monthly roots are delivered on their own successful receipt; supporting detail failures do not roll the root back.
- Slack acceptance without a persisted `channel`/`ts` receipt is ambiguous, not success.

## Normative product contracts

### Canonical nine-stage funnel

1. `raw_observed`
2. `canonical_current`
3. `minimum_evidence_sufficient`
4. `hard_gate_eligible`
5. `portfolio_reviewed`
6. `selected`
7. `delivered`
8. `user_decision_recorded`
9. `concrete_action_completed`

`current_stage` is a monotonic projection only. Append-only evidence is authoritative. Stages 8 and 9 come from explicit Product Search user/action events. CRM lifecycle remains independent.

### Gate A provisional prefilter

Gate A measures canonical stages 1–3 only. It may additionally label an evidence-sufficient candidate:

```text
provisionally_eligible
known_hard_block
unresolved_for_decision_v2
```

This label is not funnel stage 4, is not persisted as a Decision v2 verdict, and cannot be used for delivery. Gate A may estimate a likely stage-4 range through a named sample audit, but canonical stage 4 begins only after Task 11 policy and is validated in Gate B/shadow.

### Closed product vocabularies

```text
SelectionMode = Core | Exploration

watchlist_status = candidate | active | deprioritized | rejected | expired
review_state = current | review_due
company_action = nominate | promote | retain | deprioritize | reject | expire
```

Imported legacy companies start as `candidate` with no action. `promote` moves a candidate to `active`; `retain` applies only to an already-active thesis; `deprioritize`, `reject`, and `expire` produce their exact statuses. No owner decision means no action and the company stays `candidate`. `nominate` is only for a previously absent company.

### Search cells and source independence

Search Contract v1 retains the eight exact SoT lanes:

- Europe including the UK, with UK, DACH, Benelux, Nordics, CEE, and remaining agreed cells;
- APAC excluding Australia/New Zealand, split into agreed country/sub-region cells;
- GCC country cells;
- Americas, separating Canada, Latin America, and the US feasibility case;
- Global remote only for genuinely location-independent mandates;
- Australia and New Zealand as independent country cells;
- Kazakhstan as a normal eligible market with no minimum or fallback status;
- every other Central Asian country independently.

Multiple queries against one backend are one source family. A lane is meaningfully observed only when its locked cell/family attempts satisfy the contract; otherwise it is truthfully `blocked`, `not_observed`, or `searched_no_qualified_results`.

### Decision v2 boundary

Provider-assisted code may extract, normalize, compare, and cite evidence for Feasibility, Mandate Fit, Company Fit, Transferability, Career Value, and Evidence Confidence. It may not make the authoritative hard-gate, verdict, action, urgency, or delivery decision.

Deterministic policy emits:

- structured conclusions for all six dimensions;
- `SystemVerdict = Priority | Investigate | Save | Reject`;
- `SelectionMode = Core | Exploration`;
- a closed `RecommendedActionKind` plus optional bounded-question ID;
- independent `company_action`;
- named gate/rejection reasons, delivery eligibility, urgency eligibility, versions, hashes, and trace.

Unknown is a first-class value. Provider/schema failure creates no deliverable assessment and never falls back to the legacy evaluator.

### Attention and north-star accounting

An attention session records user, review batch, start/end interaction IDs and timestamps, state, measured seconds, proxy version if any, and interruption/abandonment status. At most one open session exists per user/review surface. Incomplete sessions remain unknown.

```text
activated_opportunities_per_60_review_minutes
  = activated_opportunity_count * 60 / actual_completed_review_minutes
```

`Pursue` or `Investigate` is a positive decision. Activation additionally requires a completed research, feasibility, outreach, referral, networking, or application action. Machine verdicts never enter the numerator.

## Slack interaction constraints

Hermes already uses Slack Bolt with Socket Mode. The same app receives Events API and interactive payloads over its WebSocket connection. Product Search action handlers must:

- register namespaced action IDs through the existing Slack action-handler extension point or an equally bounded adapter hook;
- acknowledge valid interaction payloads immediately and perform durable work after acknowledgement;
- validate app/team/channel/user, root/item/assessment mapping, payload/action ID, and replay identity;
- never use `response_url`, `say`, or an ad hoc `chat.postMessage` as a visible-response bypass;
- record the user/action event and enqueue any visible acknowledgement, evaluation, or package for the typed publisher;
- preserve 🔍/👍 actions without automatically mapping them to `Investigate`/`Pursue` or mutating CRM.
- treat `Start review` and `Done` as silent protocol acknowledgements that only persist attention state; they do not call `chat.update`, create a reply, or add another message kind;
- rely on an authenticated Socket Mode envelope and Bolt context—not an HTTP request-signature claim—then validate app/team/channel/user and replay identifiers;
- never run old and candidate gateway processes/runtime authorities beside each other with the same app token. SDK-managed reconnect overlap is allowed only inside the one pinned gateway process/revision/state boundary and must remain replay-safe because Slack may route a payload to any active connection.

## Gateway-owned Product Search runtime contract

Publisher, Product Search interaction processing, and protected-channel reconciliation are three independently switchable components under one shared fail-closed runtime gate. Configuration defaults to:

```yaml
product_search_runtime:
  environment: disabled
  state_namespace: none
  publisher_enabled: false
  interactions_enabled: false
  reconciler_enabled: false
```

Each enabled component requires the exact expected environment, DB/migration and component schema versions, authority-manifest hash, Slack app/team identity, server-side channel plus verified conversation type, state namespace, pinned gateway commit/runtime identity, and an allowed cutover-lock state. Absence or mismatch starts no component work, opens no Product Search DB, makes no Slack Web API call, and exposes a named privacy-safe disabled/mismatch state.

When interactions are disabled, only a minimal namespaced protocol interceptor may acknowledge an already-routed Product Search interactive envelope to prevent Slack retries. It may validate the authenticated envelope and emit a bounded operational state, but it cannot open Product Search state, infer an item, mutate data, or create a visible response. Reactions and thread messages on legacy/untracked Slack receipts are ignored by Product Search.

Enable or disable transitions are startup transitions, never magical in-process toggles:

1. stage and validate the complete config, state namespace, schemas/hashes, and next cutover-lock state;
2. stop the current gateway and prove its process/runtime authority is gone;
3. atomically install the validated config and compare-and-swap the cutover lock;
4. start exactly one gateway process at the pinned revision;
5. prove the expected component states, runtime identity, inbound health, and zero unintended protected-channel calls before enabling any scheduler.

Gate D performs this sequence to enable staging components and repeats it to return all three components to disabled after staging. Production performs it only after live migration and no-delivery dry run. A separate runtime-control protocol is out of scope.

Official Slack references: [Socket Mode interactions, reconnects, and multiple-connection behavior](https://docs.slack.dev/apis/events-api/using-socket-mode/), [interaction acknowledgement and response rules](https://docs.slack.dev/interactivity/handling-user-interaction/), [`chat.postMessage` metadata/thread behavior](https://docs.slack.dev/reference/methods/chat.postMessage/), [full message metadata retrieval](https://docs.slack.dev/messaging/message-metadata/), bounded [`conversations.history`](https://docs.slack.dev/reference/methods/conversations.history/) and [`conversations.replies`](https://docs.slack.dev/reference/methods/conversations.replies/), public-channel [`message.channels`](https://docs.slack.dev/reference/events/message.channels/) versus private-channel [`message.groups`](https://docs.slack.dev/reference/events/message.groups/), and [the mandatory v2/external file-upload flow](https://docs.slack.dev/changelog/2024-04-a-better-way-to-upload-files-is-here-to-stay/).

## Global implementation constraints

1. **Worktree authoring:** Tasks 1–28 are authored and locally verified in the dedicated feature worktree; only the named Gate A/C/D/E checkpoints may integrate pinned commits into canonical `local/customizations` for owner-authorized production-host tests.
2. **TDD:** every code task begins with a failing test and records the expected failure.
3. **Tests before progression:** all focused tests must pass before the next task.
4. **Scraper freeze:** protected scraper files and production source configuration remain unchanged.
5. **Bounded live-test exceptions:** ordinary tests use fixtures, recorded provider responses, writable DB copies, and fake Slack clients. Only the explicitly approved Gate A/C experiments and Gate D staging may use production-host sources/runtime, after the legacy system is stopped and with isolated state under the experiment/staging contract.
6. **Immutable evidence:** corrections create new snapshots/supersession links; no evidence overwrite.
7. **No silent fallback:** provider, source, parent, transport, artifact, and authority failures remain named states.
8. **No product-channel ops alerts:** operational failures go to logs/Grafana or an approved operations destination.
9. **No generic protected-channel send:** `send_message_tool`, standalone sender, generic assistant replies, and legacy Job Intel cannot write to `C0B4MM6D52A`.
10. **No production test root:** the first production message is a legitimate scheduled product event.
11. **Owner gates:** rejected/incomplete Gate A–E stops execution and closes both checkpoint hold-state axes before another checkpoint begins.
12. **Scoped commits:** conventional commits, explicit file staging, unrelated worktrees preserved.
13. **Pinned experiment runtime:** Gate A/C units execute only the manifest-pinned export, interpreter, stdlib, locked dependencies, and import path; editable/shared mutable environments are rejected.
14. **Gateway components default off:** publisher, Product Search interactions, and reconciler remain independently false unless an explicit stop/config-lock/start transition passes every shared startup gate.

## Progress tracking

- Mark completed checklist items `[x]` immediately.
- Add discovered work as `➕` and blockers as `⚠️`.
- Update this plan when scope, dependencies, paths, or gate decisions change.
- A conditional task not required by its gate is marked `[x] N/A` with the gate-record reference; it is never silently skipped.

## Execution status reconciliation (2026-08-24)

This section is a **status index**, not a new authority. The cited immutable,
machine-readable evidence is authoritative, and a later superseding record wins
over an earlier one. The `[ ]` / `[x]` ledger below drifted from reality and is
not authority for gate state.

### Task-number legend — bare `Task N` is ambiguous and is no longer permitted

Three numbering systems coexist in the record:

- **Redesign Task N** — this plan's twenty-eight tasks.
- **legacy Gate B v3 operational Task N** — the numbering in
  `docs/evidence/product-search-gate-b/task8-immediate-checkpoint.*` and
  `task9-launch-attempt.*`. Its source document is not present in the current
  tree.
- **Gate B simplification Task N** — the numbering named by commits
  `761130767e` and `2780543019` in their own messages.

Historical evidence is not rewritten. Every future record must qualify which
system it uses; a bare `Task N` is forbidden.

### Gate A — closed (Redesign Task 6)

`docs/evidence/product-search-gate-a/gate-closure.json`: `decision: proceed`,
`gate_state: closed`, `authorized_continuation.task_number: 8`,
`authorized: true`, `exact_owner_approval: "Одобряю Gate A: proceed"`,
decision date 2026-08-16. Commits `3a93976a45`, `ca8f39e17d`.
`owner-decision.md` records an owner override replacing the calendar-duration
requirement with a snapshot-first evaluation: one complete broad-source run plus
a manual quality audit may close Gate A. Run `gate-a-20260816T141344Z`;
2,414 raw / 1,814 corrected canonical / 1,314 minimum-evidence. The 1,314
denominator is minimum-evidence sufficient, not qualified.

Hold state: `canonical_candidate: dormant`, `product_search_runtime: dormant`,
`legacy_job_intel: masked`. Restoring legacy Job Intel still requires a separate
owner approval, which does not exist as of this reconciliation.

### Redesign Task 7 — executed, then superseded

The same `owner-decision.md` records an earlier decision of 2026-08-15,
`bounded_additive_source`, which required Task 7 and a repeat of Tasks 5–6. The
browser-native repair and the broadened snapshot were then carried out, and the
2026-08-16 `proceed` superseded that decision. Task 7 was therefore **required
and executed**, not skipped; its per-item ledger is unreconciled. It is not
marked `[x] N/A`.

### Redesign Tasks 8–11 — artifacts landed, ledger unreconciled

Implementation artifacts for the intended Task 8–11 outcomes have landed:
contracts, company evidence, bounded provider-assisted synthesis, and
deterministic Decision v2 exist in code and are exercised by the Gate B
readiness package. The per-item ledger was never reconciled, and **this section
does not infer completion of any individual checklist item or acceptance
criterion**. `docs/evidence/product-search-gate-b/README.md` is historical, and
`traceability-current-v2.md` is explicitly marked pre-deletion and still lists
partial, broken and unreachable entries. Open issues #9 and #10 show the live
composition is not confirmed. No box in Tasks 8–11 may be ticked without a
one-to-one audit against evidence.

### Redesign Task 12 / Gate B — incomplete

Task 12 remains incomplete. The historical Gate B v3 launch attempt of
2026-08-21T13:38:18Z failed before `ExecStartPre` with `226/NAMESPACE` and
recorded `recommendation: request_revision` in
`docs/evidence/product-search-gate-b/owner-decision.md`
(`owner_decision: pending`, `task_13_authorized: false`). Provider outcome was
`0/48` calls and `USD 0.00`. **No benchmark result and no current owner verdict
exist.**

That attempt is not retryable under its own at-most-once policy, but the
machinery it depended on has since been retired: `2897d401c0` retired the
at-most-once launch fortress, `1bc639f7c1` retired the unattended machinery, and
`2780543019` replaced every `ReadWritePaths` entry with `StateDirectory`,
removing both verified `226/NAMESPACE` causes and deleting receipt plumbing,
the `EnvironmentFile`, and the approval window with the protocol they served.
A future run therefore needs a **new reviewed manifest, corpus, runtime and
spend authorization** — not a resurrected v3 receipt, checkpoint or window.

---

## Current execution order (agreed 2026-08-24)

Three initiatives stand between here and a live Gate B verdict. They are ordered
by causality, not importance. Nothing below re-opens Gate A.

### Order 1: Restore a trustworthy live Gate B composition

Internal order is causal and may not be permuted.

1. **Seal the authority record after enrichment, not before.**
   `_AuthorityRecordingStore` (`job_intel/product_search/gate_b_evidence_runner_v1.py`)
   adds five authority fields after `capability.seal_record(...)`, on both
   `save`/`save_exclusive` and `load` (`:1414`), so the stored record no longer
   matches its own seal and `RecordingStore.load` rejects it as corrupt. The
   authority fields also sit outside the seal and are not covered by HMAC.
   Tracked as issue #9.
2. **Carry one row from `dispatch` through `recordings.verify`.**
   Four independent blockers: the redacted `provider_payload` is validated as a
   full `EvidenceSynthesisInputV2`; the ledger and the recording require one SHA
   from two different pieces of evidence; the keyed verifier is disabled by
   removing the discriminator it is meant to protect; a publish failure after
   ledger commit leaves a paid outcome with no decision and no resume path.
   Tracked as issue #10; red test
   `tests/product_search/test_gate_b_full_composition_e2e.py`.
3. **Make the built-artifact smoke prove the composition.**
   `tests/product_search/gate_b_cli_smoke_fixture.py` creates no spend record, so
   `scripts/gate_b_composition_smoke.py` dies at `spend_record_missing` before any
   work; timeouts do not account for artifact build time. Tracked as issue #8.
   **Definition of done includes binding the smoke to the single corpus authority
   defined in Order 2** — it must read and verify that authority, not a hardcoded
   SHA, which would go stale again after the Order 2 rebuild.

Rationale for the position: this is the only order that needs neither new data
nor an owner decision, and until one row completes the live path, no benchmark
can exist at all. A corpus defect makes a benchmark wrong; this defect makes it
nonexistent.

### Order 2: Single corpus authority and valid coverage

1. **Name one canonical corpus authority and prove the whole live path consumes
   it.** The pin is currently split: `5b8e29b0…` in
   `job_intel/product_search/gate_b.py:124` and
   `docs/evidence/product-search-gate-b/v3-fragment-allowlist.yaml`, while
   `b1db802d…` remains in `job_intel/product_search/input_materialization.py:45,54`,
   in `docs/evidence/product-search-gate-b/benchmark-summary.json`, in the Gate B
   README and owner-decision front matter, and in
   `tests/product_search/gate_b_cli_smoke_fixture.py:37` plus six other tests.
   The fix is a machine-readable authority that consumers read and verify — not
   one constant replaced by another.
2. **Record a representativeness contract.** The rebuilt corpus
   (`715c334cf1`, selection
   `deterministic-content-eligible-coverage-first-stratified-round-robin-v2`,
   `eligible 1193 / excluded 121`, min description > 500) removed the SERP and
   aggregator noise, and with it the geographic coverage. Measured composition:
   families `greenhouse 28 / ashby 19 / remoteok 1`; lanes `global_ats 47` and
   `global_remote 1`; eight companies; seven of the eight Search Contract lanes
   and `chief_product` unrepresented. A product verdict on that sample is not
   generalisable without an explicit stated limitation. The manifest itself sets
   `repeat_after_collection_fixes: true`.
3. **Recover coverage at the collector.** SmartRecruiters evidence stores an
   empty description although the already-fetched detail response carries
   `jobAd.sections` (issue #5): 500 unique records, currently the exact
   difference between `canonical_current 1814` and
   `minimum_evidence_sufficient 1314`. A fix therefore *potentially* restores up
   to 500 rows, up to +38 % of the selection denominator, **after revalidation** —
   the arithmetic bounds the ceiling and does not prove that every repaired row
   passes the remaining eligibility predicates. LinkedIn evidence stores the
   title as the description (issue #4): only 7 unique records after
   canonicalisation, so its value is independent non-ATS coverage rather than
   volume, and the effort must stay proportional to seven rows.
   **Both fixes must be additive, outside the protected scraper files.** Neither
   issue authorizes relaxing global constraint 4 or the protected-path freeze; if
   the evidence cannot be recovered additively, stop and obtain a separately
   approved protected-path or SoT amendment.
4. **Rebuild the corpus and repeat the smoke on the final authority.**

Rationale for the position: strictly between Order 1 and Order 3. The pipe must
be able to execute for a benchmark to happen; the sample must be valid for its
result to mean anything.

### Order 3: Benchmark Decision v2 and take the owner decision

Run the live benchmark on the corpus from Order 2, produce the six-dimension
audit, deterministic replay, provider-failure behaviour, and cost/latency
evidence, complete the human audit, and record an explicit
`approve` / `revise` / `stop`. Only `approve` authorizes Redesign Task 13.

This is not a rerun of the 2026-08-21 attempt, which its own at-most-once policy
forbids retrying. The current supervised path requires a **new reviewed
manifest, corpus, runtime and spend authorization**; the receipt, checkpoint and
approval-window protocol that attempt used no longer exists, and the two
`226/NAMESPACE` causes were removed by `2780543019` rather than patched.

Rationale for the position: it consumes the output of Orders 1 and 2 and cannot
physically precede them. Launched early, it returns a verdict on an input that
is either unexecutable or unrepresentative.

### Discovered work

➕ **Corpus authority is split and corpus representativeness is unrecorded.**
Found 2026-08-24 during this reconciliation; no issue exists for it yet. Both
are folded into Order 2 above. If Order 2 is deferred, this finding must be
filed separately rather than carried in this plan alone.

### Out of scope of these three, tracked separately

- Publication of canonical work to `origin` — reconciled 2026-08-24, pushed
  `0250564db6..e06e3aceeb`; `local/customizations...origin` is now `0 0`.
- Restoring legacy Job Intel from its recorded `masked` hold — owner decision,
  still absent.
- Issues #2, #3, #6 — not blockers for the current Gate B execution order. #6
  arose in company-evidence tests and is topically related, but it does not
  block the live composition.


## Phase gates

| Gate | Required evidence | Owner decision |
|---|---|---|
| **Gate A — acquisition viability** | Post-rebase approved commit/manifest, pinned canonical checkpoint and immutable code/Python runtime export, temporary scheduled runner/teardown, isolated source/browser state, 7–14-day stages 1–3 report, provisional prefilter, cell/family evidence, blocked states, new-company candidates, sample-audited likely stage-4 range, cost/latency | `proceed`, `bounded_additive_source`, `sot_amendment_required`, or `stop`; non-proceed also closes both hold axes |
| **Gate B — Decision v2 readiness** | Real-vacancy benchmark, six-dimension audit, invariants, deterministic replay, provider failure behavior, cost/latency | Approve pinned Decision v2 for shadow, revise, or stop |
| **Gate C — product shadow/attention** | Post-rebase approved commit/manifest, pinned canonical checkpoint and immutable code/Python runtime export, temporary scheduled runner/teardown, isolated experiment state, integrated pipeline, offline daily/urgent/weekly/monthly root/detail artifacts, watchlist bootstrap, 1–2-week shadow, attention prototype, no Slack delivery | Approve UX/attention for staging, revise, or stop; non-proceed also closes both hold axes |
| **Gate D — direct Slack staging** | Post-rebase approved candidate, one pinned gateway runtime authority, separate staging DB/outbox/spool/ledgers, all production components default-off/channel denied, same-app Socket Mode interactions, type-derived Slack scope/event manifest, roots/replies/files, receipts, timeout reconciliation, generic/standalone denial, no production-channel writes | Approve dormant canonical deployment package, revise, or stop; non-proceed also closes both hold axes |
| **Gate E — production cutover** | Post-rebase approved dormant candidate, publisher/interactions/reconciler default-off, DB-copy rehearsal, service/unit inventory, two-plane deny proof, rollback-silence proof, exact backup/config/runtime/revision set | Explicitly authorize production window or close both hold axes |

## Target file map

Task 1 must reconcile expected paths against the real feature worktree before editing.

### New Product Search modules

- `job_intel/product_search/contracts.py`
- `job_intel/product_search/baseline.py`
- `job_intel/product_search/search_contract.py`
- `job_intel/product_search/acquisition_audit.py`
- `job_intel/product_search/acquisition_probe.py`
- `job_intel/product_search/company_evidence.py`
- `job_intel/product_search/evidence_synthesis.py`
- `job_intel/product_search/decision_v2.py`
- `job_intel/product_search/store.py`
- `job_intel/product_search/funnel.py`
- `job_intel/product_search/watchlist.py`
- `job_intel/product_search/portfolio.py`
- `job_intel/product_search/renderers.py`
- `job_intel/product_search/attention.py`
- `job_intel/product_search/metrics.py`
- `job_intel/product_search/pipeline.py`
- `job_intel/product_search/commands.py`
- `job_intel/product_search/delivery_intent.py`
- `job_intel/product_search/outbox.py`
- `job_intel/product_search/slack_contract.py`
- `job_intel/product_search/interactions.py`

### Hermes Slack integration

- `plugins/platforms/slack/product_search_runtime.py`
- `plugins/platforms/slack/product_search_publisher.py`
- `plugins/platforms/slack/product_search_reconciler.py`
- `plugins/platforms/slack/protected_channel_policy.py`
- focused updates to `plugins/platforms/slack/adapter.py`
- focused updates to `tools/send_message_tool.py` and Slack standalone sender integration
- existing plugin action registration mechanism in `hermes_cli/plugins.py` where reusable

### Configuration, deployment, and evidence

- `config/product_search/*.yaml`
- `config/product_search/slack_scope_manifest.v1.yaml`
- versioned Job Intel migrations and `job_intel/store.py` integration
- `deploy/systemd/experiments/job-intel-product-search-probe-experiment.*`
- `deploy/systemd/experiments/job-intel-product-search-shadow-experiment.*`
- `deploy/systemd/job-intel-product-search-daily.*`
- `deploy/systemd/job-intel-product-search-urgent.*`
- `deploy/systemd/job-intel-product-search-weekly.*`
- `deploy/systemd/job-intel-product-search-monthly.*`
- gateway Product Search publisher/interactions/reconciler configuration and cutover-lock transition
- `scripts/job_intel_product_search_*.sh`
- focused `tests/product_search/`, `tests/gateway/`, `tests/deploy/`, and `tests/acceptance/`
- `docs/evidence/product-search-*` and cutover runbook

### Protected read-only scraper paths

- `job_intel/sources.py`
- `job_intel/ats_sources.py`
- `job_intel/browser_sourcing.py`
- `job_intel/browser_worker.py`

---

## Phase 0 — Isolated workspace, authority, and baseline

### Task 1: Create/resume the feature worktree and pin authority

**Purpose:** establish an isolated, reproducible execution context and machine-readable authority before any behavior change.

**Files:**

- Create: `docs/authority-manifest.yaml`
- Create: `docs/product-search-impact-analysis.md`
- Create: `scripts/check_product_search_scope.sh`
- Create: `tests/product_search/test_authority_manifest.py`
- Create: `tests/product_search/test_scope_guard.py`

**Checklist:**

- [x] Recheck canonical root, branch, HEAD, `git status --short --untracked-files=all`, `git worktree list --porcelain`, protected stashes, and current `.worktrees/` ignore rule on `ssh hermes-agent`.
- [x] Detect an existing `/home/hermes/.hermes/hermes-agent/.worktrees/job-intel-product-search`; verify it is a linked worktree on `codex/job-intel-product-search` and resume it only if clean/understood.
- [x] If absent, create branch `codex/job-intel-product-search` from the rechecked `local/customizations` HEAD and add the ignored linked worktree at the exact required path.
- [x] Install/reuse project dependencies within the worktree and run the repository's focused Job Intel/Slack baseline suites before edits; record failures and stop for owner direction if baseline is not green.
  - ➕ Owner decision 2026-08-11: continue with the exact `36 failed, 1273 passed, 15 warnings` result recorded as a known-red baseline; this is not a waiver for new Product Search failures.
  - ➕ Post-change control rerun: the same 36 failing node IDs and 1273 passes; warning aggregation varied to 16 within the same unawaited Slack coroutine family.
- [x] Write failing tests requiring SoT ID/version/status/hash, authority scope, precedence, supersession, recognized message kinds, parallel authorities, unresolved conflicts, and fail-closed behavior.
- [x] Create the manifest and impact analysis across Candidate Facts/Profile, Semantic, Decision v1/v2, CRM, search policy, feedback/reactions, Slack adapter, schedulers, metrics, and dashboards.
- [x] Implement the scope guard to assert execution root/branch, record the current base commit/hashes, and reject feature-branch changes to the four protected scraper files or production source configuration relative to the current merge-base unless a separately recorded amendment explicitly authorizes them.
- [x] Add a rebase-checkpoint test proving an upstream-only protected-file change is recorded/repinned after review but is not misattributed to the Product Search feature diff.
- [x] Run authority/scope tests, focused baseline tests, ruff, and `git diff --check`; all must pass before commit, except the owner-accepted exact known-red baseline recorded above.

**Acceptance criteria:**

- All development happens in the named linked worktree, not the production checkout.
- The final SoT hash is pinned and every allowed Slack message kind maps to recognized authority.
- Protected scraper files have baseline hashes tied to a base commit; the scope guard fails on a feature fixture change and survives a reviewed upstream-only rebase.
- The canonical runtime checkout remains clean and untouched.

**Verification:**

```bash
pytest -q tests/product_search/test_authority_manifest.py tests/product_search/test_scope_guard.py
bash scripts/check_product_search_scope.sh
bash -n scripts/check_product_search_scope.sh
ruff check tests/product_search
git diff --check
```

**Commit:** `chore(job-intel): establish product search worktree contract`

### Task 2: Capture immutable acquisition, delivery, and attention baselines

**Purpose:** preserve comparable pre-pilot denominators and enumerate every current protected-channel path without retaining private Slack content in Git.

**Files:**

- Create: `job_intel/product_search/baseline.py`
- Create: `tests/product_search/test_baseline.py`
- Create: `docs/evidence/product-search-baseline/README.md`
- Create: `docs/evidence/product-search-baseline/baseline-summary.json`

**Checklist:**

- [x] Write failing fixture tests for unique delivery, user decisions, completed actions, actual review minutes, duplicates, company identity, and `activated * 60 / actual_minutes`.
- [x] Add edge tests: unknown/zero minutes are non-computable; unfinished sessions are not zero; machine verdicts are not outcomes; repeated delivery is counted once.
- [x] Implement a bounded read-only extractor that emits aggregate data and source snapshot hashes only—no message bodies, tokens, application artifacts, Candidate Facts text, or user notes.
- [x] Capture the 45-day Slack category counts, DB-accounted sends, root/reply totals, legacy unit/cron inventory, current bot identity/scopes, and current production flags without posting.
  - ➕ The immutable planning audit retains 104 DB-accounted cards; the later live snapshot replay returned 103. Both observations and their evidence hashes are preserved without rewriting history.
- [x] Inventory and classify each outbound code path as `typed_product_search` (currently none), `live_adapter_generic`, `standalone_sender`, `webhook`, `raw_slack_api`, `legacy_job_intel`, or `unknown`.
- [x] Capture current reaction/thread/action routing and confirm that the same Hermes Slack app receives them over Socket Mode.
- [x] Determine whether historical attention is comparable; when it is not, record `not_computable` and a prospective baseline period instead of imputing 60 minutes.
- [x] Run tests, scope guard, ruff, and redaction inspection; all must pass before commit.

**Acceptance criteria:**

- The 634-root/336-reply baseline and 104 DB-accounted cards are reproducible without copied private content.
- Live adapter, standalone sender, and raw/direct paths are not conflated.
- Historical outcomes without compatible attention evidence are explicitly non-comparable.
- The baseline can later prove whether legacy send attempts and unauthorized messages stopped.

**Verification:**

```bash
pytest -q tests/product_search/test_baseline.py
ruff check job_intel/product_search/baseline.py tests/product_search/test_baseline.py
bash scripts/check_product_search_scope.sh
git diff --check
```

**Commit:** `feat(job-intel): establish product search baseline`

## Phase 1 — Existing-scraper acquisition proof

### Task 3: Audit and lock existing scraper capabilities

**Purpose:** describe what the already-implemented scrapers can observe without modifying them or confusing several queries against one backend with independent breadth.

**Files:**

- Create: `job_intel/product_search/acquisition_audit.py`
- Create: `config/product_search/source_capabilities.v1.yaml`
- Create: `tests/product_search/test_acquisition_audit.py`
- Inspect only: the protected scraper files, current seeds/config, wrappers, and scheduled invocations

**Checklist:**

- [x] Write failing schema tests for source ID/family, public invocation interface, seed dependency, supported query/geography controls, freshness, auth/anti-bot state, evidence completeness, limits, and failure independence.
- [x] Add tests proving multiple query variants against one backend remain one family and a known ATS tenant collector does not independently prove broad-market discovery.
- [x] Inventory current collectors from actual invocation paths and populate `proven`, `partial`, `blocked`, or `unknown` with code/runtime evidence and inspection timestamp.
- [x] Produce a matrix by search cell, mandate vocabulary, industry/business model, and independent family; name capability gaps without changing scraper code/config.
- [x] Add drift tests for unregistered live sources and stale registry pointers.
- [x] Run the scope guard and prove protected scraper hashes still match Task 1.
- [x] Run tests and ruff; all must pass before commit.

**Acceptance criteria:**

- Existing source capabilities and successful acquisition are separate concepts.
- Known ATS/seed bias and coarse coverage are visible without claiming the scrapers are broken.
- No protected scraper or production source configuration changes.
- Gaps are inputs to Gate A, not permission for speculative scraper work.

**Verification:**

```bash
pytest -q tests/product_search/test_acquisition_audit.py
ruff check job_intel/product_search/acquisition_audit.py tests/product_search/test_acquisition_audit.py
bash scripts/check_product_search_scope.sh
git diff --check
```

**Commit:** `feat(job-intel): audit existing acquisition capabilities`

### Task 4: Define and lock Search Contract v1

**Purpose:** make search cells, query vocabulary, source breadth, freshness, and observability states testable before the real probe.

**Files:**

- Create: `config/product_search/search_contract.v1.yaml`
- Create: `job_intel/product_search/search_contract.py`
- Create: `tests/product_search/test_search_contract.py`
- Update: `docs/authority-manifest.yaml`

**Checklist:**

- [x] Write failing tests for the eight exact lanes and their required cells, including Kazakhstan as normal and each other Central Asian country independently.
- [x] Add tests for mandate/role vocabulary, transferable patterns, industry families, primary business models, Open Market/Watchlist origin, `Core|Exploration`, freshness, minimum evidence, and observability states.
- [x] Define independent-family attempt evidence and distinguish `searched_no_qualified_results`, `blocked`, and `not_observed`.
- [x] Encode ceilings/diagnostic ranges—never minimum-fill rules—including 35/week, Exploration range, employer/fintech concentration, and no geographic delivery quota.
- [x] Validate that every search cell has an invocation plan through an existing public scraper interface or an explicit named capability gap from Task 3.
- [x] Version/hash the contract as technical execution policy subordinate to Product SoT v1.
- [x] Run tests, scope guard, and ruff; all must pass before commit.

**Acceptance criteria:**

- A broad lane cannot be declared observed from one convenient country/backend.
- Discovery origin and selection mode are independent.
- The only selection-mode vocabulary is `Core|Exploration`.
- No existing scraper or production source configuration is changed.

**Verification:**

```bash
pytest -q tests/product_search/test_search_contract.py
ruff check job_intel/product_search/search_contract.py tests/product_search/test_search_contract.py
bash scripts/check_product_search_scope.sh
git diff --check
```

**Commit:** `feat(job-intel): define product search contract`

### Task 5: Build the isolated acquisition probe over existing scrapers

**Purpose:** exercise real breadth through current scraper interfaces without touching production state or pretending Decision v2 already exists.

**Files:**

- Create: `job_intel/product_search/acquisition_probe.py`
- Create: `scripts/job_intel_product_search_probe.sh`
- Create: `scripts/job_intel_product_search_experiment.sh`
- Create: `scripts/export_job_intel_product_search_experiment.sh`
- Create: `deploy/systemd/experiments/job-intel-product-search-probe-experiment.service`
- Create: `deploy/systemd/experiments/job-intel-product-search-probe-experiment.timer`
- Create: `tests/product_search/test_acquisition_probe.py`
- Create: `tests/deploy/test_product_search_experiment_runner.py`

**Checklist:**

- [x] Write failing tests for deterministic query expansion, per-cell/family attempts, content-addressed evidence bundles, identity hints, freshness, bounded retries, rate limits, and named auth/anti-bot failures.
- [x] Add safety tests proving no production DB write, no portfolio reservation, no Slack call, no production source-config mutation, and no protected scraper edit.
- [x] Write failing experiment-runner tests for an immutable tracked-file export plus dedicated read-only venv: pinned interpreter/stdlib/dependency-lock/installed-distribution/import-root/`sys.path` hashes, empty editable-install set, gate/environment identity, separate DB/evidence/log/lock/profile/cache/tmp paths, missed/overlap runs, teardown, and rejection of shared mutable venvs, every Slack credential, and every production path.
- [x] Define one portable immutable evidence-package format shared with Task 13: run/query/source IDs, raw content hash/reference, capture/parser/source versions, timestamps, and redaction class.
- [x] Invoke only existing public scraper interfaces and store results in a separate probe directory/DB; never patch adapters from inside the probe.
- [x] Canonicalize/deduplicate for measurement while preserving raw observations and query/source provenance.
- [x] Implement only the Gate A provisional prefilter: `provisionally_eligible`, `known_hard_block`, or `unresolved_for_decision_v2`; do not emit stage 4, verdict, or delivery eligibility.
- [x] Produce daily stages 1–3 counts, provisional labels, source/cell states, duplicates, cost, and latency.
- [x] Package the temporary `User=hermes` service/timer/export wrapper so it creates `runtime/` from the already-integrated canonical commit, builds `python-runtime/venv` with a copied interpreter and non-editable locked dependencies, executes only that code/runtime pair with `PYTHONNOUSERSITE=1`/`PYTHONDONTWRITEBYTECODE=1`, and fails closed on bundle/Python/dependency/import/config/source drift.
- [x] Add browser-profile clone/validation and source-level exclusive-lock modes; a source with neither safe mode must return `blocked` without invocation.
- [x] Run fixture/fake-source smoke tests, systemd syntax checks, scope guard, and ruff before commit. Do not invoke a real browser/source until Task 6 has stopped the legacy collectors.

**Acceptance criteria:**

- The probe creates observability evidence rather than querying only existing production rows.
- Every claim traces to query, source family, time, and content hash.
- Stage 4 is never claimed before Decision v2.
- Existing scrapers/config remain byte-for-byte unchanged.
- The multi-day runner is reproducible across code and Python/dependency identity, Slack-blind, production-state-isolated, and not executable from the worktree or a shared mutable venv.

**Verification:**

```bash
pytest -q tests/product_search/test_acquisition_probe.py tests/deploy/test_product_search_experiment_runner.py
bash -n scripts/job_intel_product_search_probe.sh scripts/job_intel_product_search_experiment.sh scripts/export_job_intel_product_search_experiment.sh
ruff check job_intel/product_search/acquisition_probe.py tests/product_search/test_acquisition_probe.py
bash scripts/check_product_search_scope.sh
git diff --check
```

**Commit:** `feat(job-intel): probe existing acquisition breadth`

### Task 6: Run the 7–14-day probe and close Gate A

**Purpose:** make a real stop/go decision on market observability without smuggling Decision v2 or scraper rewrites into acquisition proof.

**Files:**

- Create: `tests/acceptance/test_acquisition_gate.py`
- Create: `docs/evidence/product-search-gate-a/README.md`
- Create: `docs/evidence/product-search-gate-a/coverage-summary.json`
- Create: `docs/evidence/product-search-gate-a/experiment-manifest.yaml`
- Create: `docs/evidence/product-search-gate-a/teardown-record.md`
- Create: `docs/evidence/product-search-gate-a/owner-decision.md`

**Checklist:**

- [x] Write failing acceptance assertions for pinned-commit scheduled attempts, missed/overlap behavior, isolated state/profile/locks, evidence hashes, family accounting, cell-state closure, stages 1–3, provisional prefilter, new-company candidates, cost/latency, and zero live-DB/Slack/product-state effects.
- [ ] Without mutating production, recheck canonical state, rebase the experiment-ready feature slice, run the approved suite/scope checks in the worktree, and pin the final candidate plus manifest including code, Python, dependency, import, config, source, state, and unit hashes.
- [ ] Present that exact post-rebase candidate/manifest, window, stop list, state/backup paths, and proposed hold outcomes; obtain owner checkpoint approval. Any subsequent SHA/manifest drift returns to rebase/test/pin/approval.
- [ ] Only after approval, stop/mask all named Job Intel timers/services/cron/manual callbacks and overlapping collectors, record their prior state, confirm no relevant process remains, integrate the exact approved commit into canonical `local/customizations`, and verify canonical HEAD.
- [ ] Create/verify the immutable runtime export and dedicated experiment venv from that commit, install only the temporary probe units, clone or exclusively lock each browser profile, verify no production DB/outbox/Slack credential or mutable venv is reachable, and run one real preflight attempt before enabling the timer. This is the owner-authorized production-host path, not worktree execution.
- [ ] Run the scheduled probe for at least 7 days; extend to 14 days when necessary to exercise rolling coverage. The wrapper fails closed on runtime/source/config drift; later unrelated canonical HEAD movement is recorded but cannot replace the running export.
- [ ] Sample-audit canonicalization, freshness, minimum evidence, obvious blockers, unresolved qualitative gates, company identity, and family independence.
- [ ] Report absolute stages 1–3 counts, duplicates, provisional labels, cells/families, blocked states, variability, and a clearly noncanonical sample-audited likely stage-4 range.
- [ ] Separate supply gaps from observability/access gaps and list exact unsupported capabilities.
- [ ] Record one owner decision: `proceed`, `bounded_additive_source`, `sot_amendment_required`, or `stop`; every non-proceed decision also records `canonical_hold`, `runtime_hold`, component/unit state, continuation permission, and exact commands.
- [ ] Block Task 8 unless the decision is `proceed`; route `bounded_additive_source` to Task 7 and then repeat Tasks 5–6.
- [ ] Disable and remove the temporary units, close locks/processes, verify evidence hashes/retention, and record teardown plus the selected hold state. Leave legacy Job Intel masked unless a separate owner approval explicitly restores it.
- [ ] Run acceptance tests, scope guard, and evidence redaction checks before commit.

**Acceptance criteria:**

- Gate A proves real acquisition behavior over time through existing scrapers.
- Gate A proves real scheduler, missed-run, overlap-lock, source-session, and teardown behavior from one pinned canonical commit on the production host.
- No canonical stage 4 or qualified-new-company metric is fabricated early.
- `sot_amendment_required` stops the plan; a technical gate cannot silently narrow normative lanes/targets.
- Silence is not approval.
- No experiment writes the live DB, outbox, protected Slack channel, production browser profile, or production cache/temp state.
- A non-proceeding Gate A cannot leave canonical/runtime state implicit; absent owner closure remains dormant and masked.

**Verification:**

```bash
pytest -q tests/acceptance/test_acquisition_gate.py tests/deploy/test_product_search_experiment_runner.py
systemd-analyze verify deploy/systemd/experiments/job-intel-product-search-probe-experiment.service deploy/systemd/experiments/job-intel-product-search-probe-experiment.timer
bash scripts/check_product_search_scope.sh
git diff --check
```

**Commit:** `docs(job-intel): record acquisition viability gate`

### Task 7: Conditionally add a bounded source capability and repeat Gate A

**Condition:** execute only when Gate A explicitly records `bounded_additive_source`; otherwise mark `[x] N/A` with the Gate A record.

**Purpose:** close one evidenced acquisition gap without modifying existing scraper implementations.

**Files:**

- Create only if approved: new modules under `job_intel/product_search/acquisition_plugins/`
- Create only if approved: focused plugin contract/tests
- Update: `config/product_search/source_capabilities.v1.yaml`
- Update: Gate A evidence with a superseding run/decision

**Checklist:**

- [ ] Translate the Gate A decision into one or more bounded capabilities with exact cells/family, input contract, evidence output, limits, and success criteria.
- [ ] Verify first whether current scraper parameters/query configuration can satisfy the gap without code/config mutation.
- [ ] If additive code is required, write failing tests and add a new isolated Product Search source plugin; do not edit any protected scraper file or production source configuration.
- [ ] If the plugin adds or changes a dependency, update the approved lock, rebuild the dedicated experiment venv, repin every runtime identity hash, and obtain approval for the new post-rebase candidate/manifest.
- [ ] If integration cannot be achieved additively, record `⚠️ existing_scraper_change_required`, stop, and prepare a separate owner-reviewed amendment.
- [ ] Re-run the Task 5 probe and Task 6 Gate A window with superseding run IDs; preserve all original evidence.
- [ ] Repeat the full rebase/test/pin/approval-before-mutation checkpoint, legacy-runtime stop, isolated code/Python experiment runner, hold-state closure, and teardown for the superseding commit; never hot-patch the installed experiment.
- [ ] Record the new Gate A decision; repeat Task 7 if another individually approved capability is required, or proceed only on `proceed`.
- [ ] Run plugin tests, acceptance tests, scope guard, ruff, and diff checks before each bounded commit.

**Acceptance criteria:**

- Every added capability corresponds to an observed Gate A gap and a recorded owner decision.
- Existing scraper implementations and production source configuration remain unchanged.
- Task 8 starts only after a superseding Gate A `proceed` decision.
- Any product-scope narrowing goes through a new Product SoT version, not this loop.

**Verification:**

```bash
pytest -q tests/product_search/test_acquisition_plugins.py tests/acceptance/test_acquisition_gate.py
ruff check job_intel/product_search/acquisition_plugins tests/product_search
bash scripts/check_product_search_scope.sh
git diff --check
```

**Commit(s):** one conventional commit per approved capability, for example `feat(job-intel): add product search <family> capability`

---

## Phase 2 — Product contracts and Decision v2 before persistence

### Task 8: Define Product Search contracts and Career Profile v2

**Purpose:** create closed, typed contracts for the Product Search model while preserving Candidate Facts and keeping discovery origin, selection mode, verdict, user decision, and company action independent.

**Files:**

- Create: `job_intel/product_search/contracts.py`
- Create: `config/product_search/career_profile.v2.yaml`
- Create: `tests/product_search/test_contracts.py`
- Create: `tests/product_search/fixtures/contracts/`
- Update: `docs/authority-manifest.yaml`

**Checklist:**

- [ ] Write failing tests for every closed enum, required field, unknown state, version/hash, evidence reference, and prohibited extra field.
- [ ] Lock `SelectionMode = Core | Exploration`; add a test that rejects every other value and that unfamiliar company/industry/geography alone does not create Exploration.
- [ ] Lock `watchlist_status`, `review_state`, and `company_action` to their exact SoT values and reject synthetic values such as `None`, `defer`, `remove`, or `nominated`.
- [ ] Model the six Decision dimensions separately from `SystemVerdict`, `UserDecision`, `RecommendedActionKind`, and optional `company_action`; prove no implicit conversion between them.
- [ ] Encode discovery origin precedence: only an already-`active` thesis whose monitoring formed the canonical candidate can produce `Strategic Watchlist`; later promotion/rediscovery cannot rewrite origin.
- [ ] Build Career Profile v2 from cited Candidate Facts and approved preference/product authorities; preserve hard gates, mandate vocabulary, transferable patterns, feasibility unknowns, and the Kazakhstan/Central Asia rules without inventing experience.
- [ ] Require immutable profile, Candidate Facts, semantic, search-contract, policy, and evidence snapshot references in every assessment input.
- [ ] Add compatibility fixtures for current records, explicitly mapping legacy values only at a versioned boundary and failing closed on ambiguous values.
- [ ] Run focused tests, schema snapshots, scope guard, and ruff before commit.

**Acceptance criteria:**

- All product vocabularies are exact, typed, and versioned.
- Candidate Facts cannot be broadened by the profile, model, or migration.
- The four independent product fields remain independently testable.
- A legacy record is never silently treated as a Product Search assessment.

**Verification:**

```bash
pytest -q tests/product_search/test_contracts.py
ruff check job_intel/product_search/contracts.py tests/product_search/test_contracts.py
bash scripts/check_product_search_scope.sh
git diff --check
```

**Commit:** `feat(job-intel): define product search contracts`

### Task 9: Build company evidence as a domain-only contract

**Purpose:** establish reproducible company identity, evidence, and thesis inputs for Gate B without changing the production database before a migration framework exists.

**Files:**

- Create: `job_intel/product_search/company_evidence.py`
- Create: `config/product_search/company_evidence_contract.v1.yaml`
- Create: `tests/product_search/test_company_evidence.py`
- Create: `tests/product_search/fixtures/company_evidence/`

**Checklist:**

- [ ] Write failing tests for company identity, aliases/domains, evidence source, captured/published timestamps, fact/inference distinction, freshness, contradiction, sufficiency, redaction, and content hash.
- [ ] Define immutable `CompanyEvidenceBundleV1` and `CompanyThesisInputV1` as domain/Pydantic contracts backed only by fixture or content-addressed evidence files during this phase.
- [ ] Keep vacancy evidence and company evidence distinct while preserving their correlation and provenance.
- [ ] Implement deterministic identity resolution with explicit `resolved`, `ambiguous`, and `unresolved` outcomes; ambiguity may not merge companies or satisfy evidence.
- [ ] Encode company scale/stage, trajectory, business model, employer risk, geographic context, and credible-need evidence without turning an event into an opportunity.
- [ ] Require evidence, a fit thesis, and a proposed action for weekly company intelligence; a signal alone is insufficient.
- [ ] Add supersession links rather than mutation for corrected evidence and ensure private Candidate Facts/user notes never enter company evidence bundles.
- [ ] Prohibit imports of `job_intel.product_search.store`, SQL, migrations, or production DB access from this module and test that boundary.
- [ ] Run tests, record/replay fixtures, scope guard, and ruff before commit.

**Acceptance criteria:**

- Gate B can consume company evidence deterministically without a production schema change.
- Identity ambiguity, stale evidence, contradiction, and insufficiency remain named states.
- Company events do not become vacancies or daily-digest items.
- No persistence/store/migration file is added or modified by this task.

**Verification:**

```bash
pytest -q tests/product_search/test_company_evidence.py
ruff check job_intel/product_search/company_evidence.py tests/product_search/test_company_evidence.py
bash scripts/check_product_search_scope.sh
git diff --check
```

**Commit:** `feat(job-intel): define company evidence contract`

### Task 10: Add bounded provider-assisted evidence synthesis

**Purpose:** use the existing semantic/provider runtime for cited evidence interpretation while keeping every normative decision in deterministic code.

**Files:**

- Create: `job_intel/product_search/evidence_synthesis.py`
- Create: `config/product_search/evidence_synthesis.v1.yaml`
- Create: `tests/product_search/test_evidence_synthesis.py`
- Create: `tests/product_search/fixtures/evidence_synthesis/`

**Checklist:**

- [ ] Write failing closed-schema tests for six-dimension evidence inputs, citations, explicit/inferred/unknown status, conflicts, bounded question candidates, and provider metadata.
- [ ] Add adversarial fixtures for unsupported resume claims, title-based seniority, headquarters-based feasibility, B2B prejudice, crypto-employer role veto, platform-engineering confusion, and unknown-to-negative conversion.
- [ ] Reuse the existing Semantic Contract runtime and provider abstraction; do not add a second unconstrained LLM client.
- [ ] Supply only bounded vacancy/company/Profile evidence references and require every synthesized claim to point to an input evidence fragment.
- [ ] Ensure the provider cannot emit a final hard-gate result, `SystemVerdict`, `SelectionMode`, company transition, urgency, selection, or delivery instruction.
- [ ] Record model/provider/prompt/schema versions, input/output hashes, latency, cost, and result status for deterministic fixture replay.
- [ ] Make timeout, invalid schema, missing citation, refusal, and provider outage explicit non-deliverable results; no legacy-evaluator fallback.
- [ ] Redact provider fixtures and add a golden record/replay test that runs without network access.
- [ ] Run focused tests, scope guard, and ruff before commit.

**Acceptance criteria:**

- Provider output is evidence synthesis, never product authority.
- Unsupported Candidate Facts or vacancy/company facts fail closed.
- Provider failures cannot produce an assessment eligible for delivery.
- Tests are deterministic and network-free.

**Verification:**

```bash
pytest -q tests/product_search/test_evidence_synthesis.py
ruff check job_intel/product_search/evidence_synthesis.py tests/product_search/test_evidence_synthesis.py
bash scripts/check_product_search_scope.sh
git diff --check
```

**Commit:** `feat(job-intel): add bounded evidence synthesis`

### Task 11: Implement deterministic Decision Contract v2

**Purpose:** make hard-gate eligibility, six-dimension conclusions, verdict, selection mode, company action, and delivery/urgency eligibility reproducible and policy-owned.

**Files:**

- Create: `job_intel/product_search/decision_v2.py`
- Create: `config/product_search/decision_contract.v2.yaml`
- Create: `tests/product_search/test_decision_v2.py`
- Create: `tests/product_search/fixtures/decision_v2/`
- Update: `docs/authority-manifest.yaml`

**Checklist:**

- [ ] Write failing table-driven tests for the exact eight-step decision order and all §9.5 non-regression invariants.
- [ ] Make this task the first component allowed to emit canonical `hard_gate_eligible` stage 4; require stages 1–3 evidence and preserve unknowns.
- [ ] Evaluate Feasibility, Mandate Fit, Company Fit, Transferability, Career Value, and Evidence Confidence as separate structured conclusions.
- [ ] Emit exactly one `SystemVerdict = Priority | Investigate | Save | Reject` and retain all named gates, blockers, unknowns, warnings, and evidence pointers.
- [ ] Permit `Investigate` delivery only with a material, bounded, realistically resolvable question; permit `Save` in a daily slot only for qualified Exploration information value; never deliver `Reject` as an opportunity.
- [ ] Assign `Core|Exploration` independently, require one named hypothesis/axis for Exploration, and require an explicit multi-axis exception when applicable.
- [ ] Assign optional company action independently from vacancy verdict and validate the exact watchlist transition preconditions.
- [ ] Evaluate urgent eligibility only from a strong opportunity plus externally evidenced time sensitivity learned after the daily digest; recency or model confidence alone is insufficient.
- [ ] Produce an immutable trace containing all contract/profile/evidence/provider hashes and deterministic policy version.
- [ ] Prove byte-stable replay, ordering independence for evidence inputs, and fail-closed behavior on contract/hash mismatch or synthesis failure.
- [ ] Run focused tests, fixtures, scope guard, and ruff before commit.

**Acceptance criteria:**

- Decision v2 is the only source of canonical stage 4 and Product Search verdicts.
- Every result is reproducible from pinned immutable inputs.
- Verdict, selection mode, user decision, company action, urgency, and CRM state cannot be conflated.
- All final SoT invariants and unknown semantics pass table-driven tests.

**Verification:**

```bash
pytest -q tests/product_search/test_decision_v2.py
ruff check job_intel/product_search/decision_v2.py tests/product_search/test_decision_v2.py
bash scripts/check_product_search_scope.sh
git diff --check
```

**Commit:** `feat(job-intel): implement decision contract v2`

### Task 12: Benchmark Decision v2 and close Gate B

**Purpose:** obtain an owner-approved, pinned Decision v2 on real acquired vacancies before introducing production persistence or user-facing UX.

**Files:**

- Create: `tests/acceptance/test_decision_v2_gate.py`
- Create: `docs/evidence/product-search-gate-b/README.md`
- Create: `docs/evidence/product-search-gate-b/benchmark-summary.json`
- Create: `docs/evidence/product-search-gate-b/owner-decision.md`

**Checklist:**

- [ ] Write failing acceptance assertions for real Gate A evidence imports, six-dimension completion, stage-4 denominators, trace replay, invariants, provider failures, cost, latency, and zero persistence/Slack side effects.
- [ ] Select a redacted stratified corpus across lanes, role patterns, companies, origins, likely Core/Exploration cases, hard blocks, and important unknowns; preserve immutable source IDs/hashes.
- [ ] Run provider synthesis and deterministic Decision v2 in record mode, then replay from captured artifacts without provider/network access.
- [ ] Human-audit every high-risk invariant plus a representative random sample; record adjudicated factual/policy errors separately from interpretation disagreements.
- [ ] Compare legacy evaluator output only as a counterfactual; never use disagreement as automatic truth or migrate its score into Decision v2.
- [ ] Report absolute stage-4 counts/denominators, verdicts, dimensions, unresolved questions, likely daily/urgent eligibility, errors, provider failures, cost, and latency.
- [ ] Record owner decision: approve the exact Decision v2/profile/semantic/provider schema hashes for shadow, request revision, or stop.
- [ ] Block Task 13 unless approval is explicit; silence or passing tests is not approval.
- [ ] Run acceptance tests, redaction checks, scope guard, and diff checks before commit.

**Acceptance criteria:**

- Gate B evaluates real evidence, not synthetic-only fixtures.
- Canonical stage 4 uses approved Decision v2 and exact SoT denominators.
- The approved hash set is immutable input to persistence and shadow tasks.
- No production database or Slack state changes.

**Verification:**

```bash
pytest -q tests/acceptance/test_decision_v2_gate.py
bash scripts/check_product_search_scope.sh
git diff --check
```

**Commit:** `docs(job-intel): record decision v2 readiness gate`

---

## Phase 3 — Versioned persistence, portfolio UX, and runtime orchestration

### Task 13: Introduce the migration framework and Product Search provenance store

**Purpose:** create one versioned, crash-safe persistence boundary before any Product Search domain object mutates the production-oriented schema, then import Gate A evidence through that boundary.

**Files:**

- Create: versioned migration files under the repository's reconciled Job Intel migration path
- Create: `job_intel/product_search/store.py`
- Create: `tests/product_search/test_migrations.py`
- Create: `tests/product_search/test_store.py`
- Update: `job_intel/store.py` only at the reviewed migration/integration seam

**Checklist:**

- [ ] Inspect the current SQLite schema/bootstrap behavior and write failing migration tests before changing it; do not assume the target migration directory or version table.
- [ ] Define a monotonic migration ledger with version, name, checksum, started/applied timestamps, status, and failure detail; reject changed checksums, gaps, and unknown future versions.
- [ ] Test fresh database creation, upgrade from a copied current schema, idempotent reopen, interrupted migration recovery, concurrent startup, checksum mismatch, future-version refusal, and rollback-on-failure.
- [ ] Add append-only provenance for acquisition run/query/source attempts, raw/content-addressed evidence references, canonical vacancy identity, company evidence snapshots, semantic/provider synthesis snapshots, policy inputs, and assessment scaffolding.
- [ ] Preserve source timestamps, parser/model/schema versions, hashes, supersession links, redaction class, and named failure states; corrections append rather than overwrite.
- [ ] Implement a verified importer for the exact Gate A evidence-package contract from Task 5; validate package hash/schema, idempotency, provenance completeness, and no production ID collision.
- [ ] Prove the imported canonical/evidence entities are the same model consumed by Decision v2 rather than a second incompatible probe schema.
- [ ] Keep delivery, user-action, watchlist-transition, and CRM ledgers out of this task except for migration primitives required by later versioned migrations.
- [ ] Run all migrations only against temporary databases and writable copies; never against the live DB during implementation.
- [ ] Run migration/store tests, existing store regression tests, scope guard, ruff, SQLite integrity checks, and diff checks before commit.

**Acceptance criteria:**

- The migration framework exists and is tested before all later persistence mutations.
- Gate A evidence imports with identical hashes and provenance, idempotently.
- A failed, concurrent, tampered, or future migration fails closed without a partially accepted schema.
- Existing production rows remain readable in the copied-current-schema rehearsal.

**Verification:**

```bash
pytest -q tests/product_search/test_migrations.py tests/product_search/test_store.py tests/test_store.py
ruff check job_intel/product_search/store.py tests/product_search/test_migrations.py tests/product_search/test_store.py
bash scripts/check_product_search_scope.sh
git diff --check
```

**Commit:** `feat(job-intel): add product search migration framework`

### Task 14: Persist immutable assessments, funnel, user actions, and CRM-independent state

**Purpose:** make every assessment/delivery/user outcome replayable while preserving the exact nine-stage funnel and preventing intent from mutating CRM.

**Files:**

- Create: `job_intel/product_search/funnel.py`
- Create: next versioned Product Search migration(s)
- Create: `tests/product_search/test_assessment_store.py`
- Create: `tests/product_search/test_funnel.py`
- Update: `job_intel/product_search/store.py`

**Checklist:**

- [ ] Write failing tests for immutable assessment versions, current-assessment pointer, input hashes, deterministic replay, supersession, and concurrent duplicate assessment insertion.
- [ ] Add append-only assessment dimension/verdict/gate/question/hypothesis/company-action records pinned to the Gate B-approved contract/profile/policy hashes.
- [ ] Model all nine funnel events in order and compute `current_stage` as a projection; prohibit skips, regression, mutation, and inference of stages 8/9 from a machine verdict.
- [ ] Keep `selected` separate from `delivered`; a selected item with no successful root receipt remains stage 6 and consumes no delivered slot.
- [ ] Store the six exact user decisions—`Pursue`, `Investigate`, `Save for later`, `Not interesting`, `Not feasible`, `Wrong or stale data`—plus reason taxonomy, optional note, actor, time, and replay key.
- [ ] Store concrete action events separately from user decisions and store CRM linkage/events separately from both; `Pursue`, 👍, or a package request cannot create `application_submitted`.
- [ ] Exclude factual corrections from learning, require explicit repair/supersession, and preserve original evidence for audit.
- [ ] Pin every later delivery item to one immutable assessment ID; reassessment may not rewrite what was shown.
- [ ] Add migration and concurrency tests on temporary DBs, then run existing store regressions, scope guard, and ruff.

**Acceptance criteria:**

- The nine-stage funnel is event-backed and reproducible.
- Stages 8 and 9 require explicit, distinct events.
- User intent, reactions, concrete actions, and CRM lifecycle cannot silently update one another.
- Historical deliveries remain explainable after reassessment.

**Verification:**

```bash
pytest -q tests/product_search/test_assessment_store.py tests/product_search/test_funnel.py
ruff check job_intel/product_search/funnel.py job_intel/product_search/store.py tests/product_search
bash scripts/check_product_search_scope.sh
git diff --check
```

**Commit:** `feat(job-intel): persist assessments and funnel events`

### Task 15: Implement the exact watchlist lifecycle and pilot-start snapshot

**Purpose:** migrate company candidates without inventing decisions, enforce the exact lifecycle, and make “previously untracked” measurable from an immutable denominator.

**Files:**

- Create: `job_intel/product_search/watchlist.py`
- Create: next versioned Product Search migration(s)
- Create: `tests/product_search/test_watchlist.py`
- Create: `tests/product_search/fixtures/watchlist/`
- Update: `job_intel/product_search/store.py`

**Checklist:**

- [ ] Write failing tests for the exact status/review/action vocabularies and reject all unrecognized legacy/synthetic values.
- [ ] Encode transitions exactly: `nominate→candidate`, candidate-only `promote→active`, active-only `retain→active` with refreshed thesis, `deprioritize→deprioritized`, `reject→rejected`, and review-due `expire→expired`.
- [ ] Require `nominate` to start a new thesis only for an absent company and require explicit owner action for every transition.
- [ ] Preserve `review_state=current|review_due` independently; prove `review_due` does not itself change status/origin and terminal theses require a new nomination for reconsideration.
- [ ] Import every legacy company reference as `candidate` with no action. Any promotion/retention/removal happens only later through a new explicit owner decision; “no decision” creates no action.
- [ ] Create a content-hashed, immutable pilot-start set of every company in every watchlist status before the first pilot acquisition; never recompute that denominator after the pilot begins.
- [ ] Implement “qualified previously untracked company” exactly: absent from that start set, sufficient company evidence, and either a `Priority|Investigate|Save` vacancy or an owner-approved standalone `nominate|promote` thesis.
- [ ] Preserve primary origin: candidate/late-promoted companies remain Open Market for already-canonical vacancies; only pre-existing active monitoring can create Strategic Watchlist origin.
- [ ] Add overdue-thesis, unbounded-candidate, idempotency, concurrency, and full-history tests.
- [ ] Run tests, migrations on temp DBs, scope guard, and ruff before commit.

**Acceptance criteria:**

- The lifecycle vocabulary and transitions match the final SoT exactly.
- Legacy import fabricates neither active status nor an owner action.
- Previously-untracked company metrics use a frozen pilot-start denominator.
- Watchlist never becomes a search allowlist or retroactively rewrites origin.

**Verification:**

```bash
pytest -q tests/product_search/test_watchlist.py
ruff check job_intel/product_search/watchlist.py tests/product_search/test_watchlist.py
bash scripts/check_product_search_scope.sh
git diff --check
```

**Commit:** `feat(job-intel): implement watchlist lifecycle`

### Task 16: Implement portfolio selection, reservation, and caps

**Purpose:** choose a bounded daily/urgent portfolio from approved assessments without quota filling, duplicate delivery, or concurrent oversubscription.

**Files:**

- Create: `job_intel/product_search/portfolio.py`
- Create: next versioned Product Search migration(s)
- Create: `tests/product_search/test_portfolio.py`
- Update: `job_intel/product_search/store.py`

**Checklist:**

- [ ] Write failing property/table tests for Monday–Friday Almaty dates, weekend accumulation, daily working range, authoritative 35/week cap, and correct under-fill.
- [ ] Enforce eligibility: `Priority`; bounded-question `Investigate`; and exceptional `Save + Exploration` with explicit information value/hypothesis. Exclude `Reject`.
- [ ] Enforce uniqueness across canonical vacancy/materially unchanged versions, already reviewed/delivered items, retries, holiday shifts, and concurrent runs.
- [ ] Preserve exactly one immutable origin and one `Core|Exploration` mode per selected item.
- [ ] Treat 60–70/30–40 origin mix, 5–7 weekly Exploration, employer/fintech concentration, and broad coverage as diagnostic targets/ceilings, never minimum-fill rules.
- [ ] Require one named Exploration hypothesis and normally one material axis; allow the documented multi-axis exception only when explicit.
- [ ] Apply no more than two ordinary vacancies per employer per week and report, rather than hide, justified exceptional deviations.
- [ ] Reserve selected items/weekly slots transactionally under a run ID; allow lease expiry/recovery without double selection and finalize consumption only under Task 21–23 delivery semantics.
- [ ] Maintain a separate urgent reservation capped at one per Almaty date and require post-digest externally evidenced urgency; an urgent item cannot also appear in an ordinary root.
- [ ] Emit explicit no-fill reasons separating insufficient qualifying supply from weak/blocked observability.
- [ ] Run property, concurrency, migration, scope-guard, and ruff tests before commit.

**Acceptance criteria:**

- Concurrent or replayed schedulers cannot exceed daily/weekly/urgent caps.
- Selected and delivered remain distinct until a publisher receipt exists.
- Portfolio balance never lowers hard-gate, verdict, evidence, or career-value requirements.
- An under-filled day is valid and explained.

**Verification:**

```bash
pytest -q tests/product_search/test_portfolio.py
ruff check job_intel/product_search/portfolio.py tests/product_search/test_portfolio.py
bash scripts/check_product_search_scope.sh
git diff --check
```

**Commit:** `feat(job-intel): add bounded portfolio selection`

### Task 17: Render daily and urgent artifacts with progressive disclosure

**Purpose:** produce transport-neutral, accessible root/detail artifacts that fit the attention budget and expose evidence without giving renderers delivery authority.

**Files:**

- Create: `job_intel/product_search/renderers.py`
- Create: `tests/product_search/test_renderers.py`
- Create: `tests/product_search/fixtures/renderers/`

**Checklist:**

- [ ] Write failing golden tests for daily root, zero/under-filled digest, opportunity detail, urgent root/detail, long text, missing optional facts, Unicode, escaping, and stable ordering.
- [ ] Keep each daily compact item to role/company/location/work format, verdict and Exploration marker, two specific reasons, one main risk/unknown, and recommended action.
- [ ] Put mandate/scope evidence, transferability bridge/gaps, feasibility, company rationale, career value, confidence, unknowns, origin, and Exploration hypothesis in its correlated detail artifact.
- [ ] Render actual item counts and no-fill/coverage notes; never claim 5–7 when fewer qualify and never create placeholder cards.
- [ ] Render urgent only from Decision v2 urgent eligibility and show the external time-sensitive evidence; do not use urgent styling for ordinary Priority items.
- [ ] Produce typed neutral document models with accessible fallback text and stable content hashes; do not import Slack SDKs, call network APIs, or choose a channel.
- [ ] Keep internal scores, prompts, Candidate Facts, private notes, tokens, filesystem paths, and unsupported inferences out of artifacts.
- [ ] Test deterministic redaction and maximum block/text budgets before the Slack mapping exists.
- [ ] Run golden tests, scope guard, and ruff before commit.

**Acceptance criteria:**

- Daily/urgent UX implements progressive disclosure without a Slack dependency.
- Root content is sufficient for delivery semantics even when detail later fails.
- Rendered facts and recommendations trace to the pinned assessment.
- Zero/under-fill behavior is truthful and concise.

**Verification:**

```bash
pytest -q tests/product_search/test_renderers.py
ruff check job_intel/product_search/renderers.py tests/product_search/test_renderers.py
bash scripts/check_product_search_scope.sh
git diff --check
```

**Commit:** `feat(job-intel): render daily and urgent reviews`

### Task 18: Render reviews and implement the locked metric dictionary

**Purpose:** produce weekly/monthly decision surfaces and mathematically exact product metrics without mixing acquisition, delivery, prediction, attention, or company denominators.

**Files:**

- Create: `job_intel/product_search/metrics.py`
- Create: `job_intel/product_search/attention.py`
- Create: `tests/product_search/test_metrics.py`
- Create: `tests/product_search/test_attention.py`
- Update: `job_intel/product_search/renderers.py`
- Update: `tests/product_search/test_renderers.py`

**Checklist:**

- [ ] Write failing tests for every Product SoT §14.1 metric with explicit numerator, denominator, stage, uniqueness key, time window, and non-computable state.
- [ ] Implement prospective attention sessions with one open session per user/surface, actual timestamps, interruption/abandonment, and no imputed zero/60-minute values.
- [ ] Calculate north star only as activated opportunities × 60 / actual completed review minutes; machine verdicts never enter user-outcome numerators.
- [ ] Keep stage-4 geography/industry/business-model coverage separate from stage-5 review and stage-7 delivery cuts.
- [ ] Implement qualified-previously-untracked-company from Task 15's frozen pilot-start snapshot and evidence/verdict/action rules.
- [ ] Implement duplicate, non-product false-positive, unresolved-feasibility, material factual-error, origin/mode mix, concentration, positive-decision, activation, and source/observability metrics exactly.
- [ ] Render weekly market/company review contents from §10.4, including proposed changes as proposals requiring explicit approval and company lifecycle decisions with rationale.
- [ ] Render monthly strategy questions from §10.5 with absolute numerators/denominators, comparable baselines, confounders, guardrails, and hypothesis status.
- [ ] Add weekly/monthly support-detail artifacts and keep them transport-neutral; no Slack calls or operational alerts in product artifacts.
- [ ] Run metric, attention, renderer, scope-guard, and ruff tests before commit.

**Acceptance criteria:**

- Every metric names its correct stage and denominator.
- Unknown/incomparable attention stays unknown/incomparable.
- Company intelligence stays a weekly product and does not consume daily vacancy slots.
- Weekly/monthly reports propose but never silently apply profile/search-policy changes.

**Verification:**

```bash
pytest -q tests/product_search/test_metrics.py tests/product_search/test_attention.py tests/product_search/test_renderers.py
ruff check job_intel/product_search/metrics.py job_intel/product_search/attention.py job_intel/product_search/renderers.py tests/product_search
bash scripts/check_product_search_scope.sh
git diff --check
```

**Commit:** `feat(job-intel): add product search reviews and metrics`

### Task 19: Add the explicit Product Search runtime orchestrator

**Purpose:** connect acquisition evidence, Decision v2, persistence, portfolio, rendering, and a transport-neutral non-sendable delivery intent through one resumable command surface before Gate C.

**Files:**

- Create: `job_intel/product_search/pipeline.py`
- Create: `job_intel/product_search/commands.py`
- Create: `job_intel/product_search/delivery_intent.py`
- Create: `deploy/systemd/experiments/job-intel-product-search-shadow-experiment.service`
- Create: `deploy/systemd/experiments/job-intel-product-search-shadow-experiment.timer`
- Create: `tests/product_search/test_pipeline.py`
- Create: `tests/product_search/test_commands.py`
- Create: `tests/product_search/test_delivery_intent.py`
- Update: the real Job Intel CLI registration seam discovered in Task 1

**Checklist:**

- [ ] Write failing end-to-end fixture tests for run identity, Almaty reporting period, lock acquisition, contract/hash validation, ordered steps, stage transitions, portfolio reservation, render, and immutable `DeliveryIntentV1` creation.
- [ ] Define `DeliveryIntentV1` as transport-neutral, content-hashed, and non-sendable: it has semantic delivery identity and rendered-bundle references but no channel, Slack metadata, transport request, envelope, or outbox state.
- [ ] Implement exact CLI entry points: `product-search shadow-run`, `product-search daily`, `product-search urgent`, `product-search weekly`, and `product-search monthly`.
- [ ] Ensure every command invokes one orchestrator rather than reassembling domain steps in systemd wrappers.
- [ ] Add `--shadow`, no-delivery, offline-render, fixture/record-replay, bounded date/run ID, and dry-run behavior as explicit modes. Before Task 21 every command is forcibly non-sendable; a production-delivery/outbox request fails closed.
- [ ] Persist step attempts/checkpoints so an interrupted run resumes idempotently and cannot repeat acquisition import, assessment, reservation, or delivery-intent creation.
- [ ] Add an import/dependency test proving Task 19 does not reference `outbox.py`, `slack_contract.py`, `ProtectedChannelEnvelopeV1`, or a sendable transport mode; Task 21 is the only extension point for those dependencies.
- [ ] Fail closed on authority/profile/search/semantic/policy hash drift, migration mismatch, provider/schema failure, stale/ambiguous evidence, lock contention, or unrecognized mode.
- [ ] Keep the legacy evaluator as a separately named shadow counterfactual only; never fall back to it or let it write Product Search verdict/funnel/delivery state.
- [ ] Separate daily, urgent, weekly, and monthly locks/caps while preserving cross-command uniqueness and slot rules.
- [ ] Package the shadow experiment service/timer through the Task 5 runner contract: immutable export from the integrated canonical commit, isolated Gate C state paths, no Slack credentials, and exact `product-search shadow-run` command.
- [ ] Add structured, privacy-safe run logs and operational states without posting health/status messages to the protected channel.
- [ ] Prove commands do not import or mutate protected scraper implementations/config and that all acquisition occurs through the locked public probe/orchestration interfaces.
- [ ] Run pipeline/command/domain regression tests, scope guard, ruff, and diff checks before commit.

**Acceptance criteria:**

- There is one explicit, testable runtime path before shadow begins.
- Resume/replay cannot duplicate assessment, selection, delivery intent, or funnel state.
- No Product Search scheduled command owns Slack credentials or calls Slack.
- No sendable outbox or Slack envelope exists in the Task 19 dependency graph.
- All five exact commands are discoverable and documented by CLI help tests.

**Verification:**

```bash
pytest -q tests/product_search/test_pipeline.py tests/product_search/test_commands.py tests/product_search/test_delivery_intent.py tests/product_search
ruff check job_intel/product_search tests/product_search
bash scripts/check_product_search_scope.sh
git diff --check
```

**Commit:** `feat(job-intel): add product search orchestrator`

### Task 20: Run the integrated shadow and close Gate C

**Purpose:** validate the complete product policy, offline UX, watchlist bootstrap, and attention instrumentation for 1–2 weeks without any Slack delivery.

**Files:**

- Create: `tests/acceptance/test_product_search_shadow.py`
- Create: `docs/evidence/product-search-gate-c/README.md`
- Create: `docs/evidence/product-search-gate-c/shadow-summary.json`
- Create: `docs/evidence/product-search-gate-c/experiment-manifest.yaml`
- Create: `docs/evidence/product-search-gate-c/teardown-record.md`
- Create: `docs/evidence/product-search-gate-c/owner-decision.md`

**Checklist:**

- [ ] Write failing acceptance assertions for all orchestrator stages, daily/urgent/weekly/monthly root-and-detail artifacts, decision-control UX mocks, immutable delivery intents, scheduled/missed/overlap behavior, and zero Slack API/credential/outbox use. Prompts, acknowledgements, evaluations, packages, and processed interaction flows are explicitly deferred to Gate D.
- [ ] Without mutating production, recheck canonical state, rebase the shadow-ready slice, run the approved suite/scope checks in the worktree, and pin the final candidate plus code/Python/dependency/import/config/state/unit manifest.
- [ ] Present that exact post-rebase candidate/manifest, window, stop list, state/backup paths, and proposed hold outcomes; obtain owner checkpoint approval. Any later SHA/manifest drift requires new approval.
- [ ] Only after approval, confirm legacy Job Intel remains stopped/masked, integrate the exact approved commit into canonical `local/customizations`, verify canonical HEAD, create/verify its immutable code export and dedicated experiment venv, and install only the temporary shadow units.
- [ ] Create an isolated Gate C DB/evidence/log/lock/profile/cache/tmp environment and import the approved Gate A package into it; pin Gate B hashes and create the immutable watchlist start snapshot without touching live Product Search state.
- [ ] Run `product-search shadow-run` on the normal temporary-systemd schedule for at least 7 days; extend to 14 days when coverage/attention evidence requires it. Fail closed on commit/config/schema drift.
- [ ] Generate daily/urgent/weekly/monthly artifacts offline, including valid zero/no-fill days, rejected urgencies, blocked cells, company decisions, and proposed—not applied—policy changes.
- [ ] Exercise provider outage, partial source failure, stale evidence, lock collision, interrupted/resumed run, reassessment, under-fill, cap exhaustion, and zero-action periods.
- [ ] Human-review a bounded sample for factual grounding, six dimensions, decision utility, progressive disclosure, company rationale, and Exploration hypotheses.
- [ ] Run a prospective attention prototype with actual completed minutes or record non-computable sessions; do not infer production attention from offline file generation.
- [ ] Report all SoT metrics with absolute values/denominators, legacy shadow disagreements, confounders, guardrails, and privacy/redaction audit.
- [ ] Record owner decision to approve UX/attention for direct Slack staging, revise, or stop; block Task 21 without explicit approval, and close both hold axes plus continuation permission for revise/stop.
- [ ] Disable/remove the shadow experiment units, close locks/processes, verify retained evidence hashes, and record teardown/hold state; do not restart the legacy system without a separate owner decision.
- [ ] Run acceptance/regression tests, scope guard, ruff, and diff checks before commit.

**Acceptance criteria:**

- The integrated pipeline runs through its explicit command surface for 1–2 weeks.
- Every artifact is generated without contacting Slack, creating outbox rows, or mutating the live Product Search DB/state; only the approved canonical code checkpoint and isolated experiment state touch the production host.
- The owner sees representative UX and attention evidence before Slack engineering begins.
- Gate C approval pins the artifact/renderer/policy hash set for staging.
- Interaction message kinds and their processing remain Gate D scope.
- A non-proceeding Gate C leaves the candidate dormant and legacy runtime masked until the owner explicitly selects another closed hold outcome.

**Verification:**

```bash
pytest -q tests/acceptance/test_product_search_shadow.py tests/deploy/test_product_search_experiment_runner.py tests/product_search
systemd-analyze verify deploy/systemd/experiments/job-intel-product-search-shadow-experiment.service deploy/systemd/experiments/job-intel-product-search-shadow-experiment.timer
ruff check job_intel/product_search tests/product_search tests/acceptance/test_product_search_shadow.py
bash scripts/check_product_search_scope.sh
git diff --check
```

**Commit:** `docs(job-intel): record product search shadow gate`

---

## Phase 4 — Direct Hermes Slack delivery and interaction staging

### Task 21: Add the typed Slack envelope and transactional outbox

**Purpose:** extend Task 19's non-sendable delivery intent into a durable, authorized, deterministic outbox while still making no Slack call.

**Files:**

- Create: `job_intel/product_search/slack_contract.py`
- Create: `job_intel/product_search/outbox.py`
- Create: next versioned Product Search migration(s)
- Create: `tests/product_search/test_slack_contract.py`
- Create: `tests/product_search/test_outbox.py`
- Update: `job_intel/product_search/pipeline.py`
- Update: `job_intel/product_search/commands.py`
- Update: `job_intel/product_search/delivery_intent.py`

**Checklist:**

- [ ] Write failing schema tests for the closed `ProtectedChannelEnvelopeV1`, ten allowed message kinds, authority/hash, placement, correlations, assessment/report IDs, versions, reporting date, fallback text, blocks, artifacts, and prohibited extra fields.
- [ ] Add the only permitted conversion from immutable `DeliveryIntentV1` to `ProtectedChannelEnvelopeV1`; verify semantic delivery identity and renderer/assessment hashes before transactional enqueue.
- [ ] Make the protected channel server-supplied; reject any envelope containing an arbitrary channel/target, unrecognized message kind/authority, free-form payload, or missing immutable correlation.
- [ ] Limit Slack message metadata to opaque message kind/outbox/request/correlation IDs; reject vacancy text, Candidate Facts, user notes, secrets, and application content in metadata.
- [ ] Validate content-addressed artifacts by allowlisted spool root, basename, media type, size, SHA-256, owner/mode, and retention; reject symlinks, traversal, arbitrary URLs, and mutable/mismatched files.
- [ ] Give each artifact its own immutable ID, `required` flag, payload hash, and closed delivery state `pending|uploading|uploaded|retryable_failed|ambiguous|dead_letter`, separate from the package-message receipt and with a nullable Slack file ID filled only by the publisher.
- [ ] Add a transactional outbox with closed states `pending`, `leased`, `delivered`, `rejected`, `retryable_failed`, `ambiguous`, and `dead_letter`; store each immutable payload hash and append-only attempt/transition records.
- [ ] Generate deterministic `outbox_id`/`transport_request_id` from semantic delivery identity so replay or concurrent pipeline runs cannot create a second root.
- [ ] Require replies to name `parent_delivery_id`; do not permit raw Slack `thread_ts` in domain input and never fall back from reply to root.
- [ ] Model root and detail deliveries independently: root success marks included vacancies delivered and consumes slots; detail failure remains retryable without root replay or funnel rollback.
- [ ] Keep ambiguous root attempts non-delivered until reconciliation proves the Slack message and persists its receipt.
- [ ] Add lease expiry/reclaim, bounded retry, permanent rejection, dead-letter, partial-detail, crash-before-send, crash-after-send, and concurrent-claim tests on temporary DBs.
- [ ] Explicitly extend the Task 19 orchestrator with a production-delivery mode that can create outbox rows only after Task 21 migrations/contracts are present; shadow/no-delivery/offline-render modes continue to emit delivery intents without sendable rows.
- [ ] Run contract/outbox/migration/pipeline tests, scope guard, and ruff before commit.

**Acceptance criteria:**

- Only the ten recognized message kinds can enter the protected-channel outbox.
- A semantic delivery identity has at most one root request despite retry/concurrency.
- Root/detail failure semantics are explicit and tested.
- Package-message and per-artifact state are independent; retry can target only missing artifacts.
- No component in this task calls Slack or requires Slack credentials.

**Verification:**

```bash
pytest -q tests/product_search/test_slack_contract.py tests/product_search/test_outbox.py tests/product_search/test_pipeline.py
ruff check job_intel/product_search/slack_contract.py job_intel/product_search/outbox.py tests/product_search
bash scripts/check_product_search_scope.sh
git diff --check
```

**Commit:** `feat(job-intel): add protected channel outbox`

### Task 22: Enforce the shared protected-channel guard and denial audit

**Purpose:** close the two confirmed generic Hermes send planes and every inventoried direct writer before enabling the Product Search publisher.

**Files:**

- Create: `plugins/platforms/slack/protected_channel_policy.py`
- Create: `tests/gateway/test_protected_channel_policy.py`
- Create: `tests/gateway/test_slack_send_surfaces.py`
- Update: `plugins/platforms/slack/adapter.py`
- Update: `tools/send_message_tool.py`
- Update: the Slack plugin standalone sender discovered in Task 2
- Update: each other credential-bearing Slack writer identified by the static/runtime inventory

**Checklist:**

- [ ] Write failing tests proving generic live-adapter send and standalone send to `C0B4MM6D52A` are rejected before token lookup, client construction, network call, file read/upload, update, reaction, or retry.
- [ ] Add one shared policy keyed by the exact protected channel ID; generic `SlackAdapter.send`, `send_message_tool`, standalone `_standalone_send`, media/file helpers, update/delete/reaction helpers, and known raw Web API writers must consult it.
- [ ] Make standalone delivery to the protected channel unconditionally unavailable, including when the gateway is down; there is no webhook, raw API, or standalone fallback.
- [ ] Define a bounded typed-publisher authorization path that accepts only a validated `ProtectedChannelEnvelopeV1` loaded from the outbox; no string flag, caller-supplied source name, environment variable, or forgeable metadata alone may bypass the guard.
- [ ] Keep generic adapter behavior unchanged for every non-protected channel and retain inbound membership/event handling for the protected channel.
- [ ] Emit privacy-safe denial records containing time, channel, surface/callsite, process/service identity, attempted operation, and payload hash/size—but no message body, blocks, filenames, user notes, tokens, or artifacts.
- [ ] Add static inventory tests/search checks for `chat_postMessage`, `files_upload_v2`, `files.getUploadURLExternal`, `files.completeUploadExternal`, webhook clients, `SlackAdapter.send`, `send_message_tool`, reactions, updates, and direct Slack clients; every credential-bearing path must be classified and guarded or explicitly proven unable to target the channel.
- [ ] Explicitly reject the retired `files.upload`/legacy SDK `files_upload` method in Product Search and protected-channel send surfaces; only `files_upload_v2` or its external-upload sequence is accepted.
- [ ] Add integration tests with spy clients proving denials produce zero Slack calls and valid non-protected sends are non-regressed.
- [ ] Document the boundary honestly: this is enforced application/runtime policy plus credential scoping, not a sandbox against arbitrary code in the gateway process, host root, workspace admins, humans, or a compromised token.
- [ ] Run gateway/platform regression tests, static inventory, scope guard, and ruff before commit; do not enable the new publisher yet.

**Acceptance criteria:**

- Both confirmed legacy planes—live adapter and standalone sender—fail closed before network access.
- All known direct credential-bearing writers are guarded/classified.
- The Hermes app remains in the channel and continues receiving events/interactions.
- Denial audit can identify remaining emitters without retaining private content.

**Verification:**

```bash
pytest -q tests/gateway/test_protected_channel_policy.py tests/gateway/test_slack_send_surfaces.py
ruff check plugins/platforms/slack/protected_channel_policy.py plugins/platforms/slack/adapter.py tools/send_message_tool.py tests/gateway
bash scripts/check_product_search_scope.sh
git diff --check
```

**Commit:** `fix(slack): guard product search channel writes`

### Task 23: Add the gateway-owned direct Product Search publisher

**Purpose:** consume the transactional outbox inside the long-running Hermes gateway and publish directly through the existing Hermes Slack app with durable receipts and ambiguity reconciliation.

**Files:**

- Create: `plugins/platforms/slack/product_search_runtime.py`
- Create: `plugins/platforms/slack/product_search_publisher.py`
- Create: `plugins/platforms/slack/product_search_reconciler.py`
- Create: `tests/gateway/test_product_search_runtime.py`
- Create: `tests/gateway/test_product_search_publisher.py`
- Create: `tests/gateway/test_product_search_reconciler.py`
- Update: `plugins/platforms/slack/adapter.py`
- Update: the bounded gateway lifecycle/plugin registration seam discovered in Task 1
- Update: gateway Product Search configuration schema/example without copying secrets

**Checklist:**

- [ ] Write failing runtime/fake-client tests for all-three-default-off startup, explicit stop/config-lock/start transitions, envelope load/validation, lease/heartbeat, exact channel injection, accessible `chat.postMessage`, thread replies, v2/external file uploads, per-artifact receipts, full metadata reconciliation, rate limits, retries, and graceful shutdown.
- [ ] Add the shared `product_search_runtime` configuration/startup gate with `publisher_enabled=false`, `interactions_enabled=false`, and `reconciler_enabled=false`; Task 23 implements publisher/reconciler behavior while reserving the later interaction flag as a closed disabled value.
- [ ] Start one `ProductSearchSlackPublisher` worker under the existing Hermes gateway lifecycle only when its flag is explicitly true **and** environment is exactly `production|staging`, DB migration/outbox schema version matches, authority-manifest hash matches, Slack app/team identity and verified conversation type match, server-side channel/state namespace match that environment, pinned gateway/runtime identity matches, and the cutover lock is in the expected state.
- [ ] When any startup condition is absent or mismatched, do not start/claim the worker, make no Slack call, and expose one named privacy-safe operational state; importing/registering the class or restarting the gateway can never enable consumption implicitly.
- [ ] Keep `SLACK_BOT_TOKEN` and `SLACK_APP_TOKEN` only in the Hermes gateway runtime; daily/urgent/weekly/monthly Job Intel services receive neither token and cannot instantiate this publisher.
- [ ] Persist the message attempt before the first Slack API call and persist `channel`, `ts`, response hash, timestamps, and attempt identity atomically after acceptance; file attempts/receipts are stored separately per artifact.
- [ ] Resolve a reply parent only from a delivered parent receipt matching authority/correlation; missing, rejected, or ambiguous parent fails closed and never becomes a root.
- [ ] On timeout/connection loss after a possible send, mark `ambiguous`; reconcile only through narrow timestamp-window `conversations.history(include_all_metadata=true)` or `conversations.replies(include_all_metadata=true)` calls using opaque metadata/request identity. Honor pagination/rate limits and `Retry-After`; never blindly resend an ambiguous root.
- [ ] If reconciliation proves the message, persist the recovered receipt and finalize once; if it disproves delivery, schedule a bounded retry; if it cannot decide, quarantine for operator review outside the product channel.
- [ ] On daily/urgent root receipt, atomically finalize stage 7 and delivered-slot consumption for every included assessment. Keep detail status independently retryable and never reselect those items.
- [ ] Upload files only with Python SDK `files_upload_v2` or the underlying `files.getUploadURLExternal → upload bytes → files.completeUploadExternal` sequence; reject the retired `files.upload` path in unit/static tests.
- [ ] For `application_package`, publish the package message once, persist each returned Slack file ID/state independently, retry only missing/failed artifacts, and never reupload successful files. Mark the package complete only when its message receipt and every `required=true` artifact are delivered.
- [ ] Treat post-timeout file ambiguity through the external upload/file IDs and per-artifact attempt state—not message metadata alone; unresolved ambiguity quarantines only that artifact.
- [ ] Implement the default-off bounded reconciler needed by Gate D: it operates only on ambiguous/recent delivery IDs in its configured state namespace, uses narrow full-metadata history/replies windows with pagination/rate-limit/`Retry-After` handling, persists message/artifact results independently, and never scans or opens state when disabled/mismatched.
- [ ] Map all ten message kinds to the correct root/thread/file behavior, validate Slack limits, and preserve fallback text; publisher code cannot accept free-form messages.
- [ ] Verify gateway unavailability merely leaves outbox rows pending; no scheduled process falls back to standalone/direct delivery.
- [ ] Add metrics/logs for queue age, attempts, delivered/rejected/ambiguous/dead-letter states, receipt lag, and artifact cleanup without high-cardinality/private labels.
- [ ] Prove enable/disable requires validated config plus cutover-lock transition and full gateway stop/start; no generic in-process toggle or ordinary restart can enable a component accidentally.
- [ ] Run runtime/publisher/reconciler/outbox/guard/platform tests, scope guard, and ruff before commit.

**Acceptance criteria:**

- The existing Hermes Slack app is the sole Product Search sender/receiver identity.
- Job Intel scheduled processes have no Slack credentials and only write outbox state.
- Every accepted message has a durable, correlated receipt or remains ambiguous/non-delivered.
- All three Product Search gateway component flags default off; the publisher cannot start on configuration, identity, authority, schema, conversation type, namespace, runtime, or cutover-lock drift.
- Package text and artifacts are idempotent independently; partial upload never duplicates the message or successful files.
- A detail failure cannot replay a delivered root or free a consumed slot.
- The reconciler is available for Gate D staging but remains default-off outside an explicitly enabled, namespace-bound runtime transition.

**Verification:**

```bash
pytest -q tests/gateway/test_product_search_runtime.py tests/gateway/test_product_search_publisher.py tests/gateway/test_product_search_reconciler.py tests/gateway/test_protected_channel_policy.py tests/product_search/test_outbox.py
ruff check plugins/platforms/slack/product_search_runtime.py plugins/platforms/slack/product_search_publisher.py plugins/platforms/slack/product_search_reconciler.py plugins/platforms/slack tests/gateway
bash scripts/check_product_search_scope.sh
git diff --check
```

**Commit:** `feat(slack): publish product search outbox`

### Task 24: Handle direct interactions, reactions, and thread replies through the same app

**Purpose:** preserve approved Product Search feedback and 🔍/👍 workflows through Hermes's existing Slack Bolt/Socket Mode adapter without creating any alternate visible-send path.

**Files:**

- Create: `job_intel/product_search/interactions.py`
- Create: next versioned Product Search migration(s)
- Create: `tests/product_search/test_interactions.py`
- Create: `tests/gateway/test_product_search_interactions.py`
- Update: `plugins/platforms/slack/adapter.py`
- Update: the existing namespaced Slack action-handler registration seam

**Checklist:**

- [ ] Write failing tests for namespaced Block Kit actions, modal submissions, `reaction_added`/`reaction_removed`, protected-channel thread replies, immediate acknowledgement, durable enqueue, replay, unauthorized actor/app/team/channel, stale assessment, malformed/expired item correlation, and ambiguous free text.
- [ ] Register Product Search actions through the existing Hermes Slack action-handler extension; keep the same Slack app/channel membership and Socket Mode connection.
- [ ] Activate full Product Search handlers only when `interactions_enabled=true` and every shared runtime gate matches the environment/state namespace; disabled/mismatch state may protocol-ack through the minimal interceptor but cannot open Product Search tables, mutate state, infer an item, or emit a visible response.
- [ ] Put decision/reason actions on the tracked `opportunity_detail` child message for one item. Each action value carries only opaque `delivery_item_id`, `assessment_id`, and one-use `action_nonce`, validated against the persisted detail receipt.
- [ ] Open an optional-note modal within Slack's trigger lifetime; encode the same opaque identifiers in modal `private_metadata` and revalidate them, the submitting user, receipt, expiry, and nonce on submission.
- [ ] Acknowledge interactive payloads within Slack's three-second window before provider/research/render work; enqueue durable processing only after validating the authenticated Socket Mode envelope and Bolt context, app/team/channel, configured Denis user ID, root/detail receipt, item, assessment, action ID/nonce, and replay identity.
- [ ] Persist the six exact user decisions, one optional primary reason/note, timestamps, and assessment/delivery linkage idempotently; an absent decision creates no event.
- [ ] Preserve `Wrong or stale data` as repair evidence excluded from preference learning; keep feasibility and genuine preference signals separate.
- [ ] Treat no reaction as no signal. For Exploration, require a reason for an interpretable hypothesis outcome and update only the named hypothesis.
- [ ] Preserve approved 🔍 as `vacancy_evaluation` and 👍 as `application_package` requests only when the reaction targets a tracked `opportunity_detail` child receipt. A reaction on the digest root, evaluation reply, package reply, prompt, or acknowledgement creates no item action or action chain.
- [ ] Accept a free-text reply as an item note only when it contains a validated explicit correlation token or the same user has exactly one unexpired pending item prompt in that root thread and the event replay identity is unused. Otherwise record it as a generic thread event, infer no item, and create no protected-channel response.
- [ ] Handle `Start review`/`Done` as idempotent silent acknowledgements that persist attention timestamps only; they never use `chat.update`, enqueue a visible reply, or mutate a digest message.
- [ ] Forbid `response_url`, `say`, direct `chat.postMessage`, generic adapter send, or standalone send for visible results. Every acknowledgement/evaluation/package is a typed outbox envelope handled by Task 23.
- [ ] Persist before visible acknowledgement; if durable work fails, log/alert outside the product channel and keep a retryable interaction state rather than bypassing the publisher.
- [ ] Add replay, concurrent-click, reaction toggle, deleted parent, expired artifact, provider failure, schema-mismatch, disabled-dormant legacy-message, and gateway stop/config-lock/start transition tests.
- [ ] Run interaction/gateway/outbox/guard tests, scope guard, and ruff before commit.

**Acceptance criteria:**

- The same Hermes app directly receives and publishes Product Search interactions.
- Every visible response is one of the ten allowed kinds and passes through the outbox/publisher.
- 🔍/👍 preserve their recognized meanings without fabricating user decisions, actions, or CRM state.
- Every item action is bound to one persisted detail receipt/assessment through a one-use nonce; digest-root/non-detail-reply reactions and ambiguous free text cannot infer an item.
- Duplicate/replayed interactions are idempotent.
- Dormant production gateway operation cannot process old channel messages or touch an absent/incompatible Product Search schema.

**Verification:**

```bash
pytest -q tests/product_search/test_interactions.py tests/gateway/test_product_search_runtime.py tests/gateway/test_product_search_interactions.py tests/gateway/test_product_search_publisher.py
ruff check job_intel/product_search/interactions.py plugins/platforms/slack tests/product_search tests/gateway
bash scripts/check_product_search_scope.sh
git diff --check
```

**Commit:** `feat(slack): handle product search interactions`

### Task 25: Exercise direct Slack delivery in staging and close Gate D

**Purpose:** prove the full same-app delivery/interaction system and both legacy denials in a non-production channel before any write to `C0B4MM6D52A`.

**Files:**

- Create: `tests/acceptance/test_product_search_slack_staging.py`
- Create: `docs/evidence/product-search-gate-d/README.md`
- Create: `docs/evidence/product-search-gate-d/staging-summary.json`
- Create: `docs/evidence/product-search-gate-d/staging-runtime-manifest.yaml`
- Create: `docs/evidence/product-search-gate-d/gateway-maintenance-record.md`
- Create: `docs/evidence/product-search-gate-d/owner-decision.md`
- Create: `config/product_search/slack_scope_manifest.v1.yaml`

**Checklist:**

- [ ] Write failing acceptance assertions for one gateway process/pinned revision/Product Search runtime authority, all production components default-off, separate staging state, exact app/team/channel/type/user binding, ten message kinds, root/thread placement, v2 uploads/per-artifact receipts, full metadata reconciliation, item correlation, actions/reactions/replay/reconnect, and zero production-channel calls.
- [ ] Record an owner-approved non-production `staging_channel_id` in the Gate D evidence; explicitly reject `C0B4MM6D52A` as a staging target.
- [ ] Without mutating production, recheck canonical state, rebase/verify Tasks 21–24 and the full approved staging suite in the worktree, pin the final candidate/staging manifest, and present the exact commit, window, stop list, state/backup paths, component transition, and proposed hold outcomes for owner approval.
- [ ] Only after exact-candidate approval, start the controlled maintenance window, stop/mask legacy Job Intel, stop the old Hermes gateway, and integrate the exact approved commit into `local/customizations`; any drift requires a new approval.
- [ ] Include Telegram, ordinary Slack, WhatsApp, approvals, and other gateway consumers in the maintenance impact record; drain/record in-flight work where supported and verify their health after the single candidate gateway starts.
- [ ] Prove the old gateway process/runtime authority is gone before starting the candidate. Never overlap old and candidate processes with the same app token; SDK-managed reconnect connections are allowed only inside the single candidate process/revision/state boundary and must pass envelope replay/idempotency tests.
- [ ] Stage and validate the staging config/lock transition, then start the canonical candidate with production publisher/interactions/reconciler all disabled and `C0B4MM6D52A` hard-denied. Enable staging publisher/interactions/reconciler only through the explicit stop/config-lock/start sequence, bound server-side to the approved staging channel and `environment_id=staging`/staging state namespace.
- [ ] Give staging a separate DB, outbox, artifact spool, reporting-period namespace, attention sessions, user/action/watchlist ledgers, assessment IDs, locks, and config. A staging interaction cannot resolve or mutate a production entity or consume a production slot.
- [ ] Resolve production and staging conversation types from authenticated Slack API/app evidence such as `conversations.info` or trusted adapter metadata; record type, membership, evidence time/hash, and never infer public/private from the channel ID prefix.
- [ ] Derive and record the exact Slack manifest: Product Search bot subset includes `chat:write`, `reactions:read`, `files:write`, and the relevant type-specific history scope/event pair (`channels:history` + `message.channels` for public channels, `groups:history` + `message.groups` for private channels); app-token `connections:write`, `reaction_added`/`reaction_removed`, Socket Mode/interactivity settings, and the existing Hermes app's total scopes/events are also recorded. Stop on missing membership, scope, subscription, or type mismatch; any added Product Search scope requires a cited API call and review.
- [ ] Exercise all four root kinds and six reply kinds with representative redacted artifacts, including zero/under-fill digest, failed detail, missing parent, long content, and artifact cleanup.
- [ ] Exercise primary user decisions, reasons, Exploration feedback, thread correction, 🔍 evaluation, 👍 package, retries, concurrent actions, reaction removal, and gateway restart.
- [ ] Inject timeout-before-send, timeout-after-possible-send, `Retry-After`, file-step failure/ambiguity, partial application package, expired lease, bounded history/replies reconciliation with `include_all_metadata=true`, corrupt envelope, stale assessment, and provider failure.
- [ ] Prove generic live-adapter, `send_message_tool`, standalone sender, legacy Job Intel, webhook/raw writer fixtures, and manual free-form sends cannot target the production channel and make zero Slack calls.
- [ ] Prove staging/product sends cannot escape to the production channel through parent IDs, metadata, file targets, fallback, interaction payloads, or configuration drift.
- [ ] Inspect real Slack layout/accessibility, direct interactions, receipts, redacted logs, and actual app identity; archive evidence without private message bodies/tokens.
- [ ] Close Gate D through the explicit stop/config-lock/start sequence with staging publisher/interactions/reconciler disabled, staging state closed, production guard preserved, and all production components still default-off. On failure, keep the guard and legacy Job Intel senders masked; do not restore the old unguarded gateway.
- [ ] Record owner decision to approve the deployment package, request revision, or stop; block Phase 5 without explicit approval, and close both hold axes plus continuation permission on revise/stop/failure.
- [ ] Run acceptance and all Slack/Product Search regressions, scope guard, ruff, and diff checks before commit.

**Acceptance criteria:**

- All ten kinds and interaction flows work through the same Hermes app in staging.
- One gateway process, pinned candidate revision, Product Search runtime authority, and state boundary process the staging window; SDK reconnect overlap stays inside that authority, and no old/candidate process overlap occurs.
- Staging state is physically/semantically disjoint from production state, and the exact app scope/event manifest is recorded.
- Generic live and standalone writers are denied for the production channel before network calls.
- Modern v2/external uploads, partial-package retry, full-metadata bounded reconciliation, deterministic item correlation, and default-off startup all pass real staging tests.
- Ambiguous delivery never causes a blind duplicate.
- No test or staging write reaches `C0B4MM6D52A`.
- A failed/non-proceeding Gate D records canonical/runtime holds and leaves all Product Search components disabled with legacy senders masked until an explicit owner transition.

**Verification:**

```bash
pytest -q tests/acceptance/test_product_search_slack_staging.py tests/gateway/test_product_search_runtime.py tests/gateway tests/product_search
ruff check job_intel/product_search plugins/platforms/slack tests/product_search tests/gateway tests/acceptance/test_product_search_slack_staging.py
bash scripts/check_product_search_scope.sh
git diff --check
```

**Commit:** `test(slack): prove product search staging gate`

---

## Phase 5 — Observability, deployment package, and Gate E rehearsal

### Task 26: Add product metrics and harden protected-channel reconciliation

**Purpose:** prove product outcomes and detect any application-authored Slack bypass without turning operational telemetry into channel noise.

**Files:**

- Update: `job_intel/product_search/metrics.py`
- Update: `plugins/platforms/slack/product_search_reconciler.py`
- Create: `tests/product_search/test_product_metrics.py`
- Update: `tests/gateway/test_product_search_reconciler.py`
- Update: exporter/Grafana definitions through the real observability seams discovered in Task 2

**Checklist:**

- [ ] Write failing tests for all product, funnel, source/cell, watchlist, attention, outbox, publisher, denial, ambiguity, receipt, interaction, and reconciliation metrics with bounded label sets.
- [ ] Export absolute numerators/denominators and explicit `not_computable`/blocked states; do not publish private text, URLs, company/vacancy/user IDs, correlation IDs, run IDs, or exception bodies as metric labels.
- [ ] Extend the Task 23 gateway-owned reconciler to compare recent protected-channel app-authored messages with outbox receipts using narrow timestamp-window `conversations.history(include_all_metadata=true)` and `conversations.replies(include_all_metadata=true)`, opaque metadata, and persisted `channel`/`ts` identities.
- [ ] Start reconciliation only when `reconciler_enabled=true` and every shared runtime gate matches the environment, verified conversation type, and state namespace; disabled/mismatch operation opens no Product Search state and performs no history/replies call.
- [ ] Bound reconciliation by ambiguous/recent delivery IDs rather than scanning channel history, paginate conservatively, honor method-specific rate limits and `Retry-After`, and surface exhaustion as a named deferred state rather than tight-looping.
- [ ] Reconcile application-package message and artifact states independently; file ambiguity uses persisted external-upload/file IDs and per-artifact attempts, never message metadata alone.
- [ ] Classify an app-authored message with no matching authorized outbox/receipt as an incident and correlate it to denial/process logs where possible; never legitimize it by backfilling an outbox row.
- [ ] Classify authorized outbox roots missing from Slack as delivery incidents and ambiguous/retry states according to Task 23; do not mark delivered from DB intent alone.
- [ ] Distinguish Hermes-app, other-app, bot, and human authors. Report out-of-bound actors for awareness while preserving the documented application boundary; do not claim the guard can block workspace admins/humans.
- [ ] Emit incidents to logs/Prometheus/Grafana or an explicitly approved operations destination, never as a Product Search root/reply in `C0B4MM6D52A`.
- [ ] Add alerts for legacy deny attempts, unauthorized Hermes-app messages, stuck/old outbox rows, repeated ambiguity, failed artifacts/interactions, migration mismatch, source degradation, and Product SoT pause triggers.
- [ ] Build dashboards/reports that keep stage-4 observability, stage-5 review, stage-7 delivery, user decisions, concrete actions, and CRM responses separate.
- [ ] Verify the 45-day Task 2 baseline can be compared to post-cutover counts without copying Slack message bodies.
- [ ] Add default-off, schema/authority/type/namespace mismatch, dormant-gateway, and explicit stop/config-lock/start transition tests for the reconciler.
- [ ] Run metric/reconciler/exporter/dashboard validation, scope guard, ruff, and diff checks before commit.

**Acceptance criteria:**

- The system can detect a Hermes-app message that bypassed the authorized outbox.
- Product metrics use exact SoT definitions and bounded, privacy-safe labels.
- Operational failures cannot create product-channel messages.
- Human/admin/credential-compromise limits remain explicit rather than overstated.
- A dormant or mismatched reconciler cannot scan production Slack history or touch Product Search tables.

**Verification:**

```bash
pytest -q tests/product_search/test_product_metrics.py tests/gateway/test_product_search_runtime.py tests/gateway/test_product_search_reconciler.py
ruff check job_intel/product_search/metrics.py plugins/platforms/slack/product_search_reconciler.py tests/product_search tests/gateway
bash scripts/check_product_search_scope.sh
git diff --check
```

**Commit:** `feat(job-intel): observe product search delivery`

### Task 27: Package the explicit scheduler units and gateway deployment

**Purpose:** make daily, urgent, weekly, and monthly orchestration independently operable while keeping Slack credentials and publishing inside the Hermes gateway.

**Files:**

- Create: `deploy/systemd/job-intel-product-search-daily.service`
- Create: `deploy/systemd/job-intel-product-search-daily.timer`
- Create: `deploy/systemd/job-intel-product-search-urgent.service`
- Create: `deploy/systemd/job-intel-product-search-urgent.timer`
- Create: `deploy/systemd/job-intel-product-search-weekly.service`
- Create: `deploy/systemd/job-intel-product-search-weekly.timer`
- Create: `deploy/systemd/job-intel-product-search-monthly.service`
- Create: `deploy/systemd/job-intel-product-search-monthly.timer`
- Create: focused host wrapper(s) under `scripts/`
- Create: `scripts/verify_product_search_deployment.sh`
- Create: `tests/deploy/test_product_search_systemd.py`
- Create: `tests/deploy/test_product_search_deployment_verifier.py`
- Update: gateway configuration/service packaging for publisher/interactions/reconciler

**Checklist:**

- [ ] Write failing unit-parser tests for all four service/timer pairs, exact CLI commands, canonical checkout path, `User=hermes`, environment boundaries, locks, time zone, persistence/catch-up policy, failure behavior, and install targets.
- [ ] Make daily invoke `product-search daily` Monday–Friday in Asia/Almaty and never on ordinary weekends; preserve the authoritative weekly cap under delayed/replayed timer runs.
- [ ] Make urgent invoke `product-search urgent` on its explicitly reviewed bounded polling schedule with an independent lock and one-per-Almaty-date cap; it is a real service/timer, not an undocumented branch of daily.
- [ ] Make weekly/monthly invoke their exact commands on owner-reviewed reporting boundaries and produce one idempotent period root each.
- [ ] Point every service/wrapper to `/home/hermes/.hermes/hermes-agent` on `local/customizations`; reject `.worktrees/`, `/tmp`, root home, and an unpinned checkout.
- [ ] Give scheduled Product Search services a dedicated env boundary containing DB/runtime/product config only; explicitly unset/reject `SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN`, webhook URLs, and standalone delivery settings.
- [ ] Keep Slack credentials in the existing Hermes gateway environment only; configure publisher/interactions/reconciler without duplicating secrets into files or unit definitions.
- [ ] Make deployed gateway configuration explicitly carry `publisher_enabled=false`, `interactions_enabled=false`, and `reconciler_enabled=false` by default plus expected environment, state namespace, DB/component schema versions, authority hash, app/team identity, server-side channel/conversation type, pinned runtime identity, and cutover-lock path; omission/mismatch keeps every component inactive.
- [ ] Ensure systemd wrappers call Task 19 commands only, use fixed absolute paths/umask/flock, and cannot invoke legacy delivery or protected scraper mutation.
- [ ] Build a read-only verifier for deployed commit/branch/Python-dependency identity, exactly one gateway PID/pinned revision/Product Search runtime authority, SDK connection/reconnect lineage owned by that process, unit contents/hash, timer next/last runs, environment key names without values, DB migration version/integrity, contract hashes, outbox state counts, all-three-default-off/startup gates, conversation-type-derived app scope/event manifest, guard/component registration, and legacy send-unit state.
- [ ] Add install/upgrade/restart ordering and rollback-silence instructions; packaging/tests must not install units, restart the gateway, migrate the live DB, or post to Slack.
- [ ] Run systemd verification, shell syntax, deployment tests, scope guard, ruff, and diff checks before commit.

**Acceptance criteria:**

- All four products, including urgent, have explicit tested service/timer pairs.
- Scheduled Job Intel processes have no Slack credentials or direct-send fallback.
- The gateway alone owns Product Search Slack publishing and inbound handling.
- Installed or restarted gateway code does not publish, process Product Search interactions, reconcile, or consume the production outbox until the explicit Gate E config/lock/restart transition passes every startup gate.
- Packaged units can never execute the feature worktree.

**Verification:**

```bash
pytest -q tests/deploy/test_product_search_systemd.py tests/deploy/test_product_search_deployment_verifier.py
bash -n scripts/verify_product_search_deployment.sh scripts/job_intel_product_search_*.sh
ruff check tests/deploy
bash scripts/check_product_search_scope.sh
git diff --check
```

**Commit:** `feat(deploy): package product search schedules`

### Task 28: Rehearse integration, migration, silence, and close Gate E

**Purpose:** integrate and exercise the final code dormantly on the production host under a maintenance checkpoint, while keeping the live Product Search DB/outbox/channel untouched and publisher/interactions/reconciler disabled pending explicit cutover authorization.

**Files:**

- Create: `tests/acceptance/test_product_search_cutover.py`
- Create: `docs/evidence/product-search-gate-e/README.md`
- Create: `docs/evidence/product-search-gate-e/rehearsal-summary.json`
- Create: `docs/evidence/product-search-gate-e/cutover-manifest.yaml`
- Create: `docs/evidence/product-search-gate-e/owner-decision.md`
- Create: `docs/runbooks/job-intel-product-search-cutover.md`

**Checklist:**

- [ ] Without mutating production, recheck canonical `local/customizations` branch/HEAD/status/stashes/worktrees and the dormant Gate D deployment, then rebase `codex/job-intel-product-search` onto its current HEAD inside the feature worktree; resolve only owned conflicts and rerun the full approved Product Search integration suite.
- [ ] Pin the final post-rebase candidate and manifest, verify the complete diff contains only approved plan scope, compare protected paths to the current merge-base, review/record any upstream-only changes, repin hashes to that base, and prove the Product Search diff leaves scraper/config paths unchanged.
- [ ] Prohibit an unbounded repository-wide test run on the production gateway host unless CPU/memory/disk/runtime impact receives a separate resource review; “full” in this plan means the full approved Product Search integration suite named in Verification.
- [ ] Present the exact candidate/manifest, maintenance window, stop list, state/backup paths, all-three-disabled config/lock transition, and proposed hold outcomes; obtain owner checkpoint approval before stopping any process. Any later drift requires a new approval.
- [ ] Only after approval, keep legacy Job Intel masked, stop the old gateway/runtime authority, integrate the exact approved candidate into canonical `local/customizations`, and verify exact HEAD/diff before starting any new process.
- [ ] Start one canonical candidate gateway process/pinned revision/runtime authority with the protected-channel guard active and production publisher/interactions/reconciler all disabled. Prove default-off startup gates, minimal disabled interaction interception, generic/standalone denial, inbound Socket Mode health, and zero protected-channel calls; no old unguarded gateway may remain.
- [ ] Copy the live Job Intel DB with a SQLite-safe snapshot method, preserve owner/mode/hash/size, run the full migration/import/replay on the copy, and verify integrity, row-count invariants, current/future version handling, and application compatibility.
- [ ] Rehearse install/render/verification for all systemd units and gateway configuration against a disposable root; optionally install production units disabled, and prove no unit points to a worktree or gives a Product Search scheduled service Slack credentials.
- [ ] Rehearse guard-first ordering: legacy live adapter, standalone sender, current Job Intel delivery, manual tool, and every known raw writer are denied before the publisher is considered enabled.
- [ ] Rehearse gateway unavailable/restart, all-three-disabled/enabled transitions through stop/config-lock/start, pending/ambiguous outbox, root delivered/detail failed, disabled/active interaction replay, DB rollback, artifact cleanup, and protected-channel reconciliation with fakes/staging only.
- [ ] Prove rollback stops/masks Product Search schedulers and restarts the gateway with publisher/interactions/reconciler disabled while keeping protected-channel guards and legacy sender masks active, so rollback means silence rather than restored noise.
- [ ] Build the exact cutover manifest: source/base/candidate commits, SoT/contract/config/unit/Python-dependency hashes, DB backup/restore paths, expected migrations, gateway/service/runtime identities, component flags/state namespace/cutover-lock states, verified production/staging conversation types and derived scope/event manifest, credential key locations without values, approved staging channel, proposed hold outcomes, and operator commands.
- [ ] Run the deployment verifier and full approved Product Search integration suite; obtain code/architecture/operations review and record every finding/disposition.
- [ ] Record explicit owner Gate E decision authorizing a named production window or requesting revision/stop. On revision/stop, close both hold axes and continuation permission. The dormant canonical code/guard/all-three-default-off checkpoint is allowed by the owner; do not migrate the live DB, enable any production component/timer, create live outbox state, or write `C0B4MM6D52A` in this task.

**Acceptance criteria:**

- A rebased, reviewed candidate commit is deployed canonically but dormant, and the exact cutover manifest is pinned.
- Writable-copy migration and production-host dormant integration/deployment rehearsals pass.
- Both real generic Slack send planes and all known direct writers are denied before publisher enablement.
- Rollback-silence and closed hold-state handling are demonstrated; the production checkout/gateway may contain the owner-approved guarded candidate, but publisher/interactions/reconciler, live Product Search DB/outbox/timers/channel remain untouched pending Gate E authorization.

**Verification:**

```bash
pytest -q tests/acceptance/test_product_search_cutover.py tests/gateway/test_product_search_runtime.py tests/product_search tests/gateway tests/deploy
ruff check job_intel/product_search plugins/platforms/slack tests/product_search tests/gateway tests/deploy tests/acceptance
bash scripts/check_product_search_scope.sh
bash scripts/verify_product_search_deployment.sh --rehearsal
git diff --check
```

**Commit:** `docs(job-intel): prepare product search cutover gate`

---

## Post-completion production cutover

This section executes only after Gate E contains an explicit owner authorization for the pinned candidate, manifest, and window. It is operational work, not part of plan-file completion.

1. Recheck that canonical `local/customizations` is exactly the dormant Gate E commit, one gateway process/runtime authority runs that commit with publisher/interactions/reconciler all disabled, guards are active, legacy Job Intel remains masked, live DB integrity/WAL/free space are healthy, and the approved manifest has not drifted. Any code/runtime/config drift returns execution to Task 28; do not merge ad hoc during cutover.
2. Take and verify SQLite-safe DB, config, environment, systemd, gateway, and current-revision backups at the manifest paths; test the DB copy with `PRAGMA integrity_check` before proceeding.
3. Confirm every legacy Job Intel service/timer/cron/manual wrapper remains stopped/masked, then quiesce the dormant Hermes gateway and prove its process/runtime authority is gone before live DB migration or config change.
4. Apply the reviewed migrations to the live DB once, verify migration checksums/version/integrity/imported evidence counts, and stop on any mismatch.
5. Remove Slack bot/webhook credentials and standalone delivery settings from `/etc/job-intel/job-intel.env` and the new scheduled-service environment while retaining existing Slack credentials only in the Hermes gateway environment. Verify key presence/absence without printing values.
6. Install/verify the pinned guard and all-three-default-off gateway config, then start one gateway process/runtime authority. Prove exact commit/Python-dependency/app/team identity, authenticated production conversation type and matching scope/event manifest, generic live-adapter/standalone/manual/raw-writer denial, inbound Socket Mode health, disabled component states, schema/authority/channel/namespace startup gates, and zero Product Search Slack calls.
7. Run the exact Product Search pipeline in no-delivery/dry-run mode against live read paths; verify contracts, migrations, selection, delivery-intent behavior, outbox suppression, unit paths, locks, and zero Slack writes.
8. Stage and validate the production config with publisher/interactions/reconciler enabled, exact state namespace and schemas/hashes, plus the next compare-and-swap cutover-lock state; keep all Product Search timers disabled.
9. Stop the disabled gateway and prove its process/runtime authority is gone, atomically install the validated config and transition the cutover lock, then start one enabled gateway process at the pinned revision. Prove all three startup gates, handler/reconciler/publisher health, reconnect replay safety, empty/no-send publisher state, and zero unintended calls.
10. Only after step 9 passes, install/enable the daily, urgent, weekly, and monthly service/timer pairs.
11. Allow the first production write only when a legitimate scheduled Product Search event exists. Do not send a test root. Verify its allowed kind, authority, receipt, metadata, root/thread placement, funnel stage, and slot accounting.
12. Reconcile Slack history/replies with `include_all_metadata=true` against message/artifact receipts immediately and after the first interaction cycle, using bounded windows and `Retry-After`. Any unauthorized Hermes-app message, ambiguous root/file, duplicate, or legacy attempt pauses new publishing while guards remain active.
13. Monitor for at least 72 hours across one daily, urgent polling, interaction, and applicable review cycle. Use denial logs to remove or rewrite legacy emitters one at a time with regression tests and scoped commits; never relax the guard to make an old sender work.
14. Declare cutover complete only when the channel contains only recognized kinds/authorities during the observed window, all Product Search message/artifact receipts reconcile, legacy emitters remain denied/masked, and owner acceptance is recorded.

Rollback at any failed cutover step stops/masks all new Product Search timers/services, stages the all-three-disabled config/lock state, stops and proves absence of the current gateway authority, atomically installs that disabled state, and restarts one guarded dormant gateway only when safe. It restores pinned code/DB/config according to the recorded hold outcome while keeping the protected-channel guard and legacy sender masks in place. Pending outbox rows are quarantined. Rollback must leave `C0B4MM6D52A` silent; it must never restore the old noisy delivery path.

After the rollback window and owner sign-off, remove the feature worktree with `git worktree remove /home/hermes/.hermes/hermes-agent/.worktrees/job-intel-product-search` and prune it only after proving the branch/commits are integrated and no uncommitted files remain. This cleanup is recoverability-sensitive and is not performed earlier.

## End-to-end definition of done

- Gate A proves acquisition through existing scrapers for 7–14 days, or the plan stops/amends; the protected scraper files and production source configuration never change.
- All code is authored in the dedicated worktree. Gate A/C/D/E production-host tests run only after post-rebase candidate pin, exact owner approval, and reviewed canonical checkpoint integration, with legacy Job Intel stopped/masked and isolated experiment/staging state; no systemd unit executes the worktree.
- Gates B and C approve pinned Decision v2 and offline UX/orchestration before persistence reaches delivery or Slack staging.
- Task 19 ends at non-sendable `DeliveryIntentV1`; only Task 21 can extend the pipeline with typed envelopes and transactional outbox creation.
- Decision v2 alone produces canonical stage 4; all nine funnel stages, exact enums, watchlist transitions, metrics, and no-fill rules match Product SoT v1.
- Every assessment, company thesis, selection, delivery, user decision, action, and metric is reproducible from immutable versioned evidence.
- Daily/urgent/weekly/monthly services invoke the explicit Task 19 command surface from the canonical checkout; urgent has its own service/timer.
- Job Intel scheduled services never own Slack credentials or call Slack; the existing Hermes gateway/app directly publishes and directly receives interactions.
- Publisher, Product Search interactions, and reconciler default off and start only through an explicit stop/config-lock/start transition with exact environment/schema/authority/app/team/channel/type/namespace/runtime agreement; an ordinary gateway restart can never enable them.
- Gate D uses one canonical gateway process, pinned revision, Product Search runtime authority, and physically separate staging DB/outbox/spool/reporting/user-action state. SDK reconnect overlap may exist only inside that authority; old/candidate processes never overlap with the same app token.
- The protected channel accepts only the ten typed kinds through `ProductSearchSlackPublisher`; generic live adapter, standalone sender, legacy, webhook/raw, and manual tool paths are denied before network access.
- Every root/reply and every individual artifact has a persisted receipt or named ambiguous state. V2/external file upload is mandatory; package text/successful files never repeat when one artifact fails. Root delivery consumes slots/stage 7 even if detail fails; a reply never becomes a root.
- Same-app Block Kit actions/modals, bounded thread replies, and 🔍/👍 reactions are bound deterministically to one tracked detail/assessment/nonce, validated, idempotent, durably stored, and visibly answered only through the typed outbox/publisher. Ambiguous free text and root/non-detail reactions infer no item.
- Attention `Start review`/`Done` uses silent protocol acknowledgement only; no generic or typed `chat.update` path is added.
- Protected-channel reconciliation finds no unauthorized Hermes-app messages for the acceptance window, and operational alerts remain outside the product channel.
- Reconciliation uses bounded `history`/`replies` calls with `include_all_metadata=true`, `Retry-After`, and independent message/artifact state; Gate D/E authenticates each conversation type and records the corresponding history scope/message event plus the total existing-app scope/event manifest.
- Cutover and rollback are rehearsed from writable copies; rollback preserves guard/masks and therefore silence.
- Gate A/C experiment manifests pin code, interpreter, stdlib, dependency lock, installed distributions, import root, `sys.path`, and zero editable installs; runtime drift fails closed before execution.
- Every non-proceeding Gate A/C/D/E explicitly closes canonical/runtime hold state; absent owner closure stays dormant and masked and blocks the next checkpoint.
- Owner approvals for Gates A–E and the final 72-hour acceptance are explicit, timestamped, and pinned to final post-rebase commits/manifests.

## Explicitly deferred or excluded

- Any rewrite, refactor, repair, tuning, or production-configuration change to existing vacancy scrapers.
- Any source expansion not justified by Gate A and approved as an additive isolated capability.
- Any narrowing or broadening of the final Product SoT without a new versioned owner-approved SoT.
- Notification Manager integration or a separate receive-only Slack app.
- Automatic Career Profile/filter/watchlist learning from reactions or model output.
- Automatic CRM mutation, application submission, outreach, or external messaging from verdicts/reactions.
- Slack workspace-admin controls, human posting restrictions, credential-compromise containment, or host-root isolation beyond the documented application/runtime boundary.
- Cosmetic dashboard work not required for a SoT metric, guardrail, gate, or operational safety signal.
