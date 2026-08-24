# Починка отбора тестов в гейте upstream-sync (`fix/upstream-sync-test-selection`)

Версия 3. Три круга ревью с Codex; все находки подтверждены по коду.
`D*` — находки, `T*` — задачи. Все задачи TDD.

**DoD** у каждой задачи — **одна** названная команда, тест или именованный
acceptance-скрипт. Отложенные проверки помечены явно и гейтом приземления
не являются.

## Правило остановки

v3 — последняя редакция прозы. После неё — bounded acceptance, что перечисленные
здесь контракты присутствуют, и сразу T1. Всё, найденное дальше, идёт в тесты
соответствующей задачи, а не в переписывание плана.

**Исключение (согласовано).** Если поздняя находка меняет границу авторизации,
перечень допустимых записей в production state или разрушающую последовательность
выката — это поправка плана, а не тест задачи.

## Замеры (исходное состояние и стоимости)

| замер | «новых» падений |
|---|---|
| `upstream_sync_gate.py new-failures` как есть | **97** |
| симметричный набор, те же условия | **10** |
| в тесной изоляции | **2** |

Артефакты: `/home/hermes/claude-gate-evidence-20260824/`. Дерево до мержа в
изоляции — 258 passed / 0 failed, после — 5 failed. `collect-only` на 530 файлах
— 80 с; полный прогон — 13–15 мин.

**Это замеры ДО правок, константами приёмки они не являются.** Классификация по
узлам законно перераспределит их. Ни один DoD не требует воспроизвести 97, 10
или 2. Вперёд переносятся только цифры стоимости.

## Находки

- **D1.** `MERGE_CHANGED` — `git diff --name-only` без `--diff-filter`, в наборе
  оказываются **удалённые** мержем файлы; pytest на несуществующем пути даёт
  `no tests ran`, компаратор не находит итоговой строки, rc 2.
- **D2.** Наборы baseline и post не совпадают: `merge_changed=0` против 55.
- **D3.** Граница из ref `upstream/main`, отставшего на 752 коммита; ~105
  апстримовых файлов гонялись как тесты форка.
- **D4.** `_FAILED_LINE` = `^FAILED\s+(\S+)` — собираются только `FAILED`, а
  ошибки сбора идут как `ERROR`; в `_SUMMARY_LINE` шаблон `error\b` не матчит
  «errors». Нужны **и** флаг в раннере, **и** новый parser.
- **D5.** Slack печатает `baseline tail` первым; шапка читается как «мерж сломал
  N тестов».
- **D6.** Корзины по путям, вердикт по узлам. На `f941342b`:
  `test_exec_prefixes_interpreter_for_env_shebang_python_script` — 0 вхождений в
  `f941342b^1`, 1 в `f941342b`, 1 в `f941342b^2`. Апстримовый тест. Прежний
  вывод «две регрессии» неверен: регрессия одна, второе — admission failure.
- **D7.** `HERMES_SCRIPTS_DIR` не разрывает bootstrap:
  `sync-local-customizations.sh:41-47` берёт `REPO` как `$SCRIPT_DIR/..` и
  `HERMES_REPO` не учитывает; `upstream-sync-smoketest.sh:9-15` — та же
  эвристика, `HERMES_REPO` только в `else`. Smoketest записал бы worktree HEAD в
  `last-synced.json`.
- **D8.** Resume guard (576) — одиночный `test` с `-o`: resume разрешён при
  наличии `MERGE_HEAD` даже при чужом HEAD; `upstream_head` не участвует.
- **D9.** `merge_passes_fork_tests` (318) вызывается один раз (493), при успехе
  исполнение проваливается прямо к fast-forward. Непортящего прогона нет.
- **D10.** `sync-local-customizations.sh` запускает `TEST_CMD` на 609, а
  `git merge` — на 619. В момент baseline `after` не существует, поэтому один
  манифест с `before` и `after` невозможен без пересеквенсирования.
- **D11.** В юните `WorkingDirectory=` пуст, то есть cwd — `/`. А
  `sync-local-customizations.sh` **первым** проверяет `${PWD:-.}/.git`. Запуск
  из другого cwd уходит в другую ветку выбора `REPO`. Поэтому реконструкция
  юнита (transient) не заменяет установленный юнит: drop-in, drift `ExecStart`,
  `WorkingDirectory`, `Type`, окружение менеджера и `PathExists`-handoff она не
  проверяет. Проверено: drop-in нет, `NeedDaemonReload=no` — но это состояние
  на сегодня, а не гарантия контракта.

---

## Контракт изоляции попытки (обязателен до первой строки кода)

