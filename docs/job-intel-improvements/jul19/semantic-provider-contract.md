# Semantic Provider Contract — SoT (Step 5A.0)

**Статус:** Active normative contract (documentation-only slice)
**Версия:** 1.0.0
**Дата:** 2026-07-19
**Владелец:** Denis Vanyushkin
**Уровень:** контракт реализации интерфейса, НЕ архитектурный слой.
**Позиция в цепочке контрактов:** Career Preference SoT → Vacancy Semantic Contract (Step 4A) → **Provider Contract (этот документ)** → Runtime (Step 4B) → Decision SoT (Step 3A) → Benchmark (Step 5B).

Этот документ определяет, каким должен быть любой Semantic Observation Provider, чтобы оставаться взаимозаменяемым без изменений runtime и evaluator. Он управляет **поведением провайдера**, а не семантической политикой: что означают факты, определяет Semantic Contract (`semantic-fact-contract.yaml` v1.0.0); как принимаются решения — Decision SoT v1.1.0. Оба этим документом не изменяются.

Нормативная привязка к коду: интерфейс = `SemanticProvider` Protocol и модель `Observation` в `job_intel/vacancy_understanding/semantic/runtime/models.py`; референсная конформная реализация = `DeterministicPhraseProvider` (`runtime/provider.py`). При расхождении этого документа с Semantic Contract приоритет у Semantic Contract; при расхождении с кодом runtime — приоритет у контрактов, код чинится.

---

## 1. Provider responsibilities

Провайдер **может только**:

1. читать текст вакансии (`title`, `text`, `structured` — то, что runtime передал в `extract_semantic_observations`);
2. производить observations (`Observation[]`);
3. прикреплять evidence (verbatim `excerpt` + `location`);
4. назначать observation confidence class (`basis`: `explicit | direct | weak` — класс качества evidence, см. §4);
5. раскрывать provenance о себе (`provider_id`, `prompt_version`, метаданные §8).

Провайдер **никогда не может**:

- производить канонические semantic-факты (fragment пишет только runtime, стадии 4–10);
- производить выходы evaluator (feasibility/mandate/company/overall);
- производить recommendations или actions;
- выводить или учитывать предпочтения пользователя (провайдер кандидато-независим, как весь Step 2/4A слой);
- применять decision rules, caps, матрицу или любую политику Decision SoT;
- обращаться к запрещённым источникам evidence (§18 Semantic Contract): репутация бренда, world knowledge вне текста вакансии, история прошлых вакансий/оценок/feedback.

Единственная точка входа — метод протокола:

```python
extract_semantic_observations(*, title: str, text: str, structured: dict) -> list[Observation]
```

Провайдер не имеет других каналов влияния на систему. Любой выход помимо возвращаемого списка observations (запись файлов состояния, мутация входа, сайд-эффекты в БД) — нарушение контракта.

## 2. Observation production rules

### 2.1 Обязательные поля (все — модель `Observation`, `extra="forbid"`)

| Поле | Требование |
|---|---|
| `observation_id` | уникален в пределах одного вызова; стабилен при повторном вызове на том же входе (см. §6); формат — короткий детерминированный id (референс: `obs-<n>` в порядке производства) |
| `excerpt` | verbatim-подстрока входного текста, ≤ 400 символов (§3) |
| `location` | только `"title"` или `"description"`; excerpt обязан быть подстрокой именно этого источника |
| `signal_type` | строго `"<fact_leaf>=<value>"`; leaf резолвится в canonical fact id контракта, value ∈ enum этого факта по Step 2 |
| `interpretation` | 1–2 предложения, строго evidence-based; без chain-of-thought (запрещён Semantic Contract) |
| `maps_to` | непустой список canonical fact ids; каждый id обязан существовать в контракте |
| `basis` | `explicit | direct | weak` (§4) |

Опциональных полей **нет**: схема запрещает дополнительные поля. Провайдеру, которому «нужно» новое поле (reasoning, score, candidate rank), это поле не нужно — это признак утечки ответственности (§1).

### 2.2 Duplicate handling

- Повторный `observation_id` в одном вызове будет отброшен runtime (`duplicate_observation_id`); провайдер обязан не производить дубликатов сам, а не полагаться на фильтр.
- Семантические дубликаты (один signal с одинаковым basis из одного location) нормализуются runtime на Stage 4; провайдер может их не дедуплицировать, но не должен производить намеренно (шум ухудшает cost/latency-оси benchmark).

### 2.3 Unsupported signal handling

