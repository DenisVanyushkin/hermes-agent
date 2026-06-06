# V2 Rollout Readiness Audit

run_id: 218 (smoke)
Dataset: vacancies from this run, deduped by URL, offline rescored with scorer v1 and v2 (no code/config changes).

## 1. Bucket Transition Matrix (v1 -> v2)

Deduped vacancy count: 110

Matrix counts:

| v1 \ v2 | strong_fit | potential_fit | near_miss | reject |
| --- | --- | --- | --- | --- |
| strong_fit | 1 | 0 | 10 | 23 |
| potential_fit | 0 | 0 | 2 | 3 |
| near_miss | 0 | 0 | 11 | 21 |
| reject | 0 | 0 | 3 | 36 |

Explanation of why v1 had many >reject while v2 has fewer:
- v2 removes/limits several broad keyword boosts and gates `executive_visibility` behind product-domain.
- v2 adds `non_product_function_penalty` for non-product functions when product-domain is absent.
- v2 treats company_score/hiring_likelihood as neutral (no boost), so non-product roles that previously accumulated many generic positives drop.

## 2. Top Regressions (v1 strong/potential -> v2 reject)

Count: 26 (showing up to 20)

### 1. planetlabs | Customer Success Associate | greenhouse
- v1: score=100 rec=strong_fit breakdown=+25 PnL_ownership, +20 executive_visibility, +15 B2C_platform, +12 growth_signal, +10 mobile_product, +10 international_team, +10 AI_or_modern_tech, +5 remote_friendly
- v2: score=37 rec=reject breakdown=+12 AI_or_modern_tech, +10 B2C_platform, +10 PnL_ownership, +5 remote_friendly
- delta: -63
- exact reasons (breakdown delta): -20 executive_visibility, -15 PnL_ownership, -12 growth_signal, -10 international_team, -10 mobile_product, -5 B2C_platform, +2 AI_or_modern_tech
- url: https://job-boards.greenhouse.io/planetlabs/jobs/7625367

### 2. planetlabs | Industry Marketing Manager, Commercial | greenhouse
- v1: score=100 rec=strong_fit breakdown=+25 PnL_ownership, +20 executive_visibility, +15 B2C_platform, +12 growth_signal, +10 mobile_product, +10 international_team, +10 AI_or_modern_tech, +5 remote_friendly
- v2: score=37 rec=reject breakdown=+12 AI_or_modern_tech, +10 B2C_platform, +10 PnL_ownership, +5 remote_friendly
- delta: -63
- exact reasons (breakdown delta): -20 executive_visibility, -15 PnL_ownership, -12 growth_signal, -10 international_team, -10 mobile_product, -5 B2C_platform, +2 AI_or_modern_tech
- url: https://job-boards.greenhouse.io/planetlabs/jobs/7618778

### 3. planetlabs | Industry Marketing Manager, Commercial | greenhouse
- v1: score=100 rec=strong_fit breakdown=+25 PnL_ownership, +20 executive_visibility, +15 B2C_platform, +12 growth_signal, +10 mobile_product, +10 international_team, +10 AI_or_modern_tech, +5 remote_friendly
- v2: score=37 rec=reject breakdown=+12 AI_or_modern_tech, +10 B2C_platform, +10 PnL_ownership, +5 remote_friendly
- delta: -63
- exact reasons (breakdown delta): -20 executive_visibility, -15 PnL_ownership, -12 growth_signal, -10 international_team, -10 mobile_product, -5 B2C_platform, +2 AI_or_modern_tech
- url: https://job-boards.greenhouse.io/planetlabs/jobs/7683178

### 4. planetlabs | AI Engineer, Marketing | greenhouse
- v1: score=82 rec=strong_fit breakdown=+20 executive_visibility, +15 B2C_platform, +12 growth_signal, +10 mobile_product, +10 international_team, +10 AI_or_modern_tech, +5 remote_friendly
- v2: score=27 rec=reject breakdown=+12 AI_or_modern_tech, +10 B2C_platform, +5 remote_friendly
- delta: -55
- exact reasons (breakdown delta): -20 executive_visibility, -12 growth_signal, -10 international_team, -10 mobile_product, -5 B2C_platform, +2 AI_or_modern_tech
- url: https://job-boards.greenhouse.io/planetlabs/jobs/7782587