Без него T18–T20 и T24 могут получить зелёные локальные тесты на **разных**
представлениях жизненного цикла, а несовместимость проявится только на живом
состоянии.

**Два пространства состояния.**

- *Production state* — `$STATE_DIR`: `pending.json`, `apply-prepare.json`,
  `scratch/`, `finalize-result.json`, `last-synced.json`, накопленные
  `gate-*` улики.
- *Attempt namespace* — `$STATE_DIR/attempts/<attempt_id>/`, где `attempt_id`
  выводится из тройки `before`, `after`, `boundary` и потому воспроизводим.
  Внутри: `gate-selection.txt`, `gate-baseline.log`, `gate-post.log`,
  `gate-upstream-probe.log`, `gate-failures.json`, `attempt.json`.

**Права.** `gate-only` вправе создавать и ротировать **только** содержимое
своего attempt namespace. Ему **запрещено** читать, архивировать и
перезаписывать `pending.json`, `apply-prepare.json`, `scratch/` и улики прежних
попыток. `apply` пишет в свой attempt namespace и **дополнительно** публикует
итог в production state.

**Привязка.** `attempt.json` несёт `schema_version`, `before`, `after`,
`boundary`, `attempt_id`. Потребитель обязан отказать, если тройка не совпадает
с тем, что он видит.

**Два независимых поля исхода.**

- `execution_status` ∈ `ok | failed | awaiting_decision` — отработал ли механизм.
  Это существующий контракт `finalize-result.json`, он не расширяется.
- `gate_verdict` ∈ `pass | block | unknown` — что решил гейт.

Успешно отработавшая репетиция, которая честно заблокировала мерж, это
`execution_status=ok` **и** `gate_verdict=block`. Одно поле `status: ok` в такой
ситуации читается как разрешение и потому запрещено.

---

## Слой 1. Раннер и манифест

### T1. Падающий тест: удалённый мержем файл не попадает в набор (D1)

**Падающий тест.** Новый `tests/scripts/test_run_fork_tests_selection.py`;
фикстура строит репозиторий в `tmp_path`: апстрим с двумя тестами, форк со своим
третьим, мерж, удаляющий один апстримовый.

**DoD.** `pytest tests/scripts/test_run_fork_tests_selection.py::test_deleted_path_not_selected`
падает, и в тексте падения назван удалённый путь.

**Зависимости.** нет. **Сложность.** S.

### T2. `--print-selection`: пути в stdout, диагностика в stderr (D1)

**Что сделать.** `--print-selection` печатает **только** пути в stdout;
`fork test selection:` и число отброшенных — в stderr. `--diff-filter=d`, поверх
— фильтр существования.

**DoD.** `pytest tests/scripts/test_run_fork_tests_selection.py -k print_selection`
зелёный; среди случаев — редирект stdout в файл, где **каждая** строка
существующий путь.

**Внимание.** `test_sync_runtime_scripts.py` запрещает `source` из подкаталогов.
Логику держать в одном файле.

**Зависимости.** T1. **Сложность.** S.

### T3. Падающий тест: boundary только явная (D3)

**DoD.** `pytest tests/scripts/test_run_fork_tests_selection.py::test_boundary_is_explicit`
падает, и падение показывает **два разных** размера набора.

**Зависимости.** T2. **Сложность.** S.

### T4. Явная boundary; раннер не знает про мержи (D3)

**Зачем.** Отказ на не-merge внутри раннера сломал бы `sync-local`, который
подаёт `--detach HEAD`. Раннер — исполнитель, не арбитр.

**DoD.** `pytest tests/scripts/test_run_fork_tests_selection.py::test_no_implicit_boundary`
зелёный: **поведенческий** тест — в репозитории, где `upstream/main` существует
и дал бы непустой набор, запуск без `--boundary` завершается кодом 2 и набора не
печатает. (Grep по исходнику критерием не является.)

**Зависимости.** T3. **Сложность.** M.

### T5. Падающий тест: union-manifest (D2, D6)

**Падающий тест.** Универсум = union fork-only из **обоих** деревьев
относительно одной boundary плюс все тестовые пути из `diff before..after`,
включая удаления; классификация по `exists_pre`/`exists_post`.

**DoD.** `pytest tests/scripts/test_run_fork_tests_selection.py::test_manifest_universe_is_union`
падает, и в падении видно отсутствие удалённого пути в универсуме.

**Зависимости.** T4. **Сложность.** M.

### T6. Union-manifest: атомарная запись, привязка, `pre_only_paths` (D2, D6)

