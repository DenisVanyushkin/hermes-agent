# Hermes Recruiter — Action Plan

Status: draft execution plan  
Target repository path: `docs/hermes-recruiter-action-plan.md`  
Canonical host/repo: `ssh hermes-agent && cd /home/hermes/.hermes/hermes-agent`  
Current baseline reference: `9682a3478026ddc08221192ded4ee7343fa98fcc` on `local/customizations`  
Protected stash: `stash@{0}: On local/customizations: codex-preserve-db-persistence-before-controlled-manual-sot-slice`

## 1. Purpose

This document is the working implementation plan for adding the first non-engineering Hermes role: **Recruiter**.

The plan intentionally keeps the design small:

- reuse Hermes skills instead of inventing a new HR primitive framework;
- reuse job-intel as the operational source of truth instead of duplicating it;
- add only the runtime glue required for reliable read-only use;
- defer writes, outbound sends, package lifecycle tooling, DAG workflows, compensation analysis, relocation analysis, and contact discovery until the MVP is working.

The plan should be followed slice-by-slice. Each slice must leave the repository in a reviewable state and must not drift into adjacent nice-to-have work.

## 2. Related Source-of-Truth Documents

Primary related documents:

```text
docs/hermes-recruiter-skill-package-architecture-sot.md
docs/job-intel-source-of-truth.md
docs/job-intel-audit-sot-plan.md
docs/hermes-role-package-runtime-slice-plan.md
docs/hermes-subagent-architecture-source-of-truth-v2.md
```

Skills/profile/package background:

```text
docs/audit/03-profiles-skills-context.md
docs/audit/04-tools-permissions-safety.md
docs/audit/05-extension-points-role-packages.md
```

If a future implementation detail conflicts with these documents, the implementation must stop and the SoT must be reconciled before code proceeds.

## 3. Architectural Decision

The Recruiter capability is implemented as a **role-scoped skill package**, not as a hardcoded HR platform.

```text
Recruiter role
→ role-scoped recruiter skills
→ recruiter skill bundles
→ read-only job-intel facade
→ career SoT facts/preferences
→ user-visible drafts/evaluations
```

Not this:

```text
Recruiter role
→ direct SQLite / job-intel CLI / crm_service / crm_reconciler
```

Not this:

```text
Recruiter role
→ new Python primitive per HR task
→ custom HR workflow engine
```

## 4. Existing Hermes Skill Mechanics We Reuse

The plan is based on current Hermes skill mechanics:

- bundled skills live in repo `skills/` and `optional-skills/`;
- user/agent-created skills live in `~/.hermes/skills/`;
- external read-only skill directories are configured through `skills.external_dirs`;
- local skills win collisions;
- compact skill discovery/index metadata is included in the stable system prompt;
- full `SKILL.md` content is loaded on demand;
- skill bundles live under `~/.hermes/skill-bundles/*.yaml` and define named groups of skills loaded together;
- external-dir skills are read-only by convention;
- role context is injected per turn and is guidance unless backed by runtime enforcement.

Implication:

```text
Skills are task instructions and reusable procedures.
They are not permission boundaries, model-provider systems, source-of-truth stores, or write APIs.
```

## 5. Scope and Non-Scope

### 5.1 MVP Scope

The MVP must support:

- routing recruiter-related prompts to the Recruiter capability;
- evaluating a vacancy using vacancy facts and Denis career/search context;
- producing a positioning/evidence packet;
- drafting user-reviewable HR/application documents;
- reviewing generated documents for hallucination, genericness, weak positioning, and unsupported claims;
- reading job-intel state only through a dedicated read-only facade;
- reporting source/model/input errors clearly.

### 5.2 Explicit Non-Scope

Do not implement in MVP:

- CRM writeback;
- Slack/Gmail/Telegram/LinkedIn outbound sends;
- applying to jobs;
- Gmail draft creation;
- LinkedIn recruiter discovery;
- relocation analysis;
- compensation benchmarking;
- browser/source acquisition;
- job-intel scheduled job invocation;
- role package install/update/remove CLI;
- full workflow DAG engine;
- separate Python modules for each HR primitive;
- package marketplace/security lifecycle.

These are later extensions only after the basic Recruiter flow is proven useful.

## 6. Current Job-Intel Audit Findings That Drive the Plan