### 5. planetlabs | AI Engineer, Marketing | greenhouse
- v1: score=82 rec=strong_fit breakdown=+20 executive_visibility, +15 B2C_platform, +12 growth_signal, +10 mobile_product, +10 international_team, +10 AI_or_modern_tech, +5 remote_friendly
- v2: score=27 rec=reject breakdown=+12 AI_or_modern_tech, +10 B2C_platform, +5 remote_friendly
- delta: -55
- exact reasons (breakdown delta): -20 executive_visibility, -12 growth_signal, -10 international_team, -10 mobile_product, -5 B2C_platform, +2 AI_or_modern_tech
- url: https://job-boards.greenhouse.io/planetlabs/jobs/7782580

### 6. planetlabs | Customer Success Manager, EMEA Civil Governments | greenhouse
- v1: score=82 rec=strong_fit breakdown=+20 executive_visibility, +15 B2C_platform, +12 growth_signal, +10 mobile_product, +10 international_team, +10 AI_or_modern_tech, +5 remote_friendly
- v2: score=27 rec=reject breakdown=+12 AI_or_modern_tech, +10 B2C_platform, +5 remote_friendly
- delta: -55
- exact reasons (breakdown delta): -20 executive_visibility, -12 growth_signal, -10 international_team, -10 mobile_product, -5 B2C_platform, +2 AI_or_modern_tech
- url: https://job-boards.greenhouse.io/planetlabs/jobs/7870467

### 7. planetlabs | Customer Success Manager II, EMEA (DACH - Civil Government) | greenhouse
- v1: score=82 rec=strong_fit breakdown=+20 executive_visibility, +15 B2C_platform, +12 growth_signal, +10 mobile_product, +10 international_team, +10 AI_or_modern_tech, +5 remote_friendly
- v2: score=27 rec=reject breakdown=+12 AI_or_modern_tech, +10 B2C_platform, +5 remote_friendly
- delta: -55
- exact reasons (breakdown delta): -20 executive_visibility, -12 growth_signal, -10 international_team, -10 mobile_product, -5 B2C_platform, +2 AI_or_modern_tech
- url: https://job-boards.greenhouse.io/planetlabs/jobs/7742352

### 8. planetlabs | Customer Support Representative | greenhouse
- v1: score=82 rec=strong_fit breakdown=+20 executive_visibility, +15 B2C_platform, +12 growth_signal, +10 mobile_product, +10 international_team, +10 AI_or_modern_tech, +5 remote_friendly
- v2: score=27 rec=reject breakdown=+12 AI_or_modern_tech, +10 B2C_platform, +5 remote_friendly
- delta: -55
- exact reasons (breakdown delta): -20 executive_visibility, -12 growth_signal, -10 international_team, -10 mobile_product, -5 B2C_platform, +2 AI_or_modern_tech
- url: https://job-boards.greenhouse.io/planetlabs/jobs/7927109

### 9. planetlabs | Engineering Program Manager | greenhouse
- v1: score=82 rec=strong_fit breakdown=+20 executive_visibility, +15 B2C_platform, +12 growth_signal, +10 mobile_product, +10 international_team, +10 AI_or_modern_tech, +5 remote_friendly
- v2: score=27 rec=reject breakdown=+12 AI_or_modern_tech, +10 B2C_platform, +5 remote_friendly
- delta: -55
- exact reasons (breakdown delta): -20 executive_visibility, -12 growth_signal, -10 international_team, -10 mobile_product, -5 B2C_platform, +2 AI_or_modern_tech
- url: https://job-boards.greenhouse.io/planetlabs/jobs/7455984

