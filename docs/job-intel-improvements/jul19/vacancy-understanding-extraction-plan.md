# Vacancy Understanding — Extraction Boundary & Implementation Plan (Step 2)

**Дата:** 2026-07-19. Статус: план; live provider execution НЕ реализован и
запрещён до отдельного одобрения.

## 1. Граница извлечения

### 1.1 Deterministic — реализовано (extractor v0.1.1, pure/replayable)

raw/normalized title; title families; management level (title-capped ≤medium);
location → city/country → country_group (versioned resolver); work format
(location tokens + явные фразы «hybrid», «office at least N days»,
«remote-first»); sponsorship phrases (yes/no; молчание = unknown); relocation
phrases; «right to work» → must_be_already_authorized; языковые требования
(«Fluent in X and Y»); years of experience; явный P&L wording; «team of N»;
reports-to; KZ local indicator; риск source_text_incomplete (<200 симв.);
фактическая пометка relocation_unclear для USA без sponsorship-фразы.

### 1.2 Semantic — отложено (контракт готов, значения в gold)

scope_breadth; revenue_proximity; platform_as_business vs platform_engineering;
digital_business_ownership; narrow-scope признаки (feature_delivery_only);
transformation_phase; domain transferability / entry barriers (кроме явных
языковых); product_culture_signal; mandate_summary; title/scope mismatch;
org-authority beyond explicit phrases.

### 1.3 External enrichment — отложено

company scale/stage/size; global footprint; brand tier; crypto-exchange
status; outsourcing status; emerging-markets presence. Источник фиксируется
отдельно от текста вакансии (`EvidenceSourceType.company_enrichment`).
Авто-обогащение из интернета в Step 2 запрещено.

### 1.4 Непознаваемо из вакансии

реальная культура; фактический (а не заявленный) объём полномочий; судьба
роли (repost/refill причины); внутренняя орг-политика. Модель хранит их
только как unknown/risks.

## 2. Будущий provider-backed semantic extraction (design)

- Extension point: `extractor.SemanticExtractorProtocol` —
  `extract_semantic(raw, base) -> VacancyUnderstanding`; в Step 2 отключён и
  не используется (нет ни одной реализации).
- Требования к будущей реализации: (a) промпт получает feature-definitions
  как семантический словарь; (b) выход валидируется канонической моделью;
  (c) каждый inferred факт обязан иметь excerpt-evidence из текста; (d)
  расхождение с deterministic-фактами — internal_contradiction risk, не
  перезапись; (e) golden dataset — регрессия качества до любого включения;
  (f) включение — отдельный слайс с explicit approval, фиксацией модели LLM и
  bump extractor_version.
- Confidence policy: explicit→high; deterministic→high/medium;
  semantic-with-language→medium; title-only→low; нет evidence → unknown.

## 3. Cache / versioning strategy

- Ключ кэша извлечения: `(source_content_hash, extractor_version,
  schema_version, resolver_version)` — любое изменение логики делает старые
  записи атрибутируемыми, но не «портит» их.
- Хранение канонических записей (будущее): отдельная таблица
  `vacancy_understanding` (JSON + версии + hash), append-only; в Step 2 БД
  НЕ изменяется — записи живут только в фикстурах/тестах.
- Fixture dataset version: 1.0.0; регенерация — только через
  `_generate_fixtures.py` с bump версии.

## 4. Failure / fallback behavior

- Пустой/короткий текст → факты unknown + risk source_text_incomplete;
  экстрактор никогда не бросает на битом тексте (HTML unescape + strip).
- Country resolver не знает страну → group other (или unknown при пустом
  входе); никаких free-text догадок про sanctioned/unstable.
- Противоречивые сигналы (напр. обе platform-формы) → model-level ошибка на
  этапе конструирования: экстрактор обязан выбрать доминирующую форму и
  добавить internal_contradiction risk (semantic-слайс).
- Semantic provider недоступен (будущее) → запись остаётся валидной с
  deterministic-фактами + unknown; никаких fallback-на-legacy-скоринг.

## 5. Observability contract (определено, не подключено)

Метрики из human contract §9; все считаются из канонических записей.
Реализация — не раньше Step 3 shadow-подключения, read-only.
