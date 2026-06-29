# Career/Search Source of Truth

Status: draft SoT contract for Hermes Recruiter MVP  
Canonical host/repo: `ssh hermes-agent && cd /home/hermes/.hermes/hermes-agent`  
Related documents:

- `docs/hermes-recruiter-action-plan.md` (present locally during this slice; missing on host at inspection time)
- `docs/hermes-recruiter-skill-package-architecture-sot.md` (present locally during this slice; missing on host at inspection time)
- [`docs/job-intel-recruiter-read-facade.md`](docs/job-intel-recruiter-read-facade.md)
- `docs/job-intel-source-of-truth.md` (present locally during this slice; missing on host at inspection time)
- `docs/job-intel-audit-sot-plan.md` (present locally during this slice; missing on host at inspection time)
- [`docs/hermes-role-package-runtime-slice-plan.md`](docs/hermes-role-package-runtime-slice-plan.md)
- local reference inputs inspected for this slice:
- `company_intelligence_architecture.md`
- `denis_vanyushkin_structured_resume_v1_1.json`
- `opportunity-thesis.md`
- `scoring_v3.md`

## 1. Purpose

This document defines the Source-of-Truth contract for the Hermes Recruiter MVP career/search/scoring slice.

The goal is to make Recruiter reuse existing sources correctly instead of inventing parallel facts, parallel scoring, or unauthorized write paths.

This slice is documentation-only.

It does not introduce:

- new Recruiter skills;
- route selection changes;
- scoring rewrites;
- scoring v4;
- company intelligence engine implementation;
- CRM writes;
- document generation flows;
- provider/model calls;
- live smoke behavior.

## 2. Core Decision

Recruiter must not invent a parallel vacancy scoring system.

Operational machine scoring already exists in job-intel and remains the source of truth for machine vacancy score and machine recommendation.

Recruiter may add strategic interpretation around that score, but must not silently replace it.

If Recruiter disagrees with the machine score, the disagreement must be explicit and user-visible.

Required framing:

```text
machine score/recommendation = operational job-intel output
recruiter interpretation = strategic explanation / context / escalation / caution
```

Not allowed:

```text
Recruiter internally computes a second hidden score
and presents it like the authoritative vacancy score
and discards or overrides job-intel scoring without explanation
```

## 3. Source Hierarchy

Recruiter source hierarchy for MVP:

```text
candidate facts
-> opportunity thesis
-> company intelligence thesis
-> operational job-intel scoring
-> application/status/history via recruiter_read_facade
```

Interpretation of the hierarchy:

1. Candidate facts define what is true about Denis.
2. Opportunity thesis defines what Denis should strategically optimize for.
3. Company intelligence thesis defines what kinds of companies deserve attention and why.
4. Job-intel scoring defines the operational machine evaluation of a specific vacancy.
5. Application/status/history comes from job-intel state, but only through the read-only facade.

Generated outputs sit below all of the above and never become source of truth automatically.

## 4. Ownership Map

### 4.1 Candidate facts

Canonical source:

- structured candidate profile / resume-derived JSON, such as `denis_vanyushkin_structured_resume_v1_1.json`;
- explicit Denis-confirmed facts;
- future approved career SoT artifacts when added to repo docs.

Candidate facts own:

- identity;
- current and previous roles;
- companies worked at;
- scope stated in source materials;
- explicitly stated achievements;
- explicitly stated responsibilities;
- known geography base;
- known contact details when present in source artifacts.

### 4.2 Opportunity thesis

Canonical source:

- `opportunity-thesis.md` and future repo-native SoT copies when added.

Opportunity thesis owns:

- target role families;
- acceptable seniority range;
- target geographies;
- target business situations;
- anti-thesis / deprioritized directions;
- strategic promotion and rejection rules.

### 4.3 Company intelligence thesis

Canonical source:

- `company_intelligence_architecture.md` and future repo-native SoT copies when added.

Company intelligence thesis owns:

- company archetypes;
- company score meaning at the thesis level;
- signal taxonomy;
- monitoring priority logic;
- rationale for why a company is strategically interesting.

### 4.4 Operational vacancy scoring

Canonical source:

- [`job_intel/evaluator.py`](job_intel/evaluator.py)
- [`job_intel/seed/scoring.yaml`](job_intel/seed/scoring.yaml)
- local scoring references inspected for this slice, including `scoring_v3.md`
- future repo-native v3 scoring audit/readiness docs when present on host

Operational scoring owns:

- machine vacancy score;
- machine recommendation/tier;
- matched scoring signals;
- concerns and reasons emitted by the scorer;
- explicit gate behavior for v3 shadow logic.

### 4.5 Application/status/history

Canonical source:

- [`job_intel/recruiter_read_facade.py`](job_intel/recruiter_read_facade.py)
- underlying job-intel DB state, accessed only through the facade from Recruiter MVP behavior.

Application/status/history owns:

- current opportunity status;
- historical opportunity events;
- vacancy lookup by ID or URL when exposed through facade methods;
- provenance and staleness warnings attached to read-side data.

### 4.6 Generated drafts

Generated drafts own nothing authoritative.

They may contain:

- CV drafts;
- cover-letter drafts;
- recruiter-message drafts;
- positioning summaries;
- evidence maps;
- application-answer drafts.

They remain drafts unless Denis explicitly confirms a claim or approves a state transition through a separate allowed workflow.

## 5. Candidate Facts Contract

### 5.1 Canonical candidate profile source

The canonical candidate profile must come from resume-derived structured data and Denis-confirmed facts.

For this slice, the inspected reference was `denis_vanyushkin_structured_resume_v1_1.json`.

Important rule:

```text
derived fields are usable only when they remain marked as derived
```

Examples of derived content:

- positioning tags;
- inferred strengths;
- inferred company-fit statements;
- inferred leadership themes.

### 5.2 What Recruiter may use directly

Recruiter may use directly:

- titles present in the candidate source;
- employers present in the candidate source;
- responsibilities present in the candidate source;
- achievements present in the candidate source;
- explicitly stated scope when present;
- explicitly stated location/contact details when relevant;
- clearly labeled derived summaries when presented as interpretation rather than fact.

### 5.3 What requires explicit Denis confirmation

Recruiter must not state the following as fact unless explicitly confirmed by Denis:

- invented KPI values;
- invented revenue numbers;
- invented salary history;
- invented budget ownership values;
- invented P&L values;
- invented team-size numbers not present in source;
- invented title changes or title inflation;
- invented board/executive reporting lines;
- invented market-share claims;
- invented launch ownership beyond source evidence.

### 5.4 Hard anti-hallucination rules

Recruiter must not invent:

- achievements;
- metrics;
- compensation numbers;
- budget sizes;
- P&L ownership values;
- title upgrades;
- direct reports;
- geographic mobility claims;
- language fluency claims;
- domain depth not grounded in source material.

If a desired claim is plausible but unconfirmed, Recruiter must either:

- omit it; or
- present it as a question / confirmation request to Denis.

## 6. Opportunity Thesis Contract

The opportunity thesis is the strategic filter above operational vacancy scoring.

Based on the inspected `opportunity-thesis.md`, the thesis currently emphasizes:

- executive product and transformation leadership;
- B2C digital ecosystems;
- telecom / fintech adjacency;
- business ownership, monetization, and path toward P&L scope;
- complex multi-stakeholder environments;
- preference toward CPO / VP Product / Head of Product / Head of Digital Business type directions.

The opportunity thesis contract owns:

- target role families;
- target company situations;
- geography strategy;
- anti-thesis / avoid patterns;
- strategic decision rules for what should be promoted, tolerated, or rejected.

Recruiter should use the opportunity thesis to answer questions like:

- even if this vacancy scores reasonably, does it fit the intended career direction?
- is this role broad enough strategically?
- is this company situation the right kind of transition?
- does this opportunity build toward the desired executive trajectory?

Operational score alone is not enough to answer those questions.

## 7. Company Intelligence Contract

The company intelligence layer is thesis-first, not source-first.

Based on the inspected `company_intelligence_architecture.md`, company intelligence should answer:

```text
Why is this company worth attention for Denis specifically?
```

It owns:

- company archetypes;
- company score as a thesis-first radar construct;
- signal taxonomy;
- monitoring allocation logic;
- strategic explanation of company attractiveness.

Recruiter should use company intelligence to:

- explain why a company fits or does not fit Denis's trajectory;
- separate role-title attractiveness from company attractiveness;
- highlight company context that the machine vacancy score may not fully express;
- prioritize company follow-up even when a single vacancy is ambiguous;
- deprioritize superficially attractive titles in weak company contexts.

Important boundary:

- company intelligence may influence strategic interpretation;
- it does not replace the operational vacancy score;
- it does not authorize hidden re-scoring.

## 8. Job-Intel Scoring Contract

### 8.1 Operational scoring SoT

Existing job-intel scoring is the operational scoring source of truth.

Relevant files:

- [`job_intel/evaluator.py`](job_intel/evaluator.py)
- [`job_intel/seed/scoring.yaml`](job_intel/seed/scoring.yaml)

Observed scoring entry points and behavior anchors on host:

- `classify_vacancy(...)`
- `tier_for_score(...)`
- `salary_tier_for(...)`
- `score_vacancy_with_version(...)`
- `score_vacancy_v3_shadow(...)`
- `_evaluation_from_v3_shadow(...)`

### 8.2 Config vs logic boundary

`scoring.yaml` is configuration/seed, not the complete scoring source of truth.

The decisive scoring logic lives in `job_intel/evaluator.py`.

Therefore:

```text
Do not treat YAML alone as the full scoring contract.
```

### 8.3 v3 interpretation

The inspected scoring references describe v3 as gate-based operational scoring evidence.

For Recruiter MVP contract purposes:

- v3 logic is operational evidence, not a narrative layer;
- Recruiter may explain why gates passed, failed, or remained unknown;
- Recruiter must not restate v3 as a different private rubric.

### 8.4 Machine score vs recruiter interpretation

Allowed pattern:

```text
Machine score: possible_fit
Machine concerns: missing product-leadership proof, geography ambiguity
Recruiter interpretation: strategically weak because the company and role path do not build toward the target executive thesis
```

Also allowed:

```text
Machine score: reject
Recruiter interpretation: strategically interesting company, but current vacancy should still be treated as a no-go unless new evidence appears
```

Not allowed:

```text
Machine score: reject
Recruiter silently treats it as strong_fit without explicit disagreement labeling
```

### 8.5 No parallel Recruiter scoring system

Recruiter must not introduce:

- a second hidden numeric score;
- a second hidden recommendation scale;
- hidden threshold overrides;
- shadow score replacement in prompts or drafts.

If Recruiter adds judgment, it must be phrased as one of:

- strategic interpretation;
- thesis alignment note;
- caution;
- escalation note;
- uncertainty note.

## 9. Facade Integration Contract

Recruiter reads application/status/history through the read-only facade only.

Relevant file:

- [`job_intel/recruiter_read_facade.py`](job_intel/recruiter_read_facade.py)

MVP rules:

- no direct SQLite reads from Recruiter skills;
- no direct DB path access from Recruiter prompts/skills;
- no direct `crm_service` usage;
- no direct `crm_reconciler` usage;
- no direct `OpportunityRepository` or equivalent write-adjacent repository usage from Recruiter behavior;
- no write paths in MVP.

Why this boundary exists:

- schema coupling must stay localized;
- read-side provenance and staleness warnings should come from one boundary;
- write-capable CRM modules sit too close to read methods;
- MVP scope explicitly excludes state mutation.

## 10. Drafts vs Facts Boundary

Generated CVs, cover letters, recruiter messages, summaries, and application answers are drafts.

Drafts are not source of truth.

Drafts may synthesize existing facts, but they do not create new facts.

Promotion rule:

```text
A generated claim becomes reusable candidate fact only after explicit Denis confirmation.
```

Application status rule:

```text
Application status changes require explicit approval and are outside Recruiter MVP.
```

This means Recruiter must not:

- treat generated text as canonical biography;
- feed generated text back into candidate facts automatically;
- claim an application was sent or updated;
- claim a CRM state changed;
- treat persuasive phrasing as evidence.

## 11. Minimal Conceptual Packets

