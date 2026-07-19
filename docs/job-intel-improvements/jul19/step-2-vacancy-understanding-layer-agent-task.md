# Step 2 Agent Task — Vacancy Understanding Layer

**Project:** Job Intel Career Preference System  
**Step:** 2 of 5  
**Canonical host:** `hermes-agent`  
**Repository:** `/home/hermes/.hermes/hermes-agent`  
**Branch:** `local/customizations`  
**Execution mode:** bounded implementation on canonical host only  
**Production impact:** strictly none  
**Status:** ready for execution after Step 1 KZ sponsorship regression fix is committed and green

---

## 1. Objective

Build the **Vacancy Understanding Layer**: a canonical, versioned, evidence-backed representation of what a vacancy actually means.

This layer must describe the vacancy independently of Denis's preferences.

It must answer questions such as:

- What is the real breadth of the mandate?
- Is this a feature role, domain role, business-line role, regional role, or portfolio role?
- Is the role close to revenue or P&L?
- Is “platform” the business itself, or only platform engineering / DevEx?
- Is the job product leadership, digital-business ownership, hybrid GM/commercial ownership, or a non-product support function?
- Is relocation feasible, uncertain, or impossible based on the posting?
- Does the role require a non-transferable domain or language barrier?
- What company characteristics are observable from the vacancy and company evidence?
- Which facts are explicit, which are inferred, and which remain unknown?

The layer created in this step will later be consumed by:

- Step 3 shadow evaluator;
- production recommendation ranking after controlled rollout;
- company evaluation;
- CV tailoring;
- cover-letter generation;
- recruiter messaging;
- analytics and feedback learning.

The output of Step 2 is **not a score and not a recommendation**.

---

## 2. Source of Truth and required inputs

Read and treat as binding:

1. `docs/job-intel-improvements/jul19/job-intel-career-preference-system-development-sot.md`
2. `docs/job-intel-improvements/jul19/career-preference-model.md`
3. `job_intel/preference_model/career-preference-model.yaml`
4. `job_intel/preference_model/model.py`
5. `docs/job-intel-improvements/jul19/career-preference-model-migration-map.md`
6. `docs/audit/2026-07-19-career-preference-model.md`
7. `docs/audit/2026-07-19-recommendation-system-audit.md`
8. existing vacancy ingestion, normalization, storage, evaluator, company-intelligence, and feedback code

Before implementation, verify that the Step 1 follow-up regression is present:

```text
KZ local role + sponsorship unknown
→ feasibility eligible for fallback lane
→ never uncertain solely because sponsorship is unknown
```

If that fix is not present, do not silently compensate inside Step 2. Report it as a precondition failure.

---

## 3. Core architectural principle

There must be three independent layers:

```text
Career Preference Model
    describes Denis

Vacancy Understanding Model
    describes the vacancy and relevant company facts

Shadow Evaluator
    compares the first two
```

Step 2 implements only the second layer.

The Vacancy Understanding Layer must contain no user-specific desirability weights, no recommendation bands, and no apply/reject decision.

A vacancy fact may be extracted because it is useful to the current preference model, but its semantic definition must remain candidate-independent.

Bad example:

```yaml
is_good_for_denis: true
```

Good example:

```yaml
mandate:
  scope_breadth:
    value: region
    confidence: high
    evidence:
      - "Own APAC growth and expansion..."
```

---

## 4. Deliverables

Create a coherent Step 2 package with at least the following artifacts.

### 4.1 Canonical Pydantic contract

Suggested location:

```text
job_intel/vacancy_understanding/model.py
```

The model must:

- use Pydantic v2;
- use `extra="forbid"` throughout;
- have strict enums where semantics are closed;
- support explicit `unknown`;
- represent evidence and confidence per extracted field;
- distinguish explicit fact, deterministic derivation, semantic inference, and external/company enrichment;
- carry schema and extractor version;
- support stable serialization to JSON/YAML;
- avoid importing the preference model at runtime.

