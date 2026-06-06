# Scoring Model Redesign Audit (2026-05-30)

## Inputs
- sqlite: `/var/lib/job-intel/state/job_intel.sqlite3`
- latest meaningful production daily runs used: `177, 156, 154, 139`
- latest run for Top-50 rejected analysis: `177`
- current acceptance threshold: `60` (possible_fit)

# Part 1) Score Decomposition (Top 50 highest-scoring rejected)

Breakdown uses `vacancy_evaluations.raw_breakdown_json` (true score components). Rejection reasons come from `vacancy_rejection_events` (heuristics; not always score components).

## 1. Product Lead | Meteoro Platform | headhunter | score=47
- location: Unknown
- url: https://hh.ru/vacancy/133652461
- threshold: 60
- rejection_reasons: blocked_geography, insufficient_data, low_company_score, missing_p_and_l, missing_product_ownership, salary_too_low

Positive contributions:
- +20 executive_visibility
- +15 B2C_platform
- +12 growth_signal
Negative contributions: -

## 2. Head of Product - Fintech | Discovered MENA | linkedin | score=40
- location: Remote
- url: https://www.linkedin.com/jobs/view/4417070084
- threshold: 60
- rejection_reasons: blocked_geography, insufficient_data, low_company_score, low_hiring_likelihood, missing_p_and_l, missing_product_ownership, salary_too_low, wrong_geography

Positive contributions:
- +20 executive_visibility
- +15 fintech_or_telecom
- +5 remote_friendly
Negative contributions: -

## 3. Head of Product - Fintech | Discovered MENA | linkedin | score=40
- location: Remote
- url: https://www.linkedin.com/jobs/view/4417070084
- threshold: 60
- rejection_reasons: blocked_geography, insufficient_data, low_company_score, low_hiring_likelihood, missing_p_and_l, missing_product_ownership, salary_too_low, wrong_geography

Positive contributions:
- +20 executive_visibility
- +15 fintech_or_telecom
- +5 remote_friendly
Negative contributions: -

## 4. Head of Product (AI Product) - Full remote | Social Discovery Group | linkedin | score=35
- location: Remote
- url: https://www.linkedin.com/jobs/view/4384021732
- threshold: 60
- rejection_reasons: blocked_geography, insufficient_data, low_company_score, low_hiring_likelihood, missing_p_and_l, salary_too_low, wrong_geography

Positive contributions:
- +20 executive_visibility
- +10 AI_or_modern_tech
- +5 remote_friendly
Negative contributions: -

## 5. Head of Product (AI Product) - Full remote Head of Product (AI Product) - Full remote with verification | Unknown | linkedin | score=35
- location: Unknown
- url: https://www.linkedin.com/jobs/view/4384021732
- threshold: 60
- rejection_reasons: blocked_geography, insufficient_data, low_company_score, low_hiring_likelihood, missing_p_and_l, salary_too_low, wrong_geography

Positive contributions:
- +20 executive_visibility
- +10 AI_or_modern_tech
- +5 remote_friendly
Negative contributions: -

## 6. Head of Product (AI Product) - Full remote | Social Discovery Group | linkedin | score=35
- location: Remote
- url: https://www.linkedin.com/jobs/view/4384021732
- threshold: 60
- rejection_reasons: blocked_geography, insufficient_data, low_company_score, low_hiring_likelihood, missing_p_and_l, salary_too_low, wrong_geography

Positive contributions:
- +20 executive_visibility
- +10 AI_or_modern_tech
- +5 remote_friendly
Negative contributions: -

## 7. Head of Product (AI Product) - Full remote Head of Product (AI Product) - Full remote with verification | Unknown | linkedin | score=35
- location: Unknown
- url: https://www.linkedin.com/jobs/view/4384021732
- threshold: 60
- rejection_reasons: blocked_geography, insufficient_data, low_company_score, low_hiring_likelihood, missing_p_and_l, salary_too_low, wrong_geography

Positive contributions:
- +20 executive_visibility
- +10 AI_or_modern_tech
- +5 remote_friendly
Negative contributions: -

