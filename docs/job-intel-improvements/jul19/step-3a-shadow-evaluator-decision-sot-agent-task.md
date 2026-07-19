# Step 3A Agent Task — Shadow Evaluator Decision SoT

## 0. Purpose of this task

Before implementing the Step 3 Shadow Preference Evaluator, create and validate its **Decision Source of Truth**.

This task is deliberately architecture-first.

Do **not** implement the evaluator yet.

The purpose is to prevent hidden decision logic from appearing in Python code, tests, numeric weights, or ad-hoc special cases. The future evaluator must be a faithful executable implementation of an approved decision contract—not the place where product policy is invented.

The output of this task will become the binding architectural basis for Step 3 implementation.

---

## 1. Why a separate SoT is required

Steps 1 and 2 established two independent sources of truth:

```text
Career Preference Model
    describes Denis's constraints, motivations, preferences and anti-preferences

Vacancy Understanding Model
    describes the vacancy, company facts, requirements and uncertainty
```

Step 3 introduces a qualitatively different responsibility:

```text
Shadow Evaluator
    compares the two SoTs and turns the comparison into a decision
```

This comparison cannot safely be left implicit because the evaluator must resolve questions such as:

- Which feasibility rule wins when several rules match?
- Does `uncertain` block a recommendation or only reduce confidence?
- Can exceptional mandate fit compensate for weak company fit?
- Can company fit ever compensate for weak mandate fit?
- What happens when a role is highly attractive but evidence is incomplete?
- How is the KZ fallback lane kept separate from the global core lane?
- What distinguishes `exceptional`, `strong`, `promising`, `unclear`, and `not_recommended`?
- How many unknowns are acceptable before escalation is prohibited?
- What is a blocker versus a concern?
- How are interaction rules applied and ordered?
- How should legacy disagreement be interpreted?

Without an explicit SoT, these choices will drift into:

- arbitrary `if` statements;
- undocumented numeric weights;
- golden tests that encode accidental behaviour;
- exception proliferation;
- recommendation labels with unstable meaning;
- explanations that do not match actual decisions.

The Decision SoT must make all such choices inspectable and reviewable before implementation.

---

## 2. Canonical environment

Work only on the canonical host:

```bash
ssh hermes-agent
cd /home/hermes/.hermes/hermes-agent
```

Repository:

```text
/home/hermes/.hermes/hermes-agent
```

Branch:

```text
local/customizations
```

Do not push.

Do not restart the gateway.

Do not modify live configuration.

Do not write to the live DB.

Do not send Slack or Telegram messages.

Do not invoke providers.

Do not touch the protected stash.

---

## 3. Preconditions

Before starting, verify and report:

1. Step 1 Career Preference Model is present and green.
2. The Step 1 KZ sponsorship correction is present.
3. Step 2 Vacancy Understanding Model is present and green.
4. The Step 2 follow-up corrections are present:
   - deterministic/replayable extraction metadata;
   - geographically complete Africa resolution or approved equivalent;
   - correct relocation evidence provenance.
5. Existing Step 1 and Step 2 tests pass.
6. No production integration currently consumes either new SoT.

If any precondition is not met, stop and report the exact gap. Do not silently compensate inside the Decision SoT.

---

## 4. Binding source materials

Read and treat as authoritative:

### Process and architecture

```text
docs/job-intel-improvements/jul19/job-intel-career-preference-system-development-sot.md
```

### Step 1

```text
docs/job-intel-improvements/jul19/career-preference-model.md
job_intel/preference_model/career-preference-model.yaml
job_intel/preference_model/model.py
docs/job-intel-improvements/jul19/career-preference-model-migration-map.md
```

### Step 2

```text
docs/job-intel-improvements/jul19/vacancy-understanding-model.md
docs/job-intel-improvements/jul19/vacancy-understanding-extraction-plan.md
docs/job-intel-improvements/jul19/vacancy-understanding-migration-map.md
job_intel/vacancy_understanding/model.py
job_intel/vacancy_understanding/feature-definitions.yaml
tests/fixtures/vacancy_understanding/
```

### Historical evidence

```text
docs/audit/2026-07-19-career-preference-model.md
docs/audit/2026-07-19-recommendation-system-audit.md
```

Inspect existing evaluator, scoring, feedback, observability and delivery code only to identify migration constraints and historical behaviour. Legacy logic is not authoritative for the new decision semantics.

---

