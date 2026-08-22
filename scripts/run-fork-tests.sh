#!/usr/bin/env bash
# Прогон собственных тестов форка в указанном каталоге.
#
# Набор считается каждый раз, а не хранится списком: файлы tests/, которых нет
# в дереве upstream. Так он сам подхватывает новые наши тесты и сам забывает
# те, что апстрим удалил.
set -uo pipefail

WT="${1:?usage: run-fork-tests.sh <worktree>}"
UPSTREAM_REF="${HERMES_UPSTREAM_REMOTE:-upstream}/${HERMES_UPSTREAM_BRANCH:-main}"
# Интерпретатор берём из ГЛАВНОГО чекаута, а не из worktree: venv лежит в
# основном рабочем дереве и в worktree не копируется. Взять оттуда python3 без
# зависимостей проекта — значит получить два одинаково рассыпавшихся прогона,
# совпадающие множества падений и гейт, который пропускает что угодно.
MAIN_CHECKOUT="$(dirname "$(git -C "$WT" rev-parse --git-common-dir)")"
PYTHON_BIN="${HERMES_PYTHON:-$MAIN_CHECKOUT/venv/bin/python}"
[ -x "$PYTHON_BIN" ] || PYTHON_BIN="$WT/venv/bin/python"
[ -x "$PYTHON_BIN" ] || PYTHON_BIN="$(command -v python3)"

if ! git -C "$WT" rev-parse --verify "$UPSTREAM_REF^{commit}" >/dev/null 2>&1; then
  echo "FAILED: upstream boundary $UPSTREAM_REF is unavailable; refusing to run a fork gate against a stale or missing ref." >&2
  exit 2
fi

filter_tests() {
  grep -E '\.py$' | grep -v '/fixtures/' | grep -v '__init__\.py$' \
    | grep -v '/[.]_' | grep -v '^[.]_' || true
}

mapfile -t OURS < <(
  comm -23 \
    <(git -C "$WT" ls-tree -r --name-only HEAD tests/ | sort) \
    <(git -C "$WT" ls-tree -r --name-only "$UPSTREAM_REF" tests/ | sort) \
  | filter_tests
)

# A merge can change a test that already exists upstream. Include those files
# as well; otherwise the merge's own test change is outside the sensor set.
mapfile -t MERGE_CHANGED < <(
  if FIRST_PARENT=$(git -C "$WT" rev-parse --verify HEAD^1 2>/dev/null); then
    git -C "$WT" diff --name-only "$FIRST_PARENT" HEAD -- tests/ | filter_tests
  fi
)
mapfile -t TESTS < <(printf '%s\n' "${OURS[@]}" "${MERGE_CHANGED[@]}" | sed '/^$/d' | sort -u)
MAX_FILES="${HERMES_FORK_TEST_MAX_FILES:-800}"
if [ "${#TESTS[@]}" -gt "$MAX_FILES" ]; then
  echo "FAILED: fork test selection has ${#TESTS[@]} files, over limit $MAX_FILES; explicit operator decision required (no silent truncation)." >&2
  exit 2
fi
printf 'fork test selection: boundary=%s files=%s fork_only=%s merge_changed=%s\n' \
  "$UPSTREAM_REF" "${#TESTS[@]}" "${#OURS[@]}" "${#MERGE_CHANGED[@]}"

# Пустой набор — сбой вычисления, а не «проверять нечего»: у форка собственных
# тестов заведомо больше трёхсот файлов. Сообщать о чистом прогоне в этом
# случае значит пустить слияние в прод вслепую.
if [ "${#TESTS[@]}" -eq 0 ]; then
  echo "FAILED: computed an empty fork test set; refusing to report a clean run." >&2
  exit 2
fi

cd "$WT"
nice -n 19 "$PYTHON_BIN" -m pytest "${TESTS[@]}" \
  -q -p no:cacheprovider --timeout=90 -rf
