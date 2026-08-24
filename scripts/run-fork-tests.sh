#!/usr/bin/env bash
# Прогон собственных тестов форка в указанном каталоге.
#
# Набор считается каждый раз, а не хранится списком: файлы tests/, которых нет
# в дереве upstream. Так он сам подхватывает новые наши тесты и сам забывает
# те, что апстрим удалил.
set -uo pipefail

# Опции читаются до первого позиционного или до `--`, дальше всё оставшееся —
# позиционные, и их должно быть ровно столько, сколько мы умеем принять. Лишний
# argv отвергаем, а не проглатываем: набор тестов решает, поедет ли обновление
# в прод, и «взяли последний путь, остальные молча выбросили» — не то поведение,
# которое стоит иметь в такой позиции.
PRINT_SELECTION=0
BOUNDARY="${HERMES_UPSTREAM_BOUNDARY:-}"
while [ "$#" -gt 0 ]; do
  case "$1" in
    --print-selection) PRINT_SELECTION=1; shift ;;
    --boundary)
      shift
      [ "$#" -gt 0 ] || { echo "FAILED: --boundary needs a value" >&2; exit 2; }
      BOUNDARY="$1"; shift ;;
    --boundary=*) BOUNDARY="${1#--boundary=}"; shift ;;
    --) shift; break ;;
    -*) echo "FAILED: unknown option $1" >&2; exit 2 ;;
    *) break ;;
  esac
done
if [ "$#" -ne 1 ]; then
  echo "FAILED: expected exactly one worktree path, got $#${*:+ ($*)}" >&2
  echo "usage: run-fork-tests.sh [--print-selection] --boundary <ref|sha> [--] <worktree>" >&2
  exit 2
fi
WT="$1"

# Граница задаётся вызывающим и только им. Прежде здесь стояло умолчание
# upstream/main, и это молчаливо подставляло remote-tracking ref, который может
# сколько угодно отставать от коммита, который мы на самом деле сливаем: в бою
# он отстал на 752 коммита, и около 105 апстримовых тестовых файлов пошли в
# набор как «свои». Раннер не знает и не должен знать, мерж перед ним или нет —
# он исполнитель. Отказ принадлежит тому, кто не может предъявить границу.
if [ -z "$BOUNDARY" ]; then
  echo "FAILED: no upstream boundary given; pass --boundary <ref|sha> (or HERMES_UPSTREAM_BOUNDARY). Refusing to guess it from a remote-tracking ref that may lag the commit under test." >&2
  exit 2
fi
# Интерпретатор берём из ГЛАВНОГО чекаута, а не из worktree: venv лежит в
# основном рабочем дереве и в worktree не копируется. Взять оттуда python3 без
# зависимостей проекта — значит получить два одинаково рассыпавшихся прогона,
# совпадающие множества падений и гейт, который пропускает что угодно.
MAIN_CHECKOUT="$(dirname "$(git -C "$WT" rev-parse --git-common-dir)")"
PYTHON_BIN="${HERMES_PYTHON:-$MAIN_CHECKOUT/venv/bin/python}"
[ -x "$PYTHON_BIN" ] || PYTHON_BIN="$WT/venv/bin/python"
[ -x "$PYTHON_BIN" ] || PYTHON_BIN="$(command -v python3)"

if ! BOUNDARY_SHA="$(git -C "$WT" rev-parse --verify "$BOUNDARY^{commit}" 2>/dev/null)"; then
  echo "FAILED: upstream boundary $BOUNDARY is unreachable in $WT; refusing to run a fork gate against a boundary we cannot resolve." >&2
  exit 2
fi

filter_tests() {
  grep -E '\.py$' | grep -v '/fixtures/' | grep -v -E '(^|/)__init__\.py$' \
    | grep -v '/[.]_' | grep -v '^[.]_' || true
}

mapfile -t OURS < <(
  comm -23 \
    <(git -C "$WT" ls-tree -r --name-only HEAD tests/ | sort) \
    <(git -C "$WT" ls-tree -r --name-only "$BOUNDARY_SHA" tests/ | sort) \
  | filter_tests
)

