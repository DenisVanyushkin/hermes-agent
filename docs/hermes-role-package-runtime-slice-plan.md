# Hermes Role Package Runtime Slice Plan

Status: draft source-of-truth candidate  
Target repository path: `docs/hermes-role-package-runtime-slice-plan.md`  
Canonical host/repo: `ssh hermes-agent && cd /home/hermes/.hermes/hermes-agent`  
Current production mode assumption: autonomous production pilot enabled with commit gate only  
Protected stash: `stash@{0}: On local/customizations: codex-preserve-db-persistence-before-controlled-manual-sot-slice`

## 1. Purpose

This document defines the implementation plan for making Hermes roles configurable through role packages, so new specialized roles can be added primarily by editing configuration and prompt files rather than changing Python code.

Initial target roles:

1. **Recruiter** — searches, reads, and evaluates vacancies; prepares CV, cover letter, recruiter messages, and related application materials for user review.
2. **Artist** — uses a text model to turn a user creative brief into a high-quality image-generation prompt, then uses an image generation executor to create the image.

The goal is not to hardcode these two roles. The goal is to build a robust role package runtime that can support these and future roles with strict safety, routing, and execution boundaries.

## 2. Non-goals

This plan does not enable fully autonomous commits or pushes.

This plan does not allow role packages to execute arbitrary Python, shell commands, or unregistered tools.

This plan does not let a role package grant itself new permissions. Role packages can only request capabilities that the runtime already exposes and policy allows.

This plan does not replace the existing engineering autonomous pipeline. It extends the same architecture with a generalized role package layer.

This plan does not require all future roles to be purely configuration-only if they need new runtime primitives. The rule is:

```text
Adding a role that uses existing executor primitives = config/prompt only.
Adding a new executor primitive = code once, then reusable by config.
```

## 3. Existing source-of-truth documents

This plan must remain aligned with the existing Hermes source-of-truth documents:

```text
docs/hermes-subagent-architecture-source-of-truth-v2.md
docs/hermes-controlled-engineering-runtime-slice-plan.md
docs/hermes-controlled-engineer-bridge-terminal-contract-slice-plan.md
```

The role package runtime must not weaken the engineering pipeline's existing safety model:

```text
user request
→ router
→ orchestrator
→ pipeline plan/state machine
→ runtime factory
→ subagent runner
→ engineer/reviewer/tool execution
→ controlled evidence
→ final response / stop gate
```

For engineering, the existing source-of-truth principle remains:

```text
engineer text is not the source of truth
git/worktree diff is source of truth for changes
controlled test runner evidence is source of truth for tests
reviewer decision is source of truth for approval/block
```

For role packages, the analogous principle is:

```text
role config is source of truth for routing/context/policy/workflow
runtime primitives are source of truth for executable capability
role text is not authority
```

## 4. Core terminology

### Role

A role is a named capability/persona/policy contract. It defines routing triggers, model preferences, context, allowed output types, workflow defaults, and tool boundaries.

Examples:

```text
hermes_engineer_core
hermes_code_reviewer
hermes_recruiter
hermes_artist
```

### Subagent

A subagent is a model invocation boundary using a role-specific prompt and runtime context. A pipeline may invoke one or more subagents.

Example:

```text
artist_prompt_writer = subagent using hermes_artist role context
```

### Pipeline

A pipeline is a workflow/state machine over subagents, tools, gates, and evidence.

Examples:

```text
engineering_review_pipeline
recruiter_application_pipeline
artist_image_pipeline
default_conversation_pipeline
```

### Role package

A role package is a distributable bundle containing role configuration, prompts, optional workflow recipes, documentation, tests/golden examples, and metadata.

### Executor primitive

An executor primitive is a hardcoded, safe runtime capability exposed by Hermes and selectable by configuration.

Examples:

```text
text_model
web_research
document_render
image_generation
controlled_response
```

Unknown executor primitives must be rejected by validation.

## 5. Target architecture

The target architecture has four layers.

### 5.1 Role Registry

The role registry loads and validates role definitions from built-in config and installed role packages.

Target inputs:

