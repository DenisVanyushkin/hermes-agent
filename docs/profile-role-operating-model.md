# Hermes Profile Role Operating Model v1

**Purpose:** clarify the intended role-based operating model for Hermes.

**Status:** draft v1

## Overview

Hermes works best when it behaves like a coordinated set of specialized roles rather than a single generalist blob that tries to do everything badly. The point of the profile architecture is not to build a fortress of gates. Safety matters, but it is a guardrail — not the product.

The core operating idea is simple:

1. **Chief / Coordinator** routes the task to the right role.
2. The **selected role** does the work.
3. **Reviewer roles** are used only when needed.
4. **Scribe** records durable outcomes when that adds value.
5. **Production/runtime mutations** require explicit approval.

This model is designed to reduce cognitive load, improve execution quality, and keep Hermes from turning every request into a bureaucratic security ritual.

---

## Role model

### 1) Chief / Coordinator

**Purpose**
- Receives the user request.
- Interprets the task shape.
- Routes to the best role.
- Resolves cross-role conflicts.
- Decides when a human confirmation or approval is required.
- Keeps the overall task chain coherent.

**Typical tasks**
- Task classification.
- Role selection.
- Cross-role coordination.
- Determining whether a reviewer is needed.
- Determining whether Scribe should capture a durable outcome.

**Invoke when**
- Any user request arrives.
- The task could plausibly map to more than one role.
- The task may require a reviewer or explicit approval.

**Do not invoke when**
- Not applicable; Chief is the routing entry point.

**Typical next role / reviewer**
- Engineer, Scribe, Researcher, Career Strategist, Security Auditor, or General Operator depending on the task.

---

### 2) Engineer

**Purpose**
- Owns technical execution: code, tests, repository config, runtime diagnostics, controlled deployment work, and engineering fixes.
- Handles repo/code mutation as part of normal work.

**Typical tasks**
- Bug fixes.
- Refactors.
- Tests.
- Repo-local config edits.
- Runtime inspection.
- Controlled deploy/rollback workflows after approval.
- Technical troubleshooting.

**Invoke when**
- The task is engineering, infrastructure, automation, debugging, or implementation work.

**Do not invoke when**
- The task is primarily personal/admin.
- The task is mainly research/career/documentation.
- The task is ordinary and best handled by General Operator.

**Typical next role / reviewer**
- Security Auditor for sensitive code/config changes.
- Scribe for durable engineering outcomes.
- Explicit operator approval for production/runtime mutations.

---

### 3) Security Auditor

**Purpose**
- Reviews risk, exposure, permissions, and safety-sensitive changes.
- Provides review, not universal blocking.

**Typical tasks**
- Public exposure review.
- Auth/session/cookie review.
- Secrets/tokens/env review.
- Tool-permission review.
- Cloudflare / reverse proxy / firewall review.
- SSH / browser profile / upload / shell surface review.
- Security-sensitive production/runtime review.

**Invoke when**
- Sensitive code/config changed.
- A production/runtime action touches a security-sensitive area.
- Public exposure, auth, secrets, or permission changes are involved.

**Do not invoke when**
- Ordinary engineering work is not touching sensitive surfaces.
- Ordinary documentation updates.
- Normal personal/admin tasks.
- Career/job tasks unless privacy or security is actually involved.

**Typical next role / reviewer**
- Scribe for durable security outcomes.
- Explicit approval when a production/runtime action is security-sensitive.

---

### 4) Scribe

**Purpose**
- Captures durable memory, decisions, handoffs, state changes, and useful operational outcomes.
- Records truth that will matter later.

**Typical tasks**
- Decision logs.
- Handoff records.
- Open questions.
- Project state updates.
- Meaningful implementation summaries.
- Important long-term preferences when appropriate.

**Invoke when**
- The task produced durable state.
- The work is meaningful enough that a later operator would benefit from a record.
- The task changed the project in a way that should be remembered.

**Do not invoke when**
- The task is an ordinary personal/admin task.
- The action is a preview only.
- The action is noisy or ephemeral.
- Temporary logs are enough.

**Typical next role / reviewer**
- Usually none; Scribe is often the final durable step.

---

### 5) Researcher

