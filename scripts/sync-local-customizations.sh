#!/usr/bin/env bash
set -euo pipefail

# Under `set -e` an unguarded command failure kills the script with no
# indication of where it died; log consumers (finalizer, cron delivery)
# keep only the output tail, so such deaths look causeless. Name the
# failing command and line in the tail. -E propagates the trap into
# functions and subshells.
set -E
trap 'echo "ERROR: sync-local-customizations.sh: command failed (rc=$?) at line $LINENO: $BASH_COMMAND" >&2' ERR

# If there are root-owned files anywhere in the repo, elevate to root so the
# ownership-repair logic below can fix them and re-exec as the repo owner.
# Check FIRST to avoid an infinite loop: after root repairs ALL ownership and
# re-execs as hermes, the find returns nothing and we skip the sudo.
_default_repo="${HOME:-/home/hermes}/.hermes/hermes-agent"
if [ "$(id -u)" -ne 0 ] && \
   [ -n "$(find "$_default_repo" -maxdepth 6 -user root -print -quit 2>/dev/null)" ]; then
  # Pass HOME explicitly so root's REPO fallback resolves to the hermes home,
  # not /root — sudo resets HOME to /root by default.
  exec sudo -n env HOME="$HOME" "$0" "$@"
fi
unset _default_repo

# --post-update-only <before-head>: the branch was already moved by the caller
# (the finalizer fast-forwarding an operator-approved merge). Do only what
# follows a landed update — parse check, runtime script sync, push, gateway
# restart, report. No fetch, no merge: bringing in newer upstream commits is
# the next scheduled sync's job, and doing it here gated the push and the
# restart of an already-landed merge on an unrelated conflict set (2026-08-15).
POST_UPDATE_ONLY_FROM=""
if [ "${1:-}" = "--post-update-only" ]; then
  if [ -z "${2:-}" ]; then
    echo "FAILED: --post-update-only needs the pre-update HEAD as its argument." >&2
    exit 2
  fi
  POST_UPDATE_ONLY_FROM="$2"
  shift 2
fi

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if [ -d "${PWD:-.}/.git" ] && [ -d "${PWD:-.}/agent" ] && [ -d "${PWD:-.}/gateway" ]; then
  REPO="${PWD}"
elif [ -d "$SCRIPT_DIR/../agent" ] && [ -d "$SCRIPT_DIR/../gateway" ]; then
  REPO="$(cd -- "$SCRIPT_DIR/.." && pwd)"
else
  REPO="$HOME/.hermes/hermes-agent"
fi

source "$SCRIPT_DIR/lib/git-retry.sh"

BRANCH="${HERMES_LOCAL_BRANCH:-local/customizations}"
UPSTREAM_REMOTE="${HERMES_UPSTREAM_REMOTE:-origin}"
UPSTREAM_BRANCH="${HERMES_UPSTREAM_BRANCH:-main}"
UPSTREAM_REF="$UPSTREAM_REMOTE/$UPSTREAM_BRANCH"
PERSONAL_REMOTE="${HERMES_PERSONAL_REMOTE:-origin}"
PERSONAL_REMOTE_URL="${HERMES_PERSONAL_REMOTE_URL:-https://github.com/DenisVanyushkin/hermes-agent.git}"
UPSTREAM_FETCH_URL="${HERMES_UPSTREAM_FETCH_URL:-https://github.com/NousResearch/hermes-agent.git}"
HERMES_ENV_FILE="${HERMES_ENV_FILE:-$HOME/.hermes/.env}"

load_github_token() {
  if [ -n "${GITHUB_TOKEN:-}" ]; then
    printf '%s\n' "$GITHUB_TOKEN"
    return 0
  fi
  if [ -r "$HERMES_ENV_FILE" ]; then
    # shellcheck disable=SC1090
    set -a
    . "$HERMES_ENV_FILE"
    set +a
    if [ -n "${GITHUB_TOKEN:-}" ]; then
      printf '%s\n' "$GITHUB_TOKEN"
      return 0
    fi
  fi
  return 1
}

ensure_personal_remote_https() {
  current_url="$(git -C "$REPO" remote get-url "$PERSONAL_REMOTE" 2>/dev/null || true)"
  if [ -z "$current_url" ]; then
    echo "Personal remote not found: $PERSONAL_REMOTE" >&2
    exit 1
  fi
  if [ "$current_url" != "$PERSONAL_REMOTE_URL" ]; then
    git -C "$REPO" remote set-url "$PERSONAL_REMOTE" "$PERSONAL_REMOTE_URL" >/dev/null
  fi
}

