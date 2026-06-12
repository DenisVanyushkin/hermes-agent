# Decision Quality Validation

run_id: 199 (production daily)

## 1. Top opportunities
Top 20 vacancies by score:

- headhunter | Meteoro Platform | Product Lead | Unknown | score=47 | bucket=near_miss | blockers=- | unknown=location_unknown, salary_unknown, pnl_unknown | https://hh.ru/vacancy/133652461
- linkedin | Discovered MENA | Head of Product - Fintech | Remote | score=40 | bucket=near_miss | blockers=- | unknown=salary_unknown, pnl_unknown | https://www.linkedin.com/jobs/view/4417070084
- linkedin | Unknown | Head of Product (AI Product) - Full remote | Unknown | score=35 | bucket=reject | blockers=- | unknown=location_unknown, salary_unknown, pnl_unknown | https://www.linkedin.com/jobs/collections/recommended
- linkedin | Social Discovery Group | Head of Product (AI Product) - Full remote | Remote | score=35 | bucket=reject | blockers=- | unknown=salary_unknown, pnl_unknown | https://www.linkedin.com/jobs/view/4384021732
- headhunter | ТОО Ticketon Events | Product Lead (Афиша) | Алматы, проспект Жибек Жолы, 135 | score=32 | bucket=reject | blockers=- | unknown=salary_unknown, pnl_unknown | https://hh.ru/vacancy/133470638
- headhunter | ТОО Bilim Land (Bilim Group) | Growth Product Lead | Unknown | score=32 | bucket=reject | blockers=- | unknown=location_unknown, salary_unknown, pnl_unknown | https://hh.ru/vacancy/132562956
- headhunter | BrainShells | Head of Sales (B2B, Fintech / Crypto) | Unknown | score=25 | bucket=reject | blockers=- | unknown=location_unknown, salary_unknown, pnl_unknown | https://hh.ru/vacancy/133668801
- headhunter | ТОО KT&G GLOBAL KAZAKHSTAN (КЕЙ-ТИ-ЭНД-ДЖИ ГЛОБАЛ КАЗАХСТАН) | HNB Consumer Engagment Lead | Unknown | score=25 | bucket=reject | blockers=- | unknown=location_unknown, salary_unknown, pnl_unknown | https://hh.ru/vacancy/133402913
- linkedin | CAMMA Corporate Learning and Development Center | Product Launch Manager (1 Position at Head Office) | Phnom Penh, Phnom Penh, Cambodia (On-site) | score=20 | bucket=reject | blockers=- | unknown=salary_unknown, pnl_unknown | https://www.linkedin.com/jobs/view/4338017837
- headhunter | АО CENTER FOR DIGITAL TECHNOLOGY AND INNOVATION | Head of Product | Ташкент, улица Махтумкули, 79 | score=20 | bucket=reject | blockers=- | unknown=salary_unknown, pnl_unknown | https://hh.ru/vacancy/133446873
- headhunter | Esprow Pte Ltd | Digital Marketing Specialist (Technical Software) / Специалист по цифровому маркетингу (fintech) | Unknown | score=15 | bucket=reject | blockers=- | unknown=location_unknown, salary_unknown, pnl_unknown | https://hh.ru/vacancy/133665432
- headhunter | Team.Inno / Фабрика инноваций и решений | Business Analyst with Fintech and Banking experience | Минск | score=15 | bucket=reject | blockers=- | unknown=salary_unknown, pnl_unknown | https://hh.ru/vacancy/125440730
- headhunter | ТОО Лореаль Казахстан | Key Account Manager (Consumer Products Division) | Алматы | score=15 | bucket=reject | blockers=- | unknown=salary_unknown, pnl_unknown | https://hh.ru/vacancy/132989208
- headhunter | ТОО BSH Home Appliances (БСХ Хоум Аплайансэс) | Head of Marketing and Go-to Market department | Алматы, микрорайон Коктем-1, 15А | score=10 | bucket=reject | blockers=- | unknown=salary_unknown, pnl_unknown | https://hh.ru/vacancy/132668007
- headhunter | ИП Иванова Марина Николаевна | AI Product & Operations Lead | Unknown | score=10 | bucket=reject | blockers=- | unknown=location_unknown, salary_unknown, pnl_unknown | https://hh.ru/vacancy/133486511
- headhunter | ООО Технологический парк программных продуктов и информационных технологий | International Expansion Lead (IT & Product Companies) | Ташкент, улица Муминова, 7/2 | score=10 | bucket=reject | blockers=- | unknown=salary_unknown, pnl_unknown | https://hh.ru/vacancy/133043996
- linkedin | CAMMA Corporate Learning and Development Center | Unit Manager, Digital Banking for Product Management - 01 post. | Phnom Penh, Phnom Penh, Cambodia (On-site) | score=0 | bucket=reject | blockers=- | unknown=salary_unknown, pnl_unknown | https://www.linkedin.com/jobs/view/4316220156
- linkedin | MUK | Deputy Head of Cloud Products and Services at MUK Cloud | Kazakhstan (On-site) | score=0 | bucket=reject | blockers=- | unknown=salary_unknown, pnl_unknown | https://www.linkedin.com/jobs/view/4401491114
- headhunter | Magnetto.com | Chief Business Development Officer | Unknown | score=0 | bucket=reject | blockers=- | unknown=location_unknown, salary_unknown, pnl_unknown | https://hh.ru/vacancy/132653639
- headhunter | ТОО Freedom Media | Senior Product Analyst | Алматы, улица Сергея Луганского, 96 | score=0 | bucket=reject | blockers=- | unknown=salary_unknown, pnl_unknown | https://hh.ru/vacancy/132390785

