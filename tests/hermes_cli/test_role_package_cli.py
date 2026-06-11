"""Tests for the hermes role CLI lifecycle.

All tests use isolated HERMES_HOME via monkeypatch.setenv so they never
touch the real ~/.hermes/ directory.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from hermes_cli.role_packages import (
    PackageLoadStatus,
    RolePackageError,
    default_hermes_home,
    get_package_info,
    get_role_packages_dir,
    install_package,
    list_packages,
    read_lockfile,
    remove_package,
    validate_manifest_path,
)
from hermes_cli.profile_routing import (
    DEFAULT_MODEL_POLICY_PATH,
    DEFAULT_PROFILE_REGISTRY_PATH,
    route_task,
)

_REGISTRY_PATH = DEFAULT_PROFILE_REGISTRY_PATH
_POLICY_PATH = DEFAULT_MODEL_POLICY_PATH


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_manifest(
    role_id: str = "test_role",
    name: str = "test-role",
    version: str = "0.1.0",
    boundary_mode: str = "advisory",
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "package": {"name": name, "version": version, "description": "Test role"},
        "role": {
            "id": role_id,
            "canonical_id": role_id,
            "display_name": "Test Role",
            "role_family": "test",
            "purpose_summary": "A test role for CLI lifecycle tests",
        },
        "boundary_mode": boundary_mode,
    }


def _make_package_dir(tmp_path: Path, **kwargs) -> Path:
    pkg_dir = tmp_path / "source-pkg"
    pkg_dir.mkdir(parents=True, exist_ok=True)
    (pkg_dir / "role-package.yaml").write_text(
        yaml.safe_dump(_make_manifest(**kwargs), sort_keys=False), encoding="utf-8"
    )
    return pkg_dir


@pytest.fixture()
def hermetic_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Isolated HERMES_HOME for each test."""
    hermes_home = tmp_path / "hermes_home"
    hermes_home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    return hermes_home


# ---------------------------------------------------------------------------
# Install valid local package
# ---------------------------------------------------------------------------


def test_install_valid_package(tmp_path: Path, hermetic_home: Path) -> None:
    source = _make_package_dir(tmp_path)

    pkg = install_package(source, hermetic_home)

    assert pkg.status == PackageLoadStatus.OK
    assert pkg.name == "test-role"
    assert pkg.version == "0.1.0"
    assert pkg.role_id == "test_role"

    # Payload copied.
    dest = get_role_packages_dir(hermetic_home) / "test-role"
    assert dest.is_dir()
    assert (dest / "role-package.yaml").exists()


def test_install_creates_lockfile_entry(tmp_path: Path, hermetic_home: Path) -> None:
    source = _make_package_dir(tmp_path)
    install_package(source, hermetic_home)

    packages_dir = get_role_packages_dir(hermetic_home)
    lock = read_lockfile(packages_dir)

    assert "test-role" in lock["packages"]
    entry = lock["packages"]["test-role"]
    assert entry["role_id"] == "test_role"
    assert entry["status"] == "active"
    assert entry["source_type"] == "local"
    assert entry["version"] == "0.1.0"


# ---------------------------------------------------------------------------
# List shows installed package
# ---------------------------------------------------------------------------


def test_list_shows_installed_package(tmp_path: Path, hermetic_home: Path) -> None:
    source = _make_package_dir(tmp_path)
    install_package(source, hermetic_home)

    packages = list_packages(hermetic_home)

    assert len(packages) == 1
    p = packages[0]
    assert p["name"] == "test-role"
    assert p["status"] == "active"


def test_list_empty_when_nothing_installed(hermetic_home: Path) -> None:
    packages = list_packages(hermetic_home)
    assert packages == []


# ---------------------------------------------------------------------------
# Info shows package details
# ---------------------------------------------------------------------------


def test_info_shows_package_details(tmp_path: Path, hermetic_home: Path) -> None:
    source = _make_package_dir(tmp_path)
    install_package(source, hermetic_home)

    info = get_package_info("test-role", hermetic_home)

    assert info is not None
    assert info["name"] == "test-role"
    assert info["version"] == "0.1.0"
    assert info["role_id"] == "test_role"
    assert "install_path" in info


def test_info_returns_none_for_unknown(hermetic_home: Path) -> None:
    info = get_package_info("nonexistent", hermetic_home)
    assert info is None


