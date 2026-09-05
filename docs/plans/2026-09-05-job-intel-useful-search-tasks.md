# Job Intel: подробные задачи полезного поиска

Дата: 2026-09-05. Авторы: Claude и Codex. Статус: specification, execution not started.
[Roadmap](2026-09-05-job-intel-useful-search-roadmap.md) задаёт порядок.
[Planning addendum](../job-intel-search-planning-addendum.md) задаёт manual route.
[Product SoT](../superpowers/specs/2026-08-10-job-intel-search-product-redesign-design.md)
остаётся политикой поиска; [аудит](../audit/2026-09-05-job-intel-roadmap-current-state.md)
содержит подтверждённую базу. Идентификаторы M* ниже не заменяют Redesign Task N.

## Общие правила исполнения

- Перед задачей сверить canonical HEAD, рабочую ветку, status, protected stash,
  runtime pin и используемые SoT. Не считать исторические чекбоксы current evidence.
- M0/M1 работают read-only с DB и сохраняют отдельные review artifacts; не вызывают
  daily/bootstrap, provider benchmark, CRM writers или production delivery.
- Результат для владельца может содержать вакансии и его решения; хранить его вне
  публичного Git: `/home/hermes/.hermes/job_intel/manual-shortlist/<release-id>/`.
  В repo — инструкция, шаблон без личных данных и обезличенный outcome по запросу.
- Для изменений кода: отдельный worktree, тест воспроизводит исходный дефект,
  минимальная правка, targeted GREEN и composition check затронутого потребителя.
  Учитывать known-red baseline; не запускать весь pytest на production VPS.
- Выполнение collector fixes не означает разрешения менять защищённый source
  path вопреки существующему scope: сначала additive seam; если он недостаточен,
  подготовить точный scope amendment до зависимой правки. Не ослаблять guard.
- Live/provider запуски выполняются только в рамках применимого разрешения,
  ограниченного бюджета и pinned runtime. Fixture result не выдаётся за live yield.

<a id="m0"></a>
## M0 — Первый shortlist без разработки продукта

**Входы:** свежая canonical inventory, Candidate Facts по profile reference,
Product SoT §§5,9, действующая preference policy; список уже рассмотренных ролей,
если доступен через read facade. Отсутствующее пользовательское решение не выдумывать.
**Читать:** `job_intel/recruiter_read_facade.py`, `job_intel/store.py`,
`config/product_search/career_profile.v2.yaml`. **Код не менять.**
**Результаты:** private `review-pool.json`, `shortlist.md`, `review-outcome.md`.

1. Зафиксировать UTC timestamp, HEAD, run ID и фактический интервал данных.
   По умолчанию использовать inventory, наблюдавшуюся за последние 7 суток;
   если свежего сбора нет, явно отметить это и не выдавать старые записи за текущие.
   DB открыть `mode=ro`, `PRAGMA query_only=ON`; не вызывать bootstrap.
2. Собрать очередь проверки по всем восьми role families Product SoT. Legacy score
   может быть отдельным справочным полем, но `accepted=true` и точный title match
   не служат единственным входным фильтром. Включить расширенные business/GM/growth
   названия и текстовые mandate signals. Зафиксировать запросы и знаменатель.
3. На первую сессию взять до 40 уникальных кандидатов после canonical URL/requisition
   dedup, распределив очередь между доступными семействами/географиями; не выбирать
   первые 40 из одного alphabetic company order. Это диагностический пул, не
   репрезентативная выборка рынка. Записать unavailable strata и правило порядка.
4. Проверить evidence достаточность пула; наиболее перспективные роли изучить
   подробнее. До 90 минут reviewer time — первоначальный лимит сессии, не SLA.
   При исчерпании записать непросмотренный остаток. Это ограничение внимания,
   а не основание заключать, что остальные роли нерелевантны.
5. Для **каждой роли, включаемой в shortlist**, открыть официальную страницу
   работодателя/ATS и записать URL, время, статус open/closed/unavailable и короткие
   evidence excerpts для существенных выводов. Убедиться в идентичности requisition
   и location; repost/другая страна не автоматически та же роль. Closed исключить;
   недоступную страницу оставить отдельным unresolved item, не подтверждённой ролью.
