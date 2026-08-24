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
RECEIPT_CONTRACT = "v1"
_RECEIPT_FIELDS = {
    "manifest": "manifest_sha256",
    "legacy": "selection_sha256",
}


def fork_test_receipt(*, source: str, digest: str) -> str:
    """Format the receipt shared by the runner and its enforcing caller."""
    try:
        field = _RECEIPT_FIELDS[source]
    except KeyError as exc:
        raise ValueError(f"unknown fork-test receipt source {source!r}") from exc
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("fork-test receipt digest must be a lowercase SHA-256")
    return (
        f"fork test receipt: contract={RECEIPT_CONTRACT} source={source} "
        f"{field}={digest}"
    )


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


def _require_distinct_candidate_trees(before: str, after: str) -> None:
    if before == after:
        raise ValueError("selection manifest before and after must be distinct")


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
    _require_distinct_candidate_trees(before, after)
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
        # Foreign entries mean the evidence namespace is corrupt. Never skip
        # or remove them automatically: preserving unexplained evidence is
        # safer than making later generations appear trustworthy around it.
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
    manifest.update(
        {
            "candidate_id": metadata["candidate_id"],
            "generation": metadata["generation"],
            "run_id": metadata["run_id"],
        }
    )
    write_json_atomic(attempt_dir / "gate-selection.json", manifest)
    # Commit marker last: readers treat a generation without attempt.json as
    # incomplete, never as a bound manifest that is safe to consume.
    write_json_atomic(attempt_dir / "attempt.json", metadata)
    return {
        **selection_manifest_report(manifest),
        "attempt_dir": str(attempt_dir),
        "candidate_id": metadata["candidate_id"],
        "generation": metadata["generation"],
        "run_id": metadata["run_id"],
    }


def _read_nul_paths(path: str) -> list[str]:
    return [os.fsdecode(item) for item in Path(path).read_bytes().split(b"\0") if item]