## 5. Required primary artifact

Create:

```text
docs/job-intel-improvements/jul19/shadow-evaluator-decision-sot.md
```

This must be the canonical, human-readable Decision SoT for Step 3.

It must be complete enough that a separate coding agent could implement the evaluator without inventing decision policy.

Also create machine-readable companion artifacts where appropriate:

```text
job_intel/shadow_evaluator/decision-contract.yaml
job_intel/shadow_evaluator/decision-contract.schema.json
```

A Pydantic schema may be proposed or created only for validating the Decision SoT structure. Do not implement runtime evaluation.

---

## 6. Required decision architecture

The SoT must define four distinct outputs:

```text
feasibility
mandate_fit
company_fit
overall_recommendation
```

These must remain separate throughout the decision process.

### 6.1 Feasibility

Answers:

```text
Can Denis realistically accept this opportunity?
```

Required verdicts:

```text
feasible
uncertain
infeasible
```

Required lane:

```text
core
fallback_local
```

Feasibility must not be represented as a preference score.

### 6.2 Mandate fit

Answers:

```text
How well does the actual role mandate match Denis's desired work?
```

Required fit bands:

```text
exceptional
strong
moderate
weak
mismatch
unknown
```

Use qualitative bands, not arbitrary numeric weights.

### 6.3 Company fit

Answers:

```text
How well do the observable company characteristics support Denis's career objective?
```

Required fit bands:

```text
exceptional
strong
moderate
weak
mismatch
unknown
```

Company fit must never overwrite mandate fit.

### 6.4 Overall recommendation

Required labels:

```text
exceptional
strong
promising
unclear
not_recommended
```

The overall recommendation must be derived only from the prior outputs and explicit decision rules.

It must never be calculated independently.

---

## 7. Formal decision graph

The SoT must include a complete decision graph.

At minimum, formalize the following order:

```text
1. Validate both input models and supported schema major versions.
2. Determine evaluation lane.
3. Evaluate feasibility constraints.
4. Apply feasibility interaction rules and precedence.
5. If infeasible, derive terminal recommendation.
6. Evaluate mandate preferences and role anti-preferences.
7. Apply mandate interaction rules.
8. Evaluate company preferences and company anti-preferences.
9. Apply company interaction rules.
10. Calculate section confidence and unresolved unknowns.
11. Derive overall recommendation through the approved matrix.
12. Generate evidence-backed explanation and clarification questions.
```

For each node state:

- input;
- output;
- terminal/non-terminal behaviour;
- possible transitions;
- evidence requirement;
- confidence effect;
- failure behaviour.

Include a visual or Mermaid diagram in the SoT.

---

## 8. Precedence rules

Create an explicit precedence table.

At minimum address:

### 8.1 Feasibility precedence

Proposed baseline to validate:

```text
infeasible > uncertain > feasible
```

But lane routing must remain independent.

For example:

```text
KZ local:
  lane = fallback_local
  feasibility may still be feasible

KZ fallback must not be overridden by unknown sponsorship alone.
```

Define what happens when:

- several feasibility rules match;
- one is feasible and another uncertain;
- one is uncertain and another infeasible;
- an override is allowed;
- an interaction rule suppresses or limits another rule;
- evidence confidence differs.

### 8.2 Rule precedence

Define deterministic ordering among:

- feasibility constraints;
- interaction rules;
- hard role mismatch;
- company-level anti-preferences;
- role-level anti-preferences;
- exploration rules;
- fallback routing.

Clarify whether lower numeric priority means earlier application and whether later rules may reverse earlier results.

No rule order may remain implicit in YAML list position.

### 8.3 Evidence precedence

Define conflict handling among:

```text
explicit vacancy statement
structured source field
deterministic derivation
semantic inference
company enrichment
manual gold annotation
title-only evidence
```

Proposed baseline:

```text
explicit reliable evidence
> structured source evidence
> deterministic derivation
> semantic inference
> title-only inference
```

Manual gold annotation is test truth, not runtime production evidence.

Document the final approved hierarchy.

---

## 9. Unknown truth table

Create a complete unknown-semantics matrix.

For every major field family define whether unknown:

- has no effect;
- reduces section confidence;
- adds a clarification;
- caps recommendation;
- routes to `unclear`;
- prevents `exceptional`;
- prevents `strong`;
- may become terminal only in combination with another unknown.

At minimum cover:

### Feasibility unknowns