_push_personal_branch_once() {
  ensure_personal_remote_https
  github_token="$(load_github_token || true)"
  if [ -z "$github_token" ]; then
    echo "Skipping push to $PERSONAL_REMOTE: no GitHub token available." >&2
    return 0
  fi

  askpass_dir="$(mktemp -d)"
  askpass_script="$askpass_dir/askpass.sh"
  cat >"$askpass_script" <<'EOF'
#!/usr/bin/env bash
case "$1" in
  *Username*) printf '%s\n' "x-access-token" ;;
  *Password*) printf '%s\n' "$GITHUB_TOKEN" ;;
  *) printf '\n' ;;
esac
EOF
  chmod 700 "$askpass_script"
  # Обычный push, без --force-with-lease: слияние не переписывает историю,
  # поэтому отказ означает, что другой хост что-то добавил, — это чинится
  # вливанием, а не форсом.
  if ! GIT_ASKPASS="$askpass_script" GIT_TERMINAL_PROMPT=0 GITHUB_TOKEN="$github_token" git -C "$REPO" push "$PERSONAL_REMOTE" "$BRANCH" >/dev/null; then
    rm -rf "$askpass_dir"
    return 1
  fi
  rm -rf "$askpass_dir"
  return 0
}

# The push is the last thing this script does, long after the merge landed.
# A rejected lease means only that the other host pushed inside that window.
# Swallowing it (pre-2026-07-16) let the two lineages diverge for days; dying
# on it (pre-2026-07-27) made the finalizer roll back a rebase that was
# already correct. Neither is right — the work is done, the lease is merely
# stale. Fold their commits in and push once more.
push_personal_branch() {
  if _push_personal_branch_once; then
    return 0
  fi
  echo "Push to $PERSONAL_REMOTE/$BRANCH rejected (lease stale) — another host pushed after our fetch; re-integrating and retrying once..." >&2
  if ! integrate_personal_remote; then
    echo "FAILED: could not fold in the other host's commits after the stale-lease rejection. Integrate manually, then re-run." >&2
    return 1
  fi
  # We are about to publish code we have not parsed since the rebase.
  if ! verify_tree_compiles; then
    echo "FAILED: the tree does not parse after folding in the other host's commits — not pushing (see SYNTAX ERROR lines above)." >&2
    return 1
  fi
  if ! _push_personal_branch_once; then
    echo "FAILED: push to $PERSONAL_REMOTE/$BRANCH still rejected after re-integration — the shared branch is moving faster than one run can follow. Resolve manually." >&2
    return 1
  fi
  echo "Push succeeded after folding in the other host's commits." >&2
  return 0
}

# Влить коммиты, которые другие хосты отправили в общую личную ветку с нашей
# последней синхронизации. Раньше здесь была эвристика с prev_tip и
# cherry-pick: она существовала только потому, что ребейз переписывал SHA и
# ветка переставала быть потомком общего типа, из-за чего скрипт 2026-07-27
# принял собственную дорефрешенную линию за 742 чужих коммита и уничтожил
# законченную работу. Слияние историю не переписывает, поэтому достаточно
# обычного merge. Токен не нужен — fetch только читает.
integrate_personal_remote() {
  if ! git -C "$REPO" fetch "$PERSONAL_REMOTE_URL" \
       "+refs/heads/$BRANCH:refs/remotes/$PERSONAL_REMOTE/$BRANCH" >/dev/null 2>&1; then
    echo "Warning: could not fetch $PERSONAL_REMOTE_URL; proceeding with local view only." >&2
    return 0
  fi

  local remote_tip
  remote_tip="$(git -C "$REPO" rev-parse "$PERSONAL_REMOTE/$BRANCH" 2>/dev/null || true)"
  [ -n "$remote_tip" ] || return 0
  if git -C "$REPO" merge-base --is-ancestor "$remote_tip" HEAD; then
    return 0
  fi

  echo "Shared branch on $PERSONAL_REMOTE has $(git -C "$REPO" rev-list --count "HEAD..$remote_tip") commit(s) from another host — merging before the upstream merge..." >&2
  local integrate_log
  integrate_log="$(mktemp)"
  if ! git -C "$REPO" -c rerere.enabled=false merge --no-edit "$remote_tip" >"$integrate_log" 2>&1; then
    abort_merge_if_needed
    echo "FAILED: could not merge $PERSONAL_REMOTE/$BRANCH ($remote_tip)." >&2
    echo "Another host's commits conflict with local ones; integrate manually (git merge $PERSONAL_REMOTE/$BRANCH)." >&2
    echo "Merge output:" >&2
    cat "$integrate_log" >&2
    rm -f "$integrate_log"
    return 1
  fi
  rm -f "$integrate_log"
}

