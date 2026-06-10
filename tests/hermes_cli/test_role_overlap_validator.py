"""Tests for Slice 4: role package routing overlap and ambiguity validator."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from hermes_cli.role_overlap import (
    CODE_BROAD_TRIGGER,
    CODE_EXACT_DUPLICATE,
    CODE_ROLE_FAMILY_OVERLAP,
    CODE_ROUTING_FLIP,
    CODE_SUBSTRING_BUILTIN_IN_PKG,
    CODE_SUBSTRING_PKG_IN_BUILTIN,
    SEVERITY_ERROR,
    SEVERITY_WARNING,
    OverlapFinding,
    has_errors,
    validate_package_overlap,
)
from hermes_cli.role_packages import (
    PackageLoadStatus,
    RolePackageError,
    install_package,
    remove_package,
    validate_manifest_path,
)
from hermes_cli.profile_routing import (
    DEFAULT_MODEL_POLICY_PATH,
    DEFAULT_PROFILE_REGISTRY_PATH,
    route_task,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_FIXTURES = _REPO_ROOT / "tests" / "fixtures" / "role_packages"
_REGISTRY_PATH = DEFAULT_PROFILE_REGISTRY_PATH
_POLICY_PATH = DEFAULT_MODEL_POLICY_PATH


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_fixture(name: str) -> dict[str, Any]:
    p = _FIXTURES / name / "role-package.yaml"
    return yaml.safe_load(p.read_text(encoding="utf-8"))


def _make_manifest(
    role_id: str = "test_xyz_role",
    name: str = "test-xyz-role",
    triggers_en: list[str] | None = None,
    triggers_ru: list[str] | None = None,
    role_family: str = "test",
    overlap_notes: list | None = None,
) -> dict[str, Any]:
    triggers: dict[str, Any] = {}
    if triggers_en:
        triggers["en"] = triggers_en
    if triggers_ru:
        triggers["ru"] = triggers_ru
    routing: dict[str, Any] = {"triggers": triggers}
    if overlap_notes:
        routing["overlap_notes"] = overlap_notes
    return {
        "schema_version": 1,
        "package": {"name": name, "version": "0.1.0"},
        "role": {
            "id": role_id,
            "canonical_id": role_id,
            "display_name": "Test Role",
            "role_family": role_family,
            "routing": routing,
        },
        "boundary_mode": "advisory",
    }


@pytest.fixture()
def hermetic_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    hermes_home = tmp_path / "hermes_home"
    hermes_home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    return hermes_home


# ---------------------------------------------------------------------------
# Trigger overlap tests — exact duplicate
# ---------------------------------------------------------------------------


def test_exact_duplicate_trigger_is_error() -> None:
    """Trigger exactly matching a built-in term → ERROR EXACT_DUPLICATE."""
    manifest = _make_manifest(triggers_en=["deploy"])
    findings = validate_package_overlap(manifest, "test-pkg")

    assert any(
        f.severity == SEVERITY_ERROR and f.code == CODE_EXACT_DUPLICATE
        for f in findings
    ), f"expected EXACT_DUPLICATE error, got: {findings}"


def test_exact_duplicate_security_trigger_is_error() -> None:
    """Trigger matching a security_auditor term → ERROR."""
    manifest = _make_manifest(triggers_en=["audit"])
    findings = validate_package_overlap(manifest, "test-pkg")

    assert any(f.code == CODE_EXACT_DUPLICATE and f.severity == SEVERITY_ERROR for f in findings)


def test_exact_duplicate_career_trigger_is_error() -> None:
    manifest = _make_manifest(triggers_en=["vacancy"])
    findings = validate_package_overlap(manifest, "test-pkg")
    assert any(f.code == CODE_EXACT_DUPLICATE and f.severity == SEVERITY_ERROR for f in findings)


# ---------------------------------------------------------------------------
# Trigger overlap tests — substring
# ---------------------------------------------------------------------------


def test_builtin_trigger_substring_of_pkg_trigger_is_error() -> None:
    """'deploy' is a substring of 'deploy containers' → SUBSTRING_BUILTIN_IN_PKG ERROR."""
    manifest = _make_manifest(triggers_en=["deploy containers"])
    findings = validate_package_overlap(manifest, "test-pkg")

    assert any(
        f.code == CODE_SUBSTRING_BUILTIN_IN_PKG and f.severity == SEVERITY_ERROR
        for f in findings
    ), f"expected SUBSTRING_BUILTIN_IN_PKG error; got: {findings}"


def test_pkg_trigger_substring_of_builtin_trigger_is_error() -> None:
    """'approval' is a substring of built-in 'approval gate' → SUBSTRING_PKG_IN_BUILTIN ERROR."""
    manifest = _make_manifest(triggers_en=["approval"])
    findings = validate_package_overlap(manifest, "test-pkg")

    assert any(
        f.code == CODE_SUBSTRING_PKG_IN_BUILTIN and f.severity == SEVERITY_ERROR
        for f in findings
    ), f"expected SUBSTRING_PKG_IN_BUILTIN error; got: {findings}"


# ---------------------------------------------------------------------------
# Trigger overlap tests — unique trigger (no error)
# ---------------------------------------------------------------------------


def test_unique_trigger_no_error() -> None:
    """A trigger with no overlap with built-in tables → no ERROR."""
    manifest = _make_manifest(triggers_en=["incident xyz unique xjqzw phrase"])
    findings = validate_package_overlap(manifest, "test-pkg")

    errors = [f for f in findings if f.severity == SEVERITY_ERROR]
    assert errors == [], f"unexpected errors for unique trigger: {errors}"


def test_unique_trigger_fixture_passes() -> None:
    manifest = _load_fixture("overlap_unique_trigger")
    findings = validate_package_overlap(manifest, "unique-trigger-role")
    assert not has_errors(findings), f"unique trigger fixture has errors: {findings}"


# ---------------------------------------------------------------------------
# Trigger overlap tests — broad generic trigger
# ---------------------------------------------------------------------------


def test_broad_trigger_produces_warning() -> None:
    """Very short trigger ('ping') → WARNING BROAD_TRIGGER."""
    manifest = _make_manifest(triggers_en=["ping"])
    findings = validate_package_overlap(manifest, "test-pkg")

    assert any(
        f.code == CODE_BROAD_TRIGGER and f.severity == SEVERITY_WARNING
        for f in findings
    ), f"expected BROAD_TRIGGER warning; got: {findings}"


def test_broad_trigger_does_not_produce_error() -> None:
    """Broad trigger alone is WARNING not ERROR."""
    manifest = _make_manifest(triggers_en=["ping"])
    findings = validate_package_overlap(manifest, "test-pkg")
    errors = [f for f in findings if f.severity == SEVERITY_ERROR and f.code != CODE_ROUTING_FLIP]
    assert errors == [], f"unexpected errors for broad trigger: {errors}"


# ---------------------------------------------------------------------------
# Routing flip tests
# ---------------------------------------------------------------------------


def test_routing_flip_builtin_corpus_is_error() -> None:
    """Trigger 'container' appears in corpus prompt routing to engineer → ROUTING_FLIP ERROR."""
    # 'container' is NOT in any built-in term table but appears in corpus:
    # "Check the docker container logs for errors" → engineer
    manifest = _make_manifest(triggers_en=["container"])
    findings = validate_package_overlap(manifest, "test-pkg")

    flip_errors = [f for f in findings if f.code == CODE_ROUTING_FLIP and f.severity == SEVERITY_ERROR]
    assert flip_errors, f"expected ROUTING_FLIP error for 'container'; got: {findings}"
    assert any(f.conflicting_role == "engineer" for f in flip_errors)


def test_unique_trigger_no_routing_flip() -> None:
    """A trigger absent from all corpus prompts → no ROUTING_FLIP."""
    manifest = _make_manifest(triggers_en=["incident xyz unique xjqzw phrase"])
    findings = validate_package_overlap(manifest, "test-pkg")

    flip = [f for f in findings if f.code == CODE_ROUTING_FLIP]
    assert flip == [], f"unexpected ROUTING_FLIP findings: {flip}"


def test_routing_flip_fixture() -> None:
    manifest = _load_fixture("routing_flip_builtin")
    findings = validate_package_overlap(manifest, "routing-flip-role")
    assert has_errors(findings)
    assert any(f.code == CODE_ROUTING_FLIP for f in findings)


# ---------------------------------------------------------------------------
# Overlap acknowledgement
# ---------------------------------------------------------------------------


def test_acknowledged_overlap_is_warning_not_error() -> None:
    """overlap_notes acknowledgement downgrades EXACT_DUPLICATE from ERROR to WARNING."""
    manifest = _make_manifest(
        triggers_en=["deploy"],
        overlap_notes=[{
            "conflicts_with": "engineer",
            "trigger": "deploy",
            "rationale": "Extends deploy workflow for test env",
        }],
    )
    findings = validate_package_overlap(manifest, "test-pkg")

    # Should have no EXACT_DUPLICATE ERROR (downgraded to WARNING)
    exact_errors = [
        f for f in findings
        if f.code == CODE_EXACT_DUPLICATE and f.severity == SEVERITY_ERROR
    ]
    assert exact_errors == [], f"acknowledged overlap should not be ERROR: {exact_errors}"

    # Should still appear as WARNING
    exact_warnings = [
        f for f in findings
        if f.code == CODE_EXACT_DUPLICATE and f.severity == SEVERITY_WARNING
    ]
    assert exact_warnings, f"acknowledged overlap should produce WARNING: {findings}"


def test_routing_flip_cannot_be_acknowledged() -> None:
    """ROUTING_FLIP is always ERROR regardless of overlap_notes."""
    # 'container' flips corpus entry; acknowledge it — should still be ERROR
    manifest = _make_manifest(
        triggers_en=["container"],
        overlap_notes=[{"conflicts_with": "engineer", "trigger": "container", "rationale": "test"}],
    )
    findings = validate_package_overlap(manifest, "test-pkg")

    flip_errors = [f for f in findings if f.code == CODE_ROUTING_FLIP and f.severity == SEVERITY_ERROR]
    assert flip_errors, "ROUTING_FLIP must remain ERROR even when acknowledged"


def test_acknowledged_overlap_fixture() -> None:
    manifest = _load_fixture("overlap_acknowledged")
    findings = validate_package_overlap(manifest, "acknowledged-role")
    # deploy is acknowledged → no ERROR from table overlap (downgraded to WARNING)
    # But ROUTING_FLIP from corpus is still ERROR (deploy is in corpus prompts)
    table_errors = [
        f for f in findings
        if f.severity == SEVERITY_ERROR and f.code in (
            CODE_EXACT_DUPLICATE, CODE_SUBSTRING_BUILTIN_IN_PKG, CODE_SUBSTRING_PKG_IN_BUILTIN
        )
    ]
    assert table_errors == [], f"table overlap errors despite acknowledgement: {table_errors}"


# ---------------------------------------------------------------------------
# Role family overlap
# ---------------------------------------------------------------------------


def test_role_family_engineering_produces_warning() -> None:
    manifest = _make_manifest(role_family="engineering")
    findings = validate_package_overlap(manifest, "test-pkg", check_corpus=False)
    assert any(
        f.code == CODE_ROLE_FAMILY_OVERLAP and f.severity == SEVERITY_WARNING
        for f in findings
    )


def test_custom_role_family_no_warning() -> None:
    manifest = _make_manifest(role_family="custom_domain")
    findings = validate_package_overlap(manifest, "test-pkg", check_corpus=False)
    assert not any(f.code == CODE_ROLE_FAMILY_OVERLAP for f in findings)


# ---------------------------------------------------------------------------
# has_errors helper
# ---------------------------------------------------------------------------


def test_has_errors_true_when_error_present() -> None:
    findings = [OverlapFinding(
        severity=SEVERITY_ERROR, code=CODE_EXACT_DUPLICATE,
        message="test", package_name="p", trigger="t",
    )]
    assert has_errors(findings)


def test_has_errors_false_when_only_warnings() -> None:
    findings = [OverlapFinding(
        severity=SEVERITY_WARNING, code=CODE_BROAD_TRIGGER,
        message="test", package_name="p", trigger="t",
    )]
    assert not has_errors(findings)


# ---------------------------------------------------------------------------
# CLI integration: validate command
# ---------------------------------------------------------------------------


def test_validate_overlap_package_returns_errors(tmp_path: Path) -> None:
    """validate_manifest_path with check_overlap=True catches overlap errors."""
    fixture_dir = _FIXTURES / "overlap_exact_trigger"
    _, errors, _ = validate_manifest_path(fixture_dir, check_overlap=True)
    assert any("EXACT_DUPLICATE" in e or "SUBSTRING" in e for e in errors), \
        f"expected overlap error; got: {errors}"


def test_validate_unique_package_no_errors(tmp_path: Path) -> None:
    """validate_manifest_path with check_overlap=True passes for unique triggers."""
    fixture_dir = _FIXTURES / "overlap_unique_trigger"
    _, errors, _ = validate_manifest_path(fixture_dir, check_overlap=True)
    assert errors == [], f"unexpected errors: {errors}"


# ---------------------------------------------------------------------------
# CLI integration: install command
# ---------------------------------------------------------------------------


def test_install_overlap_package_fails_and_leaves_no_residue(
    tmp_path: Path, hermetic_home: Path
) -> None:
    """install_package() rejects a package with overlap errors; no residue left."""
    from hermes_cli.role_packages import get_role_packages_dir

    fixture_dir = _FIXTURES / "overlap_exact_trigger"
    with pytest.raises(RolePackageError) as exc_info:
        install_package(fixture_dir, hermetic_home)

    assert "EXACT_DUPLICATE" in str(exc_info.value) or "ROUTING_FLIP" in str(exc_info.value) or \
           "validation failed" in str(exc_info.value).lower()

    # No payload copied.
    packages_dir = get_role_packages_dir(hermetic_home)
    assert not (packages_dir / "exact-trigger-role").exists()


def test_install_unique_package_succeeds(tmp_path: Path, hermetic_home: Path) -> None:
    """install_package() succeeds for a package with unique triggers."""
    fixture_dir = _FIXTURES / "overlap_unique_trigger"
    pkg = install_package(fixture_dir, hermetic_home)
    assert pkg.status == PackageLoadStatus.OK
    assert pkg.name == "unique-trigger-role"


def test_install_routing_flip_package_fails(tmp_path: Path, hermetic_home: Path) -> None:
    """install_package() rejects a package that flips golden corpus routing."""
    fixture_dir = _FIXTURES / "routing_flip_builtin"
    with pytest.raises(RolePackageError):
        install_package(fixture_dir, hermetic_home)


# ---------------------------------------------------------------------------
# Parity: built-in routing unchanged after overlap validator introduced
# ---------------------------------------------------------------------------


def test_golden_routing_unaffected_by_overlap_validator() -> None:
    """route_task() built-in results must not change after adding overlap validator."""
    probes = [
        ("deploy docker systemd rollback", "engineer"),
        ("auth secrets exposure cloudflare firewall", "security_auditor"),
        ("vacancy CV cover letter recruiter", "career_strategist"),
        ("capture durable memory of today work", "scribe"),
        ("weather news company research digest", "researcher"),
    ]
    for prompt, expected in probes:
        decision = route_task(prompt, registry_path=_REGISTRY_PATH, policy_path=_POLICY_PATH)
        assert decision.primary_profile == expected, (
            f"routing changed: {prompt!r} → {decision.primary_profile!r} (expected {expected!r})"
        )


def test_valid_ru_en_triggers_fixture_has_no_errors() -> None:
    """A package with unique RU and EN triggers passes overlap validation."""
    manifest = _load_fixture("valid_ru_en_triggers")
    findings = validate_package_overlap(manifest, "ru-en-triggers-role")
    errors = [f for f in findings if f.severity == SEVERITY_ERROR]
    assert errors == [], f"unexpected errors: {errors}"


# ---------------------------------------------------------------------------
# Deduplication: each (code, trigger, conflicting_role, conflicting_trigger) only once
# ---------------------------------------------------------------------------


def test_findings_deduplicated() -> None:
    """Calling validator twice on same manifest should not create duplicates."""
    manifest = _make_manifest(triggers_en=["deploy"])
    f1 = validate_package_overlap(manifest, "p1")
    f2 = validate_package_overlap(manifest, "p1")
    # Results should be identical (same deduplication logic each call).
    codes1 = {(f.code, f.trigger, f.conflicting_role) for f in f1}
    codes2 = {(f.code, f.trigger, f.conflicting_role) for f in f2}
    assert codes1 == codes2