## 8. Head of Product (AI Product) - Full remote | Social Discovery Group | linkedin | score=35
- location: Remote
- url: https://www.linkedin.com/jobs/view/4384021732
- threshold: 60
- rejection_reasons: blocked_geography, insufficient_data, low_company_score, low_hiring_likelihood, missing_p_and_l, salary_too_low, wrong_geography

Positive contributions:
- +20 executive_visibility
- +10 AI_or_modern_tech
- +5 remote_friendly
Negative contributions: -

## 9. Head of Product (AI Product) - Full remote Head of Product (AI Product) - Full remote with verification | Unknown | linkedin | score=35
- location: Unknown
- url: https://www.linkedin.com/jobs/view/4384021732
- threshold: 60
- rejection_reasons: blocked_geography, insufficient_data, low_company_score, low_hiring_likelihood, missing_p_and_l, salary_too_low, wrong_geography

Positive contributions:
- +20 executive_visibility
- +10 AI_or_modern_tech
- +5 remote_friendly
Negative contributions: -

## 10. Head of Product - Fintech Head of Product - Fintech with verification | Unknown | linkedin | score=35
- location: Unknown
- url: https://www.linkedin.com/jobs/view/4417070084
- threshold: 60
- rejection_reasons: blocked_geography, insufficient_data, low_company_score, low_hiring_likelihood, missing_p_and_l, missing_product_ownership, salary_too_low, wrong_geography

Positive contributions:
- +20 executive_visibility
- +15 fintech_or_telecom
Negative contributions: -

## 11. Head of Product - Fintech Head of Product - Fintech with verification | Unknown | linkedin | score=35
- location: Unknown
- url: https://www.linkedin.com/jobs/view/4417070084
- threshold: 60
- rejection_reasons: blocked_geography, insufficient_data, low_company_score, low_hiring_likelihood, missing_p_and_l, missing_product_ownership, salary_too_low, wrong_geography

Positive contributions:
- +20 executive_visibility
- +15 fintech_or_telecom
Negative contributions: -

## 12. Head of Product - Fintech | Unknown | linkedin | score=35
- location: Unknown
- url: https://www.linkedin.com/jobs/view/4417070084
- threshold: 60
- rejection_reasons: blocked_geography, insufficient_data, low_company_score, low_hiring_likelihood, missing_p_and_l, missing_product_ownership, salary_too_low, wrong_geography

Positive contributions:
- +20 executive_visibility
- +15 fintech_or_telecom
Negative contributions: -

## 13. Head of Product - Fintech | Unknown | linkedin | score=35
- location: Unknown
- url: https://www.linkedin.com/jobs/view/4417070084
- threshold: 60
- rejection_reasons: blocked_geography, insufficient_data, low_company_score, low_hiring_likelihood, missing_p_and_l, missing_product_ownership, salary_too_low, wrong_geography

Positive contributions:
- +20 executive_visibility
- +15 fintech_or_telecom
Negative contributions: -

## 14. Head of Product (AI Product) - Full remote | Social Discovery Group | linkedin | score=35
- location: Remote
- url: https://www.linkedin.com/jobs/view/4384021732
- threshold: 60
- rejection_reasons: blocked_geography, insufficient_data, low_company_score, low_hiring_likelihood, missing_p_and_l, salary_too_low, wrong_geography

Positive contributions:
- +20 executive_visibility
- +10 AI_or_modern_tech
- +5 remote_friendly
Negative contributions: -

## 15. Head of Product (AI Product) - Full remote Head of Product (AI Product) - Full remote with verification | Unknown | linkedin | score=35
- location: Unknown
- url: https://www.linkedin.com/jobs/view/4384021732
- threshold: 60
- rejection_reasons: blocked_geography, insufficient_data, low_company_score, low_hiring_likelihood, missing_p_and_l, salary_too_low, wrong_geography

Positive contributions:
- +20 executive_visibility
- +10 AI_or_modern_tech
- +5 remote_friendly
Negative contributions: -

## 16. Product Lead (Афиша) | ТОО Ticketon Events | headhunter | score=32
- location: Алматы, проспект Жибек Жолы, 135
- url: https://hh.ru/vacancy/133470638
- threshold: 60
- rejection_reasons: blocked_geography, insufficient_data, low_company_score, low_confidence, low_hiring_likelihood, missing_p_and_l, missing_product_ownership, salary_too_low