```text
config/roles/registry.yaml
config/roles/*.yaml
prompts/roles/*.md
~/.hermes/role-packages/*/role-package.yaml
```

The registry exposes a normalized role catalog to router, orchestrator, runtime factory, and pipeline recipe executor.

### 5.2 Data-driven routing

The router uses role configuration to select specialized roles and pipelines.

Target behavior:

```text
engineering request → engineering_review_pipeline
job/vacancy/application request → recruiter pipeline
image/design/creative generation request → artist pipeline
ordinary question → default_conversation_pipeline / no specialized pipeline
```

Routing must remain conservative. When confidence is low or specialized intent is unclear, the router should choose default conversation or ask for clarification, not overroute into specialized execution.

### 5.3 Workflow recipe execution

A workflow recipe defines an ordered list of stages, their executor primitive, input mapping, output schema, stop gates, and error policy.

For MVP, workflow recipes should be limited to linear workflows or simple DAGs. They must not become arbitrary code execution.

### 5.4 Tool and policy boundaries

Effective tool access is computed by intersection:

```text
effective tools =
  global allowed tools
  ∩ platform/session allowed tools
  ∩ role requested tools
  ∩ pipeline allowed tools
  - hard denies
```

Role packages cannot expand their privileges. They can only request capabilities from already registered executor primitives and tool categories.

## 6. Config schema v1

A role config should be explicit, strict, and easy to validate.

Illustrative schema:

```yaml
schema_version: role_package_v1
id: hermes_recruiter
kind: role
display_name: Recruiter
status: active

routing:
  priority_class: career
  min_confidence: 0.75
  triggers:
    ru:
      - вакансия
      - податься
      - сопроводительное письмо
      - резюме
      - cv
      - cl
      - recruiter
      - linkedin
    en:
      - job
      - vacancy
      - apply
      - cover letter
      - resume
      - cv
      - recruiter
      - hiring

workflow:
  default_pipeline_id: recruiter_evaluation_pipeline
  supported_pipeline_ids:
    - recruiter_evaluation_pipeline
    - recruiter_application_pack_pipeline

model_policy:
  primary_model: gpt-5.4
  fallback_model: gpt-5.4-mini
  allow_fallback: true

prompts:
  system: prompts/roles/hermes_recruiter.md

tools:
  allow_categories:
    - web_search
    - browser_read
    - document_generation
    - job_intel
  deny_categories:
    - git_write
    - shell
    - gateway_restart
    - live_config_write

outputs:
  artifacts:
    - fit_assessment
    - cv
    - cover_letter
    - recruiter_message
  default_stop_gate: user_review

safety:
  repo_mutation_allowed: false
  outbound_message_send_allowed: explicit_user_approval_only
  source_facts_required: true
```

Artist example:

```yaml
schema_version: role_package_v1
id: hermes_artist
kind: role
display_name: Artist
status: active

routing:
  priority_class: creative
  min_confidence: 0.75
  triggers:
    ru:
      - сделай картинку
      - нарисуй
      - сгенерируй изображение
      - ценник
      - постер
      - иллюстрация
      - дизайн
    en:
      - create image
      - generate image
      - draw
      - poster
      - label
      - illustration
      - design

workflow:
  default_pipeline_id: artist_image_pipeline
  supported_pipeline_ids:
    - artist_prompt_only_pipeline
    - artist_image_pipeline

stages:
  - id: prompt_writer
    executor: text_model
    role_prompt: prompts/roles/hermes_artist_prompt_writer.md
    output_schema: image_prompt_v1

  - id: image_generation
    executor: image_generation
    input_from: prompt_writer.final_prompt
    output: generated_image

model_policy:
  primary_model: gpt-5.4
  fallback_model: gpt-5.4-mini
  allow_fallback: true

tools:
  allow_categories:
    - image_generation
  deny_categories:
    - git_write
    - shell
    - filesystem_write
    - gateway_restart
    - live_config_write

outputs:
  artifacts:
    - image_prompt
    - generated_image
  default_stop_gate: deliver_to_user

safety:
  repo_mutation_allowed: false
  requires_uploaded_image_for_user_likeness: true
  requires_existing_image_for_edit: true
```

