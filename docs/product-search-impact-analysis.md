# Product Search Impact Analysis

## Execution context

- Feature branch: `codex/job-intel-product-search`
- Linked worktree: `/home/hermes/.hermes/hermes-agent/.worktrees/job-intel-product-search`
- Initial canonical base: `local/customizations@bbf2ae0f4885dcbd4be5a08692d6d757372fd0d8`
- Runtime for development verification: Python 3.12.13
- Product authority: `PS-SOT-2026-08-10-v1`, version 1.0.0, Approved

## Owner-accepted baseline exception

The pre-edit focused Job Intel and Slack baseline completed with `36 failed, 1273 passed, 15 warnings`.
On 2026-08-11 the owner directed execution to continue with this result recorded as an
**owner-accepted known-red baseline**. This records pre-existing failures; it does not waive Product Search regression failures,
focused-task verification, scope-guard failures, or any
new failure outside the exact recorded baseline. No baseline failure is treated as fixed.

The failures cluster in legacy LinkedIn acquisition tests, removed CRM command surfaces,
deployment/monitoring artifact assumptions, recruiter read-facade behavior, runtime provenance,
and Slack coroutine warnings. Product Search work may not edit protected scraper files to make
this baseline green.

A post-change control rerun preserved the exact 36 failing node IDs and 1273 passing tests. Its
warning count was 16 rather than 15 because another test emitted the same unawaited Slack coroutine
warning family. Warning aggregation is therefore recorded as timing-sensitive; any new failing node
ID remains a regression and is not covered by the owner decision.

## Authority and impact map

| Area | Current authority | Product Search impact |
|---|---|---|
| Candidate Facts / structured resume | Candidate Facts and structured resume | Remains canonical; no profile, provider, migration, or policy may broaden experience claims. |
| Career Profile | Existing preference model until Career Profile v2 | Task 8 adds a versioned profile bounded by Candidate Facts and Product Search SoT. |
| Vacancy facts | Official vacancy evidence | Remains canonical; source, title, or company reputation cannot substitute for evidence. |
| Semantic Contract v1 | `semantic-fact-contract.yaml` | Remains immutable until a separately approved versioned migration. |
| Decision Contract v1 | `shadow_evaluator/decision-contract.yaml` | Remains a non-user-facing counterfactual; it cannot emit Product Search decisions. |
| Decision Contract v2 | Product Search SoT plus future v2 contract | Task 11 becomes the sole authority for canonical stage 4 and Product Search verdicts. |
| CRM | Existing CRM lifecycle | Remains authoritative for application and outreach state; Product Search events cannot imply submission. |
| Search policy | Product Search SoT and future Search Contract v1 | Adds auditable cells, families, freshness, and observation states without editing existing scrapers. |
| Feedback | Existing feedback contract | Remains parallel; failures may not create a protected-channel root. |
| Reactions | Approved reaction-trigger SoT | Preserved for correlated evaluation/package actions; reactions do not imply user decisions or CRM state. |
| Slack adapter | Existing Hermes Slack app and Socket Mode adapter | Later receives a typed publisher, interaction handler, and shared protected-channel deny policy. |
| Schedulers | Existing Job Intel units/timers | Remain unchanged until owner-approved gate checkpoints; experiments use isolated temporary units. |
| Metrics and dashboards | Existing Job Intel observability | Later gains Product Search metrics; operational output never goes to the product channel. |

## Protected implementation boundary

The four existing scraper modules and production search seed files are frozen. Their hashes and
base commit are recorded in `docs/product-search-scope-baseline.yaml`. The scope guard compares
the feature diff from the current merge-base and fails closed on feature-authored changes. A
reviewed upstream change requires an explicit baseline repin after rebase; it is never silently
attributed to this feature.

## Conflict handling

Unknown authority IDs and unresolved authority conflicts fail closed. Resolution requires a named,
versioned migration or explicit owner decision. Product Search supersession is limited to the
legacy policies named by Appendix B of the approved SoT.