# ---------------------------------------------------------------------------
# Validate installed package
# ---------------------------------------------------------------------------


def test_validate_installed_package_succeeds(tmp_path: Path, hermetic_home: Path) -> None:
    source = _make_package_dir(tmp_path)
    install_package(source, hermetic_home)

    dest = get_role_packages_dir(hermetic_home) / "test-role"
    manifest, errors, _ = validate_manifest_path(dest)

    assert errors == []
    assert manifest is not None


def test_validate_local_path_succeeds(tmp_path: Path) -> None:
    source = _make_package_dir(tmp_path)
    manifest, errors, _ = validate_manifest_path(source)
    assert errors == []
    assert manifest is not None


# ---------------------------------------------------------------------------
# Remove package
# ---------------------------------------------------------------------------


def test_remove_clears_payload_and_lockfile(tmp_path: Path, hermetic_home: Path) -> None:
    source = _make_package_dir(tmp_path)
    install_package(source, hermetic_home)

    remove_package("test-role", hermetic_home)

    packages_dir = get_role_packages_dir(hermetic_home)
    assert not (packages_dir / "test-role").exists()

    lock = read_lockfile(packages_dir)
    assert "test-role" not in lock.get("packages", {})


def test_remove_not_found_raises(hermetic_home: Path) -> None:
    with pytest.raises(RolePackageError, match="not installed"):
        remove_package("nonexistent", hermetic_home)


# ---------------------------------------------------------------------------
# Install failure cases
# ---------------------------------------------------------------------------


def test_install_invalid_manifest_fails_with_no_residue(tmp_path: Path, hermetic_home: Path) -> None:
    source = tmp_path / "bad-pkg"
    source.mkdir()
    (source / "role-package.yaml").write_text("not: valid: yaml:\n", encoding="utf-8")

    with pytest.raises(RolePackageError):
        install_package(source, hermetic_home)

    # No payload left behind.
    packages_dir = get_role_packages_dir(hermetic_home)
    assert not (packages_dir / "bad-pkg").exists()
    lock = read_lockfile(packages_dir)
    assert "bad-pkg" not in lock.get("packages", {})


def test_install_duplicate_builtin_id_fails(tmp_path: Path, hermetic_home: Path) -> None:
    source = _make_package_dir(tmp_path, role_id="engineer", name="bad-engineer")

    with pytest.raises(RolePackageError, match="collides"):
        install_package(source, hermetic_home)


def test_install_missing_source_dir_fails(tmp_path: Path, hermetic_home: Path) -> None:
    with pytest.raises(RolePackageError, match="not a directory"):
        install_package(tmp_path / "does-not-exist", hermetic_home)


def test_install_same_package_twice_fails(tmp_path: Path, hermetic_home: Path) -> None:
    source = _make_package_dir(tmp_path)
    install_package(source, hermetic_home)

    with pytest.raises(RolePackageError, match="already installed"):
        install_package(source, hermetic_home)


def test_install_force_overwrites(tmp_path: Path, hermetic_home: Path) -> None:
    source = _make_package_dir(tmp_path)
    install_package(source, hermetic_home)

    # Force reinstall with different version.
    updated_manifest = _make_manifest(version="0.2.0")
    (source / "role-package.yaml").write_text(
        yaml.safe_dump(updated_manifest, sort_keys=False), encoding="utf-8"
    )
    pkg = install_package(source, hermetic_home, force=True)

    assert pkg.version == "0.2.0"
    packages_dir = get_role_packages_dir(hermetic_home)
    lock = read_lockfile(packages_dir)
    assert lock["packages"]["test-role"]["version"] == "0.2.0"


# ---------------------------------------------------------------------------
# Parity: golden routing corpus and built-in probes after install/remove
# ---------------------------------------------------------------------------


def test_builtin_routing_unaffected_after_install(tmp_path: Path, hermetic_home: Path) -> None:
    """Installing a package must not change built-in routing decisions."""
    source = _make_package_dir(tmp_path)
    install_package(source, hermetic_home)

    probes = [
        ("deploy docker systemd rollback", "engineer"),
        ("auth secrets exposure cloudflare", "security_auditor"),
        ("vacancy CV cover letter recruiter", "career_strategist"),
    ]
    for prompt, expected in probes:
        decision = route_task(prompt, registry_path=_REGISTRY_PATH, policy_path=_POLICY_PATH)
        assert decision.primary_profile == expected, (
            f"routing changed after install: {prompt!r} → {decision.primary_profile!r} (expected {expected!r})"
        )