- work format unknown;
- country unknown;
- sponsorship unknown;
- relocation support unknown;
- right-to-work unknown;
- timezone expectations unknown;
- language requirement unclear.

### Mandate unknowns

- scope breadth unknown;
- revenue proximity unknown;
- P&L unknown;
- digital-business ownership unknown;
- organization scope unknown;
- platform shape unknown.

### Company unknowns

- company scale unknown;
- stage unknown;
- brand recognition unknown;
- company culture unknown;
- crypto/outsourcing status unknown.

Required principles:

1. Unknown is never false.
2. Unknown alone is not a negative signal.
3. Unknown can reduce confidence.
4. Critical unknowns can cap recommendation.
5. The cap must be explicit and field-specific.
6. The evaluator must state what information would resolve the uncertainty.

---

## 10. Blocker, concern and positive-support semantics

Define three distinct result types:

```text
blocker
concern
support
```

### Blocker

A fact or matched rule that can make a role infeasible or mandate-mismatch.

Examples:

- USA onsite without explicit sponsorship;
- non-transferable mandatory domain/language barrier;
- scope clearly below executive threshold;
- pure sales/support function with no digital-business ownership.

### Concern

A negative factor that affects fit or confidence but is not independently terminal.

Examples:

- crypto exchange employer;
- risk/compliance-heavy domain;
- weak company evidence;
- timezone burden;
- narrow scope before interaction exceptions.

### Support

A positive match to a preference.

Examples:

- regional ownership;
- P&L;
- monetization core;
- platform-as-business;
- global multi-region company;
- explicit sponsorship.

For each type define:

- whether it can be suppressed;
- whether it can be overridden;
- whether it affects confidence;
- whether several concerns may jointly become a mismatch;
- whether support can compensate for concerns.

Do not rely on hidden numeric accumulation.

---

## 11. Fit-band semantics

Define exact semantic meaning for every mandate/company fit band.

Example structure:

### Exceptional

- rare, unusually strong alignment;
- broad ownership at or above preferred scope;
- multiple high-confidence critical/strong preferences matched;
- no unsuppressed strong anti-preference;
- sufficient evidence coverage.

### Strong

- clearly above threshold;
- several strong matches;
- no terminal mismatch;
- manageable concerns.

### Moderate

- meaningful alignment but either narrower scope, weaker evidence, or notable concerns.

### Weak

- some transferable relevance but does not solve the core career objective.

### Mismatch

- actual role/company conflicts with critical desired semantics.

### Unknown

- evidence insufficient to classify honestly.

Do not merely copy these examples. Validate them against the Step 1 model and historical cases.

---

## 12. Recommendation derivation matrix

Create a complete matrix mapping:

```text
feasibility × mandate_fit × company_fit × confidence/lane
→ overall recommendation
```

The matrix must explicitly cover all meaningful combinations.

At minimum resolve:

| Feasibility | Mandate | Company | Question |
|---|---|---|---|
| infeasible | exceptional | exceptional | must still be not_recommended |
| uncertain | exceptional | strong | strong, promising or unclear? |
| feasible | exceptional | weak | can mandate compensate? |
| feasible | strong | mismatch | terminal or promising? |
| feasible | moderate | exceptional | company must not rescue weak mandate |
| feasible | weak | exceptional | likely not_recommended |
| feasible | unknown | strong | unclear |
| feasible | strong | unknown | strong or promising depending evidence cap |
| feasible fallback_local | strong | strong | separate fallback recommendation semantics |
| feasible core | mismatch | exceptional | not_recommended |

Required architectural principle:

> Mandate fit is primary. Company fit can strengthen or weaken a viable mandate, but cannot turn a mandate mismatch into a positive recommendation.

Validate or refine this principle explicitly.

Also define whether feasibility `uncertain`:

- caps recommendation at `promising`;
- routes to `unclear`;
- allows `strong` when uncertainty is only clarification-grade.

This must not be left to code.

---

## 13. Interaction-rule execution semantics

The Career Preference Model contains interaction effects such as:

```text
suppress
limit_to_company_fit
gate
route_to_fallback
exclude_from
allow
```

For each effect define executable semantics:

### suppress

- which matched result is removed;
- whether it remains visible in audit trail;
- whether its evidence remains visible;
- whether suppression affects confidence.

### limit_to_company_fit

- prevents a company concern from contaminating mandate fit;
- specify exact output transformation.