Positive contributions:
- +20 executive_visibility
- +12 growth_signal
Negative contributions: -

## 17. Founding CPO → CEO для AI-стартапа в США | Aspirine HR | headhunter | score=30
- location: Unknown
- url: https://hh.ru/vacancy/133184144
- threshold: 60
- rejection_reasons: blocked_geography, insufficient_data, low_company_score, low_confidence, low_hiring_likelihood, missing_p_and_l, missing_product_ownership, salary_too_low

Positive contributions:
- +20 executive_visibility
- +10 AI_or_modern_tech
Negative contributions: -

## 18. Platform Architect/Архитектор платформы | YADRO | headhunter | score=27
- location: Unknown
- url: https://hh.ru/vacancy/132307461
- threshold: 60
- rejection_reasons: blocked_geography, insufficient_data, low_company_score, low_confidence, low_hiring_likelihood, low_seniority, missing_executive_responsibility, missing_p_and_l, missing_product_ownership, salary_too_low

Positive contributions:
- +15 B2C_platform
- +12 growth_signal
Negative contributions: -

## 19. Deputy Head of Cloud Products and Services at MUK Cloud | MUK | linkedin | score=20
- location: Kazakhstan (On-site)
- url: https://www.linkedin.com/jobs/view/4401491114
- threshold: 60
- rejection_reasons: blocked_geography, insufficient_data, low_company_score, low_confidence, low_hiring_likelihood, missing_p_and_l, onsite_requirement_mismatch, salary_too_low, wrong_geography

Positive contributions:
- +20 executive_visibility
Negative contributions: -

## 20. Deputy Head of Cloud Products and Services at MUK CloudDeputy Head of Cloud Products and Services at MUK Cloud | Unknown | linkedin | score=20
- location: Unknown
- url: https://www.linkedin.com/jobs/view/4401491114
- threshold: 60
- rejection_reasons: blocked_geography, insufficient_data, low_company_score, low_confidence, low_hiring_likelihood, missing_p_and_l, salary_too_low, wrong_geography

Positive contributions:
- +20 executive_visibility
Negative contributions: -

## 21. Deputy Head of Cloud Products and Services at MUK Cloud | MUK | linkedin | score=20
- location: Kazakhstan (On-site)
- url: https://www.linkedin.com/jobs/view/4401491114
- threshold: 60
- rejection_reasons: blocked_geography, insufficient_data, low_company_score, low_confidence, low_hiring_likelihood, missing_p_and_l, onsite_requirement_mismatch, salary_too_low, wrong_geography

Positive contributions:
- +20 executive_visibility
Negative contributions: -

## 22. Deputy Head of Cloud Products and Services at MUK CloudDeputy Head of Cloud Products and Services at MUK Cloud | Unknown | linkedin | score=20
- location: Unknown
- url: https://www.linkedin.com/jobs/view/4401491114
- threshold: 60
- rejection_reasons: blocked_geography, insufficient_data, low_company_score, low_confidence, low_hiring_likelihood, missing_p_and_l, salary_too_low, wrong_geography

Positive contributions:
- +20 executive_visibility
Negative contributions: -

## 23. Deputy Head of Cloud Products and Services at MUK Cloud | MUK | linkedin | score=20
- location: Kazakhstan (On-site)
- url: https://www.linkedin.com/jobs/view/4401491114
- threshold: 60
- rejection_reasons: blocked_geography, insufficient_data, low_company_score, low_confidence, low_hiring_likelihood, missing_p_and_l, onsite_requirement_mismatch, salary_too_low, wrong_geography

Positive contributions:
- +20 executive_visibility
Negative contributions: -

## 24. Deputy Head of Cloud Products and Services at MUK CloudDeputy Head of Cloud Products and Services at MUK Cloud | Unknown | linkedin | score=20
- location: Unknown
- url: https://www.linkedin.com/jobs/view/4401491114
- threshold: 60
- rejection_reasons: blocked_geography, insufficient_data, low_company_score, low_confidence, low_hiring_likelihood, missing_p_and_l, salary_too_low, wrong_geography

