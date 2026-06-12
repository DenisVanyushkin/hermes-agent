# Scoring v2 Design (Calibration-Based, Cleaned)

Ground truth: `opportunity-thesis-calibration-filled.md` (human_labeled).
This version applies a **calibration cleanup** override for 3 cases explicitly requested by the operator:
- `Head of Sales (B2B, Fintech / Crypto)` => `reject`
- `Chief Business Development Officer` => `reject`
- `Senior Growth Manager (Performance & Analytics)` => `reject`

Scope constraints: no new sources; no ATS changes; no Company Intelligence changes; no production scoring changes in this phase.

## 1. Calibration Analysis

Definitions used for metrics (two views):
- Review-positive (what should land in human review): model recommendation in `{near_miss, potential_fit, strong_fit}`.
- Alert-positive (what should trigger alert): model recommendation in `{potential_fit, strong_fit}`.
- Human-positive: human_rating in `{possible_fit, strong_fit, exceptional_fit}`.

### Per-vacancy comparison (Top-30 calibration set)

| # | title | company | loc | source | model_score | model_rec | human_rating | delta(human-model) | class(review) | url |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | Product Lead | Meteoro Platform | Unknown | headhunter | 47 | near_miss | reject | -1 | FP | https://hh.ru/vacancy/133652461 |
| 2 | Head of Product - Fintech | Discovered MENA | Remote | linkedin | 40 | near_miss | possible_fit | +0 | TP | https://www.linkedin.com/jobs/view/4417070084 |
| 3 | Head of Product (AI Product) - Full remote | Unknown | Unknown | linkedin | 35 | reject | strong_fit | +2 | FN | https://www.linkedin.com/jobs/collections/recommended |
| 4 | Head of Product (AI Product) - Full remote | Social Discovery Group | Remote | linkedin | 35 | reject | possible_fit | +1 | FN | https://www.linkedin.com/jobs/view/4384021732 |
| 5 | Product Lead (Афиша) | ТОО Ticketon Events | Алматы, проспект Жибек Жолы, 135 | headhunter | 32 | reject | possible_fit | +1 | FN | https://hh.ru/vacancy/133470638 |
| 6 | Growth Product Lead | ТОО Bilim Land (Bilim Group) | Unknown | headhunter | 32 | reject | possible_fit | +1 | FN | https://hh.ru/vacancy/132562956 |
| 7 | Head of Sales (B2B, Fintech / Crypto) | BrainShells | Unknown | headhunter | 25 | reject | reject | +0 | TN | https://hh.ru/vacancy/133668801 |
| 8 | HNB Consumer Engagment Lead | ТОО KT&G GLOBAL KAZAKHSTAN (КЕЙ-ТИ-ЭНД-ДЖИ ГЛОБАЛ КАЗАХСТАН) | Unknown | headhunter | 25 | reject | reject | +0 | TN | https://hh.ru/vacancy/133402913 |
| 9 | Product Launch Manager (1 Position at Head Office) | CAMMA Corporate Learning and Development Center | Phnom Penh, Phnom Penh, Cambodia (On-site) | linkedin | 20 | reject | reject | +0 | TN | https://www.linkedin.com/jobs/view/4338017837 |
| 10 | Head of Product | АО CENTER FOR DIGITAL TECHNOLOGY AND INNOVATION | Ташкент, улица Махтумкули, 79 | headhunter | 20 | reject | reject | +0 | TN | https://hh.ru/vacancy/133446873 |
| 11 | Digital Marketing Specialist (Technical Software) / Специалист по цифровому маркетингу (fintech) | Esprow Pte Ltd | Unknown | headhunter | 15 | reject | reject | +0 | TN | https://hh.ru/vacancy/133665432 |
| 12 | Business Analyst with Fintech and Banking experience | Team.Inno / Фабрика инноваций и решений | Минск | headhunter | 15 | reject | reject | +0 | TN | https://hh.ru/vacancy/125440730 |
| 13 | Key Account Manager (Consumer Products Division) | ТОО Лореаль Казахстан | Алматы | headhunter | 15 | reject | reject | +0 | TN | https://hh.ru/vacancy/132989208 |
| 14 | Head of Marketing and Go-to Market department | ТОО BSH Home Appliances (БСХ Хоум Аплайансэс) | Алматы, микрорайон Коктем-1, 15А | headhunter | 10 | reject | reject | +0 | TN | https://hh.ru/vacancy/132668007 |
| 15 | AI Product & Operations Lead | ИП Иванова Марина Николаевна | Unknown | headhunter | 10 | reject | reject | +0 | TN | https://hh.ru/vacancy/133486511 |
| 16 | International Expansion Lead (IT & Product Companies) | ООО Технологический парк программных продуктов и информационных технологий | Ташкент, улица Муминова, 7/2 | headhunter | 10 | reject | reject | +0 | TN | https://hh.ru/vacancy/133043996 |
| 17 | Unit Manager, Digital Banking for Product Management - 01 post. | CAMMA Corporate Learning and Development Center | Phnom Penh, Phnom Penh, Cambodia (On-site) | linkedin | 0 | reject | reject | +0 | TN | https://www.linkedin.com/jobs/view/4316220156 |
| 18 | Deputy Head of Cloud Products and Services at MUK Cloud | MUK | Kazakhstan (On-site) | linkedin | 0 | reject | reject | +0 | TN | https://www.linkedin.com/jobs/view/4401491114 |
| 19 | Chief Business Development Officer | Magnetto.com | Unknown | headhunter | 0 | reject | reject | +0 | TN | https://hh.ru/vacancy/132653639 |
| 20 | Senior Product Analyst | ТОО Freedom Media | Алматы, улица Сергея Луганского, 96 | headhunter | 0 | reject | reject | +0 | TN | https://hh.ru/vacancy/132390785 |
| 21 | CMO (Chief Marketing Officer) | Банк | Алматы | headhunter | 0 | reject | reject | +0 | TN | https://hh.ru/vacancy/133504798 |
| 22 | Head of Cargo Marketing | Международный Аэропорт Алматы, АО | Алматы, аэропорт Алматы | headhunter | 0 | reject | reject | +0 | TN | https://hh.ru/vacancy/132525450 |
| 23 | Head of Marketing / Ведущий маркетолог | ИП АРМАН | Unknown | headhunter | 0 | reject | reject | +0 | TN | https://hh.ru/vacancy/133497057 |
| 24 | Продуктовый маркетолог / Product Marketing Manager | ТОО Сентрас Капитал | Алматы | headhunter | 0 | reject | reject | +0 | TN | https://hh.ru/vacancy/133642542 |
| 25 | Business Growth Lead / CVM & Partnerships Lead | Kaspi.kz | Алматы | headhunter | 0 | reject | reject | +0 | TN | https://hh.ru/vacancy/132546096 |
| 26 | Senior Growth Manager (Performance & Analytics) | ООО Бринго | Unknown | headhunter | 0 | reject | reject | +0 | TN | https://hh.ru/vacancy/133515581 |
| 27 | Head of Digital / Digital Marketing Manager | ТОО СТА Поехали с нами | Алматы | headhunter | 0 | reject | reject | +0 | TN | https://hh.ru/vacancy/132728958 |
| 28 | Product Marketing Lead | BI Group. BI Development | Unknown | headhunter | 0 | reject | reject | +0 | TN | https://hh.ru/vacancy/133399864 |
| 29 | Production Team Lead (Руководитель Производства) | ТОО Breezy | Алматы, проспект Суюнбая, 43/3 | headhunter | 0 | reject | reject | +0 | TN | https://hh.ru/vacancy/133203696 |
| 30 | Product Marketing Manager (ВНЕ РФ И РБ) | SpaceHub | Unknown | headhunter | 0 | reject | reject | +0 | TN | https://hh.ru/vacancy/133397975 |

