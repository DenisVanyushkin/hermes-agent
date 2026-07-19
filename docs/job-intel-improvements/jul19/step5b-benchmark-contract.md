# Step 5B — Benchmark Contract and Metric Definitions

**Статус:** Active benchmark contract (Slice 5B-0, documentation-only)
**Дата:** 2026-07-19 · **Владелец:** Denis Vanyushkin
**Нормативный источник:** Roadmap SoT §9.4 (Semantic Provider Benchmark philosophy: providers compete, architecture does not; no aggregate score)
**Предшествующий gate:** `STEP_5A_COMPLETE_READY_FOR_CROSS_PROVIDER_BENCHMARK` (commit `29fe6177c4`)

Этот документ определяет **что и как измеряется** до того, как что-либо реализовано или вызвано. Никакая последующая инфраструктура (Slice 5B-1+) не должна изобретать формулы метрик заново — она обязана реализовывать определения отсюда. Изменение определения после начала измерений = требует нового benchmark identity (§6 задания) и явной пометки, какие прежние числа стали несравнимы.

Non-goals этого слайса: implementation, платные вызовы, provider tuning, выбор порогов, рекомендация победителя.

---

## 1. Unit of evaluation

Метрики определяются раздельно по шести уровням; смешение уровней без явного указания запрещено (частая ошибка — репортить "recall" не уточняя, по vacancy это или по fact).

| Unit | Определение |
|---|---|
| **vacancy** | один прогон `extract_semantic()` на одном `(vacancy_key, title, text)` — эквивалент одного benchmark case |
| **observation** | один элемент `Observation`, возвращённый провайдером до Stage 3-валидации |
| **semantic fact** | одно поле итогового `fragment` (canonical Step 2 field path, `fact_id` из Semantic Contract) — то, что реально попадает в `SemanticExtraction.fragment` |
| **signal/fact type** | группировка facts по `fact_id`-leaf (напр. все `growth_mandate` через корпус) — единица per-fact-type precision/recall в 5B-5 |
| **evidence span** | один `excerpt` внутри одной observation — единица verbatim/support-проверки |
| **provider run** | один benchmark manifest (§6 задания) — набор всех vacancy-прогонов одного провайдера на одном датасете при фиксированной identity |

---

## 2. Precision

### 2.1 Matching policy (обязательна перед любой формулой)

Сравнение ведётся на уровне **semantic fact** (не observation — несколько observations могут схлопнуться в один факт через Stage 4 normalization, а конфликт-резолюция Stage 5-6 может дать `unknown` даже при валидных observations). Единица сравнения: `(vacancy_key, fact_id) → value`.

Против gold/annotated label вводятся 4 категории соответствия:

| Категория | Определение |
|---|---|
| **exact_match** | provider value == gold value (после нормализации enum, регистронезависимо) |
| **compatible_match** | provider value ∈ множеству значений, которое Decision SoT матрица трактует эквивалентно gold value для итогового verdict (напр. `company.scale=regional` vs `multi_region`, если оба ведут к одному decision-cell); список эквивалентностей публикуется отдельным приложением к отчёту 5B-5, не изобретается ad hoc в момент подсчёта |
| **partial_match** | provider произвёл наблюдение по правильному `fact_id`, но fact остался `unknown` из-за conflict-резолюции (basis/evidence слабее конкурирующего наблюдения), либо значение неверно, но evidence релевантно (не noise) |
| **mismatch** | provider value ≠ gold и не относится ни к одной из категорий выше, включая случай, когда provider вообще не произвёл наблюдения по этому fact_id, а gold — не unknown |

**Precision считается по exact_match + compatible_match** в числителе (partial_match и mismatch — не precision-хиты). exact и compatible репортятся раздельно всегда — компонент compatible не может «прятаться» внутри единой цифры.

### 2.2 Учёт duplicate / rejected / unsupported

- **Duplicate observations** (тот же `(fact_id, value)` из разных observations на одну вакансию) — считаются **один раз** в числителе/знаменателе fact-level метрик; в observation-level метриках (§evidence coverage) считаются каждая отдельно, дублирование само по себе — не ошибка precision, но входит в error taxonomy как `duplicate_observation`.
- **Rejected observations** (отсеянные Stage 3 — `excerpt_not_verbatim`, `unknown_fact_reference` и т.д.) **не участвуют** в precision числителе как «предъявленные» факты: они никогда не долетают до fragment. Они репортятся отдельно как rejection-taxonomy (§8) и снижают эффективный recall (провайдер «хотел» дать факт, но evidence не прошла).
- **Unsupported observations** (excerpt формально verbatim и enum валиден, но не подтверждает interpretation — ловится только ручным review, не автоматикой) относятся к error taxonomy `unsupported_evidence`, не к precision-числителю: unsupported факт, даже случайно совпавший с gold, помечается mismatch-эквивалентом отдельным флагом `precision_hit_but_unsupported` в отчёте — он не считается «чистым» précision-хитом.

