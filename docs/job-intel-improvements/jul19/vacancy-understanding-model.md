# Vacancy Understanding Model — Human-Readable Contract (v1.0.0)

**Статус:** Step 2, NOT integrated в production.
**Машинные артефакты:** `job_intel/vacancy_understanding/model.py` (Pydantic-контракт),
`vacancy-understanding.schema.json` (генерируется, sync-тест),
`feature-definitions.yaml` (семантический словарь), `country_groups.py`
(версионируемый резолвер), `extractor.py` (детерминированный baseline v0.1.0).
**Golden dataset:** `tests/fixtures/vacancy_understanding/` (21 кейс, dataset 1.0.0).
**Тесты:** `tests/job_intel/test_vacancy_understanding_{model,golden}.py`.

## 1. Назначение и границы

Слой описывает, **что вакансия означает** — кандидато-независимо. Три слоя
архитектуры: Career Preference Model (описывает Дениса, Step 1) ↔ **Vacancy
Understanding Model (описывает вакансию — этот слой)** ↔ Shadow Evaluator
(сопоставляет, Step 3). Здесь нет и не может быть: скоров, band'ов,
apply/reject, весов предпочтений, `is_good_for_*`. Тест `test_no_verdicts_anywhere`
и запрет импорта preference model это enforce'ят.

## 2. Секции канонической записи

| Секция | Содержание |
|---|---|
| `metadata` | schema/extractor версии (semver), created_at, vacancy_key, source, content hash, language, `is_synthetic_fixture`, `production_integration=false` |
| `role_identity` | raw/normalized title, title_families и function_families (гибриды сохраняют все семьи), management_level_observed (титул = evidence, confidence ≤ medium) |
| `mandate` | scope_breadth, revenue_proximity, 21 tri-state факт (growth, monetization, P&L, platform-as-business vs platform-engineering, internal tools, …), transformation_phase (мультизначная), mandate_summary |
| `organization` | reports-to, direct reports, cross-functional/hiring/budget/org-design authority, geo/portfolio responsibility |
| `company` | наблюдаемые/обогащённые ФАКТЫ компании: scale, stage, customer model, is_crypto_exchange, is_outsourcing, brand_recognition, regulatory_risk_signal… — не вердикты |
| `feasibility_facts` | страна/город/country_group (+resolver version), work_format, sponsorship_stated, relocation_support, авторизация, timezone/hours/travel, языки, local_market_indicator |
| `requirements` | опыт, обязательные экспертизы, entry_barriers с transferability-классификацией |
| `risks` | фактические предупреждения (RiskKind enum) — никогда не reject |
| `evidence_registry` | источники (vacancy_text / structured fields / enrichment / gold) — каждый Evidence ссылается сюда |
| `extraction_diagnostics` | warnings, длина текста, счётчики фактов |

## 3. Unknown, confidence, evidence

- **Unknown — first-class.** Все tri-state факты по умолчанию `unknown`;
  отсутствие данных никогда не становится `false` (валидатор + тест).
- **Fact-обёртка:** `{value, confidence, method, evidence[]}`.
  Инварианты: известное значение обязано иметь method; `semantic_inference` и
  `company_enrichment` обязаны иметь evidence; unknown не может притворяться
  извлечённым (method=none).
- **Confidence:** high/medium/low/unknown. Explicit statement → high; сильная
  детерминированная деривация → high/medium; semantic inference с прямой
  языковой опорой → medium; вывод только из титула → ≤ medium (для
  management level — механически ограничено); нет evidence → значение unknown.
- **Evidence:** source_id (→ registry), source_type, bounded excerpt (≤400
  симв., полные описания не хранятся — copyright), location, rationale.

## 4. Ключевые семантические различения

1. **Platform-as-business ≠ platform engineering.** Первое: деньги/ценность
   идут ЧЕРЕЗ платформу (Airwallex GPNI). Второе: внутренняя инженерная
   платформа (Coinbase Core Infra). Слово «infrastructure» само по себе не
   решает; одновременно true — model-level ошибка (internal contradiction).
2. **Титул — evidence, не истина.** scope_breadth выводится из
   обязанностей; title-only деривация уровня ограничена medium confidence.
3. **Crypto-работодатель ≠ crypto-барьер.** `company.is_crypto_exchange` —
   факт компании; entry barrier возникает только из непереносимого
   требования (обязательный Mandarin, глубокая обязательная экспертиза).
4. **KZ local + sponsorship unknown — валидная комбинация фактов** (для
   работы в KZ виза не нужна); противоречием не является.
5. **Sponsorship silence = unknown**, никогда «no».
6. **Риски — факты, не штрафы.** `timezone_burden`, `sponsorship_absent` и
   пр. downstream могут стать только clarification.

## 5. Deterministic vs semantic vs enrichment

- **Deterministic (реализовано, extractor v0.1.0):** title-нормализация и
  families, management level (capped), location→city/country→country_group
  (через резолвер), work format (location + явные фразы), sponsorship/
  relocation/authorization фразы, языки, годы опыта, явный P&L, team size,
  reports-to, KZ local indicator, риски неполного текста.
- **Semantic (отложено, только контракт):** scope_breadth, revenue_proximity,
  platform shapes, digital_business_ownership, transformation_phase,
  transferability, mandate_summary, product_culture_signal,
  title/scope mismatch. В golden dataset они заданы manual gold annotation.
- **Enrichment (отложено):** company scale/stage/brand/crypto/outsourcing/
  footprint — с обязательным указанием источника, отдельного от текста
  вакансии.

Каждый факт показывает происхождение через `method` + `evidence.source_type`.

## 6. Country-group resolver

Отдельный версионируемый контракт (`country_groups.py`, snapshot
`2026.07.19`): curated-списки usa/kazakhstan/sanctioned/unstable/africa;
не входящие в списки страны → `other`; пустой вход → `unknown`.
`sanctioned`/`unstable` **никогда** не выводятся из free-text интуиции.
Будущие авторитетные источники: консолидированные санкционные списки
(OFAC/EU/UN) и operator-reviewed список нестабильности. Результат
объясним: `{group, matched_key, resolver_version, source}`.

## 7. Versioning

`schema_version` (контракт), `extractor_version` (логика извлечения),
`dataset_version` (фикстуры), `source_content_hash`, `country_group_resolver_version`.
Breaking-семантика поля → major schema. Изменение экстракции, способное дать
другие значения → bump extractor_version даже без изменения схемы.
Исторические записи остаются атрибутируемыми своей версии экстрактора.

## 8. Контракт для потребителей

Step 3 shadow evaluator (и позже company evaluation, CV tailoring, recruiter
messaging, analytics) читают ТОЛЬКО канонические факты + evidence +
confidence; они не читают legacy score/matched_signals. Потребитель обязан
уважать unknown (не интерпретировать как false) и пиновать major
schema_version. Производственная интеграция запрещена до отдельного
одобренного шага (`production_integration=false` + import-guard тест).

## 9. Observability contract (определено, НЕ подключено)

Будущие метрики: extraction success rate; unknown rate по фичам;
low-confidence rate; deterministic vs semantic счётчики; evidence coverage;
golden pass rate; распределение extractor-версий; legacy vs canonical
disagreement; company-enrichment missing rate; country-group unresolved rate;
source-text incompleteness rate. Всё вычислимо из канонических записей;
запись в production observability — не в Step 2.
