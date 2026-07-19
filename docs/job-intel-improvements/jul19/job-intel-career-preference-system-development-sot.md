# Job Intel Career Preference System — Development SoT

**Статус:** Active process Source of Truth  
**Дата:** 2026-07-19  
**Владелец:** Denis Vanyushkin  
**Назначение:** единый план доработки Hermes Job Intel от текущего статического скоринга к объяснимой системе рекомендаций, основанной на карьерных предпочтениях пользователя.  
**Режим:** работа по последовательным слайсам; каждый следующий шаг начинается только после выполнения DoD предыдущего.

## 1. Основание и ключевой вывод

Проведены два исследования:

1. Recommendation System Audit: выявлены разомкнутый feedback loop, fintech/company bias, деградация precision, resend-дубли и отсутствие обучаемого preference layer.
2. User Career Preference Model Research: установлено, что идеальная вакансия определяется прежде всего **формой мандата и реализуемостью**, а не индустрией, страной или титулом.

Центральная продуктовая гипотеза процесса:

> Система должна оценивать не «насколько вакансия похожа на fintech/AI Director role», а насколько она предоставляет Денису широкий, реализуемый и карьерно-конвертируемый мандат: бизнес-линия, регион, платформа как бизнес или портфель; рост, монетизация, P&L, организация и трансформация.

## 2. Зафиксированные пользовательские ограничения и предпочтения

### 2.1 Relocation policy

- Пользователь готов релоцироваться практически в любую страну.
- Исключения:
  - подсанкционные страны;
  - политически или социально нестабильные страны;
  - Африка как регион не рассматривается на текущем этапе.
- США:
  - relocation в США считается практически сложным;
  - onsite/hybrid USA без **явно указанной готовности работодателя спонсировать визу и релокацию** должен отсеиваться;
  - remote US role не должна автоматически отбрасываться;
  - USA role с explicit visa sponsorship может рассматриваться на общих основаниях.
- Казахстан:
  - локальный рынок не является предпочтительным долгосрочным направлением;
  - при ухудшении текущей рабочей ситуации пользователь готов рассматривать местные компании и временно снижать планку;
  - следовательно, KZ не является hard veto; это отдельный fallback lane с иными порогами качества и приоритета.

### 2.2 Remote timezone policy

- На текущем этапе нет жестких ограничений по разнице во времени.
- Пользователь готов рассматривать любую timezone difference.
- При качественной роли и компенсации возможен переезд в более удобную часовую зону.
- Timezone не должна использоваться как hard gate до появления реального негативного опыта или явного пользовательского ограничения.
- Система может фиксировать timezone как risk/clarification, но не как автоматический rejection reason.

### 2.3 Compensation policy

- Компенсация пока не используется как критерий отбора.
- Причины:
  - диапазон редко указан;
  - текущая цель — сначала улучшить качество и объем релевантных возможностей;
  - вопрос compensation floor будет введен позже, когда низкая компенсация станет реальной причиной отказов.
- Отсутствующий salary range не является недостатком вакансии.
- Низкая или неизвестная компенсация не должна влиять на ranking до явного изменения SoT.

## 3. Принципы реализации

1. **Mandate over labels.** Индустрия, страна и title не получают самостоятельных весов; они служат сырьем для определения мандата, feasibility и company context.
2. **Feasibility отдельно от preference.** «Не могу принять» и «не хочу» — разные verdicts и разные причины.
3. **Role fit отдельно от company fit.** Сильная роль в слабой компании и слабая роль в сильной компании не должны схлопываться в один score.
4. **Unknown не равен false.** При нехватке evidence extractor должен возвращать unknown, а не выдумывать отрицательное значение.
5. **Evidence-first.** Каждый семантический feature и verdict должен иметь evidence snippet или явную причину отсутствия evidence.
6. **No silent learning.** Любое изменение preference SoT сначала предлагается, объясняется и подтверждается человеком.
7. **Clean labels only.** Тестовые пользователи, resend-дубли и data-quality карточки не участвуют в обучении и метриках preference quality.
8. **Controlled rollout.** Новая логика сначала shadow-only, затем ограниченные gates, затем ranking, и только потом production ownership.
9. **No architecture drift.** Нельзя перескочить к learned re-ranker до появления стабильной feature schema, golden dataset и shadow evidence.
10. **Local fallback is explicit.** Казахстанские роли обрабатываются отдельной политикой, а не смешиваются с глобальным основным поиском.

## 4. Целевая модель решений

Любой будущий evaluator должен формировать минимум четыре независимых блока:

```yaml
feasibility:
  verdict: feasible | uncertain | infeasible
  reasons: []

mandate_fit:
  verdict: exceptional | strong | plausible | weak | mismatch
  reasons: []

company_fit:
  verdict: positive | neutral | concern | negative
  reasons: []

overall:
  recommendation: exceptional | strong | promising | unclear | not_recommended
  action: apply | investigate | save | reject   # operational action vocabulary
  confidence: high | medium | low
```

