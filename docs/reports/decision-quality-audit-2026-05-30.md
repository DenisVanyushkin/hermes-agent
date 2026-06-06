# Scoring, Classifier and Decision Transparency Audit (2026-05-30)

## Inputs
- sqlite: `/var/lib/job-intel/state/job_intel.sqlite3`
- latest meaningful production daily runs considered: `177, 156, 154`
- threshold (possible_fit): `60`

# Part 1) Executive Classifier Audit

Top 100 vacancies classified as executive (sorted by score, across latest meaningful runs; de-duped by URL):

| title | company | location | source | classification | fired_rules | confidence | score | url |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Product Lead | Meteoro Platform | Unknown | headhunter | product_strategy_and_growth | TEXT_HAS_STRATEGIC_SCOPE,TITLE_HAS_LEADERSHIP_TOKEN | 0.65 | 47 | https://hh.ru/vacancy/133652461 |
| Head of Finance / Finance Director — Longevity, Wellness & H | Clarity Partners (ИП Дынко Алла Дмитриев | Москва | headhunter | executive_product_leadership | TITLE_HAS_PRODUCT_DOMAIN_AND_EXEC_SENIORITY | 0.90 | 45 | https://hh.ru/vacancy/132805669 |
| Head of Product - Fintech | Discovered MENA | Remote | linkedin | executive_product_leadership | TITLE_HAS_PRODUCT_DOMAIN_AND_EXEC_SENIORITY | 0.90 | 40 | https://www.linkedin.com/jobs/view/4417070084 |
| Head of Product (AI Product) - Full remote | Social Discovery Group | Remote | linkedin | executive_product_leadership | TITLE_HAS_PRODUCT_DOMAIN_AND_EXEC_SENIORITY | 0.90 | 35 | https://www.linkedin.com/jobs/view/4384021732 |
| Deputy Head of Cloud Products and Services at MUK Cloud | MUK | Kazakhstan (On-site) | linkedin | executive_product_leadership | TITLE_HAS_PRODUCT_DOMAIN_AND_EXEC_SENIORITY | 0.90 | 20 | https://www.linkedin.com/jobs/view/4401491114 |
| Product Launch Manager (1 Position at Head Office) | CAMMA Corporate Learning and Development | Phnom Penh | linkedin | executive_product_leadership | TITLE_HAS_PRODUCT_DOMAIN_AND_EXEC_SENIORITY | 0.90 | 20 | https://www.linkedin.com/jobs/view/4338017837/ |
| Head of Product | АО CENTER FOR DIGITAL TECHNOLOGY AND INN | Ташкент | headhunter | executive_product_leadership | TITLE_HAS_PRODUCT_DOMAIN_AND_EXEC_SENIORITY | 0.90 | 20 | https://hh.ru/vacancy/133446873 |
| Product Live Ops Director | ТОО G5EN KAZ | Unknown | headhunter | executive_product_leadership | TITLE_HAS_PRODUCT_DOMAIN_AND_EXEC_SENIORITY | 0.90 | 20 | https://hh.ru/vacancy/133049614 |
| Head of Product | ТОО Коркем Телеком | Алматы | headhunter | executive_product_leadership | TITLE_HAS_PRODUCT_DOMAIN_AND_EXEC_SENIORITY | 0.90 | 20 | https://hh.ru/vacancy/132650487 |
| Team Lead / Head of Product Marketing (Africa | iGaming) | Headshot | Unknown | headhunter | executive_product_leadership | TITLE_HAS_PRODUCT_DOMAIN_AND_EXEC_SENIORITY | 0.90 | 20 | https://hh.ru/vacancy/133107602 |

Summary:
- total vacancies inspected (until 100 executive found): 80
- executive classifications produced: 10
- executive classification rate (in inspected slice): 12.5%
- top rules responsible:
  - TITLE_HAS_PRODUCT_DOMAIN_AND_EXEC_SENIORITY: 9
  - TEXT_HAS_STRATEGIC_SCOPE: 1
  - TITLE_HAS_LEADERSHIP_TOKEN: 1

# Part 2) Production Scoring Audit (Latest Run)

Run used: `177`
Rejected vacancies: `37`

| bucket | count | pct |
| --- | --- | --- |
| geography mismatch | 37 | 100.0% |
| missing P&L | 37 | 100.0% |
| other | 37 | 100.0% |
| low confidence | 31 | 83.8% |
| missing product ownership | 27 | 73.0% |
| insufficient seniority | 18 | 48.6% |
| missing executive responsibility | 18 | 48.6% |

# Part 3) Near-Miss Analysis (Top 50 Rejected)

| title | company | location | source | final_score | threshold | rejection_reasons | url |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Product Lead | Meteoro Platform | Unknown | headhunter | 47 | 60 | blocked_geography,insufficient_data,low_company_score,missing_p_and_l,missing_product_ownership,salary_too_low | https://hh.ru/vacancy/133652461 |
| Head of Product - Fintech | Discovered MENA | Remote | linkedin | 40 | 60 | blocked_geography,insufficient_data,low_company_score,low_hiring_likelihood,missing_p_and_l,missing_product_ownership,salary_too_low,wrong_geography | https://www.linkedin.com/jobs/view/4417070084 |
| Head of Product (AI Product) - Full remote | Social Discovery Group | Remote | linkedin | 35 | 60 | blocked_geography,insufficient_data,low_company_score,low_hiring_likelihood,missing_p_and_l,salary_too_low,wrong_geography | https://www.linkedin.com/jobs/view/4384021732 |
| Head of Product (AI Product) - Full remote Head of Product ( | Unknown | Unknown | linkedin | 35 | 60 | blocked_geography,insufficient_data,low_company_score,low_hiring_likelihood,missing_p_and_l,salary_too_low,wrong_geography | https://www.linkedin.com/jobs/view/4384021732 |
| Head of Product - Fintech Head of Product - Fintech with ver | Unknown | Unknown | linkedin | 35 | 60 | blocked_geography,insufficient_data,low_company_score,low_hiring_likelihood,missing_p_and_l,missing_product_ownership,salary_too_low,wrong_geography | https://www.linkedin.com/jobs/view/4417070084 |
| Head of Product - Fintech | Unknown | Unknown | linkedin | 35 | 60 | blocked_geography,insufficient_data,low_company_score,low_hiring_likelihood,missing_p_and_l,missing_product_ownership,salary_too_low,wrong_geography | https://www.linkedin.com/jobs/view/4417070084 |
| Product Lead (Афиша) | ТОО Ticketon Events | Алматы, проспект Жибек | headhunter | 32 | 60 | blocked_geography,insufficient_data,low_company_score,low_confidence,low_hiring_likelihood,missing_p_and_l,missing_product_ownership,salary_too_low | https://hh.ru/vacancy/133470638 |
| Founding CPO → CEO для AI-стартапа в США | Aspirine HR | Unknown | headhunter | 30 | 60 | blocked_geography,insufficient_data,low_company_score,low_confidence,low_hiring_likelihood,missing_p_and_l,missing_product_ownership,salary_too_low | https://hh.ru/vacancy/133184144 |
| Platform Architect/Архитектор платформы | YADRO | Unknown | headhunter | 27 | 60 | blocked_geography,insufficient_data,low_company_score,low_confidence,low_hiring_likelihood,low_seniority,missing_executive_responsibility,missing_p_and_l,missing_product_ownership,salary_too_low | https://hh.ru/vacancy/132307461 |
| Deputy Head of Cloud Products and Services at MUK Cloud | MUK | Kazakhstan (On-site) | linkedin | 20 | 60 | blocked_geography,insufficient_data,low_company_score,low_confidence,low_hiring_likelihood,missing_p_and_l,onsite_requirement_mismatch,salary_too_low,wrong_geography | https://www.linkedin.com/jobs/view/4401491114 |
| Deputy Head of Cloud Products and Services at MUK CloudDeput | Unknown | Unknown | linkedin | 20 | 60 | blocked_geography,insufficient_data,low_company_score,low_confidence,low_hiring_likelihood,missing_p_and_l,salary_too_low,wrong_geography | https://www.linkedin.com/jobs/view/4401491114 |
| Product Launch Manager (1 Position at Head Office) | CAMMA Corporate Learning and Develop | Phnom Penh, Phnom Penh | linkedin | 20 | 60 | blocked_geography,insufficient_data,low_company_score,low_confidence,low_hiring_likelihood,missing_p_and_l,onsite_requirement_mismatch,salary_too_low | https://www.linkedin.com/jobs/view/4338017837/ |
| Product Launch Manager (1 Position at Head Office)Product La | Unknown | Unknown | linkedin | 20 | 60 | blocked_geography,insufficient_data,low_company_score,low_confidence,low_hiring_likelihood,missing_p_and_l,salary_too_low | https://www.linkedin.com/jobs/view/4338017837/ |
| Chief Business Development Officer | Magnetto.com | Unknown | headhunter | 20 | 60 | blocked_geography,insufficient_data,low_company_score,low_confidence,low_hiring_likelihood,missing_p_and_l,missing_product_ownership,salary_too_low,wrong_geography | https://hh.ru/vacancy/132653639 |
| Team Lead / Head of Product Marketing (Africa | iGaming) | Headshot | Unknown | headhunter | 20 | 60 | blocked_geography,insufficient_data,low_company_score,low_confidence,low_hiring_likelihood,missing_p_and_l,missing_product_ownership,salary_too_low | https://hh.ru/vacancy/133107602 |
| Head of Data | ТОО Alem Agro Holding (АлемАгро Холд | Алматы | headhunter | 20 | 60 | blocked_geography,insufficient_data,low_company_score,low_confidence,low_hiring_likelihood,missing_p_and_l,missing_product_ownership,salary_too_low | https://hh.ru/vacancy/133143236 |
| Head of Product | АО CENTER FOR DIGITAL TECHNOLOGY AND | Ташкент, улица Махтумк | headhunter | 20 | 60 | blocked_geography,insufficient_data,low_company_score,low_confidence,low_hiring_likelihood,missing_p_and_l,missing_product_ownership,salary_too_low | https://hh.ru/vacancy/133446873 |
| CMO (Chief Marketing Officer) | Банк | Алматы | headhunter | 20 | 60 | blocked_geography,insufficient_data,low_company_score,low_confidence,low_hiring_likelihood,missing_p_and_l,missing_product_ownership,salary_too_low | https://hh.ru/vacancy/133504798 |
| Head of Cargo Marketing | Международный Аэропорт Алматы, АО | Алматы, аэропорт Алмат | headhunter | 20 | 60 | blocked_geography,insufficient_data,low_company_score,low_confidence,low_hiring_likelihood,missing_p_and_l,missing_product_ownership,salary_too_low | https://hh.ru/vacancy/132525450 |
| Head of Data and Analytics / Руководитель направления Данных | Novakid Inc | Unknown | headhunter | 20 | 60 | blocked_geography,insufficient_data,low_company_score,low_confidence,low_hiring_likelihood,missing_p_and_l,missing_product_ownership,salary_too_low | https://hh.ru/vacancy/133223960 |
| Business Analyst with Fintech and Banking experience | Team.Inno / Фабрика инноваций и реше | Минск | headhunter | 15 | 60 | blocked_geography,insufficient_data,low_company_score,low_confidence,low_hiring_likelihood,low_seniority,missing_executive_responsibility,missing_p_and_l,missing_product_ownership,salary_too_low,wrong_geography | https://hh.ru/vacancy/125440730 |
| Digital Marketing Specialist (Technical Software) / Специали | Esprow Pte Ltd | Unknown | headhunter | 15 | 60 | blocked_geography,insufficient_data,low_company_score,low_confidence,low_hiring_likelihood,low_seniority,missing_executive_responsibility,missing_p_and_l,missing_product_ownership,salary_too_low | https://hh.ru/vacancy/133665432 |
| Senior Growth Manager (Performance & Analytics) | ООО Бринго | Unknown | headhunter | 12 | 60 | blocked_geography,insufficient_data,low_company_score,low_confidence,low_hiring_likelihood,low_seniority,missing_executive_responsibility,missing_p_and_l,salary_too_low,wrong_geography | https://hh.ru/vacancy/133515581 |
| International Expansion Lead (IT & Product Companies) | ООО Технологический парк программных | Ташкент, улица Муминов | headhunter | 10 | 60 | blocked_geography,insufficient_data,low_company_score,low_confidence,low_hiring_likelihood,low_seniority,missing_executive_responsibility,missing_p_and_l,salary_too_low,wrong_geography | https://hh.ru/vacancy/133043996 |
| AI Product & Operations Lead | ИП Иванова Марина Николаевна | Unknown | headhunter | 10 | 60 | blocked_geography,insufficient_data,low_company_score,low_confidence,low_hiring_likelihood,low_seniority,missing_executive_responsibility,missing_p_and_l,missing_product_ownership,salary_too_low | https://hh.ru/vacancy/133486511 |
| Product Operations Manager | Morph | Remote | linkedin | 5 | 60 | blocked_geography,insufficient_data,low_company_score,low_confidence,low_hiring_likelihood,low_seniority,missing_executive_responsibility,missing_p_and_l,salary_too_low | https://www.linkedin.com/jobs/view/4410293868/ |
| Product Operations ManagerProduct Operations Manager | Unknown | Unknown | linkedin | 0 | 60 | blocked_geography,insufficient_data,low_company_score,low_confidence,low_hiring_likelihood,low_seniority,missing_executive_responsibility,missing_p_and_l,salary_too_low | https://www.linkedin.com/jobs/view/4410293868/ |
| Senior Product Analyst | ТОО Freedom Media | Алматы, улица Сергея Л | headhunter | 0 | 60 | blocked_geography,insufficient_data,low_company_score,low_confidence,low_hiring_likelihood,low_seniority,missing_executive_responsibility,missing_p_and_l,missing_product_ownership,salary_too_low | https://hh.ru/vacancy/132390785 |
| Product Marketing Lead | BI Group. BI Development | Unknown | headhunter | 0 | 60 | blocked_geography,insufficient_data,low_company_score,low_confidence,low_hiring_likelihood,low_seniority,missing_executive_responsibility,missing_p_and_l,missing_product_ownership,salary_too_low | https://hh.ru/vacancy/133399864 |
| Product Marketing Manager (ВНЕ РФ И РБ) | SpaceHub | Unknown | headhunter | 0 | 60 | blocked_geography,insufficient_data,low_company_score,low_confidence,low_hiring_likelihood,low_seniority,missing_executive_responsibility,missing_p_and_l,missing_product_ownership,salary_too_low | https://hh.ru/vacancy/133397975 |
| Data Analytics Product Owner | EPAM Systems | Remote | linkedin | -25 | 60 | blocked_geography,insufficient_data,low_company_score,low_confidence,low_hiring_likelihood,low_seniority,missing_executive_responsibility,missing_p_and_l,missing_product_ownership,salary_too_low,wrong_geography | https://www.linkedin.com/jobs/view/4413685216 |
| Data Analytics Product Owner Data Analytics Product Owner wi | Unknown | Unknown | linkedin | -30 | 60 | blocked_geography,insufficient_data,low_company_score,low_confidence,low_hiring_likelihood,low_seniority,missing_executive_responsibility,missing_p_and_l,missing_product_ownership,salary_too_low,wrong_geography | https://www.linkedin.com/jobs/view/4413685216 |
| Product Owner (e-com) / Менеджер ИТ продукта | Панда Гифтс | Unknown | headhunter | -30 | 60 | blocked_geography,insufficient_data,low_company_score,low_confidence,low_hiring_likelihood,low_seniority,missing_executive_responsibility,missing_p_and_l,missing_product_ownership,salary_too_low | https://hh.ru/vacancy/133549827 |
| Product Owner | ООО ACCESA. Frontend | Unknown | headhunter | -30 | 60 | blocked_geography,insufficient_data,low_company_score,low_confidence,low_hiring_likelihood,low_seniority,missing_executive_responsibility,missing_p_and_l,missing_product_ownership,salary_too_low | https://hh.ru/vacancy/133011441 |
| Product Owner CVM | АО Народный банк Казахстана | Unknown | headhunter | -30 | 60 | blocked_geography,insufficient_data,low_company_score,low_confidence,low_hiring_likelihood,low_seniority,missing_executive_responsibility,missing_p_and_l,missing_product_ownership,salary_too_low | https://hh.ru/vacancy/133657225 |
| Product Owner «Счета и остатки» | АО Народный банк Казахстана | Unknown | headhunter | -30 | 60 | blocked_geography,insufficient_data,low_company_score,low_confidence,low_hiring_likelihood,low_seniority,missing_executive_responsibility,missing_p_and_l,missing_product_ownership,salary_too_low | https://hh.ru/vacancy/133362004 |
| Data Analyst / Product Owner | ТОО 4FINANCE DIGITAL KZ | Кейптаун | headhunter | -30 | 60 | blocked_geography,insufficient_data,low_company_score,low_confidence,low_hiring_likelihood,low_seniority,missing_executive_responsibility,missing_p_and_l,missing_product_ownership,salary_too_low | https://hh.ru/vacancy/133148114 |

# Part 4) Geography Audit

Top 50 geography rejects (by score):

| title | company | location | source | country | region | geo_reason | url |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Product Lead | Meteoro Platform | Unknown | headhunter | Unknown | Unknown | blocked_geography | https://hh.ru/vacancy/133652461 |
| Head of Product - Fintech | Discovered MENA | Remote | linkedin | Remote | Remote | blocked_geography,wrong_geography | https://www.linkedin.com/jobs/view/4417070084 |
| Head of Product (AI Product) - Full remote | Social Discovery Group | Remote | linkedin | Remote | Remote | blocked_geography,wrong_geography | https://www.linkedin.com/jobs/view/4384021732 |
| Head of Product (AI Product) - Full remote Head of Product ( | Unknown | Unknown | linkedin | Unknown | Unknown | blocked_geography,wrong_geography | https://www.linkedin.com/jobs/view/4384021732 |
| Head of Product - Fintech Head of Product - Fintech with ver | Unknown | Unknown | linkedin | Unknown | Unknown | blocked_geography,wrong_geography | https://www.linkedin.com/jobs/view/4417070084 |
| Head of Product - Fintech | Unknown | Unknown | linkedin | Unknown | Unknown | blocked_geography,wrong_geography | https://www.linkedin.com/jobs/view/4417070084 |
| Product Lead (Афиша) | ТОО Ticketon Events | Алматы, проспект Жибек Ж | headhunter | 135 | Other | blocked_geography | https://hh.ru/vacancy/133470638 |
| Founding CPO → CEO для AI-стартапа в США | Aspirine HR | Unknown | headhunter | Unknown | Unknown | blocked_geography | https://hh.ru/vacancy/133184144 |
| Platform Architect/Архитектор платформы | YADRO | Unknown | headhunter | Unknown | Unknown | blocked_geography | https://hh.ru/vacancy/132307461 |
| Deputy Head of Cloud Products and Services at MUK Cloud | MUK | Kazakhstan (On-site) | linkedin | Kazakhstan | Kazakhstan | blocked_geography,onsite_requirement_mismatch,wrong_geography | https://www.linkedin.com/jobs/view/4401491114 |
| Deputy Head of Cloud Products and Services at MUK CloudDeput | Unknown | Unknown | linkedin | Unknown | Unknown | blocked_geography,wrong_geography | https://www.linkedin.com/jobs/view/4401491114 |
| Product Launch Manager (1 Position at Head Office) | CAMMA Corporate Learning and D | Phnom Penh, Phnom Penh,  | linkedin | Cambodia (On-site) | Other | blocked_geography,onsite_requirement_mismatch | https://www.linkedin.com/jobs/view/4338017837/ |
| Product Launch Manager (1 Position at Head Office)Product La | Unknown | Unknown | linkedin | Unknown | Unknown | blocked_geography | https://www.linkedin.com/jobs/view/4338017837/ |
| Chief Business Development Officer | Magnetto.com | Unknown | headhunter | Unknown | Unknown | blocked_geography,wrong_geography | https://hh.ru/vacancy/132653639 |
| Team Lead / Head of Product Marketing (Africa | iGaming) | Headshot | Unknown | headhunter | Unknown | Unknown | blocked_geography | https://hh.ru/vacancy/133107602 |
| Head of Data | ТОО Alem Agro Holding (АлемАгр | Алматы | headhunter | Алматы | Other | blocked_geography | https://hh.ru/vacancy/133143236 |
| Head of Product | АО CENTER FOR DIGITAL TECHNOLO | Ташкент, улица Махтумкул | headhunter | Ташкент | Other | blocked_geography | https://hh.ru/vacancy/133446873 |
| CMO (Chief Marketing Officer) | Банк | Алматы | headhunter | Алматы | Other | blocked_geography | https://hh.ru/vacancy/133504798 |
| Head of Cargo Marketing | Международный Аэропорт Алматы, | Алматы, аэропорт Алматы | headhunter | аэропорт Алматы | Other | blocked_geography | https://hh.ru/vacancy/132525450 |
| Head of Data and Analytics / Руководитель направления Данных | Novakid Inc | Unknown | headhunter | Unknown | Unknown | blocked_geography | https://hh.ru/vacancy/133223960 |
| Business Analyst with Fintech and Banking experience | Team.Inno / Фабрика инноваций  | Минск | headhunter | Минск | Other | blocked_geography,wrong_geography | https://hh.ru/vacancy/125440730 |
| Digital Marketing Specialist (Technical Software) / Специали | Esprow Pte Ltd | Unknown | headhunter | Unknown | Unknown | blocked_geography | https://hh.ru/vacancy/133665432 |
| Senior Growth Manager (Performance & Analytics) | ООО Бринго | Unknown | headhunter | Unknown | Unknown | blocked_geography,wrong_geography | https://hh.ru/vacancy/133515581 |
| International Expansion Lead (IT & Product Companies) | ООО Технологический парк прогр | Ташкент, улица Муминова, | headhunter | Ташкент | Other | blocked_geography,wrong_geography | https://hh.ru/vacancy/133043996 |
| AI Product & Operations Lead | ИП Иванова Марина Николаевна | Unknown | headhunter | Unknown | Unknown | blocked_geography | https://hh.ru/vacancy/133486511 |
| Product Operations Manager | Morph | Remote | linkedin | Remote | Remote | blocked_geography | https://www.linkedin.com/jobs/view/4410293868/ |
| Product Operations ManagerProduct Operations Manager | Unknown | Unknown | linkedin | Unknown | Unknown | blocked_geography | https://www.linkedin.com/jobs/view/4410293868/ |
| Senior Product Analyst | ТОО Freedom Media | Алматы, улица Сергея Луг | headhunter | Алматы | Other | blocked_geography | https://hh.ru/vacancy/132390785 |
| Product Marketing Lead | BI Group. BI Development | Unknown | headhunter | Unknown | Unknown | blocked_geography | https://hh.ru/vacancy/133399864 |
| Product Marketing Manager (ВНЕ РФ И РБ) | SpaceHub | Unknown | headhunter | Unknown | Unknown | blocked_geography | https://hh.ru/vacancy/133397975 |
| Data Analytics Product Owner | EPAM Systems | Remote | linkedin | Remote | Remote | blocked_geography,wrong_geography | https://www.linkedin.com/jobs/view/4413685216 |
| Data Analytics Product Owner Data Analytics Product Owner wi | Unknown | Unknown | linkedin | Unknown | Unknown | blocked_geography,wrong_geography | https://www.linkedin.com/jobs/view/4413685216 |
| Product Owner (e-com) / Менеджер ИТ продукта | Панда Гифтс | Unknown | headhunter | Unknown | Unknown | blocked_geography | https://hh.ru/vacancy/133549827 |
| Product Owner | ООО ACCESA. Frontend | Unknown | headhunter | Unknown | Unknown | blocked_geography | https://hh.ru/vacancy/133011441 |
| Product Owner CVM | АО Народный банк Казахстана | Unknown | headhunter | Unknown | Unknown | blocked_geography | https://hh.ru/vacancy/133657225 |
| Product Owner «Счета и остатки» | АО Народный банк Казахстана | Unknown | headhunter | Unknown | Unknown | blocked_geography | https://hh.ru/vacancy/133362004 |
| Data Analyst / Product Owner | ТОО 4FINANCE DIGITAL KZ | Кейптаун | headhunter | Кейптаун | Other | blocked_geography | https://hh.ru/vacancy/133148114 |

Rejection count by country:

| country | count |
| --- | --- |
| Unknown | 22 |
| Remote | 4 |
| Алматы | 3 |
| Ташкент | 2 |
| Kazakhstan | 1 |
| Cambodia (On-site) | 1 |
| Минск | 1 |
| 135 | 1 |
| Кейптаун | 1 |
| аэропорт Алматы | 1 |

Rejection count by region:

| region | count |
| --- | --- |
| Unknown | 22 |
| Other | 10 |
| Remote | 4 |
| Kazakhstan | 1 |

# Part 5) ATS Scoring Consistency Audit

Top 50 highest-scoring ATS vacancies for Greenhouse and Ashby. `ATS score` and `production score` are computed using the same `score_vacancy` function; differences should be zero. If production rejects while ATS validation 'accepted', that indicates post-score gates / filters beyond the score tier.

| ats | company | title | location | ats_score | prod_score | classification | fired_rules | decision | url |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| greenhouse | stripe | Account Executive, AI Sales | San Francisco, CA | 100 | 100 | product_strategy_and_growth | TEXT_HAS_STRATEGIC_SCOPE | accept | https://stripe.com/jobs/search |
| greenhouse | stripe | Account Executive,Iberia, Enterprise | Spain | 100 | 100 | product_strategy_and_growth | TEXT_HAS_STRATEGIC_SCOPE | accept | https://stripe.com/jobs/search |
| greenhouse | stripe | Account Executive, Platforms | Sydney Or Melbourne | 100 | 100 | product_strategy_and_growth | TEXT_HAS_STRATEGIC_SCOPE | accept | https://stripe.com/jobs/search |
| greenhouse | stripe | Account Executive, Platforms (Hunter) | Paris, Framce | 100 | 100 | product_strategy_and_growth | TEXT_HAS_STRATEGIC_SCOPE | accept | https://stripe.com/jobs/search |
| greenhouse | stripe | Account Executive, Product Sales - Capital | SF, NYC, SEA, CHI | 100 | 100 | product_strategy_and_growth | TEXT_HAS_STRATEGIC_SCOPE | accept | https://stripe.com/jobs/search |
| greenhouse | stripe | Account Executive, Product Sales - Payouts | SF, NYC | 100 | 100 | product_strategy_and_growth | TEXT_HAS_STRATEGIC_SCOPE | accept | https://stripe.com/jobs/search |
| greenhouse | stripe | Accounting Technical Solutions Lead | Seattle, San Francisco | 100 | 100 | product_strategy_and_growth | TEXT_HAS_STRATEGIC_SCOPE,TITLE_HAS_LEADERSHIP_TOKEN | accept | https://stripe.com/jobs/search |
| greenhouse | stripe | Accounts Receivable Analyst - Service Strategy Ena | Seattle, SF | 100 | 100 | product_strategy_and_growth | TEXT_HAS_STRATEGIC_SCOPE | accept | https://stripe.com/jobs/search |
| greenhouse | stripe | Business Development Manager, Agentic Commerce | San Francisco, CA; Sea | 100 | 100 | product_strategy_and_growth | TEXT_HAS_STRATEGIC_SCOPE | accept | https://stripe.com/jobs/search |
| greenhouse | stripe | Consumer Product Marketing Lead, Link | San Fransisco, New Yor | 100 | 100 | product_strategy_and_growth | TEXT_HAS_STRATEGIC_SCOPE,TITLE_HAS_LEADERSHIP_TOKEN | accept | https://stripe.com/jobs/search |
| greenhouse | stripe | Creative Technologist Brand Designer, Labs | NYC | 100 | 100 | product_strategy_and_growth | TEXT_HAS_STRATEGIC_SCOPE | accept | https://stripe.com/jobs/search |
| greenhouse | stripe | Data Science Manager, Risk | Bengaluru | 100 | 100 | product_strategy_and_growth | TEXT_HAS_STRATEGIC_SCOPE | accept | https://stripe.com/jobs/search |
| greenhouse | stripe | Data Scientist | Seattle, WA | 100 | 100 | product_strategy_and_growth | TEXT_HAS_STRATEGIC_SCOPE | accept | https://stripe.com/jobs/search |
| greenhouse | stripe | Engineering Manager, Terminal | Toronto, Canada | 100 | 100 | product_strategy_and_growth | TEXT_HAS_STRATEGIC_SCOPE | accept | https://stripe.com/jobs/search |
| greenhouse | stripe | Enterprise Risk Management Lead | Atlanta, GA | 100 | 100 | product_strategy_and_growth | TEXT_HAS_STRATEGIC_SCOPE,TITLE_HAS_LEADERSHIP_TOKEN | accept | https://stripe.com/jobs/search |
| greenhouse | stripe | Executive Briefing Manager | South San Francisco | 100 | 100 | product_strategy_and_growth | TEXT_HAS_STRATEGIC_SCOPE | accept | https://stripe.com/jobs/search |
| greenhouse | stripe | Finance & Strategy Lead, Payments Product | Seattle, Chicago, San  | 100 | 100 | product_strategy_and_growth | TEXT_HAS_STRATEGIC_SCOPE,TITLE_HAS_LEADERSHIP_TOKEN | accept | https://stripe.com/jobs/search |
| greenhouse | stripe | Fraud Operations Team Lead | Mexico City | 100 | 100 | product_strategy_and_growth | TEXT_HAS_STRATEGIC_SCOPE,TITLE_HAS_LEADERSHIP_TOKEN | accept | https://stripe.com/jobs/search |
| greenhouse | stripe | Global AML Lead, Risk Operations | United States | 100 | 100 | product_strategy_and_growth | TEXT_HAS_STRATEGIC_SCOPE,TITLE_HAS_LEADERSHIP_TOKEN | accept | https://stripe.com/jobs/search |
| greenhouse | stripe | Head of Connect & Crypto, Finance & Strategy | San Francisco, Seattle | 100 | 100 | executive_product_leadership | TITLE_HAS_PRODUCT_DOMAIN_AND_EXEC_SENIORITY | accept | https://stripe.com/jobs/search |
| greenhouse | datadog | Director, Product Management - Infrastructure Moni | New York, New York, US | 100 | 100 | executive_product_leadership | TITLE_HAS_PRODUCT_DOMAIN_AND_EXEC_SENIORITY | accept | https://careers.datadoghq.com/detail/7899504/ |
| greenhouse | datadog | Distinguished Architect, AI | New York, New York, US | 100 | 100 | product_strategy_and_growth | TEXT_HAS_STRATEGIC_SCOPE | accept | https://careers.datadoghq.com/detail/7966795/ |
| greenhouse | datadog | Field CTO (Japan) | Tokyo, Japan | 100 | 100 | product_strategy_and_growth | TEXT_HAS_STRATEGIC_SCOPE | accept | https://careers.datadoghq.com/detail/7526356/ |
| greenhouse | datadog | Inclusion Program Manager (NYC) | New York, New York, US | 100 | 100 | product_strategy_and_growth | TEXT_HAS_STRATEGIC_SCOPE | accept | https://careers.datadoghq.com/detail/7874842/ |
| greenhouse | datadog | Manager I, Engineering - Subscriptions | New York, New York, US | 100 | 100 | product_strategy_and_growth | TEXT_HAS_STRATEGIC_SCOPE | accept | https://careers.datadoghq.com/detail/7761803/ |
| greenhouse | figma | Brand Producer | San Francisco, CA • Ne | 100 | 100 | product_strategy_and_growth | TEXT_HAS_STRATEGIC_SCOPE | accept | https://boards.greenhouse.io/figma/jobs/6000002004 |
| greenhouse | figma | Compensation Partner | San Francisco, CA • Ne | 100 | 100 | product_strategy_and_growth | TEXT_HAS_STRATEGIC_SCOPE | accept | https://boards.greenhouse.io/figma/jobs/5893911004 |
| greenhouse | figma | Corporate Development & Strategy, M&A Integration | San Francisco, CA • Ne | 100 | 100 | product_strategy_and_growth | TEXT_HAS_STRATEGIC_SCOPE | accept | https://boards.greenhouse.io/figma/jobs/5699991004 |
| greenhouse | figma | Director, Product - Enterprise | San Francisco, CA • Ne | 100 | 100 | executive_product_leadership | TITLE_HAS_PRODUCT_DOMAIN_AND_EXEC_SENIORITY | accept | https://boards.greenhouse.io/figma/jobs/6002024004 |
| greenhouse | figma | Manager, Figma for Education (International) | San Francisco, CA • Ne | 100 | 100 | product_strategy_and_growth | TEXT_HAS_STRATEGIC_SCOPE,TITLE_HAS_LEADERSHIP_TOKEN | accept | https://boards.greenhouse.io/figma/jobs/6004606004 |
| greenhouse | figma | Manager, Software Engineering - Billing | San Francisco, CA • Ne | 100 | 100 | product_strategy_and_growth | TEXT_HAS_STRATEGIC_SCOPE | accept | https://boards.greenhouse.io/figma/jobs/5722244004 |
| greenhouse | figma | People Partner, Engineering | San Francisco, CA • Ne | 100 | 100 | product_strategy_and_growth | TEXT_HAS_STRATEGIC_SCOPE | accept | https://boards.greenhouse.io/figma/jobs/6004746004 |
| greenhouse | figma | People Partner, Sales | San Francisco, CA • Ne | 100 | 100 | product_strategy_and_growth | TEXT_HAS_STRATEGIC_SCOPE | accept | https://boards.greenhouse.io/figma/jobs/5831836004 |
| greenhouse | figma | Product Designer, Growth & Monetization | San Francisco, CA • Ne | 100 | 100 | product_strategy_and_growth | TEXT_HAS_STRATEGIC_SCOPE | accept | https://boards.greenhouse.io/figma/jobs/5711595004 |
| greenhouse | figma | Product Marketing Manager, Monetization | San Francisco, CA • Ne | 100 | 100 | product_strategy_and_growth | TEXT_HAS_STRATEGIC_SCOPE | accept | https://boards.greenhouse.io/figma/jobs/5989134004 |
| greenhouse | figma | Product Partner Manager | San Francisco, CA • Ne | 100 | 100 | product_strategy_and_growth | TEXT_HAS_STRATEGIC_SCOPE | accept | https://boards.greenhouse.io/figma/jobs/5822583004 |
| greenhouse | figma | Researcher, Core Product Strategy | San Francisco, CA • Ne | 100 | 100 | product_strategy_and_growth | TEXT_HAS_STRATEGIC_SCOPE | accept | https://boards.greenhouse.io/figma/jobs/5651744004 |
| greenhouse | figma | Sales Operations Manager | San Francisco, CA • Ne | 100 | 100 | product_strategy_and_growth | TEXT_HAS_STRATEGIC_SCOPE | accept | https://boards.greenhouse.io/figma/jobs/5839419004 |
| greenhouse | figma | Sales Operations Manager, New Business | San Francisco, CA • Ne | 100 | 100 | product_strategy_and_growth | TEXT_HAS_STRATEGIC_SCOPE | accept | https://boards.greenhouse.io/figma/jobs/5998224004 |
| greenhouse | figma | Software Engineer, Full Stack | San Francisco, CA • Ne | 100 | 100 | product_strategy_and_growth | TEXT_HAS_STRATEGIC_SCOPE | accept | https://boards.greenhouse.io/figma/jobs/5691911004 |
| greenhouse | figma | Software Engineer, Growth & Monetization | San Francisco, CA • Ne | 100 | 100 | product_strategy_and_growth | TEXT_HAS_STRATEGIC_SCOPE | accept | https://boards.greenhouse.io/figma/jobs/5552560004 |
| greenhouse | figma | Strategic Finance, Systems & AI Innovation | San Francisco, CA • Ne | 100 | 100 | product_strategy_and_growth | TEXT_HAS_STRATEGIC_SCOPE | accept | https://boards.greenhouse.io/figma/jobs/5998049004 |
| greenhouse | figma | UX Writer, AI | San Francisco, CA • Ne | 100 | 100 | product_strategy_and_growth | TEXT_HAS_STRATEGIC_SCOPE | accept | https://boards.greenhouse.io/figma/jobs/5839202004 |
| greenhouse | stripe | Account Executive, Platforms (Existing Business) | San Francisco, CA; Chi | 97 | 97 | product_strategy_and_growth | TEXT_HAS_STRATEGIC_SCOPE | accept | https://stripe.com/jobs/search |
| greenhouse | stripe | Account Executive, Platforms (French Speaking) | Dublin, Paris, France | 97 | 97 | product_strategy_and_growth | TEXT_HAS_STRATEGIC_SCOPE | accept | https://stripe.com/jobs/search |
| greenhouse | stripe | Account Executive, Startup Platforms | San Francisco | 97 | 97 | product_strategy_and_growth | TEXT_HAS_STRATEGIC_SCOPE | accept | https://stripe.com/jobs/search |
| greenhouse | stripe | Account Executive, Velocity Platforms (Grower) | New York City | 97 | 97 | product_strategy_and_growth | TEXT_HAS_STRATEGIC_SCOPE | accept | https://stripe.com/jobs/search |
| greenhouse | stripe | Account Executive, Velocity Platforms (Grower) | Chicago | 97 | 97 | product_strategy_and_growth | TEXT_HAS_STRATEGIC_SCOPE | accept | https://stripe.com/jobs/search |
| greenhouse | stripe | Accounts Receivable Analyst | Bengaluru | 97 | 97 | product_strategy_and_growth | TEXT_HAS_STRATEGIC_SCOPE | accept | https://stripe.com/jobs/search |
| greenhouse | stripe | AI Specialist, Treasury Finance Operations | IN-Bengaluru | 97 | 97 | product_strategy_and_growth | TEXT_HAS_STRATEGIC_SCOPE | accept | https://stripe.com/jobs/search |
| ashby | openai | Software Engineer, Financial Engineering | San Francisco | 100 | 100 | product_strategy_and_growth | TEXT_HAS_STRATEGIC_SCOPE | accept | https://jobs.ashbyhq.com/openai/4ef5bf23-cf0e-4b97-a639-11f963c99b88 |
| ashby | openai | Data Scientist, Financial Engineering | San Francisco | 100 | 100 | product_strategy_and_growth | TEXT_HAS_STRATEGIC_SCOPE | accept | https://jobs.ashbyhq.com/openai/898a87fb-4cb8-450e-9840-ee5dc710a57d |
| ashby | openai | Trust & Safety Operations Analyst, Ads | San Francisco | 100 | 100 | product_strategy_and_growth | TEXT_HAS_STRATEGIC_SCOPE | accept | https://jobs.ashbyhq.com/openai/c9e9e3a5-fb93-4162-b876-6266016819c0 |
| ashby | openai | Software Engineer, Monetization Product & Platform | San Francisco | 100 | 100 | product_strategy_and_growth | TEXT_HAS_STRATEGIC_SCOPE | accept | https://jobs.ashbyhq.com/openai/c51e48ce-31ad-4176-a6d5-4a785b44ab73 |
| ashby | openai | Pricing Strategist | San Francisco | 100 | 100 | product_strategy_and_growth | TEXT_HAS_STRATEGIC_SCOPE | accept | https://jobs.ashbyhq.com/openai/3ab0541b-160b-49c0-8609-574db6358332 |
| ashby | openai | Frontend Engineer, Financial Web Platform | San Francisco | 100 | 100 | product_strategy_and_growth | TEXT_HAS_STRATEGIC_SCOPE | accept | https://jobs.ashbyhq.com/openai/192980b8-b874-4493-a8c1-f7d5660f00f3 |
| ashby | ramp | Customer Activation Manager | Enterprise | New York, NY (HQ) | 100 | 100 | product_strategy_and_growth | TEXT_HAS_STRATEGIC_SCOPE | accept | https://jobs.ashbyhq.com/ramp/09a9381c-677b-40a5-9ff1-027bd4302c13 |
| ashby | ramp | Customer Activation Manager | Mid-Market | New York, NY (HQ) | 100 | 100 | product_strategy_and_growth | TEXT_HAS_STRATEGIC_SCOPE | accept | https://jobs.ashbyhq.com/ramp/8086c65e-b4cc-4bdd-8f1f-5bdf03130ff4 |
| ashby | ramp | Customer Activation Manager | Strategic Enterprise | New York, NY (HQ) | 100 | 100 | product_strategy_and_growth | TEXT_HAS_STRATEGIC_SCOPE | accept | https://jobs.ashbyhq.com/ramp/f330ccd7-59f5-4032-82b0-4448482769e4 |
| ashby | ramp | Software Engineer, Accounting | New York, NY (HQ) | 100 | 100 | product_strategy_and_growth | TEXT_HAS_STRATEGIC_SCOPE | accept | https://jobs.ashbyhq.com/ramp/ed2e9a94-f58e-4ff8-8853-afd977850d43 |
| ashby | openai | Industry Product Marketing Manager | San Francisco | 97 | 97 | product_strategy_and_growth | TEXT_HAS_STRATEGIC_SCOPE | accept | https://jobs.ashbyhq.com/openai/0e078d07-fb0b-4893-b90a-f50e413f3e13 |
| ashby | openai | Growth, Korea | Seoul, South Korea | 97 | 97 | product_strategy_and_growth | TEXT_HAS_STRATEGIC_SCOPE | accept | https://jobs.ashbyhq.com/openai/c6ce7090-9061-4dae-90d5-3e120f179a39 |
| ashby | ramp | Software Engineer, Argentina | Remote (Buenos Aires,  | 97 | 97 | product_strategy_and_growth | TEXT_HAS_STRATEGIC_SCOPE | accept | https://jobs.ashbyhq.com/ramp/9320454f-f2ca-4c19-82d2-b51b8d75fd3a |
| ashby | ramp | Software Engineer, Ramp Travel | New York, NY (HQ) | 97 | 97 | product_strategy_and_growth | TEXT_HAS_STRATEGIC_SCOPE | accept | https://jobs.ashbyhq.com/ramp/4bb1ccd6-cf0d-48a8-90f6-05d61617d0d4 |
| ashby | ramp | Senior Applied Scientist, Credit Risk | New York, NY (HQ) | 97 | 97 | product_strategy_and_growth | TEXT_HAS_STRATEGIC_SCOPE | accept | https://jobs.ashbyhq.com/ramp/2888b101-b1da-4e53-a02e-1bb9b1b5a951 |
| ashby | cursor | Software Engineer, Growth | San Francisco | 97 | 97 | product_strategy_and_growth | TEXT_HAS_STRATEGIC_SCOPE | accept | https://jobs.ashbyhq.com/cursor/0ec39ed7-a5dc-4551-bb26-b7f4f9fb4a74 |
| ashby | ramp | Mobile Engineer, Android | New York, NY (HQ) | 92 | 92 | product_strategy_and_growth | TEXT_HAS_STRATEGIC_SCOPE | accept | https://jobs.ashbyhq.com/ramp/f564dcf9-9390-4a3f-896f-8047a5086040 |
| ashby | ramp | Mobile Engineer, iOS | New York, NY (HQ) | 92 | 92 | product_strategy_and_growth | TEXT_HAS_STRATEGIC_SCOPE | accept | https://jobs.ashbyhq.com/ramp/4859cd5e-f2a9-44d7-81f7-8bfc0e62369f |
| ashby | ramp | Product Operations Specialist | Accounting | New York, NY (HQ) | 92 | 92 | product_strategy_and_growth | TEXT_HAS_STRATEGIC_SCOPE | accept | https://jobs.ashbyhq.com/ramp/b830aa03-c897-4f7d-80c1-047b529576e1 |
| ashby | ramp | Integrations Expert | Customer Experience | New York, NY (HQ) | 92 | 92 | product_strategy_and_growth | TEXT_HAS_STRATEGIC_SCOPE | accept | https://jobs.ashbyhq.com/ramp/910f3de4-8492-47ca-b55c-6cea0ab64b1a |
| ashby | ramp | Customer Activation Manager | Public Sector | Washington, D.C. | 92 | 92 | product_strategy_and_growth | TEXT_HAS_STRATEGIC_SCOPE | accept | https://jobs.ashbyhq.com/ramp/ab6902bd-544e-4107-bf3b-b2157ee84aee |
| ashby | ramp | Account Manager | Enterprise | New York, NY (HQ) | 92 | 92 | product_strategy_and_growth | TEXT_HAS_STRATEGIC_SCOPE | accept | https://jobs.ashbyhq.com/ramp/f5cf4790-0b77-47be-b332-02ca6ec755fd |
| ashby | ramp | Software Engineer, Core Product | New York, NY (HQ) | 92 | 92 | product_strategy_and_growth | TEXT_HAS_STRATEGIC_SCOPE | accept | https://jobs.ashbyhq.com/ramp/5fe4c64e-9336-4384-9e6f-ff32eeb3fdae |
| ashby | ramp | Software Engineer, Bill Pay & Procurement | New York, NY (HQ) | 92 | 92 | product_strategy_and_growth | TEXT_HAS_STRATEGIC_SCOPE | accept | https://jobs.ashbyhq.com/ramp/2a4968ae-220c-471b-b890-a011de570bbb |
| ashby | ramp | Senior Account Manager | Commercial | New York, NY (HQ) | 92 | 92 | product_strategy_and_growth | TEXT_HAS_STRATEGIC_SCOPE | accept | https://jobs.ashbyhq.com/ramp/c539b349-d570-41a8-824f-94fc9729caa6 |
| ashby | ramp | Channel Partner Manager, Juno | New York, NY (HQ) | 92 | 92 | product_strategy_and_growth | TEXT_HAS_STRATEGIC_SCOPE | accept | https://jobs.ashbyhq.com/ramp/bf7064e1-3eb3-4d03-ac81-0e52b71661a4 |
| ashby | ramp | Technical Channel Partner Manager | ISV | New York, NY (HQ) | 92 | 92 | product_strategy_and_growth | TEXT_HAS_STRATEGIC_SCOPE | accept | https://jobs.ashbyhq.com/ramp/6c68ed4b-dab8-4e2d-b677-a7b6ddca0272 |
| ashby | ramp | Senior Technical Channel Partner Manager | ISV | New York, NY (HQ) | 92 | 92 | product_strategy_and_growth | TEXT_HAS_STRATEGIC_SCOPE | accept | https://jobs.ashbyhq.com/ramp/b8291945-567a-4ed6-8a06-3deab4e18fd7 |
| ashby | ramp | Software Engineer, Production Engineering | New York, NY (HQ) | 92 | 92 | product_strategy_and_growth | TEXT_HAS_STRATEGIC_SCOPE | accept | https://jobs.ashbyhq.com/ramp/be496b52-cfbf-494e-b862-61fb4a188b24 |
| ashby | openai | AI Deployment Engineer- Codex | Remote - US | 87 | 87 | product_strategy_and_growth | TEXT_HAS_STRATEGIC_SCOPE | accept | https://jobs.ashbyhq.com/openai/4f4221ef-f2bc-4dc1-9086-69ba126e4903 |
| ashby | openai | Strategic Finance, GTM | San Francisco | 87 | 87 | product_strategy_and_growth | TEXT_HAS_STRATEGIC_SCOPE | accept | https://jobs.ashbyhq.com/openai/4f1870f0-b0a8-44fb-b6fe-28638b45ae47 |
| ashby | openai | Account Director, Financial Services | New York City | 85 | 85 | product_strategy_and_growth | TEXT_HAS_STRATEGIC_SCOPE,TITLE_HAS_LEADERSHIP_TOKEN | accept | https://jobs.ashbyhq.com/openai/9b8b0edd-89e3-43d2-8b34-65df8e55ca79 |
| ashby | openai | Research Engineer, Retrieval & Search, Applied Eng | San Francisco | 82 | 82 | product_strategy_and_growth | TEXT_HAS_STRATEGIC_SCOPE | accept | https://jobs.ashbyhq.com/openai/7322d344-9325-4a92-8445-0a2c4e9272f8 |
| ashby | openai | Technical Threat Investigator, Threat Intel Engine | San Francisco | 82 | 82 | product_strategy_and_growth | TEXT_HAS_STRATEGIC_SCOPE | accept | https://jobs.ashbyhq.com/openai/f01b7084-a68d-4e30-ace9-6b5e6d90c517 |
| ashby | openai | Software Engineer, Identity Infrastructure Enginee | San Francisco | 82 | 82 | product_strategy_and_growth | TEXT_HAS_STRATEGIC_SCOPE | accept | https://jobs.ashbyhq.com/openai/551b0d0d-46c2-42fb-bb05-46e2fba8d4db |
| ashby | openai | Regional Client Partner, Ads Solutions (Spanish Sp | Remote - US | 82 | 82 | product_strategy_and_growth | TEXT_HAS_STRATEGIC_SCOPE | accept | https://jobs.ashbyhq.com/openai/c9c7c6af-7d22-4f14-b6ef-99c7ea66a151 |
| ashby | openai | Technical Abuse Investigator | San Francisco | 82 | 82 | product_strategy_and_growth | TEXT_HAS_STRATEGIC_SCOPE | accept | https://jobs.ashbyhq.com/openai/31f19fa3-d8c1-4db2-b336-ab046f9cde41 |
| ashby | openai | AI Deployment Engineer- Codex | San Francisco | 82 | 82 | product_strategy_and_growth | TEXT_HAS_STRATEGIC_SCOPE | accept | https://jobs.ashbyhq.com/openai/ca7e4019-bf93-42fd-8f15-fac59c6e237c |
| ashby | openai | AI Deployment Engineer- ChatGPT Ecosystem | Remote - US | 82 | 82 | product_strategy_and_growth | TEXT_HAS_STRATEGIC_SCOPE | accept | https://jobs.ashbyhq.com/openai/387e28ec-a4db-42da-9448-b6dbb7b13901 |
| ashby | ramp | Partner Consultant | Remote (US) | 82 | 82 | product_strategy_and_growth | TEXT_HAS_STRATEGIC_SCOPE | accept | https://jobs.ashbyhq.com/ramp/c63ba7d7-5290-4d9b-b002-40b2873b66f6 |
| ashby | cursor | Commercial Expansion Account Executive | San Francisco | 82 | 82 | product_strategy_and_growth | TEXT_HAS_STRATEGIC_SCOPE | accept | https://jobs.ashbyhq.com/cursor/85c98417-583b-412e-b31a-e4052195b703 |
| ashby | cursor | Engineering Manager, Desktop | San Francisco | 82 | 82 | product_strategy_and_growth | TEXT_HAS_STRATEGIC_SCOPE | accept | https://jobs.ashbyhq.com/cursor/f27e0997-e283-4015-be61-e13d844a2834 |
| ashby | cursor | Solutions Architect | Remote | 82 | 82 | product_strategy_and_growth | TEXT_HAS_STRATEGIC_SCOPE | accept | https://jobs.ashbyhq.com/cursor/cda9256f-e820-4f0a-9e77-e44c61295e1d |
| ashby | ramp | Customer Experience Associate | New York, NY (HQ) | 80 | 80 | product_strategy_and_growth | TEXT_HAS_STRATEGIC_SCOPE | accept | https://jobs.ashbyhq.com/ramp/d64609dd-c391-45a2-bfdb-c1bb34e8f93c |
| ashby | ramp | University Grad | Customer Experience Associate | New York, NY (HQ) | 80 | 80 | product_strategy_and_growth | TEXT_HAS_STRATEGIC_SCOPE | accept | https://jobs.ashbyhq.com/ramp/fc971889-db1d-4a20-a25e-f282f9296936 |
| ashby | ramp | Customer Experience Associate (Evening Shift) | New York, NY (HQ) | 80 | 80 | product_strategy_and_growth | TEXT_HAS_STRATEGIC_SCOPE | accept | https://jobs.ashbyhq.com/ramp/40f43993-21e8-4db1-a9f4-7a3e6098a9ba |
| ashby | ramp | Customer Experience Associate - London | London | 80 | 80 | product_strategy_and_growth | TEXT_HAS_STRATEGIC_SCOPE | accept | https://jobs.ashbyhq.com/ramp/0d731586-3626-48fb-9dc5-3061ae5fd240 |
| ashby | openai | Software Engineer, Data Infrastructure | San Francisco | 77 | 77 | product_strategy_and_growth | TEXT_HAS_STRATEGIC_SCOPE | accept | https://jobs.ashbyhq.com/openai/f763c6b3-5167-4a67-b691-4c3fa2c44156 |
| ashby | openai | Data Engineer | San Francisco | 77 | 77 | product_strategy_and_growth | TEXT_HAS_STRATEGIC_SCOPE | accept | https://jobs.ashbyhq.com/openai/fc5bbc77-a30c-4e7a-9acc-8a2e748545b4 |
| ashby | openai | Software Engineer, Observability | San Francisco | 77 | 77 | product_strategy_and_growth | TEXT_HAS_STRATEGIC_SCOPE | accept | https://jobs.ashbyhq.com/openai/d4dcd344-40cf-44d6-a7dd-172118eb0842 |

# Part 6) Decision Transparency Proposal (Design Only)

Proposed decision buckets per run (separate from technical KPI):

- **Strong Fit**: `tier in {exceptional_fit, strong_fit}` AND no hard rejection reasons.
- **Potential Fit**: `tier == possible_fit` OR `tier == weak_fit` but no hard rejection reasons; requires review.
- **Near Miss**: rejected, but `score within 10 points of threshold` OR rejected for a single soft reason.

Proposed daily user report layout:
1. Funnel by source: found -> executive_detected -> scored -> accepted -> notified
2. Strong Fit (top 20)
3. Potential Fit (top 20)
4. Near Miss (top 20, with explicit reasons)
5. Rejection Intelligence: top reasons (counts + examples)

Sample (illustrative):
- Strong Fit: Company X | VP Product | score=82 | why: executive visibility + product ownership + fintech adjacency
- Near Miss: Company Y | Head of Product | score=55 (threshold 60) | rejected: wrong_geography

