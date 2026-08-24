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
- *Attempt namespace* — `$STATE_DIR/attempts/<candidate_id>/<generation>/`.
  Внутри: `gate-selection.json`, `gate-baseline.log`, `gate-post.log`,
  `gate-upstream-probe.log`, `gate-failures.json`, `attempt-result.json`,
  `attempt.json`.

**Две идентичности, а не одна.** `candidate_id` = хеш тройки `before`, `after`,
`boundary` — он опознаёт *кандидата* и воспроизводим. `run_id` =
`candidate_id` + монотонная `generation` — он опознаёт *запуск*. Generations
внутри кандидата **append-only**: повторный `gate-only` на той же тройке
заводит новую generation и не имеет права переписать предыдущую. Идентичность,
выведенная только из тройки, этого не даёт — второй запуск легально «ротировал
бы своё», уничтожив улики первого.

**Права: control-plane отдельно от data-plane.** Запуск через реальный юнит
физически не может обойтись без служебных файлов, поэтому запрет «только свой
namespace» в лоб недостижим.

*Control-plane, разрешено:* принять и переместить **свой собственный**
`finalize-request.json` → `finalize-request.processing.json`, удалить его по
завершении, держать `finalize.lock` (`flock`). Ничего чужого не трогать.

*Data-plane, запрещено:* менять `finalize-result.json`, `finalize-detail.log`,
`last-synced.json`, `pending.json`, `apply-prepare.json`, `scratch/` и улики
прежних generations. Итог `gate-only` пишется в `attempt-result.json` **внутри**
своей generation.

*Наружу:* Slack-уведомление для репетиции запрещено; если оно нужно, оно
маршрутизируется в отдельный безопасный канал и помечается как репетиция.
`notify_slack` вызывается на строке 145 безусловно, поэтому это правило —
изменение кода, а не соглашение.

`apply` пишет в свою generation и **дополнительно** публикует итог в production
state.

**Привязка.** `attempt.json` несёт `schema_version`, `before`, `after`,
`boundary`, `candidate_id`, `generation`, `run_id`. Потребитель обязан отказать,
если тройка или generation не совпадают с тем, что он видит.
`attempt.json` пишется **последним** и служит commit marker поколения: каталог
без него означает оборванную или ещё не завершённую запись и потреблению не
подлежит, даже если `gate-selection.json` уже появился атомарно.

**Два независимых поля исхода.**

- `status` ∈ `ok | failed | awaiting_decision` — отработал ли механизм. Это
  **существующий** ключ (`upstream-sync-finalize.sh:121`), его читают нынешние
  потребители. Он **не переименовывается**: переименование — слом схемы.
- `gate_verdict` ∈ `pass | block | unknown` — что решил гейт. Это **новое** поле.

Новые потребители читают **пару** `status` + `gate_verdict`. Честно
заблокировавшая репетиция — это `status=ok` **и** `gate_verdict=block`. Голый
`status: ok` без `gate_verdict` в такой ситуации читается как разрешение и
потому запрещён.

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

### T4. Явная boundary — раннер и **оба** вызывающих, одним коммитом (D3)

**Зачем.** Отказ на не-merge внутри раннера сломал бы `sync-local`, который
подаёт `--detach HEAD`. Раннер — исполнитель, не арбитр: он принимает
`--boundary` как данные и не спрашивает, мерж ли перед ним.

**Почему атомарно.** Удаление умолчания делает контракт вызова несовместимым в
обе стороны сразу. Разнесённое на два коммита, оно оставляет ветку с заведомо
красным `test_sync_local_customizations.py` на несколько задач вперёд, и
красный тест перестаёт отличать «я сломал» от «так задумано». Критерий порядка
здесь не «что от чего зависит», а «после какого коммита ветка остаётся зелёной».

**Что входит.**
- `run-fork-tests.sh`: `--boundary <ref|sha>` и `HERMES_UPSTREAM_BOUNDARY`,
  умолчания `upstream/main` больше нет, без границы — отказ с кодом 2.
- `upstream-sync-finalize.sh`: `merge_passes_fork_tests before after boundary`;
  граница берётся из уже сверенного `UPSTREAM_FULL` (485) и **не** выводится
  заново из `after^2` — второй источник истины может разойтись с проверкой
  родителей, которая уже пройдена.
- `sync-local-customizations.sh`: полный неизменяемый SHA разрешается **один**
  раз сразу после fetch и используется в трёх местах — `merge-tree`, сам
  `merge` и `--boundary` обоих прогонов. Иначе остаётся гонка: проверили одного
  кандидата, слили другого. Короткий `BASE_AFTER` годится только для сообщения.