### gate

- specify whether it blocks rule evaluation or only limits applicability.

### route_to_fallback

- specify lane transition and whether evaluation continues.

### exclude_from

- specify how a fact is barred from contributing to a named preference.

### allow

- specify whether it suppresses a blocker or prevents it from matching.

Define idempotency and conflict handling.

Every interaction execution must remain visible in the evaluation trace.

---

## 14. Confidence model

Define section confidence separately from fit.

Required confidence labels:

```text
high
medium
low
unknown
```

Do not use confidence as a hidden score.

The SoT must explain:

- how fact confidence propagates;
- how evidence coverage affects section confidence;
- how conflicting evidence affects confidence;
- how source-text incompleteness affects confidence;
- how title-only facts cap confidence;
- whether one critical low-confidence fact caps a whole section;
- how overall recommendation confidence is derived.

Propose an explainable qualitative algorithm or decision table.

Avoid arbitrary averaging.

---

## 15. Clarification contract

Define when the evaluator emits clarification questions.

Each clarification must contain:

```yaml
question:
reason:
affected_section:
affected_recommendation:
required_fact:
priority:
```

Priorities:

```text
blocking
recommendation_changing
confidence_improving
optional
```

Examples:

- Does the company sponsor relocation for this non-US onsite role?
- Does the role own a P&L or only influence revenue?
- How many product domains and teams are in scope?
- Is “platform infrastructure” a customer-facing product or internal engineering platform?

Clarifications must be fact-seeking, not preference-seeking unless the preference axis is explicitly marked exploration in Step 1.

---

## 16. Explanation contract

Define the canonical explanation object for future reuse in:

- offline replay;
- Slack cards;
- CRM;
- recruiter materials;
- CV tailoring;
- analytics.

Each explanation item must contain:

```yaml
section:
kind: support | concern | blocker | unknown | interaction
preference_rule_id:
vacancy_fact_path:
statement:
evidence_refs:
confidence:
impact:
```

The top-level explanation must provide:

- one-sentence verdict summary;
- why this role may be attractive;
- why it may not work;
- what remains unknown;
- which interaction rules changed the raw result;
- which lane was used.

No explanation may cite a numeric score.

No generated statement may claim more than the evidence supports.

---

## 17. KZ fallback lane contract

Fully specify KZ fallback behaviour.

Required constraints:

1. `fallback_local` is physically and analytically separate from `core`.
2. A fallback evaluation must never enter global core recommendation metrics.
3. Fallback recommendations must be marked as such.
4. Unknown sponsorship does not make a KZ local role uncertain.
5. A small local company concern may be suppressed or reinterpreted inside fallback according to the Step 1 interaction rule.
6. Fallback activation remains manual/standby until explicitly changed.
7. Shadow replay may evaluate fallback cases, but production delivery remains disabled.
8. Feedback from fallback must not silently recalibrate core preferences.

Define whether fallback uses the same recommendation labels with a lane marker or a distinct recommendation vocabulary. Provide rationale.

---

## 18. Exploration contract

Define how Step 1 exploration axes affect Step 3.

Exploration is not ordinary recommendation.

Specify:

- eligibility;
- feasibility requirements;
- one-axis-at-a-time invariant;
- recommendation label or marker;
- confidence treatment;
- separation from ordinary precision metrics;
- feedback value;
- maximum rate;
- interaction with company/mandate fit.

An exploration card may be useful despite moderate fit, but must never bypass a hard feasibility blocker.

---

## 19. Historical replay protocol

Design the Step 3 offline replay protocol.

Do not run production integration.

Define:

### Input cohort

- clean historical vacancies;
- deduplicated;
- smoke/test users excluded;
- resend duplicates excluded;
- known data-quality cases identified;
- source-text completeness recorded;
- feedback labels mapped but not treated as absolute truth.

### Outputs

For every case:

```text
legacy result
shadow result
user feedback
difference classification
evidence completeness
```

### Disagreement taxonomy

At minimum:

```text
expected_architecture_change
legacy_false_positive
legacy_false_negative
shadow_possible_false_positive
shadow_possible_false_negative
insufficient_vacancy_evidence
preference_model_gap
vacancy_understanding_gap
decision_contract_gap
feedback_ambiguity
```

### Evaluation metrics

Do not optimize one aggregate accuracy score.

Report:

- positive precision by recommendation band;
- recall of applied/exceptional/interesting cases;
- negative precision;
- unknown/unclear rate;
- infeasible precision;
- lane-specific performance;
- source-specific performance;
- explanation coverage;
- top disagreement causes;
- critical false-negative list;
- critical false-positive list.

Historical user feedback is evidence, not infallible ground truth.

---

## 20. Golden decision cases

Create a decision-case specification, not implementation tests yet.

Use at least these cases:

### Expected high positive

- Wise — APAC Growth & Expansion
- Airwallex — Global Payments Network Infrastructure
- Monzo — Business Banking
- Brex — Growth/AI
- Affirm — Remote US leadership

### Valuable narrower exceptions

- Wise — Pricing
- Wise — Acquiring

### Negative / weak mandate contrasts

- Wise — Financial Crime
- Wise — Onboarding
- Airwallex — Payment Fraud
- Coinbase — Core/Developer Infrastructure
- internal tools role
- sales-only role
- FP&A role

### Feasibility contrasts

- US onsite without sponsorship
- US onsite with sponsorship
- remote US
- non-US onsite with unknown sponsorship
- KZ local with unknown sponsorship
- sanctioned location
- Africa location
- explicit non-transferable language/domain barrier

### Company-fit contrasts

- broad strong mandate at crypto exchange
- narrow role at tier-1 company
- strong role at small local company
- strong role with company facts unknown

For each case specify:

```yaml
expected_lane:
expected_feasibility:
expected_mandate_fit:
expected_company_fit:
expected_recommendation:
expected_confidence:
required_supports:
required_concerns:
required_blockers:
required_unknowns:
expected_interactions:
rationale:
```

Do not write code before these expected outcomes are reviewable.

---

## 21. Legacy comparison boundaries

The SoT must state explicitly:

- legacy numeric score is not migrated;
- old thresholds are not authoritative;
- old recommendation labels are comparison evidence only;
- fintech/telecom weight is deprecated;
- title bonuses are deprecated;
- duplicate geo penalties are deprecated;
- shadow/legacy disagreement is expected where architecture intentionally changed;
- the purpose of replay is validation and discovery, not forced agreement.

---

## 22. Machine-readable decision contract

Create a YAML representation of the approved decision semantics.

Suggested top-level structure:

```yaml
shadow_evaluator_decision_contract:
  metadata:
  supported_input_versions:
  evaluation_order:
  precedence:
  feasibility:
  mandate_fit:
  company_fit:
  interaction_effects:
  unknown_policy:
  confidence_policy:
  recommendation_matrix:
  clarification_policy:
  explanation_contract:
  fallback_policy:
  exploration_policy:
  replay_protocol:
  change_policy:
```

Requirements:

- strict schema;
- unique ids;
- no numeric preference weights;
- no production-integration flag enabled;
- semantic versioning;
- explicit owner approval for changes;
- no silent learning;
- unknown fields rejected.

The YAML is policy, not executable code.

---

## 23. Required validation tests for the SoT

Tests may validate the contract, but must not implement evaluator behaviour.

At minimum:

- YAML validates against schema.
- JSON Schema remains in sync if generated.
- recommendation matrix covers every supported combination.
- no unsupported verdict or label.
- every interaction effect has defined semantics.
- every rule target resolves.
- every golden decision case has a complete expected result.
- no numeric scoring weights.
- no production imports.
- no production integration.
- fallback and core are distinct.
- unknown never maps directly to false.
- mandate mismatch cannot become positive solely through company fit.
- infeasible always maps to `not_recommended`.
- evidence/explanation contract requires source references.
- no silent learning.

---

## 24. Required secondary artifact

Create:

```text
docs/job-intel-improvements/jul19/shadow-evaluator-sot-review-report.md
```

Include:

1. source materials reviewed;
2. decisions inherited without change;
3. decisions newly formalized;
4. ambiguities found;
5. alternative policies considered;
6. recommended policy with rationale;
7. unresolved owner decisions;
8. implementation readiness assessment;
9. risks of premature implementation.

Do not hide unresolved policy questions by selecting arbitrary defaults.

---

## 25. Questions that must be resolved or explicitly surfaced

The SoT must answer or escalate:

1. Can an `uncertain` feasibility result ever produce `strong`?
2. Is one unresolved sponsorship question enough to force `unclear`?
3. Can exceptional mandate + weak company produce `strong`, or only `promising`?
4. Does company mismatch always make the result `not_recommended`?
5. Can multiple soft concerns combine into mandate mismatch without numeric weights?
6. What evidence coverage is required for `exceptional`?
7. Can a title-only vacancy ever receive more than `promising`?
8. How should Wise Pricing/Acquiring be classified: `strong` or `promising`?
9. What recommendation should a strong KZ fallback role receive while fallback is standby?
10. Does a crypto-exchange employer cap recommendation?
11. How should strong role fit with unknown company facts be handled?
12. When should `unknown` become `unclear` versus merely lowering confidence?
13. Are big-tech and early-startup exploration cases eligible before those preference questions are answered?

Where the existing SoTs clearly determine the answer, document it.

Where they do not, present alternatives and a recommendation for owner approval.

---

## 26. Prohibited work

Do not:

- implement runtime evaluator code;
- add numeric weights;
- add production ranking;
- alter current evaluator;
- modify scoring.yaml;
- change thresholds;
- change Slack card selection;
- change feedback processing;
- modify CRM;
- modify recruiter materials;
- write live DB data;
- add provider calls;
- add ML;
- change cron;
- restart gateway;
- alter live configuration;
- push;
- touch protected stash.

This task ends at an approved and validated Decision SoT.

---

## 27. Suggested bounded execution slices

### SoT Slice A — Evidence and ambiguity inventory

Deliver:

- reviewed source map;
- policy ambiguity list;
- legacy conflict list;
- proposed decision dimensions.

Commit separately.

### SoT Slice B — Human decision contract

Deliver:

- complete `shadow-evaluator-decision-sot.md`;
- decision graph;
- precedence;
- fit semantics;
- unknown matrix;
- recommendation matrix;
- fallback/exploration rules.

Commit separately.

### SoT Slice C — Machine-readable contract

Deliver:

- decision-contract YAML;
- strict schema;
- validation tests;
- golden decision specification.

Commit separately.

### SoT Slice D — Review and readiness report

Deliver:

- review report;
- unresolved owner decisions;
- implementation mapping for future Step 3;
- no implementation code.

Commit separately.

Do not compress the task into one unreviewable commit.

---

## 28. Definition of Done

This Decision SoT task is complete only when:

1. The four-output architecture is formalized.
2. A complete decision graph exists.
3. Rule and evidence precedence are explicit.
4. Unknown semantics are covered by a field-specific truth table.
5. Blocker/concern/support semantics are explicit.
6. Mandate and company fit bands have precise meanings.
7. The recommendation matrix covers all supported combinations.
8. Interaction effects have executable semantics.
9. Confidence is separated from fit.
10. Clarification generation is specified.
11. Explanation format is canonical and evidence-backed.
12. KZ fallback is physically and semantically separate.
13. Exploration behaviour is specified.
14. Historical replay protocol and disagreement taxonomy exist.
15. Golden decision cases have reviewable expected outcomes.
16. Legacy numeric scoring is explicitly non-authoritative.
17. Machine-readable contract validates strictly.
18. No runtime evaluator was implemented.
19. No production integration or operational change occurred.
20. Remaining product questions are explicitly surfaced for owner approval.
21. The final review states whether implementation can proceed without policy invention.

---

## 29. Final report format

Return:

1. **Verdict**
2. **Preflight**
3. **Commits**
4. **Sources reviewed**
5. **Decision architecture**
6. **Decision graph**
7. **Precedence**
8. **Unknown policy**
9. **Fit-band semantics**
10. **Recommendation matrix**
11. **Interaction semantics**
12. **Confidence policy**
13. **Clarification and explanation contracts**
14. **Fallback and exploration**
15. **Golden decision cases**
16. **Replay protocol**
17. **Legacy conflicts**
18. **Unresolved owner decisions**
19. **Implementation readiness**
20. **Safety confirmation**

Safety confirmation must explicitly state:

- no runtime evaluator implementation;
- no production imports;
- no push;
- no gateway restart;
- no live config changes;
- no Slack/Telegram sends;
- no DB writes;
- no provider calls;
- stash untouched.

---

## 30. Success criterion

The task succeeds when a future coding agent can implement the Shadow Evaluator by translating an approved decision contract into code, without inventing:

- fit semantics;
- precedence;
- recommendation thresholds;
- unknown handling;
- interaction behaviour;
- exception logic;
- fallback behaviour;
- replay interpretation.

If implementation still requires product judgement, the SoT is incomplete.
