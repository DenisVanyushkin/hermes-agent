# Job Intel: независимый аудит для roadmap, 2026-09-05

Автор: Codex. Read-only проверка canonical VPS и SQLite; не benchmark и не приёмка поиска. Наблюдения относятся к указанному HEAD и данным на момент чтения. Тесты, provider, acquisition, Slack и изменения runtime в этом аудите не запускались.

## 1. Проверенная база

- SSH: `hermes-agent`; repo `/home/hermes/.hermes/hermes-agent`.
- Branch `local/customizations`; HEAD `c6283e8c5b4dc712b5c00447dc37c16c3800ea57`.
- Tracked tree clean. Unrelated untracked: `docs/plans/2026-08-22-upstream-sync-gate-review-fixes.md`.
- Protected stash: `stash@{0}: WIP on feature/smart-approvals-all-gates: 4c8da1a23`.
- Product worktree `codex/job-intel-product-search@c5b4de9dba`; Order 1 worktree `codex/order-1-gate-b-composition@dd901dfefc`.
- Mac checkout `feature/smart-approvals-all-gates@17adad08348bb25e37666cc2aeca6f8424ed4610` содержит многочисленные прежние изменения; он не использован как актуальная runtime authority.

## 2. Цепочка SoT и различия, влияющие на план

1. `docs/superpowers/specs/2026-08-10-job-intel-search-product-redesign-design.md`, approved v1.0.0, PS-SOT-2026-08-10-v1: продуктовая политика, mandate-first, восемь role families, Open Market / Strategic Watchlist отдельно от Core / Exploration и системного verdict. Appendix B явно supersedes конфликтующие legacy scoring/company/geography правила.
2. `config/product_search/career_profile.v2.yaml` ссылается на Product SoT, Candidate Facts, Preference Model и Search Contract по hashes. Проверены реальные SHA: Product SoT `430340de2613ee733926d73ce276c93676fe64b1841bb2f68f3f9303b61fc3a8`; Candidate Facts `/home/hermes/.hermes/private/career/denis_vanyushkin_structured_resume_v1_1.json` — `7219eea2fbf04c92291254f83a76b8d2d1ef53e6004ac64ff4601c726eb9fac9`; Preference Model — `d9cab661eb5b492f1566c661ecf6b4038cd5332793c37349089c3f0fb3585aeb`. Все три совпадают с profile references.
3. `job_intel/preference_model/career-preference-model.yaml:253` запрещает missing/low compensation влиять на gating/ranking до явного изменения SoT. Product SoT §5.7 сохраняет compensation unknown; timezone и неизвестное sponsorship вне onsite US также не автоматический reject. Старые KZ fallback / exploration quota superseded Product SoT.
4. `docs/job-intel-improvements/jul19/shadow-evaluator-decision-sot.md` и `semantic-vacancy-understanding-sot.md` содержат evidence/decision invariants; их исторические заголовки «runtime НЕ реализован» не отражают текущего наличия runtime modules. Product SoT Appendix B задаёт границы совместимости.
5. `docs/career-search-source-of-truth.md` — draft Recruiter MVP contract; `docs/job-intel-source-of-truth.md` — audit 2026-06-28. Их исторические runtime статусы и legacy policy не заменяют свежую проверку и approved Product SoT. Read facade уже есть в `job_intel/recruiter_read_facade.py`, хотя июньский audit перечисляет его отсутствие.
6. `/home/hermes/.hermes/job_intel/career_facts/preferences.yaml` — отдельный legacy Recruiter bundle от 2026-07-04 с industry weights. Не использовать его как замену Career Profile v2. Candidate facts в этом bundle также отдельный hash; конфликт нельзя молча разрешать смешением файлов.
7. Gate A `gate-closure.json` содержит историческое proceed 2026-08-16. `docs/evidence/product-search-gate-a/2026-08-26-p0-authorization.md` явно задаёт `gate_a_reopened: true`, `prior_product_claim_superseded: true`. Доказательство жизнеспособности сбора, продуктивность запросов, полнота конкретного запроса и достаточность наблюдения рынка — разные выводы.
8. Gate B `owner-decision.md` хранит историческую неудачу 2026-08-21 и pending; Execution status reconciliation 2026-08-24 в redesign plan объясняет retirement старого v3 launch protocol. Будущая работа использует текущий supervised runner, новый reviewed corpus/runtime/spend contract; не восстанавливает старую fortress.
9. `docs/plans/2026-08-24-order-1-gate-b-composition.md` отмечает Order 1 closed. Это закрытие технической композиции, не принятие Decision v2 и не разрешение Task 13. Orders 2–3: единая corpus authority / представительность, затем benchmark и owner decision.

