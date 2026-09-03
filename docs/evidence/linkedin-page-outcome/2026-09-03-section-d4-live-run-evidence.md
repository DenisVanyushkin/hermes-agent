# Пакет улик: D4, узкий живой прогон

**Дата:** 2026-09-03. **Составил:** Claude. **Ревьюер:** Codex, read-only.
**Санкция:** прямой receipt владельца, дословно «давай прогон и HTTP-код
подтянем», плюс выбор владельца поднимать браузер вручную, без supervisor.

Этот файл существует потому, что ревьюер отказался поднимать статус по
пересказу: `run_id` в репозитории не было, и улика жила только в моих
сообщениях. Здесь она зафиксирована.

---

## 1. Прогонов было два. Первый не тронул LinkedIn

| | первый | второй |
|---|---|---|
| `run_id` | `gate-a-20260903T162652Z` | `gate-a-20260903T162926Z` |
| exit | 0 | 0 |
| `source_states.linkedin` | `blocked_no_safe_isolation` | `observed` |
| `received_records` | 0 по всем ячейкам | 430 / 348 / 152 |
| улика | `summary-attempt1-no-isolation.json`, SHA-256 `6ad41d394061c2bdd6a5b7572bc41b2216c137b71525b64402409e6a957ad04a` | `summary.json`, SHA-256 `28020116ef51782059a439b3faa1d6a2be2b3cdba14eafa22ee2c9a2d2b3b433` |

**Первый прогон — `environment_failure`, и он поучителен.** `exit 0` был
честным кодом возврата команды и не означал **ничего** о результате: проба
отказалась работать, не найдя безопасной изоляции, и до LinkedIn не дошла.
Замечено чтением `source_states`, а не доверием к коду возврата.

**Причина.** `write-manifest` **не генерирует** секцию `source_isolation`;
в замороженном стенде она есть, её положил шаг, которого в экспортёре нет.
Секция взята из замороженного манифеста дословно — те же `mode`,
`collection_method`, `cdp_url`, тот же логаутный профиль, — с перенаправлением
только путей на новый корень. Изоляция это safety-часть стенда, и она
воспроизведена, а не сочинена.

## 2. Что исполнялось: пинованная ревизия

| величина | значение |
|---|---|
| `commit` | `ce070a9e0f85bdfa2bcd1444bfe8e53cfaaffce1` |
| `runtime_sha256` | `705688f42f772feba7ce52c1e14aea78e086677eb0fac0386e4d30e22a7abacd` |
| `source_sha256` | `61560592a89c7c9b177563343644ecbc1d31e5c860b88dc056ad470609127033` |
| корень эксперимента | `~/.hermes/job_intel/experiments/gate-a/ce070a9e0f85bdfa2bcd1444bfe8e53cfaaffce1` |
| `manifest.yaml` | SHA-256 `1d7060d5082643ea17c387b0439465495e05346342923cf5816aa50526792837` |
| `experiment.sqlite3` | SHA-256 `470a2f66bc8324960b83728e7013675c14b3e0c6de6abe1ce551753b637e664f` |
| python | `3.12.13`, пинован в `python-runtime/venv` |
| `PYTHONPATH` | корень `runtime`, выставляется entrypoint'ом |

**Что прогон исполнял именно исправленный код** — проверяемо:
`runtime/job_intel/browser_sourcing.py` имеет SHA-256
`1b4e63b0f335bcafe2cc739e2cff8acec44b550ab4757ac2fc32c83ee91c49e9`, что байт в
байт равно `git show ce070a9e0f:job_intel/browser_sourcing.py`. Замороженная
улика раздела B несёт другой файл, `dfe155c6f8326382ceebec203cda42e4e8a18127354247eb0588c778150c642c`,
и осталась нетронутой с правами `dr-xr-xr-x`.

Команда прогона:

```bash
D=~/.hermes/job_intel/experiments/gate-a/ce070a9e0f85bdfa2bcd1444bfe8e53cfaaffce1
env -u SLACK_BOT_TOKEN -u SLACK_APP_TOKEN -u JOB_INTEL_SLACK_WEBHOOK_URL \
  PRODUCT_SEARCH_PYTHON=$D/python-runtime/venv/bin/python \
  PRODUCT_SEARCH_RUNTIME_ROOT=$D/runtime \
  /usr/bin/timeout 2400 bash $D/runtime/scripts/job_intel_product_search_experiment.sh \
  run $D/manifest.yaml
```

