"""Решения гейта upstream-sync над выводом git и pytest.

Модуль намеренно не знает ни про git, ни про pytest: он получает их вывод
текстом и отдаёт структуру. Так обе проверки, от которых зависит, поедет ли
обновление в прод, тестируются на подготовленных входах, без временных
репозиториев и без запуска pytest внутри pytest.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class MergeTreeReport:
    tree_oid: str
    conflicted_paths: list[str] = field(default_factory=list)


def parse_merge_tree(output: str) -> MergeTreeReport:
    """Разобрать вывод ``git merge-tree --write-tree --name-only``.

    Формат: OID результирующего дерева первой строкой; затем пути
    конфликтующих файлов до первой пустой строки; затем информационные
    сообщения (``Auto-merging``, ``CONFLICT (content)``), которые путями не
    являются и в отчёт не попадают.

    Пустой вывод — не «чистое слияние», а признак того, что команда не
    отработала: git всегда печатает хотя бы OID.
    """
    lines = output.split("\n")
    if not lines or not lines[0].strip():
        raise ValueError("git merge-tree printed no tree OID; the command did not run")

    tree_oid = lines[0].strip()
    paths: list[str] = []
    for line in lines[1:]:
        if not line.strip():
            break
        paths.append(line.strip())
    return MergeTreeReport(tree_oid=tree_oid, conflicted_paths=paths)


_FAILED_LINE = re.compile(r"^FAILED\s+(\S+)")
_SUMMARY_LINE = re.compile(
    r"^=*\s*\d+\s+(?:failed|passed|error)\b.*\bin\s+[\d.]+s", re.MULTILINE
)


def _failures(log: str) -> set[str]:
    if not _SUMMARY_LINE.search(log):
        raise ValueError(
            "pytest log has no summary line: the run was killed, not clean"
        )
    found: set[str] = set()
    for line in log.split("\n"):
        m = _FAILED_LINE.match(line)
        if m:
            found.add(m.group(1))
    return found


def new_failures(baseline_log: str, post_log: str) -> list[str]:
    """Тесты, упавшие после слияния и не падавшие до него.

    Пропавшие падения не возвращаются: слияние, которое что-то починило, —
    не повод его блокировать.
    """
    return sorted(_failures(post_log) - _failures(baseline_log))


def _main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="upstream-sync gate decisions")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_mt = sub.add_parser("merge-tree", help="list conflicting paths")
    p_mt.add_argument("--output", required=True, help="file with git merge-tree output")

    p_nf = sub.add_parser("new-failures", help="list failures the merge introduced")
    p_nf.add_argument("--baseline", required=True)
    p_nf.add_argument("--post", required=True)

    args = parser.parse_args(argv)

    try:
        if args.cmd == "merge-tree":
            report = parse_merge_tree(Path(args.output).read_text(encoding="utf-8"))
            items = report.conflicted_paths
        else:
            items = new_failures(
                Path(args.baseline).read_text(encoding="utf-8"),
                Path(args.post).read_text(encoding="utf-8"),
            )
    except (ValueError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 2

    for item in items:
        print(item)
    return 1 if items else 0


if __name__ == "__main__":
    raise SystemExit(_main())
