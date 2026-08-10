#!/usr/bin/env bash
# Снимает состояние сессии LinkedIn и дописывает его в журнал серии.
#
# Замер, существующий только в чате, теряется вместе с прогоном, а в этой
# серии потеря одной точки обесценивает остальные: вывод делается по форме
# кривой, а не по последнему значению. Поэтому запись в журнал — часть
# измерения, а не удобство.
#
# Использование: linkedin_session_probe.sh <label> [note]
set -euo pipefail

LABEL="${1:?нужна метка точки, например post-run-plus30m}"
NOTE="${2:-}"
NETNS="${LINKEDIN_NETNS:-ln-eg}"
PROFILE="${LINKEDIN_BROWSER_PROFILE:-/var/lib/browser-desktop/profiles/linkedin}"
# HOME здесь не используется намеренно: скрипту нужен root ради
# `ip netns exec`, а sudo подменяет HOME на /root. Этот проект уже ломался
# ровно так в rebase-скрипте — пути уезжали в несуществующий /root/.hermes.
# Venv находится от расположения самого скрипта, журнал — от HERMES_HOME с
# явным умолчанием.
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
HERMES_STATE="${HERMES_HOME:-/home/hermes/.hermes}"
LOG="${LINKEDIN_SERIES_LOG:-${HERMES_STATE}/diagnostics/linkedin-session-series.jsonl}"
PYTHON="${LINKEDIN_PROBE_PYTHON:-${SCRIPT_DIR}/../venv/bin/python}"
if [[ ! -x "${PYTHON}" ]]; then
  echo "Интерпретатор не найден: ${PYTHON}" >&2
  exit 1
fi

# `ip netns exec` обязателен: exit_ip измеряет выход процесса, который его
# запустил, а не браузера. Снаружи namespace он вернёт адрес хоста, и это
# прочитается как мёртвый туннель при живом.
RAW="$(ip netns exec "${NETNS}" "${PYTHON}" -m job_intel.linkedin_readout --profile "${PROFILE}" --json)"

mkdir -p "$(dirname "${LOG}")"
printf '%s' "${RAW}" | LABEL="${LABEL}" NOTE="${NOTE}" "${PYTHON}" -c '
import json, os, sys

report = json.load(sys.stdin)
li = [c for c in report["cookies"] if c["name"] == "li_at"]
record = {
    "label": os.environ["LABEL"],
    "at": report["checked_at"],
    "session_state": report["session_state"],
    "li_at_expires": li[0]["expires_at"] if li else None,
    "exit_ip": report["exit_ip"],
    "netns": report["netns"],
    "profile_dir": report["profile_dir"],
    "cookies": len(report["cookies"]),
}
note = os.environ.get("NOTE") or ""
if note:
    record["note"] = note
print(json.dumps(record, ensure_ascii=False))
' | tee -a "${LOG}"
