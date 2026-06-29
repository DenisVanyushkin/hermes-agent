# Hermes Recruiter Role — Skill-Package Architecture SoT

Status: draft SoT for implementation planning  
Target repository path: `docs/hermes-recruiter-skill-package-architecture-sot.md`  
Related SoT:

- `docs/hermes-subagent-architecture-source-of-truth-v2.md`
- `docs/hermes-role-package-runtime-slice-plan.md`
- `docs/hermes-controlled-engineering-runtime-slice-plan.md`
- `docs/hermes-controlled-engineer-bridge-terminal-contract-slice-plan.md`
- `docs/job-intel-source-of-truth.md` — to be produced by the audit
- `docs/job-intel-audit-sot-plan.md` — companion audit plan

## 1. Purpose

This document defines the target architecture for adding a Recruiter capability to Hermes without building a separate HR platform and without duplicating existing Hermes mechanisms.

The Recruiter capability must help Denis with job search workflows:

- evaluate vacancies;
- evaluate fit against Denis's career profile and preferences;
- build positioning and evidence maps;
- draft HR/application documents;
- review generated documents for hallucinations, weak positioning, and generic writing;
- optionally support deeper company, compensation, relocation, or contact-discovery analysis in later stages.

The key architectural decision is:

```text
Recruiter is implemented as a role-scoped skill package, not as a hardcoded HR primitive system.
```

## 2. Existing Hermes skill mechanics to reuse

The architecture must be based on current Hermes skill mechanics:

- Bundled skills live in repo `skills/` and `optional-skills/`.
- User/agent-created skills live in `~/.hermes/skills/`.
- External read-only skill directories come from `skills.external_dirs` in `config.yaml`.
- Local skills win name collisions.
- Skills have compact discovery/index metadata in the stable system prompt.
- Full skill content is loaded on demand through slash-command skill loading.
- Skill bundles live under `~/.hermes/skill-bundles/*.yaml` and define named sets of skills loaded together.
- External-dir skills are read-only by convention and are not mutated by Curator/self-improvement.
- Role context is injected per turn into the user-message channel, not into the cached system prompt.
- Role context is guidance unless backed by runtime tool/policy enforcement.

Implication:

```text
Do not invent a second primitive/subagent/package system where Hermes skills already provide the needed abstraction.
```

## 3. Design principles

### 3.1 Simplicity first

The first Recruiter implementation should not include:

- a full workflow DAG engine;
- a full role-package install/update/remove CLI;
- CRM writeback;
- Gmail send/draft integration;
- compensation benchmarking;
- relocation analysis;
- contact discovery;
- package marketplace/distribution lifecycle;
- Python modules for each HR task.

These may be added later only when a real use case proves they are needed.

### 3.2 Reliability over feature breadth

The MVP should do a small number of things well:

- route recruiter-related requests correctly;
- load the correct role-scoped skills;
- use job/career source-of-truth data where available;
- avoid invented career facts;
- produce useful, reviewable outputs;
- fail clearly when required inputs are missing.

### 3.3 Skills are task instructions, not authority

A skill may describe how to perform a task, but it is not the source of truth for facts, model policy, permissions, or external side effects.

```text
Skill = task instruction / reusable procedure.
Role = routing + context + policy boundary.
Bundle = configured composition of skills.
Runtime = source of truth for model invocation, tools, fallback, error handling, and final delivery.
```

### 3.4 Generated documents are drafts

Generated CVs, cover letters, recruiter messages, follow-ups, application answers, and bios are drafts until explicitly accepted by Denis.

```text
Generated text is not a career fact.
Career facts must come from Denis career SoT or explicit confirmation.
```

### 3.5 Job-intel remains the operational system

Recruiter must not duplicate the existing job-intel system.

```text
job-intel = source for job discovery, vacancy records, CRM/opportunity status, Slack delivery, historical events.
hermes_recruiter = conversational role using job-intel and career SoT to evaluate and draft materials.
```

The exact job-intel boundaries must be established by `docs/job-intel-source-of-truth.md` before implementation.

## 4. What we are doing

We are building a minimal, config-driven Recruiter role using role-scoped skills and skill bundles.

Initial target structure:

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

Possible repo-native location can be adjusted during implementation, but the conceptual unit remains the same: a Recruiter role package containing role metadata, skills, bundles, docs, and examples.

## 5. What we are not doing

This is not a full HR platform.

This is not a new primitive framework.

This is not a replacement for job-intel.

This is not a model-provider system.

This is not a permission-granting plugin system.

