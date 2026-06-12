# Scoring v2 Simulation (Offline)

Constraints: no changes to production code/evaluator/thresholds. This is an offline simulation.
Neutral assumptions: company_score = neutral; hiring_likelihood = neutral; unknown fields do not subtract.

## 1. Candidate Scoring v2 (weights)

### Current weights (v1 reference)
- AI_or_modern_tech: 10
- B2C_platform: 15
- PnL_ownership: 25
- executive_visibility: 20
- fintech_or_telecom: 15
- growth_signal: 12
- monetization_responsibility: 25
- product_ownership: 20
- remote_friendly: 5

### Proposed weights (v2 candidate)
- executive_visibility: 15 (gated by product-domain)
- head_of_product_title_bonus: 10
- product_lead_title_bonus: 10
- fintech_or_telecom: 18
- B2C_platform: 10
- AI_or_modern_tech: 12
- remote_friendly: 5
- growth_signal: 20 (only if product-growth ownership, not perf/analytics/marketing/sales/BD)
- product_ownership: 25
- monetization_responsibility: 30
- PnL_ownership bonus: 10 (bonus only)
- non_product_function_penalty: -35 (only when product-domain is absent)

## 2. Offline replay (calibration dataset)

| # | title | model_score_v1 | rec_v1 | model_score_v2 | rec_v2 | human_rating | url |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | Product Lead | 47 | near_miss | 35 | reject | reject | https://hh.ru/vacancy/133652461 |
| 2 | Head of Product - Fintech | 40 | near_miss | 48 | near_miss | possible_fit | https://www.linkedin.com/jobs/view/4417070084 |
| 3 | Head of Product (AI Product) - Full remote | 35 | reject | 42 | near_miss | strong_fit | https://www.linkedin.com/jobs/collections/recommended |
| 4 | Head of Product (AI Product) - Full remote | 35 | reject | 42 | near_miss | possible_fit | https://www.linkedin.com/jobs/view/4384021732 |
| 5 | Product Lead (Афиша) | 32 | reject | 25 | reject | possible_fit | https://hh.ru/vacancy/133470638 |
| 6 | Growth Product Lead | 32 | reject | 45 | near_miss | possible_fit | https://hh.ru/vacancy/132562956 |
| 7 | Head of Sales (B2B, Fintech / Crypto) | 25 | reject | -5 | reject | reject | https://hh.ru/vacancy/133668801 |
| 8 | HNB Consumer Engagment Lead | 25 | reject | 10 | reject | reject | https://hh.ru/vacancy/133402913 |
| 9 | Product Launch Manager (1 Position at Head Office) | 20 | reject | 25 | reject | reject | https://www.linkedin.com/jobs/view/4338017837 |
| 10 | Head of Product | 20 | reject | 25 | reject | reject | https://hh.ru/vacancy/133446873 |
| 11 | Digital Marketing Specialist (Technical Software) / Специалист по цифровому маркетингу (fintech) | 15 | reject | -17 | reject | reject | https://hh.ru/vacancy/133665432 |
| 12 | Business Analyst with Fintech and Banking experience | 15 | reject | 18 | reject | reject | https://hh.ru/vacancy/125440730 |
| 13 | Key Account Manager (Consumer Products Division) | 15 | reject | 10 | reject | reject | https://hh.ru/vacancy/132989208 |
| 14 | Head of Marketing and Go-to Market department | 10 | reject | -35 | reject | reject | https://hh.ru/vacancy/132668007 |
| 15 | AI Product & Operations Lead | 10 | reject | 37 | reject | reject | https://hh.ru/vacancy/133486511 |
| 16 | International Expansion Lead (IT & Product Companies) | 10 | reject | 25 | reject | reject | https://hh.ru/vacancy/133043996 |
| 17 | Unit Manager, Digital Banking for Product Management - 01 post. | 0 | reject | 0 | reject | reject | https://www.linkedin.com/jobs/view/4316220156 |
| 18 | Deputy Head of Cloud Products and Services at MUK Cloud | 0 | reject | 25 | reject | reject | https://www.linkedin.com/jobs/view/4401491114 |
| 19 | Chief Business Development Officer | 0 | reject | -35 | reject | reject | https://hh.ru/vacancy/132653639 |
| 20 | Senior Product Analyst | 0 | reject | 0 | reject | reject | https://hh.ru/vacancy/132390785 |
| 21 | CMO (Chief Marketing Officer) | 0 | reject | -35 | reject | reject | https://hh.ru/vacancy/133504798 |
| 22 | Head of Cargo Marketing | 0 | reject | -35 | reject | reject | https://hh.ru/vacancy/132525450 |
| 23 | Head of Marketing / Ведущий маркетолог | 0 | reject | -35 | reject | reject | https://hh.ru/vacancy/133497057 |
| 24 | Продуктовый маркетолог / Product Marketing Manager | 0 | reject | 0 | reject | reject | https://hh.ru/vacancy/133642542 |
| 25 | Business Growth Lead / CVM & Partnerships Lead | 0 | reject | -15 | reject | reject | https://hh.ru/vacancy/132546096 |
| 26 | Senior Growth Manager (Performance & Analytics) | 0 | reject | 0 | reject | reject | https://hh.ru/vacancy/133515581 |
| 27 | Head of Digital / Digital Marketing Manager | 0 | reject | -35 | reject | reject | https://hh.ru/vacancy/132728958 |
| 28 | Product Marketing Lead | 0 | reject | 25 | reject | reject | https://hh.ru/vacancy/133399864 |
| 29 | Production Team Lead (Руководитель Производства) | 0 | reject | 25 | reject | reject | https://hh.ru/vacancy/133203696 |
| 30 | Product Marketing Manager (ВНЕ РФ И РБ) | 0 | reject | 0 | reject | reject | https://hh.ru/vacancy/133397975 |