Провайдер не изобретает новые signal_type/fact ids. Сигнал, не выражаемый словарём контракта, **не эмитится вообще** — он не «упаковывается» в ближайший похожий факт. Runtime отбрасывает нарушения (`unknown_fact_reference`, `invalid_value_for_fact`, `enrichment_only_fact_forbidden_for_semantic`), но высокая доля rejected observations — признак неконформного провайдера и учитывается в benchmark.

### 2.4 Unknown behaviour

Отсутствие observation — легитимный и ожидаемый выход. Если текст не даёт qualifying evidence, провайдер возвращает **меньше observations, вплоть до пустого списка**; unknown-факты производит runtime по contract unknown policy. Провайдер никогда не эмитит observation «fact=unknown» и никогда не заполняет пробелы догадками (§7).

## 3. Evidence contract

1. **Verbatim-требование:** `excerpt` — точная подстрока входного текста (`excerpt in title|text`), без парафраза, нормализации регистра, склейки фрагментов или «…». Runtime проверяет это буквально (`excerpt_not_verbatim`).
2. **Source location:** каждый excerpt привязан к источнику (`title` | `description`); проверка verbatim выполняется против заявленного источника.
3. **Минимум evidence:** одна observation = один excerpt; observation без excerpt не существует (поле обязательное). Минимум одна observation на каждый эмитируемый semantic-факт — инвариант Semantic Contract (`min_observations_per_semantic_fact: 1`).
4. **Traceability:** через `observation_id` observation прослеживается до факта (`FactProvenance.observation_ids`) и до конфликтов (`ConflictRecord.observation_ids`). Провайдер обязан выдавать ids, пригодные для этой трассировки (уникальные, стабильные).
5. **Запрет unsupported observations:** observation, чей excerpt не поддерживает interpretation (цитата о другом), запрещена — это ключевой предмет ручной проверки в calibration и benchmark (evidence coverage / precision), фильтром runtime она не ловится.
6. **Только текст вакансии:** evidence из world knowledge модели («я знаю, что Wise глобальная») запрещён prohibited_sources Semantic Contract. Если утверждения нет в тексте — observation нет.

## 4. Confidence contract

`basis` — это **уверенность в извлечении observation** (качество evidence по иерархии Semantic Contract), и никогда не уверенность в корректности рекомендации:

- `explicit` — near-paraphrase assertion в теле текста;
- `direct` — прямой язык ответственности/мандата;
- `weak` — title-only или boilerplate-adjacent сигнал.

Итоговый fact confidence (`high|medium|low|unknown`) вычисляет **runtime** из basis-классов по confidence policy контракта. Провайдер не выбирает fact confidence.

Явно запрещено:

- вероятности и числовые скоры уверенности в любом поле;
- token logprobs как источник basis;
- model self-confidence («модель уверена на 0.87») как источник basis — `provider_confidence_is_not_a_source: true` в Semantic Contract.

Basis назначается **по типу evidence** (какая формулировка и где найдена), а не по внутреннему состоянию модели. LLM-провайдер, у которого logprob высокий, но формулировка weak, обязан выдать `weak`.

## 5. Prompt contract

1. **Prompt — implementation detail.** Промпт (для детерминированного провайдера — таблица правил; это установлено уже Step 4B) живёт целиком внутри модуля провайдера и нигде не является частью архитектуры.
2. Prompt **не может переопределять**: семантические значения фактов, инвентарь фактов (36 фактов Step 4A), evaluator policy. Промпт, который «объясняет модели», что considered scope_breadth=region значит что-то своё, — нарушение контракта.
3. **Версионирование обязательно:** любое изменение промпта/правил = bump `prompt_version`. Два разных промпта никогда не делят одну версию; `prompt_version` входит в provenance каждого факта и в diagnostics, без него replay и benchmark не атрибутируемы.
4. Prompt-оптимизации разрешены только внутри этих рамок и видимы через `prompt_version` bump — «тихие» правки промпта под конкретные кейсы запрещены (форма silent learning).

## 6. Determinism expectations