## 3. Фактический runtime и свежесть

`systemctl show job-intel-shadow-collection.service` показал `ExecMainStatus=0`, завершённый oneshot; `systemctl list-timers --all job-intel*` — один shadow timer, следующий запуск 2026-09-06 09:17 CEST (12:17 Asia/Almaty).

Unit читает `/etc/job-intel/job-intel-shadow.env`, запускает `scripts/job_intel_shadow_preflight.sh` затем `scripts/job_intel_host_wrapper.sh daily`. Credentials stores недоступны в namespace. Pin равен текущему HEAD. `scripts/job_intel_shadow_preflight.sh:83–93` требует точное равенство всего commit SHA: даже docs-only commit на canonical branch требует согласованной процедуры интеграции и сохранения pin/integrity. Это операционная зависимость публикации документа, не повод менять source policy.

SQLite открыта напрямую `sqlite3.connect('file:/var/lib/job-intel/state/job_intel.sqlite3?mode=ro', uri=True)`, затем `PRAGMA query_only=ON`. `JobIntelStore.bootstrap()` не вызывался.

- Последний run **475**, daily, `2026-09-05T07:17:46.255088+00:00` → `2026-09-05T08:33:30.975369+00:00`, status `ok`.
- Runs 470–474 завершены 2026-09-04; предыдущий 469 — 2026-08-27. Это восстановление после разрыва, не доказательство многонедельной непрерывности.
- Run 475: **3771 observability rows**, legacy accepted **25**, notified **0**. Эти единицы не являются Decision v2 stage 4 или полезными пользователю рекомендациями.
- Источники observability run 475: Greenhouse 2235, SmartRecruiters 863, Ashby 469, Lever 88, Teamtailor 50, LinkedIn 27, HeadHunter 26, Recruitee 13. Внутри источников число distinct vacancy_key равно rows; это не доказательство cross-source дедупликации.
- Metadata delivery: `status=suppressed`, attempts=0, success=false, reason `outbound delivery is disabled by JOB_INTEL_DELIVERY_DISABLED`.
- LinkedIn metadata: status=ok, hits=34, но session_health.status=degraded; 27 observation rows. Нельзя смешивать hits, inventory и observation rows или принимать внешний ok за отсутствие деградации.
- HeadHunter: hits=35, session_health=ok, API; 26 observation rows.
- SmartRecruiters: hits=1163; text_backfill attempted=400, filled=396, failed=1, unavailable=3. Daily уже имеет работающий backfill; frozen probe snapshot и ежедневный путь различаются.
- DuckDuckGo, RemoteOK, Remotive: skipped в последнем daily. Target companies: hits=0. Успешный timer не доказывает Open Market breadth.

## 4. Текстовое сырьё доступно сейчас

Запрос: `SELECT source,count(*),sum(length(description)>500) FROM vacancies WHERE last_seen_at >= '2026-09-03' GROUP BY source`.

| Source | Inventory rows | Description >500 chars |
|---|---:|---:|
| greenhouse | 2312 | 2312 |
| ashby | 476 | 476 |
| smartrecruiters | 905 | 441 |
| lever | 89 | 89 |
| linkedin | 77 | 0 |
| teamtailor | 55 | 55 |
| headhunter | 43 | 43 |
| recruitee | 13 | 13 |

Текстовая длина — диагностический proxy, не доказательство достаточности mandate/feasibility evidence или актуальности вакансии на официальной странице. `last_seen_at` — время наблюдения, не публикации и не подтверждение открытого найма.

