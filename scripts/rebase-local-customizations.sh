#!/usr/bin/env bash
set -euo pipefail

# Under `set -e` an unguarded command failure kills the script with no
# indication of where it died; log consumers (finalizer, cron delivery)
# keep only the output tail, so such deaths look causeless. Name the
# failing command and line in the tail. -E propagates the trap into
# functions and subshells.
set -E
trap 'echo "ERROR: rebase-local-customizations.sh: command failed (rc=$?) at line $LINENO: $BASH_COMMAND" >&2' ERR

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

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if [ -d "${PWD:-.}/.git" ] && [ -d "${PWD:-.}/agent" ] && [ -d "${PWD:-.}/gateway" ]; then
  REPO="${PWD}"
elif [ -d "$SCRIPT_DIR/../agent" ] && [ -d "$SCRIPT_DIR/../gateway" ]; then
  REPO="$(cd -- "$SCRIPT_DIR/.." && pwd)"
else
  REPO="$HOME/.hermes/hermes-agent"
fi

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

push_personal_branch() {
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
  if ! GIT_ASKPASS="$askpass_script" GIT_TERMINAL_PROMPT=0 GITHUB_TOKEN="$github_token" git -C "$REPO" push --force-with-lease "$PERSONAL_REMOTE" "$BRANCH" >/dev/null; then
    echo "Warning: push to $PERSONAL_REMOTE failed; continuing without remote sync." >&2
  fi
  rm -rf "$askpass_dir"
  return 0
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

abort_rebase_if_needed() {
  git -C "$REPO" rebase --abort >/dev/null 2>&1 || true
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
  REEXEC_SCRIPT="$REPO/scripts/rebase-local-customizations.sh"
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
  if ! (cd "$REPO" && git ls-files -z -- 'agent/*.py' 'gateway/*.py' 'hermes_cli/*.py' 'tools/*.py' 'cron/*.py' 'plugins/*.py' 'job_intel/*.py' 'run_agent.py' 'hermes_state.py' \
      | "$py" - <<'PYEOF'
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
PYEOF
  ); then
    echo "Post-rebase syntax check FAILED — see errors above. Not pushing, not restarting." >&2
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
# end-verify-helpers

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

if ! git -C "$REPO" remote get-url "$UPSTREAM_REMOTE" >/dev/null 2>&1; then
  echo "Remote not found: $UPSTREAM_REMOTE" >&2
  exit 1
fi

BEFORE_HEAD="$(git -C "$REPO" rev-parse --short HEAD)"
BASE_BEFORE="$(git -C "$REPO" rev-parse --short "$UPSTREAM_REF" 2>/dev/null || true)"

git -C "$REPO" fetch --prune "$UPSTREAM_FETCH_URL" "+refs/heads/$UPSTREAM_BRANCH:refs/remotes/$UPSTREAM_REMOTE/$UPSTREAM_BRANCH" >/dev/null

BASE_AFTER="$(git -C "$REPO" rev-parse --short "$UPSTREAM_REF")"
if [ "$BASE_BEFORE" = "$BASE_AFTER" ] && git -C "$REPO" merge-base --is-ancestor "$UPSTREAM_REF" HEAD; then
  push_personal_branch
  report_noop "$BEFORE_HEAD"
  exit 0
fi

# A merge commit in the local range means the branch carries a second
# lineage; a plain rebase would linearize BOTH parents and replay hundreds
# of stale commits with guaranteed conflicts (observed 2026-07-16: 1228
# replayed commits, conflict at #530). Refuse loudly instead.
MERGE_COUNT="$(git -C "$REPO" rev-list --merges --count "$UPSTREAM_REF..HEAD" 2>/dev/null || echo 0)"
if [ "${MERGE_COUNT:-0}" -gt 0 ]; then
  echo "FAILED: $MERGE_COUNT merge commit(s) in $UPSTREAM_REF..HEAD — plain rebase would replay both lineages and conflict." >&2
  echo "Linearize first: git commit-tree HEAD^{tree} -p <linear-parent> -m 'flatten merge', point $BRANCH at it, then re-run." >&2
  exit 1
fi

REBASE_LOG="$(mktemp)"
if ! git -C "$REPO" rebase "$UPSTREAM_REF" >"$REBASE_LOG" 2>&1; then
  abort_rebase_if_needed
  echo "Hermes local-branch update failed during rebase." >&2
  echo "Repo: $REPO" >&2
  echo "Branch: $BRANCH" >&2
  echo "Base: $UPSTREAM_REF" >&2
  echo "Before: $BEFORE_HEAD" >&2
  echo "Fetched base: $BASE_AFTER" >&2
  echo "Rebase output:" >&2
  cat "$REBASE_LOG" >&2
  rm -f "$REBASE_LOG"
  exit 1
fi
rm -f "$REBASE_LOG"

AFTER_HEAD="$(git -C "$REPO" rev-parse --short HEAD)"
if [ "$AFTER_HEAD" = "$BEFORE_HEAD" ]; then
  push_personal_branch
  report_noop "$BEFORE_HEAD"
  exit 0
fi

if ! verify_tree_compiles; then
  report_post_update "$BEFORE_HEAD" "$AFTER_HEAD" "no" "no"
  # Repeat the failure reason AFTER the report: downstream consumers
  # (upstream-sync finalizer, cron delivery) keep only the tail of the
  # output, so a reason printed before the report gets truncated away
  # and the failure looks causeless (2026-07-16 finalize incident).
  echo "FAILED: post-rebase syntax check failed — not syncing scripts, not pushing, not restarting (see SYNTAX ERROR lines above)." >&2
  exit 1
fi

SYNC_HELPER="$REPO/scripts/sync-runtime-scripts.sh"
if [ -x "$SYNC_HELPER" ]; then
  "$SYNC_HELPER" >/dev/null
else
  echo "Updated repo, but could not find runtime script sync helper: $SYNC_HELPER" >&2
  exit 1
fi

push_personal_branch

HERMES_BIN="$(resolve_hermes_bin || true)"
if [ -z "$HERMES_BIN" ]; then
  echo "Updated repo and pushed changes, but could not find hermes executable to restart gateway; skipping restart." >&2
  echo "Repo: $REPO" >&2
  echo "Branch: $BRANCH" >&2
  echo "Before: $BEFORE_HEAD" >&2
  echo "After: $AFTER_HEAD" >&2
  report_post_update "$BEFORE_HEAD" "$AFTER_HEAD" "no"
  exit 0
fi

RESTART_OUTPUT="$($HERMES_BIN gateway restart 2>&1)" || {
  echo "Updated repo and pushed changes, but gateway restart failed; continuing." >&2
  echo "Repo: $REPO" >&2
  echo "Branch: $BRANCH" >&2
  echo "Before: $BEFORE_HEAD" >&2
  echo "After: $AFTER_HEAD" >&2
  echo "Restart output:" >&2
  printf '%s\n' "$RESTART_OUTPUT" >&2
  report_post_update "$BEFORE_HEAD" "$AFTER_HEAD" "no"
  exit 0
}

report_post_update "$BEFORE_HEAD" "$AFTER_HEAD"
