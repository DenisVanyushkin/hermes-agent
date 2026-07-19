# Vacancy Understanding — Migration Inventory (Step 2, evidence-based)

**Дата:** 2026-07-19. Пути проверены в коде на canonical host
(`local/customizations`). Legacy-поля в Step 2 НЕ изменены.

Формат: legacy source → canonical field → migration status → data-loss risk →
confidence risk → future consumer.

## 1. Ingestion / normalized vacancy (`job_intel/models.py: Vacancy`, `sources.py`, `ats_sources.py`)

| Legacy | Canonical | Status | Data-loss | Confidence | Consumer |
|---|---|---|---|---|---|
| `Vacancy.title` | `role_identity.raw_title/normalized_title` + families | pending | нет | title-only деривации капятся medium | Step 3, материалы |
| `Vacancy.location` (свободная строка) | `feasibility_facts.city/country/country_group/work_format` | pending | **строка неструктурирована**: часть локаций не парсится (напр. «London» без страны → country unknown) | country_group unknown до enrichment | Step 3 гейты |
| `Vacancy.description` | вход экстрактора; bounded excerpts в evidence | pending | полные тексты у части источников отсутствуют (Wise: title-echo, dlen<70) → source_text_incomplete | semantic-факты недоступны без текста | всё |
| `Vacancy.salary` | сознательно НЕ маппится (comp вне Step 2/3; 0 заполненных в БД) | not planned | — | — | — |
| `Vacancy.metadata` (dict) | частично → structured evidence | pending | разнородность по источникам | | |

## 2. Evaluator inputs (`evaluator.py`)

| Legacy | Canonical | Status | Data-loss | Confidence | Consumer |
|---|---|---|---|---|---|
| substring-сигналы v1 (`fintech_or_telecom` +15, «growth» и пр.) и `W_*` v2 | НЕ мигрируются как значения; смысловые аналоги — mandate.* факты с evidence | deprecate at Step 5 | keyword-хиты без evidence теряются намеренно | у legacy-сигналов нулевая дискриминация (audit §5.1) | — |
| `Evaluation.matched_signals/concerns/reasons` | `mandate.*` + `risks[]` (типизированные) | pending | свободный текст → enum: хвост в `RiskKind.other` | | Step 3 объяснения |
| `Evaluation.score/tier/recommendation` | НЕ мигрируются (вердикты запрещены в этом слое) | never | — | — | Step 3 сравнивает как legacy-референс |
| geo-штрафы RU/BY −60 (двойное начисление) | `country_groups.py` резолвер + факт country_group | pending | — | резолвер объясним и версионирован | Step 3 feasibility |

## 3. Observability (`observability.py`)

| Legacy | Canonical | Status | Data-loss | Confidence | Consumer |
|---|---|---|---|---|---|
| `industry_bucket` (first-match, crypto→Fintech) | `company.*` факты; sub-industry различение через `is_crypto_exchange` | pending | бакеты грубее фактов — обратная совместимость отчётов | first-match ошибки уходят | drift-панель |
| `role_bucket` (cpo/vp/head/director) | `role_identity.management_level_observed` | pending | нет | title-cap medium | |
| `geo_bucket` | `feasibility_facts.country_group` | pending | нет | | |
| `executive_detected` | производная от management_level + mandate | pending | | | |

## 4. Company intelligence (`company_intel.py`, `universe/*`, `seed/target_companies.yaml`)

| Legacy | Canonical | Status | Data-loss | Confidence | Consumer |
|---|---|---|---|---|---|
| company-инфо в universe-отчётах | `company.*` (scale/stage/brand/…) c `company_enrichment` evidence | pending | | требует источник, отдельный от текста | Step 3 company_fit |
| fintech-якоря/бакеты | НЕ мигрируются (industry ≠ признак) | deprecate at Step 5 | | | |

## 5. Feedback taxonomy (`feedback_taxonomy.py`)

| Legacy | Canonical | Status | Data-loss | Confidence | Consumer |
|---|---|---|---|---|---|
| категории/detail-коды 👎 | будущая классификация not_interesting по осям SoT; вакансийная сторона — canonical mandate/feasibility факты | Step 5 | коды с несуществующими фичами (RC6) пересобрать | | preference proposal loop |
| `scoring_features_impacted_json` (никем не читается) | заменяется ссылками на canonical поля | Step 5 | мёртвые данные | | |

## 6. Recruiter / materials (`recruiter_read_facade.py`, career_facts)

| Legacy | Canonical | Status | Data-loss | Confidence | Consumer |
|---|---|---|---|---|---|
| фасад читает vacancy row + evaluation | должен перейти на canonical запись (facts+evidence, без скора) | after Step 3 | нет | выше: evidence-цитаты для писем | CV tailoring, recruiter messaging |

## 7. Сводные риски

1. **Location-строка** — главный источник unknown; частично лечится
   enrichment'ом, не менять ingestion в Step 2.
2. **Отсутствие полных текстов** у LinkedIn-эпохи и Wise-подобных источников —
   canonical записи честно остаются unknown + risk; legacy скоринг это
   маскировал keyword-хитами по титулу.
3. **Вердикты не мигрируют** — score/tier/recommendation остаются только в
   legacy до Step 5; canonical слой не должен получить их «на переходный
   период».
4. Дубликация terminology (bucket vs fact) — до Step 5 отчёты читают legacy;
   disagreement-метрика (observability contract) измерит расхождения.
