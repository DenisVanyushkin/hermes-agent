# Role Packages Architecture and Implementation Backlog

Status legend:

- `[x]` — complete
- `[~]` — in progress / waiting for external result
- `[ ]` — not started
- `[>]` — deferred to production v1 or future hardening

## 1. Completed pre-development audit artifacts

- [x] **00-source-discovery.md**
  - **Content:** live repository identity, branch/commit, source-of-truth decision, runtime entrypoints, top-level directory map, config/runtime directories, unknowns.
  - **DoD:** live Hermes repo identified as authoritative; branch/commit/clean status captured; upstream/origin relationship noted; no secrets read.

- [x] **00-rapid-code-map.md**
  - **Content:** high-level code map of CLI, runtime loop, profiles, context assembly, skills, tools, memory, Curator, scheduler, config, secrets, tests.
  - **DoD:** each major subsystem has responsibility, key files, relevance to role packages, confidence level.

- [x] **00-audit-plan.md**
  - **Content:** context-safe chunk plan for deeper audit.
  - **DoD:** chunks defined for runtime pipeline, memory/self-improvement, profiles/skills/context, tools/secrets/safety, extension points, tests, upstream/fork strategy.

- [x] **00-profile-package-readiness-notes.md**
  - **Content:** initial readiness assessment before terminology correction.
  - **DoD:** easy/hard integration points identified; mechanisms not to duplicate listed; next chunk recommended.

- [x] **03-profiles-skills-context.md**
  - **Content:** existing role-profile schema, deterministic routing, profile execution, role-context injection, skills pipeline, skill provenance, profile distribution, naming collision, validator requirements.
  - **DoD:** current closed-world registry documented; role context injection path proven ephemeral/cache-safe; `profile` naming conflict identified; future validator requirements include responsibility overlap and ambiguity detection.

- [x] **04-tools-permissions-safety.md**
  - **Content:** tool registry, toolsets, model exposure, dispatch path, runtime guardrails, approval system, symbolic category mapping, secrets/env flow, path safety, enforcement feasibility.
  - **DoD:** tool-level enforcement feasibility established; shell/action category limitation documented; env passthrough package risk identified; MVP/v1/future enforcement strategy proposed.

- [x] **05-extension-points-role-packages.md**
  - **Content:** concrete role-package architecture, manifest proposal, registry merge, validator evolution, routing design, context loading, boundary modes, skills/env consent, memory/SOUL/AGENTS boundaries, MVP/v1/future roadmap.
  - **DoD:** terminology resolved as `role package`; target architecture defined; implementation slices listed; MVP/v1/future separated.

- [x] **06-test-validation-strategy.md**
  - **Content:** TDD strategy, golden routing corpus, legacy parity, fixture library, manifest/CLI validation tests, overlap/ambiguity tests, env consent tests, observe_warn tests, enforced_tools tests, live smoke.
  - **DoD:** every MVP slice has a test plan; golden corpus first; hostile package fixtures defined; MVP and production v1 acceptance gates explicit.

- [x] **07-upstream-fork-strategy.md**
  - **Content:** true local delta vs `origin/main`, upstream/local split, implementation sequencing, rebase survival, branch strategy, fallback if upstream rejects, packaging/distribution strategy, documentation plan.
  - **DoD:** implementation allowed after rebase readiness; first commit defined as golden routing corpus; MVP order changed to `1 → 3 → 4 → 5 → 2 → 6`.

## 2. Current gate before implementation

- [x] **08-pre-implementation-readiness.md**
  - **Content:** rebase status, post-rebase tests, runtime smoke, `.bak` hygiene, local hunk ledger preparation, GO/NO-GO recommendation.
  - **DoD:** branch clean; rebase onto `origin/main` complete or no longer pending; profile tests pass; role context injection tests pass; profile architecture validator passes; smoke green; no unresolved conflicts; no secrets exposed.

## 3. Updated architecture/documentation artifacts to produce

- [x] **role-packages-backlog.md**
  - **Content:** this updated backlog with completed audit tasks, current readiness gate, MVP implementation slices, v1/future stages, and documentation plan.
  - **DoD:** all completed audit artifacts marked done; terminology corrected from profile package to role package; next steps visible.

- [x] **role-model-concept.md**
  - **Content:** refined role model concept after audit: role vs instance profile, role package, role registry, boundary modes, validation, memory/SOUL/AGENTS boundaries, skills/env consent, lifecycle, MVP/v1/future.
  - **DoD:** concept reflects audit findings; no outdated `profile package` framing; package-owned vs user-owned vs host-owned boundaries clear.