- **Deterministic providers** (референс: `DeterministicPhraseProvider`): одинаковый вход → байт-в-байт одинаковый выход. Никаких wall-clock, random, сетевых вызовов.
- **Stochastic providers** (LLM): обязаны быть **replay-воспроизводимыми** — фиксированные model version, prompt_version и параметры сэмплирования (temperature=0 или эквивалент детерминированного декодирования, где доступен); при недостижимости байт-стабильности выхода модели провайдер обязан обеспечивать воспроизводимость через записанные raw-ответы (record/replay): benchmark и calibration прогоняются по записанным ответам, а не по живым вызовам.
- **Replay reproducibility:** повторный прогон replay-корпуса на том же провайдере (та же model/prompt version) обязан давать тот же `semantic_dump()` (semantic_hash-стабильность). Дрейф хэшей без смены версий = дисквалификация из benchmark до объяснения.
- **Benchmark reproducibility:** все benchmark-артефакты (Step 5B) должны быть перевоспроизводимы из записанных входов/выходов без повторной оплаты LLM-вызовов.
- Нестабильность `observation_id` между повторными вызовами на одном входе — нарушение детерминизма (ломает трассировку diff-ов).

## 7. Provider limitations

- Провайдер обязан «возвращать unknown» единственным легальным способом — **не эмитить observation** (см. §2.4), а не изобретать её.
- **Provider recall limitations — не policy failures.** Потолок recall провайдера (пример: 72.6% eligible без semantic-фактов у phrase-провайдера, Step 4B) — свойство реализации, измеряемое benchmark. Он не даёт оснований менять evaluator, thresholds, unknown policy или Semantic Contract (non-goals Phase II, Roadmap SoT §9.6).
- Ответ на слабый recall — другой/лучший провайдер, а не ослабление evidence-требований.
- Провайдер не компенсирует ограничения агрессивной интерпретацией: precision (unsupported observations) — более тяжёлое нарушение, чем низкий recall, потому что ложные факты проходят вниз по конвейеру, а пропуски дают честный unknown.

## 8. Benchmark obligations

Каждый провайдер обязан раскрывать метаданные, достаточные для:

| Ось | Обязательные метаданные |
|---|---|
| replay | `provider_id`, `prompt_version`, записанные raw-входы/выходы (для stochastic — record/replay хранилище) |
| calibration | полные Observation[] с basis и excerpts (уже в `SemanticExtraction`) |
| cost accounting | для LLM: model version, счётчики токенов (prompt/completion) на вызов; для deterministic: 0-cost декларация |
| latency | время вызова провайдера на вакансию (замеряет harness; провайдер не должен скрывать вызовы вне интерфейса) |
| prompt version | `prompt_version` (Protocol-атрибут, попадает в provenance/diagnostics) |
| model version | точный идентификатор модели/ревизии для LLM-провайдеров; фиксируется в benchmark-артефактах |

Runtime уже прокидывает `provider` и `prompt_version` в `FactProvenance` и `ExtractionDiagnosticsOut`; провайдер обязан заполнять их честно (никаких переиспользованных версий). Метаданные, отсутствующие в интерфейсе (токены, model version), публикуются в benchmark-артефактах Step 5B, а не добавлением полей в `Observation`.

## 9. Extension rules

Будущие провайдеры (LLM, hybrid, внешний сервис, улучшенный phrase) **не могут требовать**:

- модификации runtime (конвейер Stage 1–10, модели, валидация);
- модификации evaluator (shadow evaluator, Decision SoT);
- модификации Semantic SoT (инвентарь фактов, evidence-иерархия, conflict/confidence policy).

Если предлагаемая реализация этого требует — это **architecture proposal**, а не provider implementation: она проходит процедуру изменения соответствующего SoT (§8 процессного SoT) с владельческим решением, и до утверждения к benchmark не допускается.

Допуск провайдера в Shadow регулируется provider acceptance gate (Roadmap SoT §9.5): contract compliant (этот документ + Semantic Contract), replay reproducible, calibration complete, benchmark completed, recommendation approved.

---

## Change record (§8 процессного SoT)

- **Причина:** Phase II вводит конкуренцию провайдеров; без нормативного Provider Contract реализации молча разойдутся в confidence, evidence, promptах и provenance, и benchmark станет несравнимым.
- **Evidence:** интерфейс и валидация уже существуют в коде (Step 4B: `SemanticProvider` Protocol, Stage 3 rejection-коды) — документ фиксирует их нормативно, ничего не меняя.
- **Затронутый шаг:** Step 5A (предпосылка 5A.0); Semantic SoT, Decision SoT, runtime — не затронуты.
- **Изменение acceptance criteria:** «contract compliant» в §9.5 Roadmap SoT теперь означает соответствие этому документу + Semantic Contract.
- **Backward compatibility impact:** нулевой; `DeterministicPhraseProvider` конформен как есть.
- **Решение владельца:** задание владельца от 2026-07-19 «Step 5A.0 — Observation Provider Contract SoT».