## 7. Registry and validation rules

### 7.1 Loading order

Recommended loading order:

```text
built-in roles
→ built-in workflow recipes
→ installed role packages
→ local operator overrides, if explicitly allowed
```

### 7.2 Built-in vs external behavior

Built-in role configuration should be fail-closed. If built-in role config is invalid, tests or startup validation should fail.

External role packages should be fail-soft. If an external package is invalid, Hermes should mark it as broken/disabled and continue running.

### 7.3 Duplicate IDs

Duplicate role IDs must be rejected.

External packages must not override built-in role IDs unless a future explicit override mechanism exists and is protected by an operator-controlled allowlist.

### 7.4 Unknown capabilities

Unknown executor primitives, tool categories, output schemas, or pipeline IDs must be rejected by validation.

### 7.5 Prompt path validation

Prompt paths must be inside allowed prompt/package roots. Role packages must not reference arbitrary host paths.

### 7.6 No arbitrary code

Role packages must not contain executable Python, shell scripts, or dynamic imports as part of runtime execution.

A role package may include tests/docs/examples, but runtime execution must go through registered executor primitives only.

## 8. Routing rules

Routing should combine deterministic trigger matching with optional LLM arbitration.

Recommended routing stages:

```text
1. hard safety/policy blockers
2. deterministic strong trigger match
3. role priority and ambiguity handling
4. optional LLM classifier/arbitration
5. conservative final selection
```

Expected routing examples:

```text
"посмотри вот эту вакансию" → hermes_recruiter
"подготовь CV и CL" → hermes_recruiter
"сделай картинку ценника для печенек" → hermes_artist
"нарисуй постер" → hermes_artist
"сколько времени во Владивостоке?" → default/no specialized pipeline
"поправь тесты в репозитории" → engineering_review_pipeline
```

Ambiguous requests should not overroute. If the router is not confident, it should return default conversation or ask a clarifying question.

Routing evidence should include:

```text
selected_role_id
selected_pipeline_id
router_strategy
router_confidence
matched_triggers
candidate_roles
rejection reasons
```

## 9. Role context injection

When a role is selected, Hermes should inject role-specific context into the selected subagent/pipeline only for that turn.

Role context should include:

```text
role identity
role task boundaries
allowed outputs
forbidden actions
model policy
workflow policy
stop gates
user/project-specific context, if allowed
```

Role context must be ephemeral per turn unless explicit persistence is requested.

Non-selected roles must not influence default conversation.

## 10. Workflow recipe executor

The workflow recipe executor should support config-defined stage execution while preserving strict runtime control.

Minimum stage fields:

```yaml
id: analyze
executor: text_model
input_from: user_message
output_schema: recruiter_fit_assessment_v1
on_error: block
```

Allowed MVP executor primitives:

```text
text_model
web_research
document_render
controlled_response
```

Future executor primitives:

```text
image_generation
gmail_draft
crm_update
calendar_action
```

Executor primitives that perform side effects must require explicit policy and explicit user approval where appropriate.

Workflow execution report should include:

```text
workflow_id
stage_id
executor
input source summary
output schema validation status
status
blocked_reason
artifacts_created
duration
```

## 11. Recruiter role MVP

### 11.1 Purpose

The recruiter role helps the user evaluate vacancies and prepare application materials.

### 11.2 Source-of-truth rules

```text
vacancy source page/text = source of truth for job facts
user profile/CV facts = source of truth for candidate facts
job-intel/CRM state = source of truth for existing opportunity status
recruiter generated documents = draft artifacts only
```

The recruiter must not invent candidate facts.

### 11.3 MVP workflows

#### recruiter_evaluation_pipeline

```text
user provides job URL or pasted vacancy
→ fetch/read vacancy if possible
→ extract title/company/location/requirements
→ evaluate fit against user profile and preferences
→ identify risks and missing information
→ produce apply/maybe/skip recommendation
```

#### recruiter_application_pack_pipeline

```text
vacancy evaluation says apply or user explicitly asks to prepare materials
→ generate CV positioning notes
→ generate cover letter draft
→ generate recruiter/LinkedIn message draft
→ optionally generate follow-up message draft
→ stop for user review
```