6. Явно описать шесть измерений: feasibility, mandate, company, transferability,
   career value, evidence confidence. Минимальный executive scope — реальное
   business/P&L/portfolio authority либо multi-team scope вместе со strategic
   business authority; тайтул и размер команды сами по себе недостаточны.
   US onsite/hybrid без explicit sponsorship — hard gate; remote оценивать по
   реальной country eligibility. KZ — обычный рынок, не fallback. Unknown sponsorship
   вне onsite US, compensation и timezone не превращать автоматически в reject.
7. Выдать **до 7**, включая 0: почему стоит внимания, что известно/неизвестно,
   human-suggested next step. Не генерировать machine verdict или скрытый numeric
   score. Stored raw lineage обозначить отдельно от свежей official evidence.
8. Получить решения Denis и записать дословный смысл причины/следующего шага,
   не выполняя автоматически outreach/applications. Измерить review time, если
   Denis его сообщает; отсутствие времени — unknown, не ноль.

**Проверка:** все выбранные роли имеют official recheck, идентичность, evidence,
шесть измерений и uncertainty; нет дублей или output-quota fill; результат
самоописан как manual_shortlist_route v1.0.0, no machine verdict, not gate evidence.
**DoD:** review выполнен в зафиксированных пределах и предоставлен владельцу.
Веха полезности закрыта только при хотя бы одной owner-confirmed useful opportunity
или meaningful next step. No-fill закрывает процедуру; ведёт в M2-R или именованный
repair, сохраняя факт, что это не доказательство отсутствия вакансий на рынке.

<a id="m1"></a>
## M1 — Повторяемое использование с человеком

**Зависимость:** M0 дал полезный результат. **Создать:**
`docs/runbooks/job-intel-assisted-search.md` и
`docs/templates/job-intel-manual-shortlist.md` (без персональных ответов).
**Private outputs:** второй release и append-only `reviewed-roles.jsonl`.

1. Превратить реально выполненную M0 процедуру в инструкцию: read command/SQL,
   параметры окна, dedup, порядок очереди, evidence fields, критерии stop/no-fill.
2. Хранить canonical role/requisition ID, source URL, дату проверки и human decision.
   Повтор не выдавать как новый; существенное изменение роли показать как update
   с причиной. Не перезаписывать CRM status. Ранее rejected не значит вечный запрет.
3. На следующем свежем сборе подготовить второй выпуск по этой инструкции.
   Сохранить reviewer minutes и размеры retrieval/review/output отдельно.
4. Проверить минимум пять passed-over/legacy-rejected записей с потенциальным
   business mandate и записать причины пропуска. Это диагностическая проверка
   false negatives, не оценка полного market recall.
5. Собрать явный отзыв Denis: что полезно, что повторяется, чего не хватает.
   Feedback создаёт гипотезы, а не изменяет preference SoT автоматически.

**DoD:** второй выпуск воспроизводим по инструкции, полезность подтверждена,
есть dedup history, причины, ограничения и измерение effort/unknown. Недостаточная
полезность возвращает исследование в M2-R; не добавляет новый gate к уже разрешённым
техническим работам. Автоматизировать сначала повторяемую затратную операцию.

<a id="m2-r"></a>
## M2-R — Где поиск теряет полезные вакансии

**Зависимость:** результат M0, в том числе no-fill. **Создать:**
`docs/research/job-intel-search-misses.md`; private reference set с URL/evidence.
**Читать:** `sources.py`, `ats_sources.py`, `text_backfill.py`,
`config/product_search/{search_contract.v1,source_capabilities.v1}.yaml`.

1. Сопоставить текущие queries и реально executed cells с восемью role families
   и географиями SoT. Разделить declared, attempted, productive, evidence-sufficient.
2. За ограниченную исследовательскую сессию до двух часов собрать внешним ручным
   поиском до 20 подтверждённых официальных вакансий-контролей; включить несколько
   доступных географий, non-fintech и non-standard executive titles, как минимум
   часть компаний вне registry. Не добирать неподходящими ради числа. Если scope
   ограничен доступностью, записать ограничения и фактический знаменатель.
