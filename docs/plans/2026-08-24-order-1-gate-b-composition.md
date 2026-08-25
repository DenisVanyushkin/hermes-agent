# Order 1 — доверенная боевая композиция Gate B: план реализации

> **Execution contract:** выполнять пункт за пунктом; на каждое изменение поведения —
> TDD; перед каждым заявлением о готовности — проверка, а не намерение. Завершить,
> проверить и закоммитить один ограниченный срез прежде чем начинать следующий.
> Зелёный прогон тестов никогда не означает продуктового или продового одобрения.

**Дата:** 2026-08-24
**Базовый HEAD:** `8a094e0a84`
**Источник порядка:** `docs/superpowers/plans/2026-08-10-job-intel-search-product-redesign.md`,
раздел «Current execution order (agreed 2026-08-24)», Order 1.
**Репозиторий issues:** https://github.com/DenisVanyushkin/hermes-agent

**Goal:** довести боевую композицию `run_collection` до того, чтобы строка проходила
от `dispatch` до `recordings.verify` включительно, записывая согласованные
доказательства — транспортное для леджера и V2 для решения, — и чтобы этот проход
был доказан сквозным smoke на собранном артефакте, а не заявлен.

**Architecture:** producer владеет синтезом и публикацией V2; runner передаёт
producer'у полную проекцию `EvidenceSynthesisInputV2` и отдельно редактированный
`provider_payload`, транспорт видит только payload. Transport receipt и V2-envelope
остаются **разными связанными артефактами**: леджер якорится на transport SHA,
authority-поля живут в V2-envelope и покрыты его печатью. Generic semantic record
остаётся отдельным sealed-артефактом и authority-полей не несёт.

**Tech Stack:** Python 3.12, Pydantic v2, pytest, ruff, SQLite/WAL, systemd.

**Где ведётся работа:** отдельный worktree на VPS, не живой чекаут
(`/home/hermes/.hermes/hermes-agent` резидентный агент периодически `reset --hard`-ает).
Ветка создаётся от `8a094e0a84`.

## Предмет