- [x] **docs/role-packages/README.md**
  - **Content:** what role packages are; MVP capabilities; intentional non-implementations; storage layout; core commands; safety model; current limitations; production v1 roadmap.
  - **DoD:** new user can understand what role packages do, what is stable, what is future work.

- [x] **docs/role-packages/operator-runbook.md**
  - **Content:** operator-facing guide with `hermes role` command examples, operational notes, safety guardrails, lockfile, env consent, troubleshooting.
  - **DoD:** operator can install, inspect, enable, disable, update, remove, and troubleshoot role packages using provided command patterns.

- [x] **docs/role-packages/authoring-guide.md**
  - **Content:** package authoring guide, minimal manifest example, package layout, validation examples, common errors, safety rules.
  - **DoD:** package author can create a valid advisory role package with skills, manifest, and understand validation requirements.

- [x] **docs/profile-handoffs/2026-06-10-role-packages-mvp-final.md**
  - **Content:** final MVP status report with HEAD, commits, what works, what is not implemented, tests summary, known issues, GO/NO-GO recommendation.
  - **DoD:** stakeholders have clear view of MVP deliverables, production readiness status, and roadmap to v1.

- [x] **MVP documentation update complete**
  - **Content:** architecture, user guide, authoring guide, final status aligned with implemented behavior.
  - **DoD:** docs match actual commands, statuses, file paths, boundary modes, limitations, and MVP status is IMPLEMENTED + SMOKED + GAPS CLOSED.

## 4. MVP implementation backlog

Implementation must not start until `08-pre-implementation-readiness.md` returns GO.

### 4.1 First implementation commit

- [x] **Golden routing corpus generator and corpus**
  - **Content:** generator script, committed corpus fixture, corpus test covering current EN/RU routing terms, overlays, docs-first markers, benign prompts, quoted/context stripping, fallback.
  - **DoD:** zero runtime code changes; current behavior locked before routing migration; test green.

### 4.2 MVP Slice 1 — Registry fail-soft + merge seam

- [x] **Role package registry merge seam**
  - **Content:** package loader/merge path attached conceptually to `load_profile_registry()`; built-ins hard-validated first; packages validated individually and skipped on failure.
  - **DoD:** zero packages = built-in registry deep-equality; invalid package marks only itself broken; built-in routing still works; tests green.

### 4.3 MVP Slice 3 — Manifest + `hermes role` CLI

- [x] **Role package manifest parser**
  - **Content:** `role-package.yaml` parser/schema for package metadata, role contract, routing triggers, boundary mode, env requirements, skills, docs, status, provenance.
  - **DoD:** valid/invalid manifest fixtures pass expected validation; secret-shaped payloads rejected; no package can include secrets or hard-deny overrides.

- [x] **`hermes role` CLI lifecycle**
  - **Content:** install/list/info/validate/remove commands, lockfile creation/removal, env consent recording, package status reporting.
  - **DoD:** full CLI lifecycle works in hermetic HERMES_HOME; lockfile has no secret values; remove cleans payload and consents.

### 4.4 MVP Slice 4 — Overlap/ambiguity validator

- [x] **Responsibility overlap validator**
  - **Content:** pairwise trigger overlap detection, substring containment detection, role-family overlap warnings, routing-flip simulation against built-ins.
  - **DoD:** exact/substring overlap fixtures fail; unique trigger passes; built-in route flips are hard errors; warnings are surfaced but controlled.

### 4.5 MVP Slice 5 — Package skills + env passthrough capping

- [x] **Package skills via external read-only dirs**
  - **Content:** package skills mounted through external skill dirs, indexed as package-origin skills, loaded on demand, not writable by Curator/self-improvement.
  - **DoD:** skill appears in index, body loads by slash-command path, uninstall removes it, Curator/self-improvement cannot mutate package files.

- [x] **Manifest-capped env passthrough and consent**
  - **Content:** skill env passthrough limited to skill-declared ∩ manifest-declared ∩ user-consented names.
  - **DoD:** undeclared, unconsented, wildcard, and secret-looking defaults rejected/blocked; consent persists in lockfile; uninstall revokes consents.

### 4.6 MVP Slice 2 — Routing triggers → data