def test_builtin_routing_unaffected_after_remove(tmp_path: Path, hermetic_home: Path) -> None:
    """Removing a package must not change built-in routing decisions."""
    source = _make_package_dir(tmp_path)
    install_package(source, hermetic_home)
    remove_package("test-role", hermetic_home)

    decision = route_task(
        "deploy docker systemd rollback",
        registry_path=_REGISTRY_PATH,
        policy_path=_POLICY_PATH,
    )
    assert decision.primary_profile == "engineer"


def test_profile_validation_still_passes_after_install(tmp_path: Path, hermetic_home: Path) -> None:
    """Profile architecture validation must remain clean after package install."""
    from hermes_cli.profile_validation import validate_profile_architecture

    source = _make_package_dir(tmp_path)
    install_package(source, hermetic_home)

    issues = validate_profile_architecture(_REGISTRY_PATH, _POLICY_PATH)
    errors = [i for i in issues if i.severity == "error"]
    assert errors == [], f"validation errors after install: {errors}"


# ---------------------------------------------------------------------------
# Multiple packages
# ---------------------------------------------------------------------------


def test_install_multiple_packages(tmp_path: Path, hermetic_home: Path) -> None:
    for i in range(3):
        src = tmp_path / f"pkg-{i}"
        src.mkdir()
        data = _make_manifest(role_id=f"role_{i}", name=f"role-{i}")
        (src / "role-package.yaml").write_text(
            yaml.safe_dump(data, sort_keys=False), encoding="utf-8"
        )
        install_package(src, hermetic_home)

    packages = list_packages(hermetic_home)
    assert len(packages) == 3
    names = {p["name"] for p in packages}
    assert names == {"role-0", "role-1", "role-2"}


def test_remove_one_of_two_leaves_other(tmp_path: Path, hermetic_home: Path) -> None:
    for i in range(2):
        src = tmp_path / f"pkg-{i}"
        src.mkdir()
        data = _make_manifest(role_id=f"role_{i}", name=f"role-{i}")
        (src / "role-package.yaml").write_text(
            yaml.safe_dump(data, sort_keys=False), encoding="utf-8"
        )
        install_package(src, hermetic_home)

    remove_package("role-0", hermetic_home)

    packages = list_packages(hermetic_home)
    assert len(packages) == 1
    assert packages[0]["name"] == "role-1"


# ---------------------------------------------------------------------------
# Task 2b: accepted_env persistence tests
# ---------------------------------------------------------------------------