**Что сделать.** Манифест считается один раз, пишется атомарно (временный файл в
том же каталоге + `rename`), несёт поля из контракта изоляции. В отчёт
возвращается **`pre_only_paths`** — informational список и счётчик путей,
удалённых мержем; это **не** корзина падений. Артефакты пишутся в attempt
namespace.

**DoD.** `pytest tests/scripts/test_upstream_sync_finalize.py -k manifest`
зелёный; среди случаев — обрыв записи, после которого частичного
`gate-selection.txt` в каталоге нет, и случай, где удалённый путь попадает
именно в `pre_only_paths`.

**Зависимости.** T5. **Сложность.** M.

### T7. Инвариант потребителя манифеста (D2)

**Что сделать.** Отказ при неизвестной `schema_version`; отказ, если HEAD
чекаута не равен ни `before`, ни `after`. Если манифест объявляет
`exists_pre=true`, а путь в чекауте `before` отсутствует — это **порча,
rc 2**, а не тихий пропуск. Пропуск допустим только при объявленном `exists=false`.

**DoD.** `pytest tests/scripts/test_run_fork_tests_selection.py -k manifest_consumer`
зелёный; среди случаев все три: unknown schema, foreign HEAD, declared-exists
mismatch.

**Зависимости.** T6. **Сложность.** M.

### T8. `--continue-on-collection-errors` в раннер (D4)

**Зачем.** Восстановленная задача: в v2 флаг упоминался только как
«недостаточно», и ни одна задача его не добавляла — то есть исходное обрушение
сбора осталось бы неисправленным. Новому парсеру (T12) без флага нечего парсить.

**DoD.** `pytest tests/scripts/test_run_fork_tests_selection.py::test_collection_error_does_not_abort_run`
зелёный: набор с модулем, кидающим на импорте, даёт итоговую строку, а не
`Interrupted`.

**Зависимости.** T2. **Сложность.** S.

### T9. Квитанция раннера как argv-контракт (D7)

**Зачем.** Старый стаб примет лишние argv, использует только `$1` и молча
проигнорирует `--selection-from`. Лишний аргумент это не ловит; квитанция ловит.

**Что сделать.** Раннер печатает в stderr строку с версией контракта и
дайджестом манифеста, который **реально** потребил.

**DoD.** `pytest tests/scripts/test_upstream_sync_finalize.py -k runner_receipt`
зелёный; среди случаев — стаб старой сигнатуры, на котором caller падает громко
с сообщением об отсутствующей квитанции.

**Зависимости.** T6. **Сложность.** M.

---

## Слой 2. Вердикт

### T10. Падающий тест: классификация по узлам (D6)

| baseline | upstream parent | merged | класс |
|---|---|---|---|
| узла нет | есть, проходит | падает | fork compatibility failure |
| узла нет | есть, падает | падает | upstream-red admission failure |
| узла нет | нет | падает | merge-resolution / local-introduced |
| collect или probe нечитаем | — | — | `unknown`, **никогда** молча в `common` |

Корзины — `common_path` / `post_only_path`; `common` формулируется как «новое
падение в пути, присутствующем в обоих деревьях», без утверждения о узле.

**DoD.** `pytest tests/scripts/test_upstream_sync_gate.py -k classification`
падает, и в наборе случаев присутствуют все четыре строки.

**Зависимости.** T7. **Сложность.** M.

### T11. Классификация с узким probe; владение третьим checkout (D6)

**Что сделать.** Присутствие узла — `collect-only` только на затронутых путях.
Причина падения — прогон **только точных newly-seen failing nodeids** на upstream
parent, включая новые узлы внутри `common_path`. Третий checkout создаёт и
держит **финализатор**, он же пишет `gate-upstream-probe.log` в attempt namespace
и передаёт структурированные свидетельства; компаратор остаётся чистой функцией.

**DoD.** `pytest tests/scripts/test_upstream_sync_gate.py -k node_probe_scope`
зелёный: на входе, воспроизводящем `f941342b`,
`test_exec_prefixes_interpreter_for_env_shebang_python_script` попадает в
`post_only_node`, и spy фиксирует, что probe запросил **ровно** множество
newly-seen failing nodeids (попытка прогнать путь целиком роняет тест).

**Зависимости.** T10. **Сложность.** M.

### T12. Общий parser outcomes: FAILED и ERROR (D4)

**DoD.** `pytest tests/scripts/test_upstream_sync_gate.py -k outcomes` зелёный;
среди случаев — прогон, где между baseline и post **появилась** ошибка сбора, и
компаратор возвращает её находкой, а не rc 2.

**Зависимости.** T8. **Сложность.** M.

### T13. Миграция схемы `gate-failures.json` (D2)

**Зачем.** `upstream_sync_triage.py:411` читает только `new_failures` и выходит
при пустом списке.