### 11.4 Allowed capabilities

```text
web/search/read
job-intel context
CRM read
document drafting
Gmail draft only if explicitly requested
CRM update only if explicitly requested
```

### 11.5 Forbidden capabilities

```text
send application without explicit approval
send email/message without explicit approval
change repo
run shell
restart gateway
write live config
mutate CRM state without explicit command
invent career facts
bypass login walls
```

### 11.6 Output artifacts

```text
fit assessment
risk flags
application strategy
CV tailoring notes
cover letter
recruiter message
follow-up message
```

### 11.7 Tests

Required tests:

```text
job vacancy request routes to recruiter
CV/CL request routes to recruiter
ordinary time question does not route to recruiter
engineering repo mutation request routes to engineering, not recruiter
recruiter cannot request git/shell tools
invalid vacancy fetch produces controlled blocked/partial response
```

## 12. Artist role MVP

### 12.1 Purpose

The artist role handles creative image generation. It uses a text model first to convert the user brief into a structured image prompt, then invokes an image generation executor.

### 12.2 Source-of-truth rules

```text
user creative brief = source of truth for requested content
prompt writer output = structured intermediate artifact
image generation executor result = source of truth for generated image availability
artist text is not proof that an image was generated
```

### 12.3 MVP workflows

#### artist_prompt_only_pipeline

```text
user asks for an image prompt or creative prompt
→ prompt writer generates structured image prompt
→ return prompt for review
```

#### artist_image_pipeline

```text
user asks to create/generate/draw image
→ prompt writer generates structured image prompt
→ validate prompt and safety requirements
→ image_generation executor generates image
→ return generated image
```

### 12.4 Structured prompt schema

Recommended intermediate schema:

```json
{
  "schema_version": "image_prompt_v1",
  "intent": "price_label",
  "final_prompt": "...",
  "style": "...",
  "text_to_render": ["..."],
  "negative_constraints": ["..."],
  "safety_notes": [],
  "requires_user_image": false,
  "requires_existing_image_target": false
}
```

### 12.5 Allowed capabilities

```text
text prompt generation
image generation
image edit only when actual image target exists
```

### 12.6 Forbidden capabilities

```text
repo changes
shell
gateway restart
live config write
pretending image generated when executor failed
editing an image that was not provided
using user likeness without uploaded reference image when required
```

### 12.7 Runtime primitive requirement

The artist role requires a reusable executor primitive:

```text
executor: image_generation
```

Before this executor exists, the role may be installed but must be blocked at runtime with a clear reason:

```text
blocked_reason=executor_not_available:image_generation
```

### 12.8 Tests

Required tests:

```text
image/design request routes to artist
artist prompt-only request returns structured prompt
artist image request invokes image_generation executor only after prompt validation
artist cannot mutate repo
missing image target blocks edit requests
user likeness request requires uploaded reference image
ordinary question does not route to artist
engineering request does not route to artist
```

## 13. Role package CLI lifecycle

Target CLI commands:

```bash
hermes role list
hermes role inspect hermes_recruiter
hermes role validate path/to/role-package.yaml
hermes role install path_or_git_url
hermes role disable hermes_artist
hermes role enable hermes_artist
hermes role remove hermes_artist
```

The CLI should expose:

```text
role status
validation errors
effective routing triggers
effective tools
effective pipelines
package source/version/hash
reason why role is not routable, if applicable
```

A lockfile should track installed packages:

```text
role id
package version
source
content hash
install timestamp
status
validation result
```

## 14. Observability and reports

Every specialized routed turn should record:

```text
selected_role_id
selected_pipeline_id
router_strategy
router_confidence
matched_triggers
effective_tool_categories
workflow_stages_invoked
executor primitives invoked
blocked_reason
final_response_source
artifacts_created
repo_changed
```

For non-engineering roles, these conditions are RED:

```text
repo_changed=true
shell invoked
git_write invoked
gateway/config write attempted
reviewer invoked unexpectedly
engineering execution invoked unexpectedly
```