Positive contributions:
- +20 executive_visibility
Negative contributions: -

## 25. Product Launch Manager (1 Position at Head Office) | CAMMA Corporate Learning and Development Center | linkedin | score=20
- location: Phnom Penh, Phnom Penh, Cambodia (On-site)
- url: https://www.linkedin.com/jobs/view/4338017837/
- threshold: 60
- rejection_reasons: blocked_geography, insufficient_data, low_company_score, low_confidence, low_hiring_likelihood, missing_p_and_l, onsite_requirement_mismatch, salary_too_low

Positive contributions:
- +20 executive_visibility
Negative contributions: -

## 26. Product Launch Manager (1 Position at Head Office)Product Launch Manager (1 Position at Head Office) | Unknown | linkedin | score=20
- location: Unknown
- url: https://www.linkedin.com/jobs/view/4338017837/
- threshold: 60
- rejection_reasons: blocked_geography, insufficient_data, low_company_score, low_confidence, low_hiring_likelihood, missing_p_and_l, salary_too_low

Positive contributions:
- +20 executive_visibility
Negative contributions: -

## 27. Deputy Head of Cloud Products and Services at MUK Cloud | MUK | linkedin | score=20
- location: Kazakhstan (On-site)
- url: https://www.linkedin.com/jobs/view/4401491114
- threshold: 60
- rejection_reasons: blocked_geography, insufficient_data, low_company_score, low_confidence, low_hiring_likelihood, missing_p_and_l, onsite_requirement_mismatch, salary_too_low, wrong_geography

Positive contributions:
- +20 executive_visibility
Negative contributions: -

## 28. Deputy Head of Cloud Products and Services at MUK CloudDeputy Head of Cloud Products and Services at MUK Cloud | Unknown | linkedin | score=20
- location: Unknown
- url: https://www.linkedin.com/jobs/view/4401491114
- threshold: 60
- rejection_reasons: blocked_geography, insufficient_data, low_company_score, low_confidence, low_hiring_likelihood, missing_p_and_l, salary_too_low, wrong_geography

Positive contributions:
- +20 executive_visibility
Negative contributions: -

## 29. Chief Business Development Officer | Magnetto.com | headhunter | score=20
- location: Unknown
- url: https://hh.ru/vacancy/132653639
- threshold: 60
- rejection_reasons: blocked_geography, insufficient_data, low_company_score, low_confidence, low_hiring_likelihood, missing_p_and_l, missing_product_ownership, salary_too_low, wrong_geography

Positive contributions:
- +20 executive_visibility
Negative contributions: -

## 30. Chief Business Development Officer | Magnetto.com | headhunter | score=20
- location: Unknown
- url: https://hh.ru/vacancy/132653639
- threshold: 60
- rejection_reasons: blocked_geography, insufficient_data, low_company_score, low_confidence, low_hiring_likelihood, missing_p_and_l, missing_product_ownership, salary_too_low, wrong_geography

Positive contributions:
- +20 executive_visibility
Negative contributions: -

## 31. Team Lead / Head of Product Marketing (Africa | iGaming) | Headshot | headhunter | score=20
- location: Unknown
- url: https://hh.ru/vacancy/133107602
- threshold: 60
- rejection_reasons: blocked_geography, insufficient_data, low_company_score, low_confidence, low_hiring_likelihood, missing_p_and_l, missing_product_ownership, salary_too_low

Positive contributions:
- +20 executive_visibility
Negative contributions: -

## 32. Head of Data | ТОО Alem Agro Holding (АлемАгро Холдинг) | headhunter | score=20
- location: Алматы
- url: https://hh.ru/vacancy/133143236
- threshold: 60
- rejection_reasons: blocked_geography, insufficient_data, low_company_score, low_confidence, low_hiring_likelihood, missing_p_and_l, missing_product_ownership, salary_too_low

Positive contributions:
- +20 executive_visibility
Negative contributions: -

## 33. Head of Product | АО CENTER FOR DIGITAL TECHNOLOGY AND INNOVATION | headhunter | score=20
- location: Ташкент, улица Махтумкули, 79
- url: https://hh.ru/vacancy/133446873
- threshold: 60
- rejection_reasons: blocked_geography, insufficient_data, low_company_score, low_confidence, low_hiring_likelihood, missing_p_and_l, missing_product_ownership, salary_too_low