This is not a system that sends applications, emails, LinkedIn messages, or CRM writes without explicit approval.

This is not a system that guarantees hard tool isolation beyond what current Hermes runtime actually enforces.

## 6. Recruiter role boundary

The initial Recruiter role may:

- read vacancy text or job URL content through existing safe source-reading mechanisms;
- use job-intel read-only data if available;
- use career SoT facts and preferences;
- load recruiter skills and bundles;
- produce user-visible evaluations and drafts;
- mark missing inputs and uncertainty.

The initial Recruiter role must not:

- mutate the Hermes repo;
- run shell commands;
- restart gateway;
- modify live config;
- send email, Slack, Telegram, or LinkedIn messages;
- apply to jobs;
- update job-intel CRM state;
- invent career facts;
- silently proceed when the vacancy source is unavailable;
- silently downgrade model quality for important outputs without reporting fallback.

## 7. Initial skill set

The MVP requires four skills.

### 7.1 `vacancy-evaluation`

Purpose:

- read and normalize vacancy facts;
- identify title/function traps;
- evaluate fit against Denis's profile and preferences;
- provide apply / maybe / skip recommendation;
- mark unknowns.

Inputs:

- vacancy text, fetched page content, or job-intel vacancy ID;
- Denis career/search SoT;
- optional company/job-intel context.

Output:

- normalized vacancy facts;
- fit verdict;
- confidence;
- reasons;
- gaps and unknowns.

Failure behavior:

- if no vacancy text/source is available, return `SOURCE_REQUIRED`;
- if source is partial, mark partial confidence and unknowns.

### 7.2 `positioning-and-evidence`

Purpose:

- convert vacancy facts and Denis career facts into a concrete positioning packet;
- map job requirements to Denis evidence;
- identify strong/medium/weak/missing evidence;
- define what the eventual document writer should emphasize and avoid.

This skill is mandatory before vacancy-specific document generation.

```text
document-writer must not create vacancy-specific CV/CL/recruiter-message drafts without a positioning-and-evidence packet.
```

Output:

- headline / positioning angle;
- 3-5 selling points;
- risk mitigation;
- evidence map;
- unsupported claims that must not be used.

### 7.3 `document-writer`

Purpose:

- draft HR/job-search documents from an existing positioning packet and source facts.

Supported document types may include:

- cover letter;
- recruiter message;
- LinkedIn DM;
- follow-up email;
- CV tailoring notes;
- executive bio;
- application-form answer.

Rules:

- for vacancy-specific documents, require `positioning-and-evidence` output;
- do not invent career facts;
- do not imply application submission;
- produce drafts for user review only.

### 7.4 `document-reviewer`

Purpose:

- review generated documents for:
  - invented facts;
  - generic wording;
  - weak positioning;
  - mismatch with vacancy facts;
  - unsupported claims;
  - tone issues;
  - missing executive-level framing.

Output:

- approve / revise recommendation;
- issues found;
- suggested edits;
- final user-facing readiness status.

## 8. Initial bundles

### 8.1 `evaluate-vacancy`

Use when Denis asks what to think about a vacancy or whether it is worth applying.

Required skills:

```text
vacancy-evaluation
positioning-and-evidence
```

Rationale:

Fit evaluation without positioning/evidence is too shallow for Denis's use case.

Output:

- vacancy summary;
- fit verdict;
- evidence-backed reasoning;
- recommendation;
- next best action.

### 8.2 `application-materials`

Use only when Denis explicitly wants application materials.

Required skills:

```text
vacancy-evaluation
positioning-and-evidence
document-writer
document-reviewer
```

Output:

- positioning summary;
- evidence map;
- requested drafts;
- reviewer notes;
- final readiness status.

This bundle must stop at user review. It must not send messages or submit applications.

## 9. Model policy

Model selection should not live inside `SKILL.md`.

Skills may describe capability requirements, such as:

```yaml
capability_requirements:
  reasoning: medium
  writing_quality: high
  needs_web: optional
```

The actual LLM model policy belongs to role/package/bundle/runtime config.

Recommended MVP policy:

```yaml
role: hermes_recruiter
model_policy:
  evaluate_vacancy:
    preferred_model_tier: standard_reasoning
    fallback_allowed: true
  application_materials:
    preferred_model_tier: high_writing_quality
    fallback_allowed: true
    report_fallback_to_user: true
```

Do not hardcode exact model names in `SKILL.md`. Exact model mapping should remain host-owned runtime configuration.

## 10. Error handling

### 10.1 Source errors