class TestAcceptEnvCLI:
    """Tests for --accept-env install flag and accepted_env lockfile persistence."""

    def _make_manifest(
        self,
        tmp_path: Path,
        name: str = "test-pkg",
        env_requires: list | None = None,
    ) -> Path:
        data: dict = {
            "schema_version": 1,
            "package": {"name": name, "version": "0.1.0"},
            "role": {
                "id": f"{name}-id",
                "canonical_id": f"{name}-id",
                "display_name": name,
            },
        }
        if env_requires is not None:
            data["env_requires"] = env_requires
        src = tmp_path / name
        src.mkdir(exist_ok=True)
        (src / "role-package.yaml").write_text(
            yaml.safe_dump(data, sort_keys=False), encoding="utf-8"
        )
        return src

    def test_install_without_accept_env_stores_empty_list(
        self, tmp_path: Path, hermetic_home: Path
    ) -> None:
        """accepted_env defaults to [] when install_package called without accept_env."""
        src = self._make_manifest(tmp_path, env_requires=[{"name": "FOO"}])
        install_package(src, hermetic_home)
        lock = read_lockfile(get_role_packages_dir(hermetic_home))
        entry = lock["packages"]["test-pkg"]
        assert entry.get("accepted_env", []) == []

    def test_install_with_accept_env_persists_names(
        self, tmp_path: Path, hermetic_home: Path
    ) -> None:
        """accepted_env list is written to lockfile when accept_env is supplied."""
        src = self._make_manifest(
            tmp_path,
            env_requires=[{"name": "FOO"}, {"name": "BAR"}],
        )
        install_package(src, hermetic_home, accept_env=["FOO", "BAR"])
        lock = read_lockfile(get_role_packages_dir(hermetic_home))
        entry = lock["packages"]["test-pkg"]
        assert sorted(entry.get("accepted_env", [])) == ["BAR", "FOO"]

    def test_accept_env_only_allows_declared_names(
        self, tmp_path: Path, hermetic_home: Path
    ) -> None:
        """Accepting an env var not declared in env_requires raises RolePackageError."""
        src = self._make_manifest(tmp_path, env_requires=[{"name": "FOO"}])
        with pytest.raises(RolePackageError, match="not declared"):
            install_package(src, hermetic_home, accept_env=["UNDECLARED_VAR"])

    def test_reinstall_updates_accepted_env(
        self, tmp_path: Path, hermetic_home: Path
    ) -> None:
        """Re-installing with force=True updates accepted_env in the lockfile."""
        src = self._make_manifest(
            tmp_path,
            env_requires=[{"name": "FOO"}, {"name": "BAR"}],
        )
        install_package(src, hermetic_home, accept_env=["FOO"])
        install_package(src, hermetic_home, force=True, accept_env=["FOO", "BAR"])
        lock = read_lockfile(get_role_packages_dir(hermetic_home))
        entry = lock["packages"]["test-pkg"]
        assert sorted(entry.get("accepted_env", [])) == ["BAR", "FOO"]


# ---------------------------------------------------------------------------
# G1: --accept-env CLI flag tests (RED: fail until flag is added to parser)
# ---------------------------------------------------------------------------


class TestAcceptEnvCLIFlag:
    """Tests for --accept-env CLI flag on hermes role install.

    These tests verify that the argparse-level flag is wired up and that
    accepted_env is correctly written to the lockfile.
    """

    def _make_env_pkg(
        self,
        tmp_path: Path,
        name: str = "env-test-pkg",
        env_requires: list | None = None,
    ) -> Path:
        env_requires = env_requires or [{"name": "FAKE_TOKEN"}, {"name": "ANOTHER_TOKEN"}]
        data = {
            "schema_version": 1,
            "package": {"name": name, "version": "0.1.0"},
            "role": {
                "id": f"{name.replace('-', '_')}_id",
                "canonical_id": f"{name.replace('-', '_')}_id",
                "display_name": name,
            },
            "env_requires": env_requires,
        }
        src = tmp_path / name
        src.mkdir(exist_ok=True)
        (src / "role-package.yaml").write_text(
            yaml.safe_dump(data, sort_keys=False), encoding="utf-8"
        )
        return src

    def _build_install_args(self, path: Path, accept_env: list[str] | None = None, force: bool = False):
        """Parse CLI args for 'hermes role install' using the real arg parser."""
        import argparse
        from hermes_cli.subcommands.role import build_role_parser

        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="command")
        from hermes_cli.subcommands.role import cmd_role
        build_role_parser(subparsers, cmd_role=cmd_role)

        argv = ["role", "install", str(path)]
        if accept_env:
            for val in accept_env:
                argv += ["--accept-env", val]
        if force:
            argv.append("--force")
        return parser.parse_args(argv)

    def test_accept_env_single_flag_written_to_lockfile(
        self, tmp_path: Path, hermetic_home: Path
    ) -> None:
        """--accept-env FAKE_TOKEN writes FAKE_TOKEN to lockfile accepted_env."""
        from hermes_cli.subcommands.role import cmd_role

        src = self._make_env_pkg(tmp_path)
        args = self._build_install_args(src, accept_env=["FAKE_TOKEN"])
        cmd_role(args)

        lock = read_lockfile(get_role_packages_dir(hermetic_home))
        entry = lock["packages"]["env-test-pkg"]
        assert "FAKE_TOKEN" in entry.get("accepted_env", [])

    def test_accept_env_repeated_flags_accepted(
        self, tmp_path: Path, hermetic_home: Path
    ) -> None:
        """--accept-env FOO --accept-env BAR writes both to lockfile."""
        from hermes_cli.subcommands.role import cmd_role

        src = self._make_env_pkg(tmp_path)
        args = self._build_install_args(src, accept_env=["FAKE_TOKEN", "ANOTHER_TOKEN"])
        cmd_role(args)

        lock = read_lockfile(get_role_packages_dir(hermetic_home))
        entry = lock["packages"]["env-test-pkg"]
        assert sorted(entry.get("accepted_env", [])) == ["ANOTHER_TOKEN", "FAKE_TOKEN"]

    def test_accept_env_comma_separated_values(
        self, tmp_path: Path, hermetic_home: Path
    ) -> None:
        """--accept-env FOO,BAR writes both tokens to lockfile."""
        from hermes_cli.subcommands.role import cmd_role

        src = self._make_env_pkg(tmp_path)
        # Simulate comma-separated in a single flag value
        args = self._build_install_args(src, accept_env=["FAKE_TOKEN,ANOTHER_TOKEN"])
        cmd_role(args)

        lock = read_lockfile(get_role_packages_dir(hermetic_home))
        entry = lock["packages"]["env-test-pkg"]
        assert sorted(entry.get("accepted_env", [])) == ["ANOTHER_TOKEN", "FAKE_TOKEN"]

    def test_accept_env_undeclared_var_exits_nonzero(
        self, tmp_path: Path, hermetic_home: Path
    ) -> None:
        """--accept-env of a var not in env_requires causes non-zero exit."""
        from hermes_cli.subcommands.role import cmd_role

        src = self._make_env_pkg(tmp_path)
        args = self._build_install_args(src, accept_env=["UNDECLARED_FAKE_VAR"])
        with pytest.raises(SystemExit) as exc_info:
            cmd_role(args)
        assert exc_info.value.code != 0

    def test_install_no_accept_env_stores_empty_list(
        self, tmp_path: Path, hermetic_home: Path
    ) -> None:
        """No --accept-env flag leaves accepted_env as empty list."""
        from hermes_cli.subcommands.role import cmd_role

        src = self._make_env_pkg(tmp_path)
        args = self._build_install_args(src)
        cmd_role(args)

        lock = read_lockfile(get_role_packages_dir(hermetic_home))
        entry = lock["packages"]["env-test-pkg"]
        assert entry.get("accepted_env", []) == []