The completed job-intel audit established the following implementation constraints:

- canonical live DB: `/var/lib/job-intel/state/job_intel.sqlite3`;
- job-intel remains the operational system for vacancy discovery, historical vacancy facts, CRM/opportunity status, Slack delivery history, feedback, and application status;
- Recruiter MVP must not mutate CRM, scheduled jobs, Slack/Gmail/Telegram, browser profiles, or DB rows;
- generated Recruiter outputs are drafts unless Denis explicitly accepts them;
- no stable read-only Recruiter API exists yet;
- direct SQLite reads would couple Recruiter to schema;
- existing CRM/service modules contain write paths close to read methods;
- `JobIntelStore.connect(read_only=True)` exists and should be the foundation for a dedicated read-only facade;
- vacancy text may be stale or partial, so Recruiter output must include provenance and staleness warnings.

Therefore:

```text
Before Recruiter skills can use job-intel, implement a dedicated read-only job-intel facade.
```

## 7. Core Design Rules

### 7.1 Source-of-truth hierarchy

```text
job-intel DB = source of truth for vacancy records, CRM/opportunity state, delivery history, and historical job-intel evaluations.
career SoT = source of truth for Denis career facts, preferences, constraints, positioning facts, and approved reusable materials.
Recruiter generated text = draft only.
Recruiter skill text = task instruction only.
```

### 7.2 Document writer dependency

`document-writer` must not create vacancy-specific CV/CL/recruiter-message drafts unless a `positioning-and-evidence` packet exists in the current context.

Allowed exception: neutral, non-vacancy-specific messages such as a short follow-up can proceed with application history context only.

### 7.3 Model policy location

LLM model selection must not live inside `SKILL.md`.

Correct location:

```text
role config / bundle config / runtime model policy
```

Skills may declare capability requirements such as writing quality or reasoning level, but the runtime owns:

- preferred model selection;
- fallback policy;
- fallback reporting;
- model-unavailable failures;
- provider errors.

### 7.4 Error ownership

Skills describe semantic behavior for missing inputs, but runtime owns operational errors.

Examples:

```text
source unavailable → source resolver / facade status
vacancy missing → skill returns SOURCE_REQUIRED
model unavailable → runtime model invocation status
fallback used → runtime report and user-visible note when relevant
schema invalid → runtime validation failure
```

### 7.5 Safety boundaries

Recruiter MVP must not:

- write repo files except implementation files being explicitly changed by the coding agent;
- write job-intel DB;
- call job-intel write/apply/reconcile paths;
- call CRMService mutating methods;
- send outbound messages;
- restart gateway except when explicitly running a later live smoke;
- change live config except when explicitly requested;
- touch protected stash.

## 8. Target Recruiter Skill Package Shape

Conceptual package structure:

```text
role-packages/recruiter/
  role-package.yaml
  role.yaml
  skills/
    vacancy-evaluation/SKILL.md
    positioning-and-evidence/SKILL.md
    document-writer/SKILL.md
    document-reviewer/SKILL.md
  bundles/
    evaluate-vacancy.yaml
    application-materials.yaml
  docs/
    boundaries.md
    examples.md
```

The actual repo path may be adjusted during implementation if it better matches existing Hermes conventions. The conceptual unit must remain the same.

## 9. Required Initial Skills

### 9.1 `vacancy-evaluation`

Purpose:

- normalize vacancy facts;
- identify title/function traps;
- evaluate fit against Denis profile/preferences;
- produce `apply | maybe | skip` recommendation;
- mark unknowns and source limitations.

Failure behavior:

```text
No vacancy source/text → SOURCE_REQUIRED.
Partial source/text → proceed only with partial-confidence and explicit unknowns.
```

### 9.2 `positioning-and-evidence`

Purpose:

- transform vacancy facts and Denis career facts into a concrete positioning packet;
- map requirements to evidence;
- mark evidence strength as strong / medium / weak / missing;
- define what document-writer should emphasize and avoid.

This skill is mandatory before vacancy-specific document generation.

### 9.3 `document-writer`

Purpose:

- draft HR/job-search documents from an existing positioning packet and approved source facts.

Supported first document types:

- cover letter;
- recruiter message;
- LinkedIn DM text;
- follow-up email;
- CV tailoring notes;
- application-form answer.