**Что сделать.** `schema_version`, корзины отдельными ключами и агрегированный
`blocking_failures` с инвариантом: он равен **устойчивому уникальному
объединению** блокирующих корзин и не содержит informational и `unknown`.

**DoD.** `pytest tests/scripts/test_upstream_sync_triage.py -k schema` зелёный;
среди случаев — проверка самого инварианта, а не только совпадения счётчика.

**Зависимости.** T10. **Сложность.** M.

### T14. Slack: подписанные хвосты, класс сбоя, golden-render (D5)

**DoD.** `pytest tests/scripts/test_upstream_sync_slack.py -k gate_report`
зелёный; golden-случаи покрывают: подпись «до мержа, не блокирует» раньше
первого числа падений; `unknown` из-за нечитаемого baseline; из-за нечитаемого
post; из-за нечитаемого collect; из-за нечитаемого probe — и ни один из
четырёх не приписывает падения мержу.

**Зависимости.** T13. **Сложность.** M.

---

## Слой 3. Вызывающие

### T15. Пересеквенсировать `sync-local-customizations.sh` (D10)

**Что сделать.** Сначала строим мерж и фиксируем `after`, затем манифест, затем
`checkout before` + прогон, `checkout after` + прогон — форма, которую
финализатор уже использует.

**DoD.** `pytest tests/scripts/test_sync_local_customizations.py -k call_trace`
зелёный: трассировка доказывает, что оба прогона получили **идентичные**
манифест и argv.

**Зависимости.** T9. **Сложность.** M.

### T16. Caller требует квитанцию (D7)

**DoD.** `pytest tests/scripts/test_sync_local_customizations.py tests/scripts/test_upstream_sync_finalize.py -k receipt_required`
зелёный для обоих вызывающих.

**Зависимости.** T15. **Сложность.** S.

### T17. Строгий приоритет `HERMES_REPO` в дочерних скриптах (D7, D11)

**Зачем.** Самостоятельный выбор `REPO` по `PWD`, затем по `SCRIPT_DIR` — мина
под любым запуском с нестандартным cwd или `SCRIPTS_DIR`.

**DoD.** `pytest tests/scripts/test_sync_local_customizations.py -k repo_precedence`
зелёный для `sync-local-customizations.sh` и `upstream-sync-smoketest.sh`,
включая случай с cwd `/`.

**Зависимости.** T16. **Сложность.** S.

---

## Слой 4. Общий шов гейта и состояние

### T18. Один `run_gate` для `apply` и `gate-only` (D9)

**Зачем.** «Та же композиция» словами не проверяется: отдельная пустая ветка
`gate-only` прошла бы проверку на неизменность HEAD.

**Что сделать.** `run_gate(before, after, boundary, attempt_id)`, который зовут
оба пути.

**DoD.** `pytest tests/scripts/test_upstream_sync_finalize.py -k shared_gate_seam`
зелёный: параметризованный spy доказывает совпадение аргументов, набора
артефактов и исходов `pass`/`block`/`unknown` для обоих вызывающих.

**Зависимости.** T11. **Сложность.** M.

### T19. `gate-only` с изоляцией попытки (D9, контракт)

**DoD.** `pytest tests/scripts/test_upstream_sync_finalize.py -k gate_only_isolation`
зелёный: тест фиксирует `HEAD`, `pending.json`, `apply-prepare.json`, `scratch/`
и прежние `gate-*` улики до и после и требует побайтового совпадения, а
созданное — только внутри attempt namespace.

**Зависимости.** T18. **Сложность.** M.

### T20. Единый инвариант `prepare`/`scratch` (D8)

**Что сделать.** Инвариант: `prep.local_base == pending.local_head == live HEAD`
и `prep.upstream_head == pending.upstream_head`, а `scratch`/`MERGE_HEAD`
согласованы с этой парой. Один helper атомарно архивирует старую попытку и
вызывает свежий `prepare`; T24 зовёт **его**, а не свои шаги.

**DoD.** `pytest tests/scripts/test_upstream_sync_finalize.py -k attempt_invariant`
зелёный; среди случаев оба: чужой `local_head` и тот же `local` с другим
`upstream`; интеграционный случай стартует со stale resumable scratch и
заканчивается согласованной парой.

**Зависимости.** T19. **Сложность.** M.

---

## Слой 5. Выкат — порядок инвертирован

Ветка правок приземляется нормальным dev-флоу, а не upstream-финализатором.
Сначала правки и публикация, потом синк: цикл исчезает, новый local HEAD
естественно становится первым родителем будущего мержа, и проверка родителей
(486–490) сходится сама.

