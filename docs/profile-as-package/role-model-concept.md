# Refined Hermes Role Model Concept

## Executive Summary

Hermes should evolve from a closed-world role-profile registry into a **role package** architecture.

A role package is a distributable, additive unit that contributes one role to an existing Hermes agent. It is not a separate agent, not a HERMES_HOME instance profile, not a plugin that executes Python at load time, and not a replacement for SOUL/MEMORY/AGENTS.

The architecture should reuse existing Hermes mechanisms:

- built-in role registry and contracts;
- deterministic role routing;
- ephemeral role-context injection;
- role-level approval hard-stop;
- tool registry and toolsets;
- pre-dispatch tool gating;
- skills and external skill directories;
- skill provenance;
- env passthrough machinery;
- MemoryManager, Curator, and self-improvement boundaries;
- profile distribution safety patterns where applicable.

The work is not to invent a parallel agent system. The work is to safely open the existing role registry, validate installed roles, keep one bad package from breaking built-ins, and progressively move from advisory role boundaries toward enforced tool-level boundaries.

## Terminology

### Role

A **role** is a persona/capability contract inside one running Hermes agent.

Examples:

- engineer;
- security auditor;
- scribe;
- researcher;
- career strategist;
- general operator.

A role affects routing, role context, output expectations, approval triggers, reviewer/scribe behavior, and eventually effective tool policy.

### Role package

A **role package** is a versioned distributable unit that contributes a role to the merged role registry.

A role package may include:

- role manifest;
- role contract;
- routing triggers;
- persona/boundary text for ephemeral role context;
- approval trigger declarations;
- skill payloads;
- docs;
- env requirement declarations;
- provenance and ownership metadata.

A role package must not include:

- secret values;
- `.env` contents;
- `auth.json`;
- credential file contents;
- wildcard env passthrough;
- hard-deny overrides;
- SOUL.md;
- MEMORY.md / USER.md;
- AGENTS.md;
- executable Python intended to be imported during package load.

### Instance profile

An **instance profile** is the existing `hermes profile` / HERMES_HOME concept: a separate agent home with its own config, memory, skills, sessions, gateway, and cron.

Role packages must not reuse the `hermes profile` terminology. The CLI namespace should be `hermes role ...`.

### Role registry

The **role registry** is the effective merged view of:

1. built-in roles from the repo;
2. installed active role packages from HERMES_HOME;
3. package status/lockfile metadata.

Built-ins are the floor and always win. Packages are additive only.

### Effective role policy

The **effective role policy** is the per-turn computed policy derived from:

```text
role contract ∩ session/platform/subagent tool scope ∩ global policy
```

The effective policy can only narrow access. It must never widen tools, paths, secrets, approvals, or model capabilities beyond host/session/global policy.

## Design Principles

### 1. Built-ins always work

One bad installed package must never break built-in routing, built-in role context, or existing tool behavior.

Built-in role validation remains hard. Package validation is per-package fail-soft.

If a package is invalid at load time, it becomes `broken` and is skipped. Routing continues over built-ins.

### 2. Additive only, no shadowing

Role packages must not override built-in roles, aliases, canonical IDs, model tiers, or hard-deny policies.

Conflicts with built-ins are install-time hard errors.

### 3. Context stays ephemeral

Dynamic role content must use the existing ephemeral per-turn role-context injection seam.

Role package context must not be injected into the cached system prompt by default.

Only stable skill index entries may enter the stable prompt tier through the existing skills system. Skill bodies and role docs are lazy-loaded.

### 4. No second approval system

Role packages must not create a second approval plane.

Intent-level approval uses the existing role execution / approval planning layer.

Command-level approval and shell-borne action classes use the existing `tools/approval.py` machinery.

### 5. Boundary honesty

Role packages must explicitly declare boundary mode:

- `advisory`;
- `observe_warn`;
- `enforced_tools`.

The system must not claim hard isolation when only prompt-level guidance exists.

### 6. Secrets are host/user-owned

Role packages may declare required environment variable names and credential file purposes, but never values.

Package skill env passthrough is allowed only when:

```text
skill declares env var ∩ manifest declares env var ∩ user consent recorded in lockfile
```