These packets are conceptual only.

They are intentionally lightweight and are not implementation schemas.

### 11.1 CandidateFactsPacket

Purpose:

- provide canonical candidate facts plus clearly labeled derived fields.

Conceptual contents:

- identity and current location;
- roles and employers;
- confirmed achievements/responsibilities;
- confirmed scope facts;
- derived positioning notes marked as derived;
- open confirmation gaps.

### 11.2 OpportunityThesisPacket

Purpose:

- provide strategic search direction.

Conceptual contents:

- target role families;
- target company types;
- geography preferences;
- anti-thesis signals;
- decision rules for promote / tolerate / reject.

### 11.3 JobIntelScorePacket

Purpose:

- carry the machine scoring result without rewriting it.

Conceptual contents:

- scoring version/source;
- score/tier/recommendation;
- matched signals;
- concerns/reasons;
- provenance;
- staleness or missing-data warnings when available.

### 11.4 RecruiterPositioningPacket

Purpose:

- map vacancy requirements to Denis evidence and strategic fit.

Conceptual contents:

- strongest alignment points;
- weak/missing evidence;
- strategic fit commentary;
- explicit disagreement note if recruiter interpretation differs from machine scoring;
- follow-up questions for Denis.

### 11.5 DocumentDraftPacket

Purpose:

- hold generated draft outputs without elevating them to fact.

Conceptual contents:

- draft type;
- intended audience/use;
- fact sources used;
- unresolved placeholders or confirmation gaps;
- safety note that draft text is not authoritative fact.

## 12. Recruiter Output Rules

Recruiter outputs should keep these layers distinct:

1. Confirmed candidate fact
2. Derived candidate interpretation
3. Strategic opportunity thesis
4. Company-intelligence thesis context
5. Operational machine score
6. Recruiter strategic interpretation
7. Draft output text

A good output makes the layer visible when it matters.

A bad output collapses all layers into one confident narrative.

## 13. Practical Decision Rules

### 13.1 When machine score is strong but thesis fit is weak

Recruiter may say:

- operationally strong vacancy;
- strategically weak fit for Denis's intended path.

### 13.2 When thesis fit is interesting but machine score is weak

Recruiter may say:

- strategically interesting company/context;
- current vacancy still weak operationally;
- watch company, not necessarily this vacancy.

### 13.3 When candidate proof is insufficient

Recruiter must:

- avoid invented claims;
- request confirmation; or
- downgrade confidence explicitly.

### 13.4 When history/status is needed

Recruiter must:

- obtain it through the facade only;
- preserve provenance/staleness cues;
- avoid implied write actions.

## 14. MVP Non-Goals Reconfirmed

This contract does not authorize implementation of:

- Recruiter routing changes;
- Recruiter skill package loading;
- scoring rewrite or v4;
- company intelligence engine implementation;
- CRM writeback;
- direct DB reads from skills;
- outbound sends;
- document-generation automation;
- live provider/model execution;
- application-state mutation.

## 15. Source Inventory for This Slice

### 15.1 Present on hermes-agent during inspection

- `docs/job-intel-recruiter-read-facade.md`
- `docs/hermes-role-package-runtime-slice-plan.md`
- `job_intel/evaluator.py`
- `job_intel/seed/scoring.yaml`
- `job_intel/recruiter_read_facade.py`

### 15.2 Present locally but missing on hermes-agent during inspection

- `docs/hermes-recruiter-action-plan.md`
- `docs/hermes-recruiter-skill-package-architecture-sot.md`
- `docs/job-intel-source-of-truth.md`
- `docs/job-intel-audit-sot-plan.md`
- `company_intelligence_architecture.md`
- `denis_vanyushkin_structured_resume_v1_1.json`
- `opportunity-thesis.md`
- `scoring_v3.md`

## 16. Bottom Line

For Hermes Recruiter MVP:

- candidate facts are not generated text;
- opportunity thesis is not vacancy scoring;
- company intelligence is not a hidden score override;
- job-intel scoring remains the operational machine score source of truth;
- application/status/history must be read through `recruiter_read_facade` only;
- drafts remain drafts until Denis explicitly confirms facts or separately approves state changes.