resolve_hermes_bin() {
  can_run_hermes() {
    [ -n "$1" ] && [ -x "$1" ] || return 1
    "$1" --version >/dev/null 2>&1
  }

  for candidate in "${HERMES_BIN:-}" "$REPO/venv/bin/hermes" "$HOME/.local/bin/hermes" "$(command -v hermes 2>/dev/null || true)"; do
    [ -n "$candidate" ] || continue
    if can_run_hermes "$candidate"; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  return 1
}

abort_merge_if_needed() {
  git -C "$REPO" merge --abort >/dev/null 2>&1 || true
}

report_noop() {
  cat <<EOF
Hermes local-branch update: no upstream changes.
Repo: $REPO
Branch: $BRANCH
Base: $UPSTREAM_REF
Current: $1
Gateway restarted: no
EOF
}

report_post_update() {
  local before="$1"
  local after="$2"
  local restart_status="${3:-yes}"
  local synced_status="${4:-yes}"
  local changed_commits changed_files clean_status

  changed_commits="$(git -C "$REPO" log --no-merges --format='%h %s' "$before..$after" 2>/dev/null || true)"
  changed_files="$(git -C "$REPO" diff --name-only "$before..$after" 2>/dev/null || true)"
  clean_status="$(git -C "$REPO" status --porcelain --untracked-files=all 2>/dev/null || true)"

  cat <<EOF
Hermes local-branch update completed.
Repo: $REPO
Branch: $BRANCH
Base: $UPSTREAM_REF
Before: $before
After: $after

### Updated commits
EOF
  if [ -n "$changed_commits" ]; then
    printf '%s\n' "$changed_commits" | sed 's/^/- /'
  else
    echo "- (no new commits; update was a no-op)"
  fi

  cat <<EOF

### Updated files
EOF
  if [ -n "$changed_files" ]; then
    printf '%s\n' "$changed_files" | sed 's/^/- /'
  else
    echo "- (no file-path changes detected)"
  fi

  cat <<EOF

### Verification
- repo branch: $(git -C "$REPO" branch --show-current)
- git status clean: $( [ -z "$clean_status" ] && echo yes || echo no )
- runtime scripts synced: $synced_status
- gateway restarted: $restart_status
EOF
}

if [ ! -d "$REPO/.git" ]; then
  echo "Repo not found or not a git checkout: $REPO" >&2
  exit 1
fi

REPO_UID="$(stat -c '%u' "$REPO")"
REPO_GID="$(stat -c '%g' "$REPO")"
CURRENT_UID="$(id -u)"
if [ "$CURRENT_UID" -eq 0 ] && [ "$REPO_UID" != "0" ]; then
  echo "Running as root against non-root-owned repo; repairing ownership and re-running as repo owner..." >&2
  find "$REPO/.git" \( -user root -o -group root \) -exec chown -h "$REPO_UID:$REPO_GID" {} +
  find "$REPO" -path "$REPO/.git" -prune -o \( -user root -o -group root \) -exec chown -h "$REPO_UID:$REPO_GID" {} +
  REPO_USER="$(getent passwd "$REPO_UID" | cut -d: -f1 || true)"
  if [ -z "$REPO_USER" ]; then
    echo "Could not resolve repo owner UID $REPO_UID to a user; refusing to run git as root." >&2
    exit 1
  fi
  REEXEC_SCRIPT="$REPO/scripts/sync-local-customizations.sh"
  if [ ! -r "$REEXEC_SCRIPT" ]; then
    echo "Repo-owned updater is not readable: $REEXEC_SCRIPT" >&2
    exit 1
  fi
  printf -v REEXEC_CMD 'cd %q && exec bash %q' "$REPO" "$REEXEC_SCRIPT"
  exec su -s /bin/bash "$REPO_USER" -c "$REEXEC_CMD"
fi

if [ "$CURRENT_UID" -ne 0 ]; then
  ROOT_OWNED_SAMPLE="$(find "$REPO/.git" \( -user root -o -group root \) -print -quit 2>/dev/null || true)"
  if [ -n "$ROOT_OWNED_SAMPLE" ]; then
    echo "Warning: repo contains root-owned git files; continuing for now: $ROOT_OWNED_SAMPLE" >&2
    echo "If git later fails, repair ownership before retrying." >&2
  fi