### 4.2 Generated JSON Schema

Suggested location:

```text
job_intel/vacancy_understanding/vacancy-understanding.schema.json
```

The schema must be generated from Pydantic and protected by a sync test.

### 4.3 Human-readable semantic contract

Suggested location:

```text
docs/job-intel-improvements/jul19/vacancy-understanding-model.md
```

It must explain:

- purpose and boundaries;
- every field and enum;
- explicit vs inferred vs unknown semantics;
- evidence requirements;
- confidence rules;
- deterministic vs semantic extraction boundary;
- platform-as-business vs platform-engineering distinction;
- scope-breadth semantics;
- feasibility fact semantics;
- company-fact semantics;
- downstream consumer contract;
- versioning and migration rules.

### 4.4 Canonical feature dictionary

Suggested location:

```text
job_intel/vacancy_understanding/feature-definitions.yaml
```

For every feature include:

```yaml
id:
description:
type:
allowed_values:
unknown_allowed:
source_priority:
positive_examples:
negative_examples:
ambiguity_notes:
consumer_notes:
```

This is a semantic dictionary, not a scoring config.

### 4.5 Golden dataset

Suggested location:

```text
tests/fixtures/vacancy_understanding/
```

Create a compact but high-value dataset using real historical vacancies or sanitized snapshots from the live DB / stored source payloads.

Minimum required cases:

1. Wise — Product Director, APAC Growth & Expansion
2. Wise — Pricing
3. Wise — Acquiring
4. Wise — Financial Crime
5. Wise — Onboarding Experience
6. Airwallex — Head of Product, Global Payments Network Infrastructure
7. Airwallex — Payment Fraud
8. Monzo — Senior Product Director, Business Banking
9. Monzo — Flex / Borrowing
10. Brex — Director of Product, Growth/AI
11. Affirm — Senior Director, Product Management, Remote US
12. Coinbase — Core Infrastructure / Developer Infrastructure
13. OKX — role with crypto + Chinese domain barrier
14. one pure sales / strategic product sales role
15. one FP&A / finance support role
16. one KZ local fallback role
17. one remote role with large timezone difference
18. one onsite non-US role with unknown sponsorship
19. one US onsite role without explicit sponsorship
20. one US onsite role with explicit sponsorship, if evidence exists; otherwise create a clearly marked synthetic policy-control fixture

Use real source text where legally and operationally available. If a fixture is synthetic, label it explicitly and never mix it with behavioral evidence metrics.

### 4.6 Contract tests and golden tests

Suggested location:

```text
tests/job_intel/test_vacancy_understanding_model.py
tests/job_intel/test_vacancy_understanding_golden.py
```

### 4.7 Extraction boundary / implementation plan

Suggested location:

```text
docs/job-intel-improvements/jul19/vacancy-understanding-extraction-plan.md
```

Document:

- what can be extracted deterministically now;
- what requires semantic extraction;
- what requires company enrichment;
- what cannot be known from a vacancy;
- cache and versioning strategy;
- migration from legacy fields;
- failure and fallback behavior;
- future provider-backed extraction design.

Do not implement live provider execution in this step unless explicitly approved by a later slice.

---

## 5. Required canonical model

The exact class names may differ, but the semantic structure must cover the following.

```yaml
vacancy_understanding:
  metadata:
  source:
  role_identity:
  mandate:
  organization:
  company:
  feasibility_facts:
  requirements:
  risks:
  evidence_registry:
  extraction_diagnostics:
```

---

## 6. Required field families

## 6.1 Metadata

Required:

- `schema_version`
- `extractor_version`
- `created_at`
- `vacancy_key`
- `source_system`
- `source_record_id`
- `source_content_hash`
- `language`
- `is_synthetic_fixture`
- `production_integration`

`production_integration` must remain `false` in Step 2 fixtures and runtime package.

---

## 6.2 Role identity