### 2.3 Macro / micro

- **Macro precision** = среднее precision по fact_id (каждый signal type имеет равный вес независимо от частоты) — измеряет качество на редких, но важных facts.
- **Micro precision** = агрегированный precision по всем fact-instances в корпусе (частые facts доминируют) — измеряет типичный опыт по объёму.
Обе публикуются всегда вместе; ни одна не замещает другую.

---

## 3. Recall

### 3.1 Denominator (обязательное решение, не подразумевается)

Recall считается **только против provider-observable facts** — то есть facts, для которых Semantic Contract (`extraction_class ∈ {semantic_only, hybrid}`) допускает semantic-извлечение И gold-аннотация содержит не-`unknown` значение. Facts класса `deterministic_only` и `enrichment_only` исключены из знаменателя обоих providers — сравнение по ним архитектурно бессмысленно (см. Roadmap §9.6 non-goals: benchmark не тюнит extraction_class).

Явно из знаменателя исключаются:

- **exemptions** — 2 задокументированных `CONTROL_EXEMPTIONS` (calibration.py) и их прямые эквиваленты в historical corpus, если такие найдутся (документируются построчно, не скрыто);
- **ambiguous labels** — gold-записи, помеченные аннотатором как `ambiguous` или с confidence ниже gold acceptance threshold (если такая аннотация существует в датасете; если нет — все gold считаются authoritative, это фиксируется явно в отчёте, не подразумевается).

### 3.2 Формула

```
recall(fact_type) = (exact_match + compatible_match) / provider_observable_gold_count(fact_type)
```

Macro/micro recall — та же логика §2.3.

### 3.3 F1

`F1 = 2 * precision * recall / (precision + recall)`, считается на exact+compatible числителе для macro и micro раздельно. F1 не заменяет отдельную публикацию precision/recall — это дополнение, не сокращение отчёта.

---

## 4. Evidence coverage

Базовая формула (обязательная, задание §5B-0):

```
evidence_coverage = accepted_observations_with_valid_evidence / all_accepted_observations
```

где "valid evidence" = excerpt прошёл Stage 3 verbatim-проверку (`excerpt_not_verbatim` не сработал) — по построению это 100% для всего, что достигло `accepted`, поскольку Stage 3 — гейт. Поэтому эта метрика на уровне accepted всегда ≈1.0 и **сама по себе неинформативна для сравнения провайдеров**; она нужна как sanity-инвариант (если <1.0 — баг в раннере, не в провайдере), а различающие метрики — четыре ниже.

Различающие evidence-метрики (все на уровне **observation**, до и после Stage 3):

| Метрика | Формула | Что измеряет |
|---|---|---|
| **verbatim evidence rate** | `(emitted_observations − excerpt_not_verbatim_count) / emitted_observations` | доля наблюдений, где провайдер вообще процитировал текст корректно (до остальной Stage 3 фильтрации) |
| **evidence-to-fact support rate** | `manually_confirmed_supporting / accepted_observations_sampled` | ручная выборочная проверка (§8, error taxonomy): доля accepted observations, где excerpt реально подтверждает interpretation, а не formally verbatim но not-actually-supporting (см. Step 5A smoke findings: 2 натянутые кейса) |
| **unsupported evidence rate** | `1 − evidence_to_fact_support_rate` | обратная величина; репортится напрямую, не только выводится |
| **evidence missing rate** | `zero_observation_cases / total_cases`, отдельно для case, где gold ожидает ≥1 provider-observable fact | доля вакансий, где провайдер вообще ничего не дал там, где что-то ожидалось (empty control из smoke — единственный случай, где 0 наблюдений корректно) |

`evidence-to-fact support rate` требует ручной выборки (не 100% корпуса) — размер выборки и метод сэмплирования фиксируются в 5B-5 execution, не здесь; здесь фиксируется только формула.

---

## 5. Reproducibility

Пять обязательных проверок, каждая — отдельный булев результат в manifest, не единая "reproducible: true/false":

