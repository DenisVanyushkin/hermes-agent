import pytest

from hermes_cli.ops_catalog import (
    RISK_READ,
    OpsCatalogError,
    resolve_operation,
)


def test_git_status_resolves_to_an_exact_argv():
    op = resolve_operation("git_status", {})
    assert op.argv == ("git", "status", "--short", "--branch")
    assert op.risk == RISK_READ
    assert op.irreversible is None


def test_git_fetch_carries_the_validated_remote():
    op = resolve_operation("git_fetch", {"remote": "origin"})
    assert op.argv == ("git", "fetch", "--quiet", "origin")


def test_unknown_operation_is_refused():
    with pytest.raises(OpsCatalogError):
        resolve_operation("rm_rf", {})


def test_hostile_parameter_never_reaches_argv():
    # Каталог опирается на валидаторы: значение отклоняется до построения argv.
    with pytest.raises(OpsCatalogError):
        resolve_operation("git_log", {"branch": "--upload-pack=/bin/sh"})