Entrypoint **отказывается** работать при наличии `SLACK_BOT_TOKEN`,
`SLACK_APP_TOKEN` или `JOB_INTEL_SLACK_WEBHOOK_URL`: доставка запрещена
конструкцией, а не обещанием. Переменные сняты явно.

## 3. Стенд: что подняли и чем это отличалось от авторизованного

Логаутный браузер поднят **вручную, без supervisor**, потому что supervisor
безусловно зовёт `browser-desktop-bootstrap.sh` с `apt-get`, `pip` и
`playwright install` — объявленный инвариант плана. Ни одной установки не
выполнено; использован уже лежавший на диске `chromium-1228`.

| | авторизованный (не трогали) | логаутный (наш) |
|---|---|---|
| профиль | `/var/lib/browser-desktop/profiles/linkedin` | `/var/lib/browser-desktop/profiles/c1anoauth` |
| CDP | `127.0.0.1:9222` | `127.0.0.1:9236` |
| реле | `169.254.77.2:19222` | `169.254.77.2:19236` |
| PID | chrome `399123`, relay `399125` | chrome `2187980`, relay `2188487` |

Оба стенда живут в **одном** netns `ln-eg` — сверено по inode
`net:[4026533032]`. Сосуществование заложено конструкцией:
`CDP_RELAY_PORT = CDP_PORT + 10000` в bootstrap, поэтому 19236 ↔ 9236 не
конфликтует с 19222 ↔ 9222. Браузер отчитался как `Chrome/149.0.7827.55` —
контрольная сборка.

## 4. Результат по страницам

Двадцать четыре страницы, все `usable_result_surface`, все `http_status` 200,
ни одной причины на оси безопасности, и **на каждой паре исполнены оба
объявленных offset'а**.

| ячейка | query | offset | status | класс | safety | ID | новых |
|---|---|---|---|---|---|---|---|
| `uk` | `54725f2ae193` | 0 | 200 | `usable_result_surface` | — | 60 | 60 |
| `uk` | `54725f2ae193` | 25 | 200 | `usable_result_surface` | — | 60 | 0 |
| `uk` | `8a2625b6303b` | 0 | 200 | `usable_result_surface` | — | 60 | 60 |
| `uk` | `8a2625b6303b` | 25 | 200 | `usable_result_surface` | — | 60 | 0 |
| `uk` | `bd600368b345` | 0 | 200 | `usable_result_surface` | — | 60 | 60 |
| `uk` | `bd600368b345` | 25 | 200 | `usable_result_surface` | — | 60 | 0 |
| `uk` | `fb910d029b4f` | 0 | 200 | `usable_result_surface` | — | 60 | 60 |
| `uk` | `fb910d029b4f` | 25 | 200 | `usable_result_surface` | — | 60 | 0 |
| `singapore` | `1d032936232f` | 0 | 200 | `usable_result_surface` | — | 60 | 60 |
| `singapore` | `1d032936232f` | 25 | 200 | `usable_result_surface` | — | 60 | 0 |
| `singapore` | `4967176e296c` | 0 | 200 | `usable_result_surface` | — | 60 | 60 |
| `singapore` | `4967176e296c` | 25 | 200 | `usable_result_surface` | — | 60 | 0 |
| `singapore` | `e2dae9a6f476` | 0 | 200 | `usable_result_surface` | — | 60 | 60 |
| `singapore` | `e2dae9a6f476` | 25 | 200 | `usable_result_surface` | — | 60 | 0 |
| `singapore` | `ebd152abb57e` | 0 | 200 | `usable_result_surface` | — | 60 | 60 |
| `singapore` | `ebd152abb57e` | 25 | 200 | `usable_result_surface` | — | 60 | 0 |
| `kazakhstan` | `356ff9964e7b` | 0 | 200 | `usable_result_surface` | — | 26 | 26 |
| `kazakhstan` | `356ff9964e7b` | 25 | 200 | `usable_result_surface` | — | 26 | 0 |
| `kazakhstan` | `75e39ed80a81` | 0 | 200 | `usable_result_surface` | — | 49 | 49 |
| `kazakhstan` | `75e39ed80a81` | 25 | 200 | `usable_result_surface` | — | 49 | 0 |
| `kazakhstan` | `9c2c792a1c08` | 0 | 200 | `usable_result_surface` | — | 52 | 52 |
| `kazakhstan` | `9c2c792a1c08` | 25 | 200 | `usable_result_surface` | — | 52 | 0 |
| `kazakhstan` | `f85e382a5f69` | 0 | 200 | `usable_result_surface` | — | 53 | 53 |
| `kazakhstan` | `f85e382a5f69` | 25 | 200 | `usable_result_surface` | — | 53 | 0 |