Отдельно проверен риск provenance: `text_backfill_state` непустой у **5** свежих записей (HH ok3/failed1, SR ok1); daily in-memory backfill не заполняет этот persistent state. NULL не доказывает, что попытки не было. Но это также не делает происхождение всех текстов неизвестным: у GH2312 сохранён `metadata_json.raw.content`, у Ashby476 — `raw.descriptionPlain` / `descriptionHtml`; нормализация ровно как в fetcher (`re.sub` tags, collapse whitespace, strip) восстановила `description` побайтово у **2788/2788** этих записей. Это согласованность stored raw → normalized text, не независимое подтверждение HTTP capture или открытого статуса. Для остальных источников такая проверка здесь не выполнена. M0 сохраняет доступный raw lineage и помечает недоказанное unknown; official-page recheck остаётся обязательным для свежести и claim evidence. Задача backfill accounting должна отличать raw source text, fetched detail, failed/unavailable/not_attempted и budget-skipped, не подменяя это NULL-полем.

Отдельная title-only выборка обнаружила свежие записи с полными описаниями и executive product названиями, включая Product Strategy/Product Director в Singapore, Senior Product Director в London, Director of Product в Berlin. Та же выборка содержит Product Design, Product Marketing, internal systems и US-only вакансии: title match не является fit. Вакансии в рамках этого аудита не рекомендованы и не перепроверены live на странице работодателя.

Вывод: первая ручная проверка качественно достаточного подмножества технически возможна без ожидания заполнения всего корпуса, зарплат и LinkedIn pagination. Наличие подходящих ролей ещё надо доказать самой проверкой. Корпус 2026-08-29 нельзя использовать как текущий shortlist без revalidation.

## 5. Код, определяющий кратчайший путь и следующие задачи

- `job_intel/cli.py:2073 run_daily`: store bootstrap → acquisition → legacy classify/score; импортирует `score_vacancy_v3_shadow`. Не подключает Product Search Decision v2. `v3_shadow` metadata не означает Product Search shadow Gate C.
- `job_intel/recruiter_read_facade.py:RecruiterReadFacade`: существующий read-only доступ к vacancy/evaluation/CRM context. Использовать доступные read methods; не вызывать lifecycle writers. Устаревание по timestamps у фасада не заменяет official-page freshness check.
- `job_intel/cli.py:_apply_text_backfill` и `job_intel/text_backfill.py`: daily backfill до classification, default budget 400; failed backfill сохраняет текущую обработку. `job_intel/ats_sources.py:fetch_smartrecruiters_detail` уже разбирает jobAd sections.
- `job_intel/product_search/acquisition_plugins/ats_snapshot.py:AtsSnapshotSource.__call__`: возвращает fetcher vacancies, daily `_apply_text_backfill` не подключает. Поэтому фиксация пустого описания у probe не доказывает, что весь daily тракт требует того же исправления. Задача должна называть конкретного потребителя и проверять обе композиции.
- `job_intel/ats_sources.py:fetch_greenhouse` запрашивает `content=true`; `fetch_ashby` — `includeCompensation=false`. Ни один не передаёт salary в `_vacancy`; исправление требует и запроса, и mapping, и проверок absent/currency/period/location applicability. API flag не гарантирует salary у всех вакансий. Это улучшение evidence, не prerequisite shortlist по политике.
- В тех же fetchers есть `jobs[:max_jobs_per_company]`; cap ограничивает наблюдение. Исследование должно измерять marginal executive yield после cap, а не просто поднимать лимит до бесконечности.
- В `fetch_smartrecruiters` проверка `len(vacancies) >= max_jobs_per_company` использует общий для компаний accumulator. Дефект #17 подтверждён чтением текущего кода: это отдельная ошибка бюджета на компанию, а не только исследование величины cap.
- `job_intel/sources.py:ROLE_FAMILIES`, `CONTEXT_FAMILIES`, `rotating_linkedin_queries`: пять legacy role groups × четыре context groups; восемь role families Product SoT не равны этому набору. Запрос role AND context может исключать подходящий mandate без этих слов. 18 daily queries с 24 supported cells — план ротации, а не доказанный ledger покрытия; mapping также содержит unsupported cells.
- `job_intel/product_search/{contracts,company_evidence,evidence_synthesis,decision_v2}.py`: существующие domain modules для автоматизации; не переписывать заново ради MVP.
- `job_intel/product_search/gate_b_evidence_runner_v1.py`: текущий supervised collection/decision/evaluation путь; `gate_b.py` содержит legacy machinery, наличие которой само по себе не означает активный executable route.