## 2. Near miss review
near_miss count: 2

### Meteoro Platform — Product Lead (headhunter)
- score: 47 (threshold potential_fit=60)
- bucket: near_miss
- executive: True | classification: platform_ecosystem_product_lead | fired_rules: PATTERN_MATCH:platform_ecosystem_product_lead
- blockers: -
- unknown_fields: location_unknown, salary_unknown, pnl_unknown
- why not potential_fit: score<60
- score_breakdown: +20 executive_visibility, +15 B2C_platform, +12 growth_signal
- url: https://hh.ru/vacancy/133652461

### Discovered MENA — Head of Product - Fintech (linkedin)
- score: 40 (threshold potential_fit=60)
- bucket: near_miss
- executive: True | classification: head_product | fired_rules: PATTERN_MATCH:head_product
- blockers: -
- unknown_fields: salary_unknown, pnl_unknown
- why not potential_fit: score<60
- score_breakdown: +20 executive_visibility, +15 fintech_or_telecom, +5 remote_friendly
- url: https://www.linkedin.com/jobs/view/4417070084

## 3. Rejected review
Top 20 rejected (highest score):

- score=35 | linkedin | Unknown | Head of Product - Fintech Head of Product - Fintech with verification | Unknown | exec=True class=head_product rules=PATTERN_MATCH:head_product | rejection_reasons=company_score_unknown, duplicate, hiring_likelihood_unknown, insufficient_data, location_unknown, pnl_unknown, salary_unknown | https://www.linkedin.com/jobs/view/4417070084
- score=35 | linkedin | Unknown | Head of Product (AI Product) - Full remote | Unknown | exec=True class=head_product rules=PATTERN_MATCH:head_product | rejection_reasons=company_score_unknown, duplicate, hiring_likelihood_unknown, insufficient_data, location_unknown, pnl_unknown, salary_unknown | https://www.linkedin.com/jobs/collections/recommended
- score=35 | linkedin | Social Discovery Group | Head of Product (AI Product) - Full remote | Remote | exec=True class=head_product rules=PATTERN_MATCH:head_product | rejection_reasons=company_score_unknown, duplicate, hiring_likelihood_unknown, insufficient_data, missing_product_ownership, pnl_unknown, salary_unknown | https://www.linkedin.com/jobs/view/4384021732
- score=32 | headhunter | ТОО Ticketon Events | Product Lead (Афиша) | Алматы, проспект Жибек Жолы, 135 | exec=True class=product_lead rules=PATTERN_MATCH:product_lead | rejection_reasons=company_score_unknown, duplicate, hiring_likelihood_unknown, insufficient_data, low_confidence, missing_product_ownership, pnl_unknown, salary_unknown | https://hh.ru/vacancy/133470638
- score=32 | headhunter | ТОО Bilim Land (Bilim Group) | Growth Product Lead | Unknown | exec=True class=growth_product_lead rules=PATTERN_MATCH:growth_product_lead | rejection_reasons=company_score_unknown, hiring_likelihood_unknown, insufficient_data, location_unknown, low_confidence, pnl_unknown, salary_unknown | https://hh.ru/vacancy/132562956
- score=25 | headhunter | BrainShells | Head of Sales (B2B, Fintech / Crypto) | Unknown | exec=False class=other rules=- | rejection_reasons=company_score_unknown, duplicate, hiring_likelihood_unknown, insufficient_data, location_unknown, low_confidence, low_seniority, missing_executive_responsibility, missing_product_ownership, pnl_unknown, salary_unknown | https://hh.ru/vacancy/133668801
- score=25 | headhunter | ТОО KT&G GLOBAL KAZAKHSTAN (КЕЙ-ТИ-ЭНД-ДЖИ ГЛОБАЛ КАЗАХСТАН) | HNB Consumer Engagment Lead | Unknown | exec=False class=other rules=- | rejection_reasons=company_score_unknown, duplicate, hiring_likelihood_unknown, insufficient_data, location_unknown, low_confidence, low_seniority, missing_executive_responsibility, missing_product_ownership, pnl_unknown, salary_unknown | https://hh.ru/vacancy/133402913
- score=20 | linkedin | CAMMA Corporate Learning and Development Center | Product Launch Manager (1 Position at Head Office) | Phnom Penh, Phnom Penh, Cambodia (On-site) | exec=True class=head_product rules=PATTERN_MATCH:head_product | rejection_reasons=company_score_unknown, hiring_likelihood_unknown, insufficient_data, low_confidence, onsite_requirement_mismatch, pnl_unknown, salary_unknown | https://www.linkedin.com/jobs/view/4338017837
- score=20 | headhunter | АО CENTER FOR DIGITAL TECHNOLOGY AND INNOVATION | Head of Product | Ташкент, улица Махтумкули, 79 | exec=True class=head_product rules=PATTERN_MATCH:head_product | rejection_reasons=company_score_unknown, duplicate, hiring_likelihood_unknown, insufficient_data, low_confidence, missing_product_ownership, pnl_unknown, salary_unknown | https://hh.ru/vacancy/133446873
- score=15 | headhunter | Esprow Pte Ltd | Digital Marketing Specialist (Technical Software) / Специалист по цифровому маркетингу (fintech) | Unknown | exec=False class=other rules=- | rejection_reasons=company_score_unknown, duplicate, hiring_likelihood_unknown, insufficient_data, location_unknown, low_confidence, low_seniority, missing_executive_responsibility, missing_product_ownership, pnl_unknown, salary_unknown | https://hh.ru/vacancy/133665432
- score=15 | headhunter | Team.Inno / Фабрика инноваций и решений | Business Analyst with Fintech and Banking experience | Минск | exec=False class=other rules=- | rejection_reasons=company_score_unknown, hiring_likelihood_unknown, insufficient_data, low_confidence, low_seniority, missing_executive_responsibility, missing_product_ownership, pnl_unknown, salary_unknown | https://hh.ru/vacancy/125440730
- score=15 | headhunter | ТОО Лореаль Казахстан | Key Account Manager (Consumer Products Division) | Алматы | exec=False class=other rules=- | rejection_reasons=company_score_unknown, duplicate, hiring_likelihood_unknown, insufficient_data, low_confidence, low_seniority, missing_executive_responsibility, missing_product_ownership, pnl_unknown, salary_unknown | https://hh.ru/vacancy/132989208
- score=10 | headhunter | ТОО BSH Home Appliances (БСХ Хоум Аплайансэс) | Head of Marketing and Go-to Market department | Алматы, микрорайон Коктем-1, 15А | exec=False class=other rules=- | rejection_reasons=company_score_unknown, duplicate, hiring_likelihood_unknown, insufficient_data, low_confidence, low_seniority, missing_executive_responsibility, pnl_unknown, salary_unknown | https://hh.ru/vacancy/132668007
- score=10 | headhunter | ИП Иванова Марина Николаевна | AI Product & Operations Lead | Unknown | exec=False class=other rules=- | rejection_reasons=company_score_unknown, hiring_likelihood_unknown, insufficient_data, location_unknown, low_confidence, low_seniority, missing_executive_responsibility, pnl_unknown, salary_unknown | https://hh.ru/vacancy/133486511
- score=10 | headhunter | ООО Технологический парк программных продуктов и информационных технологий | International Expansion Lead (IT & Product Companies) | Ташкент, улица Муминова, 7/2 | exec=False class=other rules=- | rejection_reasons=company_score_unknown, hiring_likelihood_unknown, insufficient_data, low_confidence, low_seniority, missing_executive_responsibility, missing_product_ownership, pnl_unknown, salary_unknown | https://hh.ru/vacancy/133043996
- score=0 | linkedin | CAMMA Corporate Learning and Development Center | Unit Manager, Digital Banking for Product Management - 01 post. | Phnom Penh, Phnom Penh, Cambodia (On-site) | exec=False class=other rules=- | rejection_reasons=company_score_unknown, hiring_likelihood_unknown, insufficient_data, low_confidence, low_seniority, missing_executive_responsibility, onsite_requirement_mismatch, pnl_unknown, salary_unknown | https://www.linkedin.com/jobs/view/4316220156
- score=0 | linkedin | MUK | Deputy Head of Cloud Products and Services at MUK Cloud | Kazakhstan (On-site) | exec=False class=other rules=- | rejection_reasons=company_score_unknown, duplicate, hiring_likelihood_unknown, insufficient_data, low_confidence, low_seniority, missing_executive_responsibility, missing_product_ownership, onsite_requirement_mismatch, pnl_unknown, salary_unknown | https://www.linkedin.com/jobs/view/4401491114
- score=0 | headhunter | Magnetto.com | Chief Business Development Officer | Unknown | exec=False class=other rules=- | rejection_reasons=company_score_unknown, duplicate, hiring_likelihood_unknown, insufficient_data, location_unknown, low_confidence, low_seniority, missing_executive_responsibility, missing_product_ownership, pnl_unknown, salary_unknown | https://hh.ru/vacancy/132653639
- score=0 | headhunter | ТОО Freedom Media | Senior Product Analyst | Алматы, улица Сергея Луганского, 96 | exec=False class=other rules=- | rejection_reasons=company_score_unknown, duplicate, hiring_likelihood_unknown, insufficient_data, low_confidence, low_seniority, missing_executive_responsibility, missing_product_ownership, pnl_unknown, salary_unknown | https://hh.ru/vacancy/132390785
- score=0 | headhunter | Банк | CMO (Chief Marketing Officer) | Алматы | exec=False class=other rules=- | rejection_reasons=company_score_unknown, duplicate, hiring_likelihood_unknown, insufficient_data, low_confidence, low_seniority, missing_executive_responsibility, pnl_unknown, salary_unknown | https://hh.ru/vacancy/133504798

