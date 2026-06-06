# ATS Validation + Funnel Audit (2026-05-30)

Scope: validate ATS adapters can return real vacancies (temporary seeds allowed), and explain why the latest meaningful **production daily** run rejects everything.

This report is evidence-based (sqlite + direct adapter calls). No redesign is implemented here.

## 0) Executive Summary

Findings:
- **Production daily run `run_id=177` did not run ATS at all**: all ATS sources were `source_status=skipped`. Only **LinkedIn + HeadHunter** executed.
- Funnel problem is real: for `run_id=177`, `evaluated=67`, `accepted_like=0`.
- Rejections are dominated by **hard-gates requiring P&L / executive responsibility / product ownership** and by **geography gating**.
- ATS adapters themselves are queryable (with temporary seeds) for **Greenhouse** and **Ashby** (hundreds of vacancies returned). **Lever** seeds provided are invalid (404). **SmartRecruiters/Personio/Recruitee** are currently **not discoverable without working discovery or seeds**.
- Executive classifier is still too permissive: e.g. `Account Executive` is treated as executive leadership because of the token `executive` in the title.

## 1) Latest Meaningful Production Run Chosen For Funnel Audit

Chosen run: **`run_id=177` (production, mode=daily)**
- started: `2026-05-30T07:16:20Z`
- finished: `2026-05-30T07:26:34Z`

Reason: it is the latest production daily with non-zero results and clean `status=ok`.

## 2) Production Source KPI Snapshot (run_id=177)

From `source_kpi_run`:
- `linkedin`: `status=ok found=34 executive_detected=24 scored=34 accepted=0 notified=0 rejected=34`
- `headhunter`: `status=ok found=33 executive_detected=13 scored=33 accepted=0 notified=0 rejected=33`

ATS sources in this run:
- `greenhouse/lever/ashby/teamtailor/smartrecruiters/personio/recruitee`: **`status=skipped found=0`**

Implication: any KPI/weekly comparisons involving ATS for this run are not measuring ATS yield.

## 3) Funnel Audit: Why Accepted = 0 (run_id=177)

### 3.1 Funnel Counts

From sqlite:
- `vacancy_evaluations` rows: **67**
- `recommendation != reject`: **0**

By source:
- `linkedin`: evaluated 34, accepted_like 0
- `headhunter`: evaluated 33, accepted_like 0

### 3.2 Rejection Breakdown (by vacancy, top reason)

Computed from `vacancy_rejection_summary.top_rejection_reason` for rejected vacancies (`n=37`):
- `wrong_geography`: 12 (32.4%)
- `missing_p_and_l`: 11 (29.7%)
- `missing_executive_responsibility`: 9 (24.3%)
- `missing_product_ownership`: 5 (13.5%)

### 3.3 Rejection Events (multi-reason telemetry)

`vacancy_rejection_events` is multi-label, so totals exceed vacancy count. For `run_id=177` there are `581` rejection events. Top reasons:
- `missing_p_and_l`: 67 (11.5%)
- `salary_too_low`: 67 (11.5%)
- `low_company_score`: 67 (11.5%)
- `insufficient_data`: 67 (11.5%)
- `blocked_geography`: 67 (11.5%)
- `low_hiring_likelihood`: 66 (11.4%)
- `low_confidence`: 52 (9.0%)
- `missing_product_ownership`: 40 (6.9%)
- `missing_executive_responsibility`: 30 (5.2%)
- `low_seniority`: 30 (5.2%)
- `wrong_geography`: 23 (4.0%)

Interpretation:
- Even when a role looks "executive" by title, acceptance is blocked by multiple "evidence requirements" that are rarely explicit in job descriptions (P&L, product ownership proof, executive responsibility proof).
- Geography is also acting as a strong hard gate.

## 4) Near-Miss Analysis (run_id=177)

Threshold context: the evaluator uses score tiers; acceptance requires `recommendation != reject`, effectively score >= `possible_fit` threshold (configured at 60).

Top rejected (highest scores) are still far below 60.

Top examples (from the **top-20 near misses**):
1. HeadHunter | Meteoro Platform | Product Lead | score=47 | rejected: `missing_p_and_l` | https://hh.ru/vacancy/133652461
2. LinkedIn | Discovered MENA | Head of Product - Fintech | score=40 | rejected: `wrong_geography` | https://www.linkedin.com/jobs/view/4417070084
3. LinkedIn | Social Discovery Group | Head of Product (AI Product) - Full remote | score=35 | rejected: `wrong_geography` | https://www.linkedin.com/jobs/view/4384021732

## 5) Executive Classifier Audit (run_id=177)

Using stored `vacancy_evaluations.matched_signals_json` as the "why".