## 6. Приоритет, который следует из проверки

1. Ограниченный ручной shortlist из свежей inventory: evidence review и official-page recheck выбранных кандидатов по действующей политике; no-fill при отсутствии подходящих; отдельный manual artifact и ручное решение пользователя. Не использовать legacy accepted как единственный recall gate, не делать скрытый второй machine score.
2. Сразу после первого выпуска: воспроизводимый manual workflow, дедуп/повторы, отрицательная выборка пропущенных ролей и обратная связь с причиной. Пользовательское подтверждение полезности определяет дальнейшие затраты.
3. Исправлять text/coverage bottlenecks по фактически потерянным релевантным кандидатам: LinkedIn details, probe SmartRecruiters backfill, query vocabulary, caps и второй независимый broad-market источник. Ручное использование может продолжаться, formal coverage остаётся unestablished.
4. Зарплата — небольшая полезная параллельная задача, не блокирующий этап и не повод менять preferences.
5. После представительного свежего корпуса — единая corpus authority, Gate B human-audited Decision v2, затем по одобренному redesign плану persistence/portfolio/orchestrator, Gate C, Slack staging и cutover. Не путать раннюю ручную полезность с автономным продуктом.

## 7. Границы проверки

Не выполнялись тесты, внешние API источников, provider benchmark, поиск вакансий для выдачи пользователю, изменения DB/таймеров/pin, Slack, CRM или применение к вакансии. Числа frozen Gate A snapshot предоставлены Claude и требуют его воспроизводимой evidence-ссылки; таблица выше измерена независимо на текущей DB.

## 8. Tracker сверка 2026-09-05

Read-only `gh issue list --repo DenisVanyushkin/hermes-agent --state all --limit 50 --json number,title,state,url,updatedAt` и `gh issue view N --json number,title,body,state` для N=4,5,15,17,18,19.

- [#4](https://github.com/DenisVanyushkin/hermes-agent/issues/4), [#5](https://github.com/DenisVanyushkin/hermes-agent/issues/5), [#15](https://github.com/DenisVanyushkin/hermes-agent/issues/15), [#17](https://github.com/DenisVanyushkin/hermes-agent/issues/17), [#18](https://github.com/DenisVanyushkin/hermes-agent/issues/18), [#19](https://github.com/DenisVanyushkin/hermes-agent/issues/19) открыты. Их тела прочитаны. Historic measurements относятся к указанным в issue корпусам/датам, не автоматически к run475.
- [#7](https://github.com/DenisVanyushkin/hermes-agent/issues/7) закрыта. Не ставить V1/V2 incompatibility как заново обнаруженный открытый blocker.
- [#8](https://github.com/DenisVanyushkin/hermes-agent/issues/8), [#9](https://github.com/DenisVanyushkin/hermes-agent/issues/9), [#10](https://github.com/DenisVanyushkin/hermes-agent/issues/10) открыты в tracker, хотя canonical Order 1 plan фиксирует техническое закрытие. Их статус требует reconciliation по evidence; не дублировать реализацию исключительно на основании OPEN.
- #18 уже требует запрос **и** salary mapping, сохранение inactive compensation policy, правильные cents/period. #19 отдельно признаёт допустимый ноль опубликованных диапазонов по компании. Не превращать их в обещание 100% salary completeness.
- #15 требует независимый продуктивный broad-market источник и отдельный Open Market denominator. HH daily работает, но это не доказывает поддержанные им ячейки Product Search probe. Remote feeds не дают автоматического права приписать региональное покрытие.
- Split corpus constants подтверждены текущим кодом: `gate_b.py:124` / `v3-fragment-allowlist.yaml:3` — `5b8e29b0...`; `input_materialization.py:45,54` — `b1db802d...`. Текущий supervised runner достигает проверки `recordings.verify` в исходнике; факт реального benchmark исполнения из этого не следует.
