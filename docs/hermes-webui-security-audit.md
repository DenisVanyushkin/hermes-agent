# Hermes WebUI Security Audit

Дата: 2026-06-06

Объект: `https://github.com/nesquena/hermes-webui`, ветка `master`, commit `32d46f44503df91d0c2493950e298f10f8b35afe` (`v0.51.293`, 2026-06-06).

## Краткий вывод

Hermes WebUI можно ставить рядом с вашим Hermes только как локальный privileged admin UI: bind на `127.0.0.1`, доступ через SSH tunnel, запуск не от root, сильная аутентификация, без публикации порта в интернет.

В публичном виде это небезопасно. Даже с паролем WebUI имеет поверхности, эквивалентные доступу к shell, workspace file manager, git operations, Hermes sessions/memory и части конфигурации агента. Компромисс WebUI на вашем VPS фактически становится компромиссом `/home/hermes/.hermes/`, включая `auth.json`, `.env`, `state.db`, sessions и Job Intel state.

## Что проверено

- Python backend без web-framework: `server.py`, `api/auth.py`, `api/routes.py`, `api/workspace.py`, `api/terminal.py`, `api/workspace_git.py`, `api/upload.py`, `api/startup.py`, `api/config.py`.
- Frontend: `static/index.html`, основные `static/*.js` sinks (`innerHTML`, `insertAdjacentHTML`, `document.write`).
- Docker/bootstrap: `Dockerfile`, `docker_init.bash`, `docker-compose*.yml`, `bootstrap.py`, `start.sh`, `ctl.sh`.
- Автоскан: `uv run --with bandit bandit -r /private/tmp/hermes-webui-audit/hermes-webui-master -ll -ii`.

## Findings

### H-1. Web terminal делает WebUI эквивалентом shell-доступа

Evidence:

- `POST /api/terminal/start`, `/input`, `/resize`, `/close` зарегистрированы в `api/routes.py`.
- `_handle_terminal_start()` берет session workspace и запускает terminal.
- `api/terminal.py` запускает интерактивный shell через `subprocess.Popen` с `args: _shell_argv(shell)`, `cwd`, PTY и process group.

Impact: любой, кто вошел в WebUI, получает интерактивный shell от имени процесса WebUI в выбранном workspace. На вашем Hermes это достаточно, чтобы читать/менять файлы агента, дергать git, запускать код и потенциально дотянуться до секретов, доступных Unix-пользователю.

Recommendation: не публиковать WebUI наружу. Считать WebUI локальной админкой уровня SSH. Запускать от `hermes`, не от root, и только на `127.0.0.1`.

### H-2. File/workspace/git APIs дают широкую мутацию состояния

Evidence:

- `api/routes.py` содержит POST handlers для `file/delete`, `file/save`, `file/create`, `file/rename`, `file/move`, `git/stage`, `git/discard`, `git/commit`, `git/pull`, `git/push`, `git/checkout`.
- `api/workspace.py` доверяет каталогам под home, saved workspaces и default workspace.
- `api/workspace_git.py` выполняет git shell-free и чистит опасные `GIT_*` env vars, что хорошо, но сама возможность все равно privileged.

Impact: вошедший пользователь WebUI может менять рабочие деревья, коммитить, пушить, удалять файлы и менять workspace state. Если workspace указывает на `/home/hermes/.hermes/hermes-agent`, это прямой контроль локальных кастомизаций агента.

Recommendation: workspace должен быть минимально нужным. Не добавлять `/home/hermes`, `/home/hermes/.hermes` или `/` как workspace. Если нужен доступ к agent repo, добавлять только `/home/hermes/.hermes/hermes-agent` и понимать, что это админский доступ.

### H-3. Auth по умолчанию выключен; non-loopback без auth только предупреждает

Evidence:

- `api/auth.py` явно говорит: authentication off by default, включается через `HERMES_WEBUI_PASSWORD`, settings или passkeys.
- `server.py` при bind не на loopback без auth печатает предупреждение, но не блокирует запуск.
- Docker Compose публикует порт на `127.0.0.1:8787` по умолчанию, что правильно, но в контейнере `HERMES_WEBUI_HOST=0.0.0.0`.

Impact: ошибка в port mapping, reverse proxy или firewall может открыть полный WebUI без auth.

Recommendation: даже при loopback включить пароль. Для вашего VPS дополнительно проверить UFW/Traefik/nginx, что `8787` не слушает внешний интерфейс.

### M-1. CSP сейчас не является enforcing-защитой

Evidence:

- `server.py` отправляет `Content-Security-Policy-Report-Only`, а не enforcing `Content-Security-Policy`.
- Policy содержит `script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net` и `style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net`.
- `static/index.html` использует inline scripts, `document.write`, CDN assets и частично SRI.

Impact: при XSS CSP в текущем режиме в основном логирует, но не блокирует. Из-за Web terminal и file APIs последствия XSS высокие.

Recommendation: для localhost-only deployment это acceptable residual risk. Для reverse-proxy/public deployment сначала нужен enforcing CSP без `unsafe-inline` или с nonce/hash migration.

### M-2. Authenticated SSRF/probe surface есть, но это вторичный риск

Evidence:

- onboarding/model provider probes принимают base URL и делают `/models` запросы.
- В коде есть явный комментарий, что private-IP ranges не блокируются полностью, иначе local model servers станут бесполезны.
- В `api/config.py` есть частичный guard against private IP для non-trusted endpoints.

Impact: вошедший пользователь может использовать WebUI как HTTP client к внутренним сервисам. Если attacker уже вошел в WebUI, у него есть terminal/file APIs, поэтому SSRF не главный риск. Но для публичной установки это усиливает blast radius.

Recommendation: не открывать WebUI недоверенным пользователям. Не считать парольную форму multi-tenant boundary.

### M-3. Bootstrap/supply-chain: curl-pipe и непинованные runtime deps

Evidence:

- `bootstrap.py` при отсутствии Hermes Agent запускает `curl -fsSL .../install.sh | bash`.
- `Dockerfile` ставит `uv` через `curl -LsSf https://astral.sh/uv/install.sh | ... sh`.
- `requirements.txt` задает `pyyaml>=6.0`, `cryptography>=42.0`, без lock-файла.

Impact: на production VPS bootstrap/build-time supply chain получает высокий privilege. Это не exploit в WebUI, но риск установки.

Recommendation: не запускать `bootstrap.py` auto-install на вашем Hermes host. Клонировать pinned commit/tag, ставить зависимости в контролируемый venv, фиксировать версию контейнерного image или commit SHA.

## Положительные наблюдения

- Password auth использует PBKDF2-SHA256 600k, HMAC signed session cookies, HttpOnly, SameSite=Lax, rate limiting и persisted sessions с `0600`.
- CSRF реализован для unsafe browser requests через Origin/Referer + per-session header token.
- File read/write path traversal и symlink races закрываются лучше среднего: `resolve()`, workspace containment, `openat`, `O_NOFOLLOW`, anchored create/delete/write.
- Upload/extract имеет size cap, member-count cap, zip-slip/tar-slip checks и anchored writes.
- Git subprocess calls `shell=False`, timeouts, scrub dangerous `GIT_*` env vars.
- Docker default port mapping `127.0.0.1:8787:8787` безопаснее, чем all-interfaces.

## Bandit result

Команда завершилась с non-zero, потому что `bandit` нашел issues. Большая часть шума относится к tests, `assert`, `urllib.urlopen` в тестовых helpers и hardcoded `/tmp`.

Ручная оценка production-срабатываний:

- `B324 hashlib.sha1` в `api/updates.py` используется для короткого dirty suffix версии, не для безопасности. False positive для этого threat model.
- `B608 SQL` в просмотренных местах использует schema-derived column fragments и parameter placeholders. Не подтверждено как SQL injection.
- `B310 urlopen` подтверждает реальную probe/SSRF surface, отраженную в `M-2`.

## Рекомендованный профиль установки для вашего Hermes

1. Клонировать pinned commit/tag, не `master` без фиксации SHA.
2. Запускать systemd unit от `User=hermes`, не через root shell.
3. `HERMES_HOME=/home/hermes/.hermes`
4. `HERMES_WEBUI_AGENT_DIR=/home/hermes/.hermes/hermes-agent`
5. `HERMES_WEBUI_HOST=127.0.0.1`
6. `HERMES_WEBUI_PORT=8787`
7. Включить password auth до любой удаленной эксплуатации.
8. Не открывать `8787/tcp` в UFW и не вешать публичный reverse proxy.
9. Доступ только через SSH tunnel: `ssh -N -L 8787:127.0.0.1:8787 hermes`.
10. После запуска проверить на host: `ss -ltnp | grep 8787`, `curl http://127.0.0.1:8787/health`, `ufw status numbered`.

## Malware / secret leakage / вредоносный код

Я не нашел в проверенном commit признаков явной малвари: нет подтвержденных зашитых реальных секретов, нет очевидного production `eval/new Function`, нет скрытого base64-loader/stager, нет подозрительных hardcoded exfiltration endpoints за пределами ожидаемых LLM/provider/CDN/install URLs. Найденные ключи выглядят как fake/test values в `tests/`.

Остаются реальные supply-chain риски:

- `bootstrap.py` умеет запускать Hermes Agent installer как `curl ... | bash`.
- `Dockerfile` и `docker_init.bash` ставят `uv` через `curl ... | sh`.
- `requirements.txt` содержит диапазоны `pyyaml>=6.0`, `cryptography>=42.0`, lock-файла нет.
- `static/index.html` грузит frontend-библиотеки с `cdn.jsdelivr.net`; часть с SRI, CSS без SRI по комментарию в самом файле.
- `static/vendor/smd.min.js` vendored/minified: это не индикатор малвари само по себе, но такой файл сложнее ревьюить вручную, поэтому его стоит считать supply-chain artifact.

Отдельно: WebUI не обязан быть вредоносным, чтобы утекли секреты. Если он запущен с доступом к `/home/hermes/.hermes`, он по дизайну может читать Hermes config, `.env`, `auth.json`, sessions и workspace. Компрометация WebUI, XSS в браузере админа или внешний доступ к порту превращаются в практический риск утечки токенов.

Mitigation для вашего VPS:

1. Не запускать auto-bootstrap install paths на production host.
2. Использовать pinned commit `32d46f44503df91d0c2493950e298f10f8b35afe` или конкретный tag, а не плавающий `master`.
3. Ставить зависимости контролируемо: lock/constraints или заранее собранный venv/container image.
4. Не давать WebUI root, Docker socket, `/`, `/home/hermes` целиком или лишние bind mounts.
5. При повышенной паранойе заблокировать egress всем, кроме нужных LLM endpoints, GitHub/Astral на этапе build/install и jsdelivr только если оставляете CDN assets.
6. Обновления делать через `git fetch && git diff <old>..<new>` с повторным ревью опасных файлов: `bootstrap.py`, `Dockerfile`, `docker_init.bash`, `server.py`, `api/routes.py`, `api/terminal.py`, `api/config.py`, `static/index.html`, `static/*.js`.

## Итог

Безопасно при соблюдении локальной модели доступа. Небезопасно как публичный web service.

Для вашего текущего Hermes я бы ставил только в режиме `127.0.0.1 + SSH tunnel + User=hermes + pinned SHA`, с явной проверкой, что порт не появился на `0.0.0.0` и не проброшен через Traefik/nginx/UFW.