**Purpose**
- Performs external research, source evaluation, and synthesis.
- Finds information; does not pretend to be operational truth.

**Typical tasks**
- Market/company research.
- Source gathering.
- Comparative analysis.
- Summaries from external material.
- Research notes with citations.

**Invoke when**
- The task is primarily about gathering and evaluating outside information.

**Do not invoke when**
- The task is an internal engineering change.
- The task is a normal personal/admin request.
- The task is mainly documentation or execution work.

**Typical next role / reviewer**
- Career Strategist if the research feeds job/career decisions.
- Scribe if the research should be preserved.

---

### 6) Career Strategist

**Purpose**
- Handles job search, vacancy evaluation, CV/cover-letter strategy, and career decision-making.
- Focuses on fit, positioning, and decision support.

**Typical tasks**
- Vacancy evaluation.
- Application strategy.
- CV / cover-letter drafting.
- Career planning.
- Recruiter communication strategy.

**Invoke when**
- The task is related to job search or career positioning.

**Do not invoke when**
- The task is technical engineering work.
- The task is ordinary personal/admin work.
- The task is unrelated to career.

**Typical next role / reviewer**
- Researcher if external facts are needed.
- Scribe for durable application or decision records.

---

### 7) General Operator / Personal Assistant

**Purpose**
- Fallback role for ordinary personal and administrative tasks.
- Handles the everyday stuff that does not belong to engineering, security, documentation, research, career, or trading.

**Typical tasks**
- Book a haircut.
- Create a calendar event.
- Draft or send a message.
- Arrange an appointment.
- Make a reservation.
- Coordinate a simple personal task.
- Create a reminder.
- Prepare a simple checklist.

**Invoke when**
- No specialized role matches.
- The task is ordinary, safe, and personal/admin in nature.
- The task is unclear and needs lightweight clarification.

**Do not invoke when**
- The task is clearly engineering, research, career, or documentation.
- The task touches sensitive/security-heavy surfaces better handled by another role.

**Rules**
- If no specialized profile matches, route to General Operator.
- If the task creates an external commitment, ask for confirmation before final action.
- If the task touches money, identity documents, secrets, legal/medical/financial risk, production infrastructure, or public exposure, escalate to the relevant reviewer or ask for explicit confirmation.
- Do not create project handoff artifacts for ordinary personal tasks unless a durable preference or long-term state is learned.

**Typical next role / reviewer**
- Researcher if venue or option discovery is needed.
- Security Auditor if the task crosses into sensitive territory.
- Scribe only if a durable preference or long-term state should be recorded.

---

### 8) Trading Observer / Trader, deferred

**Purpose**
- Deferred future role for trading-related observation or execution.

**Typical tasks**
- Market observation.
- Trading research.
- Risk-monitoring workflows.

**Invoke when**
- Only when trading is explicitly activated under a separate policy.

**Do not invoke when**
- In the current MVP.
- For ordinary personal, research, or engineering tasks.

**Typical next role / reviewer**
- Security Auditor for risk-sensitive surfaces.
- Scribe for durable trading-state records if/when the role is introduced.

---

## Fallback routing

The fallback rule is simple:

- If no specialized profile matches and the task is ordinary and safe, Chief routes it to **General Operator**.
- If the task is unclear, Chief either asks a minimal clarifying question or routes to **General Operator** for clarification.
- If the task is risky, Chief routes to the primary role plus the appropriate reviewer.

This avoids over-routing. Not every request needs a security ceremony.

---

## Role chains

### Engineering bugfix

**Chain:** Chief → Engineer → tests → Scribe

Use this for routine implementation and verification work.

### Sensitive engineering change

**Chain:** Chief → Engineer → Security Auditor → Scribe

Use this when the diff touches sensitive surfaces or the change has meaningful exposure risk.

### Public exposure / Cloudflare / auth change

**Chain:** Chief → Engineer → Security Auditor → explicit approval → Scribe

Use this for public exposure, auth/session, or any similar boundary change.

### Documentation update

**Chain:** Chief → Scribe

Use this when the task is mainly documentation and no implementation work is needed.

### Runbook with technical commands

**Chain:** Chief → Scribe → Engineer review

Use this when a doc contains operational commands or change-sensitive guidance that should be checked technically.

### Company/job research