For artist, additionally RED:

```text
image_generation requested without validated image prompt
image edit requested without existing image target
user likeness generated without required user image/reference policy satisfaction
```

For recruiter, additionally RED:

```text
outbound message sent without explicit approval
application submitted without explicit approval
career fact invented without source
CRM state changed without explicit command
```

## 15. Implementation slices

### Slice A — Role Registry v1, no behavior change

Implement:

```text
hermes_cli/role_registry.py
hermes_cli/role_validation.py
config/roles/registry.yaml
config/roles/*.yaml test fixtures
```

Behavior:

```text
zero configured external roles = current behavior unchanged
built-in roles validate fail-closed
external packages validate fail-soft
```

DoD:

```text
unit tests for load/merge/validate
duplicate IDs rejected
bad external role disabled, gateway not crashed
built-in engineering/default behavior unchanged
ruff clean
```

### Slice B — Data-driven routing from role configs

Implement generic role-trigger routing using registry data.

DoD:

```text
engineering smoke routing still passes
non-engineering time question stays default
recruiter examples route to recruiter
artist examples route to artist
ambiguous examples do not overroute
routing evidence includes role candidates and matched triggers
```

### Slice C — Role context injection

Inject selected role context into pipeline/subagent runtime.

DoD:

```text
selected_role_id visible in execution report
role context only appears for selected role turn
default conversation not polluted by recruiter/artist context
non-selected roles do not affect model context
```

### Slice D — Generic linear workflow recipe executor

Implement config-defined stage execution over registered executor primitives.

MVP executors:

```text
text_model
controlled_response
```

Optional if already available:

```text
web_research
document_render
```

DoD:

```text
unknown executor rejected
stage output schema validated
failed stage blocks cleanly
stage evidence appears in report
no arbitrary Python/shell from config
```

### Slice E — Recruiter role MVP

Add recruiter role config, prompt, routing tests, and MVP evaluation/application pack workflows.

DoD:

```text
job URL/text request routes to recruiter
fit assessment generated with cited/marked job facts
application pack generated on explicit request or apply recommendation
no outbound send without explicit approval
repo remains clean
```

### Slice F — Recruiter live smoke

Manual live smoke through Telegram or Slack.

Suggested smoke prompt:

```text
HERMES-ROLE-RECRUITER-SMOKE-001

Посмотри эту вакансию: <URL>. Что думаешь, стоит ли подаваться? Если да, подготовь CV positioning, cover letter и сообщение рекрутеру, но ничего не отправляй.
```

Expected verdict:

```text
GREEN_RECRUITER_APPLICATION_PACK_STOPPED_FOR_REVIEW
```

Required evidence:

```text
selected_role_id=hermes_recruiter
selected_pipeline_id=recruiter_application_pipeline or recruiter_evaluation_pipeline
no engineering execution
no repo changes
no outbound send
artifacts prepared
```

### Slice G — Image generation executor primitive

Add a reusable image generation executor primitive to runtime.

DoD:

```text
executor registered as image_generation
only callable by allowed role/pipeline/tool policy
requires validated prompt schema
handles provider/tool failure with controlled blocked response
never exposes raw tool JSON to user
```

### Slice H — Artist role MVP

Add artist role config, prompt-writer prompt, routing tests, prompt schema, and image workflow.

DoD:

```text
image request routes to artist
prompt writer emits image_prompt_v1
image_generation executor invoked after validation
generated image delivered
repo remains clean
edit/l likeness edge cases block correctly
```

### Slice I — Artist live smoke

Suggested smoke prompt:

```text
HERMES-ROLE-ARTIST-SMOKE-001

Сделай картинку ценника для домашних печенек на русском и английском. Цена: 3 штуки за 200 тенге. Стиль: добрый детский garage sale, аккуратно и читаемо.
```

Expected verdict:

```text
GREEN_ARTIST_IMAGE_GENERATED
```

Required evidence:

```text
selected_role_id=hermes_artist
selected_pipeline_id=artist_image_pipeline
prompt_writer invoked
image_generation invoked
no engineering execution
no repo changes
image delivered
```

### Slice J — Role package lifecycle CLI