fi

git config --global --add safe.directory "$REPO" >/dev/null 2>&1 || true

if [ -d "$REPO/.git/rebase-merge" ] || [ -d "$REPO/.git/rebase-apply" ]; then
  echo "Repo is already mid-rebase; resolve it before running update." >&2
  exit 1
fi

AUTOSTASH_CREATED=0
AUTOSTASH_RESTORE_FAILED=0
cleanup_autostash() {
  local status="$1"
  if [ "$AUTOSTASH_CREATED" -eq 1 ]; then
    if git -C "$REPO" stash pop --index >/dev/null 2>&1; then
      AUTOSTASH_CREATED=0
    else
      # stash pop failed — check if it's just merge conflicts in auto-generated
      # files (e.g. package-lock.json updated by both upstream and the stash).
      # Resolve by taking HEAD for every conflicting file, then drop the stash.
      local conflicts
      conflicts="$(git -C "$REPO" diff --name-only --diff-filter=U 2>/dev/null || true)"
      if [ -n "$conflicts" ]; then
        echo "Warning: autostash conflicts in the following files — taking rebased (HEAD) versions:" >&2
        printf '  %s\n' $conflicts >&2
        # shellcheck disable=SC2086
        git -C "$REPO" checkout HEAD -- $conflicts 2>/dev/null || true
        git -C "$REPO" stash drop >/dev/null 2>&1 || true
        AUTOSTASH_CREATED=0
      else
        AUTOSTASH_RESTORE_FAILED=1
        echo "Warning: autostash could not be restored cleanly; it remains in git stash." >&2
      fi
    fi
  fi
  if [ "$status" -eq 0 ] && [ "$AUTOSTASH_RESTORE_FAILED" -eq 1 ]; then
    exit 1
  fi
  if [ "$status" -eq 0 ]; then
    local popped_files
    popped_files="$(git -C "$REPO" status --porcelain 2>/dev/null | awk '{print $2}')"
    if ! verify_no_duplicate_defs "$popped_files"; then
      echo "Stash pop left broken python files (see above); fix them before the next run." >&2
      exit 1
    fi
  fi
}

resolve_repo_python() {
  if [ -x "$REPO/venv/bin/python" ]; then
    printf '%s\n' "$REPO/venv/bin/python"
  else
    command -v python3 || true
  fi
}

# Whole-tree syntax check. Auto-resolved merges can leave byte-valid but
# unparsable files; catch that before we push and restart the gateway.
# AST-based (no .pyc writes) so root-owned __pycache__ dirs left behind by
# sandbox containers cannot fail the check spuriously.
verify_tree_compiles() {
  local py
  py="$(resolve_repo_python)"
  [ -n "$py" ] || return 0
  # NB: the program must be passed via -c, NOT a stdin heredoc: `... | python - <<EOF`
  # points python's stdin at the heredoc, so the piped file list was never read
  # (the check was vacuous) and once `git ls-files` output outgrew the 64K pipe
  # buffer the unread pipe raised SIGPIPE and failed the check with no output
  # at all (root cause of the 2026-07-16 causeless finalize failure).
  if ! (cd "$REPO" && git ls-files -z -- 'agent/*.py' 'gateway/*.py' 'hermes_cli/*.py' 'tools/*.py' 'cron/*.py' 'plugins/*.py' 'job_intel/*.py' 'run_agent.py' 'hermes_state.py' \
      | "$py" -c '
import ast, sys
failed = 0
for path in sys.stdin.buffer.read().split(b"\0"):
    if not path:
        continue
    name = path.decode()
    try:
        with open(name, "rb") as fh:
            ast.parse(fh.read(), filename=name)
    except SyntaxError as e:
        print(f"SYNTAX ERROR: {name}: {e}", file=sys.stderr)
        failed = 1
    except OSError:
        pass
sys.exit(failed)
'
  ); then
    echo "Post-merge syntax check FAILED — see errors above. Not pushing, not restarting." >&2
    return 1
  fi
}