| Проверка | Определение | Ожидание |
|---|---|---|
| **repeated deterministic run equality** | тот же провайдер, тот же вход, N≥2 прогонов подряд → байт-идентичный `Observation[]` | 100% для DeterministicPhraseProvider (природа реализации); для LLM в record mode неприменимо (см. ниже) |
| **live-to-replay equality** | recorded raw response, реплеенный через parser, даёт тот же `Observation[]`, что был получен непосредственно в live-вызове (сравнение против записи `last_call_metadata` момента записи) | 100% — это свойство record/replay механизма, не провайдера; расхождение = баг раннера |
| **Observation equality** | `[o.model_dump() for o in obs]` идентичен между двумя прогонами сравниваемого типа (repeated или live-vs-replay) | как выше |
| **semantic_dump equality** | `SemanticExtraction.semantic_dump()` идентичен (JSON-эквивалентность после исключения run-metadata, которое дамп уже не содержит по определению Stage 10) | как выше |
| **semantic hash equality** | `sha256(json.dumps(semantic_dump(), sort_keys=True))` идентичен между прогонами | как выше |

**Важное разграничение, обязательное к соблюдению в отчётах:** LLM-провайдер в **replay mode** обязан давать 100% по всем пяти пунктам (это чистая offline-операция над записанным ответом — уже доказано в Step 5A smoke, 15/15). LLM-провайдер в **live mode** (два разных живых вызова на один и тот же вход) НЕ обязан давать 100% repeated-run equality даже при temperature=0 — стохастичность декодирования на уровне провайдера не гарантирует байт-идентичность при повторном live-вызове (зафиксировано как известное свойство в Provider Contract §6). Поэтому: "reproducibility" для LLM в benchmark-отчётах — это **replay-reproducibility относительно единственной live-записи**, а не live-repeat-reproducibility. Если требуется измерить live-стохастичность самого провайдера — это отдельная, явно объявленная дополнительная метрика (`live_repeat_stability`, опционально, не входит в обязательный набор), а не тихая подмена определения.

**Decision output equality** (где применимо) — тот же `evaluate()` (Decision SoT engine) на одинаковом semantic fragment даёт идентичный verdict; проверяется только для vacancies, где Decision SoT вообще вызывается в pipeline benchmark-раннера (§5B-1, downstream comparison), не для голого semantic-benchmark на 175 controls (у синтетических controls часто нет полного vacancy-контекста для evaluator).

---

## 6. Cost

Единицы учёта на уровне **provider run** и **vacancy**:

| Метрика | Формула / источник |
|---|---|
| input tokens | из `usage.prompt_tokens` записи (record) |
| output tokens | из `usage.completion_tokens` записи |
| total tokens | input + output |
| provider-reported cost | `input_tokens * PRICE_IN + output_tokens * PRICE_OUT` по прайсу, зафиксированному на дату запуска (прайс — не константа кода, публикуется в manifest run-а: он может измениться между 5B-4 и 5B-7) |
| cost per vacancy | provider-reported cost / cases_total (включая failed cases, если они потребили токены; failed cases с 0 токенов на transport-уровне — 0) |
| cost per accepted observation | provider-reported cost / observations_accepted (не emitted — иначе метрика поощряет провайдера, который выдаёт мало брака дёшево) |
| projected full-corpus cost | (cost per vacancy на выборке) × (размер полного eligible corpus, 3626), с явным доверительным диапазоном, если выборка < полного корпуса |

Для **DeterministicPhraseProvider** все денежные поля = `known_zero` (не `null`, не `0` без пометки — состояние явное, см. §"состояния" ниже). Задание прямо требует не путать "неизвестно" и "ноль".

### Состояния значений (обязательны для ЛЮБОГО числового поля метрик, не только cost)

```
known_zero    — измерено и равно нулю по природе провайдера (deterministic cost)
known_value   — измерено, конкретное ненулевое число
unknown       — должно было быть измерено, но данных нет (баг сбора, а не факт о провайдере)
not_applicable — метрика не имеет смысла для этого провайдера/режима (напр. live_repeat_stability для replay-only прогона)
```

Отчёты 5B-3+ обязаны маркировать каждое агрегатное число одним из этих состояний; голое число без состояния = дефект отчёта.

---

## 7. Latency

Единицы: **total** (сумма по run), **per vacancy**, **per accepted observation**, и перцентили **p50/p90/p95/p99/max** — все на уровне отдельных vacancy-прогонов (не на уровне суммарного batch-времени, которое зависит от параллелизма раннера и не сравнимо между провайдерами).

- Источник: `latency_ms` из `last_call_metadata` (LLM) / измеренный wall-clock таймер вокруг `extract_semantic_observations()` (deterministic — микросекунды, но измеряется тем же таймером ради единообразия метода).
- **per accepted observation** = latency_ms / max(observations_accepted, 1) — защита от деления на 0 на zero-observation cases; при 0 accepted метрика помечается `not_applicable`, а не как деление на малое число.
- Replay-latency (offline, из recording) публикуется отдельно от live-latency и никогда не смешивается в одну percentile-серию — офлайн-replay на порядки быстрее и не характеризует production-стоимость времени.