Positive contributions:
- +20 executive_visibility
Negative contributions: -

## 34. CMO (Chief Marketing Officer) | Банк | headhunter | score=20
- location: Алматы
- url: https://hh.ru/vacancy/133504798
- threshold: 60
- rejection_reasons: blocked_geography, insufficient_data, low_company_score, low_confidence, low_hiring_likelihood, missing_p_and_l, missing_product_ownership, salary_too_low

Positive contributions:
- +20 executive_visibility
Negative contributions: -

## 35. Chief Business Development Officer | Magnetto.com | headhunter | score=20
- location: Unknown
- url: https://hh.ru/vacancy/132653639
- threshold: 60
- rejection_reasons: blocked_geography, insufficient_data, low_company_score, low_confidence, low_hiring_likelihood, missing_p_and_l, missing_product_ownership, salary_too_low, wrong_geography

Positive contributions:
- +20 executive_visibility
Negative contributions: -

## 36. Head of Cargo Marketing | Международный Аэропорт Алматы, АО | headhunter | score=20
- location: Алматы, аэропорт Алматы
- url: https://hh.ru/vacancy/132525450
- threshold: 60
- rejection_reasons: blocked_geography, insufficient_data, low_company_score, low_confidence, low_hiring_likelihood, missing_p_and_l, missing_product_ownership, salary_too_low

Positive contributions:
- +20 executive_visibility
Negative contributions: -

## 37. Head of Data and Analytics / Руководитель направления Данных и Аналитики | Novakid Inc | headhunter | score=20
- location: Unknown
- url: https://hh.ru/vacancy/133223960
- threshold: 60
- rejection_reasons: blocked_geography, insufficient_data, low_company_score, low_confidence, low_hiring_likelihood, missing_p_and_l, missing_product_ownership, salary_too_low

Positive contributions:
- +20 executive_visibility
Negative contributions: -

## 38. Chief Business Development Officer | Magnetto.com | headhunter | score=20
- location: Unknown
- url: https://hh.ru/vacancy/132653639
- threshold: 60
- rejection_reasons: blocked_geography, insufficient_data, low_company_score, low_confidence, low_hiring_likelihood, missing_p_and_l, missing_product_ownership, salary_too_low, wrong_geography

Positive contributions:
- +20 executive_visibility
Negative contributions: -

## 39. Business Analyst with Fintech and Banking experience | Team.Inno / Фабрика инноваций и решений | headhunter | score=15
- location: Минск
- url: https://hh.ru/vacancy/125440730
- threshold: 60
- rejection_reasons: blocked_geography, insufficient_data, low_company_score, low_confidence, low_hiring_likelihood, low_seniority, missing_executive_responsibility, missing_p_and_l, missing_product_ownership, salary_too_low, wrong_geography

Positive contributions:
- +15 fintech_or_telecom
Negative contributions: -

## 40. Digital Marketing Specialist (Technical Software) / Специалист по цифровому маркетингу (fintech) | Esprow Pte Ltd | headhunter | score=15
- location: Unknown
- url: https://hh.ru/vacancy/133665432
- threshold: 60
- rejection_reasons: blocked_geography, insufficient_data, low_company_score, low_confidence, low_hiring_likelihood, low_seniority, missing_executive_responsibility, missing_p_and_l, missing_product_ownership, salary_too_low

Positive contributions:
- +15 fintech_or_telecom
Negative contributions: -

## 41. Business Analyst with Fintech and Banking experience | Team.Inno / Фабрика инноваций и решений | headhunter | score=15
- location: Минск
- url: https://hh.ru/vacancy/125440730
- threshold: 60
- rejection_reasons: blocked_geography, insufficient_data, low_company_score, low_confidence, low_hiring_likelihood, low_seniority, missing_executive_responsibility, missing_p_and_l, missing_product_ownership, salary_too_low, wrong_geography

Positive contributions:
- +15 fintech_or_telecom
Negative contributions: -