### Metrics (current model on cleaned calibration set)

**Review-positive**
- TP=1 TN=24 FP=1 FN=4
- precision=0.5
- recall=0.2
- false_positive_rate=0.04
- false_negative_rate=0.8

**Alert-positive**
- TP=0 TN=25 FP=0 FN=5
- precision=n/a
- recall=0.0
- false_positive_rate=0.0
- false_negative_rate=1.0

Distribution (human vs model):
- human: {'reject': 25, 'possible_fit': 4, 'strong_fit': 1}
- model: {'near_miss': 2, 'reject': 28}

## 2. Root Cause Analysis (False Positives / False Negatives)

### Example #1: FP | model=near_miss (47) vs human=reject
- Meteoro Platform | Product Lead | Unknown | headhunter
- url: https://hh.ru/vacancy/133652461
- expert_comment: (see calibration file; expert_comment lines were used for vacancies 2-30)
- Model positives: +20 executive_visibility, +15 B2C_platform, +12 growth_signal
- Rejection telemetry: company_score_unknown, duplicate, insufficient_data, location_unknown, missing_product_ownership, pnl_unknown, salary_unknown
- Root cause (likely):
  - Executive keyword signals are being over-valued without product ownership evidence (role-function gating missing).

### Example #3: FN | model=reject (35) vs human=strong_fit
- Unknown | Head of Product (AI Product) - Full remote | Unknown | linkedin
- url: https://www.linkedin.com/jobs/collections/recommended
- expert_comment: Роль релевантная по title/AI/remote, но company=Unknown и URL на recommendations снижают доверие; нужно восстановить реальную компанию/URL
- Model positives: +20 executive_visibility, +10 AI_or_modern_tech, +5 remote_friendly
- Rejection telemetry: company_score_unknown, duplicate, hiring_likelihood_unknown, insufficient_data, location_unknown, pnl_unknown, salary_unknown
- Root cause (likely):
  - Product leadership thesis roles do not reach review buckets because the score model over-relies on explicit ownership/P&L keywords and has no company trust signal.