# A merge can change a test that already exists upstream. Include those files
# as well; otherwise the merge's own test change is outside the sensor set.
#
# --diff-filter=d исключает УДАЛЁННЫЕ пути. Без него сюда попадает файл,
# который мерж удалил: в diff он есть по определению (diff описывает переход
# между деревьями), а в дереве, против которого мы запускаем, его нет. pytest
# такой путь не пропускает, а обрушивает весь прогон — «no tests ran» вместо
# итоговой строки, и гейт возвращает «несравнимо» на непроверенном мерже.
mapfile -t MERGE_CHANGED < <(
  if FIRST_PARENT=$(git -C "$WT" rev-parse --verify HEAD^1 2>/dev/null); then
    git -C "$WT" diff --name-only --diff-filter=d "$FIRST_PARENT" HEAD -- tests/ | filter_tests
  fi
)
mapfile -t CANDIDATES < <(printf '%s\n' "${OURS[@]}" "${MERGE_CHANGED[@]}" | sed '/^$/d' | sort -u)

# Фильтр существования поверх --diff-filter: он ловит не только удаления, но и
# переименования и всё, что придумает следующий апстрим. Отброшенное считаем и
# называем вслух — молча уменьшившийся сенсор это гейт, который «всё прошёл».
TESTS=()
DROPPED=()
for path in "${CANDIDATES[@]}"; do
  if [ -f "$WT/$path" ]; then
    TESTS+=("$path")
  else
    DROPPED+=("$path")
  fi
done

MAX_FILES="${HERMES_FORK_TEST_MAX_FILES:-800}"
if [ "${#TESTS[@]}" -gt "$MAX_FILES" ]; then
  echo "FAILED: fork test selection has ${#TESTS[@]} files, over limit $MAX_FILES; explicit operator decision required (no silent truncation)." >&2
  exit 2
fi

# Диагностика идёт в stderr, а не в stdout: stdout — это протокол, по нему
# отдаётся только набор. Иначе `--print-selection > manifest` записал бы эту
# строку в манифест как ещё один «путь».
printf 'fork test selection: boundary=%s files=%s fork_only=%s merge_changed=%s dropped_missing=%s\n' \
  "$BOUNDARY_SHA" "${#TESTS[@]}" "${#OURS[@]}" "${#MERGE_CHANGED[@]}" "${#DROPPED[@]}" >&2
# Отброшенное — не «мелочь, о которой мы сообщили», а признак, что дерево не
# соответствует своему HEAD: кандидаты берутся из ls-tree HEAD и из diff без
# удалений, поэтому отсутствие пути на диске означает неполный или разошедшийся
# чекаут. Продолжить прогон значит отдать компаратору нормальную итоговую
# строку по уменьшившемуся сенсору — гейт, который «всё прошёл». Ни один
# потребитель dropped_missing не читает, так что видимость для человека здесь
# не заменяет отказа.
#
# Файл, удалённый мержем, сюда не попадает: его снимает --diff-filter=d выше.
if [ "${#DROPPED[@]}" -gt 0 ]; then
  printf 'fork test selection: dropped (absent from the tree under test): %s\n' "${DROPPED[*]}" >&2
  echo "FAILED: ${#DROPPED[@]} selected path(s) are absent from the tree under test; the checkout does not match the HEAD being gated. Refusing to run a shrunken sensor." >&2
  exit 2
fi

# Пустой набор — сбой вычисления, а не «проверять нечего»: у форка собственных
# тестов заведомо больше трёхсот файлов. Сообщать о чистом прогоне в этом
# случае значит пустить слияние в прод вслепую.
if [ "${#TESTS[@]}" -eq 0 ]; then
  echo "FAILED: computed an empty fork test set; refusing to report a clean run." >&2
  exit 2
fi

if [ "$PRINT_SELECTION" -eq 1 ]; then
  printf '%s\n' "${TESTS[@]}"
  exit 0
fi

cd "$WT"
nice -n 19 "$PYTHON_BIN" -m pytest "${TESTS[@]}" \
  -q -p no:cacheprovider --timeout=90 -rf
