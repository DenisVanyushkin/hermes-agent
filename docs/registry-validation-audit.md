# Registry Validation Audit

Run: `273`
Mode: `daily_registry_validation` | Status: `ok` | Run type: `manual`
Started: `2026-06-01T09:36:21.499613+00:00` | Finished: `2026-06-01T09:39:20.978066+00:00`

## Collection Quality

| company | source | ats_slug | vacancies_found | unique_urls | unique_titles | duplicate_rate | source_status |
|---|---|---|---:|---:|---:|---:|---|
| Adyen | greenhouse | adyen | 200 | 200 | 164 | 0.00% | ok |
| Airwallex | ashby | airwallex | 300 | 298 | 271 | 0.67% | ok |
| Canva | smartrecruiters | canva | 297 | 294 | 226 | 1.01% | ok |
| Delivery Hero | smartrecruiters | deliveryhero | 300 | 0 | 0 | 100.00% | ok |
| GitLab | greenhouse | gitlab | 162 | 161 | 154 | 0.62% | ok |
| Talabat | smartrecruiters | deliveryhero | 300 | 296 | 280 | 1.33% | ok |
| Wise | smartrecruiters | wise | 300 | 299 | 284 | 0.33% | ok |
| Wolt | greenhouse | wolt | 200 | 198 | 149 | 1.00% | ok |

Configured collection limits (run context):
- `greenhouse`: env `JOB_INTEL_ATS_GREENHOUSE_MAX_JOBS_PER_COMPANY` (not set in validation run), default `200`
- `ashby`: env `JOB_INTEL_ATS_ASHBY_MAX_JOBS_PER_COMPANY` (not set in validation run), default `300`
- `smartrecruiters`: env `JOB_INTEL_ATS_SMARTRECRUITERS_MAX_JOBS_PER_COMPANY` (not set in validation run), default `250`
- Estimated totals are not consistently provided by all connectors; this audit uses observed `vacancies_found` and uniqueness metrics.

## Funnel Quality

- collected: `2059`
- stored: `2059`
- deduped (unique vacancy_key proxy): `1746`
- scored: `2059`
- executive_detected (deduped): `612`
- near_miss: `177`
- potential_fit: `175`
- strong_fit: `370`

## Opportunity Quality

Top 25 by score (deduped by vacancy_key):

| company | title | source | score | recommendation | url |
|---|---|---|---:|---|---|
| adyen | Alliance Partnership Manager | greenhouse | 100 | strong_fit | https://job-boards.greenhouse.io/adyen/jobs/7694136 |
| adyen | Data Analyst II | greenhouse | 100 | strong_fit | https://job-boards.greenhouse.io/adyen/jobs/7865350 |
| adyen | Data Analyst - Regulatory Reporting | greenhouse | 100 | strong_fit | https://job-boards.greenhouse.io/adyen/jobs/7903556 |
| adyen | Enterprise Account Manager | greenhouse | 100 | strong_fit | https://job-boards.greenhouse.io/adyen/jobs/7342856 |
| adyen | Enterprise Account Manager | greenhouse | 100 | strong_fit | https://job-boards.greenhouse.io/adyen/jobs/6918826 |
| adyen | Enterprise Account Manager | greenhouse | 100 | strong_fit | https://job-boards.greenhouse.io/adyen/jobs/6762094 |
| adyen | Enterprise Account Manager | greenhouse | 100 | strong_fit | https://job-boards.greenhouse.io/adyen/jobs/7451786 |
| adyen | Enterprise, Account Manager | greenhouse | 100 | strong_fit | https://job-boards.greenhouse.io/adyen/jobs/6542244 |
| adyen | Group Product Manager, Credit | greenhouse | 100 | strong_fit | https://job-boards.greenhouse.io/adyen/jobs/6630580 |
| adyen | HR Business Partner - Tech | greenhouse | 100 | strong_fit | https://job-boards.greenhouse.io/adyen/jobs/7641158 |
| adyen | Optimization Data Analyst | greenhouse | 100 | strong_fit | https://job-boards.greenhouse.io/adyen/jobs/7571869 |
| adyen | Senior Alliances Partner Manager | greenhouse | 100 | strong_fit | https://job-boards.greenhouse.io/adyen/jobs/7913587 |
| adyen | Senior Enterprise Account Manager | greenhouse | 100 | strong_fit | https://job-boards.greenhouse.io/adyen/jobs/6761918 |
| adyen | Senior Observability Infrastructure Engineer | greenhouse | 100 | strong_fit | https://job-boards.greenhouse.io/adyen/jobs/7607691 |
| adyen | Senior Platform Engineer | greenhouse | 100 | strong_fit | https://job-boards.greenhouse.io/adyen/jobs/7547055 |
| gitlab | Backend Engineer, Analytics Instrumentation (Golang) | greenhouse | 100 | strong_fit | https://job-boards.greenhouse.io/gitlab/jobs/8481929002 |
| gitlab | Customer Success Manager | greenhouse | 100 | strong_fit | https://job-boards.greenhouse.io/gitlab/jobs/8512758002 |
| gitlab | Customer Success Manager - Australia | greenhouse | 100 | strong_fit | https://job-boards.greenhouse.io/gitlab/jobs/8548980002 |
| gitlab | Customer Success Manager, Japan | greenhouse | 100 | strong_fit | https://job-boards.greenhouse.io/gitlab/jobs/8478618002 |
| gitlab | Director of Pricing | greenhouse | 100 | strong_fit | https://job-boards.greenhouse.io/gitlab/jobs/8500092002 |
| gitlab | Distinguished Engineer, Agentic SDLC & Non‑Linear Productivity | greenhouse | 100 | strong_fit | https://job-boards.greenhouse.io/gitlab/jobs/8537853002 |
| gitlab | Intermediate Vulnerability Researcher, AST: Vulnerability Research | greenhouse | 100 | strong_fit | https://job-boards.greenhouse.io/gitlab/jobs/8443302002 |
| gitlab | Lead Product Marketing Manager, AI | greenhouse | 100 | strong_fit | https://job-boards.greenhouse.io/gitlab/jobs/8472475002 |
| gitlab | Lead Product Marketing Manager, Pricing and Packaging | greenhouse | 100 | strong_fit | https://job-boards.greenhouse.io/gitlab/jobs/8542042002 |
| gitlab | Lead Talent Management Partner | greenhouse | 100 | strong_fit | https://job-boards.greenhouse.io/gitlab/jobs/8436097002 |

## Executive Opportunity Yield

- VP Product: `0`
- Head of Product: `0`
- Director Product: `0`
- GM Product: `0`
- CPO: `0`

## Registry ROI

| company | executive_vacancies | near_miss | potential_fit | strong_fit |
|---|---:|---:|---:|---:|
| Adyen | 199 | 37 | 27 | 132 |
| Airwallex | 215 | 61 | 51 | 176 |
| Canva | 16 | 9 | 0 | 0 |
| Delivery Hero | 0 | 0 | 0 | 0 |
| GitLab | 106 | 14 | 75 | 61 |
| Talabat | 1 | 0 | 0 | 0 |
| Wise | 16 | 10 | 7 | 1 |
| Wolt | 59 | 46 | 15 | 0 |