### Example #4: FN | model=reject (35) vs human=possible_fit
- Social Discovery Group | Head of Product (AI Product) - Full remote | Remote | linkedin
- url: https://www.linkedin.com/jobs/view/4384021732
- expert_comment: Head of Product + AI + remote релевантно, но Social Discovery Group требует ручной проверки по продукту/репутации/компенсации
- Model positives: +20 executive_visibility, +10 AI_or_modern_tech, +5 remote_friendly
- Rejection telemetry: company_score_unknown, duplicate, hiring_likelihood_unknown, insufficient_data, missing_product_ownership, pnl_unknown, salary_unknown
- Root cause (likely):
  - Product leadership thesis roles do not reach review buckets because the score model over-relies on explicit ownership/P&L keywords and has no company trust signal.

### Example #5: FN | model=reject (32) vs human=possible_fit
- ТОО Ticketon Events | Product Lead (Афиша) | Алматы, проспект Жибек Жолы, 135 | headhunter
- url: https://hh.ru/vacancy/133470638
- expert_comment: B2C marketplace/events в Алматы, Product Lead. Ниже executive-цели, но может быть полезным локальным вариантом или market signal
- Model positives: +20 executive_visibility, +12 growth_signal
- Rejection telemetry: company_score_unknown, duplicate, hiring_likelihood_unknown, insufficient_data, low_confidence, missing_product_ownership, pnl_unknown, salary_unknown
- Root cause (likely):
  - Product leadership thesis roles do not reach review buckets because the score model over-relies on explicit ownership/P&L keywords and has no company trust signal.

### Example #6: FN | model=reject (32) vs human=possible_fit
- ТОО Bilim Land (Bilim Group) | Growth Product Lead | Unknown | headhunter
- url: https://hh.ru/vacancy/132562956
- expert_comment: Growth Product Lead в edtech может быть релевантен, если есть ownership за growth/monetization и масштаб; нужна детализация
- Model positives: +20 executive_visibility, +12 growth_signal
- Rejection telemetry: company_score_unknown, hiring_likelihood_unknown, insufficient_data, location_unknown, low_confidence, pnl_unknown, salary_unknown
- Root cause (likely):
  - Product leadership thesis roles do not reach review buckets because the score model over-relies on explicit ownership/P&L keywords and has no company trust signal.