## 42. Business Analyst with Fintech and Banking experience | Team.Inno / Фабрика инноваций и решений | headhunter | score=15
- location: Минск
- url: https://hh.ru/vacancy/125440730
- threshold: 60
- rejection_reasons: blocked_geography, insufficient_data, low_company_score, low_confidence, low_hiring_likelihood, low_seniority, missing_executive_responsibility, missing_p_and_l, missing_product_ownership, salary_too_low, wrong_geography

Positive contributions:
- +15 fintech_or_telecom
Negative contributions: -

## 43. Digital Marketing Specialist (Technical Software) / Специалист по цифровому маркетингу (fintech) | Esprow Pte Ltd | headhunter | score=15
- location: Unknown
- url: https://hh.ru/vacancy/133665432
- threshold: 60
- rejection_reasons: blocked_geography, insufficient_data, low_company_score, low_confidence, low_hiring_likelihood, low_seniority, missing_executive_responsibility, missing_p_and_l, missing_product_ownership, salary_too_low

Positive contributions:
- +15 fintech_or_telecom
Negative contributions: -

## 44. Business Analyst with Fintech and Banking experience | Team.Inno / Фабрика инноваций и решений | headhunter | score=15
- location: Минск
- url: https://hh.ru/vacancy/125440730
- threshold: 60
- rejection_reasons: blocked_geography, insufficient_data, low_company_score, low_confidence, low_hiring_likelihood, low_seniority, missing_executive_responsibility, missing_p_and_l, missing_product_ownership, salary_too_low, wrong_geography

Positive contributions:
- +15 fintech_or_telecom
Negative contributions: -

## 45. Senior Growth Manager (Performance & Analytics) | ООО Бринго | headhunter | score=12
- location: Unknown
- url: https://hh.ru/vacancy/133515581
- threshold: 60
- rejection_reasons: blocked_geography, insufficient_data, low_company_score, low_confidence, low_hiring_likelihood, low_seniority, missing_executive_responsibility, missing_p_and_l, salary_too_low, wrong_geography

Positive contributions:
- +12 growth_signal
Negative contributions: -

## 46. Senior Growth Manager (Performance & Analytics) | ООО Бринго | headhunter | score=12
- location: Unknown
- url: https://hh.ru/vacancy/133515581
- threshold: 60
- rejection_reasons: blocked_geography, insufficient_data, low_company_score, low_confidence, low_hiring_likelihood, low_seniority, missing_executive_responsibility, missing_p_and_l, salary_too_low, wrong_geography

Positive contributions:
- +12 growth_signal
Negative contributions: -

## 47. International Expansion Lead (IT & Product Companies) | ООО Технологический парк программных продуктов и информационных технологий | headhunter | score=10
- location: Ташкент, улица Муминова, 7/2
- url: https://hh.ru/vacancy/133043996
- threshold: 60
- rejection_reasons: blocked_geography, insufficient_data, low_company_score, low_confidence, low_hiring_likelihood, low_seniority, missing_executive_responsibility, missing_p_and_l, salary_too_low, wrong_geography

Positive contributions:
- +10 international_team
Negative contributions: -

## 48. AI Product & Operations Lead | ИП Иванова Марина Николаевна | headhunter | score=10
- location: Unknown
- url: https://hh.ru/vacancy/133486511
- threshold: 60
- rejection_reasons: blocked_geography, insufficient_data, low_company_score, low_confidence, low_hiring_likelihood, low_seniority, missing_executive_responsibility, missing_p_and_l, missing_product_ownership, salary_too_low

Positive contributions:
- +10 AI_or_modern_tech
Negative contributions: -

## 49. Product Operations Manager | Morph | linkedin | score=5
- location: Remote
- url: https://www.linkedin.com/jobs/view/4410293868/
- threshold: 60
- rejection_reasons: blocked_geography, insufficient_data, low_company_score, low_confidence, low_hiring_likelihood, low_seniority, missing_executive_responsibility, missing_p_and_l, salary_too_low

Positive contributions:
- +5 remote_friendly
Negative contributions: -