- Тестовые двойники: стабы раннера читали worktree из `$1`, при новом argv это
  `--boundary`. Compatibility shim не нужен — квитанция (T9) всё равно докажет,
  что старую сигнатуру нельзя принять молча.

**DoD.** `pytest tests/scripts/test_run_fork_tests_selection.py tests/scripts/test_upstream_sync_finalize.py tests/scripts/test_sync_local_customizations.py`
зелёный, и среди случаев есть три: `test_boundary_is_explicit` — запуск без
границы даёт код 2 и пустой stdout (поведенческий, grep по исходнику критерием
не является); `test_explicit_boundary_selects_from_that_commit` — при
существующем отставшем `upstream/main` набор считается от переданного коммита,
то есть значение границы ею и управляет; `test_both_gate_runs_receive_the_same_upstream_boundary`
— оба прогона гейта получили один и тот же полный `upstream_head`.

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
namespace. Persisted manifest — самодостаточный JSON
`gate-selection.json`: он содержит схему, тройку SHA и `exists_pre`/
`exists_post`, а после выделения generation — также `candidate_id`, `generation`
и `run_id`, без которых T7 не может fail-closed проверить потребление.
Построчный stdout `--print-selection` остаётся отдельным shell-протоколом и не
является форматом persisted manifest.

**DoD.** `pytest tests/scripts/test_upstream_sync_finalize.py -k manifest`
зелёный; среди случаев — обрыв записи, после которого частичного
`gate-selection.json` в каталоге нет, и случай, где удалённый путь попадает
именно в `pre_only_paths`.

**Зависимости.** T5. **Сложность.** M.

### T7. Инвариант потребителя манифеста (D2)

**Что сделать.** Отказ при неизвестной `schema_version`; отказ, если HEAD
чекаута не равен ни `before`, ни `after`. Если манифест объявляет
`exists_pre=true`, а путь в чекауте `before` отсутствует — это **порча,
rc 2**, а не тихий пропуск. Пропуск допустим только при объявленном
`exists=false`. Consumer получает один самодостаточный `--selection-from` и:

- требует присутствия соседнего `attempt.json` как commit marker завершённой
  generation, но его содержимое не читает и второго data-контракта не заводит;
- сверяет `candidate_id`, `generation` и `run_id` внутри манифеста с тройкой SHA
  и каталогом `$STATE_DIR/attempts/<candidate_id>/<generation>/`, откуда файл
  загружен; скопированный в чужую generation манифест — порча, rc 2.
- получает ожидаемый корень attempts отдельным обязательным аргументом и после
  `resolve` требует точного совпадения физического родителя candidate с этим
  корнем; корректный по форме manifest из `/tmp/attempts/...` остаётся чужим.
- требует ровно один явный режим раннера: `--legacy-selection` либо
  `--selection-from ... --attempt-root ...`. Отсутствие обоих не означает
  молчаливый откат к вычислению, а даёт rc 2; sync-local до T15 явно объявляет
  временный legacy-режим.
- отвергает `before == after` и при построении, и при потреблении: у такого
  кандидата нельзя однозначно выбрать сторону `exists_pre`/`exists_post`.

Точное равенство expected attempts root ограничивает consumer текущим
`$STATE_DIR/attempts`, но **не** разделяет apply и gate-only: оба режима по
этому контракту живут под одним корнем, а изоляция репетиции обеспечивается
запретами data-plane в T19. Если позже репетициям понадобится отдельный корень,
consumer сейчас принимает ровно один expected root, и этот контракт придётся
явно изменить — текущая проверка не должна читаться как такая изоляция.

**DoD.** `scripts/run_tests.sh tests/scripts/test_run_fork_tests_selection.py tests/scripts/test_upstream_sync_gate.py`
зелёный; среди случаев все три: unknown schema, foreign HEAD, declared-exists
mismatch; плюс отсутствующий commit marker и manifest, перемещённый в чужую
candidate/generation или attempts root, несущий чужой `run_id`, а также
неявный режим selection и одинаковые `before`/`after`.

**Зависимости.** T6. **Сложность.** M.

### T8. `--continue-on-collection-errors` в раннер (D4)

**Зачем.** Восстановленная задача: в v2 флаг упоминался только как
«недостаточно», и ни одна задача его не добавляла — то есть исходное обрушение
сбора осталось бы неисправленным. Новому парсеру (T12) без флага нечего парсить.