## 4. Executive classifier audit
Sample size: 100 vacancies (de-duped by url), sorted by score desc from this run.

| title | executive | classification | fired_rules |
| --- | --- | --- | --- |
| Product Lead | true | platform_ecosystem_product_lead | PATTERN_MATCH:platform_ecosystem_product_lead |
| Head of Product - Fintech | true | head_product | PATTERN_MATCH:head_product |
| Head of Product (AI Product) - Full remote | true | head_product | PATTERN_MATCH:head_product |
| Head of Product (AI Product) - Full remote | true | head_product | PATTERN_MATCH:head_product |
| Product Lead (Афиша) | true | product_lead | PATTERN_MATCH:product_lead |
| Growth Product Lead | true | growth_product_lead | PATTERN_MATCH:growth_product_lead |
| Head of Sales (B2B, Fintech / Crypto) | false | other | - |
| HNB Consumer Engagment Lead | false | other | - |
| Product Launch Manager (1 Position at Head Office) | true | head_product | PATTERN_MATCH:head_product |
| Head of Product | true | head_product | PATTERN_MATCH:head_product |
| Digital Marketing Specialist (Technical Software) / Специалист по цифровому маркетингу (fintech) | false | other | - |
| Business Analyst with Fintech and Banking experience | false | other | - |
| Key Account Manager (Consumer Products Division) | false | other | - |
| Head of Marketing and Go-to Market department | false | other | - |
| AI Product & Operations Lead | false | other | - |
| International Expansion Lead (IT & Product Companies) | false | other | - |
| Unit Manager, Digital Banking for Product Management - 01 post. | false | other | - |
| Deputy Head of Cloud Products and Services at MUK Cloud | false | other | - |
| Chief Business Development Officer | false | other | - |
| Senior Product Analyst | false | other | - |
| CMO (Chief Marketing Officer) | false | other | - |
| Head of Cargo Marketing | false | other | - |
| Head of Marketing / Ведущий маркетолог | false | other | - |
| Продуктовый маркетолог / Product Marketing Manager | false | other | - |
| Business Growth Lead / CVM & Partnerships Lead | false | other | - |
| Senior Growth Manager (Performance & Analytics) | false | other | - |
| Head of Digital / Digital Marketing Manager | false | other | - |
| Product Marketing Lead | false | other | - |
| Production Team Lead (Руководитель Производства) | false | other | - |
| Product Marketing Manager (ВНЕ РФ И РБ) | false | other | - |
| Data Analytics Product Owner | false | other | - |
| Product Owner CVM | false | other | - |
| Product Owner «Счета и остатки» | false | other | - |

False positive checks (should be 0 after hardening):
- false_positive_count_in_sample: 0

True positive examples (non-exhaustive):
- Head of Product - Fintech
- Head of Product (AI Product) - Full remote
- Head of Product (AI Product) - Full remote
- Head of Product

## 5. Score distribution (last 10 production daily ok runs)
runs: 199, 188, 187, 186, 177, 156, 154, 139, 136, 135
total evaluations: 259
- count score >= 80: 0
- count score >= 70: 0
- count score >= 60: 0
- count score >= 50: 0
- count score >= 40: 22