### 7. Package payload is read-only at runtime

Curator and self-improvement must not mutate package-owned files.

Agent-generated improvements to package content should become user-owned proposals, overlays, reports, or local skills, not edits to package payload.

### 8. Test before behavior change

Routing must be locked by a golden corpus before moving triggers from Python constants to data.

Zero-package behavior must remain byte-identical unless an explicit golden-approved behavior change is made.

## Role Package Lifecycle

### Install

`hermes role install <source>` should:

1. stage the package;
2. reject symlinks and forbidden files;
3. parse `role-package.yaml`;
4. validate schema and identity;
5. scan for secret-shaped content;
6. check `hermes_requires`;
7. validate trigger overlap and routing ambiguity;
8. validate env requirements;
9. prompt for env/credential consent;
10. write package payload under HERMES_HOME;
11. write lockfile entry;
12. clear relevant registry/skill caches.

### Load

At runtime, role registry loading should:

1. load and validate built-ins hard;
2. read installed package lockfile;
3. validate each package cheaply;
4. merge valid active packages additively;
5. mark invalid packages `broken` and skip;
6. return the effective role registry.

### Route

Routing should remain deterministic for MVP/v1.

Built-in trigger behavior must be preserved first. Package triggers are merged into data-driven routing only after overlap/ambiguity validation.

Quoted/context stripping remains the single pre-routing filter and must apply to package triggers exactly as it applies to built-ins.

### Render context

For the selected role, Hermes renders compact role context into the current turn only.

The block may include:

- role display name;
- purpose;
- personality summary;
- boundaries;
- output style;
- reviewer/scribe/security policy;
- approval policy;
- routing confidence;
- package attribution if applicable.

It must not include:

- manifest internals;
- routing trigger tables;
- env requirements;
- lockfile contents;
- secrets;
- full docs;
- full skill bodies.

### Update

Package update should be staged:

1. fetch/copy new package;
2. validate;
3. compare env requirements;
4. prompt only for new env/credential declarations;
5. atomically swap payload;
6. update lockfile;
7. invalidate caches.

### Remove

Package removal should:

1. remove payload;
2. remove lockfile entry;
3. revoke env/credential consents;
4. invalidate caches;
5. verify routing falls back to built-ins.

## Ownership Model

### Host-owned

- built-in roles;
- model policy;
- global hard-denies;
- file safety policy;
- sandbox env blocklist;
- category-to-tool mapping table;
- approval machinery;
- package validator code;
- package lockfile format.

### Package-owned

- role manifest;
- role contract;
- persona/boundary text;
- routing triggers;
- package docs;
- package skills;
- package version/provenance.

Package-owned files are read-only at runtime.

### User-owned

- memory;
- SOUL;
- AGENTS;
- approval grants;
- env/credential consents;
- local overlays;
- local skills;
- package enable/disable decisions;
- lockfile state.

## Boundary Modes

### advisory

The role package affects routing, context, skills, docs, and approval hints. It does not block tool calls.

Honest guarantee:

> This role shapes behavior but does not constrain a misbehaving or prompt-injected model at dispatch time.

### observe_warn

The system computes effective policy and logs would-be denials, but does not block.

Purpose:

- calibrate mapping table;
- measure false positives;
- prepare for enforcement;
- verify no tool scope widening.

### enforced_tools

The system enforces a per-turn tool allow-set at the pre-dispatch seam.

Denied calls return synthetic blocked tool results and do not reach the tool handler.

Requirements:

- ContextVar-based per-turn policy;
- propagation through concurrent executor;
- intersection with session/platform/subagent toolsets;
- global hard-deny precedence;
- kill-switch to downgrade to observe_warn;
- live observe_warn calibration before rollout.

## Tool and Approval Model

### Tool-level categories

Some symbolic categories map cleanly to tools/toolsets:

- web search;
- browser;
- calendar;
- contacts;
- email;
- Slack/message sending;
- cron/scheduler tools;
- package-provided read-only skills.

These can become tool-level enforcement in production v1.

### Shell-borne categories

Some categories are properties of command strings, not tools:

- production deploy;
- database migration;
- service restart;
- Cloudflare/DNS changes;
- Docker diagnostics;
- secrets write.