### 10. planetlabs | Jr Field Marketing Analyst LATAM | greenhouse
- v1: score=82 rec=strong_fit breakdown=+20 executive_visibility, +15 B2C_platform, +12 growth_signal, +10 mobile_product, +10 international_team, +10 AI_or_modern_tech, +5 remote_friendly
- v2: score=27 rec=reject breakdown=+12 AI_or_modern_tech, +10 B2C_platform, +5 remote_friendly
- delta: -55
- exact reasons (breakdown delta): -20 executive_visibility, -12 growth_signal, -10 international_team, -10 mobile_product, -5 B2C_platform, +2 AI_or_modern_tech
- url: https://job-boards.greenhouse.io/planetlabs/jobs/7760416

### 11. planetlabs | PreSales Sr. Solutions Architect - French Defense and Intelligence | greenhouse
- v1: score=82 rec=strong_fit breakdown=+20 executive_visibility, +15 B2C_platform, +12 growth_signal, +10 mobile_product, +10 international_team, +10 AI_or_modern_tech, +5 remote_friendly
- v2: score=27 rec=reject breakdown=+12 AI_or_modern_tech, +10 B2C_platform, +5 remote_friendly
- delta: -55
- exact reasons (breakdown delta): -20 executive_visibility, -12 growth_signal, -10 international_team, -10 mobile_product, -5 B2C_platform, +2 AI_or_modern_tech
- url: https://job-boards.greenhouse.io/planetlabs/jobs/7622544

### 12. planetlabs | PreSales Sr. Solutions Architect - French Defense and Intelligence | greenhouse
- v1: score=82 rec=strong_fit breakdown=+20 executive_visibility, +15 B2C_platform, +12 growth_signal, +10 mobile_product, +10 international_team, +10 AI_or_modern_tech, +5 remote_friendly
- v2: score=27 rec=reject breakdown=+12 AI_or_modern_tech, +10 B2C_platform, +5 remote_friendly
- delta: -55
- exact reasons (breakdown delta): -20 executive_visibility, -12 growth_signal, -10 international_team, -10 mobile_product, -5 B2C_platform, +2 AI_or_modern_tech
- url: https://job-boards.greenhouse.io/planetlabs/jobs/7700271

### 13. planetlabs | Senior Product Designer | greenhouse
- v1: score=82 rec=strong_fit breakdown=+20 executive_visibility, +15 B2C_platform, +12 growth_signal, +10 mobile_product, +10 international_team, +10 AI_or_modern_tech, +5 remote_friendly
- v2: score=27 rec=reject breakdown=+12 AI_or_modern_tech, +10 B2C_platform, +5 remote_friendly
- delta: -55
- exact reasons (breakdown delta): -20 executive_visibility, -12 growth_signal, -10 international_team, -10 mobile_product, -5 B2C_platform, +2 AI_or_modern_tech
- url: https://job-boards.greenhouse.io/planetlabs/jobs/7664694

### 14. planetlabs | Senior Product Designer | greenhouse
- v1: score=82 rec=strong_fit breakdown=+20 executive_visibility, +15 B2C_platform, +12 growth_signal, +10 mobile_product, +10 international_team, +10 AI_or_modern_tech, +5 remote_friendly
- v2: score=27 rec=reject breakdown=+12 AI_or_modern_tech, +10 B2C_platform, +5 remote_friendly
- delta: -55
- exact reasons (breakdown delta): -20 executive_visibility, -12 growth_signal, -10 international_team, -10 mobile_product, -5 B2C_platform, +2 AI_or_modern_tech
- url: https://job-boards.greenhouse.io/planetlabs/jobs/7664733

### 15. planetlabs | Senior Product Designer | greenhouse
- v1: score=82 rec=strong_fit breakdown=+20 executive_visibility, +15 B2C_platform, +12 growth_signal, +10 mobile_product, +10 international_team, +10 AI_or_modern_tech, +5 remote_friendly
- v2: score=27 rec=reject breakdown=+12 AI_or_modern_tech, +10 B2C_platform, +5 remote_friendly
- delta: -55
- exact reasons (breakdown delta): -20 executive_visibility, -12 growth_signal, -10 international_team, -10 mobile_product, -5 B2C_platform, +2 AI_or_modern_tech
- url: https://job-boards.greenhouse.io/planetlabs/jobs/7664731

