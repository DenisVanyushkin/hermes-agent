# Step 3 Agent Task — Implement the Shadow Preference Evaluator

## 0. Objective

Implement the **Shadow Preference Evaluator** as a faithful executable translation of the approved Step 3A Decision Source of Truth.

The evaluator must compare:

```text
Career Preference Model (Step 1)
        +
Vacancy Understanding Model (Step 2)
        ↓
Shadow Preference Evaluator (Step 3)
```

The evaluator must:

- consume only the canonical Step 1 and Step 2 models;
- implement the approved decision graph, precedence, unknown policy, interaction semantics, confidence policy, recommendation matrix, caps, clarification contract, and explanation contract;
- run only in shadow/offline mode;
- produce deterministic, evidence-backed results;
- support historical replay and disagreement analysis;
- make no production decision-path changes.

The implementation agent must not invent product policy. Any gap between the SoT and executable behaviour is a blocking defect in the contract or implementation and must be surfaced, not silently resolved.

---

# 1. Canonical environment

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

Do not send Slack or Telegram messages.

Do not write to the live production DB.

Do not invoke providers.

Do not touch the protected stash.

---

# 2. Mandatory precondition — close the Step 3A owner decisions

Before runtime implementation, create one bounded SoT amendment commit implementing the approved owner decisions.

## 2.1 Approved owner decisions

### O1 — recommendation vs action vocabulary

Canonical evaluator recommendation vocabulary:

```text
exceptional
strong
promising
unclear
not_recommended
```

Separate operational action vocabulary:

```text
apply
investigate
save
reject
```

Approved mapping:

```text
exceptional     -> apply
strong          -> apply
promising       -> investigate when confidence >= medium
promising       -> save when confidence = low or feasibility is uncertain
unclear         -> investigate with required clarification
not_recommended -> reject
```

`exploration` remains a marker, not a recommendation or action label.

Update the process SoT language so the old values are described as action outcomes, not the evaluator recommendation vocabulary.

### O2 — recommendation matrix and caps

Approve the Step 3A matrix and existing caps, subject to O6 below.

Keep these invariants:

- infeasible is always terminal;
- mandate weak/mismatch cannot be rescued by company fit;
- company mismatch means not_recommended;
- uncertain feasibility cannot yield strong or exceptional;
- incomplete source text caps at promising;
- strong mandate + unknown company = promising;
- exceptional mandate + weak company = promising;
- company fit may strengthen or weaken a viable mandate but may never overwrite it.

### O3 — Wise full-text prerequisite

Record:

- runtime evaluator implementation may proceed;
- replay acceptance and calibration may not be concluded until full texts are re-fetched for title-only Wise flagship cases;
- policy-only/golden controls may validate expected full-text behaviour;
- title-only historical cases must remain honestly capped.

### O4 — crypto employer cap

Approve for shadow only:

```text
crypto employer concern
-> company_fit <= weak
-> overall <= promising
```

Mark explicitly:

```yaml
status: provisional_shadow_policy
production_rollout_requires_review: true
```

Crypto is not a role veto and may remain exploration-eligible when the mandate is transferable and strong.

### O5 — big tech / early startup

Keep both:

```text
neutral
not an anti-preference
not a positive support
not exploration-eligible
```

until the owner answers direct preference questions.

### O6 — remove generic concern counting

Delete:

```text
>=3 concerns -> lower band by one
```

from:

- human SoT;
- machine contract;
- schema assumptions;
- result aggregation semantics;
- tests;
- golden policy expectations, if any.

Generic concern counting is prohibited.

Replace it with:

1. qualitative band criteria;
2. named, evidence-backed compound interaction rules only;
3. explicit owner-approved rules for any future multi-factor downgrade.

No generic threshold based on the number of concerns may remain.

## 2.2 Required amendment outputs

Update:

```text
docs/job-intel-improvements/jul19/job-intel-career-preference-system-development-sot.md
docs/job-intel-improvements/jul19/shadow-evaluator-decision-sot.md
job_intel/shadow_evaluator/decision-contract.yaml
docs/job-intel-improvements/jul19/shadow-evaluator-sot-review-report.md
```

