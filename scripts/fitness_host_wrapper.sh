#!/usr/bin/env bash
# scripts/fitness_host_wrapper.sh — точка входа для script-mode cron.
#
# script-mode cron игнорирует поле workdir и запускает скрипт с cwd каталога
# скрипта (~/.hermes/scripts), то есть вне репозитория. Репозиторий ищем сами.
#
# Проверяем МАРКЕРНЫЙ ФАЙЛ fitness/__main__.py, а не каталог: каталог с именем
# пакета рядом с ~/.hermes затеняет настоящий пакет (PEP 420).
set -euo pipefail

HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
export HERMES_HOME

REPO=""
for c in "$HERMES_HOME/hermes-agent" "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"; do
  if [[ -f "$c/fitness/__main__.py" ]]; then
    REPO="$c"
    break
  fi
done

if [[ -z "$REPO" ]]; then
  echo "fitness: не найден репозиторий с fitness/__main__.py" >&2
  exit 1
fi

cd "$REPO"
exec "$REPO/venv/bin/python" -m fitness "$@"
