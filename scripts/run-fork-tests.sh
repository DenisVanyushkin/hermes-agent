#!/usr/bin/env bash
# Прогон собственных тестов форка в указанном каталоге.
#
# Набор считается каждый раз, а не хранится списком: файлы tests/, которых нет
# в дереве upstream. Так он сам подхватывает новые наши тесты и сам забывает
# те, что апстрим удалил.
set -uo pipefail

WT="${1:?usage: run-fork-tests.sh <worktree>}"
UPSTREAM_REF="${HERMES_UPSTREAM_REMOTE:-origin}/${HERMES_UPSTREAM_BRANCH:-main}"
PYTHON_BIN="${HERMES_PYTHON:-$WT/venv/bin/python}"
[ -x "$PYTHON_BIN" ] || PYTHON_BIN="$(command -v python3)"

mapfile -t OURS < <(
  comm -23 \
    <(git -C "$WT" ls-tree -r --name-only HEAD tests/ | sort) \
    <(git -C "$WT" ls-tree -r --name-only "$UPSTREAM_REF" tests/ | sort) \
  | grep -E '\.py$' | grep -v '/fixtures/' | grep -v '__init__\.py$' \
  | grep -v '/[.]_' | grep -v '^[.]_'
)

# Пустой набор — сбой вычисления, а не «проверять нечего»: у форка собственных
# тестов заведомо больше трёхсот файлов. Сообщать о чистом прогоне в этом
# случае значит пустить слияние в прод вслепую.
if [ "${#OURS[@]}" -eq 0 ]; then
  echo "FAILED: computed an empty fork test set; refusing to report a clean run." >&2
  exit 2
fi

cd "$WT"
nice -n 19 "$PYTHON_BIN" -m pytest "${OURS[@]}" \
  -q -p no:cacheprovider --timeout=90 -rf
