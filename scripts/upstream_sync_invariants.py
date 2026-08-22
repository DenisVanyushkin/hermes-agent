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
The checks state what changed and stop; they never restore anything.

Telling an accepted deletion from a dropped one is what the merge base is for.
Without it, every deliberate removal and every rename an upstream batch brings
became a finding — and since the only answer to a finding disarmed the gate for
the whole merge, the noise was training the operator to bypass it. The rule, in
full:

    silent  iff  the base is known and parses
                 AND both sides have the file
                 AND exactly one side still defines the name;
    report  otherwise — including a base that is absent or unparseable, and
            including a side that has no file at all, because neither says
            anything about intent.

Both preconditions are load-bearing. A side that lacks the file presents the
same empty name set as one that deleted everything in it, so read without the
presence check every base-era name looks one-sidedly removed and the file is
excused entirely — the exact defect this module exists to catch.

The comparison is by NAME. It does not establish that the surviving side left
the body alone, so "the fork reworked this function and upstream deleted it" is
suppressed too; narrowing that needs a body comparison the module does not do.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from typing import Mapping



@dataclass(frozen=True)
class Finding:
    path: str
    kind: str                 # "unparseable" | "lost_definition"
    message: str
    symbol: str | None = None
    line: int | None = None
    policy: str | None = None
    finding_id: str | None = None
    fingerprint: dict | None = None


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


def _definition_segments(src: str) -> dict[str, tuple[str, ...]]:
    """Return exact top-level source segments, retaining duplicate occurrences."""
    tree = ast.parse(src)
    states: dict[str, list[str]] = {}
    for node in tree.body:
        names: list[str] = []
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names = [node.name]
        elif isinstance(node, ast.Assign):
            names = [target.id for target in node.targets if isinstance(target, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names = [node.target.id]
        segment = ast.get_source_segment(src, node) or ""
        for name in names:
            states.setdefault(name, []).append(segment)
    return {name: tuple(segments) for name, segments in states.items()}


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
        if src is None:
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


def lost_definitions(*, ours, theirs, result, path: str, base=None,
                     policy: str | None = None) -> list:
    """Report definitions present on either parent but absent from the result.

    Both parents count: dropping upstream's new function is as much a loss as
    dropping ours.

    *ours* and *theirs* are ``None`` when that side has no such file, which is
    not the same as an empty one: a side that HAS the file and defines nothing
    in it removed those definitions on purpose, while a side without the file
    says nothing at all. *base* is the same file at the merge base, and is what
    separates an accepted deletion from a dropped one. Anything unknown —
    absent base, unparseable base, a side with no file — reports rather than
    suppresses. See the module docstring for the rule in full.
    """
    if not _is_python(path):
        return []
    sides = {}
    segments = {}
    for label, src in (("result", result), ("ours", ours), ("theirs", theirs)):
        try:
            sides[label] = _definitions(src or "")
            segments[label] = _definition_segments(src or "")
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
    if policy in {"keep-local", "take-upstream"}:
        base_names = set()
        if base is not None:
            try:
                base_names = _definitions(base)
            except SyntaxError:
                base_names = set()
        if policy == "keep-local":
            missing -= {n for n in missing if n in sides["theirs"]
                        and n not in sides["ours"] and n not in base_names}
        else:
            missing -= {n for n in missing if n in sides["ours"]
                        and n not in sides["theirs"] and n not in base_names}
    accepted = set()
    if ours is not None and theirs is not None:
        accepted = _accepted_deletions(base, ours=sides["ours"], theirs=sides["theirs"])
        missing -= accepted

    # Under merge-both, presence by name is not enough: a resolver can keep the
    # local definition while silently dropping an upstream body edit. Compare
    # exact top-level source segments and apply the file policy to the side that
    # was discarded. keep-local/take-upstream intentionally suppress only their
    # corresponding one-sided loss; merge-both reports it.
    contribution_loss = set()
    if policy == "merge-both":
        base_segments = {}
        if base is not None:
            try:
                base_segments = _definition_segments(base)
            except SyntaxError:
                base_segments = {}
        for name in set(segments["ours"]) | set(segments["theirs"]):
            if name in accepted:
                continue
            ours_state = segments["ours"].get(name, ())
            theirs_state = segments["theirs"].get(name, ())
            result_state = segments["result"].get(name, ())
            base_state = base_segments.get(name, ())
            if result_state == ours_state and theirs_state != base_state and theirs_state != ours_state:
                contribution_loss.add(name)
            elif result_state == theirs_state and ours_state != base_state and ours_state != theirs_state:
                contribution_loss.add(name)
            # A module-level set loses the second definition. Keep the raw
            # compromise for old callers, but make the blocking policy report
            # the missing occurrence explicitly instead of silently collapsing it.
            max_count = max(len(ours_state), len(theirs_state))
            if len(result_state) < max_count:
                for occurrence in range(len(result_state), max_count):
                    contribution_loss.add(f"{name}#{occurrence + 1}")
    missing |= contribution_loss
    return [
        Finding(
            path=path,
            kind="lost_definition",
            symbol=name,
            message=(
                f"{name!r} is defined on a parent of this merge but absent from the "
                f"resolution - confirm the removal was intended"
            ),
            policy=policy,
        )
        for name in sorted(missing)
    ]


def _accepted_deletions(base, *, ours: set, theirs: set) -> set:
    """Names the merge base had that exactly one side no longer defines.

    One side dropped the name and the other still carries it, so a resolution
    without it followed a deletion rather than losing something both parents
    agreed to keep. A name both sides still define, or one no side had to begin
    with, is not in here: dropping either of those is a resolver defect.

    Returns the empty set when the base is unknown or does not parse, so the
    fallback is noise rather than silence. The caller is responsible for the
    other precondition — not reaching here when a side has no file at all —
    because that side's empty name set is indistinguishable here from one that
    deleted everything.
    """
    if base is None:
        return set()
    try:
        base_names = _definitions(base)
    except SyntaxError:
        # The base is not a parent of this merge and its syntax is not this
        # merge's problem; it simply stops being usable evidence.
        return set()
    return {n for n in base_names if (n in ours) != (n in theirs)}


def check_merge(paths, read_ours, read_theirs, read_result, read_base=None,
                policy_by_path: Mapping[str, str] | None = None) -> Report:
    """Run every check over ``paths``; readers return file text for one side.

    A reader returns ``None`` for a side that has no such file. ``read_base`` is
    optional so a caller with no merge base (the parse-only sweep) keeps
    working; supplying it is what silences accepted deletions.
    """
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
                ours=read_ours(path), theirs=read_theirs(path), result=result, path=path,
                base=read_base(path) if read_base is not None else None,
                policy=(policy_by_path or {}).get(path),
            )
        )
    return report