> **Поправка 2026-07-19 (owner decision O1, Step 3A):** словарь разделён на
> два уровня. `recommendation` описывает качество возможности;
> `action` — операционный вывод (прежние значения apply/investigate/save/
> reject — это action outcomes, не recommendation). Mapping утверждён в
> shadow-evaluator-decision-sot.md §1; `exploration` — отдельный маркер,
> не label.

Единый непрозрачный score не может быть единственным выходом системы.

---

# 5. Пятишаговый план реализации

## Step 1 — Career Preference Model SoT Normalization

### Цель

Преобразовать исследовательский `career-preference-model.draft.yaml` в строгий, версионируемый и валидируемый контракт, пригодный для всех потребителей карьерной платформы.

### Основные задачи

- разделить:
  - feasibility constraints;
  - mandate preferences;
  - company preferences;
  - anti-preferences;
  - interaction/override rules;
  - exploration policy;
  - fallback-local-market policy;
- встроить новые решения пользователя по relocation, timezone, compensation и Казахстану;
- убрать title-based и industry-based абсолютные выводы;
- нормализовать enum, confidence и strength;
- добавить provenance/evidence metadata;
- описать override semantics;
- подготовить JSON Schema или эквивалентную строгую схему;
- определить compatibility/versioning policy;
- подготовить миграционную карту от текущих `preferences.yaml`, `search_criteria.yaml`, `scoring.yaml` и hardcoded rules.

### Deliverables

1. `career-preference-model.yaml`
2. `career-preference-model.schema.json` или эквивалент
3. `career-preference-model.md` — human-readable contract
4. `career-preference-model-migration-map.md`
5. validation tests для схемы и ключевых invariants
6. read-only comparison report: draft vs normalized model

### DoD

- модель парсится и проходит schema validation;
- нет неизвестных/несогласованных enum;
- каждый активный rule имеет `id`, `status`, `strength`, `confidence`, `source`, `evidence`;
- feasibility, mandate и company signals физически разделены;
- interaction rules явно покрывают минимум:
  - narrow scope + monetization exception;
  - B2B + platform-as-business exception;
  - remote role не наследует country onsite penalty;
  - crypto employer не равен automatic role veto;
  - USA relocation требует explicit sponsorship;
  - KZ local fallback lane;
- compensation не участвует в gating/ranking;
- timezone не является hard gate;
- существующий production evaluator не изменен;
- никакой production config не переключен.

### Запрещено

- менять production ranking;
- переписывать evaluator;
- добавлять ML/LLM runtime scoring;
- автоматически применять модель;
- удалять старые конфиги до утвержденной migration strategy.

---

## Step 2 — Vacancy Feature Extraction Contract & Golden Dataset

### Цель

Определить, какие структурированные факты должны извлекаться из вакансии, и создать эталонный размеченный набор для проверки extraction и будущего evaluator.

### Основные задачи

- разработать feature schema для:
  - mandate scope;
  - revenue proximity;
  - growth/monetization/P&L;
  - org mandate;
  - transformation phase;
  - function family;
  - domain transferability;
  - platform-as-business vs platform engineering;
  - company scale/brand/stage/model;
  - work format, location, sponsorship, language;
  - local fallback lane;
- зафиксировать deterministic vs semantic extraction boundary;
- сохранить evidence snippets и field-level confidence;
- вручную разметить golden dataset на очищенных исторических вакансиях;
- включить внутрикомпанейские contrast pairs и контрпримеры.

### Обязательные golden cases

Positive/high-fit:
- Wise — APAC Growth & Expansion;
- Airwallex — Global Payments Network Infrastructure;
- Monzo — Business Banking;
- Brex — Growth/AI;
- Wise — Pricing/Acquiring как conditional exception.

Negative/contrast:
- Wise — Financial Crime / Data Product / Onboarding;
- Airwallex — Payment Fraud;
- Coinbase/Adyen — platform engineering/DevEx;
- internal tools/back-office;
- pure sales/FP&A/project delivery;
- onsite USA without sponsorship;
- local low-scope role;
- KZ role с достаточно сильным мандатом для fallback lane.

### Deliverables

1. `vacancy-feature-schema.yaml|json`
2. feature extraction contract
3. golden dataset
4. annotation guide
5. coverage/gap report
6. expected extractor behavior for unknown/ambiguous cases

### DoD

- golden dataset содержит чистые labels без test/resend/data-quality noise;
- минимум один contrast pair на каждое ключевое interaction rule;
- каждое поле имеет definition, type, allowed values и evidence rule;
- missing/unknown semantics заданы явно;
- ни один downstream ranking change не внедрен.

---

## Step 3 — Shadow Preference Evaluator

### Цель

Реализовать read-only evaluator, который использует нормализованный SoT и extracted features, но не влияет на отправку вакансий.

### Выход evaluator

- feasibility verdict;
- mandate fit;
- company fit;
- overall recommendation;
- confidence;
- explanations;
- applied interaction/override rules;
- unknowns/clarifications.

### Обязательная ретроспективная проверка