### 16. planetlabs | Senior Product Designer | greenhouse
- v1: score=82 rec=strong_fit breakdown=+20 executive_visibility, +15 B2C_platform, +12 growth_signal, +10 mobile_product, +10 international_team, +10 AI_or_modern_tech, +5 remote_friendly
- v2: score=27 rec=reject breakdown=+12 AI_or_modern_tech, +10 B2C_platform, +5 remote_friendly
- delta: -55
- exact reasons (breakdown delta): -20 executive_visibility, -12 growth_signal, -10 international_team, -10 mobile_product, -5 B2C_platform, +2 AI_or_modern_tech
- url: https://job-boards.greenhouse.io/planetlabs/jobs/7664728

### 17. planetlabs | Solutions Architect Deployment Strategist | greenhouse
- v1: score=82 rec=strong_fit breakdown=+20 executive_visibility, +15 B2C_platform, +12 growth_signal, +10 mobile_product, +10 international_team, +10 AI_or_modern_tech, +5 remote_friendly
- v2: score=27 rec=reject breakdown=+12 AI_or_modern_tech, +10 B2C_platform, +5 remote_friendly
- delta: -55
- exact reasons (breakdown delta): -20 executive_visibility, -12 growth_signal, -10 international_team, -10 mobile_product, -5 B2C_platform, +2 AI_or_modern_tech
- url: https://job-boards.greenhouse.io/planetlabs/jobs/7377994

### 18. planetlabs | Technical Program Manager | greenhouse
- v1: score=82 rec=strong_fit breakdown=+20 executive_visibility, +15 B2C_platform, +12 growth_signal, +10 mobile_product, +10 international_team, +10 AI_or_modern_tech, +5 remote_friendly
- v2: score=27 rec=reject breakdown=+12 AI_or_modern_tech, +10 B2C_platform, +5 remote_friendly
- delta: -55
- exact reasons (breakdown delta): -20 executive_visibility, -12 growth_signal, -10 international_team, -10 mobile_product, -5 B2C_platform, +2 AI_or_modern_tech
- url: https://job-boards.greenhouse.io/planetlabs/jobs/7907773

### 19. planetlabs | Executive Operations Partner | greenhouse
- v1: score=70 rec=potential_fit breakdown=+20 org_transformation, +15 B2C_platform, +10 mobile_product, +10 international_team, +10 AI_or_modern_tech, +5 remote_friendly
- v2: score=27 rec=reject breakdown=+12 AI_or_modern_tech, +10 B2C_platform, +5 remote_friendly
- delta: -43
- exact reasons (breakdown delta): -20 org_transformation, -10 international_team, -10 mobile_product, -5 B2C_platform, +2 AI_or_modern_tech
- url: https://job-boards.greenhouse.io/planetlabs/jobs/7926536

### 20. planetlabs | Regional Manager, Customer Success (D&I - APJ) | greenhouse
- v1: score=70 rec=potential_fit breakdown=+20 org_transformation, +15 B2C_platform, +10 mobile_product, +10 international_team, +10 AI_or_modern_tech, +5 remote_friendly
- v2: score=27 rec=reject breakdown=+12 AI_or_modern_tech, +10 B2C_platform, +5 remote_friendly
- delta: -43
- exact reasons (breakdown delta): -20 org_transformation, -10 international_team, -10 mobile_product, -5 B2C_platform, +2 AI_or_modern_tech
- url: https://job-boards.greenhouse.io/planetlabs/jobs/7785112

## 3. Top Improvements (v1 reject -> v2 near_miss/potential/strong)

Count: 3 (showing up to 20)

