# Career Preference Model — Migration Map (Step 1, evidence-based)

**Дата:** 2026-07-19. Все пути проверены в фактическом коде на canonical host
(`/home/hermes/.hermes/hermes-agent`, ветка `local/customizations`).
**Ничто из перечисленного в Step 1 не изменено** — карта фиксирует будущую
миграцию. Legacy-конфиги не удаляются до утверждённой migration strategy.

Формат: current source/rule → normalized SoT field → future consumer →
migration status → conflict/deprecation risk.

## 1. Seed-конфиги (`job_intel/seed/`)

| Current source | Normalized SoT | Future consumer | Status | Risk |
|---|---|---|---|---|
| `search_criteria.yaml: role_titles.high_priority` (9 титулов) | `fc_scope_below_executive` (scope-based) + titles как evidence в Step 2 extraction | shadow evaluator | pending | **конфликт**: title-список — hard truth в текущем поиске; SoT понижает титулы до evidence. Поисковые queries могут остаться title-based (это recall-механика), но verdict-логика должна перейти на scope |
| `search_criteria.yaml: business_models.preferred` (incl. **fintech**) | `business_model_shape` (fintech удалён: индустрия нейтральна) | evaluator, universe | pending | fintech в preferred прямо противоречит SoT-принципу «industry = neutral» — deprecation при миграции |
| `scoring.yaml: positive_signals` (13 весов, incl. `fintech_or_telecom: 15`) | mandate/company preferences (качественные strength, без чисел); `fintech_or_telecom` не переносится | Step 3 shadow evaluator | pending | численная калибровка — engine concern; прямой перенос весов запрещён (No architecture drift) |
| `scoring.yaml: negative_signals` (7) | `anti_preferences` (outsourcing_company, mature_bureaucratic_org, …) + `fc_function_digital_business_ownership` (pure_project_management/delivery_only) | shadow evaluator | pending | двойной штраф: negative_signals пересекаются с гейтами — SoT разводит их по level/tier |
| `scoring.yaml: thresholds` | не переносится (band-семантика — решение Step 3/5) | selection policy | not planned in SoT | — |
| `candidate.yaml: seniority.level [VP, Director, C-level]` | `fc_scope_below_executive` | shadow evaluator | pending | как у role_titles |
| `target_companies.yaml` (~20, ~50% fintech) | не входит в preference SoT; будущий company-universe процесс должен фильтроваться через `company_preferences` + `anti_preferences.company` | universe discovery | pending | **RC2-риск**: сохранение fintech-anchor списка воспроизводит bias |
| `company_red_flags.yaml` | частично → `anti_preferences` (company level) | company intel | pending | дубли сигналов с scoring.negative_signals |

## 2. Hardcoded rules в коде

| Current source | Normalized SoT | Future consumer | Status | Risk |
|---|---|---|---|---|
| `evaluator.py:446-596` (v1 литеральные веса), `:628-639` (v2 `W_*`, incl. `W_FINTECH=18`, `W_NON_PRODUCT_PENALTY=-35`) | качественные preferences; fintech-вес не переносится; non-product → `fc_function_digital_business_ownership` (с hybrid-исключением `ir_hybrid_gm_roles_allowed`) | Step 3 shadow evaluator (новый код) | pending | production evaluator НЕ трогаем до Step 5; риск расхождения shadow vs legacy — измеряется disagreement-отчётом Step 3 |
| `evaluator.py` substring «fintech\|telecom» +15/+18 | удаляется (industry neutral) | — | deprecate at Step 5 | главный источник fintech-bias |
| гео-штрафы RU/BY −60 (двойное начисление, аудит §2.2) | `fc_sanctioned_geo` (одно правило, verdict, не score) | shadow evaluator | pending | SoT-принцип «no double penalties» |
| `universe/anchors.py: BEHAVIORAL_ANCHORS=["wise","airwallex"]`, editorial/anchor_similar (8/8 fintech), fintech-gated buckets | company discovery должен якориться на `company_preferences` свойства (scale/brand/stage/platform), не industry-similarity | universe discovery | pending | **RC2**: прямое противоречие SoT; до миграции не расширять вселенную |
| `observability.py:253-267` industry-классификатор (first-match, crypto→Fintech) | Step 2 feature schema: sub-industry (crypto ≠ payments) | extraction (Step 2) | pending | без раздельного crypto-признака `crypto_exchange_employer` невыразим |

## 3. Feedback / calibration

| Current source | Normalized SoT | Future consumer | Status | Risk |
|---|---|---|---|---|
| `feedback_taxonomy.py: REASON_TO_SCORING_FEATURE` (reason→feature map) | Step 5 feedback semantics: классификация not_interesting по SoT-осям (mandate/company/feasibility/sponsorship/…) | feedback closure (Step 5) | pending | текущий map указывает на несуществующие фичи scoring.yaml (аудит RC6) — при миграции пересобрать против SoT-осей |
| `feedback_taxonomy.py: DEFAULT_HARD_BLOCKER_CODES` | `feasibility_constraints` (verdict infeasible) | review/feedback | pending | категории vs detail-коды рассинхронизированы (RC6) |
| `calibration.py` (propose→apply в scoring.yaml) | `change_policy` (proposal-only, owner approval) — механизм совместим идеологически | preference proposal loop (Step 5) | pending | calibration пишет в scoring.yaml, будущие proposals должны писать в SoT YAML через новый workflow |

## 4. Career-facts / материалы (data dir, вне репо)

| Current source | Normalized SoT | Future consumer | Status | Risk |
|---|---|---|---|---|
| `~/.hermes/job_intel/career_facts/preferences.yaml` (self-declared: target roles, веса-копии scoring, red flags, geo exclusions) | explicit-provenance правила SoT (`ev_preferences_yaml`); веса-копии не переносятся | recruiter decision support, материалы | pending | **дубликация**: файл дублирует seed/scoring — после миграции он должен стать производным от SoT или ссылаться на него; сейчас читается recruiter-skills — не трогать |
| `~/.hermes/job_intel/career_facts/career_facts.json` v1.1 | остаётся отдельным SoT фактов карьеры; preference model ссылается (`ev_career_facts`) | CV tailoring | keep as-is | — |
| `docs/hermes_vacancy_materials_sot.md` | мотивации/позиционирование согласованы (`ev_materials_sot`) | материалы | keep as-is | расхождений не обнаружено (research §14.2) |

## 5. Сводка рисков deprecation

1. **fintech как preferred/anchored** — везде (search_criteria, scoring, target_companies, anchors) противоречит SoT; удалять только на Step 5 с shadow-evidence.
2. **Title-based логика** — понижение титулов до evidence требует рабочего extraction (Step 2) до отключения title-правил.
3. **Двойные штрафы** — при миграции каждый сигнал должен получить ровно одну точку приложения (level/tier в SoT).
4. **Config-дубликация** (`career_facts/preferences.yaml` ↔ `seed/*`) — консолидировать после Step 3, не раньше.