Ни одна строка не проходит боевой путь `run_collection` целиком. План закрывает три
issue, доводящие путь до `recordings.verify` и делающие его самопроверяемым:
[#9](https://github.com/DenisVanyushkin/hermes-agent/issues/9),
[#10](https://github.com/DenisVanyushkin/hermes-agent/issues/10),
[#8](https://github.com/DenisVanyushkin/hermes-agent/issues/8).

**Оговорка о состоянии #9.** Тело issue #9 описывает состояние до коммита
`802dd48698` (2026-08-24 06:39, «Bind Gate B ledger to transport receipts»). На
базовом HEAD `_AuthorityRecordingStore` **не инстанцируется нигде**
(`grep -rn "_AuthorityRecordingStore(" job_intel/ tests/ scripts/` — пусто), а
`v2_record` в `gate_b_evidence_runner_v1.py` собирается со всеми пятью
authority-полями **до** `seal_record`. Поэтому раздел 1 — это сверка и уборка, а
не восстановление декоратора в боевом пути. Утверждение issue «любая запись,
прошедшая через этот декоратор, не читается обратно» остаётся верным про сам
декоратор, но боевой путь через него больше не ходит.

## Порядок исполнения и почему он не переставляется

Разделы 1 → 2 → 3 исполняются строго в этом порядке.

- Раздел 1 идёт первым как регрессионный gate: он фиксирует, где именно живёт
  authority и почему generic transport record её не несёт. Без этой фиксации
  раздел 2 может «починить» anchor, вернув authority не в тот артефакт.
- Раздел 3 нельзя считать закрытым до раздела 2: smoke доказывает композицию, а
  композиции пока нет — зелёный smoke до раздела 2 означал бы, что он не доходит
  до предмета доказательства.

## Общие инварианты

- [ ] Инварианты соблюдены во всех разделах
- [ ] Ни один пункт не правит `provider_payload` так, чтобы в него попадали
      намеренно скрытые поля, и ни один не сдвигает `input_sha256`, по которому
      сверяется манифест. Прямой запрет из тела #10: «⚠️ **Дописывать поля в
      `provider_payload` нельзя:** (1) это отправит провайдеру намеренно скрытые
      данные; (2) сдвинет `input_sha256`, по которому сверяется манифест».
- [ ] Transport receipt и V2-envelope остаются разными артефактами; authority-поля
      не дописываются в generic transport receipt.
- [ ] Каждая правка сопровождается тестом, который **падает до** правки и
      **проходит после**; «тест написан» без зафиксированного красного прогона
      не засчитывается.
- [ ] Тесты запускаются интерпретатором worktree, а не gateway-venv: под
      gateway-venv часть проверок ложно краснеет из-за pysqlite3-шима
      ([#6](https://github.com/DenisVanyushkin/hermes-agent/issues/6)).
- [ ] Ограничение по времени задаётся внешним `/usr/bin/timeout`, а не аргументом
      `--timeout`: плагина `pytest-timeout` в каноническом venv нет.
- [ ] Полный `pytest` по репозиторию на VPS не запускается: он даёт loadavg ~50
      и systemd-вотчдог убивает живой гейтвей. Только адресные наборы.
- [ ] Прогон без итоговой строки pytest считается убитым, а не «чистым».

## Вне рамок

- Формат самой записи и состав authority-полей (явный out of scope #9).
- Контракт V2 provider-record и полноценный envelope (явный out of scope #9).
- Механика committed-budget record `gate_b_spend_record_v1.py` — она работает
  как задумано (явный out of scope #8).
- Живые прогоны с реальным провайдером и реальными тратами (явный out of scope #8).
- Cross-process recovery: возврат снесённой машинерии не планируется, остаточный
  риск назван в пункте 2.4.
- Назначение производственного канонического corpus SHA и пересборка корпуса —
  это Order 2, issues #5 и #4.

---

## 1. Authority живёт в V2-envelope, generic transport record её не несёт

- [x] **Раздел 1 выполнен целиком**

**Закрывает:** [issue #9](https://github.com/DenisVanyushkin/hermes-agent/issues/9)

> `_AuthorityRecordingStore` дописывает пять authority-полей **после** `capability.seal_record(...)`,
> поэтому сохранённая запись не соответствует собственной печати. При обратном чтении
> `RecordingStore.load` пересчитывает `metadata_sha256` уже по обогащённой записи, не сходится с
> сохранённым значением и отвергает её как повреждённую. **Любая запись, прошедшая через этот
> декоратор, не читается обратно.**

> ## Expected behavior
> Запись, сохранённая через `_AuthorityRecordingStore`, читается обратно без ошибок, а
> authority-поля входят в подписанный набор: печать выполняется **после** того, как в записи есть
> все поля, включая authority.

### 1.1 Зафиксировать реальное состояние: дефект есть, боевой достижимости нет

- [x] **Пункт 1.1 выполнен**

**Закрывает** направление из Suggested scope [issue #9](https://github.com/DenisVanyushkin/hermes-agent/issues/9):

> - тест round-trip: сохранить через декоратор → прочитать → сверить печать

**Задачи**

- [x] Написать прямой воспроизводитель: настоящий `RecordingStore` под
      `_AuthorityRecordingStore`, запись запечатана `capability.seal_record(...)`,
      сохранена через декоратор, прочитана обратно через `store.load(input_hash)`.
- [x] Добавить контрольную группу в том же тесте: та же запись через
      `RecordingStore` напрямую читается успешно — чтобы тест доказывал, что
      разница именно в декораторе, а не в записи.
- [x] Зафиксировать отсутствие боевой достижимости:
      `grep -rn "_AuthorityRecordingStore(" job_intel/ tests/ scripts/` даёт пусто.
- [x] Записать в план или в коммит, что evidence issue #9 относится к состоянию до
      `802dd48698`, и какой именно факт устарел.

**DoD**

- [x] Воспроизводитель **падает** на базовом HEAD с `recording_corrupt` (или
      эквивалентным `LLMProviderError`), вывод прогона с итоговой строкой приложен.
- [x] Ветка контрольной группы (`RecordingStore` напрямую) в том же прогоне
      **проходит** — иначе тест меряет не декоратор.
- [x] Отсутствие инстанцирований зафиксировано выводом команды, а не утверждением.

### 1.2 Убрать мёртвый декоратор и закрепить текущую архитектуру тестом

- [x] **Пункт 1.2 выполнен**

**Закрывает** направление из Suggested scope [issue #9](https://github.com/DenisVanyushkin/hermes-agent/issues/9):

> - `_AuthorityRecordingStore` в `job_intel/product_search/gate_b_evidence_runner_v1.py`

**Задачи**

- [x] Удалить мёртвый `_AuthorityRecordingStore`, а не возвращать его в боевой путь:
      после `802dd48698` authority принадлежит V2-envelope, и восстановление
      декоратора означало бы дописывание authority в generic sealed artifact.
- [x] Закрепить текущую архитектуру тестом: generic transport record остаётся raw,
      round-trip через `SemanticRecordingStore` проходит, authority-полей в нём нет.
- [x] Закрепить вторым тестом, что `v2_record` содержит все пять authority-полей и
      `semantic_transport_record_sha256` **до** вызова `seal_record`.
- [x] Если исполнитель найдёт живого потребителя декоратора и решит его сохранить —
      это требует отдельного обоснования в плане и API вида «обогатить → запечатать
      → сохранить»; одинаковая перестановка обоих путей `seal → save` **не**
      является решением и на текущем коде была бы регрессией.

**DoD**

- [x] Оба закрепляющих теста проходят; тест на порядок сборки `v2_record` падает,
      если authority-поле перенести после `seal_record` (проверяется мутацией).
- [x] После удаления декоратора адресный набор тестов product_search и semantic
      runtime зелёный, вывод с итоговой строкой приложен.
- [x] Команда `grep -n "seal_record"` по обоим модулям используется как подсказка
      ревьюеру, но сама по себе DoD не закрывает: доказательство — тест.

### 1.3 Доказать, что HMAC покрывает authority именно V2-envelope

- [x] **Пункт 1.3 выполнен**

**Закрывает** критерии приёмки [issue #9](https://github.com/DenisVanyushkin/hermes-agent/issues/9):

> - `verify_record()` на такой записи проходит, то есть authority-поля покрыты `metadata_sha256` и HMAC
> - подмена любого authority-поля после сохранения детектируется как нарушение печати
> - существующие потребители generic semantic recording не сломаны

**Задачи**

- [x] Мутационный тест: подмена каждого из пяти authority-полей V2-envelope после
      сохранения детектируется как нарушение печати.
- [x] Контрольная немодифицированная запись проходит тот же verifier.
- [x] Найти всех потребителей generic semantic recording
      (`grep -rn "RecordingStore\|governed_structured_call\|verify_record"` по
      `job_intel/` и `tests/`), выписать список в коммит и прогнать адресный набор.

**DoD**

- [x] Мутационный тест проходит по каждому authority-полю по отдельности.
- [x] Адресный прогон тестов semantic recording зелёный, вывод с итоговой строкой
      приложен.
- [x] Все четыре критерия приёмки #9 отмечены по одному, с указанием теста,
      который каждый доказывает.

---
## 2. Сквозной проход `run_collection` до `recordings.verify`

- [x] Раздел 2 выполнен целиком

Закрывает: [issue #10](https://github.com/DenisVanyushkin/hermes-agent/issues/10)

> `run_collection` проводит строку от dispatch до `recordings.verify` включительно и записывает
> согласованные доказательства: транспортное — для леджера, V2 — для решения.

**Задачи**

- [x] Выполнить подпункты 2.1–2.4 в указанном порядке: каждый следующий blocker становится
  наблюдаемым только после устранения предыдущего.
- [x] После зелёного минимального E2E выполнить подпункты 2.5–2.7; они не блокируют достижение
  `recordings.verify`, но блокируют закрытие issue #10 и агрегатный чекбокс раздела 2.
- [x] Выполнить интеграционный gate 2.8 без ослабления тестов, удаления проверок или подмены
  production-композиции моками.

**DoD**

- [x] Все агрегатные чекбоксы 2.1–2.8 отмечены только после выполнения их собственных DoD.
- [x] `run_collection` проводит production-shaped строку через `dispatch`, Decision v2,
  `RecordingStore.save_exclusive` и `recordings.verify` без реального provider/network/spend.
- [x] Transport receipt и V2-envelope остаются разными, явно связанными артефактами; ни один
  SHA не используется одновременно как transport anchor и как hash V2-envelope.

### 2.1 Typed V2 dispatch seam: полная проекция локально, редактированный payload в transport

- [x] Пункт 2.1 выполнен целиком

Закрывает: [issue #10](https://github.com/DenisVanyushkin/hermes-agent/issues/10)

> typed V2 dispatch seam: `run_collection` отдаёт producer'у полную проекцию **и** редактированный
> payload; синтез получает проекцию, транспорт — только payload; публикация V2 остаётся у producer
> (согласовано при разборе — не переносить сборку записи в runner, иначе размывается граница
> receipt/HMAC)

**Задачи**

- [x] В `tests/product_search/test_gate_b_full_composition_e2e.py` добавить RED-контроль
  `test_live_dispatch_keeps_full_projection_local_and_sends_only_redacted_payload`: producer
  получает полный `EvidenceSynthesisInputV2`, а fake transport не видит `vacancy_evidence` и
  `prohibited_company_claim_text_sha256s` и получает byte-identical `provider_payload()`.
- [x] В `job_intel/product_search/gate_b_evidence_runner_v1.py` ввести typed immutable request
  `GateBDispatchRequestV2` с полями `synthesis_input: EvidenceSynthesisInputV2` и
  `provider_payload: Mapping[str, object]`; конструктор отвергает payload, не совпадающий с
  `synthesis_input.provider_payload()` по каноническим байтам.
- [x] Изменить `GovernedProvider.dispatch`, `GovernedStructuredProviderAdapter.dispatch`,
  `_LiveGateBProvider.dispatch` и тестовые providers так, чтобы они принимали
  `GateBDispatchRequestV2`, а не неразличимый `dict`.
- [x] В `run_collection` передавать `projected` как полную `synthesis_input`, а результат
  `projected.provider_payload()` — как отдельный redacted `provider_payload`; manifest-bound
  `row.input_sha256` продолжает считаться только по provider-visible payload.
- [x] В `_LiveGateBProvider` передавать полную проекцию в `run_evidence_synthesis_v2`, оставляя
  transport-вызов и V2-публикацию внутри producer; не добавлять скрытые поля в provider payload.

**DoD**

- [x] До реализации команда
  `/usr/bin/timeout 90s .venv/bin/pytest -q tests/product_search/test_gate_b_full_composition_e2e.py::test_live_dispatch_keeps_full_projection_local_and_sends_only_redacted_payload`
  падает на отсутствии typed seam; после реализации возвращает `1 passed`.
- [x] Команда
  `rg -n 'EvidenceSynthesisInputV2\.model_validate\(payload\)' job_intel/product_search/gate_b_evidence_runner_v1.py`
  не находит прежнюю попытку восстановить полный V2-вход из редактированного payload.
- [x] В тесте явно проверены оба отрицательных утверждения: provider-visible mapping не содержит
  `vacancy_evidence` и `prohibited_company_claim_text_sha256s`; добавление любого из них меняет
  canonical payload и отклоняется typed seam до dispatch.

### 2.2 Разделить transport anchor и SHA V2-envelope

- [x] Пункт 2.2 выполнен целиком

Закрывает: [issue #10](https://github.com/DenisVanyushkin/hermes-agent/issues/10)

> `reconcile` коммитит SHA transport receipt (`:2588-2614`); `_provider_dispatch_result` считает
> свой SHA от V2-записи (`:2460-2467`) и кладёт в metadata `SealedRecording` (`:2783-2794`);
> `_verify_dispatch_anchor` требует их равенства (`:763-781`).

**Задачи**

- [x] Добавить RED-тест
  `test_recording_anchor_uses_semantic_transport_sha_and_keeps_v2_envelope_sha`, который создаёт
  разные SHA для generic semantic transport record и V2-envelope и воспроизводит текущий
  `recording provider anchor mismatch`.
- [x] При создании `SealedRecording.metadata` сохранять оба значения:
  `semantic_transport_record_sha256` из проверенной V2 metadata и отдельный
  `provider_record_sha256` по каноническим байтам V2-envelope.
- [x] Изменить `RecordingStore._verify_dispatch_anchor`: сравнивать
  `dispatch_entry.recording_sha256` только с `metadata.semantic_transport_record_sha256`; не
  сравнивать ledger anchor с `provider_record_sha256`.
- [x] Оставить `provider_record_sha256` в evidence/decision lineage и добавить отрицательные
  тесты на подмену каждого из двух SHA независимо.

**DoD**

- [x] `/usr/bin/timeout 90s .venv/bin/pytest -q tests/product_search/test_gate_b_full_composition_e2e.py::test_recording_anchor_uses_semantic_transport_sha_and_keeps_v2_envelope_sha`
  возвращает `1 passed` и доказывает, что оба SHA различны.
- [x] `/usr/bin/timeout 90s .venv/bin/pytest -q tests/product_search/test_gate_b_recording_replay.py`
  проходит целиком; подмена transport SHA даёт `recording provider anchor mismatch`, а подмена
  V2-envelope SHA отклоняется собственной V2/evidence-проверкой, не ledger-сравнением.
- [x] `test_full_run_collection_reaches_recording_provider_anchor` доходит до
  `recordings.verify` и проходит его.

### 2.3 Keyed verify не зависит от поля внутри проверяемой записи

- [x] Пункт 2.3 выполнен целиком

Закрывает: [issue #10](https://github.com/DenisVanyushkin/hermes-agent/issues/10)

> `_provider_record` зовёт keyed verifier только если запись содержит
> `provider_record_kind == "gate-b-evidence-synthesis-v2"` (`:2355-2368`), а дискриминатор лежит
> внутри самой записи. Удалить его, изменить cost, честно пересчитать `metadata_sha256` — запись
> принимается.

**Задачи**

- [x] Сохранить существующий strict-xfail
  `test_v2_record_without_discriminator_is_rejected` как RED-контроль до исправления.
- [x] Изменить `_provider_record`, чтобы наличие keyed verifier определялось доверенным producer/
  store contract, а не `provider_record_kind` или любым другим полем загруженной записи.
- [x] Добавить параметризованный тест, который по одному удаляет или изменяет каждое поле
  V2-record, пересчитывает незакрытый `metadata_sha256`, сохраняет старый HMAC и требует
  `LLMProviderError("provider_metadata_mismatch")`; `provider_record_kind`, cost и authority SHA
  входят в набор мутаций явно.
- [x] После GREEN удалить `@pytest.mark.xfail` с существующего теста, не меняя его ожидаемое
  fail-closed поведение.

**DoD**

- [x] `/usr/bin/timeout 90s .venv/bin/pytest -q tests/product_search/test_gate_b_full_composition_e2e.py::test_v2_record_without_discriminator_is_rejected`
  возвращает `1 passed`, а не `xfailed` или `xpassed`.
- [x] Параметризованный mutation-тест проходит для каждого поля V2-record; контрольная
  немодифицированная запись проходит тот же keyed verifier.
- [x] `rg -n 'xfail.*HMAC|HMAC.*xfail' tests/product_search/test_gate_b_full_composition_e2e.py`
  не находит маркер, маскирующий этот дефект.

### 2.4 Восстановление после сбоя V2-публикации без повторного provider call

- [x] Пункт 2.4 выполнен целиком

Закрывает: [issue #10](https://github.com/DenisVanyushkin/hermes-agent/issues/10)

> Леджер коммитится до возврата рантайма, V2 публикуется позже. При сбое публикации в леджере
> остаётся терминальный исход без решения; resume невозможен — replay-метаданные несут старый
> `sealed_provider_record_sha256`, publisher требует `transport_record_sha256`.

**Задачи**

- [x] Сохранить существующий strict-xfail
  `test_v2_publication_failure_does_not_leave_paid_terminal_dispatch` как RED-контроль.
- [x] Разделить transport reconciliation и terminal ledger publication: `reconcile` проверяет и
  удерживает transport receipt/cost/outcome, но `ledger.commit_terminal` вызывается только после
  успешного create-once сохранения и keyed-проверки V2-envelope.
- [x] Сохранить при publication failure состояние `JournalState.DISPATCHED`, зарезервированный
  conservative cost и generic semantic record в process-local provider store; не освобождать
  call/spend cap и не разрешать второй transport dispatch.
- [x] Добавить producer-owned in-process recovery seam
  `resume_v2_publication(request: GateBDispatchRequestV2, *, input_hash: str, capability: object)`:
  он загружает уже записанный generic semantic record, проверяет seal/authority, повторно строит и
  валидирует V2-result, публикует V2-envelope и только затем финализирует ledger. Seam не принимает
  transport и не вызывает `chat.completions.create`.
- [x] На повторном входе в `run_collection` с тем же process-local ledger распознавать
  `JournalState.DISPATCHED` + сохранённый generic semantic record как recoverable publication
  state, заново вывести provider-input identity из manifest-bound request, проверить record новой
  capability того же manifest и вызвать `resume_v2_publication` вместо `dispatch`. Resume не
  вызывает `reserve`/`mark_dispatching`, затем продолжает обычные Decision v2,
  `RecordingStore.save_exclusive` и `recordings.verify`; новый процесс и новый ledger не объявлять
  resume-механизмом.
- [x] Добавить RED-тест
  `test_v2_publication_resume_uses_stored_transport_without_redispatch`: первый save падает,
  повторный `run_collection` с тем же provider stores и process-local ledger после восстановления
  V2 store
  завершает V2 publication/ledger/recording при неизменном transport call count `1`.
- [x] После GREEN удалить strict-xfail с исходного теста без ослабления его assertion на
  `JournalState.DISPATCHED` после publication failure.

**Граница восстановления и остаточный риск**

Этот пункт обещает resume только внутри того же процесса, пока живы process-local
`ForegroundDispatchLedger` и его привязки. Cross-process recovery в Order 1 не обещается:
если процесс умрёт после платного transport call и durable generic recording, но до V2 publication,
долговечный spend record останется списанным, а row-level ledger и привязка к transport receipt
исчезнут. Новый процесс в таком случае обязан остановиться fail-closed; план не имеет права
называть это cross-process resume или незаметно делать второй provider call.

- [x] До закрытия 2.4 владелец либо явно принимает эту process-local границу, либо остаточный
  риск выносится в отдельный tracked issue; ссылка на решение или issue записана в evidence реализации.
- Evidence: владелец принял process-local границу Order 1; cross-process recovery вынесен в
  [issue #11](https://github.com/DenisVanyushkin/hermes-agent/issues/11).
- [x] Условие полного снятия риска зафиксировано без воскрешения retired launch protocol: durable,
  create-once, manifest-ref-bound recovery journal с dispatch identity, provider-input hash, generic transport
  receipt SHA, spend reservation/outcome и тестом рестарта, который публикует V2 без network/provider/spend.

**DoD**

- [x] Оба теста publication failure/resume возвращают `2 passed`; после первого сбоя ledger
  остаётся `DISPATCHED`, после resume становится терминальным и содержит transport receipt SHA.
- [x] Fake transport фиксирует ровно один вызов до и после resume; попытка повторного dispatch для
  той же строки отклоняется до transport.
- [x] Resume завершается `recordings.verify` и Decision v2 без network/provider/spend; новый
  `ForegroundDispatchLedger` или cross-process restart не обещаются и не добавляются.

### 2.5 Дубликаты manifest rows сохраняют разные dispatch identities

- [x] Пункт 2.5 выполнен целиком

Закрывает: [issue #10](https://github.com/DenisVanyushkin/hermes-agent/issues/10)

> обратная привязка допускает один dispatch на provider-input, тогда как
> манифест дубликаты разрешает (`:2550-2562`)

**Принятое денежное решение**

Каждая manifest row получает собственный dispatch, reservation и provider call, даже если два
provider-visible payload побайтово равны. Значит, две такие строки занимают два call/spend slots.
Это не скрытая деталь DoD, а явная цена выбранного контракта. Основание: ledger, HMAC и
recording lineage привязаны к `ManifestRef`/ordinal, а одинаковый redacted payload не доказывает
одинаковую полную локальную проекцию. Существующий
`test_collection_runner_dispatches_duplicate_inputs_as_distinct_rows` уже фиксирует такую кардинальность.
Альтернатива — один платный transport result с fan-out на несколько rows — в Order 1 отклонена:
для неё нет согласованного контракта shared receipt, per-row keyed evidence и распределения стоимости.
Это может стать отдельной owner-approved оптимизацией, но не незаметной сменой доказательной модели.

**Задачи**

- [x] Заменить текущий passing-контроль
  `test_duplicate_provider_input_cannot_bind_to_two_dispatches` на RED-контроль, доказывающий
  требуемый контракт: два разных `ManifestRef` с одинаковым provider-visible payload получают
  разные dispatch/reservation identities без `transport_receipt_identity_conflict`.
- [x] Сделать transport-record identity dispatch-qualified: одинаковые provider-visible bytes не
  должны схлопывать ordinal, ledger entry или recording identity; provider-visible payload при этом
  остаётся byte-identical и не получает технический discriminator.
- [x] Обновить reverse binding так, чтобы один provider-input мог быть связан с несколькими
  dispatch keys, а каждый captured receipt однозначно разрешался по dispatch/reservation identity.
- [x] Сохранить правило существующего
  `test_collection_runner_dispatches_duplicate_inputs_as_distinct_rows`: две manifest rows
  потребляют две dispatch slots и дают два row results.

**DoD**

- [x] Новый тест `test_duplicate_provider_input_binds_to_distinct_manifest_dispatches` проходит и
  одновременно утверждает: provider payload bytes равны; dispatch keys различны; ledger refs имеют
  ordinals `0` и `1`; обе строки имеют отдельные проверяемые recordings.
- [x] `/usr/bin/timeout 90s .venv/bin/pytest -q tests/product_search/test_gate_b_collection_runner.py -k 'duplicate_inputs or duplicate_provider_input or reservation_identity'`
  проходит без xfail/skip.
- [x] Call/spend cap считает две manifest rows как две dispatch slots; никакого неявного reuse или
  второго результата из чужого receipt нет.

### 2.6 `reconcile` очищает обратную привязку только после безопасной финализации

- [x] Пункт 2.6 выполнен целиком

Закрывает: [issue #10](https://github.com/DenisVanyushkin/hermes-agent/issues/10)

> `provider_input_to_dispatch` не очищается в `reconcile`

**Задачи**

- [x] Добавить RED-тест `test_finalized_dispatch_releases_reverse_provider_input_binding`, который
  завершает первую строку, затем связывает тот же provider-input со второй manifest row и проверяет,
  что stale dispatch не перехватывает reserve/reconcile второй строки.
- [x] Хранить reverse binding до успешной V2 publication/finalization из 2.4, чтобы partial failure
  оставался resumable; после terminal commit атомарно удалить только binding завершённого dispatch.
- [x] Очищать связанные `reservations`, `receipts`, `transport_receipts` и pending-finalization
  entry согласованно; повторная идемпотентная финализация тех же bytes разрешена, конфликтующие
  bytes отклоняются.

**DoD**

- [x] `test_finalized_dispatch_releases_reverse_provider_input_binding` проходит: второй dispatch
  получает собственный `ManifestRef`, receipt и terminal ledger entry.
- [x] Publication failure из 2.4 не очищает binding преждевременно и остаётся resumable; успешная
  финализация удаляет stale binding и не ослабляет call/spend cap.
- [x] Focused capability/collection tests проходят два последовательных цикла bind → reconcile →
  publish/finalize для одинакового provider-input без утечки результата между ordinals.

### 2.7 `run_one_row` не принимает непроверенный caller-supplied provider record

- [x] Пункт 2.7 выполнен целиком

Закрывает: [issue #10](https://github.com/DenisVanyushkin/hermes-agent/issues/10)

> `run_one_row` принимает произвольный `provider_record` без keyed-проверки
> (`:2862-2880`).

**Задачи**

- [x] В `tests/product_search/test_gate_b_evidence_skeleton.py` добавить RED-тест
  `test_run_one_row_rejects_unverified_caller_provider_record`, который передаёт tampered mapping и
  показывает, что текущий compatibility seam принимает его в Decision request.
- [x] Удалить параметр `provider_record` из публичной сигнатуры `run_one_row`; загружать record по
  dispatch identity через `_provider_record(provider, input_hash)`, затем выполнять keyed verify и
  `_assert_provider_record_authority` до построения Decision request.
- [x] Обновить callers/fixtures так, чтобы fake provider предоставлял store + явный verifier; не
  добавлять test-only bypass в production signature.
- [x] Добавить отрицательные тесты для отсутствующего verifier, tampered HMAC и authority mismatch.

**DoD**

- [x] `/usr/bin/timeout 90s .venv/bin/pytest -q tests/product_search/test_gate_b_evidence_skeleton.py`
  проходит целиком, включая новый fail-closed тест.
- [x] `rg -n 'provider_record: Mapping\[str, object\].*= None' job_intel/product_search/gate_b_evidence_runner_v1.py`
  не находит caller-supplied provider-record параметр `run_one_row`.
- [x] Непроверенная, HMAC-tampered или authority-mismatched запись отклоняется до
  `decision_request_factory`; контрольная keyed-verified запись доходит до Decision v2.

### 2.8 Интеграционный acceptance gate раздела 2

- [x] Пункт 2.8 выполнен целиком

Закрывает: [issue #10](https://github.com/DenisVanyushkin/hermes-agent/issues/10)

> `test_gate_b_full_composition_e2e.py` зелёный целиком, включая снятые `xfail`
>
> фейковый прогон корпуса остаётся 48 / 45 / 3

**Задачи**

- [x] Зафиксировать RED baseline на исходном HEAD командой
  `/usr/bin/timeout 90s .venv/bin/pytest -q tests/product_search/test_gate_b_full_composition_e2e.py`:
  `1 failed, 1 passed, 2 xfailed`, текущая ошибка — два отсутствующих поля при
  `EvidenceSynthesisInputV2.model_validate(payload)`.
- [x] После выполнения 2.1–2.7 удалить оба strict-xfail marker; не менять production-shaped fake
  на Mock, не подменять `recordings`, `reconcile`, `RecordingStore` или `DecisionEvidenceStore`.
- [x] Запустить существующие четыре acceptance node отдельно и получить `4 passed`; новые тесты,
  добавленные этим планом, также остаются в полном файле и проходят.
- [x] Запустить focused regression suites collection/recording/skeleton и readiness experiment;
  сохранить fake-only режим, без live provider/network/spend.

**DoD**

- [x] `/usr/bin/timeout 90s .venv/bin/pytest -q tests/product_search/test_gate_b_full_composition_e2e.py`
  завершается с exit `0`; output не содержит `failed`, `xfailed`, `xpassed` или `skipped`.
- [x] `rg -n '@pytest\.mark\.xfail|pytest\.xfail' tests/product_search/test_gate_b_full_composition_e2e.py`
  не находит маркеров, скрывающих blocker 3 или blocker 4.
- [x] `/usr/bin/timeout 90s .venv/bin/pytest -q tests/product_search/test_gate_b_collection_runner.py tests/product_search/test_gate_b_recording_replay.py tests/product_search/test_gate_b_evidence_skeleton.py`
  завершается с exit `0`.
- [x] `/usr/bin/timeout 90s .venv/bin/pytest -q tests/product_search/test_gate_b_readiness_experiment.py`
  проходит assertions `transport_success: 48`, `assessed: 45`, `decision_fail_closed: 3` и summary
  `Transport completed: 48/48. Decisions assessed: 45/48. Not measurable: 3/48.`
- [x] `.venv/bin/ruff check job_intel/product_search/gate_b_evidence_runner_v1.py job_intel/product_search/evidence_synthesis.py job_intel/vacancy_understanding/semantic/runtime/llm_provider.py tests/product_search/test_gate_b_full_composition_e2e.py tests/product_search/test_gate_b_collection_runner.py tests/product_search/test_gate_b_recording_replay.py tests/product_search/test_gate_b_evidence_skeleton.py`
  завершается с exit `0`, затем `git diff --check` не печатает ошибок.

---

## 3. Smoke на собранном артефакте доказывает композицию

- [ ] **Раздел 3 выполнен целиком**

**Закрывает:** [issue #8](https://github.com/DenisVanyushkin/hermes-agent/issues/8)

> `scripts/gate_b_composition_smoke.py` не может завершить прогон: supervised-коллекция падает на
> `SpendRecordError: spend_record_missing`. Фикстура `tests/product_search/gate_b_cli_smoke_fixture.py`
> не создаёт spend record, который с 2026-08-22 требуется на старте прогона.

> Композиционный smoke — единственная проверка, которая гоняет CLI через реальный
> собранный артефакт целиком. Пока он падает на старте, он не подтверждает **ничего**

### 3.1 Фикстура готовит spend record

- [ ] **Пункт 3.1 выполнен**

**Закрывает** направление из Suggested scope [issue #8](https://github.com/DenisVanyushkin/hermes-agent/issues/8):

> - `tests/product_search/gate_b_cli_smoke_fixture.py` — `prepare()` создаёт spend record

**Задачи**

- [ ] Добавить в `prepare()` создание spend record через
      `SpendRecordStore.provision(...)` — публичную фабрику, а не запись файла
      руками, иначе фикстура разойдётся с форматом при следующем изменении.
- [ ] Согласовать `manifest_sha256`, под которым provision создаёт запись, с тем,
      под которым `_build_committed_budget_reserver()` её открывает — иначе
      `SpendRecordStore.open` снова даст `spend_record_missing`.
- [ ] Задать бюджет, покрывающий число вызовов smoke, и зафиксировать его в
      фикстуре явным числом, а не «побольше».

**DoD**

- [ ] `grep -n "spend_record\|SpendRecord" tests/product_search/gate_b_cli_smoke_fixture.py`
      даёт непустой результат — прямая инверсия улики из #8, где этот grep пуст.
- [ ] Прогон smoke больше не падает на `spend_record_missing`; точка отказа (если
      она есть) сдвинулась дальше, и это зафиксировано в выводе.

### 3.2 Таймауты по фактическому времени сборки и внятная диагностика

- [ ] **Пункт 3.2 выполнен**

**Закрывает** направление из Suggested scope [issue #8](https://github.com/DenisVanyushkin/hermes-agent/issues/8) и критерии приёмки:

> - `scripts/gate_b_composition_smoke.py` — таймауты, адекватные времени сборки артефакта

> - таймаут прогона выбран с запасом относительно фактического времени сборки артефакта
> - сломанная предпосылка smoke даёт внятную диагностику, а не выглядит как зависание

**Задачи**

- [ ] Измерить фактическое время сборки артефакта на VPS и записать число в план
      или в коммит; из #8 известно, что оно «заметно больше 90 s» при архиве по
      ~11 000 файлов, но точная цифра не зафиксирована.
- [ ] Поднять таймауты `subprocess.run` в `scripts/gate_b_composition_smoke.py`
      исходя из измеренного времени с явным запасом.
- [ ] Сделать так, чтобы отсутствие обязательной предпосылки печаталось как
      именованная ошибка предпосылки, а не как обрыв по таймауту.

**DoD**

- [ ] Измеренное время сборки записано числом, а не оценкой.
- [ ] Прогон с намеренно удалённой предпосылкой (например, удалённым spend
      record) завершается быстро и печатает, **какая именно** предпосылка не
      выполнена.
- [ ] Прогон в штатных условиях не упирается в таймаут.

### 3.3 Smoke реально ходит через production-адаптер

- [ ] **Пункт 3.3 выполнен**

**Закрывает** критерий приёмки [issue #8](https://github.com/DenisVanyushkin/hermes-agent/issues/8):

> - прогон доходит до `decision_request_factory`, то есть подтверждает, что smoke реально ходит через
>   `build_decision_request_from_context_v2`, а не только объявляет это в конфиге

**Задачи**

- [ ] Довести прогон до вызова `decision_request_factory` и доказать вызов
      наблюдаемым следом (лог, счётчик, артефакт), а не тем, что имя стоит в
      конфиге.
- [ ] Убедиться, что фабрикой оказывается `build_decision_request_from_context_v2`,
      а не тестовая фикстура.

**DoD**

- [ ] В выводе прогона присутствует наблюдаемое доказательство вызова
      production-фабрики.
- [ ] Подмена фабрики на заведомо ломающуюся ломает прогон — то есть проверка
      действительно зависит от неё, а не проходит мимо.

### 3.4 Smoke читает corpus authority, а не хранит SHA в коде

- [ ] **Пункт 3.4 выполнен**

**Закрывает:** [issue #8](https://github.com/DenisVanyushkin/hermes-agent/issues/8)

> Композиционный smoke — единственная проверка, которая гоняет CLI через реальный
> собранный артефакт целиком. Пока он падает на старте, он не подтверждает **ничего**

Второй источник — DoD Order 1 в
`docs/superpowers/plans/2026-08-10-job-intel-search-product-redesign.md`:

> **Definition of done includes binding the smoke to the single corpus authority
> defined in Order 2** — it must read and verify that authority, not a hardcoded
> SHA, which would go stale again after the Order 2 rebuild.

**Разделение ответственности с Order 2.** Order 1 закрывает **способность
потребителя**: захардкоженного SHA нет, smoke читает настраиваемую машиночитаемую
authority и падает закрыто при расхождении, тест использует временную тестовую
authority. Order 2 **назначает** производственный канонический SHA и повторяет
smoke на финальном значении; этот повтор закрывает Order 2, а не данный пункт.
Так пункт закрывается внутри Order 1 и не образует цикла, при котором Order 1 не
завершён, а значит Order 2 по согласованному порядку не начинается.

**Задачи**

- [ ] Убрать `CORPUS_SHA256` как захардкоженную константу из
      `tests/product_search/gate_b_cli_smoke_fixture.py:37`.
- [ ] Заставить фикстуру читать corpus authority из машиночитаемого источника,
      путь к которому настраивается, и **проверять** её, а не принимать на веру.
- [ ] Добавить тест на fail-closed: при расхождении прочитанной authority с тем,
      что потребляет боевой путь, smoke падает с именованной ошибкой.
- [ ] Тест использует временную тестовую authority, а не производственное значение.

**DoD**

- [ ] `grep -rn "b1db802dbb3d" tests/product_search/gate_b_cli_smoke_fixture.py`
      не даёт совпадений.
- [ ] Подмена authority-файла в тесте меняет то, что читает smoke, без правки его
      кода.
- [ ] Тест на fail-closed падает, если проверку расхождения убрать.

---

## Приёмка Order 1 целиком

- [ ] **Order 1 закрыт**
- [ ] Раздел 1 отмечен выполненным.
- [ ] Раздел 2 отмечен выполненным, включая **DoD пункта 2.8** — он является
      нормативным полным критерием сквозного E2E; здесь он не переформулируется,
      чтобы две версии не разъехались.
- [ ] Раздел 3 отмечен выполненным.
- [ ] Композиционный smoke на собранном артефакте доходит до конца и печатает
      результат.
- [ ] Ни одна отметка `[x]` не поставлена без указания теста, команды или файла,
      который её доказывает.