Represent at least:

- normalized title;
- raw title;
- title family;
- employment type if known;
- management level as observed;
- title seniority evidence;
- function family.

Title must remain evidence, not a final truth about scope.

Recommended title/function families:

```text
product
growth
general_management
commercial
strategy
operations
engineering
sales
finance
project_delivery
other
unknown
```

Hybrid roles must be representable without forcing a single misleading category.

---

## 6.3 Mandate

Required semantic features:

### `scope_breadth`

Closed ordinal:

```text
feature
domain
business_line
region
portfolio
enterprise
unknown
```

Definitions must be precise.

Examples:

- onboarding flow → `feature` or narrow `domain`
- pricing → `domain`
- business banking → `business_line`
- APAC growth & expansion → `region`
- several product domains → `portfolio`
- company-wide CPO / GM digital → `enterprise`

### `revenue_proximity`

Closed ordinal:

```text
support
enabling
indirect
direct_revenue
direct_pnl
unknown
```

### Boolean / tri-state facts

Use explicit `true | false | unknown`, not absent-field ambiguity:

- growth mandate;
- expansion mandate;
- monetization core;
- pricing core;
- acquiring core;
- P&L ownership;
- strategy ownership;
- org design mandate;
- team build/rebuild mandate;
- executive exposure;
- board exposure;
- market-entry ownership;
- turnaround mandate;
- zero-to-one mandate;
- digital business ownership;
- platform-as-business;
- platform-engineering;
- internal-tools / back-office;
- feature-delivery-only;
- maintenance / optimize-only.

### `transformation_phase`

Allow multiple values when justified:

```text
build
scale
turnaround
expand
optimize
maintain
unknown
```

### `mandate_summary`

A concise candidate-independent synthesis:

```text
"Regional growth and market-expansion ownership with direct revenue accountability."
```

It must be evidence-backed and must not include a recommendation.

---

## 6.4 Organization and authority

Represent:

- reports-to level;
- reports-to title if explicit;
- estimated direct reports;
- estimated total organization scope;
- cross-functional leadership;
- hiring authority;
- budget ownership;
- org-design authority;
- decision authority;
- geographic responsibility;
- product portfolio responsibility.

Unknown must be preserved.

Do not infer people scope from a senior title alone.

---

## 6.5 Company facts

Required:

- company name;
- company scale;
- geographic footprint;
- company stage;
- business model;
- customer model;
- platform/ecosystem shape;
- B2C / SMB-mass / B2B-enterprise characteristics;
- crypto exchange employer;
- outsourcing / agency;
- local-only company;
- product-culture signal;
- emerging-markets footprint;
- brand recognition signal;
- maturity / bureaucracy signal.

Important:

These are observable or enriched company facts, not company-fit verdicts.

Do not encode:

```yaml
company_quality: bad
```

Instead encode:

```yaml
is_crypto_exchange: true
global_scale: global
brand_recognition: known
regulatory_risk_signal: present
```

Company facts derived from external sources must identify the source separately from vacancy text.

---

## 6.6 Feasibility facts

Step 2 does not apply Denis's feasibility policy, but it must extract the facts needed by Step 3.

Required:

- country;
- city;
- country group;
- work format;
- remote geography restrictions;
- relocation support stated;
- visa sponsorship stated;
- work authorization required;
- candidate must already be authorized;
- timezone expectations;
- required working hours;
- travel requirement;
- language requirements;
- local-market indicator.

Recommended enums:

```text
sponsorship_stated:
  yes
  no
  unknown

relocation_support:
  explicit
  implied
  absent
  unknown
```

The country-group resolver must be a separate, versioned concern.

Do not maintain sanctions or political-instability truth inside the preference YAML.

For Step 2:

- specify the resolver contract;
- identify authoritative future sources;
- allow manually curated snapshot/version;
- keep resolution explainable;
- never infer `sanctioned` from free-text intuition.