## 50. Product Operations ManagerProduct Operations Manager | Unknown | linkedin | score=0
- location: Unknown
- url: https://www.linkedin.com/jobs/view/4410293868/
- threshold: 60
- rejection_reasons: blocked_geography, insufficient_data, low_company_score, low_confidence, low_hiring_likelihood, low_seniority, missing_executive_responsibility, missing_p_and_l, salary_too_low

Positive contributions: -
Negative contributions: -

# Part 2) Unknown vs Negative Audit

| component | where | current_behavior | unknown_handling |
| --- | --- | --- | --- |
| P&L not mentioned | observability.rejection_reasons_for | Adds missing_p_and_l if no P&L text AND evaluation.score < 75 | Unknown treated as negative reason (conditional) |
| Salary missing | observability.rejection_reasons_for | If salary missing AND score < 50 => salary_too_low | Unknown treated as negative reason (conditional) |
| Description short/missing | observability.rejection_reasons_for | If description <120 chars => insufficient_data | Unknown treated as negative reason |
| Location bucket Other | observability.rejection_reasons_for | If geo_bucket(location)==Other AND score<60 => blocked_geography; also wrong_geography if region tokens (APAC/MENA/EMEA/Global) in text | Unknown treated as negative reason |
| low_company_score | observability.rejection_reasons_for | If evaluation.score <55 => low_company_score (circular) | Derived from score, not a field |
| low_hiring_likelihood | observability.rejection_reasons_for | If evaluation.score <45 => low_hiring_likelihood (circular) | Derived from score, not a field |
| low_confidence | observability.rejection_reasons_for | If evaluation.score <35 => low_confidence (circular) | Derived from score, not a field |
| Industry not detected | evaluator.score_vacancy | No industry keyword match => 0 points (neutral) | Unknown treated as neutral |
| Product ownership not detected | evaluator.score_vacancy + observability | No product ownership keywords => no +20; observability also adds missing_product_ownership | Neutral in score; negative in reasons |

# Part 3) Threshold Calibration

Evaluations across runs: total=163

Counts:
- >=80: 0
- >=70: 0
- >=60: 0
- >=50: 0
- >=40: 17

Examples (top 3 for each band):
## band >=80
## band >=70
## band >=60
## band >=50
## band >=40
- run=154 score=47 headhunter | Meteoro Platform | Product Lead | Unknown | https://hh.ru/vacancy/133652461
- run=156 score=47 headhunter | Meteoro Platform | Product Lead | Unknown | https://hh.ru/vacancy/133652461
- run=177 score=47 headhunter | Meteoro Platform | Product Lead | Unknown | https://hh.ru/vacancy/133652461

# Part 4) Opportunity Review Mode (Recommendation)

- Strong Fit: score >=75
- Potential Fit: score 60-74
- Near Miss: score 45-59
- Reject: score <45
Note: Bands assume executive classifier fixed; current classifier inflates scores for non-product roles.

# Part 5) Geography Model Review

| rejection_reason | exact rule (current) |
| --- | --- |
| blocked_geography | blocked_geo_terms in text => blocked_geography; OR geo_bucket(location)==Other AND score<60 => blocked_geography |
| wrong_geography | region tokens (latam/mena/emea/apac/global) in text + geo_bucket==Other; also triggers on term 'remote only in' |
| onsite_requirement_mismatch | if 'onsite' or 'on-site' or 'hybrid' in text |

# Part 6) Recommendation (Concrete, No Implementation)

Evidence-driven proposals based on observed data:
- Stop treating missing evidence as negative in rejection telemetry: replace `missing_p_and_l`, `salary_too_low` (when salary missing) with `pnl_unknown`, `salary_unknown` and do not count them as rejections.
- Remove circular reasons (`low_company_score`, `low_hiring_likelihood`, `low_confidence`) from rejection reports; they are thresholds on the same score and add noise.
- Tighten executive detection to product leadership only; block sales `Account Executive`/`Sales Executive` patterns from adding executive points.
- Calibrate acceptance: keep 60 as 'Potential Fit' threshold, but do not binary-reject weak_fit; surface them as Near Miss with explicit missing evidence.
- Geography: replace heuristic `wrong_geography` triggers based on region tokens with explicit Tier1/Tier2 allowlists to match strategy (Europe/GCC/APAC/Remote; Kazakhstan Tier2).