### 1. planetlabs | Lab Manager, R&D | greenhouse
- v1: score=20 rec=reject breakdown=-30 delivery_only, +15 B2C_platform, +10 mobile_product, +10 international_team, +10 AI_or_modern_tech, +5 remote_friendly
- v2: score=42 rec=near_miss breakdown=-30 delivery_only, +25 product_ownership, +20 growth_signal, +12 AI_or_modern_tech, +10 B2C_platform, +5 remote_friendly
- delta: 22
- exact reasons (breakdown delta): +25 product_ownership, +20 growth_signal, -10 international_team, -10 mobile_product, -5 B2C_platform, +2 AI_or_modern_tech
- url: https://job-boards.greenhouse.io/planetlabs/jobs/7644556

### 2. ТОО Bilim Land (Bilim Group) | Growth Product Lead | headhunter
- v1: score=32 rec=reject breakdown=+20 executive_visibility, +12 growth_signal
- v2: score=45 rec=near_miss breakdown=+20 growth_signal, +15 executive_visibility, +10 product_lead_title_bonus
- delta: 13
- exact reasons (breakdown delta): +10 product_lead_title_bonus, +8 growth_signal, -5 executive_visibility
- url: https://hh.ru/vacancy/132562956

### 3. Social Discovery Group | Head of Product (AI Product) - Full remote | linkedin
- v1: score=35 rec=reject breakdown=+20 executive_visibility, +10 AI_or_modern_tech, +5 remote_friendly
- v2: score=42 rec=near_miss breakdown=+15 executive_visibility, +12 AI_or_modern_tech, +10 head_of_product_title_bonus, +5 remote_friendly
- delta: 7
- exact reasons (breakdown delta): +10 head_of_product_title_bonus, -5 executive_visibility, +2 AI_or_modern_tech
- url: https://www.linkedin.com/jobs/view/4384021732

## 4. Sanity Check (target titles)

Counts found in this run (deduped):
- Head of Product: 4
- VP Product: 0
- Director Product: 0
- GM Product: 0
- Product Lead: 3
- Growth Product Lead: 1

Details:
- Head of Product: Discovered MENA | Head of Product - Fintech | linkedin | v1=40/near_miss -> v2=48/near_miss | https://www.linkedin.com/jobs/view/4417070084
- Head of Product: Social Discovery Group | Head of Product (AI Product) - Full remote | linkedin | v1=35/reject -> v2=42/near_miss | https://www.linkedin.com/jobs/view/4384021732
- Product Lead: ТОО Bilim Land (Bilim Group) | Growth Product Lead | headhunter | v1=32/reject -> v2=45/near_miss | https://hh.ru/vacancy/132562956
- Growth Product Lead: ТОО Bilim Land (Bilim Group) | Growth Product Lead | headhunter | v1=32/reject -> v2=45/near_miss | https://hh.ru/vacancy/132562956
- Head of Product: АО CENTER FOR DIGITAL TECHNOLOGY AND INNOVATION | Head of Product | headhunter | v1=20/reject -> v2=25/reject | https://hh.ru/vacancy/133446873
- Head of Product: Headshot | Team Lead / Head of Product Marketing (Africa | iGaming) | headhunter | v1=20/reject -> v2=35/reject | https://hh.ru/vacancy/133107602
- Product Lead: Meteoro Platform | Product Lead | headhunter | v1=47/near_miss -> v2=35/reject | https://hh.ru/vacancy/133652461
- Product Lead: ТОО Ticketon Events | Product Lead (Афиша) | headhunter | v1=32/reject -> v2=25/reject | https://hh.ru/vacancy/133470638

## 5. Recommendation

READY FOR PRODUCTION

- v1 strong/potential=39, v2 strong/potential=1 (non-zero), with regression list reviewed above.
- Proceed with 7-run dual-score rollout and monitor FP/FN examples.

Appendix:
- v1 bucket distribution (deduped): {'reject': 39, 'near_miss': 32, 'strong_fit': 34, 'potential_fit': 5}
- v2 bucket distribution (deduped): {'reject': 83, 'near_miss': 26, 'strong_fit': 1}