KZ local + sponsorship unknown must remain a factual combination, not an error.

---

## 6.7 Requirements and entry barriers

Represent:

- required years of experience;
- mandatory industry experience;
- mandatory domain expertise;
- regulatory expertise;
- technical expertise;
- language requirements;
- location authorization;
- education requirements;
- certification requirements.

Add semantic classification:

```text
transferability:
  transferable
  adjacent
  specialized_but_learnable
  non_transferable_barrier
  unknown
```

A barrier classification must include:

- the explicit requirement;
- why it was classified;
- evidence;
- confidence.

Do not label crypto context itself as a barrier. The barrier arises only when deep prior crypto expertise is mandatory or when another non-transferable requirement exists.

---

## 6.8 Risks

Risks are factual warnings, not preference penalties.

Examples:

- relocation unclear;
- sponsorship absent;
- timezone burden;
- title/scope mismatch;
- ambiguous P&L;
- company facts missing;
- role appears reposted;
- source text incomplete;
- internal contradiction;
- low extraction confidence.

Risks must never directly become `reject`.

---

## 6.9 Evidence

Every semantically inferred field must support:

- source type;
- exact or bounded source excerpt;
- source location / field;
- extraction method;
- confidence;
- optional rationale.

Recommended source types:

```text
vacancy_text
structured_source_field
company_enrichment
deterministic_derivation
semantic_inference
manual_gold_annotation
```

Avoid duplicating full copyrighted job descriptions in fixtures. Store only what is necessary for reliable regression tests and comply with source retention policies.

---

## 6.10 Confidence and unknown semantics

Required confidence enum:

```text
high
medium
low
unknown
```

Rules:

- Explicit statement normally → high.
- Strong deterministic derivation → high or medium.
- Semantic inference with direct supporting language → medium.
- Weak title-only inference → low.
- No evidence → value must be unknown, not guessed.

Unknown is a first-class value.

The extractor must not convert missing data to false.

---

## 7. Deterministic vs semantic extraction boundary

Create and document a strict boundary.

## 7.1 Deterministic candidates

Examples:

- raw title;
- explicit location;
- work-format phrases;
- explicit remote restrictions;
- explicit relocation / sponsorship phrases;
- explicit languages;
- explicit years of experience;
- source IDs and timestamps;
- exact reports-to phrases;
- explicit team size;
- explicit P&L wording;
- known company identifier.

## 7.2 Semantic candidates

Examples:

- scope breadth;
- platform-as-business vs platform engineering;
- digital business ownership;
- revenue proximity;
- narrow feature scope;
- transformation phase;
- domain transferability;
- product-culture signal;
- mandate summary;
- title/scope mismatch.

## 7.3 External enrichment candidates

Examples:

- company stage;
- company size;
- global footprint;
- brand tier;
- crypto-exchange status;
- outsourcing status;
- emerging-market presence.

The vacancy understanding record must show where each fact came from.

---

## 8. Minimal extractor implementation for Step 2

Implement only enough deterministic extraction and fixture loading to validate the model.

Allowed:

- pure functions;
- regex / structured-field parsing;
- source-normalization helpers;
- manual gold annotations;
- local replay against stored vacancy text;
- no-network test fixtures.

Not allowed without separate approval:

- live LLM/provider calls;
- modifying production evaluation;
- sending Slack cards;
- changing cron;
- changing gateway configuration;
- writing new verdicts into the live DB;
- changing current score/band;
- changing source acquisition;
- auto-enriching companies from the internet.

A provider interface may be defined as a future extension point, but it must be disabled and unused.

---

## 9. Golden dataset rules

Each case must include:

```yaml
fixture_id:
vacancy_identity:
source_text_or_snapshot:
expected:
  role_identity:
  mandate:
  company:
  feasibility_facts:
  requirements:
critical_assertions:
ambiguities:
annotation_source:
```

Separate:

- expected explicit facts;
- expected semantic facts;
- allowed unknowns;
- facts intentionally not asserted.

Do not overfit every sentence.

Golden annotations must focus on decision-critical semantics.

---

## 10. Required golden contrasts

The test suite must explicitly assert the following pairs.

### Wise

```text
APAC Growth & Expansion
→ region scope
→ growth/expansion
→ direct revenue or P&L-like ownership
→ broad mandate

Pricing / Acquiring
→ domain scope
→ monetization core
→ narrow but commercially central

Financial Crime / Onboarding / Data Product
→ feature/domain scope
→ no automatic broad-mandate inference
```

### Airwallex

```text
Global Payments Network Infrastructure
→ platform_as_business = true
→ platform_engineering must not be inferred solely from "infrastructure"
→ broad platform/business mandate

Payment Fraud
→ narrow domain
→ fraud/risk-heavy
→ not platform-as-business by company association alone
```

### Monzo

```text
Business Banking
→ business_line scope

Flex / Borrowing
→ narrower product/domain scope
```

### Coinbase

```text
Core / Developer Infrastructure
→ platform_engineering = true
→ platform_as_business = false or unknown
```

### Hybrid function

```text
GM Digital / GM Market / commercial-product hybrid
→ digital_business_ownership may be true
→ non_product_function alone must not imply pure support function
```

### Geography

```text
Remote US
→ remote
→ sponsorship unknown allowed as fact
→ no onsite sponsorship conclusion

US onsite without explicit sponsorship
→ sponsorship unknown or no
→ factual extraction only; no Step 2 rejection verdict

KZ local onsite
→ local_market = true
→ sponsorship may be unknown
→ no contradiction
```

---

## 11. Required tests

At minimum:

### Schema / contract

- JSON Schema is in sync with Pydantic.
- Unknown fields fail.
- Closed enums reject typos.
- Versions are semver.
- IDs are unique.
- Evidence references resolve.
- Explicit tri-state fields never collapse missing to false.
- `production_integration` remains false.
- no production evaluator imports the package.

### Semantic invariants

- title alone cannot set executive scope with high confidence;
- industry alone cannot set role or company desirability;
- country alone cannot set a preference verdict;
- "platform" alone cannot set platform-as-business;
- "infrastructure" alone cannot automatically set platform-engineering when commercial platform ownership is explicit;
- crypto company and crypto expertise barrier are separate facts;
- B2B and enterprise-sales context are separate;
- remote and onsite feasibility facts remain separate;
- sponsorship unknown is valid;
- KZ local + sponsorship unknown is valid;
- missing compensation has no effect because Step 2 does not evaluate compensation.

### Golden fixtures

All required contrasts above must pass.

---

## 12. Migration inventory

Produce an evidence-based map of current fields into the new model.

Inspect at least:

- source adapters;
- normalized vacancy object;
- vacancy storage schema;
- evaluator inputs;
- observability fields;
- recruiter read facade;
- company intelligence;
- application-material input packets;
- feedback taxonomy.

For each existing field document:

```text
legacy source
→ canonical vacancy-understanding field
→ migration status
→ data-loss risk
→ confidence risk
→ future consumer
```

Do not change the legacy fields in Step 2.

---

## 13. Versioning

Use:

- semantic `schema_version`;
- semantic `extractor_version`;
- fixture dataset version;
- source-content hash;
- extraction-policy version.

Breaking changes to field semantics require a major schema version.

Changes to extraction logic that may produce different values require an extractor-version bump even when the schema is unchanged.

Historical extracted records must remain attributable to their extractor version.

---

## 14. Observability contract

Define, but do not wire to production, the future metrics:

- extraction success rate;
- unknown rate by feature;
- low-confidence rate;
- deterministic vs semantic fact counts;
- evidence coverage;
- golden-case pass rate;
- extractor-version distribution;
- disagreement between legacy fields and canonical facts;
- company-enrichment missing rate;
- country-group unresolved rate;
- source-text incompleteness rate.

