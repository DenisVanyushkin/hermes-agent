# Пакет улик: раздел D, исход страницы LinkedIn

**Дата:** 2026-09-03. **Составил:** Claude. **Ревьюер:** Codex, read-only, по
приёмочным снапшотам.
**Формат:** §4 документа `agentic-delivery-rules.md`, SHA-256
`728e90e60e1ddeac9bff3b0fe506fd103e7a188ac2613eeed7af3a449ae7727e`.

> **Этот пакет описывает срез ДО живого прогона и до проброса HTTP-статуса.**
> Владелец расширил санкцию позже: «давай прогон и HTTP-код подтянем». Что
> изменилось после — в
> [`2026-09-03-section-d4-live-run-evidence.md`](2026-09-03-section-d4-live-run-evidence.md),
> и статус на передаче теперь читается **там**, а не здесь. Поля ниже
> сохранены как состояние на своей дате, а не переписаны задним числом:
> переписанный пакет перестал бы быть уликой того, что было известно тогда.

Этот файл — не отчёт о работе, а пакет, по которому ревьюер воспроизводит
критические выводы сам. Где сказано «не проверено», это факт, а не скромность.

---

```text
objective:
  Ячейка, чей LinkedIn-запрос отдал публичную страницу результатов, получает
  правдивый ярлык исхода: исполнение названо исполнением, покрытие названо
  неустановленным, и Gate при этом не открывается.

authorized_scope:
  Прямой receipt владельца 2026-09-03, дословно: «разрешаю план + код + тесты,
  без живых прогонов».

explicit_deferrals:
  D4, узкий живой прогон — санкции нет; он же единственный способ доказать
    стык страница → trace.
  Первый полный vertical slice — недостижим без D4 и отдельного receipt'а.
  Проброс HTTP-статуса в BrowserFetchResult — отдельная правка пути фетча.
  Повторная живая проба раздела B; перезапуск producer'ов раздела A;
    размаскировка таймеров job-intel; доставка; публикация и влитие ветки;
    обновление живого чекаута.
  Открытие Gate — по C′ допуск fail-closed до отдельного решения владельца
    о достаточности охвата.

base_revision:
  b7da67a7368d5787bec9be66c3c2d298d31ae40d  (коммит handoff, до этой работы)

candidate_revision:
  b87ca02eebe6fbea27e574c940e892f0042fab9c
  ветка job-intel/linkedin-page-outcome, upstream отсутствует, не публикована

changed_paths:
  docs/plans/2026-09-03-linkedin-page-outcome-implementation-plan.md
  job_intel/browser_sourcing.py
  job_intel/product_search/acquisition_probe.py
  tests/job_intel/test_browser_acquisition.py
  tests/product_search/fixtures/linkedin-public-search-results.html
  tests/product_search/test_coverage_hold.py
  tests/product_search/test_linkedin_page_classification.py
  итого 7 файлов, 2133 вставки, 31 удаление против a2a343f1ea

red_evidence:
  Обвязка всех прогонов, проверена в собранном виде под bash -lc:
    PY="/usr/bin/timeout 1800 env PYTHONPATH=/home/hermes/worktrees/linkedin-page-outcome \
        /home/hermes/.hermes/hermes-agent/venv/bin/python"
    cd /home/hermes/worktrees/linkedin-page-outcome

  1. Удержание Gate, на композиции, которая БЕЗ C′ Gate проходит.
     $PY -m pytest tests/product_search/test_coverage_hold.py -q -p no:cacheprovider
     exit 1, «1 failed, 2 passed in 2.33s»
     причина: assert acquisition_outcomes["b1-cell"] != "candidate_records_found"
              AssertionError: 'candidate_records_found' != 'candidate_records_found'
     категория: expected_red

  2. Тот же ярлык на уровне артефакта, читая записанный summary.json.
     exit 1, «2 failed, 2 passed in 2.04s»
     категория: expected_red

  3. Публичная страница как usable-поверхность и как не-стена.
     $PY -m pytest tests/product_search/test_linkedin_page_classification.py -q -p no:cacheprovider
     exit 1, «3 failed, 2 passed in 1.86s»
     причины: _page_has_source_results False на публичной вёрстке;
              login_walls == 1 на странице с тремя карточками
     категория: expected_red

  4. Обрыв объявленного плана authwall'ом на нулевом offset, через настоящий
     page loop.
     exit 1, «2 failed, 8 passed in 3.52s»
     причина: assert fetched == [0, 25] дало [0]
     категория: expected_red
     Этот RED вскрыл второй дефект, которого не было в задании:
     _observe_page получал запрошенный URL, поэтому /checkpoint план тоже
     не обрывал — ось безопасности была слепа к редиректам.

  5. Неизвестное значение в persisted-документе.
     до починки: PageProgressionObservation приняла
                 page_classification="not_a_declared_class" и
                 safety_reason="not_a_declared_reason"
     категория: product_failure (принято ревьюером как блокер)

green_evidence:
  $PY -m pytest tests/product_search/test_coverage_hold.py \
                tests/product_search/test_linkedin_page_classification.py \
                tests/job_intel/test_browser_acquisition.py \
                tests/job_intel/test_linkedin_auth_states.py \
                tests/product_search/test_scope_guard.py \
                tests/product_search/test_acquisition_probe.py \
                tests/product_search/test_gate_a_cell_outcomes.py \
                tests/product_search/test_gate_a_geography.py \
                -q -p no:cacheprovider
  exit 0, «256 passed in 44.60s», итоговая строка присутствует

  Baseline тех же предсуществующих файлов, снятый ДО сравнения возвратом
  исходных модулей на место: 194 passed, exit 0 по browser_sourcing;
  115 passed, exit 0 по acquisition_probe. Предсуществующих красных ноль,
  новых красных ноль.

  Записанный ярлык на удержанной ячейке:
    acquisition_outcomes["b1-cell"] = coverage_unestablished_credit_withheld
    annotation["acquisition_outcome"] = то же значение (проверено равенство)
    coverage_decision = {held: true, coverage_status: "unestablished",
                         reason: "pagination_progression_not_observed"}
    coverage_scope = {query_plan_presence: [["q-linkedin", true]], …}
    coverage_audit.pagination_outcome = pagination_not_observed
  На той же композиции без execution_plan: candidate_records_found, и
  credited_records, provenance и received_records равны точно.

composition_evidence:
  Стык trace → аудит → вето → артефакт: run_probe целиком, настоящая
  агрегация, настоящая сериализация, проверка по записанному summary.json.
  Подменён только внешний источник.
  Стык page loop → обе оси → trace: настоящий BrowserSourceClient и
  настоящий обход плана из двух offsets, подменён только fetch_page.
  Проверено, что authwall на нулевом offset НЕ отменяет план, а /checkpoint
  отменяет, и что обе оси попадают в trace по каждой странице.

  Стык страница → trace НЕ доказан и доказан быть не может без браузера.
  Поэтому composition_verified не заявляется.

runtime_evidence:
  runtime not verified

full_gate_evidence:
  не выполнялся. Полный pytest на этом хосте не запускается: наблюдённая
  операционная опасность, гейтвей убивается вотчдогом по нагрузке. Прогон
  без итоговой строки считается unreadable_or_killed, а не чистым.

known_limitations:
  1. Стык страница → trace не доказан; доказывается только D4.
  2. HTTP-статус в BrowserFetchResult отсутствует, поэтому
     http_429_rate_limit, http_401_antibot_or_auth, http_403_antibot_or_auth
     и класс http_error_surface из page loop недостижимы. Логика реализована
     и покрыта тестами с явным status; тест ассертит отсутствие поля и упадёт,
     когда оно появится.
  3. Верность coverage_scope.query_plan_presence исходным
     ProbeQuery.execution_plan из записанного документа непроверяема — планов
     в документе нет. Внутренняя согласованность scope проверяется пересчётом;
     фидельность держится на том, что единственный строитель не имеет входа
     для подмены.
  4. Классификатор публичной страницы переспрашивает парсер, то есть парсит
     HTML второй раз на страницу. На объявленном плане из двух offsets это
     не измерялось как проблема и не измерялось вообще.
  5. Сторож отсутствия читателя документа —
     test_no_production_module_reads_the_probe_summary_by_a_known_form —
     покрывает шесть строковых форм и НЕ является исчерпывающим. Вне его
     досягаемости: чтение через ORM, SQL, собранный из фрагментов, чтение
     файла через переменную и любой читатель вне пакета job_intel. Имя и
     докстринг теста приведены в соответствие с этим покрытием после
     замечания ревьюера: прежняя редакция заявляла доказательство отсутствия,
     проверяя две строки. Доказательством отсутствия на момент написания
     служит отдельное чтение всех вызовов run_probe и всех употреблений
     probe_runs, записанное в плане под вопросом 0.

working_tree_state:
  Три ревизии называются раздельно, чтобы это не выводилось из графа коммитов.
  candidate_revision — b87ca02eebe6fbea27e574c940e892f0042fab9c, код и тесты.
  packet_revision    — коммит, содержащий этот файл. Его SHA внутри файла
    привести НЕЛЬЗЯ: хэш коммита считается по содержимому, поэтому файл,
    содержащий собственный SHA, построить невозможно. Вместо прозы —
    команда, дающая вердикт:
      git log -1 --format=%H -- docs/evidence/linkedin-page-outcome/2026-09-03-section-d-evidence-packet.md
    Предыдущая ревизия пакета, для сверки цепочки:
      40d8ab9becbad52494f42c87159313b1a96ae19f
  Дерево чисто на packet_revision: git status --porcelain пуст.
  Замечание ревьюера, принято в исполнимой части.

pending_authority_or_decisions:
  1. D4, узкий живой прогон на 2–3 заранее названных ячейках — нужен прямой
     receipt владельца. До него первый полный vertical slice недостижим.
  2. Проброс HTTP-статуса в путь фетча — решение владельца, входит ли в этот
     срез или в следующий.
  3. Sufficiency-политика охвата — по C′ Gate остаётся fail-closed до
     отдельного решения владельца; ни одна правка этого не меняет.
```

---

## Статус на передаче

**`verified_in_isolation`.**

Обоснование, а не самооценка: каждое поведение имеет focused-тест и прогнано;
композиция проверена на двух стыках из трёх; третий стык — `страница → trace` —
требует живого прогона, на который санкции нет. `composition_verified` означал
бы, что пользовательский путь пройден целиком, а он не пройден.
`runtime_verified` и `deployed_not_verified` не заявляются: живого прогона не
было, ветка не публикована, живой чекаут не трогался, таймеры остаются
замаскированными.

## Что ревьюеру стоит перепроверить в первую очередь

- Что композиционные тесты действительно идут через `run_probe` и
  `BrowserSourceClient`, а не через подмену проверяемого стыка.
- Что положительный контроль удержания зелен: если он перестанет давать
  `candidate_records_found`, все тесты удержания перестают что-либо доказывать.
- Что фикстура несёт SHA-256 замороженного capture и не несёт ни одного
  реального идентификатора.
- Что `LINKEDIN_PAGE_CLASSIFICATIONS` и `LINKEDIN_SAFETY_REASONS` выведены
  через `get_args`, а не написаны вторым определением.