# ---------------------------------------------------------------------------
# G2: install exit code tests (confirm correct non-zero exit on failure)
# ---------------------------------------------------------------------------


class TestInstallExitCodes:
    """Confirm that install CLI returns correct exit codes.

    These tests document and protect the existing correct behavior.
    """

    def _make_overlap_pkg(self, tmp_path: Path) -> Path:
        """Create a package with a trigger that collides with a built-in."""
        data = {
            "schema_version": 1,
            "package": {"name": "exit-code-overlap-pkg", "version": "0.1.0"},
            "role": {
                "id": "exit_code_overlap_role",
                "canonical_id": "exit_code_overlap_role",
                "display_name": "Exit Code Overlap Role",
                "routing": {"triggers": {"en": ["deploy"]}},
            },
            "boundary_mode": "observe_warn",
        }
        src = tmp_path / "overlap-pkg"
        src.mkdir()
        (src / "role-package.yaml").write_text(
            yaml.safe_dump(data, sort_keys=False), encoding="utf-8"
        )
        return src

    def _cmd_install_args(self, path: Path) -> object:
        import argparse
        from hermes_cli.subcommands.role import build_role_parser, cmd_role

        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="command")
        build_role_parser(subparsers, cmd_role=cmd_role)
        return parser.parse_args(["role", "install", str(path)])

    def test_overlap_package_install_exits_nonzero(
        self, tmp_path: Path, hermetic_home: Path
    ) -> None:
        """Install of overlap-trigger package exits non-zero."""
        from hermes_cli.subcommands.role import cmd_role

        src = self._make_overlap_pkg(tmp_path)
        args = self._cmd_install_args(src)
        with pytest.raises(SystemExit) as exc_info:
            cmd_role(args)
        assert exc_info.value.code != 0

    def test_invalid_manifest_install_exits_nonzero(
        self, tmp_path: Path, hermetic_home: Path
    ) -> None:
        """Install of package with invalid manifest exits non-zero."""
        from hermes_cli.subcommands.role import cmd_role

        src = tmp_path / "bad-pkg"
        src.mkdir()
        (src / "role-package.yaml").write_text("not: valid: yaml:\n", encoding="utf-8")

        args = self._cmd_install_args(src)
        with pytest.raises(SystemExit) as exc_info:
            cmd_role(args)
        assert exc_info.value.code != 0

    def test_valid_package_install_exits_zero(
        self, tmp_path: Path, hermetic_home: Path
    ) -> None:
        """Install of valid package exits zero (does not raise SystemExit)."""
        from hermes_cli.subcommands.role import cmd_role

        src = tmp_path / "good-pkg"
        src.mkdir()
        data = {
            "schema_version": 1,
            "package": {"name": "good-pkg", "version": "0.1.0"},
            "role": {"id": "good_pkg_role", "canonical_id": "good_pkg_role", "display_name": "Good"},
        }
        (src / "role-package.yaml").write_text(
            yaml.safe_dump(data, sort_keys=False), encoding="utf-8"
        )
        args = self._cmd_install_args(src)
        # Should NOT raise — no sys.exit(1)
        cmd_role(args)