---

## 15. Prohibited architecture drift

Do not:

- score vacancies;
- produce apply / save / reject verdicts;
- add user-preference weights;
- import career preference rules into extraction;
- make industry, country, or title a desirability score;
- modify production evaluator;
- remove legacy rules;
- modify Slack delivery;
- modify cron;
- restart gateway;
- change live configuration;
- write live DB migrations;
- run live provider calls;
- push to remote;
- touch protected stash;
- auto-apply preference feedback;
- add ML.

If a current implementation makes a clean Step 2 model difficult, document the conflict instead of broadening scope.

---

## 16. Suggested implementation slices

### Slice 2A — Contract and semantics

Deliver:

- Pydantic model;
- JSON Schema;
- human contract;
- feature dictionary;
- schema tests.

No extractor beyond trivial model loading.

Commit separately.

### Slice 2B — Golden dataset

Deliver:

- real/sanitized fixtures;
- manual gold annotations;
- golden contrast tests;
- fixture provenance;
- fixture version.

Commit separately.

### Slice 2C — Deterministic extraction baseline

Deliver:

- pure deterministic extractors;
- normalized evidence;
- replay against fixtures;
- diagnostics;
- no provider calls.

Commit separately.

### Slice 2D — Migration and future semantic extraction plan

Deliver:

- migration map;
- provider extension-point design;
- confidence policy;
- cache/versioning plan;
- observability contract.

Commit separately.

Do not combine all slices into one unreviewable commit.

---

## 17. Definition of Done

Step 2 is complete only when:

1. A strict canonical vacancy-understanding Pydantic contract exists.
2. Generated JSON Schema is synchronized by test.
3. Human-readable semantic contract is complete.
4. Feature dictionary defines all decision-critical features.
5. Unknown and confidence semantics are explicit and tested.
6. Evidence exists per inferred gold field.
7. At least the 20 required fixtures or justified equivalent coverage exists.
8. Required Wise/Airwallex/Monzo/Coinbase/geography contrasts are green.
9. KZ local + sponsorship unknown is valid.
10. Platform-as-business and platform-engineering are reliably distinguishable in gold data.
11. Crypto employer and crypto expertise barrier are distinct.
12. Deterministic extraction baseline is pure and replayable.
13. No production component imports or consumes Step 2.
14. No score, recommendation, or user-specific desirability is produced.
15. Migration map is evidence-based.
16. All targeted tests pass.
17. `git diff --check` is clean.
18. Work is committed in bounded, intentional commits.
19. No push, gateway restart, live config change, Slack delivery, live DB write, or provider call occurred.
20. Final report includes unresolved ambiguities and Step 3 readiness.

---

## 18. Final report format

Return:

1. **Verdict**
2. **Preflight**
   - host
   - user
   - repo
   - branch
   - starting HEAD
   - Step 1 KZ regression status
3. **Commits**
   - hash
   - purpose
   - files
4. **Canonical model**
   - sections
   - enum decisions
   - unknown semantics
5. **Feature dictionary**
6. **Golden dataset**
   - real vs synthetic counts
   - source provenance
   - required contrasts
7. **Extraction baseline**
   - deterministic fields
   - intentionally deferred semantic fields
8. **Tests**
9. **Migration inventory**
10. **Production-isolation evidence**
11. **Unresolved questions**
12. **Step 3 readiness**
13. **Safety confirmation**
   - no push
   - no gateway restart
   - no live config change
   - no Slack send
   - no live DB write
   - no provider calls
   - stash untouched

---

## 19. Success criterion

A reviewer must be able to inspect one canonical record and understand:

- what the vacancy explicitly says;
- what the system inferred;
- why it inferred it;
- how confident it is;
- what remains unknown;
- which extractor version produced the fact;

without seeing any recommendation score and without knowing Denis's preferences.

That is the acceptance test for the Vacancy Understanding Layer.