**DoD.** `scripts/run_tests.sh tests/scripts/test_run_fork_tests_selection.py::test_collection_error_does_not_abort_run`
зелёный: набор с модулем, кидающим на импорте, даёт итоговую строку, а не
`Interrupted`.

**Зависимости.** T2. **Сложность.** S.

### T9. Квитанция раннера как argv-контракт (D7)

**Зачем.** Старый стаб примет лишние argv, использует только `$1` и молча
проигнорирует `--selection-from`. Лишний аргумент это не ловит; квитанция ловит.

**Что сделать.** Раннер печатает в stderr строку с версией контракта и
дайджестом манифеста, который **реально** потребил.
В этой же задаче parser отвергает повтор value-bearing singleton-опций вместо
молчаливого last-value-wins; отдельный поведенческий случай
`--boundary AAA --boundary BBB` обязан дать rc 2 до запуска тестов.

**DoD.** `scripts/run_tests.sh tests/scripts/test_upstream_sync_finalize.py -k runner_receipt`
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
Обычная регрессия узла, который был собран и проходил в baseline, но падает
после мержа, классифицируется как `fork_regression` в `common_path`.
Падение, которое уже было в baseline и продолжается после мержа, попадает
только в informational `pre_existing` и не блокирует.

T10 — чистый классификатор структурированных исходов и не меняет live-источник
решения: до T11 существующий `new_failures` остаётся legacy-компаратором.
Нельзя одновременно принимать решение по `new_failures` и по node-aware
классификатору для одного и того же прогона; переход на единственный
node-aware источник блокировки выполняется целиком в T11/T13.

**DoD.** `pytest tests/scripts/test_upstream_sync_gate.py -k classification`
падает, и в наборе случаев присутствуют все четыре строки, обычная регрессия,
`pre_existing`, incoherent outcome и детерминированная сортировка.

**Зависимости.** T7. **Сложность.** M.

### T11. Классификация с узким probe; владение третьим checkout (D6)

**Что сделать.** Присутствие узла — `collect-only` только на затронутых путях.
Причина падения — прогон **только точных newly-seen failing nodeids** на upstream
parent, включая новые узлы внутри `common_path`. Третий checkout создаёт и
держит **финализатор**, он же пишет `gate-upstream-probe.log` в attempt namespace
и передаёт структурированные свидетельства; компаратор остаётся чистой функцией.
T11 — единственная точка перехода для node-aware gate: после неё финальный
вердикт и блокировка строятся только из классификатора и его
`blocking_failures`, а `new_failures` больше не является вторым источником
решения в этом gate. Legacy caller может временно использовать
`new_failures` отдельно, но его результат не смешивается с node-aware
результатом.

**DoD.** `pytest tests/scripts/test_upstream_sync_gate.py -k node_probe_scope`
зелёный: на входе, воспроизводящем `f941342b`,
`test_exec_prefixes_interpreter_for_env_shebang_python_script` попадает в
`post_only_node`, и spy фиксирует, что probe запросил **ровно** множество
newly-seen failing nodeids (попытка прогнать путь целиком роняет тест).

**Фактическое выполнение.** T11 выполнен коммитами `4e51de4d1d`
(`make upstream gate classify exact failing nodes`) и `24c7a135af`
(`fail closed when a gate run is unreadable`). В реализацию вошли также
`--probe-nodeids-from`, `node-outcome`, третий checkout на `boundary`,
`gate-upstream-probe.log` в attempt namespace и переключение live verdict на
node-aware classifier. Это было намеренное расширение до согласованной точки
перехода T11; в сообщении о результате эти изменения должны быть перечислены,
а не подразумеваться по SHA.

**Зависимости.** T10. **Сложность.** M.

### T12. Общий parser outcomes: FAILED и ERROR (D4)

**DoD.** `scripts/run_tests.sh tests/scripts/test_upstream_sync_gate.py -k outcomes` зелёный;
среди случаев — прогон, где между baseline и post **появилась** ошибка сбора, и
компаратор возвращает её находкой, а не rc 2. Приёмочные golden inputs должны
включать дословно `2 errors in 0.05s` (множественное `errors` не должно
потеряться из-за `error\b`) и `no tests ran in 0.01s` (нечитаемый прогон,
rc 2), рядом с рабочими формами `2 passed, 1 error in 0.12s` и боевой строкой
`76 failed, 6259 passed, 2 skipped, 6 warnings in 679.63s`.