- разделяет все ключевые within-company contrasts;
- не пропускает пять чистых applied кейсов без объяснимой причины;
- сохраняет narrow monetization exception;
- не штрафует Remote US как onsite USA;
- блокирует USA relocation без explicit sponsorship;
- различает platform-as-business и platform engineering;
- не считает B2B самостоятельным негативом;
- корректно обрабатывает KZ fallback lane.

### Метрики

- recall applied/exceptional;
- precision top band;
- gate accuracy;
- pairwise contrast accuracy;
- false-negative review;
- explanation correctness;
- unknown rate;
- stability/repeatability;
- legacy vs shadow disagreement analysis.

### Deliverables

1. shadow evaluator implementation
2. historical backtest report
3. disagreement report
4. failure taxonomy
5. go/no-go recommendation for limited rollout

### DoD

- production selection и Slack delivery не изменены;
- все decisions observability-only;
- no hidden fallbacks to old labels such as fintech/company anchor;
- golden cases проходят согласованные acceptance criteria;
- false negatives разобраны вручную.

---

## Step 4 — Operational Quality Fixes

### Цель

Устранить текущий пользовательский вред независимо от нового evaluator.

### Scope

- исключение test users из аналитики;
- исключение resend/data-quality noise из обучения;
- resend hysteresis по meaningful state/band change;
- нормализованный content diff вместо raw hash;
- max resend count;
- persistent logical vacancy identity;
- grouping мультилокационных req-ID;
- per-company digest quota;
- bad-card/data feedback не влияет на preference model;
- отдельная observability по core/global и KZ fallback lane.

### Deliverables

1. implementation slices
2. regression tests
3. historical replay evidence
4. before/after metrics
5. rollback plan

### DoD

- повторные карточки существенно сокращены;
- applied/exceptional карточки не теряются;
- группы мультилокаций не склеивают разные роли;
- тестовые реакции не попадают в preference metrics;
- production behavior изменяется только в согласованном operational scope.

---

## Step 5 — Controlled Rollout & Feedback Closure

### Цель

Поэтапно включить preference model в production и замкнуть feedback loop без неконтролируемого самообучения.

### Порядок rollout

1. feasibility gates only;
2. mandate/company explanations в карточках;
3. preference-based reranking при сохранении legacy candidate generation;
4. selection policy: quotas, diversity, exploration;
5. weekly preference proposal loop;
6. позже — оценка необходимости learned re-ranker.

### Feedback semantics

- `not_interesting` должен классифицироваться как:
  - mandate mismatch;
  - company concern;
  - infeasible location;
  - sponsorship missing;
  - domain barrier;
  - seniority/scope;
  - wrong function;
  - compensation;
  - bad data/card;
- `save_for_later` = роль привлекательна, но остается blocker/uncertainty;
- `applied/exceptional` = сильный позитив с возможным уточнением решающего фактора;
- bad data никогда не обучает preference model.

### Controlled exploration

- 1–2 карточки в неделю;
- меняется одна неизвестная ось;
- все hard feasibility gates соблюдены;
- exploration помечается отдельно;
- local KZ fallback не смешивается с exploration глобального трека.

### Deliverables

1. rollout plan и feature flags
2. weekly quality/drift report
3. preference change proposal workflow
4. Slack feedback contract
5. rollback criteria
6. post-rollout review

### DoD

- включение по стадиям и feature flags;
- у каждого production verdict есть explanation и provenance;
- preference SoT не изменяется автоматически;
- weekly metrics включают precision, recall proxy, resend rate, company concentration, industry diversity, gate outcomes, core-vs-fallback split;
- существует проверяемый rollback path.

---

# 6. Зависимости и порядок

```text
Step 1 ──► Step 2 ──► Step 3 ──► Step 5
   │          │
   └──────────┴──────► Step 4 может выполняться параллельно после фиксации baseline
```

- Step 3 не начинается до утверждения Step 1 и golden schema Step 2.
- Step 5 не начинается без shadow evidence Step 3.
- Step 4 не должен внедрять shortcuts, которые противоречат будущему SoT.

# 7. Глобальные acceptance criteria процесса

Процесс считается успешным, когда:

1. система различает mandate, company и feasibility;
2. Wise APAC и Airwallex GPNI стабильно входят в верхний band;
3. Wise Financial Crime и Airwallex Fraud не проходят как сильные рекомендации;
4. Remote US не путается с USA onsite without sponsorship;
5. crypto company prior не превращается в слепое keyword veto;
6. KZ local roles доступны в fallback lane при снижении внешних возможностей;
7. timezone и compensation не создают ложных rejection;
8. resend noise минимизирован;
9. feedback реально влияет через контролируемые, объяснимые предложения изменений;
10. ни одна модель не учится на test users, bad data или resend duplicates.

# 8. Управление изменениями SoT

Любое изменение этого документа должно содержать:

- причину;
- evidence;
- затронутый шаг;
- изменение acceptance criteria;
- backward compatibility impact;
- решение владельца.

До завершения Step 5 этот документ является процессным SoT и имеет приоритет над отдельными implementation plans, если они ему противоречат.
