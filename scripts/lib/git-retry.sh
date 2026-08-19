#!/usr/bin/env bash
# Retry wrapper for the sync path's network fetches.
#
# GitHub applies a secondary, IP-scoped rate limit to git-upload-pack on this
# host: a large share of ref advertisements return HTTP 429 while
# api.github.com/rate_limit still reports a full quota, and presenting
# GITHUB_TOKEN changes nothing -- the limit is on the address, not the account.
# The failure is therefore a coin flip on every run, and an unguarded fetch
# converts it into a lost cycle: the preflight exits 128 and the next scheduled
# attempt is three days out.
#
# Only transient failures are retried. A missing repository or a rejected
# credential is reported immediately, because repeating it just buries the real
# cause under identical attempts.

HERMES_GIT_FETCH_RETRIES="${HERMES_GIT_FETCH_RETRIES:-6}"
HERMES_GIT_FETCH_RETRY_DELAY="${HERMES_GIT_FETCH_RETRY_DELAY:-30}"

# Transient: rate limits, proxy/gateway errors, truncated transfers, DNS and
# connection trouble. Anything else is treated as permanent.
_hermes_fetch_is_transient() {
  printf '%s' "$1" | grep -Eqi \
    'HTTP (429|500|502|503|504)|RPC failed|expected flush after ref listing|early EOF|unexpected disconnect|Could not resolve host|Connection (timed out|reset|refused)|Operation timed out|TLS|SSL_ERROR|The remote end hung up'
}

# git_fetch_retry <repo> <url> <refspec> [extra git-fetch args...]
git_fetch_retry() {
  local repo="$1" url="$2" refspec="$3"
  shift 3
  local attempt=1 err rc
  while :; do
    err="$(git -C "$repo" fetch --prune "$@" "$url" "$refspec" 2>&1 >/dev/null)"
    rc=$?
    if [ "$rc" -eq 0 ]; then
      [ "$attempt" -gt 1 ] && echo "git fetch: succeeded on attempt $attempt/$HERMES_GIT_FETCH_RETRIES" >&2
      return 0
    fi
    if ! _hermes_fetch_is_transient "$err"; then
      echo "git fetch: permanent failure, not retrying: $err" >&2
      return "$rc"
    fi
    if [ "$attempt" -ge "$HERMES_GIT_FETCH_RETRIES" ]; then
      echo "git fetch: giving up after $attempt attempt(s): $err" >&2
      return "$rc"
    fi
    echo "git fetch: transient failure on attempt $attempt/$HERMES_GIT_FETCH_RETRIES, retrying in $((HERMES_GIT_FETCH_RETRY_DELAY * attempt))s: $err" >&2
    sleep "$((HERMES_GIT_FETCH_RETRY_DELAY * attempt))"
    attempt=$((attempt + 1))
  done
}