**Фактическое выполнение.** T12 выполнен коммитом `aff19e73b5`
(`parse pytest collection errors as gate outcomes`). Реализация добавила общий
parser и comparator для failed nodeids и collection errors, сохранила
`2 errors in 0.05s` как читаемый исход, оставила `no tests ran in 0.01s`
нечитаемым, и подключила этот результат к `node-outcome`, чтобы collection
error давал `collect_ok=false`, а не пустой «успешный» outcome.
Формат collection error перепроверен на настоящем выводе pytest: раннер
теперь вызывает `-rEf`, parser читает строку `ERROR <path> - ...`, а
регрессия покрыта одновременно прямым subprocess pytest и существующим
runner seam-тестом. Follow-up исправление зафиксировано коммитом
`f4567e7496` (`parse real pytest collection error summaries`); оно заменяет
синтетический `ERROR collecting ...`, который pytest в этом режиме не
печатает, и довозит имя сломавшегося модуля до outcome.

**Зависимости.** T8. **Сложность.** M.

### T13. Миграция схемы `gate-failures.json` (D2)

**Зачем.** `upstream_sync_triage.py:411` читает только `new_failures` и выходит
при пустом списке.

**Что сделать.** `schema_version`, корзины отдельными ключами и агрегированный
`blocking_failures` с инвариантом: он равен **устойчивому уникальному
объединению** блокирующих корзин классификатора и не содержит informational
`pre_existing` и `unknown`. Для node-aware gate это единственный список,
по которому caller блокирует мерж; `new_failures` не подмешивается и остаётся
только legacy-проекцией там, где node-aware путь ещё не используется.

**Уже реализовано досрочно.** Поле и вычисление `blocking_failures`, а также
его использование как единственного источника live verdict для node-aware
gate вошли в T11 (`4e51de4d1d`). T13 всё ещё владеет миграцией persisted
`gate-failures.json`, потребителя triage, schema-golden тестами и удалением
остаточной legacy-проекции; эти части не считать выполненными по факту ранней
реализации поля.

**Фактическое выполнение.** Остаток T13 выполнен коммитом `37a18b750a`
(`make triage consume v2 blocking failures`). Gate-owned builder теперь
единственным местом формирует `upstream-sync-gate-failures/v2`: он сортирует
и дедуплицирует union `common_path`/`post_only_path`, не включает
`pre_existing`, `unknown` или `unreadable_runs`, и пишет результат атомарно.
Finalizer вызывает этот builder вместо второго inline-формата. Triage для v2
читает только `blocking_failures`, а для legacy сохраняет `new_failures`, и
schema-тест доказывает одинаковый набор findings в обоих форматах. Старый
finalize stub обновлён для точного `--probe-nodeids-from`, чтобы интеграция
проверяла новый node-aware контракт, а не искусственно создавала `unknown`.

**DoD.** `pytest tests/scripts/test_upstream_sync_triage.py -k schema` зелёный;
среди случаев — проверка самого инварианта, а не только совпадения счётчика.

**Зависимости.** T11, T12. **Сложность.** M.

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

**Область.** Только пересеквенсирование под единый манифест. Передачу границы
этот вызывающий получил в T4 — у одной миграции один владелец.

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

**Что сделать.** Legacy-публикация улик в production state условна по режиму.
Только `apply` вправе копировать `gate-baseline.log`/`gate-post.log`, копировать
`gate-failures.json` и удалять production `gate-failures.json` при успехе.
`gate-only` не выполняет ни одну из этих операций: все его улики и итог
остаются только в новой generation. Эти три места в нынешнем финализаторе
нельзя оставить общими после выделения shared `run_gate`.
Manifest consumer получает именно `$STATE_DIR/attempts` этого запуска как
ожидаемый корень и не может потребить generation из другого state namespace.

**DoD.** `pytest tests/scripts/test_upstream_sync_finalize.py -k gate_only_isolation`
зелёный: тест побайтово сравнивает **весь** `$STATE_DIR` до и после, исключая
`attempts/` и перечисленные control-plane файлы (`finalize-request*.json`,
`finalize.lock`), и требует совпадения; созданное — только внутри своей
generation; отдельный случай доказывает, что повторный запуск на той же тройке
заводит новую generation, а не переписывает прежнюю.

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
терминального состояния, проверить journal и результат попытки. Проверка самого
`.path` в T23 **не** входит — она отложена в T27.

**DoD.** `scripts/acceptance/published-gate-smoke.sh` завершается кодом 0:
`status=ok`, `gate_verdict` присутствует и осмыслен, `HEAD` не менялся.
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
`status=ok`, `gate_verdict=pass`, `last-synced.json` несёт актуальный
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