### Example #7: TN | model=reject (25) vs human=reject
- BrainShells | Head of Sales (B2B, Fintech / Crypto) | Unknown | headhunter
- url: https://hh.ru/vacancy/133668801
- expert_comment: Sales leadership, не product leadership. Fintech/crypto контекст недостаточен
- Model positives: +15 fintech_or_telecom, +10 AI_or_modern_tech
- Rejection telemetry: company_score_unknown, duplicate, hiring_likelihood_unknown, insufficient_data, location_unknown, low_confidence, low_seniority, missing_executive_responsibility, missing_product_ownership, pnl_unknown, salary_unknown
- Root cause (likely):
  - Useful negative control: shows what the model correctly rejects (and why).

### Example #8: TN | model=reject (25) vs human=reject
- ТОО KT&G GLOBAL KAZAKHSTAN (КЕЙ-ТИ-ЭНД-ДЖИ ГЛОБАЛ КАЗАХСТАН) | HNB Consumer Engagment Lead | Unknown | headhunter
- url: https://hh.ru/vacancy/133402913
- expert_comment: Consumer engagement/FMCG, не digital product/platform leadership
- Model positives: +15 B2C_platform, +10 international_team
- Rejection telemetry: company_score_unknown, duplicate, hiring_likelihood_unknown, insufficient_data, location_unknown, low_confidence, low_seniority, missing_executive_responsibility, missing_product_ownership, pnl_unknown, salary_unknown
- Root cause (likely):
  - Useful negative control: shows what the model correctly rejects (and why).

### Example #9: TN | model=reject (20) vs human=reject
- CAMMA Corporate Learning and Development Center | Product Launch Manager (1 Position at Head Office) | Phnom Penh, Phnom Penh, Cambodia (On-site) | linkedin
- url: https://www.linkedin.com/jobs/view/4338017837
- expert_comment: Product Launch Manager не executive product роль; onsite Cambodia не соответствует приоритетам
- Model positives: +20 executive_visibility
- Rejection telemetry: company_score_unknown, hiring_likelihood_unknown, insufficient_data, low_confidence, onsite_requirement_mismatch, pnl_unknown, salary_unknown
- Root cause (likely):
  - Useful negative control: shows what the model correctly rejects (and why).

### Example #10: TN | model=reject (20) vs human=reject
- АО CENTER FOR DIGITAL TECHNOLOGY AND INNOVATION | Head of Product | Ташкент, улица Махтумкули, 79 | headhunter
- url: https://hh.ru/vacancy/133446873
- expert_comment: Head of Product звучит релевантно, но вероятно гос/окологос digital center в Узбекистане; высокий риск бюрократии и слабого product ownership
- Model positives: +20 executive_visibility
- Rejection telemetry: company_score_unknown, duplicate, hiring_likelihood_unknown, insufficient_data, low_confidence, missing_product_ownership, pnl_unknown, salary_unknown
- Root cause (likely):
  - Useful negative control: shows what the model correctly rejects (and why).

### Example #11: TN | model=reject (15) vs human=reject
- Esprow Pte Ltd | Digital Marketing Specialist (Technical Software) / Специалист по цифровому маркетингу (fintech) | Unknown | headhunter
- url: https://hh.ru/vacancy/133665432
- expert_comment: Marketing specialist, не executive/product leadership
- Model positives: +15 fintech_or_telecom
- Rejection telemetry: company_score_unknown, duplicate, hiring_likelihood_unknown, insufficient_data, location_unknown, low_confidence, low_seniority, missing_executive_responsibility, missing_product_ownership, pnl_unknown, salary_unknown
- Root cause (likely):
  - Useful negative control: shows what the model correctly rejects (and why).

## 3. Opportunity Thesis Extraction (updated after cleanup)

### What roles are liked vs rejected (from human labels)
Human-positive (review-worthy) in this set:
- possible_fit: Head of Product - Fintech | Discovered MENA | Remote
- strong_fit: Head of Product (AI Product) - Full remote | Unknown | Unknown
- possible_fit: Head of Product (AI Product) - Full remote | Social Discovery Group | Remote
- possible_fit: Product Lead (Афиша) | ТОО Ticketon Events | Алматы, проспект Жибек Жолы, 135
- possible_fit: Growth Product Lead | ТОО Bilim Land (Bilim Group) | Unknown