# Duplicate-definition scan for files touched by a stash pop. The
# take-HEAD conflict strategy can silently leave the same function twice
# in one file when hunks land apart — syntactically valid, broken at
# runtime. Scans only the given files, so it is cheap.
verify_no_duplicate_defs() {
  local py files="$1"
  [ -n "$files" ] || return 0
  py="$(resolve_repo_python)"
  [ -n "$py" ] || return 0
  local failed=0 f
  while IFS= read -r f; do
    case "$f" in *.py) ;; *) continue;; esac
    [ -f "$REPO/$f" ] || continue
    if ! "$py" - "$REPO/$f" <<'PYEOF'
import ast, sys
path = sys.argv[1]
try:
    tree = ast.parse(open(path).read())
except SyntaxError as e:
    print(f"SYNTAX ERROR after stash pop: {path}: {e}")
    sys.exit(1)
seen = {}
for node in tree.body:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        if node.name in seen:
            print(f"DUPLICATE top-level definition after stash pop: {path}: "
                  f"{node.name} at lines {seen[node.name]} and {node.lineno}")
            sys.exit(1)
        seen[node.name] = node.lineno
PYEOF
    then
      failed=1
    fi
  done <<EOF_FILES
$files
EOF_FILES
  return "$failed"
}

# Everything that follows a landed update. Shared by the normal path (after the
# upstream merge) and by --post-update-only (after a merge the finalizer
# fast-forwarded itself).
finish_update() {
  local before="$1" after
  after="$(git -C "$REPO" rev-parse --short HEAD)"
  if [ "$after" = "$before" ]; then
    push_personal_branch
    report_noop "$before"
    return 0
  fi

  if ! verify_tree_compiles; then
    report_post_update "$before" "$after" "no" "no"
    # Repeat the failure reason AFTER the report: downstream consumers
    # (upstream-sync finalizer, cron delivery) keep only the tail of the
    # output, so a reason printed before the report gets truncated away and
    # the failure looks causeless (2026-07-16 finalize incident).
    echo "FAILED: post-merge syntax check failed — not syncing scripts, not pushing, not restarting (see SYNTAX ERROR lines above)." >&2
    return 1
  fi

  local sync_helper="$REPO/scripts/sync-runtime-scripts.sh"
  if [ -x "$sync_helper" ]; then
    "$sync_helper" >/dev/null
  else
    echo "Updated repo, but could not find runtime script sync helper: $sync_helper" >&2
    return 1
  fi

  push_personal_branch

  local hermes_bin restart_output
  hermes_bin="$(resolve_hermes_bin || true)"
  if [ -z "$hermes_bin" ]; then
    echo "Updated repo and pushed changes, but could not find hermes executable to restart gateway; skipping restart." >&2
    echo "Repo: $REPO" >&2
    echo "Branch: $BRANCH" >&2
    echo "Before: $before" >&2
    echo "After: $after" >&2
    report_post_update "$before" "$after" "no"
    return 0
  fi

  restart_output="$($hermes_bin gateway restart 2>&1)" || {
    echo "Updated repo and pushed changes, but gateway restart failed; continuing." >&2
    echo "Repo: $REPO" >&2
    echo "Branch: $BRANCH" >&2
    echo "Before: $before" >&2
    echo "After: $after" >&2
    echo "Restart output:" >&2
    printf '%s\n' "$restart_output" >&2
    report_post_update "$before" "$after" "no"
    return 0
  }

  report_post_update "$before" "$after"
}

# end-verify-helpers

# Serialize all automated git writers on a repo-level lock: git's own
# index.lock is fail-fast, so a concurrent commit (agent session, operator
# shell) and this script kill each other mid-rebase (2026-07-20 incident).
# Acquired here — after the sudo/su re-exec dance, which would not preserve
# a flock fd across process replacement.
REPO_LOCK_FILE="${HERMES_REPO_LOCK:-$REPO/.git/hermes-repo.lock}"
exec 8>"$REPO_LOCK_FILE"
if ! flock -w "${HERMES_REPO_LOCK_TIMEOUT:-600}" 8; then
  echo "FAILED: could not acquire repo lock $REPO_LOCK_FILE within ${HERMES_REPO_LOCK_TIMEOUT:-600}s — another git writer is active." >&2
  exit 1
fi

STATUS_BEFORE="$(git -C "$REPO" status --porcelain --untracked-files=all)"
if [ -n "$STATUS_BEFORE" ]; then
  echo "Local changes detected — stashing before update..." >&2
  git -C "$REPO" stash push --include-untracked -m "hermes-local-customizations-autostash-$(date +%Y%m%d-%H%M%S)" >/dev/null
  AUTOSTASH_CREATED=1
  trap 'cleanup_autostash "$?"' EXIT
fi