ATS scoring consistency evidence (top 10 each):
- greenhouse | score 100 vs prod 100 | stripe | Account Executive, AI Sales | San Francisco, CA | accept | https://stripe.com/jobs/search
- greenhouse | score 100 vs prod 100 | stripe | Account Executive,Iberia, Enterprise | Spain | accept | https://stripe.com/jobs/search
- greenhouse | score 100 vs prod 100 | stripe | Account Executive, Platforms | Sydney Or Melbourne | accept | https://stripe.com/jobs/search
- greenhouse | score 100 vs prod 100 | stripe | Account Executive, Platforms (Hunter) | Paris, Framce | accept | https://stripe.com/jobs/search
- greenhouse | score 100 vs prod 100 | stripe | Account Executive, Product Sales - Capital | SF, NYC, SEA, CHI | accept | https://stripe.com/jobs/search
- greenhouse | score 100 vs prod 100 | stripe | Account Executive, Product Sales - Payouts | SF, NYC | accept | https://stripe.com/jobs/search
- greenhouse | score 100 vs prod 100 | stripe | Accounting Technical Solutions Lead | Seattle, San Francisco, New York | accept | https://stripe.com/jobs/search
- greenhouse | score 100 vs prod 100 | stripe | Accounts Receivable Analyst - Service Strategy Enablement | Seattle, SF | accept | https://stripe.com/jobs/search
- greenhouse | score 100 vs prod 100 | stripe | Business Development Manager, Agentic Commerce | San Francisco, CA; Seattle, WA | accept | https://stripe.com/jobs/search
- greenhouse | score 100 vs prod 100 | stripe | Consumer Product Marketing Lead, Link | San Fransisco, New York, Seattle, Chicago, Remote US | accept | https://stripe.com/jobs/search
- ashby | score 100 vs prod 100 | openai | Software Engineer, Financial Engineering | San Francisco | accept | https://jobs.ashbyhq.com/openai/4ef5bf23-cf0e-4b97-a639-11f963c99b88
- ashby | score 100 vs prod 100 | openai | Data Scientist, Financial Engineering | San Francisco | accept | https://jobs.ashbyhq.com/openai/898a87fb-4cb8-450e-9840-ee5dc710a57d
- ashby | score 100 vs prod 100 | openai | Trust & Safety Operations Analyst, Ads | San Francisco | accept | https://jobs.ashbyhq.com/openai/c9e9e3a5-fb93-4162-b876-6266016819c0
- ashby | score 100 vs prod 100 | openai | Software Engineer, Monetization Product & Platform | San Francisco | accept | https://jobs.ashbyhq.com/openai/c51e48ce-31ad-4176-a6d5-4a785b44ab73
- ashby | score 100 vs prod 100 | openai | Pricing Strategist | San Francisco | accept | https://jobs.ashbyhq.com/openai/3ab0541b-160b-49c0-8609-574db6358332
- ashby | score 100 vs prod 100 | openai | Frontend Engineer, Financial Web Platform | San Francisco | accept | https://jobs.ashbyhq.com/openai/192980b8-b874-4493-a8c1-f7d5660f00f3
- ashby | score 100 vs prod 100 | ramp | Customer Activation Manager | Enterprise | New York, NY (HQ) | accept | https://jobs.ashbyhq.com/ramp/09a9381c-677b-40a5-9ff1-027bd4302c13
- ashby | score 100 vs prod 100 | ramp | Customer Activation Manager | Mid-Market | New York, NY (HQ) | accept | https://jobs.ashbyhq.com/ramp/8086c65e-b4cc-4bdd-8f1f-5bdf03130ff4
- ashby | score 100 vs prod 100 | ramp | Customer Activation Manager | Strategic Enterprise | New York, NY (HQ) | accept | https://jobs.ashbyhq.com/ramp/f330ccd7-59f5-4032-82b0-4448482769e4
- ashby | score 100 vs prod 100 | ramp | Software Engineer, Accounting | New York, NY (HQ) | accept | https://jobs.ashbyhq.com/ramp/ed2e9a94-f58e-4ff8-8853-afd977850d43