Example items from top executive-detected candidates:
- HeadHunter | Meteoro Platform | Product Lead | score=47 | why: `executive-level leadership`, `growth/strategy/product leadership`, ...
- LinkedIn | Head of Product - Fintech | score=40 | why: `executive-level leadership`, `telecom/fintech adjacency`, `remote friendly`

Red flags observed:
- `Product Launch Manager` appears in the executive list because the classifier matches on broad leadership tokens and the scoring pipeline treats it as executive visibility.
- **ATS validation shows an even stronger failure mode**: `Account Executive` roles score as executive due to the word `executive`.

Conclusion: executive detection is currently conflating sales/BD roles with product leadership.

## 6) ATS Validation (Direct Adapter Calls With Temporary Seeds)

Important: this does **not** claim production readiness; it only proves whether each adapter can return real vacancies when given seeds (or discovery works).

Temporary env seed support used:
- `JOB_INTEL_ATS_SEEDS_GREENHOUSE`
- `JOB_INTEL_ATS_SEEDS_LEVER`
- `JOB_INTEL_ATS_SEEDS_ASHBY`
- `JOB_INTEL_ATS_SEEDS_TEAMTAILOR`
- `JOB_INTEL_ATS_SEEDS_SMARTRECRUITERS`
- `JOB_INTEL_ATS_SEEDS_PERSONIO`
- `JOB_INTEL_ATS_SEEDS_RECRUITEE`

Queries passed (only for discovery fallback): `VP Product`, `Head of Product`, `Chief Product Officer`, `Director of Product`.

### 6.1 Results Table

- `greenhouse`: status=`degraded` discovered_companies=5 pages=5 found=559 executive_detected=483 accepted=465
  - errors: `404 greenhouse board=canva`, `404 greenhouse board=plaid`
- `lever`: status=`error` discovered_companies=5 pages=5 found=0 executive_detected=0 accepted=0
  - errors: `404 lever site=miro`, `404 lever site=zapier`, `404 lever site=circle`, `404 lever site=eventbrite` (seed slugs invalid)
- `ashby`: status=`degraded` discovered_companies=5 pages=5 found=505 executive_detected=277 accepted=232
  - errors: `404 ashby board=anthropic` (seed board invalid)
- `teamtailor`: status=`empty` discovered_companies=6 pages=6 found=0
- `smartrecruiters`: status=`empty` discovered_companies=0 pages=0 found=0
- `personio`: status=`empty` discovered_companies=0 pages=0 found=0
- `recruitee`: status=`empty` discovered_companies=0 pages=0 found=0

### 6.2 Vacancy Samples (Evidence)

Greenhouse (seeded via env):
- stripe | Account Executive, Enterprise - Billing | London | https://stripe.com/jobs/search

Ashby (seeded via env):
- openai | Research Engineer, Retrieval & Search, Applied Engineering | San Francisco | https://jobs.ashbyhq.com/openai/7322d344-9325-4a92-8445-0a2c4e9272f8

Notes:
- The sample set shows the adapter is returning real postings, but the **role filter/classifier is not enforcing product leadership** (sales roles are treated as executive due to token matching).

## 7) ATS Dependency Review (Current State)

### 7.1 How ATS discovery works today

If `companies` are not provided and env seeds are not set, ATS sources call `discover_companies(...)`, which extracts ATS tenant slugs by regex from HTML search results.

This makes **DuckDuckGo HTML search a critical dependency** for ATS discovery.

### 7.2 SPOF risk

- If DDG returns no parsable results for the host/IP, ATS discovery yields 0 companies.
- In that case, sources with no seeds become effectively **unusable** (they return `found=0`).

### 7.3 Recommended direction (no redesign implemented here)

Short-term (validation only): keep temporary `JOB_INTEL_ATS_SEEDS_*` to prove adapters produce data.

Medium-term (for redesign phase later): remove DDG as SPOF by adding alternate discovery channels (company->careers parsing, cached seed store from prior runs, curated directories, etc.).

## 8) Recommended Next Actions (No New Sources)

1. Fix executive classifier false positives (block `Account Executive` and other non-product executive titles from being treated as product leadership).
2. Decide on evidence gates (P&L and product ownership): they currently dominate rejections. If they stay hard-gates, accepted will often be 0.
3. Geography normalization/gating review: top rejection reason is `wrong_geography` (32.4%).
4. ATS production enablement: ensure ATS sources are not `skipped` in production daily; for validation, add seeds in `/etc/job-intel/job-intel.env` temporarily and measure ATS yield in `source_kpi_run`.
5. Lever seeds: provided slugs are invalid (404). Need correct Lever tenant slugs to validate Lever.