CURRENT_BRANCH="$(git -C "$REPO" branch --show-current)"
if [ "$CURRENT_BRANCH" != "$BRANCH" ]; then
  git -C "$REPO" checkout "$BRANCH" >/dev/null
fi

if ! git -C "$REPO" rev-parse --verify "$BRANCH" >/dev/null 2>&1; then
  echo "Local branch not found: $BRANCH" >&2
  exit 1
fi

if [ -n "$POST_UPDATE_ONLY_FROM" ]; then
  if ! BEFORE_HEAD="$(git -C "$REPO" rev-parse --short "$POST_UPDATE_ONLY_FROM" 2>/dev/null)"; then
    echo "FAILED: --post-update-only was given an unknown commit: $POST_UPDATE_ONLY_FROM" >&2
    exit 2
  fi
  finish_update "$BEFORE_HEAD" || exit 1
  exit 0
fi

if ! git -C "$REPO" remote get-url "$UPSTREAM_REMOTE" >/dev/null 2>&1; then
  echo "Remote not found: $UPSTREAM_REMOTE" >&2
  exit 1
fi

BEFORE_HEAD="$(git -C "$REPO" rev-parse --short HEAD)"

integrate_personal_remote

BASE_BEFORE="$(git -C "$REPO" rev-parse --short "$UPSTREAM_REF" 2>/dev/null || true)"

git_fetch_retry "$REPO" "$UPSTREAM_FETCH_URL" "+refs/heads/$UPSTREAM_BRANCH:refs/remotes/$UPSTREAM_REMOTE/$UPSTREAM_BRANCH"

BASE_AFTER="$(git -C "$REPO" rev-parse --short "$UPSTREAM_REF")"
# Полный неизменяемый SHA разрешается ОДИН раз сразу после fetch и дальше
# используется везде: merge-tree, сам merge и граница обоих прогонов. Если
# граница будет полным SHA, а слияние останется по ref, остаётся гонка —
# проверили одного кандидата, слили другого. BASE_AFTER короткий и годится
# только для сообщения, идентичностью он быть не может.
UPSTREAM_FULL="$(git -C "$REPO" rev-parse --verify "$UPSTREAM_REF^{commit}")"
if [ "$BASE_BEFORE" = "$BASE_AFTER" ] && git -C "$REPO" merge-base --is-ancestor "$UPSTREAM_FULL" HEAD; then
  push_personal_branch
  report_noop "$BEFORE_HEAD"
  exit 0
fi

# Решение «сливать или звать оператора» принимается детерминированно:
# merge-tree считает результат слияния, ничего не меняя в репозитории. Пока он
# не сказал «чисто», рабочее дерево не трогается вовсе.
# Гейт лежит рядом со скриптом: они одной поставки, и при запуске из
# рантайм-копии ~/.hermes/scripts оба берутся оттуда же, а не из репозитория.
GATE="$SCRIPT_DIR/upstream_sync_gate.py"
[ -f "$GATE" ] || GATE="$REPO/scripts/upstream_sync_gate.py"
PYTHON_BIN="${HERMES_PYTHON:-$REPO/venv/bin/python}"
[ -x "$PYTHON_BIN" ] || PYTHON_BIN="$(command -v python3)"

MERGE_TREE_OUT="$(mktemp)"
git -C "$REPO" merge-tree --write-tree --name-only HEAD "$UPSTREAM_FULL" >"$MERGE_TREE_OUT" 2>&1 || true

# set +e, а не `|| true`: подстановка с `|| true` возвращает код самого
# присваивания, то есть всегда 0, и код 2 (не смогли разобрать вывод)
# незаметно превратился бы в «конфликтов нет».
set +e
CONFLICT_PATHS="$("$PYTHON_BIN" "$GATE" merge-tree --output "$MERGE_TREE_OUT")"
GATE_RC=$?
set -e
if [ "$GATE_RC" -eq 2 ]; then
  echo "FAILED: could not read the merge-tree result; refusing to merge blind." >&2
  cat "$MERGE_TREE_OUT" >&2
  rm -f "$MERGE_TREE_OUT"
  exit 1
fi

