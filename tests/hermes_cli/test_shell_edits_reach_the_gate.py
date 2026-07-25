"""Edits made through the shell must reach the review gate.

detect_material_engineering_change only consults git when the turn contained one
of four named tools -- write_file, patch, edit_file, apply_patch -- and returns
False outright otherwise:

    if not has_file_mutation_tool_call:
        return False, material_paths

So an engineer that edits through execute_code or terminal (sed -i, a heredoc,
git apply, a script) produces no review requirement at all. Not a late review, a
missing one: with no verdict there is also no debt, so the Task 5 machinery never
engages either.

The guard did have a purpose -- never blame a turn for a repo it did not touch --
so the fix widens what counts as "could have written files" rather than removing
the check. Git stays the ground truth for *what* changed; the turn's tool calls
only decide *whether it is worth asking*.
"""
from dataclasses import replace

import pytest

from hermes_cli import review_gate
from hermes_cli.profile_execution import build_role_execution_plan
from hermes_cli.review_gate import detect_material_engineering_change


def _plan(category="repo_mutation"):
    return replace(build_role_execution_plan("поправь код"), operation_category=category)


def _call(name, args="{}"):
    return {"role": "assistant", "tool_calls": [
        {"function": {"name": name, "arguments": args}}
    ]}


@pytest.fixture
def git_says(monkeypatch):
    """Pin what `git diff HEAD --name-only` reports."""
    def _set(*paths):
        monkeypatch.setattr(
            review_gate, "_run_git_diff", lambda *_a, **_k: "\n".join(paths)
        )
    return _set


def test_a_shell_edit_requires_review(git_says):
    git_says("hermes_cli/thing.py")

    required, paths = detect_material_engineering_change(
        _plan(), [_call("execute_code", '{"code": "write the file"}')]
    )

    assert required is True
    assert paths == ["hermes_cli/thing.py"]


def test_a_terminal_edit_requires_review(git_says):
    git_says("config/hermes-model-policy.yaml")

    required, paths = detect_material_engineering_change(
        _plan(), [_call("terminal", '{"command": "sed -i s/a/b/ config/x.yaml"}')]
    )

    assert required is True
    assert paths == ["config/hermes-model-policy.yaml"]


def test_a_read_only_turn_never_asks_git(monkeypatch):
    """The protection the old guard provided must survive.

    Without it, any turn at all would be judged against a repo that other writers
    -- the resident agent, sandbox containers, the rebase cron -- also touch.
    """
    def _explode(*_a, **_k):
        raise AssertionError("git must not be consulted for a read-only turn")

    monkeypatch.setattr(review_gate, "_run_git_diff", _explode)

    required, paths = detect_material_engineering_change(
        _plan(), [_call("read_file", '{"path": "hermes_cli/thing.py"}')]
    )

    assert required is False
    assert paths == []


def test_tool_reported_and_shell_written_paths_are_both_reviewed(git_says):
    """A turn that writes one file each way must not have half of it reviewed."""
    git_says("hermes_cli/from_shell.py", "hermes_cli/from_tool.py")

    required, paths = detect_material_engineering_change(
        _plan(),
        [
            _call("write_file", '{"path": "hermes_cli/from_tool.py"}'),
            _call("execute_code", '{"code": "..."}'),
        ],
    )

    assert required is True
    assert set(paths) == {"hermes_cli/from_tool.py", "hermes_cli/from_shell.py"}


def test_baseline_dirt_is_still_not_blamed_on_the_turn(git_says):
    git_says("hermes_cli/mine.py", "hermes_cli/was_already_dirty.py")

    required, paths = detect_material_engineering_change(
        _plan(),
        [_call("execute_code", '{"code": "..."}')],
        baseline_dirty_paths=["hermes_cli/was_already_dirty.py"],
    )

    assert required is True
    assert paths == ["hermes_cli/mine.py"]


def test_non_material_paths_are_ignored(git_says):
    """Editing a note through the shell is not an engineering change."""
    git_says("docs/notes.md", "README.md")

    required, paths = detect_material_engineering_change(
        _plan(), [_call("execute_code", '{"code": "..."}')]
    )

    assert required is False
    assert paths == []


def test_read_only_investigation_is_still_exempt(git_says):
    git_says("hermes_cli/thing.py")

    required, _ = detect_material_engineering_change(
        _plan("read_only_investigation"), [_call("execute_code", '{"code": "..."}')]
    )

    assert required is False