# ---------------------------------------------------------------------------
# G4: validate --schema-only flag tests
# ---------------------------------------------------------------------------


class TestValidateSchemaOnlyFlag:
    """Tests for hermes role validate --schema-only flag."""

    def _make_overlap_pkg(self, tmp_path: Path) -> Path:
        """Create a package whose schema is valid but whose trigger overlaps a built-in."""
        data = {
            "schema_version": 1,
            "package": {"name": "schema-only-overlap-pkg", "version": "0.1.0"},
            "role": {
                "id": "schema_only_overlap_role",
                "canonical_id": "schema_only_overlap_role",
                "display_name": "Schema Only Overlap Role",
                "routing": {"triggers": {"en": ["deploy"]}},
            },
            "boundary_mode": "observe_warn",
        }
        src = tmp_path / "schema-only-overlap-pkg"
        src.mkdir()
        (src / "role-package.yaml").write_text(
            yaml.safe_dump(data, sort_keys=False), encoding="utf-8"
        )
        return src

    def _cmd_validate_args(self, path: Path, schema_only: bool = False) -> object:
        import argparse
        from hermes_cli.subcommands.role import build_role_parser, cmd_role

        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="command")
        build_role_parser(subparsers, cmd_role=cmd_role)
        argv = ["role", "validate", str(path)]
        if schema_only:
            argv.append("--schema-only")
        return parser.parse_args(argv)

    def test_validate_default_catches_overlap_and_exits_nonzero(
        self, tmp_path: Path, hermetic_home: Path
    ) -> None:
        """Default validate catches overlap trigger and exits non-zero."""
        from hermes_cli.subcommands.role import cmd_role

        src = self._make_overlap_pkg(tmp_path)
        args = self._cmd_validate_args(src, schema_only=False)
        with pytest.raises(SystemExit) as exc_info:
            cmd_role(args)
        assert exc_info.value.code != 0

    def test_validate_schema_only_skips_overlap_and_exits_zero(
        self, tmp_path: Path, hermetic_home: Path
    ) -> None:
        """--schema-only skips overlap validation; valid schema exits zero."""
        from hermes_cli.subcommands.role import cmd_role

        src = self._make_overlap_pkg(tmp_path)
        args = self._cmd_validate_args(src, schema_only=True)
        # Should NOT raise — overlap check skipped, schema is valid
        cmd_role(args)

    def test_validate_unique_trigger_exits_zero(
        self, tmp_path: Path, hermetic_home: Path
    ) -> None:
        """Validate of package with unique trigger exits zero by default."""
        from hermes_cli.subcommands.role import cmd_role

        src = tmp_path / "unique-pkg"
        src.mkdir()
        data = {
            "schema_version": 1,
            "package": {"name": "unique-pkg", "version": "0.1.0"},
            "role": {
                "id": "unique_pkg_role",
                "canonical_id": "unique_pkg_role",
                "display_name": "Unique Role",
                "routing": {"triggers": {"en": ["xyzzy_unique_nonexistent_trigger_42"]}},
            },
        }
        (src / "role-package.yaml").write_text(
            yaml.safe_dump(data, sort_keys=False), encoding="utf-8"
        )
        args = self._cmd_validate_args(src)
        # Should NOT raise
        cmd_role(args)