## 3. Metrics (review-positive)

Review-positive means `{near_miss, potential_fit, strong_fit}` vs human-positive `{possible_fit, strong_fit, exceptional_fit}`.

### V1 (current model)
- TP=1 TN=24 FP=1 FN=4
- precision=0.5
- recall=0.2

### V2 (simulated)
- TP=4 TN=25 FP=0 FN=1
- precision=1.0
- recall=0.8

Target criteria: recall>=0.6 and precision>=0.3 on calibration set.

## 4. Focus vacancies (bucket transitions and why)

### Head of Product - Fintech
- Head of Product - Fintech | v1=40/near_miss | v2=48/near_miss | human=possible_fit
  - v1 breakdown: +20 executive_visibility, +15 fintech_or_telecom, +5 remote_friendly
  - v2 breakdown: +18 fintech_or_telecom, +15 executive_visibility, +10 head_of_product_title_bonus, +5 remote_friendly

### Head of Product (AI Product)
- Head of Product (AI Product) - Full remote | v1=35/reject | v2=42/near_miss | human=strong_fit
  - v1 breakdown: +20 executive_visibility, +10 AI_or_modern_tech, +5 remote_friendly
  - v2 breakdown: +15 executive_visibility, +12 AI_or_modern_tech, +10 head_of_product_title_bonus, +5 remote_friendly
- Head of Product (AI Product) - Full remote | v1=35/reject | v2=42/near_miss | human=possible_fit
  - v1 breakdown: +20 executive_visibility, +10 AI_or_modern_tech, +5 remote_friendly
  - v2 breakdown: +15 executive_visibility, +12 AI_or_modern_tech, +10 head_of_product_title_bonus, +5 remote_friendly

### Product Lead (Афиша)
- Product Lead (Афиша) | v1=32/reject | v2=25/reject | human=possible_fit
  - v1 breakdown: +20 executive_visibility, +12 growth_signal
  - v2 breakdown: +15 executive_visibility, +10 product_lead_title_bonus

### Growth Product Lead
- Growth Product Lead | v1=32/reject | v2=45/near_miss | human=possible_fit
  - v1 breakdown: +20 executive_visibility, +12 growth_signal
  - v2 breakdown: +20 growth_signal, +15 executive_visibility, +10 product_lead_title_bonus

## 5. Recent production runs (distribution shift, deduped)

Rows: 60 (dedup by URL) from last 10 production daily ok runs.
- v1 bucket distribution: {'reject': 60}
- v2 bucket distribution: {'reject': 55, 'near_miss': 5}

Note: production replay has no human labels; this section is only to estimate how bucket volume would change under v2.

