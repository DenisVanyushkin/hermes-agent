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

Telling those two apart is what the merge base is for. A symbol the base had
and exactly one side no longer defines was deleted rather than lost, and
reporting that is noise. The comparison is by name only, so it does not
establish that the surviving side left the body alone — see
``_accepted_deletions`` for what that leaves uncovered. Without the base every accepted
deletion and every rename in an upstream batch became a finding, which taught
the operator to answer the gate with the whole-merge bypass; on 2026-08-22 one
false positive took the structural check off five files nobody had inspected.
So: base known and exactly one side deleted -> silent; anything else -> report.
Base absent or unparseable -> report, because unknown intent is not consent.

What remains a judgement call is answered by name rather than wholesale:
``split_acked`` lets an operator confirm the findings they actually checked and
leaves the rest blocking.
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


# ---------------------------------------------------------------------------
# Acknowledgement
# ---------------------------------------------------------------------------

def parse_ack_spec(text: str) -> list:
    """Split an acknowledgement spec into ``path:symbol`` entries.

    Commas and whitespace both separate, because an operator pasting a list
    from a report should not have to think about which. Nothing here validates
    the shape: an entry that names no real finding simply matches nothing and
    comes back as unmatched, which is how a stale acknowledgement stays visible
    instead of quietly meaning something else.
    """
    return sorted({e for e in text.replace(",", " ").split() if e})


def split_acked(findings, entries) -> tuple:
    """Partition *findings* into (still blocking, acknowledged, unmatched entries).

    Only a finding that names a symbol can be acknowledged. An unparseable file
    is not a judgement call about intent — there is nothing for a human to
    confirm — so no entry can ever silence one.

    Acknowledging by name is what keeps one legitimate finding from costing the
    check on every other file, which is exactly what the whole-merge bypass
    does (2026-08-22: one false positive disarmed the gate for five files
    nobody had inspected).
    """
    wanted = set(entries)
    matched, kept = set(), []
    for finding in findings:
        key = f"{finding.path}:{finding.symbol}" if finding.symbol else None
        if key is not None and key in wanted:
            matched.add(key)
        else:
            kept.append(finding)
    return kept, sorted(matched), sorted(wanted - matched)


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


def lost_definitions(*, ours: str, theirs: str, result: str, path: str,
                     base: str | None = None) -> list:
    """Report definitions present on either parent but absent from the result.

    Both parents count: dropping upstream's new function is as much a loss as
    dropping ours.

    *base* is the merge base of the same file, and it is what separates a
    resolver dropping code from one side deliberately deleting it. Without it
    the check can only ask "was this on a parent?", which fires on every
    accepted deletion and every rename an upstream batch brings — and a gate
    that cries wolf teaches its operator to reach for the global bypass, which
    is worse than no gate at all. Omitted (or unparseable) means the intent is
    unknown, and unknown intent is reported, never assumed away.
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
    # A side with no text is a side without the file (the readers return "" for
    # a path absent at a revision), and "absent" is not "deleted every symbol in
    # it". Left to the name comparison below, every base name would satisfy
    # "exactly one side has it" and the whole file would be excused — a
    # modify/delete resolved by keeping the file, which is the routine shape of
    # upstream retiring a module the fork still edits, would be checked against
    # nothing at all.
    if ours.strip() and theirs.strip():
        missing -= _accepted_deletions(base, ours=sides["ours"], theirs=sides["theirs"])
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


def _accepted_deletions(base: str | None, *, ours: set, theirs: set) -> set:
    """Names the merge base had that exactly one side no longer defines.

    One side dropped the name and the other still carries it, so a resolution
    without it followed a deletion rather than losing something both parents
    agreed to keep. A name both sides still define, or one no side had to begin
    with, is not in here: dropping either of those is a resolver defect.

    This compares NAMES, and that is the limit of what it establishes. Whether
    the surviving side rewrote the body is never examined, so the suppressed set
    also covers "the fork reworked this function and upstream deleted it" — the
    fork's rework then goes with the deletion, unreported. Narrowing that needs
    a body comparison against the base, which this does not do.

    Returns the empty set when the base is unknown or does not parse, so the
    fallback is noise rather than silence. The caller is responsible for not
    reaching here when a side lacks the file altogether: this compares name
    sets, and an absent side presents the same empty set as one that deleted
    everything.
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


def check_merge(paths, read_ours, read_theirs, read_result, read_base=None) -> Report:
    """Run every check over ``paths``; readers return file text for one side.

    ``read_base`` is optional so a caller with no merge base (the parse-only
    sweep) keeps working; supplying it is what silences accepted deletions.
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
            )
        )
    return report
