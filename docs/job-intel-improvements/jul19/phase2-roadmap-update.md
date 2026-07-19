# Phase II Roadmap Update — Summary

**Дата:** 2026-07-19
**Тип:** documentation-only (roadmap SoT update)
**Изменённый документ:** `docs/job-intel-improvements/jul19/job-intel-career-preference-system-development-sot.md` (новый §9 + поправки к §5/§6)
**Не изменены:** Semantic SoT (Step 4A), Decision SoT (Step 3A), evaluator, runtime pipeline, любое runtime-поведение.

---

## 1. Почему roadmap изменился

Первоначальный roadmap предполагал линейный ход после Step 4:

```text
Step 4B → LLM Provider → Replay → Calibration → Production
```

Это было логично, пока runtime не существовал. После закрытия Step 4B картина иная — уже есть:

- provider abstraction;
- replay framework (включая full historical replay: 10 013 записей, 0 failures, 0 contract gaps);
- calibration framework;
- deterministic baseline (DeterministicPhraseProvider, controls 175 / uncovered 0);
- Semantic Contract v1.0.0 и стабильный 10-стадийный конвейер.

LLM перестал быть архитектурным компонентом — это одна из реализаций уже существующего provider-интерфейса. Вопрос сменился с «как построить Semantic Extraction?» на «какой provider лучше реализует существующий Semantic Contract?». Roadmap обязан отражать эту смену стадии зрелости, поэтому «implement LLM» как первичная цель заменён на Provider Benchmark.

## 2. Что теперь содержит Phase II

```text
Phase II — Semantic Provider Benchmark
    Step 5A — LLM Observation Provider     (реализация провайдера под существующий протокол)
    Step 5B — Cross-provider Benchmark     (replay, calibration, evidence coverage,
                                            precision, recall, cost, latency, reproducibility —
                                            без агрегатного скора)
    Step 5C — Provider Selection Review    (владельческое решение по рекомендации)
Phase III — Shadow Deployment              (условен: только после утверждённого 5C)
Production Rollout
```

Acceptance gate для допуска провайдера в Shadow: contract compliant, replay reproducible, calibration complete, benchmark completed, recommendation approved.

Non-goals Phase II: никаких изменений Decision SoT, Semantic SoT, evaluator, runtime pipeline, recommendation thresholds; никакой provider-специфичной policy.

## 3. Почему это НЕ архитектурное изменение

- Архитектурные SoT (Semantic Contract, Decision Contract) не тронуты — меняется только процессный roadmap.
- Ни одна строка runtime-кода не изменена; поведение системы идентично.
- Phase II не добавляет и не убирает компонентов архитектуры — она эксплуатирует уже существующие точки расширения (provider abstraction) и уже существующие инструменты оценки (replay + calibration).
- Принцип «providers compete, architecture does not» прямо запрещает архитектурный дрейф внутри Phase II: провайдер, требующий модификаций runtime, не допускается к benchmark.

## 4. Как будущие providers вписываются в систему

```text
Semantic Provider  →  Observation[]  →  Existing Runtime  →  Existing Decision Engine
```

Любой будущий provider (LLM, гибрид, улучшенный phrase-провайдер, внешний сервис):

1. реализует тот же протокол, что и DeterministicPhraseProvider;
2. выдаёт Observation[], валидируемый Semantic Contract (verbatim-observation слой, evidence-иерархия, confidence только от evidence);
3. не требует изменений конвейера или decision engine;
4. проходит единый путь допуска: replay → calibration → benchmark по осям §9.4 → owner review;
5. сравнивается с baseline и другими providers по независимым осям, без композитного рейтинга — взвешивание осей остаётся владельческим решением на Provider Selection Review.

Runtime и Decision Engine при этом неизменны: замена провайдера — операция уровня конфигурации/выбора реализации, а не архитектуры.
