"""Invariant checks over a resolved merge, before it becomes a commit.

Both failures of 2026-08-19 produced a merge that looked fully resolved - no
conflict markers, every hunk closed, exit 0 - and was structurally broken. The
only thing that noticed was a 20-minute test gate, and its diagnosis named one
of seven lost symbols. These checks are the cheap deterministic pass that
should have run first.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "upstream_sync"
sys.path.insert(0, str(SCRIPTS))


def _sides(fixture: str):
    """Split a saved conflict fixture into (head, ours, theirs, tail)."""
    lines = (FIXTURES / fixture).read_text(encoding="utf-8").splitlines(keepends=True)
    start = next(i for i, l in enumerate(lines) if l.startswith("<<<<<<<"))
    mid = next(i for i, l in enumerate(lines) if l.startswith("======="))
    stop = next(i for i, l in enumerate(lines) if l.startswith(">>>>>>>"))
    return lines[:start], lines[start + 1:mid], lines[mid + 1:stop], lines[stop + 1:]


class TestParseFailures:
    def test_flags_the_mechanically_concatenated_dict(self):
        """merge-both on a block whose closing brace is shared context.

        ours closed "pipelines" with it, theirs closed "session" with it, and
        one closer cannot serve two bodies.
        """
        from upstream_sync_invariants import parse_failures

        head, ours, theirs, tail = _sides("config_defaults_shared_closer.conflict")
        merged = "".join(head + ours + theirs + tail)

        findings = parse_failures({"hermes_cli/config_defaults.py": merged})

        assert len(findings) == 1
        assert findings[0].path == "hermes_cli/config_defaults.py"
        assert findings[0].kind == "unparseable"

    def test_silent_on_a_correctly_resolved_file(self):
        from upstream_sync_invariants import parse_failures

        head, ours, theirs, tail = _sides("config_defaults_shared_closer.conflict")
        merged = "".join(head + ours + ["    },\n"] + theirs + tail)

        assert parse_failures({"hermes_cli/config_defaults.py": merged}) == []

    def test_ignores_non_python_paths(self):
        """A Markdown file full of braces is not a syntax error."""
        from upstream_sync_invariants import parse_failures

        assert parse_failures({"website/docs/security.md": "{ not python at all"}) == []


class TestLostDefinitions:
    def test_reports_every_definition_the_resolver_dropped(self):
        """The 211-vs-1 block: the model kept the signature, dropped the payload."""
        from upstream_sync_invariants import lost_definitions

        _, ours, theirs, _ = _sides("gateway_run_asymmetric.conflict")
        # Both sides end on a bare signature whose body is the shared context
        # following the block in the real file; supply it so each side parses.
        body = "    pass\n"
        result = "".join(theirs) + body   # what the model effectively returned

        findings = lost_definitions(
            ours="".join(ours) + body, theirs=result, result=result, path="gateway/run.py"
        )

        lost = {f.symbol for f in findings}
        # Nine, not the seven a `grep "^def _stale_guard"` finds: that misses
        # the `async def` and both module constants. Undercounting by hand is
        # the reason this check exists.
        assert lost == {
            "_STALE_GUARD_PROJECT_ROOT",
            "_STALE_GUARD_UNSET",
            "_stale_guard_hermes_home",
            "_stale_guard_load_config",
            "_stale_guard_request_restart",
            "_stale_guard_alert_config",
            "_stale_guard_send_alert",
            "_stale_guard_arm",
            "_stale_guard_tick",
        }
        assert all(f.kind == "lost_definition" for f in findings)

    def test_silent_when_the_resolution_keeps_both_sides(self):
        from upstream_sync_invariants import lost_definitions

        _, ours, theirs, _ = _sides("gateway_run_asymmetric.conflict")
        body = "    pass\n"
        kept = "".join(ours) + body       # ours already ends with the merged signature

        assert lost_definitions(ours=kept, theirs="".join(theirs) + body,
                                result=kept, path="gateway/run.py") == []

    def test_a_symbol_that_only_moved_is_not_lost(self):
        """Reordering a file must not read as deletion."""
        from upstream_sync_invariants import lost_definitions

        ours = "def a():\n    pass\n\n\ndef b():\n    pass\n"
        result = "def b():\n    pass\n\n\ndef a():\n    pass\n"

        assert lost_definitions(ours=ours, theirs=ours, result=result, path="x.py") == []

    def test_reports_a_deliberate_deletion_too(self):
        """The checker does not get to guess intent.

        `submit_pending` was deleted on purpose during this very merge. It still
        must be reported: only a human can tell a deliberate removal from a
        resolver dropping code, and the report is how they get asked.
        """
        from upstream_sync_invariants import lost_definitions

        theirs = "def submit_pending():\n    pass\n"
        ours = "# submit_pending deleted on purpose\n"

        findings = lost_definitions(ours=ours, theirs=theirs, result=ours, path="tools/approval.py")

        assert [f.symbol for f in findings] == ["submit_pending"]


class TestBaseAwareSuppression:
    """With the merge base in hand, an accepted deletion stops looking like a loss.

    Without it the check can only ask "was this symbol on a parent?", which
    conflates two different events: a resolver dropping code (the thing the gate
    exists for) and one side deliberately deleting code the other side never
    removed (routine, and the majority of what a 164-commit upstream batch
    brings). Every finding of 2026-08-21 and 2026-08-22 was the second kind, and
    a gate whose findings are noise teaches its operator to bypass it wholesale.
    """

    FOO = "def foo():\n    pass\n"
    BAR = "def bar():\n    pass\n"

    def test_upstream_deleting_a_symbol_we_never_removed_is_not_a_loss(self):
        from upstream_sync_invariants import lost_definitions

        # 2026-08-22: upstream removed 27 duplicate _ensure_telegram_mock copies
        # in c1693d7dcc; the fork had only edited neighbouring lines.
        assert lost_definitions(
            base=self.FOO + self.BAR, ours=self.FOO + self.BAR,
            theirs=self.BAR, result=self.BAR, path="x.py",
        ) == []

    def test_our_deletion_of_a_symbol_upstream_kept_is_not_a_loss(self):
        from upstream_sync_invariants import lost_definitions

        # The mirror image: 2026-08-21, submit_pending deleted on the fork.
        assert lost_definitions(
            base=self.FOO + self.BAR, ours=self.BAR,
            theirs=self.FOO + self.BAR, result=self.BAR, path="x.py",
        ) == []

    def test_a_symbol_both_parents_still_define_is_lost_when_dropped(self):
        """Neither side deleted it, so the resolution had no licence to."""
        from upstream_sync_invariants import lost_definitions

        findings = lost_definitions(
            base=self.FOO + self.BAR, ours=self.FOO + self.BAR,
            theirs=self.FOO + self.BAR, result=self.BAR, path="x.py",
        )

        assert [f.symbol for f in findings] == ["foo"]
        assert findings[0].kind == "lost_definition"

    def test_a_symbol_added_by_one_side_and_then_dropped_is_lost(self):
        """Absent from the base means nobody deleted it — it was added and lost."""
        from upstream_sync_invariants import lost_definitions

        findings = lost_definitions(
            base=self.BAR, ours=self.FOO + self.BAR,
            theirs=self.BAR, result=self.BAR, path="x.py",
        )

        assert [f.symbol for f in findings] == ["foo"]

    def test_a_rename_on_one_side_is_not_reported_as_a_loss(self):
        """foo -> renamed upstream: the old name is gone from exactly one side."""
        from upstream_sync_invariants import lost_definitions

        renamed = "def foo_v2():\n    pass\n"

        assert lost_definitions(
            base=self.FOO, ours=self.FOO, theirs=renamed, result=renamed, path="x.py",
        ) == []

    def test_an_unparseable_base_suppresses_nothing(self):
        """An unreadable base is unknown intent, and unknown intent gets reported.

        Not an ``unreadable_parent`` finding either: the base is not a parent of
        this merge and its syntax is not this merge's problem. It just stops
        being evidence, so the check falls back to reporting.
        """
        from upstream_sync_invariants import lost_definitions

        findings = lost_definitions(
            base="def broken(:\n", ours=self.FOO + self.BAR,
            theirs=self.BAR, result=self.BAR, path="x.py",
        )

        assert [f.symbol for f in findings] == ["foo"]
        assert {f.kind for f in findings} == {"lost_definition"}

    def test_without_a_base_every_absence_is_still_reported(self):
        """The pre-base contract stays intact for callers that supply nothing."""
        from upstream_sync_invariants import lost_definitions

        findings = lost_definitions(
            ours=self.FOO + self.BAR, theirs=self.BAR, result=self.BAR, path="x.py",
        )

        assert [f.symbol for f in findings] == ["foo"]


class TestASideWithoutTheFile:
    """``None`` means the side has no file; ``""`` means it has an empty one.

    They are different events and only one of them says anything about intent.
    A side that does not have the file presents the same empty name set as a
    side that deleted every symbol in it — and read as the latter, every single
    base-era name satisfies "exactly one side removed it" and the whole file is
    excused, leaving the resolution checked against nothing. That is the routine
    shape of upstream retiring a module the fork still edits, resolved by
    keeping the file.
    """

    BASE = "def a():\n    pass\ndef b():\n    pass\ndef c():\n    pass\n"
    OURS = BASE + "def d():\n    pass\n"
    RESULT = "def a():\n    pass\n"

    def test_upstream_not_having_the_file_suppresses_nothing(self):
        from upstream_sync_invariants import lost_definitions

        findings = lost_definitions(
            base=self.BASE, ours=self.OURS, theirs=None, result=self.RESULT, path="m.py",
        )

        assert [f.symbol for f in findings] == ["b", "c", "d"]

    def test_us_not_having_the_file_suppresses_nothing(self):
        from upstream_sync_invariants import lost_definitions

        theirs = self.BASE + "def d():\n    pass\n"
        findings = lost_definitions(
            base=self.BASE, ours=None, theirs=theirs, result=self.RESULT, path="m.py",
        )

        assert [f.symbol for f in findings] == ["b", "c", "d"]

    def test_a_side_that_emptied_the_file_still_counts_as_deleting(self):
        """Having the file and defining nothing in it IS a deletion of everything.

        This is the half the presence check must keep: the side is there, it
        removed the definitions on purpose, and a resolution that follows it has
        lost nothing. Only "no file at all" is unknowable from the name sets.
        """
        from upstream_sync_invariants import lost_definitions

        findings = lost_definitions(
            base=self.BASE, ours=self.OURS, theirs="", result=self.RESULT, path="m.py",
        )

        assert [f.symbol for f in findings] == ["d"]


class TestCheckMerge:
    def _readers(self, ours: dict, theirs: dict, result: dict):
        return (lambda p: ours.get(p, ""), lambda p: theirs.get(p, ""), lambda p: result.get(p, ""))

    def test_clean_merge_reports_nothing(self):
        from upstream_sync_invariants import check_merge

        src = "def a():\n    pass\n"
        ro, rt, rr = self._readers({"x.py": src}, {"x.py": src}, {"x.py": src})
        report = check_merge(["x.py"], ro, rt, rr)

        assert report.ok is True
        assert report.findings == []

    def test_collects_both_kinds_across_files(self):
        from upstream_sync_invariants import check_merge

        head, ours, theirs, tail = _sides("config_defaults_shared_closer.conflict")
        broken = "".join(head + ours + theirs + tail)
        good = "".join(head + ours + ["    },\n"] + theirs + tail)

        ro, rt, rr = self._readers(
            {"cfg.py": good, "y.py": "def kept():\n    pass\n"},
            {"cfg.py": good, "y.py": "def kept():\n    pass\n"},
            {"cfg.py": broken, "y.py": "pass\n"},
        )
        report = check_merge(["cfg.py", "y.py"], ro, rt, rr)

        assert report.ok is False
        assert {f.kind for f in report.findings} == {"unparseable", "lost_definition"}

    def test_an_unparseable_file_is_not_also_reported_as_losing_everything(self):
        """A file that does not parse cannot be diffed for definitions.

        Reporting all of its symbols as lost would bury the one finding that
        matters under noise.
        """
        from upstream_sync_invariants import check_merge

        head, ours, theirs, tail = _sides("config_defaults_shared_closer.conflict")
        broken = "".join(head + ours + theirs + tail)
        good = "".join(head + ours + ["    },\n"] + theirs + tail)

        ro, rt, rr = self._readers({"cfg.py": good}, {"cfg.py": good}, {"cfg.py": broken})
        report = check_merge(["cfg.py"], ro, rt, rr)

        assert [f.kind for f in report.findings] == ["unparseable"]


class TestUnparseableParent:
    """A parent that does not parse must never read as "nothing was lost".

    _definitions() returns an empty set for anything it cannot parse, so a
    silently unparseable side would make the whole check pass vacuously - the
    exact false-negative this module exists to prevent.
    """

    def test_reports_instead_of_silently_finding_nothing(self):
        from upstream_sync_invariants import lost_definitions

        findings = lost_definitions(
            ours="def a(:\n", theirs="def b():\n    pass\n",
            result="def b():\n    pass\n", path="x.py",
        )

        kinds = {f.kind for f in findings}
        assert "unreadable_parent" in kinds


def test_merge_both_reports_a_dropped_duplicate_definition_occurrence():
    from upstream_sync_invariants import lost_definitions

    ours = "def foo():\n    return 1\n\ndef foo():\n    return 2\n"
    theirs = ours
    result = "def foo():\n    return 1\n"
    findings = lost_definitions(ours=ours, theirs=theirs, result=result, path="x.py", policy="merge-both")
    assert any(f.symbol == "foo#2" for f in findings)


def test_merge_both_reports_one_finding_when_the_whole_name_is_absent():
    from upstream_sync_invariants import lost_definitions

    src = "def foo():\n    return 1\n"
    findings = lost_definitions(
        ours=src, theirs=src, result="", path="x.py", policy="merge-both",
    )
    assert [(f.kind, f.symbol) for f in findings] == [("lost_definition", "foo")]


def test_body_contribution_has_a_distinct_kind_and_message():
    from upstream_sync_invariants import lost_definitions

    base = "def guarded():\n    return 1\n"
    ours = "def guarded():\n    return 2\n"
    theirs = "def guarded():\n    return 3\n"
    findings = lost_definitions(
        ours=ours, theirs=theirs, result=ours, base=base,
        path="x.py", policy="merge-both",
    )
    finding = next(f for f in findings if f.symbol == "guarded")
    assert finding.kind == "discarded_contribution"
    assert "absent from the resolution" not in finding.message
    assert "upstream" in finding.message.lower()


def test_decorator_only_change_is_a_discarded_contribution():
    from upstream_sync_invariants import lost_definitions

    base = "def guarded():\n    pass\n"
    ours = "@local\n\ndef guarded():\n    pass\n"
    theirs = "@upstream\n\ndef guarded():\n    pass\n"
    findings = lost_definitions(
        ours=ours, theirs=theirs, result=ours, base=base,
        path="x.py", policy="merge-both",
    )
    assert any(
        f.kind == "discarded_contribution" and f.symbol == "guarded"
        for f in findings
    )