Human-reject examples (high score but rejected by human):
- reject: Product Lead | score=47 | Meteoro Platform | Unknown
- reject: Head of Sales (B2B, Fintech / Crypto) | score=25 | BrainShells | Unknown
- reject: HNB Consumer Engagment Lead | score=25 | ТОО KT&G GLOBAL KAZAKHSTAN (КЕЙ-ТИ-ЭНД-ДЖИ ГЛОБАЛ КАЗАХСТАН) | Unknown
- reject: Product Launch Manager (1 Position at Head Office) | score=20 | CAMMA Corporate Learning and Development Center | Phnom Penh, Phnom Penh, Cambodia (On-site)
- reject: Head of Product | score=20 | АО CENTER FOR DIGITAL TECHNOLOGY AND INNOVATION | Ташкент, улица Махтумкули, 79
- reject: Digital Marketing Specialist (Technical Software) / Специалист по цифровому маркетингу (fintech) | score=15 | Esprow Pte Ltd | Unknown
- reject: Business Analyst with Fintech and Banking experience | score=15 | Team.Inno / Фабрика инноваций и решений | Минск
- reject: Key Account Manager (Consumer Products Division) | score=15 | ТОО Лореаль Казахстан | Алматы
- reject: Head of Marketing and Go-to Market department | score=10 | ТОО BSH Home Appliances (БСХ Хоум Аплайансэс) | Алматы, микрорайон Коктем-1, 15А
- reject: AI Product & Operations Lead | score=10 | ИП Иванова Марина Николаевна | Unknown

### Growth thesis refinement (per operator note)
After cleanup, human-positive set contains:
- Growth Product Lead (edtech) as possible_fit (requires product-growth ownership).
Human explicitly rejects/does not reward:
- generic growth manager / performance / analytics growth (treated as reject).

### Signals correlating with human-positive labels (small-sample)
Component -> P(human positive | component present), sample count:
- remote_friendly: 1.00 (n=3)
- growth_signal: 0.67 (n=3)
- executive_visibility: 0.62 (n=8)
- AI_or_modern_tech: 0.50 (n=4)
- fintech_or_telecom: 0.25 (n=4)
- B2C_platform: 0.00 (n=3)
- international_team: 0.00 (n=2)

## 4. Proposed Weights Update (revised; design only)

Key adjustment vs previous draft: `growth_signal` must be **high only for product-growth ownership**, not sales/marketing/BD/analytics growth.

| component | current | proposed | change rationale (calibration) |
| --- | --- | --- | --- |
| executive_visibility | +20 (hardcoded) | +15 and gated by product-domain | Reduce false positives from non-product leadership titles. |
| growth_signal | +12 | +18 only if product-growth ownership; else +0 | Prevent growth inflation for performance/BD; increase for true growth product leadership. |
| product_ownership | +20 | +25 | Human comments heavily emphasize real ownership/scope. |
| monetization_responsibility | +25 | +30 (but require product context) | Monetization is core, but avoid keyword spam outside product. |
| PnL_ownership | +25 | +10 (bonus; not required for review) | P&L is often unknown; do not block review pipeline. |
| B2C_platform | +15 | +10 | Platform keywords are broad; reduce dominance. |
| fintech_or_telecom | +15 | +18 | Fintech/telecom adjacency aligns with thesis. |
| AI_or_modern_tech | +10 | +12 | AI product leadership is valued. |
| new: non_product_function_penalty | n/a | -35 | Hard negative for sales/marketing/finance/BD unless product ownership exists. |
| new: ownership_scope_bonus | n/a | +10..+20 | Bonus for explicit scope: strategy/roadmap/org scaling/budget/GM ownership. |

## 5. Recommendation Buckets (reporting semantics)

Requested report behavior (not code yet):
- `strong_fit`: show as high-priority
- `potential_fit`: show in daily review
- `near_miss`: show only in a separate block when strong/potential are empty or on request
- `reject`: do not show

## 6. Validation Plan (updated)

Before implementation:
- Re-run metrics on cleaned calibration set
- Add a second calibration batch (>=50) so growth gating has more examples
Success criteria target: review-positive recall >= 0.70 while keeping FP <= 1-2 per 30 items.