These must be represented as approval triggers or role-scoped command patterns in the existing approval system, not as simple tool allow/deny.

### Advisory-only categories

Some categories remain behavioral or too broad:

- inventing facts;
- pretending weak fit is strong;
- generic testing;
- generic shell local;
- secrets read, because global secret-read blocks are already stronger.

The manifest/mapping table must identify which categories are tool-enforced, approval-enforced, or advisory.

## Validator Model

The role package validator has two modes.

### Install-time validation

Strict and user-facing.

Checks:

- manifest schema;
- semver;
- `hermes_requires`;
- role ID uniqueness;
- canonical ID uniqueness;
- alias uniqueness;
- collision with built-ins;
- collision with HERMES_HOME instance profile names;
- role family validity;
- category vocabulary;
- category-to-tool mapping validity;
- boundary mode validity;
- approval trigger vocabulary;
- path scope grammar;
- env requirement shape;
- package skill env declarations are subset of manifest env declarations;
- no secret-shaped payload;
- no `.env`/`auth.json`/credential file contents;
- no symlinks;
- routing trigger overlap;
- built-in route flip simulation.

### Load-time validation

Cheap and fail-soft.

Checks:

- lockfile entry;
- package hash;
- basic schema sanity;
- status;
- conflicts caused by manual edits or removed packages.

Failure marks only that package as `broken`.

## Responsibility Overlap Model

Overlap validation is a first-class requirement.

It must detect:

- exact trigger overlap;
- substring trigger overlap;
- same-priority routing conflict;
- built-in route flips;
- role-family overlap;
- alias/canonical identity ambiguity;
- multi-language trigger collisions where detectable.

Hard errors:

- package shadows built-in trigger;
- package flips built-in golden corpus route;
- package duplicates built-in ID/canonical ID/alias;
- package claims a role family/priority in a way that breaks deterministic routing.

Warnings:

- documented lower-priority overlap;
- incomplete language coverage;
- family similarity without trigger conflict;
- advisory-only categories under advisory boundary mode.

## Memory, SOUL, AGENTS, and Self-Improvement

Role packages do not own durable identity or memory.

### SOUL

SOUL remains user/instance-owned. A package may include persona text for ephemeral role context, not SOUL fragments.

### MEMORY / USER memory

Memory remains user-owned and managed by the existing memory system.

Per-role scoped memory is future work and, if implemented, should live under user-owned HERMES_HOME memory paths, not in package payload.

### AGENTS

AGENTS-style operational rules remain project/user-owned.

A package may ship docs, but not override AGENTS.

### Self-improvement

Self-improvement may propose improvements to role behavior but must not edit package files directly.

Allowed outputs:

- memory notes;
- local overlay proposal;
- report artifact;
- user-owned skill proposal;
- issue/PR suggestion.

Disallowed outputs:

- direct edits to package-owned role contract;
- direct edits to package-owned skills;
- direct edits to manifest;
- direct escalation of package tool permissions.

## Storage Model

Installed role packages live outside the repository:

```text
~/.hermes/role-packages/<package-name>/
~/.hermes/role-packages/lock.yaml
```

The lockfile stores:

- package name;
- version;
- source URL/path;
- ref/hash;
- installed time;
- status;
- accepted env consents;
- accepted credential file consents;
- content hash;
- validation warnings.

The lockfile must not store secret values.

Repo-resident paths:

- built-in roles;
- sample package;
- test fixtures;
- architecture docs;
- validator/mapping code.

## MVP Scope

MVP is designed to be useful but conservative.

MVP implementation includes:

1. golden routing corpus (locked before migration);
2. fail-soft registry merge (invalid packages skip, built-ins always work);
3. role package manifest parser and schema validation;
4. `hermes role` CLI: install/list/info/validate/remove commands;
5. lockfile and package state management;
6. overlap/ambiguity validator (exact/substring overlap, routing flip detection);
7. package skills through external read-only directories;
8. env passthrough capping by manifest declaration and user consent;
9. data-driven routing triggers with Python fallback for backward compatibility;
10. observe_warn policy computation and structured logging (computes, logs, never blocks);
11. live smoke testing and rollback validation.