Rules:

- do not invent career facts;
- do not imply application submission;
- produce drafts only;
- require positioning/evidence for vacancy-specific material.

### 9.4 `document-reviewer`

Purpose:

- review generated drafts for hallucinations, weak positioning, unsupported claims, generic text, tone problems, and mismatch with vacancy facts.

Output:

- approve/revise recommendation;
- issues found;
- suggested edits;
- final readiness status for user review.

## 10. Required Initial Bundles

### 10.1 `evaluate-vacancy`

Use when Denis asks whether a vacancy is interesting or worth applying to.

Skills:

```text
vacancy-evaluation
positioning-and-evidence
```

Output:

- vacancy summary;
- fit verdict;
- evidence-backed reasoning;
- risks/unknowns;
- next best action.

### 10.2 `application-materials`

Use only when Denis explicitly asks to prepare application materials.

Skills:

```text
vacancy-evaluation
positioning-and-evidence
document-writer
document-reviewer
```

Output:

- positioning/evidence packet;
- requested document drafts;
- reviewer notes;
- user-facing next step.

## 11. Minimal Structured Packets

Do not create many schemas in MVP. Use two compact packets.

### 11.1 `recruiter_positioning_packet_v1`

Fields:

```yaml
schema_version: recruiter_positioning_packet_v1
source:
  source_status: fetched | unavailable | login_wall | requires_auth | parse_failed | stale_cache_used | user_pasted
  source_ref: string
  first_seen: string | null
  last_seen: string | null
  staleness_warning: string | null
vacancy_facts:
  title: string | null
  company: string | null
  location: string | null
  remote_policy: string | null
  responsibilities: list[string]
  requirements: list[string]
fit:
  verdict: apply | maybe | skip | blocked
  confidence: low | medium | high
  reasons: list[string]
positioning:
  headline: string
  selling_points: list[string]
  risks: list[string]
  risk_mitigation: list[string]
evidence_map:
  - requirement: string
    denis_evidence: string
    strength: strong | medium | weak | missing
    source: string
unknowns: list[string]
```

### 11.2 `recruiter_document_packet_v1`

Fields:

```yaml
schema_version: recruiter_document_packet_v1
document_type: cover_letter | recruiter_message | linkedin_dm | follow_up | cv_tailoring_notes | application_answer | executive_bio
audience: string | null
purpose: string
source_positioning_packet_id: string | null
draft: string
review:
  hallucination_check: pass | issues_found
  genericness_check: pass | issues_found
  missing_evidence: list[string]
  recommended_changes: list[string]
status: ready_for_user_review | blocked
```

## 12. Implementation Slices

### Slice 0 — SoT/doc stabilization

Goal: commit or otherwise stabilize the SoT documents before code work.

Inputs:

```text
docs/hermes-recruiter-skill-package-architecture-sot.md
docs/job-intel-audit-sot-plan.md
docs/job-intel-source-of-truth.md
docs/hermes-role-package-runtime-slice-plan.md
```

Tasks:

- inventory current dirty/untracked workspace;
- stage only agreed SoT docs;
- do not stage unrelated dirty files;
- commit only if explicitly approved;
- no code changes.

Definition of done:

- SoT docs exist at target repo paths;
- workspace state is documented;
- unrelated files are not accidentally staged;
- protected stash untouched.

### Slice 1 — Read-only job-intel facade

Goal: create a small stable read-only API for Recruiter to query job-intel without touching write paths.

Candidate module:

```text
job_intel/recruiter_read_facade.py
```

Allowed implementation:

- use `JobIntelStore.connect(read_only=True)` or SQLite `mode=ro` equivalent;
- expose only approved lookup methods;
- return provenance and staleness information;
- no migrations, bootstrap, writes, source fetches, scheduled jobs, CRM writes, Slack/Gmail/Telegram sends, or browser calls.

Candidate methods:

```text
get_vacancy_by_key_or_url(...)
get_opportunity_by_vacancy_or_url(...)
get_latest_vacancy_evaluation(...)
get_company_context(...)
get_application_status_summary(...)
```

Validation:

- tests prove read-only mode is used;
- tests prove mutating services are not imported or called;
- tests prove facade returns stable error statuses for missing vacancy, stale data, and missing DB;
- tests prove no DB writes occur.