if [ -n "$CONFLICT_PATHS" ]; then
  CONFLICT_TREE="$(head -n1 "$MERGE_TREE_OUT")"
  echo "Hermes local-branch update: conflicts — operator decision required."
  echo "Repo: $REPO"
  echo "Branch: $BRANCH"
  echo "Base: $UPSTREAM_REF ($BASE_AFTER)"
  echo "Conflicting files: $(printf '%s\n' "$CONFLICT_PATHS" | wc -l)"
  while IFS= read -r path; do
    [ -n "$path" ] || continue
    hunks="$(git -C "$REPO" show "$CONFLICT_TREE:$path" 2>/dev/null | grep -c '^<<<<<<<' || true)"
    echo "  $path (${hunks:-?} hunk(s))"
  done <<<"$CONFLICT_PATHS"
  echo "Nothing was changed. Resolve through the upstream-sync skill."
  rm -f "$MERGE_TREE_OUT"
  exit 0
fi
rm -f "$MERGE_TREE_OUT"

# Слияние сначала доказывается во временном worktree и только потом
# приземляется. Живая ветка не должна ни на секунду оказаться в состоянии,
# которое мы ещё не проверили: гейтвей работает на этом же дереве.
SYNC_WT="$(mktemp -d -t hermes-upstream-sync-XXXXXX)"
SELECTION_STATE_DIR="$(mktemp -d -t hermes-upstream-selection-XXXXXX)"
SELECTION_MANIFEST=""
SELECTION_ATTEMPT_ROOT="$SELECTION_STATE_DIR/attempts"
cleanup_sync_worktree() {
  git -C "$REPO" worktree remove --force "$SYNC_WT" >/dev/null 2>&1 || true
  rm -rf "$SYNC_WT"
  rm -rf "$SELECTION_STATE_DIR"
}
trap cleanup_sync_worktree EXIT

git -C "$REPO" worktree add --detach "$SYNC_WT" HEAD >/dev/null 2>&1

TEST_CMD="${HERMES_SYNC_TEST_CMD:-$SCRIPT_DIR/run-fork-tests.sh}"
[ -x "$TEST_CMD" ] || TEST_CMD="$REPO/scripts/run-fork-tests.sh"
BASELINE_LOG_FILE="$(mktemp)"
POST_LOG_FILE="$(mktemp)"
BEFORE_FULL="$(git -C "$REPO" rev-parse HEAD)"

MERGE_LOG="$(mktemp)"
# rerere is OFF for this merge on purpose. It is enabled in this repo's config
# and .git/rr-cache holds resolutions recorded while the sync was a rebase,
# where "ours"/"theirs" are inverted relative to a merge — replaying them here
# resolves conflicts backwards, and silently. The worktree shares .git with the
# live repo, so it inherits both the setting and the recordings.
if ! git -C "$SYNC_WT" -c rerere.enabled=false merge --no-edit "$UPSTREAM_FULL" >"$MERGE_LOG" 2>&1; then
  # Не всякая неудача merge — расхождение с merge-tree. Отсутствующая
  # git-identity, нехватка места, битый индекс дают тот же ненулевой код, и
  # обвинять в них merge-tree значит отправить расследование не туда.
  # Разделяет их наличие незакрытых путей в индексе.
  if [ -n "$(git -C "$SYNC_WT" ls-files -u)" ]; then
    echo "FAILED: merge-tree reported a clean merge but git merge conflicted — this is a defect." >&2
  else
    echo "FAILED: git merge could not run at all (not a conflict) — see the output below." >&2
  fi
  echo "Repo: $REPO" >&2
  echo "Base: $UPSTREAM_REF ($BASE_AFTER)" >&2
  cat "$MERGE_LOG" >&2
  rm -f "$MERGE_LOG" "$BASELINE_LOG_FILE" "$POST_LOG_FILE"
  exit 1
fi
rm -f "$MERGE_LOG"