`final_url_start` на нулевом offset'е — `None`, на двадцать пятом — `0`. Та же
нормализация, которую померил раздел B, и аудит называет её
`pagination_not_observed`, а не глубиной.

**Перекрёстная сверка с замороженной уликой.** Запрос `fb910d029b4f` в `uk` —
тот же, чей capture лежит в корпусе раздела B (`uk-fb910d029b4f6c00bb2d-0`), и
он снова дал ровно **60** ID. Число не изменилось от починки; изменился ярлык.

## 5. Результат по ячейкам

| ячейка | legacy `outcome` пары | получено | credited | исход ячейки | причина удержания |
|---|---|---|---|---|---|
| `uk` | `completed` | 430 | 133 | `coverage_unestablished_credit_withheld` | `pagination_progression_not_observed` |
| `singapore` | `completed` | 348 | 95 | `coverage_unestablished_credit_withheld` | `pagination_progression_not_observed` |
| `kazakhstan` | `completed` | 152 | 30 | `blocked` | `pagination_progression_not_observed` |
| `cee` | `blocked` | 0 | 0 | `blocked` | `coverage_not_evaluated` |

**`uk` и `singapore` — это и есть предмет доказательства.** Обе имеют две
завершённые продуктивные семьи и ненулевой credited, то есть **без удержания
дали бы `candidate_records_found`** и открыли бы Gate. Он не открылся, и
причина названа своим именем, а не заимствована у `insufficient_breadth`.

**`kazakhstan` осталась `blocked`**, потому что `headhunter` упал
`blocked_extraction_failure`. Это правильно: вето переписывает **только**
`candidate_records_found` и legacy-путь не трогает.

## 6. Расхождение объёма, названное явно

Заранее я объявил **три** ячейки: `uk`, `singapore`, `kazakhstan`. Фактически
прогон покрыл и **четвёртую — `cee`**. Она пришла не от меня: `write-manifest`
сам вписал её в `bounded_proof.negative_control` как отрицательный контроль по
правилу `first_alphabetical_unsupported_excluding_bounded_v1`.

**`cee` не используется как приёмочная улика D4** и учитывается только как
незаявленный отрицательный контроль, порождённый машинерией эксперимента.
Расхождение найдено ревьюером; принято.

## 7. Уборка: обратимость доказана, а не обещана

Погашено **только** запущенное этим прогоном, по явным PID: `2188487`,
`2187980`, `2187978`, `2187977`, `2187975`. Никаких широких шаблонов и никаких
kill по общим маскам.

Списки слушающих сокетов в `ln-eg` **до** и **после** совпадают построчно,
включая PID:

```text
127.0.0.1:5901       x11vnc     pid=399106
127.0.0.1:9222       chrome     pid=399123
169.254.77.2:19222   python3    pid=399125
169.254.77.2:6080    websockify pid=399112
```

Авторизованный chrome `399123` и его реле `399125` живы и не перезапускались.
Снимки: `~/.hermes/job_intel/discovery/linkedin-page-outcome-d4-20260903/`,
файлы `preflight.txt` и `postflight-sockets.txt`.

**Ошибка при уборке, названная.** Первый заход был сделан через
`pgrep -f "9236"`, и шаблон совпал с **собственной оболочкой**, в чьей
командной строке этот номер стоял: ssh оборвался с кодом 255. Именно поэтому
уборка по общим шаблонам запрещена, и второй заход шёл по списку PID.

## 8. Что этот прогон доказал и чего не доказал

**Доказал.** Стык `страница → trace → аудит → вето → артефакт` целиком, на
живом источнике, через production composition root. Классификация публичной
вёрстки, исполнение обоих объявленных offset'ов без ложного обрыва, приход
HTTP-статуса из Playwright, fail-closed удержание на композиции, которая без
него открыла бы Gate, и честное имя исхода.

**Не доказал.** Работу в суточном пайплайне: таймеры `job-intel` остаются
замаскированными, ветка не влита и не опубликована, живой чекаут не обновлён.
Ни одного вывода о поведении под расписанием, нагрузкой или на других ячейках
из этого прогона не следует. Достаточность охвата по-прежнему **не
установлена** и по C′ установлена этим прогоном быть не могла.

## 9. Статус

**`composition_verified`.**

`runtime_verified` не заявляется: наблюдался прогон пробы, а не работа системы
под её штатным расписанием. `deployed_not_verified` не заявляется тем более —
деплоя не было и он не разрешён.