Add role lifecycle CLI.

DoD:

```text
hermes role list works
hermes role inspect works
hermes role validate works
broken package shown as disabled/broken
lockfile records installed packages
```

### Slice K — Production hardening

Add observability, report surfaces, and negative tests.

DoD:

```text
reports include selected_role_id and effective tools
negative smokes for default/recruiter/artist/engineering boundaries
gateway startup protected from broken external packages
operator can disable role without code deploy
```

## 16. Live smoke plan

### 16.1 Recruiter smoke

Goal: prove recruiter routes correctly, prepares artifacts, and stops for review without outbound sends or repo changes.

Expected classification:

```text
GREEN_RECRUITER_APPLICATION_PACK_STOPPED_FOR_REVIEW
```

Bad classifications:

```text
RED_RECRUITER_ROUTED_TO_ENGINEERING
RED_RECRUITER_MUTATED_REPO
RED_RECRUITER_SENT_OUTBOUND_WITHOUT_APPROVAL
RED_RECRUITER_INVENTED_PROFILE_FACTS
RED_RECRUITER_FAILED_TO_DELIVER_ARTIFACTS
```

### 16.2 Artist smoke

Goal: prove artist routes correctly, generates a structured prompt, invokes image generation, and delivers an image without repo changes.

Expected classification:

```text
GREEN_ARTIST_IMAGE_GENERATED
```

Bad classifications:

```text
RED_ARTIST_ROUTED_TO_ENGINEERING
RED_ARTIST_MUTATED_REPO
RED_ARTIST_SKIPPED_PROMPT_VALIDATION
RED_ARTIST_IMAGE_EXECUTOR_NOT_INVOKED
RED_ARTIST_PRETENDED_IMAGE_GENERATED
```

### 16.3 Default negative smoke

Goal: prove normal non-specialized requests still stay default.

Example:

```text
Сколько времени сейчас во Владивостоке?
```

Expected classification:

```text
GREEN_NON_SPECIALIZED_ROUTED_TO_DEFAULT
```

## 17. Production safety baseline

Engineering production pilot remains commit-gate-only.

Forbidden unless explicitly requested:

```text
push
commit
stash pop/apply/drop
unbounded shell execution
live config write outside explicit operator task
outbound message send without approval
automatic application submission
automatic CRM state mutation
```

For role package implementation tasks, default restrictions remain:

```text
no push
no gateway restart
no live config change
no live validation
no provider calls
no Telegram messages
no commit unless explicitly approved
no stash touch
```

For live smokes, gateway restart and config toggle are allowed only inside the explicitly approved smoke procedure.

## 18. Rollback strategy

Role package runtime must support fast rollback:

```text
disable role in config
remove role package from registry
restore previous config backup
restart gateway if live config changed
```

Broken external package behavior:

```text
mark package broken
disable routing for that role
continue gateway startup
surface validation errors in CLI/report
```

If a role routes incorrectly in production pilot:

```text
disable that role
keep default and engineering unaffected if safe
capture routing evidence
add golden regression test
fix role triggers/priority/classifier
```

## 19. Documentation deliverables

Implementation should update or add:

```text
docs/hermes-role-package-runtime-slice-plan.md
docs/role-packages/role-package-schema-v1.md
docs/role-packages/recruiter-role.md
docs/role-packages/artist-role.md
docs/reports/smoke/<date>-recruiter-role-smoke.md
docs/reports/smoke/<date>-artist-role-smoke.md
```

## 20. Final target state

The system is complete when:

```text
1. New roles can be added by YAML + prompt when using existing executor primitives.
2. Role configs are validated before use.
3. The router uses role config data rather than hardcoded keyword branches.
4. Selected role context is injected only for the selected turn.
5. Workflow recipes execute through registered safe primitives only.
6. Effective tool policy is intersection-based and visible in reports.
7. Broken external packages do not crash the gateway.
8. Recruiter can evaluate vacancies and draft application materials without outbound sends.
9. Artist can generate image prompts and invoke image generation without repo mutation.
10. Engineering pipeline remains commit-gate-only unless explicitly changed.
```