# The manifest is built only after the candidate merge exists. It is the one
# persisted selection for both trees: changing the checkout changes only which
# exists_pre/exists_post side the runner consumes, never the selected universe.
SELECTION_BEFORE_PATHS="$(mktemp)"
SELECTION_AFTER_PATHS="$(mktemp)"
SELECTION_BOUNDARY_PATHS="$(mktemp)"
SELECTION_CHANGED_PATHS="$(mktemp)"
SELECTION_REPORT_FILE="$(mktemp)"
git -C "$REPO" ls-tree -r -z --name-only "$BEFORE_FULL" -- tests/ >"$SELECTION_BEFORE_PATHS"
MERGED_HEAD="$(git -C "$SYNC_WT" rev-parse HEAD)"
git -C "$REPO" ls-tree -r -z --name-only "$MERGED_HEAD" -- tests/ >"$SELECTION_AFTER_PATHS"
git -C "$REPO" ls-tree -r -z --name-only "$UPSTREAM_FULL" -- tests/ >"$SELECTION_BOUNDARY_PATHS"
git -C "$REPO" diff --no-renames --name-only -z "$BEFORE_FULL" "$MERGED_HEAD" -- tests/ >"$SELECTION_CHANGED_PATHS"
if ! "$PYTHON_BIN" "$GATE" prepare-selection \
  --state-dir "$SELECTION_STATE_DIR" \
  --before "$BEFORE_FULL" --after "$MERGED_HEAD" --boundary "$UPSTREAM_FULL" \
  --before-paths "$SELECTION_BEFORE_PATHS" --after-paths "$SELECTION_AFTER_PATHS" \
  --boundary-paths "$SELECTION_BOUNDARY_PATHS" --changed-paths "$SELECTION_CHANGED_PATHS" \
  >"$SELECTION_REPORT_FILE"; then
  echo "FAILED: could not build the bound test-selection manifest." >&2
  exit 1
fi
SELECTION_MANIFEST="$("$PYTHON_BIN" - "$SELECTION_REPORT_FILE" <<'PY'
import json, sys
from pathlib import Path
report = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
attempt_dir = report.get("attempt_dir")
if not isinstance(attempt_dir, str) or not attempt_dir:
    raise SystemExit("prepare-selection returned no attempt_dir")
print(Path(attempt_dir) / "gate-selection.json")
PY
)"
rm -f "$SELECTION_BEFORE_PATHS" "$SELECTION_AFTER_PATHS" "$SELECTION_BOUNDARY_PATHS" \
  "$SELECTION_CHANGED_PATHS" "$SELECTION_REPORT_FILE"

# Nonnzero test-run codes are expected: failures are the measured outcome.
git -C "$SYNC_WT" checkout --detach "$BEFORE_FULL" >/dev/null 2>&1
if ! "$TEST_CMD" --selection-from "$SELECTION_MANIFEST" \
  --attempt-root "$SELECTION_ATTEMPT_ROOT" --boundary "$UPSTREAM_FULL" "$SYNC_WT" \
  >"$BASELINE_LOG_FILE" 2>&1; then
  :
fi
git -C "$SYNC_WT" checkout --detach "$MERGED_HEAD" >/dev/null 2>&1
if ! "$TEST_CMD" --selection-from "$SELECTION_MANIFEST" \
  --attempt-root "$SELECTION_ATTEMPT_ROOT" --boundary "$UPSTREAM_FULL" "$SYNC_WT" \
  >"$POST_LOG_FILE" 2>&1; then
  :
fi

# Тот же приём, что и в гейте merge-tree: код 2 означает «сравнить не смогли»
# и обязан отличаться от «новых падений нет».
set +e
NEW_FAILURES="$("$PYTHON_BIN" "$GATE" new-failures \
  --baseline "$BASELINE_LOG_FILE" --post "$POST_LOG_FILE")"
NF_RC=$?
set -e
if [ "$NF_RC" -eq 2 ]; then
  echo "FAILED: could not compare test runs; refusing to land the merge." >&2
  echo "Baseline log tail:" >&2
  tail -n 5 "$BASELINE_LOG_FILE" >&2
  echo "Post-merge log tail:" >&2
  tail -n 5 "$POST_LOG_FILE" >&2
  rm -f "$BASELINE_LOG_FILE" "$POST_LOG_FILE"
  exit 1
fi

if [ -n "$NEW_FAILURES" ]; then
  echo "Hermes local-branch update: the merge introduces test failures — not landed."
  echo "Repo: $REPO"
  echo "Branch: $BRANCH"
  echo "Base: $UPSTREAM_REF ($BASE_AFTER)"
  echo "New failures:"
  printf '%s\n' "$NEW_FAILURES" | sed 's/^/  /'
  echo "Nothing was changed."
  rm -f "$BASELINE_LOG_FILE" "$POST_LOG_FILE"
  exit 0
fi
rm -f "$BASELINE_LOG_FILE" "$POST_LOG_FILE"

git -C "$REPO" branch "backup/pre-upstream-sync-$(date +%Y%m%d-%H%M%S)" HEAD >/dev/null 2>&1 || true

if ! git -C "$REPO" merge --ff-only "$MERGED_HEAD" >/dev/null 2>&1; then
  echo "FAILED: the branch moved while the merge was being verified; re-run the sync." >&2
  exit 1
fi

finish_update "$BEFORE_HEAD" || exit 1