Source fetching/parsing is runtime responsibility.

The skill receives source status and must behave accordingly.

Recommended statuses:

```text
fetched
unavailable
login_wall
requires_auth
unsupported_content
parse_failed
stale_cache_used
```

Rules:

- if vacancy text is absent, return `SOURCE_REQUIRED`;
- if only cache is available, mark `stale_cache_used` and lower confidence;
- if login wall blocks access, ask Denis for pasted vacancy text or job-intel ID;
- do not infer vacancy facts from company/title alone unless explicitly asked for rough preliminary analysis.

### 10.2 Model errors

Model invocation and fallback are runtime responsibility, not skill responsibility.

Recommended statuses:

```text
MODEL_UNAVAILABLE
MODEL_PROVIDER_ERROR
MODEL_FALLBACK_USED
MODEL_FALLBACK_FAILED
MODEL_POLICY_BLOCKED
```

Rules:

- if fallback is allowed and used, report it in execution metadata or final note;
- if no model is available and fallback is not allowed, return controlled blocked response;
- do not pretend the skill completed when the model failed.

### 10.3 Missing dependency errors

If `document-writer` is invoked without a positioning packet:

```text
POSITIONING_REQUIRED
```

It must not create vacancy-specific drafts.

If career facts are missing for a claim:

```text
EVIDENCE_MISSING
```

It must mark the gap and avoid the claim.

## 11. Minimal execution model

Do not implement a full DAG engine for MVP.

Use a simple role-scoped skill bundle executor:

```text
selected role
→ selected bundle
→ load role-scoped skill bodies
→ resolve required inputs
→ run one or two controlled model stages
→ validate expected packet(s)
→ produce final response
```

For `evaluate-vacancy`, one stage is enough:

```text
vacancy-evaluation + positioning-and-evidence
→ recruiter_positioning_packet_v1
→ final response
```

For `application-materials`, use two stages:

```text
Stage 1:
vacancy-evaluation + positioning-and-evidence
→ recruiter_positioning_packet_v1

Stage 2:
document-writer + document-reviewer
→ recruiter_document_packet_v1
```

This keeps reliability without building a general workflow engine.

## 12. Minimal schemas

### 12.1 `recruiter_positioning_packet_v1`

```yaml
schema_version: recruiter_positioning_packet_v1
source_status: fetched | unavailable | login_wall | requires_auth | unsupported_content | parse_failed | stale_cache_used
vacancy_facts:
  title:
  company:
  location:
  remote_policy:
  seniority:
  responsibilities:
  requirements:
fit:
  verdict: apply | maybe | skip | source_required
  confidence: high | medium | low
  reasons: []
positioning:
  headline:
  selling_points: []
  risks: []
  risk_mitigation: []
evidence_map:
  - requirement:
    denis_evidence:
    strength: strong | medium | weak | missing
    source:
unknowns: []
forbidden_claims: []
```

### 12.2 `recruiter_document_packet_v1`

```yaml
schema_version: recruiter_document_packet_v1
document_type:
audience:
purpose:
source_positioning_packet_id:
draft:
review:
  hallucination_check:
  genericness_check:
  missing_evidence:
  recommended_changes:
status: ready_for_user_review | blocked | positioning_required
```

## 13. Safety stance

The safety stance should be practical, not overengineered.

MVP must rely on:

- read-only package skill dirs;
- no shell for recruiter role;
- no outbound send;
- no repo writes;
- no live config writes;
- no CRM writeback;
- explicit source status;
- explicit model fallback status;
- drafts-only outputs.

Do not block implementation on future features such as full argument-level enforcement or package marketplace security.

However, do not claim isolation that Hermes does not enforce. If a boundary is advisory in current runtime, document it honestly.

## 14. Deferred capabilities

Defer until after Recruiter MVP works:

- company deep evaluation as separate skill;
- compensation benchmarking;
- relocation/environment evaluation;
- contact/recruiter discovery;
- CRM writeback with approval;
- Gmail draft/send with approval;
- role install/update/remove CLI;
- full package lockfile lifecycle;
- external role package distribution;
- general workflow DAG engine.

## 15. Definition of Done for the Recruiter architecture phase

This architecture phase is done when:

- the existing Hermes skills mechanism is confirmed as the base abstraction;
- Recruiter is specified as role-scoped skill package;
- MVP skills and bundles are defined;
- model policy ownership is defined outside `SKILL.md`;
- source/model error handling boundaries are defined;
- job-intel audit plan exists;
- implementation can proceed without inventing a parallel primitive/subagent framework.