Update schema/tests as required.

Bump:

```text
decision_contract_version: 1.1.0
```

Required commit:

```text
docs(shadow-evaluator): record owner decisions and remove concern counting
```

Runtime implementation must not start until this commit is green.

---

# 3. Binding implementation sources

After the amendment, treat these as binding:

## Step 1

```text
docs/job-intel-improvements/jul19/career-preference-model.md
job_intel/preference_model/career-preference-model.yaml
job_intel/preference_model/model.py
```

## Step 2

```text
docs/job-intel-improvements/jul19/vacancy-understanding-model.md
docs/job-intel-improvements/jul19/vacancy-understanding-extraction-plan.md
job_intel/vacancy_understanding/model.py
job_intel/vacancy_understanding/feature-definitions.yaml
tests/fixtures/vacancy_understanding/
```

## Step 3A

```text
docs/job-intel-improvements/jul19/shadow-evaluator-decision-sot.md
job_intel/shadow_evaluator/decision-contract.yaml
job_intel/shadow_evaluator/decision-contract.schema.json
tests/fixtures/shadow_evaluator/golden-decision-cases.yaml
```

The human and machine Decision SoTs must agree.

If they disagree, stop and report the mismatch.

---

# 4. Deliverables

Implement:

```text
job_intel/shadow_evaluator/
```

Suggested modules:

```text
__init__.py
models.py
engine.py
matcher.py
interactions.py
confidence.py
matrix.py
explanations.py
clarifications.py
replay.py
reporting.py
```

Exact file names may differ, but responsibilities must remain separated.

Create:

```text
docs/job-intel-improvements/jul19/shadow-evaluator-implementation.md
docs/job-intel-improvements/jul19/shadow-evaluator-replay-report.md
docs/job-intel-improvements/jul19/shadow-evaluator-disagreement-analysis.md
```

Add tests:

```text
tests/job_intel/test_shadow_evaluator_*.py
```

Add offline CLI entry point only if useful, for example:

```bash
python -m job_intel shadow-evaluate-fixture ...
python -m job_intel shadow-replay ...
```

The CLI must be read-only and disabled from normal production flows.

---

# 5. Canonical evaluator output model

Create a strict Pydantic model.

Required top-level structure:

```yaml
metadata:
feasibility:
mandate_fit:
company_fit:
overall:
explanations:
clarifications:
interaction_trace:
unknown_ledger:
diagnostics:
```

## 5.1 Metadata

At minimum:

```yaml
decision_contract_version:
preference_model_version:
vacancy_understanding_schema_version:
evaluator_version:
evaluated_at:
vacancy_key:
input_content_hash:
shadow_only: true
production_integration: false
```

Operational timestamp must not make semantic replay comparison nondeterministic. Separate semantic equality from run metadata.

## 5.2 Feasibility result

```yaml
verdict: feasible | uncertain | infeasible
lane: core | fallback_local
matched_constraints:
blockers:
unknowns:
confidence:
fallback_state:
```

## 5.3 Mandate and company results

```yaml
band: exceptional | strong | moderate | weak | mismatch | unknown
supports:
concerns:
blockers:
unknowns:
confidence:
```

## 5.4 Overall result

```yaml
recommendation: exceptional | strong | promising | unclear | not_recommended
action: apply | investigate | save | reject
confidence:
lane:
applied_caps:
exploration:
```

`action` must be derived from recommendation, confidence, and feasibility using the approved mapping.

It must not be separately scored.

## 5.5 Evidence-backed result item

Each support, concern, blocker, unknown, and interaction item must include:

```yaml
id:
section:
kind:
preference_rule_id:
vacancy_fact_path:
statement:
evidence_refs:
confidence:
impact:
active:
suppressed_by:
moved_to:
```

No active decision item without evidence references, except purely structural interaction trace entries that explicitly point to their input items.

---

# 6. Required runtime stages

Implement the exact decision graph.

## Stage 1 — validate inputs

Validate:

- supported Step 1 major;
- supported Step 2 major;
- decision contract version;
- canonical model invariants;
- required vacancy key and evidence registry.

Unsupported major:

```text
error record
no verdict
```

No fallback to legacy evaluation.

## Stage 2 — lane routing

Required:

```text
country_group=kazakhstan
AND local_market_indicator=true
-> fallback_local
```

Otherwise:

```text
core
```

Unknown country:

```text
core + clarification + uncertain-grade unknown
```

## Stage 3 — feasibility constraint matching

Evaluate canonical Step 1 constraints only against canonical Step 2 facts.

Unknown never matches `false`.

No inference of new vacancy facts is allowed.

## Stage 4 — feasibility interactions and merge

Apply interaction rules by ascending priority.

Merge:

```text
infeasible > uncertain > feasible
```

Lane remains independent.

All matched, prevented, overridden, suppressed, and skipped-conflict rules remain visible in trace.

## Stage 5 — terminal infeasible

If infeasible:

```text
overall.recommendation = not_recommended
overall.action = reject
```

Still produce:

- blockers;
- explanation;
- confidence;
- clarifications where useful.

Do not run the recommendation matrix as if the role were feasible.

Mandate/company may be omitted or optionally calculated for replay diagnostics only, but the public result must clearly mark them as non-decisioning after terminal infeasibility. Follow the Decision SoT exactly.

## Stage 6 — mandate matching

Evaluate:

- mandate preferences;
- role anti-preferences;
- narrow-scope exceptions;
- monetization exceptions;
- platform-as-business vs platform-engineering;
- digital-business ownership;
- internal tools;
- sales/finance/support functions;
- transformation and P&L signals.

No company fact may directly determine mandate band unless an approved interaction explicitly moves or excludes it.

## Stage 7 — mandate interactions

Implement exactly:

```text
suppress
exclude_from
allow
gate
```

and any approved Step 1 rule targeting mandate.

## Stage 8 — company matching

Evaluate only canonical company facts and company-level preferences/anti-preferences.

Do not use employer brand familiarity without evidence.

## Stage 9 — company interactions

Implement:

```text
limit_to_company_fit
route_to_fallback
```

and any approved company-targeted interaction.

Crypto employer must remain company-level and must not contaminate mandate fit.

## Stage 10 — confidence and unknown ledger

Implement the qualitative confidence policy.

No averaging.

Required:

```text
section confidence =
least-confident critical fact
```

with documented exceptions from the SoT.

Title-only critical evidence caps confidence.

Conflicts produce low confidence and clarification.

## Stage 11 — matrix and caps

Use the full machine-readable matrix.

Do not re-encode the matrix in unrelated conditional logic.

Recommended approach:

- parse validated contract into immutable runtime policy;
- use one central matrix resolver;
- use one central cap resolver.

Caps are monotonic and may only lower.

Prevent double punishment.

## Stage 12 — explanations and clarifications

Generate canonical explanation objects from actual matched facts and rules.

Every applied cap must appear in explanations.

Every blocking or recommendation-changing unknown must produce a clarification.

No numeric score language.

---

# 7. Interaction semantics

Implement the approved semantics exactly.

## suppress

- deactivate the target item;
- preserve it in trace;
- set `suppressed_by`;
- do not change fact confidence.

## limit_to_company_fit

- remove active impact from mandate;
- create/move active concern into company;
- preserve source item and evidence;
- set `moved_to=company_fit`.

## gate

- documentary no-op;
- records applicability boundary;
- does not reorder or block unrelated rules.

## route_to_fallback

- set lane;
- continue evaluation;
- enable approved fallback-specific suppression;
- preserve fallback state.

## exclude_from

- prevent a fact from supporting a named preference;
- retain the fact for other consumers.

## allow

- prevent a target blocker from matching;
- do not first match and then suppress it;
- trace `prevented`.

Interaction execution must be:

- deterministic;
- idempotent;
- priority-ordered;
- visible in trace.

Later rules may not reverse earlier effects.

---

# 8. Unknown handling

Implement the entire unknown policy table from the Decision SoT.

At minimum include tests for:

- unknown work format outside KZ;
- unknown work format inside KZ fallback;
- unknown sponsorship in US onsite;
- unknown sponsorship in non-US onsite;
- unknown sponsorship in remote;
- unknown country;
- unknown scope;
- unknown revenue proximity;
- unknown P&L;
- unknown digital ownership in non-product function;
- unknown company facts;
- source text incomplete.

Required invariants:

- unknown is never false;
- unknown is never a negative support/concern by itself;
- critical unknowns may reduce confidence;
- field-specific caps are explicit;
- title-only cases cannot bypass incomplete-text cap;
- KZ local + sponsorship unknown remains feasible.

---

# 9. Fit-band implementation

Implement band assignment from explicit qualitative criteria.

Do not implement generic numeric scoring.

Do not count concerns.

Do not introduce hidden thresholds.

Use:

- named preference matches;
- named anti-preference matches;
- approved interaction outcomes;
- critical-fact evidence coverage;
- named compound rules, if already present in the SoT.

If the SoT does not uniquely determine a band for a case, stop and report `decision_contract_gap`.

Do not guess.

---

# 10. Recommendation and action derivation

Recommendation:

```text
feasibility + mandate band + company band + caps
```

Action:

```text
recommendation + confidence + feasibility
```

Approved mapping:

```text
exceptional -> apply
strong -> apply
promising + confidence>=medium + feasible -> investigate
promising + low confidence -> save
promising + uncertain feasibility -> save
unclear -> investigate
not_recommended -> reject
```

For `unclear`, clarification must be non-empty.

For `save`, explanation must identify the blocking uncertainty or missing evidence.

Fallback uses the same labels and actions but always carries:

```text
lane=fallback_local
fallback_state=standby
```

No production delivery.

---

# 11. Exploration

Implement only the active Step 1 exploration axes.

Required:

- one axis at a time;
- hard feasibility gates remain active;
- mandate at least moderate;
- separate marker;
- excluded from ordinary precision metrics;
- no automatic big-tech or early-startup exploration.

Crypto may be exploration-eligible only if:

- feasibility is not infeasible;
- mandate is at least moderate;
- crypto is the single exploration axis;
- no non-transferable barrier exists.

Exploration never upgrades recommendation.

---

# 12. Golden test suite

Translate every golden decision case into executable regression tests.

At minimum all 24 current cases must pass.

Required assertions per case:

```text
lane
feasibility
mandate_fit
company_fit
recommendation
action
confidence
supports
concerns
blockers
unknowns
interactions
caps
clarifications
```

Golden fixtures backed by incomplete source text must preserve their caps.

Policy-only cases may use synthetic canonical Step 2 records explicitly marked synthetic.

Do not fabricate live vacancy evidence.

---

# 13. Additional mandatory invariants

Add explicit regression tests:

1. Infeasible always rejects.
2. Company exceptional never rescues mandate weak/mismatch.
3. Company mismatch always rejects.
4. Mandate exceptional + company weak is promising.
5. Uncertain never yields strong/exceptional.
6. Unknown never equals false.
7. KZ local sponsorship unknown stays feasible.
8. Remote US sponsorship no/unknown does not trigger onsite sponsorship gate.
9. Crypto concern affects only company fit.
10. Platform engineering never supports platform-as-business.
11. Internal tools yields mandate mismatch.
12. Title-only evidence caps at promising.
13. Every active item has evidence.
14. Every cap is explained.
15. Every blocking unknown has clarification.
16. Matrix coverage is total.
17. No numeric score or weight is present.
18. No generic concern-count threshold exists.
19. Repeated evaluation is semantically deterministic.
20. Interaction application is idempotent.
21. Unsupported model major yields error/no verdict.
22. Runtime code cannot import legacy score/tier/recommendation.
23. Production cannot import shadow evaluator.
24. Shadow evaluator cannot write DB or send messages.

---

# 14. Historical full-text recovery prerequisite

Before final replay acceptance, perform a bounded read-only recovery attempt for flagship title-only cases, especially:

```text
Wise APAC Growth & Expansion
Wise Pricing
Wise Acquiring
Wise Financial Crime
Wise Onboarding
```

Allowed sources:

- existing stored source URL;
- current public ATS/careers endpoint;
- archived local source artifacts, if already available;
- existing browser profile only if read-only and explicitly safe.

