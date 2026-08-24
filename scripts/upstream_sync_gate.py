"""Решения гейта upstream-sync над выводом git и pytest.

Модуль намеренно не знает ни про git, ни про pytest: он получает их вывод
текстом и отдаёт структуру. Так обе проверки, от которых зависит, поедет ли
обновление в прод, тестируются на подготовленных входах, без временных
репозиториев и без запуска pytest внутри pytest.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


SELECTION_MANIFEST_SCHEMA = "upstream-sync-test-selection/v1"
ATTEMPT_SCHEMA = "upstream-sync-gate-attempt/v1"


def _is_test_path(path: str) -> bool:
    """Apply the fork-test path policy to raw git listings.

    Keeping this policy in the builder prevents each shell caller from
    growing a subtly different idea of which paths may be passed to pytest.
    """
    parts = path.split("/")
    return (
        len(parts) > 1
        and parts[0] == "tests"
        and path.endswith(".py")
        and parts[-1] != "__init__.py"
        and "fixtures" not in parts
        and not any(part.startswith("._") for part in parts)
    )


def _test_paths(paths: list[str]) -> set[str]:
    return {path for path in paths if _is_test_path(path)}


def build_selection_manifest(
    *,
    before: str,
    after: str,
    boundary: str,
    before_paths: list[str],
    after_paths: list[str],
    boundary_paths: list[str],
    changed_paths: list[str],
) -> dict[str, Any]:
    """Build one test universe for both sides of the differential gate."""
    before_set = _test_paths(before_paths)
    after_set = _test_paths(after_paths)
    boundary_set = _test_paths(boundary_paths)
    changed_set = _test_paths(changed_paths)

    universe = (
        (before_set - boundary_set)
        | (after_set - boundary_set)
        | changed_set
    )
    entries = [
        {
            "path": path,
            "exists_pre": path in before_set,
            "exists_post": path in after_set,
        }
        for path in sorted(universe)
    ]
    absent = [
        entry["path"]
        for entry in entries
        if not entry["exists_pre"] and not entry["exists_post"]
    ]
    if absent:
        raise ValueError(
            "selection input is not bound to before/after: test paths exist in "
            f"neither tree: {', '.join(absent)}"
        )

    return {
        "schema_version": SELECTION_MANIFEST_SCHEMA,
        "before": before,
        "after": after,
        "boundary": boundary,
        "tests": entries,
    }


def selection_manifest_report(manifest: dict[str, Any]) -> dict[str, Any]:
    """Return informational path-level facts, never failure buckets."""
    pre_only_paths = [
        item["path"]
        for item in manifest["tests"]
        if item["exists_pre"] and not item["exists_post"]
    ]
    return {
        "pre_only_paths": pre_only_paths,
        "pre_only_path_count": len(pre_only_paths),
    }


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    """Write JSON through a same-directory temporary file and atomic rename."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary.chmod(0o600)
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _candidate_id(before: str, after: str, boundary: str) -> str:
    identity = json.dumps(
        {"after": after, "before": before, "boundary": boundary},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _allocate_attempt(
    state_dir: Path, *, before: str, after: str, boundary: str
) -> tuple[Path, dict[str, Any]]:
    candidate_id = _candidate_id(before, after, boundary)
    candidate_dir = state_dir / "attempts" / candidate_id
    candidate_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    existing_generations: list[int] = []
    for child in candidate_dir.iterdir():
        if not child.is_dir() or not child.name.isdecimal():
            raise ValueError(f"invalid attempt generation entry: {child}")
        existing_generations.append(int(child.name))
    generation = max(existing_generations, default=0) + 1
    while True:
        attempt_dir = candidate_dir / str(generation)
        try:
            attempt_dir.mkdir(mode=0o700)
            break
        except FileExistsError:
            generation += 1

    metadata = {
        "schema_version": ATTEMPT_SCHEMA,
        "before": before,
        "after": after,
        "boundary": boundary,
        "candidate_id": candidate_id,
        "generation": generation,
        "run_id": f"{candidate_id}:{generation}",
    }
    return attempt_dir, metadata


def prepare_selection_attempt(
    state_dir: Path,
    *,
    before: str,
    after: str,
    boundary: str,
    before_paths: list[str],
    after_paths: list[str],
    boundary_paths: list[str],
    changed_paths: list[str],
) -> dict[str, Any]:
    """Allocate an append-only generation and persist its bound manifest."""
    manifest = build_selection_manifest(
        before=before,
        after=after,
        boundary=boundary,
        before_paths=before_paths,
        after_paths=after_paths,
        boundary_paths=boundary_paths,
        changed_paths=changed_paths,
    )
    attempt_dir, metadata = _allocate_attempt(
        Path(state_dir), before=before, after=after, boundary=boundary
    )
    write_json_atomic(attempt_dir / "attempt.json", metadata)
    write_json_atomic(attempt_dir / "gate-selection.txt", manifest)
    return {
        **selection_manifest_report(manifest),
        "attempt_dir": str(attempt_dir),
        "candidate_id": metadata["candidate_id"],
        "generation": metadata["generation"],
        "run_id": metadata["run_id"],
    }


def _read_nul_paths(path: str) -> list[str]:
    return [os.fsdecode(item) for item in Path(path).read_bytes().split(b"\0") if item]


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

    p_selection = sub.add_parser(
        "prepare-selection", help="build and persist one gate selection manifest"
    )
    p_selection.add_argument("--state-dir", required=True)
    p_selection.add_argument("--before", required=True)
    p_selection.add_argument("--after", required=True)
    p_selection.add_argument("--boundary", required=True)
    p_selection.add_argument("--before-paths", required=True)
    p_selection.add_argument("--after-paths", required=True)
    p_selection.add_argument("--boundary-paths", required=True)
    p_selection.add_argument("--changed-paths", required=True)

    args = parser.parse_args(argv)

    try:
        if args.cmd == "merge-tree":
            report = parse_merge_tree(Path(args.output).read_text(encoding="utf-8"))
            items = report.conflicted_paths
        elif args.cmd == "new-failures":
            items = new_failures(
                Path(args.baseline).read_text(encoding="utf-8"),
                Path(args.post).read_text(encoding="utf-8"),
            )
        else:
            report = prepare_selection_attempt(
                Path(args.state_dir),
                before=args.before,
                after=args.after,
                boundary=args.boundary,
                before_paths=_read_nul_paths(args.before_paths),
                after_paths=_read_nul_paths(args.after_paths),
                boundary_paths=_read_nul_paths(args.boundary_paths),
                changed_paths=_read_nul_paths(args.changed_paths),
            )
            print(json.dumps(report, ensure_ascii=False, sort_keys=True))
            return 0
    except (ValueError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 2

    for item in items:
        print(item)
    return 1 if items else 0


if __name__ == "__main__":
    raise SystemExit(_main())