Definition of done:

- facade exists;
- tests pass;
- no live jobs run;
- no DB mutation;
- no outbound communication;
- no gateway restart;
- repo changes are limited to facade/tests/docs.

### Slice 2 — Career SoT contract

Goal: define where Denis career facts, preferences, approved claims, and generated drafts live.

Output document:

```text
docs/career-search-source-of-truth.md
```

It must define:

- authoritative career facts;
- job search preferences and constraints;
- approved reusable achievements and claims;
- current canonical CV/material sources if any;
- what is a draft;
- what must not be treated as fact;
- privacy expectations;
- relationship to `job_intel/seed/candidate.yaml` and job-intel scoring configs.

Definition of done:

- Recruiter can distinguish career facts from generated drafts;
- `candidate.yaml` authority is explicitly classified;
- generated materials remain drafts until Denis accepts them;
- privacy-sensitive files are not accidentally committed unless explicitly intended.

### Slice 3 — Recruiter skill package skeleton

Goal: add package structure and skill/bundle content without routing or live execution.

Files:

```text
role-packages/recruiter/role-package.yaml
role-packages/recruiter/role.yaml
role-packages/recruiter/skills/vacancy-evaluation/SKILL.md
role-packages/recruiter/skills/positioning-and-evidence/SKILL.md
role-packages/recruiter/skills/document-writer/SKILL.md
role-packages/recruiter/skills/document-reviewer/SKILL.md
role-packages/recruiter/bundles/evaluate-vacancy.yaml
role-packages/recruiter/bundles/application-materials.yaml
role-packages/recruiter/docs/boundaries.md
role-packages/recruiter/docs/examples.md
```

Validation:

- skill descriptions are specific enough for discovery;
- full skill instructions include required inputs and failure behavior;
- `document-writer` requires `positioning-and-evidence` for vacancy-specific drafts;
- bundles define model policy outside `SKILL.md`;
- no skill requests shell, repo write, outbound send, CRM write, or secrets.

Definition of done:

- skill package content is reviewable;
- no runtime behavior changed yet;
- no global skill pollution unless explicitly accepted for the slice.

### Slice 4 — Minimal skill loading integration

Goal: make Recruiter skills available with the smallest safe integration.

Preferred target:

```text
role-selected recruiter → expose recruiter skill namespace/bundles for this turn
```

Acceptable interim target if role-scoped loading is too expensive:

```text
recruiter-* skills exposed through skills.external_dirs, clearly namespaced, with routing/context controlling use
```

Do not implement full role package install/remove CLI.

Validation:

- zero Recruiter package installed/configured → existing behavior unchanged;
- recruiter skills do not break global conversations;
- skill loading reports origin/provenance where existing mechanisms allow it;
- no write/outbound capability is granted by skill loading.

Definition of done:

- Recruiter skill package can be loaded in a controlled way;
- ordinary non-recruiter prompts remain unaffected;
- no implementation drift into package lifecycle tooling.

### Slice 5 — Recruiter routing MVP

Goal: route recruiter-related requests to Recruiter capability without harming existing engineering/default routing.

Positive examples:

```text
посмотри вот эту вакансию, что думаешь?
стоит ли подаваться на эту роль?
подготовь cover letter под эту вакансию
напиши follow-up, 9 дней прошло
```

Negative examples:

```text
сколько времени во Владивостоке?
поправь тесты в репозитории
перезапусти gateway
нарисуй картинку
```

Validation:

- engineering prompts still route to engineering pipeline;
- default questions remain default;
- recruiter prompts route to Recruiter;
- ambiguous prompts can remain default or ask clarification;
- selected role/pipeline is observable in logs/reports.

Definition of done:

- routing tests pass;
- no provider calls required for tests unless explicitly approved;
- no live smoke yet.

### Slice 6 — Evaluate-vacancy flow

Goal: implement the simplest useful Recruiter flow.

Flow:

```text
user vacancy URL/text/job-intel key
→ source/read facade or pasted text
→ vacancy-evaluation
→ positioning-and-evidence
→ final response
```

Requirements:

- use read-only job-intel facade when job-intel data is used;
- support pasted vacancy text even without job-intel;
- return source status and unknowns;
- no document drafts unless requested;
- no writes/outbound sends.

Definition of done:

- Recruiter can answer “what do you think about this vacancy?”;
- answer includes fit verdict, evidence-backed reasoning, risks, and next best action;
- unavailable source yields `SOURCE_REQUIRED` or equivalent clear blocked response;
- tests cover source available, source missing, stale cache, and no job-intel match.

### Slice 7 — Application-materials flow

Goal: draft requested HR materials after evaluation/positioning exists.

Flow:

```text
vacancy-evaluation
→ positioning-and-evidence packet
→ document-writer
→ document-reviewer
→ user-visible drafts
```

Requirements:

- `document-writer` must fail/stop if no positioning packet exists;
- documents are drafts only;
- unsupported career claims are blocked or marked;
- reviewer output is shown or summarized;
- no outbound sends or CRM writes.

Definition of done:

- can draft cover letter/recruiter message/follow-up from approved inputs;
- reviewer flags invented facts and generic wording;
- output is useful without claiming application submission;
- tests cover missing positioning packet.

### Slice 8 — Controlled live smokes

Run only after slices 1–7 are reviewed and committed.

Smoke A — non-recruiter safety:

```text
Сколько времени сейчас во Владивостоке?
```

Expected:

```text
not routed to Recruiter
not routed to engineering
no repo changes
ordinary default answer
```

Smoke B — recruiter evaluate vacancy:

```text
посмотри вот эту вакансию, что думаешь?
<safe pasted vacancy text or known test fixture>
```

Expected:

```text
Recruiter selected
read-only path only
fit verdict produced
positioning/evidence included
no document draft unless requested
no writes/outbound sends
```

Smoke C — application materials:

```text
по этой вакансии подготовь cover letter и короткое сообщение рекрутеру
```

Expected:

```text
positioning packet exists
writer uses it
document reviewer runs
materials delivered as drafts
no send/no CRM update
```

All live smokes must follow the Hermes operator rules: manual user-triggered message, monitored terminal completion, no self-sending Telegram, rollback only when appropriate if temporary config changes are used.

## 13. Commands and Operational Rules for Agents

Default restrictions unless explicitly lifted:

```text
no push
no gateway restart
no live config change
no live validation
no provider calls
no Telegram/Slack/Gmail sends
no commit
no stash pop/apply/drop/touch
```

For coding tasks:

- always state host: `ssh hermes-agent`;
- always state repo: `/home/hermes/.hermes/hermes-agent`;
- verify branch/HEAD/status/stash before changing files;
- keep changes scoped to the requested slice;
- never stage unrelated dirty files;
- no live job-intel runs;
- no DB writes.

## 14. Review Checklist for Every Slice

Each slice report must answer:

```text
What changed?
What did not change?
Which SoT requirement does it satisfy?
Which files changed?
Was job-intel DB written? Expected: no.
Were outbound messages sent? Expected: no.
Was gateway restarted? Expected: no, unless smoke slice.
Was live config changed? Expected: no, unless explicitly approved.
Was protected stash touched? Expected: no.
What tests were run?
What remains blocked/deferred?
```

## 15. Exit Criteria for Recruiter MVP

Recruiter MVP is done when:

- job-intel read-only facade exists and is tested;
- career SoT contract exists;
- Recruiter skill package exists with four skills and two bundles;
- Recruiter routing works without breaking engineering/default routing;
- Recruiter can evaluate a vacancy with evidence-backed positioning;
- Recruiter can draft requested materials only after positioning/evidence exists;
- generated materials are marked as drafts;
- source/model failures are explicit;
- no write/outbound paths are enabled;
- controlled live smoke passes.

## 16. Deferred Items

Defer until after MVP:

- company-deep-dive skill;
- compensation benchmark;
- relocation/environment evaluation;
- recruiter/contact discovery;
- Gmail draft creation;
- CRM writeback with approval;
- artifact persistence in job-intel;
- full role package lifecycle CLI;
- role-scoped enforced tool isolation beyond current Hermes runtime;
- image/artist role integration.

## 17. Update Rules for This Plan

This plan is intentionally narrow. Update it only when:

- a slice exposes a real blocker;
- job-intel/career SoT findings contradict an assumption;
- Hermes skill mechanics differ from the assumed behavior;
- Denis explicitly changes MVP scope.

Do not add deferred items to MVP just because the architecture can support them.