Do not:

- log in interactively;
- bypass anti-bot;
- send applications;
- mutate source records;
- call external LLM providers.

Store recovered text only in an offline replay fixture/artifact unless a separate migration task approves production DB updates.

For each case report:

```text
recovered_full_text
not_available
listing_closed
source_blocked
ambiguous_match
```

If text cannot be recovered, keep the case capped and classify replay disagreement as:

```text
insufficient_vacancy_evidence
```

---

# 15. Historical replay

Implement a read-only replay path.

## 15.1 Cohort

Use the Decision SoT exclusions:

- test users;
- smoke users;
- resend duplicates;
- data-quality excluded cases;
- duplicate vacancy keys;
- production artifacts without enough identity linkage.

Read live DB only if necessary and strictly read-only.

Prefer exporting a bounded offline replay snapshot before evaluation.

## 15.2 Per-case output

```yaml
vacancy_key:
source:
legacy_result:
shadow_result:
user_feedback:
evidence_completeness:
difference_classification:
explanation_coverage:
```

## 15.3 Disagreement taxonomy

Use exactly:

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

Do not invent an eleventh category without SoT amendment.

## 15.4 Metrics

Produce:

- recommendation distribution;
- action distribution;
- positive precision by recommendation band;
- recall for applied/exceptional/interesting;
- negative precision;
- infeasible precision;
- unclear/unknown rate;
- lane-specific performance;
- source-specific performance;
- explanation coverage;
- top applied caps;
- top blockers;
- top supports;
- top clarifications;
- critical false-negative list;
- critical false-positive list;
- disagreement causes.

Do not produce one aggregate accuracy score.

---

# 16. Manual review queue

Generate bounded manual-review outputs for:

- every shadow possible false negative;
- every shadow possible false positive;
- every decision contract gap;
- every preference model gap;
- every vacancy understanding gap;
- flagship cases with incomplete evidence.

Each review item must show:

```text
vacancy
legacy outcome
shadow outcome
feedback
matched facts
matched rules
caps
unknowns
explanation
suggested disagreement class
```

No automatic contract changes.

No automatic learning.

---

# 17. Production isolation

The implementation must remain physically isolated.

Required guards:

- production modules must not import `job_intel.shadow_evaluator`;
- shadow evaluator must not import production delivery, Slack, CRM, or write-store modules;
- no live evaluator replacement;
- no feature flag enabling production use;
- no DB schema migration;
- no cron integration;
- no normal daily-run invocation;
- no recruiter flow consumption;
- no scoring config modification.

An offline CLI or test harness may import shadow evaluator explicitly.

---

# 18. Observability

Implement offline, artifact-based observability only.

Suggested outputs:

```text
artifacts/shadow-evaluator/replay/<run_id>/
```

Contents:

```text
run-metadata.json
case-results.jsonl
summary.json
disagreements.jsonl
critical-false-negatives.md
critical-false-positives.md
clarification-summary.md
```

No production metric writes.

Every run must record:

```text
decision contract version
preference model version
vacancy schema version
evaluator version
fixture/replay snapshot hash
run timestamp
```

---

# 19. Suggested bounded slices

## Slice 3.0 — Owner-decision amendment

Deliver:

- Step 3A SoT v1.1.0;
- O1–O6 resolved;
- generic concern count removed;
- tests green.

Commit separately.

## Slice 3.1 — Evaluator output model and policy loader

Deliver:

- strict output Pydantic model;
- validated immutable decision-contract loader;
- version guards;
- semantic determinism helpers;
- no evaluation logic yet.

Commit separately.

## Slice 3.2 — Feasibility and lane engine

Deliver:

- lane routing;
- feasibility matching;
- feasibility interactions;
- precedence;
- terminal infeasible;
- unknown feasibility policy;
- tests.

Commit separately.

## Slice 3.3 — Mandate and company engines

Deliver:

- support/concern/blocker matching;
- qualitative bands;
- interaction effects;
- KZ fallback handling;
- crypto company isolation;
- tests.

Commit separately.

## Slice 3.4 — Confidence, matrix, caps, action

Deliver:

- confidence propagation;
- unknown ledger;
- matrix resolver;
- monotonic caps;
- action mapping;
- tests.

Commit separately.

## Slice 3.5 — Explanation and clarification

Deliver:

- canonical explanation items;
- clarification generation;
- evidence validation;
- interaction trace;
- tests.

Commit separately.

## Slice 3.6 — Golden suite

Deliver:

- all golden cases executable;
- invariant tests;
- deterministic replay tests;
- no production imports.

Commit separately.

## Slice 3.7 — Full-text recovery and offline replay

Deliver:

- bounded recovery report;
- replay snapshot;
- disagreement analysis;
- manual review queues;
- offline observability artifacts.

Commit separately.

## Slice 3.8 — Final documentation and readiness

Deliver:

- implementation document;
- replay report;
- disagreement analysis;
- migration/readiness plan for the next step;
- no production integration.

Commit separately.

Do not collapse the work into one large commit.

---

# 20. Definition of Done

Step 3 is complete only when:

1. Owner decisions O1–O6 are incorporated in SoT v1.1.0.
2. Generic concern counting is absent everywhere.
3. Runtime evaluator consumes only Step 1 and Step 2 canonical models.
4. Decision contract is loaded and validated centrally.
5. Four independent outputs are produced.
6. Lane routing is correct.
7. Feasibility precedence is correct.
8. All six interaction effects are implemented exactly.
9. Unknown semantics are field-specific and tested.
10. Mandate/company bands use qualitative criteria only.
11. Recommendation matrix is complete and centralized.
12. Caps are monotonic and explained.
13. Action is derived from recommendation/confidence/feasibility.
14. KZ fallback remains isolated and standby.
15. Exploration remains isolated and one-axis-at-a-time.
16. Every active decision item has evidence.
17. Every blocking unknown has clarification.
18. Every golden decision case passes.
19. All mandatory invariants pass.
20. Evaluation is semantically deterministic.
21. Historical replay completes on a bounded clean cohort.
22. Full-text recovery status is reported for flagship cases.
23. Disagreement analysis uses the approved taxonomy.
24. Critical FN/FP lists are manually reviewable.
25. No single aggregate quality score is used.
26. No silent policy learning occurs.
27. No production module imports shadow evaluator.
28. No production integration is enabled.
29. Step 1, Step 2, Step 3A, and Step 3 tests are green.
30. `git diff --check` is clean.
31. Safety constraints are confirmed.

---

# 21. Required final report

Return:

1. **Verdict**
2. **Preflight**
3. **SoT amendment**
4. **Commits**
5. **Evaluator architecture**
6. **Output model**
7. **Decision contract loading**
8. **Feasibility and lane results**
9. **Mandate/company evaluation**
10. **Interactions**
11. **Unknown and confidence handling**
12. **Matrix, caps and action mapping**
13. **Explanations and clarifications**
14. **Golden results**
15. **Full-text recovery**
16. **Historical replay cohort**
17. **Replay metrics**
18. **Disagreement analysis**
19. **Critical false negatives**
20. **Critical false positives**
21. **Remaining gaps**
22. **Next-step readiness**
23. **Safety confirmation**

Safety confirmation must explicitly state:

- no push;
- no gateway restart;
- no live config changes;
- no Slack/Telegram sends;
- no production DB writes;
- live DB reads, if any, were read-only and bounded;
- no provider calls;
- no production imports;
- no production integration;
- stash untouched.

---

# 22. Stop conditions

Stop and report instead of inventing behaviour if:

- human and machine SoTs disagree;
- a golden expected outcome contradicts the approved matrix;
- a Step 1 rule references a Step 2 field that does not exist;
- an interaction target cannot be resolved;
- a band cannot be determined without a new product decision;
- full-text evidence is unavailable for a case whose acceptance depends on it;
- the only way to proceed would require production writes or provider execution.

A partial, evidence-backed implementation is preferable to an undocumented policy invention.

---

# 23. Success criterion

The task succeeds when the runtime evaluator is demonstrably a transparent, deterministic translation of the approved Decision SoT and can be evaluated offline against historical evidence without changing any production behaviour.
