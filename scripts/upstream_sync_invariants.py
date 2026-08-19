#!/usr/bin/env python3
"""Structural checks over a resolved merge, run before it becomes a commit.

A merge can be fully "resolved" - no conflict markers, every hunk closed - and
still be broken, because a conflict block does not coincide with a semantic
unit. On 2026-08-19 that happened twice in one merge:

  * mechanical merge-both concatenated two dict bodies that shared a single
    closing brace living outside the block, leaving one closer for two bodies;
  * the model was handed a block of 211 lines ours against 1 line theirs,
    resolved the one-line disagreement it could see, and dropped the other 209
    lines - seven definitions vanished while their call sites remained.

Neither is detectable by looking for markers. Both are detectable in seconds by
parsing the result and diffing its definitions against both parents.

Deliberately reports rather than repairs. During the incident audit three
symbols came up missing and two were *supposed* to be gone: one deleted on
purpose locally, one retired with an implementation upstream had rewritten.
Only a human can tell those from a resolver dropping code, so the checks state
what changed and stop.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Finding:
    path: str
    kind: str                 # "unparseable" | "lost_definition"
    message: str
    symbol: str | None = None
    line: int | None = None


@dataclass
class Report:
    findings: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.findings

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "findings": [
                {k: v for k, v in vars(f).items() if v is not None} for f in self.findings
            ],
        }


def _is_python(path: str) -> bool:
    return path.endswith(".py")


def _definitions(src: str) -> set:
    """Module-level def/class names and simple assignment targets.

    Module level only: a name nested inside a function is an implementation
    detail, and tracking those turns every refactor into a finding.

    Raises SyntaxError for unparseable input rather than returning an empty
    set: a silent empty set makes the comparison pass vacuously, which is the
    false negative this module exists to prevent.
    """
    tree = ast.parse(src)
    names = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return names


def parse_failures(files: dict) -> list:
    """Report every Python file in ``files`` that does not parse."""
    findings = []
    for path, src in sorted(files.items()):
        if not _is_python(path):
            continue
        try:
            ast.parse(src)
        except SyntaxError as exc:
            findings.append(
                Finding(
                    path=path,
                    kind="unparseable",
                    line=exc.lineno,
                    message=f"the resolved file does not parse: {exc.msg} (line {exc.lineno})",
                )
            )
    return findings


def lost_definitions(*, ours: str, theirs: str, result: str, path: str) -> list:
    """Report definitions present on either parent but absent from the result.

    Both parents count: dropping upstream's new function is as much a loss as
    dropping ours.
    """
    if not _is_python(path):
        return []
    sides = {}
    for label, src in (("result", result), ("ours", ours), ("theirs", theirs)):
        try:
            sides[label] = _definitions(src)
        except SyntaxError as exc:
            return [
                Finding(
                    path=path,
                    kind="unreadable_parent",
                    line=exc.lineno,
                    message=(
                        f"the {label} side of this merge does not parse ({exc.msg}, "
                        f"line {exc.lineno}) - the definition check cannot run and "
                        f"its silence must not be read as 'nothing was lost'"
                    ),
                )
            ]
    missing = (sides["ours"] | sides["theirs"]) - sides["result"]
    return [
        Finding(
            path=path,
            kind="lost_definition",
            symbol=name,
            message=(
                f"{name!r} is defined on a parent of this merge but absent from the "
                f"resolution - confirm the removal was intended"
            ),
        )
        for name in sorted(missing)
    ]


def check_merge(paths, read_ours, read_theirs, read_result) -> Report:
    """Run every check over ``paths``; readers return file text for one side."""
    report = Report()
    for path in paths:
        result = read_result(path)
        broken = parse_failures({path: result})
        if broken:
            # A file that does not parse has no definitions to diff; reporting
            # all of them as lost would bury the finding that matters.
            report.findings.extend(broken)
            continue
        report.findings.extend(
            lost_definitions(
                ours=read_ours(path), theirs=read_theirs(path), result=result, path=path
            )
        )
    return report