**Chain:** Chief → Researcher → Career Strategist → Scribe

Use this when the work starts as external research and ends in career guidance.

### Vacancy evaluation

**Chain:** Chief → Career Strategist → Researcher if needed → Scribe

Use this when the job-fit decision is primary and external facts are supplemental.

### CV / cover letter

**Chain:** Chief → Career Strategist → Scribe

Use this for application writing and positioning.

### Personal appointment, e.g. haircut

**Chain:** Chief → General Operator → confirmation if external booking → calendar/update

Use this for ordinary personal scheduling.

### Restaurant reservation

**Chain:** Chief → General Operator → Researcher if venue discovery is needed → confirmation

Use this for dining reservations or venue selection.

### Payment or purchase

**Chain:** Chief → General Operator → explicit confirmation; escalate if financial risk is material

Use this when money changes hands or a commitment has financial impact.

---

## Mutation model

It is important to separate **repo/code mutation** from **production/runtime mutation**.

### Repo/code mutation

Engineer is allowed to modify code, tests, docs, and repo config as part of normal work.

The protection here is **post-change review**, not pre-blocking every edit.

Required checks when relevant:
- diff summary
- tests
- Security Auditor review only if sensitive files or patterns are touched
- Scribe summary when useful

#### Sensitive diff triggers

These changes should trigger heightened review:
- auth/session/cookies
- secrets/tokens/env
- Cloudflare / reverse proxy / firewall
- gateway
- cron / scheduler
- tool permissions
- file manager / shell / upload
- SSH
- browser profiles
- WebUI public access
- production deploy scripts
- database migrations
- trading / risk / execution paths

### Production/runtime mutation

This includes:
- deploy
- restart
- rollback
- systemd changes
- Cloudflare / reverse proxy / firewall changes
- secrets/auth/tool-permission changes
- scheduler/timer changes
- database migrations or repairs
- production data deletion
- trading execution

Production/runtime mutation must require explicit operator approval.
Security review is required when the production action is security-sensitive.
Break-glass can exist later, but only with explicit reason and durable logging.

---

## Security Auditor role clarification

Security Auditor is **not** a universal blocker.
It is a reviewer.

### Invoke it when
- Engineer changes sensitive code/config.
- A production/runtime action touches a security-sensitive surface.
- Public exposure, auth, secrets, or tool-permission changes are involved.

### Do not invoke it when
- The task is ordinary engineering.
- The task is ordinary documentation.
- The task is ordinary personal/admin work.
- The task is career/job work without privacy/security implications.

---

## Scribe role clarification

Scribe is for durable memory, not noise.

### Scribe should record
- project state changes
- decisions
- handoffs
- open questions
- meaningful operational outcomes
- important long-term user preferences when appropriate

### Scribe should not record
- every ordinary personal task
- every preview
- noisy ephemeral actions
- temporary logs unless needed for debugging

---

## Execution principle

Roles should reduce cognitive load and improve work quality.
They should not add bureaucracy by default.

**Default flow:**
Chief → best role → optional reviewer → optional Scribe

**Not:**
Chief → every role → every gate → every artifact

---

## Relation to PR-1 through PR-6

The existing profile control-plane commits are useful building blocks:
- profile registry and model policy
- routing preview
- approval preview
- Scribe handoff artifact
- Security review artifact
- composed profile preview

These are **control-plane primitives**.
They should support role-based execution, not turn every task into a security workflow.

---

## Recommended next implementation direction

**PR-7: Role-based execution loop design**

### Goal
- Route tasks to specialized roles for better execution.
- Add General Operator as a fallback.
- Let Engineer modify repo code normally.
- Run post-change diff/test/security review when needed.
- Require explicit approval only for production/runtime mutations.
- Use Scribe for meaningful durable outcomes, not noise.

### Non-goals
- no universal blocking gate for all tasks
- no automatic Scribe/Security artifacts for every task
- no blocking ordinary personal/admin tasks
- no activation of Trading
- no production deploy/restart without explicit approval

---

## Notes

This model intentionally resets the tone of the architecture:
- specialized roles are for better work
- safety is a guardrail
- security review is a reviewer, not a universal choke point
- ordinary personal tasks should stay lightweight
- durable artifacts should be purposeful, not spammed