- [x] **Data-driven routing triggers**
  - **Content:** built-in keyword tables moved to registry data behind a flag-guarded fallback; package triggers merge additively.
  - **DoD:** golden routing corpus green on both legacy and data paths; quoted/context stripping preserved; no zero-package behavior change.

### 4.7 MVP Slice 6 — observe_warn policy computation

- [x] **observe_warn effective policy computation**
  - **Content:** compute would-be role policy per turn; log would-blocks; never block in MVP.
  - **DoD:** no-block behavior proven; structured logs include role/package/tool/category/reason; policy never widens session/platform/subagent scope.

### 4.8 MVP validation and rollout

- [x] **MVP live smoke**
  - **Content:** install sample advisory role, route unique trigger, verify built-ins unchanged, verify ephemeral context, skill index, fake env consent decline, observe_warn log, uninstall cleanup.
  - **DoD:** recorded in `docs/profile-handoffs/`; no service restart unless approved; no secrets exposed; rollback path tested.

- [x] **MVP documentation update**
  - **Content:** architecture, user guide, authoring guide, runbook updates aligned with implemented behavior.
  - **DoD:** docs match actual commands, statuses, file paths, boundary modes, limitations.

**MVP Status:** IMPLEMENTED + SMOKED + GAPS CLOSED

## 5. Production v1 backlog

- [>] **enforced_tools boundary mode**
  - **Content:** ContextVar per-turn role policy; pre-dispatch enforcement; denied calls return synthetic blocked result; post hooks still fire.
  - **DoD:** denied tool does not reach registry dispatch; ContextVar propagates through concurrent executor; kill-switch downgrades to observe_warn.

- [>] **Category-to-tool mapping table**
  - **Content:** host-owned `category → toolset/tools/enforcement` mapping, validated against post-discovery registry.
  - **DoD:** enforceable categories resolve; advisory-only categories cannot be used under `enforced_tools`; unknown MCP/plugin tools default deny under enforced roles.

- [>] **Role-scoped approval pattern integration**
  - **Content:** shell-borne categories compile into existing approval/hardline layer; no second approval system.
  - **DoD:** hardline beats role allow; no double prompting; role/package reason appears in approval UX.

- [>] **observe_warn calibration gate**
  - **Content:** at least one week of live would-block logs reviewed before enabling enforced_tools.
  - **DoD:** false-positive threshold met; zero false positives on built-in roles; review recorded in docs/state.

## 6. Future hardening backlog

- [>] **Selective argument-level enforcement**
  - **Content:** role-aware command classes and role-aware path predicates where safe.
  - **DoD:** implemented only through existing approval/path-safety layers; no claim of full shell isolation.

- [>] **File-tool path scope enforcement**
  - **Content:** enforce may_write/may_read scopes for file tools with resolved-path checks.
  - **DoD:** traversal and symlink bypass tests green; shell path writes remain approval/advisory only.

- [>] **Per-role scoped memory**
  - **Content:** optional user-owned memory under `~/.hermes/memories/roles/<canonical_id>.md`.
  - **DoD:** injected ephemerally; written only through memory tools with role attribution; package updates never overwrite user learning.

- [>] **Package signing / trust levels / marketplace**
  - **Content:** trust model for third-party packages.
  - **DoD:** install-time trust signals and verification exist; not needed for MVP.

- [>] **Model-assisted routing behind deterministic ladder**
  - **Content:** optional future routing improvement.
  - **DoD:** deterministic routing remains default; model-assisted mode is observable, testable, and fail-soft.

- [>] **Stale-thread follow-up bot**
  - **Content:** watches messages or tasks explicitly marked for later, then resurfaces them only after they’ve actually gone stale; includes the original context plus a suggested next action.
  - **DoD:** avoids noisy repeat nudges, works cleanly for Telegram/ops/trading follow-ups, and only pings when the item is genuinely stale.

## Core Shadow Packages (Migration Preparation)

**Status:** COMPLETE (2026-06-11)

Five shadow packages created under `tests/fixtures/role_packages/core-shadow/`:
- `hermes-scribe-core` — shadows built-in `scribe`
- `hermes-researcher-core` — shadows built-in `researcher`
- `hermes-engineer-core` — shadows built-in `engineer`
- `hermes-security-auditor-core` — shadows built-in `security_auditor`
- `hermes-career-strategist-core` — shadows built-in `career_strategist`

All five validate, install, and remove cleanly. 108 regression tests cover them.
Built-in routing is unchanged.

**Next milestone:** v1 routing activation — promote shadow triggers into live routing layer.