### T21. Репетиция из worktree

**Что сделать.** `gate-only` с `HERMES_SCRIPTS_DIR` на `scripts` ветки. Здесь же
допустим transient unit как дополнительная репетиция. Path-unit остановлен под
контролем оператора.

**DoD.** `scripts/acceptance/gate-rehearsal-worktree.sh` завершается кодом 0;
скрипт сам сверяет `HEAD`, `pending.json`, `scratch/` до и после и печатает
`gate_verdict`.

**Зависимости.** T1–T20. **Сложность.** M.

### T22. Приземлить ветку правок и опубликовать рантайм

**Зачем.** `sync-runtime-scripts.sh` публикует `install -m 755` по одному файлу
(28, плюс `lib` на 37 и данные на 45/57) — атомарной подмены каталога нет.
Совместимый набор — **семь** файлов: finalizer, runner, gate, sync-local,
smoketest (его меняет T17), triage, slack.

**DoD.** `scripts/acceptance/publish-runtime.sh` завершается кодом 0: сверяет
все семь `diff`-ов, отсутствие `finalize-request.json` и что path-unit возвращён
в `active` только после сверки.

**Зависимости.** T21. **Сложность.** M.

### T23. Приёмка через **установленный** service unit

**Зачем.** Transient unit проверяет реконструкцию, а не установленный юнит: он
не ловит drop-in, drift `ExecStart`, `WorkingDirectory` (в юните пуст → cwd `/`,
что меняет ветку выбора `REPO` в дочерних скриптах), `Type`, окружение
менеджера и `PathExists`-handoff. Это тот же класс ошибки, что D9.

**Что сделать.** При остановленном path-unit положить **gate-only** request в
боевой handoff, `systemctl start upstream-sync-finalize.service`, дождаться
терминального состояния, проверить journal и результат попытки. Затем отдельным
безопасным gate-only request проверить сам `.path`.

**DoD.** `scripts/acceptance/published-gate-smoke.sh` завершается кодом 0:
`execution_status=ok`, `gate_verdict` присутствует и осмыслен, `HEAD` не менялся.
Проверка `.path` — отложенная, вынесена в T27.

**Зависимости.** T22. **Сложность.** M.

### T24. Свежие `pending`/`prepare`

**Что сделать.** Вызвать helper из T20. Старый scratch от `13f4cfeb` ротируется
им же.

**DoD.** `scripts/acceptance/fresh-attempt.sh` завершается кодом 0: инвариант
T20 выполняется, `upstream_head` равен `git rev-parse upstream/main` после
свежего fetch.

**Зависимости.** T23. **Сложность.** M.

### T25. Две находки диагностики

1. **Регрессия.** `tests/cron/test_scheduler.py::TestCronDeliveryTargets::test_no_targets_when_gateway_config_fails`
   — апстримовая цель `bot-chat:default` протекает мимо F1. Приоритет высокий:
   это про приватность доставки.
2. **Admission failure, не регрессия.** `test_exec_prefixes_interpreter_for_env_shebang_python_script`
   — новый апстримовый тест (D6).

**DoD.** `pytest tests/cron/test_scheduler.py tests/hermes_cli/test_linux_desktop_entry.py`
зелёный на дереве мержа; для (1) среди случаев — **синтетическая** новая цель
доставки с произвольным `id`, доказывающая, что защита общая.

**Зависимости.** T24. **Сложность.** M.

### T26. Приземлить синк

**Зачем.** До T25 честный `gate-only` обязан давать `gate_verdict=block` — это
корректная работа механизма, а не сбой. Зелёный `gate-only` ожидается **только**
после T25.

**DoD.** `scripts/acceptance/land-sync.sh` завершается кодом 0:
`execution_status=ok`, `gate_verdict=pass`, `last-synced.json` несёт актуальный
`upstream_sha` и `result: clean`.

**Зависимости.** T25. **Сложность.** M.

### T27. Отложенные проверки (не гейт приземления)

**DoD.** Следующий **плановый** синк отработал без `NOT applied` в треде Slack,
и path-триггер поднял service сам. Проверяется по наступлении расписания.

**Зависимости.** T26. **Сложность.** S.

---

## Вне этого плана

- **Порядко-зависимость сьюта форка.** 164 падения в полном прогоне против 0 в
  изоляции. Пока это так, отчёты гейта будут шумными. Отдельный план.
- **`RotatingFileHandler` в тестах.** 220 `FileNotFoundError` после мержа (91 до).
- **Инвариант-чекер.** `docs/plans/2026-08-22-upstream-sync-gate-review-fixes.md`,
  15 пунктов; с отбором тестов не пересекается.