3. Сопоставить controls с inventory по requisition/canonical URL. Каждому пропуску
   назначить проверяемую причину: source absent, query exclusion, cap/page gap,
   missing text, incorrect identity, stale/closed, assessment mismatch или unknown.
4. Измерить observed matched/eligible reference set и loss counts; не называть это
   полным recall рынка. Отделить потери discovery от ошибок interpretation.
5. Предложить следующий один ограниченный repair/research: какие roles возвращает,
   стоимость/latency, доступ, прекращение при неуспехе. Уже подтверждённые #4/#5/#17
   остаются evidence и не требуют повторного воспроизведения ручным shortlist.

**DoD:** reference set воспроизводим по датированным ссылкам, причины не взяты из
тайтлов без проверки, имеются ranked repair choices и ограничение обобщения.

<a id="m2-t"></a>
## M2-T — Текст вакансий в правильном потребителе

**Два независимых среза:** [#5 SmartRecruiters](https://github.com/DenisVanyushkin/hermes-agent/issues/5)
и [#4 LinkedIn](https://github.com/DenisVanyushkin/hermes-agent/issues/4).
**Код для проверки:** `product_search/acquisition_plugins/ats_snapshot.py`,
`product_search/acquisition_probe.py`, `ats_sources.py:fetch_smartrecruiters_detail`,
`text_backfill.py`; LinkedIn public interface в `browser_sourcing.py` / `browser_worker.py`.

1. SR: fixture RED на выходе probe evidence, а не только parser helper. Переиспользовать
   существующий bounded detail fetcher через additive probe seam. Сохранять sections,
   URL/hash/time, failed/unavailable и отсутствие текста честно; title не substitute.
2. LinkedIn: отдельный bounded detail-access research по уникальным canonical IDs
   в именованной approved browser/session среде. Проверить доступ к описанию и его
   соответствие роли; auth state не доказывает detail доступ. При блоке — явный
   outcome и альтернативный official employer URL; не обходить anti-bot ради объёма.
3. После доступного seam: RED/GREEN tests на result/title-only/login-wall/closed/
   mismatched-job, budget и dedup повторных details. Если нужен protected-path change,
   сначала exact amendment; не делать его побочным изменением M0.
4. Проверить оба потребителя: daily backfill уже есть и должен сохраниться;
   probe должен хранить recovered text и provenance в своём evidence package.

**DoD каждого среза:** tests+composition зелёные; в разрешённом свежем ограниченном
прогоне измерены usable-text before/after, unique candidates recovered и failures,
а не только символы. Недоступность отделена от parser defect. Полнота рынка не заявлена.

<a id="m2-c"></a>
## M2-C — Пер-компанийный лимит и видимость усечения

**Issue:** [#17](https://github.com/DenisVanyushkin/hermes-agent/issues/17).
**Код:** `job_intel/ats_sources.py:fetch_smartrecruiters`;
**Создать tests:** `tests/job_intel/test_smartrecruiters_budget.py`;
**existing regression:** `tests/job_intel/test_ats_vacancy_description.py` и
`tests/job_intel/test_text_backfill_fetchers.py`.

1. RED: две компании с количеством postings больше cap; вторая должна получать
   свой бюджет независимо от порядка. Проверить ровно cap, отсутствие page overshoot,
   no duplicate IDs и окончания пагинации, 429/empty/error.
2. Исправить счётчик в approved scope, не менять величину лимита вместе с багом.
3. Показать per-company found/collected/truncated, фактический marginal executive
   yield. Не объявлять cap достигнутым как source exhausted.
4. Отдельное исследование GH/Ashby caps: измерять пропущенные role families до
   предложения нового бюджета, не поднимать все caps без оценки latency.

**DoD:** invariant per-company budget проверен перестановкой компаний; bounded
live measurement при его разрешении отделён от fixture proof; новый budget —
отдельное обоснованное решение, если он требуется.

<a id="m2-q"></a>
## M2-Q — Запросы по мандату и действительным рынкам

**Вход:** M2-R loss map. **Код:** `job_intel/sources.py:ROLE_FAMILIES`,
`CONTEXT_FAMILIES`, `rotating_linkedin_queries`, `rotating_source_queries`;
`config/product_search/search_contract.v1.yaml`, `linkedin_geography.v1.yaml`.
**Создать:** `docs/research/job-intel-query-coverage.md`.

1. Составить mapping восьми Product SoT role families на запросы; title используется
   как vocabulary, финальная оценка по mandate. Непредставленные families назвать.
2. Сравнить ограниченные role-only и role+context варианты на одинаковых geography,
   времени и source interface. Existing measured-empty terms учитывать, не возвращать
   без новой улики. Регистрировать requested/resolved geography и unsupported cells.
3. Измерить уникальные usable candidates, matched controls M2-R, noise, latency.
   Расширение role vocabulary не должно добавлять industry weight в решение.
4. Предложить проверенный bounded daily plan и durable executed-cell accounting;
   календарная ротация сама по себе не является coverage ledger.

**DoD:** есть before/after контроль, новые query outcomes и реальные ограничения;
fixture tests на generation/rotation + composition evidence в разрешённом scope.
Не выдавать количество query IDs за покрытие рынка.

<a id="m2-s"></a>
## M2-S — Второй независимый broad-market источник

**Issue:** [#15](https://github.com/DenisVanyushkin/hermes-agent/issues/15).
**Создать:** `docs/research/job-intel-second-market-source.md`.
**Точки интеграции:** `job_intel/product_search/acquisition_plugins/`,
`config/product_search/source_capabilities.v1.yaml` и Search Contract.

1. Проверить доступные существующие источники прежде нового scraper. HH API работает
   в daily, но его географии/роль в probe нужно отдельно доказать. Global RemoteOK/
   Remotive feed не приписывать странам, которые его контракт не различает.
2. Для кандидатов сравнить supported cells, независимость discovery от registry и
   LinkedIn, official URL/text, freshness, auth/isolation, ограничения доступа, budget.
3. Провести разрешённый bounded pilot выбранного семейства и сравнить incremental
   unique usable/eligible opportunities после cross-source dedup с LinkedIn.
4. Представить add/no-add/другой кандидат/SoT amendment с точными пробелами и cost.
   Не менять B1 outcome table ради удобного результата. Open Market и seeded ATS
   знаменатели уже существуют и должны оставаться раздельными.

**DoD исследования:** есть сравнение альтернатив и решение по ограниченному pilot.
**DoD добавления:** контракт изоляции, fixture tests, productive evidence по заявленным
ячейкам и incremental value; формальный Gate A credit только по его отдельным правилам.
Глубокая LinkedIn pagination — альтернативное исследование gap, не второй источник.

<a id="m2-b"></a>
## M2-B — Дозагрузка с бюджетом и понятным учётом

**Код:** `job_intel/cli.py:_apply_text_backfill`, `job_intel/text_backfill.py`,
`job_intel/store.py:record_text_backfill/rows_needing_text`; существующие owning tests.
**Создать:** `docs/research/job-intel-backfill-budget.md`.

1. На последовательных существующих runs сравнить уникальные detail URLs, повторные
   попытки, budget-skipped/blocked/failed/unavailable и время. В run475 budget400
   израсходован SR, filled396; это не доказывает, что повышение budget всегда полезно.
2. Проверить, не загружается ли полный текст заново каждый день, не вытесняют ли
   первые компании/тайтлы подходящие гибридные роли. Сравнить приоритет по evidence
   deficit и reusable cached text против простого роста бюджета.
3. Предложить одно bounded изменение с call/time cap и rollback. Runtime env change
   не выполнять в качестве невидимого следствия docs review.
4. В отдельном кодовом срезе связать acquisition/backfill outcome с canonical ID,
   source URL/hash/time и producer; NULL больше не интерпретировать как not_attempted.
   Если нужна schema migration — согласовать её с M4 migration framework.

**DoD:** отчёт сравнивает затраты и дополнительные оцениваемые роли; tests покрывают
повторы, exhaustion и сохранение старого текста при failure. Глобальная миграция
provenance не является prerequisite M0/M1.

<a id="m3"></a>
## M3 — Сохранять опубликованную компенсацию

**Issues:** [#18 GH](https://github.com/DenisVanyushkin/hermes-agent/issues/18),
[#19 Ashby](https://github.com/DenisVanyushkin/hermes-agent/issues/19).
**Код:** `job_intel/ats_sources.py:fetch_greenhouse/fetch_ashby/_vacancy`;
текущий `Vacancy.salary` остаётся строкой, новой schema не требуется.

1. Проверить текущую официальную API документацию и response fixtures. GH сохраняет
   `content=true` и запрашивает pay ranges; Ashby включает compensation.
2. RED: raw compensation присутствует, но Vacancy.salary пуст. Исправить и запрос,
   и mapping; изменение флага без передачи salary в конструктор не решает задачу.
3. Проверить cents, currency, hourly/annual/unknown period, диапазоны для разных
   location, absent/malformed fields. Период и применимость диапазона не выдумывать.
4. Измерить published→mapped долю на разрешённой выборке и равенство переданного
   source meaning. Компания без опубликованных ranges не является регрессией.

**DoD:** source ranges сохраняются корректно и отображаются без потери смысла;
никакого влияния на selection/ranking и никаких inferred compensation floors.
Задача полезна независимо от M0–M2 и не задерживает их.

<a id="m4"></a>
## M4 — Довести до автономного продукта, переиспользуя сделанное

**Вход:** свежий корпус с явной областью представительности и source gaps.
Не требуется доказать исчерпание всего рынка. Ручной review не считается gate
evidence; его controls/feedback можно использовать для постановки задач, а gate
строит собственный валидный evidence package по своему контракту.

| Порядок | Подробное описание существующей работы | Результат/условие перехода |
|---|---|---|
| 1 | [Order 1 reconciliation](2026-08-24-order-1-gate-b-composition.md), [current execution order](../superpowers/plans/2026-08-10-job-intel-search-product-redesign.md#current-execution-order-agreed-2026-08-24) | Сверить #7 closed, #8/#9/#10 с evidence; не повторять закрытый код |
| 2 | [Order 2: corpus authority](../superpowers/plans/2026-08-10-job-intel-search-product-redesign.md#order-2-single-corpus-authority-and-valid-coverage) | Убрать split authority, rebuild representative corpus, финальный composition smoke |
| 3 | [Redesign Task 12 / Gate B](../superpowers/plans/2026-08-10-job-intel-search-product-redesign.md#task-12-benchmark-decision-v2-and-close-gate-b) | Current supervised runner, approved spend, six-dimensional human audit, replay/failures/cost; owner decision |
| 4 | [Redesign Tasks 13–19](../superpowers/plans/2026-08-10-job-intel-search-product-redesign.md#task-13-introduce-the-migration-framework-and-product-search-provenance-store) | Migration/provenance, immutable assessments, watchlist, caps, renderer, metrics, resumable orchestrator |
| 5 | [Redesign Task 20 / Gate C](../superpowers/plans/2026-08-10-job-intel-search-product-redesign.md#task-20-run-the-integrated-shadow-and-close-gate-c) | Integrated shadow/attention evidence; no Slack delivery |
| 6 | [Redesign Tasks 21–25 / Gate D](../superpowers/plans/2026-08-10-job-intel-search-product-redesign.md#task-21-add-the-typed-slack-envelope-and-transactional-outbox) | Typed outbox, guarded publisher, interaction handling, isolated staging |
| 7 | [Redesign Tasks 26–28 / Gate E](../superpowers/plans/2026-08-10-job-intel-search-product-redesign.md#task-26-add-product-metrics-and-harden-protected-channel-reconciliation) | Metrics, scheduler/deployment package, migration/silence/rollback rehearsal |
| 8 | [Cutover и acceptance](../superpowers/plans/2026-08-10-job-intel-search-product-redesign.md#post-completion-production-cutover) | Explicit pinned production window, activation and required runtime acceptance |

После cutover — шестинедельный pilot Product SoT: positive decisions, activated
opportunities per user attention, новые компании/географии, причины отказов,
стоимость/latency и counterfactual. Company/watchlist learning, monthly strategy и
калибровка улучшают поиск по реальным результатам; policy changes остаются явными.
Outreach, application packages и подача заявок — отдельные workflows, не prerequisite
поиска и не автоматически разрешены этим roadmap.