---

## 8. Error taxonomy

Минимальный обязательный набор кодов (задание §5B-0), с точным источником каждого — где в текущем коде код уже существует, а где его предстоит ввести на уровне benchmark-раннера (Slice 5B-1), эта разница фиксируется явно:

| Код | Уровень | Источник |
|---|---|---|
| `transport_failure` | provider call | `LLMProviderError(reason="transport_error")` (llm_provider.py) |
| `model_identity_mismatch` | provider call | `LLMProviderError(reason="model_version_mismatch"\|"model_identity_unverifiable")` |
| `schema_failure` | provider call | `LLMProviderError(reason="schema_invalid")` |
| `parse_failure` | provider call | `LLMProviderError(reason="invalid_json")` |
| `unknown_fact_reference` | observation | Stage 3 rejection code (pipeline.py) — уже существует |
| `excerpt_not_verbatim` | observation | Stage 3 rejection code — уже существует |
| `unsupported_evidence` | observation | **новый, benchmark-only код** — не детектируется автоматически Stage 3 (verbatim ≠ supporting); производится ТОЛЬКО ручным review (§4), не раннером |
| `zero_observation` | vacancy | `diagnostics.observations_total == 0` — уже в diagnostics |
| `over_extraction` | vacancy/fact | provider дал fact там, где gold = отсутствует/не применимо (не то же, что mismatch — over-extraction специфично считает случаи «лишнего», не «неверного») — **новый benchmark-only код**, определяется как `provider_value is not None and gold_value is None` |
| `under_extraction` | vacancy/fact | provider не дал fact, где gold есть и fact provider-observable — **новый benchmark-only код**, `provider_value is None and gold_value is not None` |
| `duplicate_observation` | observation | Stage 4 merge log / runner подсчёт повторных `(fact_id, value, location)` до merge |
| `semantic_mismatch` | vacancy | fragment provider ≠ fragment gold на ≥1 fact_id (агрегирующий код, не заменяет per-fact taxonomy) |
| `replay_mismatch` | provider run | live vs replay `Observation[]`/hash разошлись — **должен быть 0 всегда**; ненулевое значение = blocker (баг record/replay, не provider quality finding), останавливает benchmark до объяснения (см. Roadmap §9.5 replay reproducible gate) |

Остальные Stage 3 коды, не перечисленные явно (`invalid_location`, `excerpt_too_long`, `maps_to_unresolved`, `invalid_value_for_fact`, `enrichment_only_fact_forbidden_for_semantic`, `duplicate_observation_id`), учитываются в raw rejection-таблице, но не выделены в отдельные обязательные строки итогового отчёта — репортятся как есть по коду.

---

## 9. Baseline freeze reference (для 5B-1+)

Этот документ не устанавливает baseline identity (это задача Slice 5B-1 manifest), но фиксирует, что любое изменение любого определения в §2–§8 после первого benchmark run обязано:
1. получить новый `benchmark_id`;
2. явно пометить прежние числа как несравнимые с новыми (не "устаревшими" — именно несравнимыми, до отдельного re-run с новым определением).

---

## Открытые вопросы к владельцу (не блокируют Slice 5B-1, но требуют решения до Slice 5B-5)

1. **Compatible-match эквивалентности (§2.1):** список пар «разное значение → одна decision-cell» нужно построить из Decision SoT матрицы. Предлагаю построить его автоматически (программно вывести из 36-cell матрицы, а не вручную писать список) на этапе 5B-1 — подтверди, что это не расценивается как «изменение Decision SoT» (это read-only derivation, не правка).
2. **Ambiguous-label аннотация (§3.1):** в текущих 21 gold + 25 decision cases нет явного поля `ambiguous`. Считать ли отсутствие такого поля = «все gold authoritative», или нужна отдельная разметочная проходка перед 5B-5? Без ответа Slice 5B-5 будет считать все gold authoritative по умолчанию (явно об этом напишет).
3. **`unsupported_evidence` ручная выборка (§4):** размер и метод сэмплирования (случайная N=30? стратифицированная по fact_type? 100% на 175 controls, выборка только на 3626?) — предлагаю решить в рамках Slice 5B-5 execution planning, не здесь; здесь только формула.

Ни один из вопросов не блокирует Slice 5B-1 (провайдер-агностичная инфраструктура раннеров) — они относятся к сравнительным отчётам 5B-5/5B-8.