def selection_paths_from_manifest(
    manifest_path: Path,
    *,
    expected_attempts_root: Path,
    worktree: Path,
    checkout_head: str,
    expected_boundary: str,
) -> list[str]:
    """Validate one committed attempt manifest and select this checkout's paths."""
    manifest_path = Path(manifest_path).resolve(strict=True)
    if manifest_path.name != "gate-selection.json":
        raise ValueError(
            f"selection manifest must be named gate-selection.json: {manifest_path}"
        )
    attempt_dir = manifest_path.parent
    candidate_dir = attempt_dir.parent
    expected_attempts_root = Path(expected_attempts_root).resolve(strict=True)
    if candidate_dir.parent != expected_attempts_root:
        raise ValueError(
            "selection manifest is outside the expected attempt root: "
            f"manifest {manifest_path}, expected attempt root "
            f"{expected_attempts_root}"
        )
    commit_marker = attempt_dir / "attempt.json"
    if not commit_marker.is_file():
        raise ValueError(
            f"selection generation is incomplete: missing commit marker {commit_marker}"
        )

    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("selection manifest must be a JSON object")
    schema = payload.get("schema_version")
    if schema != SELECTION_MANIFEST_SCHEMA:
        raise ValueError(
            f"unknown selection manifest schema {schema!r}; "
            f"expected {SELECTION_MANIFEST_SCHEMA!r}"
        )

    before = payload.get("before")
    after = payload.get("after")
    boundary = payload.get("boundary")
    candidate_id = payload.get("candidate_id")
    generation = payload.get("generation")
    run_id = payload.get("run_id")
    for name, value in (
        ("before", before),
        ("after", after),
        ("boundary", boundary),
        ("candidate_id", candidate_id),
        ("run_id", run_id),
    ):
        if not isinstance(value, str) or not value:
            raise ValueError(f"selection manifest field {name} must be a string")
    if type(generation) is not int or generation < 1:
        raise ValueError("selection manifest generation must be a positive integer")
    _require_distinct_candidate_trees(before, after)

    derived_candidate_id = _candidate_id(before, after, boundary)
    if candidate_id != derived_candidate_id:
        raise ValueError(
            "selection manifest candidate_id does not match before/after/boundary: "
            f"declared {candidate_id}, derived {derived_candidate_id}"
        )
    if candidate_dir.name != candidate_id:
        raise ValueError(
            "selection manifest candidate_id does not match its path: "
            f"declared {candidate_id}, path {candidate_dir.name}"
        )
    if attempt_dir.name != str(generation):
        raise ValueError(
            "selection manifest generation does not match its path: "
            f"declared {generation}, path {attempt_dir.name}"
        )
    expected_run_id = f"{candidate_id}:{generation}"
    if run_id != expected_run_id:
        raise ValueError(
            f"selection manifest run_id {run_id!r} does not match {expected_run_id!r}"
        )
    if boundary != expected_boundary:
        raise ValueError(
            "selection manifest boundary does not match the runner boundary: "
            f"manifest {boundary}, runner {expected_boundary}"
        )
    if checkout_head not in (before, after):
        raise ValueError(
            f"checkout HEAD {checkout_head} is neither manifest before {before} "
            f"nor after {after}"
        )

    entries = payload.get("tests")
    if not isinstance(entries, list):
        raise ValueError("selection manifest tests must be a list")
    exists_key = "exists_pre" if checkout_head == before else "exists_post"
    worktree = Path(worktree).resolve(strict=True)
    selected: list[str] = []
    seen: set[str] = set()
    for index, item in enumerate(entries):
        if not isinstance(item, dict):
            raise ValueError(f"selection manifest tests[{index}] must be an object")
        path = item.get("path")
        if (
            not isinstance(path, str)
            or not _is_test_path(path)
            or any(part in ("", ".", "..") for part in path.split("/"))
        ):
            raise ValueError(f"selection manifest has invalid test path {path!r}")
        if path in seen:
            raise ValueError(f"selection manifest repeats test path {path}")
        seen.add(path)
        exists_pre = item.get("exists_pre")
        exists_post = item.get("exists_post")
        if type(exists_pre) is not bool or type(exists_post) is not bool:
            raise ValueError(
                f"selection manifest existence flags must be booleans for {path}"
            )
        declared_exists = item[exists_key]
        actual_exists = (worktree / path).is_file()
        if actual_exists != declared_exists:
            raise ValueError(
                f"selection manifest declares {path} {exists_key}={declared_exists}, "
                f"but checkout presence is {actual_exists}"
            )
        if declared_exists:
            selected.append(path)
    return selected


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

    p_consume = sub.add_parser(
        "selection-paths", help="validate a bound manifest and list checkout paths"
    )
    p_consume.add_argument("--manifest", required=True)
    p_consume.add_argument("--attempt-root", required=True)
    p_consume.add_argument("--worktree", required=True)
    p_consume.add_argument("--head", required=True)
    p_consume.add_argument("--boundary", required=True)

    p_receipt = sub.add_parser(
        "receipt", help="format the runner receipt for a computed digest"
    )
    p_receipt.add_argument("--source", choices=sorted(_RECEIPT_FIELDS), required=True)
    p_receipt.add_argument("--digest", required=True)

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
        elif args.cmd == "prepare-selection":
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
        elif args.cmd == "selection-paths":
            items = selection_paths_from_manifest(
                Path(args.manifest),
                expected_attempts_root=Path(args.attempt_root),
                worktree=Path(args.worktree),
                checkout_head=args.head,
                expected_boundary=args.boundary,
            )
            for item in items:
                print(item)
            return 0
        else:
            print(fork_test_receipt(source=args.source, digest=args.digest))
            return 0
    except (ValueError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 2

    for item in items:
        print(item)
    return 1 if items else 0


if __name__ == "__main__":
    raise SystemExit(_main())
