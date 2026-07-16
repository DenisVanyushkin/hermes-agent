# Exec-mechanism audit — как терминал агента исполняет `fam`-команды (T13, находка 7)

**Что:** investigation-only аудит exec-пути гейтвея для `fam`, которые скилл
`amina-fam` просит агента гонять через `terminal`-инструмент. Вопрос:
argv-массив или shell-строка; что реально блокирует "dangerous command"
гейт (`$(`, backtick, `>`, `|`, `;`, `&&`); безопасно ли as-is.

**Локация:** VM `hermes-home`, `~/.hermes/hermes-agent`, branch
`local/customizations`, HEAD `ff658db3` (2026-07-16).

## Трасса: tool → handler → subprocess/docker
1. Скилл: `custom/skills/amina-fam/SKILL.md:82-89` — "ONE plain command
   line", never `&&`/`;`/pipe/`bash -c`; claims chained/shell-wrapped
   commands "trip the dangerous-command approval gate and stall the
   conversation".
2. Инструмент: `tools/terminal_tool.py` — описание для LLM (`TERMINAL_TOOL_DESCRIPTION`,
   ~L958) явно "Execute shell commands on a Linux environment" — это шелл-
   инструмент, не argv-exec.
3. Гейт: `tools/terminal_tool.py:2281-2285` вызывает `_check_all_guards(command,
   env_type, has_host_access=_docker_has_host_access(config))` →
   `tools/approval.py:2537 check_all_command_guards`.
4. Sandbox: `terminal.backend: docker`, `docker_image: hermes-sandbox-amina:1`
   (`~/.hermes/config.yaml:653,665`). `docker_volumes` (config.yaml:668-682)
   bind-mounтят host-пути **rw**: `~/.hermes/private/amina`,
   `~/.hermes/hermes-agent`, `~/.hermes/scripts`, `~/.hermes/config.yaml` и др.
   → `_docker_has_host_access()` (`tools/terminal_tool.py:272-278`) → `True`
   → `_should_skip_container_guards("docker", has_host_access=True)`
   (`tools/approval.py:2217-2228`) → `False`. Т.е. sandbox НЕ получает
   "изолированный контейнер, пропустить гейт" фаст-пас — гейт реально
   работает для fam-команд (это хорошо: иначе fam был бы вообще без охраны).
5. Спавн процесса: `tools/environments/docker.py:1059-1076 _run_bash()`
   строит `["docker","exec",...,container_id,"bash","-c",cmd_string]` →
   `_popen_bash()` (`tools/environments/base.py:136-154`) →
   `subprocess.Popen(cmd, ...)`, **без** `shell=True`. Внешний вызов
   `docker exec` — безопасный argv-список, но сама команда агента
   (`cmd_string`) — единственный аргумент `bash -c`, т.е. она полностью
   парсится и исполняется настоящим POSIX-шеллом внутри контейнера.

**Ответ на вопрос 1: строка через shell** (`bash -c`), не argv-массив.

## Вопрос 2 — что реально блокирует гейт
Regex-гейт = `DANGEROUS_PATTERNS` (`approval.py:547-761`) +
`HARDLINE_PATTERNS` (`approval.py:366-403`), плюс непрозрачный внешний
бинарь `tirith` (`~/.hermes/bin/tirith`, `tools/tirith_security.py:730`).

Проверено эмпирически (`venv/bin/python3` → `detect_dangerous_command()` /
`detect_hardline_command()` напрямую, и `tirith check --json
--non-interactive --shell posix --`) на безобидных fam-подобных командах:

| конструкция | regex-гейт (approval.py) | tirith |
|---|---|---|
| `;` chain | не матчится | allow |
| `&&` chain | не матчится | allow |
| `\|` pipe | не матчится | allow |
| `$(...)` | не матчится | allow |
| `` ` `` backtick | не матчится | allow |
| `>` redirect | не матчится | allow |
| `bash -c "..."` / `sh -c "..."` | **матчится** — `approval.py:588`  `r'\b(bash\|sh\|zsh\|ksh)\s+-[^\s]*c(\s+\|$)'`, "shell command via -c/-lc flag" | allow |

Ни один из шести метасимволов не блокируется как таковой —
`DANGEROUS_PATTERNS` это keyword/construct denylist (`rm -rf`, `dd`, `mkfs`,
`curl\|sh`, `sudo -S`, `git reset --hard` и т.п.), сознательно, см. комментарий
`approval.py:297-306`: "Recoverable-but-costly operations ... stay in
DANGEROUS_PATTERNS where yolo can pass them through". Из всего, что скилл
запрещает, код реально ловит только явный `bash -c`/`sh -c`. Tirith тоже не
флагует ни одну из протестированных конструкций (ruleset — бинарник +
`~/.local/share/tirith/tirith-threatdb.dat`, не аудируем из исходников).

## Вердикт: (б) — расхождение между декларацией скилла и реальным гейтом
`SKILL.md:86-88` утверждает, что chained/shell-wrapped команды "trip the
dangerous-command approval gate" — для голого `;`/`&&`/`\|`/`$()`/backtick/`>`
вокруг безобидного содержимого это неверно: гейт пропускает их молча, без
approval-промпта. Ловится только явный `bash -c`/`sh -c`.

Реальная защита = (1) дисциплина самой LLM (следование тексту скилла) + (2)
общий keyword-denylist terminal-инструмента, не специфичный для fam. Если
агент (случайно, либо под prompt injection из недоверенного контента — SMS,
страница, чужой calendar-title) вставит в аргумент fam control-символ без
экранирования, шелл внутри контейнера (has_host_access=True, rw на
`private/amina`/`hermes-agent`/`scripts`/`config.yaml`) его исполнит; ни один
из протестированных метасимволов сам по себе тревогу не поднимет. Это не
дыра, специфичная для fam — это осознанный trade-off всего terminal-гейта
(не блокировать легитимные pipes/redirects), но текст скилла создаёт ложное
чувство защищённости.

## Follow-up
1. **СДЕЛАНО 2026-07-16**: `SKILL.md` (обе копии, sha256 d35ff0ca…)
   переписан — ложное "trip the dangerous-command approval gate" для plain
   chaining убрано; правило переформулировано как operational hygiene (шелл
   интерпретирует метасимволы → порча аргументов) + честно: только `bash -c`
   реально ловится гейтом, голые `;`/`&&`/`|` — нет. **Активация за Денисом**
   (рестарт gateway + /reset — как для любой правки скилла).
2. (Опционально, отдельная security-задача, НЕ сделано) либо добавить в
   `DANGEROUS_PATTERNS` структурный паттерн на голые `;`/`&&`/`\|` для команд,
   начинающихся с `fam `, либо завести отдельный узкий exec-путь для fam
   (argv-массив прямо в контейнер, без `bash -c`), чтобы аргументы fam
   физически не парсились шеллом независимо от дисциплины LLM.

## Метод проверки (воспроизводимо, read-only)
```
venv/bin/python3 -c "from tools.approval import detect_dangerous_command as d; print(d('fam cal show week ; fam rem active --json'))"
~/.hermes/bin/tirith check --json --non-interactive --shell posix -- 'fam cal show week ; fam rem active --json'
```