MVP behavior notes:

- **Package roles are installable but not yet routable** — routing still uses built-in trigger tables; package triggers are validated but inactive in MVP.
- **built-in routing uses YAML triggers/policy with Python fallback** — deterministic routing preserved during migration.
- **observe_warn computes/logs would-blocks but never enforces** — calibration phase, no blocking in MVP.
- **enforced_tools is validated as boundary_mode but not enforced** — validation ensures consistency, enforcement deferred to v1.
- **package skills are read-only and env-capped** — Curator/self-improvement cannot mutate; env access limited to declared+consented names.
- **--accept-env is supported by CLI** — users can grant consent at install time.
- **--schema-only exists for lightweight validation** — allows validation without triggering full install steps.
- **production_deploy/secrets_read mappings are placeholders** — no actual enforcement; documented for v1 planning.

MVP does not include:

- enforced tool blocking at dispatch;
- argument-level enforcement;
- path scope enforcement;
- package signing or trust levels;
- marketplace;
- per-role scoped memory;
- model-assisted routing;
- MCP/plugin default deny.

## Production v1 Scope

Production v1 adds:

- `enforced_tools` boundary mode;
- category-to-tool mapping table;
- ContextVar per-turn role policy;
- concurrent executor propagation;
- pre-dispatch blocked result;
- MCP/plugin default deny under enforced roles;
- kill-switch downgrade to observe_warn;
- live calibration gate;
- minimal role-scoped approval patterns.

Production v1 requires:

- ContextVar propagation tests;
- proof denied tools do not reach handlers;
- intersection semantics tests;
- kill-switch tests;
- observe_warn false-positive review;
- no false positives on built-in roles.

## Future Hardening

Future stages may include:

- selective argument-level enforcement;
- file-tool path scope enforcement;
- role-aware approval UX;
- per-role scoped memory;
- package signing and trust levels;
- package marketplace;
- model-assisted routing behind deterministic ladder;
- session-stable role pinning with explicit cache tradeoff.

## Implementation Order

Implementation order after readiness GO:

1. Golden routing corpus.
2. Slice 1 — registry fail-soft + merge seam.
3. Slice 3 — manifest + `hermes role` CLI.
4. Slice 4 — overlap/ambiguity validator.
5. Slice 5 — package skills + env passthrough capping.
6. Slice 2 — routing triggers to data.
7. Slice 6 — observe_warn policy computation.
8. MVP live smoke.
9. Observe_warn calibration.
10. Production v1 enforced_tools.

## Non-Goals

The role package system is not intended to:

- replace HERMES_HOME instance profiles;
- replace plugins;
- replace skills;
- replace SOUL/MEMORY/AGENTS;
- give third-party packages arbitrary Python execution at load time;
- guarantee full shell isolation;
- bypass existing approvals;
- bypass global hard-deny rules;
- depend on upstream acceptance before local production use.

## Success Criteria

The role model is successful when:

- built-in Hermes behavior is unchanged with zero packages installed;
- a valid role package can be installed, routed to, inspected, and removed;
- invalid packages cannot break built-ins;
- responsibility overlap is detected before activation;
- package skills work without giving unchecked env access;
- observe_warn produces useful calibration logs without blocking;
- production v1 can enforce tool-level policy after calibration;
- package payload survives upstream rebases because it lives outside the repo;
- documentation honestly describes guarantees and limitations.

## Shadow Package Migration Strategy

Built-in roles are being progressively mirrored as role packages. The approach:

1. **Shadow phase (current):** Create package-form versions of each built-in role with unique non-shadowing IDs. Validate, regression-test, but do not activate routing.
2. **Calibration phase:** Install shadow packages, run in `observe_warn` mode for ≥1 week, collect false-positive data.
3. **Activation phase (v1):** Promote shadow triggers into live routing alongside built-ins.
4. **Enforcement phase (post-v1):** Enable `enforced_tools` after calibration confirms near-zero false positives.
5. **Retirement phase:** Soft-deprecate built-in profiles once package routing is proven stable.

Shadow packages are in `tests/fixtures/role_packages/core-shadow/`. Built-in routing is authoritative until v1.

