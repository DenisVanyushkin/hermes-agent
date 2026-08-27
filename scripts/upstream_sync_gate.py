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
from datetime import datetime, timezone
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


SELECTION_MANIFEST_SCHEMA = "upstream-sync-test-selection/v1"
ATTEMPT_SCHEMA = "upstream-sync-gate-attempt/v1"
GATE_FAILURES_SCHEMA = "upstream-sync-gate-failures/v2"
RECEIPT_CONTRACT = "v1"
_RECEIPT_FIELDS = {
    "manifest": "manifest_sha256",
    "legacy": "selection_sha256",
}


def fork_test_receipt(*, source: str, digest: str, side: str | None = None) -> str:
    """Format the receipt shared by the runner and its enforcing caller."""
    try:
        field = _RECEIPT_FIELDS[source]
    except KeyError as exc:
        raise ValueError(f"unknown fork-test receipt source {source!r}") from exc
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("fork-test receipt digest must be a lowercase SHA-256")
    if side is not None and side not in {"pre", "post"}:
        raise ValueError(f"unknown fork-test receipt side {side!r}")
    side_field = f" side={side}" if side is not None else ""
    return (
        f"fork test receipt: contract={RECEIPT_CONTRACT} source={source}{side_field} "
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


def _node_ids(run: dict[str, Any], field: str) -> set[str]:
    if not isinstance(run, dict):
        raise ValueError("node run must be an object")
    values = run.get(field)
    if not isinstance(values, (set, list, tuple)):
        raise ValueError(f"node run {field} must be a collection of nodeids")
    if not all(isinstance(value, str) for value in values):
        raise ValueError(f"node run {field} must contain only strings")
    return set(values)


def _manifest_presence(manifest: dict[str, Any]) -> dict[str, tuple[bool, bool]]:
    entries = manifest.get("tests")
    if not isinstance(entries, list):
        raise ValueError("classification manifest tests must be a list")
    presence: dict[str, tuple[bool, bool]] = {}
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ValueError(f"classification manifest tests[{index}] must be an object")
        path = entry.get("path")
        exists_pre = entry.get("exists_pre")
        exists_post = entry.get("exists_post")
        if (
            not isinstance(path, str)
            or type(exists_pre) is not bool
            or type(exists_post) is not bool
        ):
            raise ValueError(
                f"classification manifest tests[{index}] has invalid path presence"
            )
        if path in presence:
            raise ValueError(f"classification manifest repeats path {path}")
        presence[path] = (exists_pre, exists_post)
    return presence


def _unknown_nodes(
    *,
    source: str,
    stage: str,
    nodeids: set[str],
) -> list[dict[str, str]]:
    return [
        {
            "path": nodeid.split("::", 1)[0],
            "nodeid": nodeid,
            "source": source,
            "stage": stage,
        }
        for nodeid in sorted(nodeids)
    ]


def _empty_classification() -> dict[str, list[dict[str, str]]]:
    return {
        "common_path": [],
        "post_only_path": [],
        "pre_existing": [],
        "unknown": [],
    }


def classify_node_failures(
    *,
    baseline: dict[str, Any],
    upstream_parent: dict[str, Any],
    merged: dict[str, Any],
    manifest: dict[str, Any],
) -> dict[str, list[dict[str, str]]]:
    """Classify merged failures using one manifest and three node outcomes.

    This is deliberately a pure decision function. Callers must provide
    structured collection/probe outcomes and the persisted selection
    manifest; log parsing and live blocking policy remain outside this layer.
    """
    result = _empty_classification()
    presence = _manifest_presence(manifest)
    runs = (
        ("baseline", baseline),
        ("upstream_parent", upstream_parent),
        ("merged", merged),
    )
    collected: dict[str, set[str]] = {}
    failed: dict[str, set[str]] = {}
    incoherent: list[dict[str, str]] = []
    unreadable: list[tuple[str, str]] = []
    for source, run in runs:
        collected[source] = _node_ids(run, "collected_nodeids")
        failed[source] = _node_ids(run, "failed_nodeids")
        if run.get("collect_ok") is not True:
            unreadable.append((source, "collect"))
        elif run.get("probe_ok") is not True:
            # Collection is the prerequisite for any probe result. Report
            # only collect when both stages are bad; a probe without a valid
            # collection cannot be interpreted independently.
            unreadable.append((source, "probe"))
        invalid = failed[source] - collected[source]
        if invalid:
            incoherent.extend(
                _unknown_nodes(source=source, stage="outcome", nodeids=invalid)
            )
    if unreadable:
        result["unreadable_runs"] = [
            {"source": source, "stage": stage} for source, stage in unreadable
        ]
        for source, stage in unreadable:
            result["unknown"].extend(
                _unknown_nodes(
                    source=source,
                    stage=stage,
                    nodeids=failed["merged"],
                )
            )
        result["unknown"].sort(key=lambda item: (item["path"], item["nodeid"]))
        return result
    if incoherent:
        result["unknown"] = sorted(
            incoherent, key=lambda item: (item["path"], item["nodeid"])
        )
        return result

    for nodeid in sorted(failed["merged"]):
        path = nodeid.split("::", 1)[0]
        path_presence = presence.get(path)
        # A failure already present in baseline is informational even when it
        # came from a caller's broader legacy sensor and therefore has no
        # entry in the bound manifest. It must never become an admission
        # failure merely because the new manifest does not describe it.
        if nodeid in failed["baseline"] and path_presence is None:
            result["pre_existing"].append(
                {
                    "path": path,
                    "nodeid": nodeid,
                    "classification": "pre_existing_failure",
                }
            )
            continue
        if path_presence is None or not path_presence[1]:
            result["unknown"].append(
                {
                    "path": path,
                    "nodeid": nodeid,
                    "source": "manifest",
                    "stage": "presence",
                }
            )
            continue
        if nodeid in failed["baseline"]:
            if not path_presence[0]:
                result["unknown"].append(
                    {
                        "path": path,
                        "nodeid": nodeid,
                        "source": "manifest",
                        "stage": "presence",
                    }
                )
                continue
            classification = "pre_existing_failure"
            bucket = "pre_existing"
        elif nodeid in collected["baseline"]:
            if not path_presence[0]:
                result["unknown"].append(
                    {
                        "path": path,
                        "nodeid": nodeid,
                        "source": "manifest",
                        "stage": "presence",
                    }
                )
                continue
            classification = "fork_regression"
            bucket = "common_path"
        elif nodeid in collected["upstream_parent"]:
            if nodeid in failed["upstream_parent"]:
                classification = "upstream_red_admission_failure"
            else:
                classification = "fork_compatibility_failure"
            bucket = "common_path" if path_presence[0] else "post_only_path"
        else:
            classification = "merge_resolution_or_local_introduced"
            bucket = "post_only_path" if not path_presence[0] else "common_path"
        result[bucket].append(
            {
                "path": path,
                "nodeid": nodeid,
                "classification": classification,
            }
        )

    for key in ("common_path", "post_only_path", "pre_existing", "unknown"):
        result[key].sort(key=lambda item: (item["path"], item["nodeid"]))
    return result


def build_gate_failures_payload(
    *,
    classification: dict[str, Any],
    merge_sha: str,
    before: str,
    legacy_failures: list[str],
    created_at: str | None = None,
) -> dict[str, Any]:
    """Build the persisted v2 gate result from one classifier output.

    ``blocking_failures`` is deliberately derived here, at the persistence
    boundary, rather than trusted as a separately supplied list.  Its identity
    is the ``(path, nodeid)`` pair; the output is sorted and unique, while
    informational and unknown buckets never enter it.
    """
    buckets = ("common_path", "post_only_path")
    blocking: dict[tuple[str, str], dict[str, Any]] = {}
    for bucket in buckets:
        entries = classification.get(bucket, [])
        if not isinstance(entries, list):
            raise ValueError(f"classification bucket {bucket} must be a list")
        for item in entries:
            if not isinstance(item, dict):
                raise ValueError(f"classification bucket {bucket} contains a non-object")
            path = item.get("path")
            nodeid = item.get("nodeid")
            if not isinstance(path, str) or not path or not isinstance(nodeid, str) or not nodeid:
                raise ValueError(f"classification bucket {bucket} contains an invalid failure")
            blocking.setdefault((path, nodeid), dict(item))

    payload: dict[str, Any] = {
        "schema_version": GATE_FAILURES_SCHEMA,
        "merge_sha": merge_sha,
        "before": before,
        **{
            key: classification.get(key, [])
            for key in (
                "common_path",
                "post_only_path",
                "pre_existing",
                "unknown",
                "unreadable_runs",
            )
        },
        "blocking_failures": [
            blocking[key] for key in sorted(blocking)
        ],
        "new_failures": sorted({item for item in legacy_failures if item}),
        "created_at": created_at or datetime.now(timezone.utc).isoformat(),
    }
    return payload


def build_upstream_probe_request(
    *,
    baseline: dict[str, Any],
    merged: dict[str, Any],
    manifest: dict[str, Any],
    available_paths: set[str] | None = None,
) -> dict[str, list[str]]:
    """Select the exact merged failures that need an upstream-parent probe.

    Presence is path-level, but the probe is node-level: probing a whole file
    can hide the fact that only one newly-added node needs comparison.  The
    request is therefore deliberately derived from the merged failure set and
    contains no path that is not present in the post-merge tree.
    """
    baseline_collected = _node_ids(baseline, "collected_nodeids")
    baseline_failed = _node_ids(baseline, "failed_nodeids")
    merged_collected = _node_ids(merged, "collected_nodeids")
    merged_failed = _node_ids(merged, "failed_nodeids")
    if baseline_failed - baseline_collected:
        raise ValueError("baseline failed nodeids are not a subset of collected nodeids")
    if merged_failed - merged_collected:
        raise ValueError("merged failed nodeids are not a subset of collected nodeids")
    if baseline.get("collect_ok") is not True or merged.get("collect_ok") is not True:
        raise ValueError("cannot build an upstream probe request from an unreadable collect")
    if baseline.get("probe_ok") is not True or merged.get("probe_ok") is not True:
        raise ValueError("cannot build an upstream probe request from an unreadable probe")

    presence = _manifest_presence(manifest)
    newly_seen = merged_failed - baseline_failed
    selected_nodeids: set[str] = set()
    paths: set[str] = set()
    for nodeid in newly_seen:
        path = nodeid.split("::", 1)[0]
        if available_paths is not None and path not in available_paths:
            continue
        path_presence = presence.get(path)
        if path_presence is None or not path_presence[1]:
            raise ValueError(
                f"newly failed nodeid {nodeid} has no post-merge test path in the manifest"
            )
        selected_nodeids.add(nodeid)
        paths.add(path)
    return {"nodeids": sorted(selected_nodeids), "paths": sorted(paths)}


def filter_probe_request(
    request: dict[str, Any], available_nodeids: set[str]
) -> dict[str, list[str]]:
    """Keep only probe nodeids collected by the upstream-parent checkout."""
    nodeids = request.get("nodeids")
    if not isinstance(nodeids, list) or not all(isinstance(item, str) for item in nodeids):
        raise ValueError("probe request nodeids must be a string list")
    if not isinstance(available_nodeids, set) or not all(
        isinstance(item, str) for item in available_nodeids
    ):
        raise ValueError("available probe nodeids must be a string set")
    selected = sorted(set(nodeids) & available_nodeids)
    return {
        "nodeids": selected,
        "paths": sorted({nodeid.split("::", 1)[0] for nodeid in selected}),
    }


_FAILED_LINE = re.compile(r"^FAILED\s+(\S+)")
_COLLECTED_LINE = re.compile(r"^(?:PASSED|FAILED|SKIPPED|XFAIL|XPASS|RERUN)\s+(\S+)")
_ERROR_NODE_LINE = re.compile(r"^ERROR\s+(\S+::\S+)(?:\s+-.*)?$")
_COLLECTION_ERROR_LINE = re.compile(
    r"^ERROR\s+(?!collecting\b)(?!\S+::)(\S+)(?:\s+-.*)?$"
)
_NO_TESTS_RAN = re.compile(r"^no tests ran in\s+[\d.]+s\s*$", re.MULTILINE)
_SUMMARY_LINE = re.compile(
    r"^=*\s*(?P<counts>(?:\d+\s+(?:failed|passed|skipped|warnings?|errors?|error)\b"
    r"(?:,\s*)?)+)\s+in\s+[\d.]+s(?:\s+\(\d+:\d{2}:\d{2}\))?\s*$",
    re.MULTILINE,
)
_COUNT_TOKEN = re.compile(
    r"(?P<count>\d+)\s+(?P<label>failed|passed|skipped|warnings?|errors?|error)\b"
)


def parse_test_outcomes(log: str) -> dict[str, Any]:
    """Parse pytest's failure and collection-error outcomes together.

    A summary containing only collection errors is still readable. The special
    ``no tests ran`` line is deliberately rejected: it is an unreadable gate
    run, not evidence that the selected tests passed.
    """
    if _NO_TESTS_RAN.search(log):
        raise ValueError("pytest run is unreadable: no tests ran")
    summary = _SUMMARY_LINE.search(log)
    if not summary:
        raise ValueError(
            "pytest log has no summary line: the run was killed, not clean"
        )
    counts: dict[str, int] = {}
    for match in _COUNT_TOKEN.finditer(summary.group("counts")):
        label = match.group("label")
        if label == "warnings":
            label = "warning"
        elif label == "errors":
            label = "error"
        counts[label] = counts.get(label, 0) + int(match.group("count"))
    collected_nodeids = {
        match.group(1)
        for line in log.splitlines()
        if (match := _COLLECTED_LINE.match(line))
    }
    error_nodeids = {
        match.group(1)
        for line in log.splitlines()
        if (match := _ERROR_NODE_LINE.match(line))
    }
    collected_nodeids.update(error_nodeids)
    failed_nodeids = {
        match.group(1)
        for line in log.splitlines()
        if (match := _FAILED_LINE.match(line))
    }
    failed_nodeids.update(error_nodeids)
    collection_error_paths = sorted(
        {
            match.group(1)
            for line in log.splitlines()
            if (match := _COLLECTION_ERROR_LINE.match(line))
        }
    )
    return {
        "collected_nodeids": sorted(collected_nodeids),
        "failed_nodeids": sorted(failed_nodeids),
        "error_count": counts.get("error", 0),
        "collection_error_paths": collection_error_paths,
    }


def compare_test_outcomes(baseline_log: str, post_log: str) -> dict[str, list[str]]:
    baseline = parse_test_outcomes(baseline_log)
    post = parse_test_outcomes(post_log)
    new_collection_errors = sorted(
        set(post["collection_error_paths"]) - set(baseline["collection_error_paths"])
    )
    unidentified = max(0, post["error_count"] - baseline["error_count"])
    if new_collection_errors:
        unidentified = 0
    new_collection_errors.extend(
        f"<unidentified-{index}>" for index in range(1, unidentified + 1)
    )
    return {
        "new_failures": sorted(
            set(post["failed_nodeids"]) - set(baseline["failed_nodeids"])
        ),
        "new_collection_errors": new_collection_errors,
    }


def _failures(log: str) -> set[str]:
    return set(parse_test_outcomes(log)["failed_nodeids"])


def new_failures(baseline_log: str, post_log: str) -> list[str]:
    """Тесты, упавшие после слияния и не падавшие до него.

    Пропавшие падения не возвращаются: слияние, которое что-то починило, —
    не повод его блокировать.
    """
    compared = compare_test_outcomes(baseline_log, post_log)
    return compared["new_failures"] + [
        f"COLLECTION_ERROR {path}" for path in compared["new_collection_errors"]
    ]


def _main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="upstream-sync gate decisions")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_mt = sub.add_parser("merge-tree", help="list conflicting paths")
    p_mt.add_argument("--output", required=True, help="file with git merge-tree output")

    p_nf = sub.add_parser("new-failures", help="list failures the merge introduced")
    p_nf.add_argument("--baseline", required=True)
    p_nf.add_argument("--post", required=True)

    p_probe = sub.add_parser(
        "probe-request", help="select exact newly failing nodeids for the upstream probe"
    )
    p_probe.add_argument("--baseline", required=True)
    p_probe.add_argument("--merged", required=True)
    p_probe.add_argument("--manifest", required=True)
    p_probe.add_argument("--boundary-paths")

    p_filter_probe = sub.add_parser(
        "filter-probe-request", help="filter an upstream probe to collected nodeids"
    )
    p_filter_probe.add_argument("--request", required=True)
    p_filter_probe.add_argument("--available-nodeids", required=True)

    p_classify = sub.add_parser(
        "classify-node-failures", help="classify structured baseline, probe and merged outcomes"
    )
    p_classify.add_argument("--baseline", required=True)
    p_classify.add_argument("--upstream-parent", required=True)
    p_classify.add_argument("--merged", required=True)
    p_classify.add_argument("--manifest", required=True)

    p_outcome = sub.add_parser(
        "node-outcome", help="turn one pytest log into a minimal structured node outcome"
    )
    p_outcome.add_argument("--log", required=True)
    p_outcome.add_argument("--expected-nodeids")

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
    p_receipt.add_argument("--side", choices=("pre", "post"))
    p_receipt.add_argument("--digest", required=True)

    p_failures = sub.add_parser(
        "persist-gate-failures", help="persist one normalized v2 gate outcome"
    )
    p_failures.add_argument("--classification", required=True)
    p_failures.add_argument("--merge-sha", required=True)
    p_failures.add_argument("--before", required=True)
    p_failures.add_argument("--legacy-failures", required=True)
    p_failures.add_argument("--output", required=True)

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
        elif args.cmd == "probe-request":
            baseline = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
            merged = json.loads(Path(args.merged).read_text(encoding="utf-8"))
            manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
            available_paths = None
            if args.boundary_paths:
                available_paths = {
                    line
                    for line in Path(args.boundary_paths)
                    .read_text(encoding="utf-8")
                    .splitlines()
                    if line
                }
            print(json.dumps(
                build_upstream_probe_request(
                    baseline=baseline,
                    merged=merged,
                    manifest=manifest,
                    available_paths=available_paths,
                ),
                ensure_ascii=False,
                sort_keys=True,
            ))
            return 0
        elif args.cmd == "filter-probe-request":
            request = json.loads(Path(args.request).read_text(encoding="utf-8"))
            available = json.loads(
                Path(args.available_nodeids).read_text(encoding="utf-8")
            )
            print(json.dumps(
                filter_probe_request(request, set(available)),
                ensure_ascii=False,
                sort_keys=True,
            ))
            return 0
        elif args.cmd == "classify-node-failures":
            classification = classify_node_failures(
                baseline=json.loads(Path(args.baseline).read_text(encoding="utf-8")),
                upstream_parent=json.loads(
                    Path(args.upstream_parent).read_text(encoding="utf-8")
                ),
                merged=json.loads(Path(args.merged).read_text(encoding="utf-8")),
                manifest=json.loads(Path(args.manifest).read_text(encoding="utf-8")),
            )
            print(json.dumps(classification, ensure_ascii=False, sort_keys=True))
            return 0
        elif args.cmd == "node-outcome":
            log = Path(args.log).read_text(encoding="utf-8")
            parsed = parse_test_outcomes(log)
            failed = parsed["failed_nodeids"]
            expected = None
            if args.expected_nodeids:
                expected = json.loads(
                    Path(args.expected_nodeids).read_text(encoding="utf-8")
                )
                if not isinstance(expected, list) or not all(
                    isinstance(item, str) for item in expected
                ):
                    raise ValueError("expected nodeids must be a JSON string list")
            collected = sorted(set(expected if expected is not None else parsed["collected_nodeids"]))
            unexpected = sorted(set(failed) - set(collected))
            print(json.dumps({
                "collect_ok": not unexpected and not parsed["collection_error_paths"],
                "probe_ok": not unexpected and not parsed["collection_error_paths"],
                "collected_nodeids": collected,
                "failed_nodeids": sorted(set(failed) & set(collected)),
                "error_count": parsed["error_count"],
                "collection_error_paths": parsed["collection_error_paths"],
            }, ensure_ascii=False, sort_keys=True))
            return 0
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
        elif args.cmd == "persist-gate-failures":
            classification = json.loads(
                Path(args.classification).read_text(encoding="utf-8")
            )
            legacy = [
                line.strip()
                for line in Path(args.legacy_failures)
                .read_text(encoding="utf-8")
                .splitlines()
                if line.strip()
            ]
            write_json_atomic(
                Path(args.output),
                build_gate_failures_payload(
                    classification=classification,
                    merge_sha=args.merge_sha,
                    before=args.before,
                    legacy_failures=legacy,
                ),
            )
            return 0
        else:
            print(fork_test_receipt(source=args.source, side=args.side, digest=args.digest))
            return 0
    except (ValueError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 2

    for item in items:
        print(item)
    return 1 if items else 0


if __name__ == "__main__":
    raise SystemExit(_main())
