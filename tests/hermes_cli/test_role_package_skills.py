"""Tests for package skill directory resolution (Slice 5).

Uses isolated HERMES_HOME via hermetic_home fixture.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from hermes_cli.role_packages import (
    RolePackageError,
    get_package_skill_dirs,
    get_package_for_skill_path,
    install_package,
    get_role_packages_dir,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_manifest(tmp_path: Path, name: str = "test-pkg") -> Path:
    data = {
        "schema_version": 1,
        "package": {"name": name, "version": "0.1.0"},
        "role": {
            "id": f"{name}-id",
            "canonical_id": f"{name}-id",
            "display_name": name,
        },
    }
    src = tmp_path / name
    src.mkdir(exist_ok=True)
    (src / "role-package.yaml").write_text(
        yaml.safe_dump(data, sort_keys=False), encoding="utf-8"
    )
    return src


@pytest.fixture()
def hermetic_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "hermes_home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    return home


# ---------------------------------------------------------------------------
# TestPackageSkillDirs
# ---------------------------------------------------------------------------

class TestPackageSkillDirs:
    """get_package_skill_dirs() returns skill sub-dirs of installed packages."""

    def test_no_packages_returns_empty(self, tmp_path: Path, hermetic_home: Path) -> None:
        assert get_package_skill_dirs(hermetic_home) == []

    def test_package_without_skills_dir_not_included(
        self, tmp_path: Path, hermetic_home: Path
    ) -> None:
        src = _make_manifest(tmp_path)
        install_package(src, hermetic_home)
        dirs = get_package_skill_dirs(hermetic_home)
        assert dirs == []

    def test_package_with_skills_dir_included(
        self, tmp_path: Path, hermetic_home: Path
    ) -> None:
        src = _make_manifest(tmp_path)
        (src / "skills").mkdir()
        install_package(src, hermetic_home)
        dirs = get_package_skill_dirs(hermetic_home)
        assert len(dirs) == 1
        assert dirs[0].name == "skills"

    def test_multiple_packages_with_skills_dirs(
        self, tmp_path: Path, hermetic_home: Path
    ) -> None:
        for i in range(3):
            src = _make_manifest(tmp_path, name=f"pkg-{i}")
            (src / "skills").mkdir()
            install_package(src, hermetic_home)
        dirs = get_package_skill_dirs(hermetic_home)
        assert len(dirs) == 3


# ---------------------------------------------------------------------------
# TestPackageForSkillPath
# ---------------------------------------------------------------------------

class TestPackageForSkillPath:
    """get_package_for_skill_path() returns the package owning a skill path."""

    def test_returns_none_for_builtin_skill(
        self, tmp_path: Path, hermetic_home: Path
    ) -> None:
        builtin_skill = tmp_path / "builtin_skill.py"
        builtin_skill.write_text("")
        result = get_package_for_skill_path(builtin_skill, hermetic_home)
        assert result is None

    def test_returns_package_name_for_owned_skill(
        self, tmp_path: Path, hermetic_home: Path
    ) -> None:
        src = _make_manifest(tmp_path)
        skills_dir = src / "skills"
        skills_dir.mkdir()
        (skills_dir / "my_skill.py").write_text("")
        install_package(src, hermetic_home)

        pkgs_dir = get_role_packages_dir(hermetic_home)
        skill_path = pkgs_dir / "test-pkg" / "skills" / "my_skill.py"
        result = get_package_for_skill_path(skill_path, hermetic_home)
        assert result == "test-pkg"

    def test_returns_none_for_path_outside_packages(
        self, tmp_path: Path, hermetic_home: Path
    ) -> None:
        outside = tmp_path / "somewhere_else" / "skill.py"
        outside.parent.mkdir(parents=True)
        outside.write_text("")
        result = get_package_for_skill_path(outside, hermetic_home)
        assert result is None


# ---------------------------------------------------------------------------
# TestPackageSkillsInAllDirs — integration with get_all_skills_dirs()
# ---------------------------------------------------------------------------

class TestPackageSkillsInAllDirs:
    """Package skill dirs are included when get_all_skills_dirs() is called."""

    def test_package_skills_dir_appears_in_all_dirs(
        self, tmp_path: Path, hermetic_home: Path
    ) -> None:
        from agent.skill_utils import get_all_skills_dirs

        src = _make_manifest(tmp_path)
        (src / "skills").mkdir()
        install_package(src, hermetic_home)

        all_dirs = get_all_skills_dirs()
        pkgs_dir = get_role_packages_dir(hermetic_home)
        expected = pkgs_dir / "test-pkg" / "skills"
        assert expected in all_dirs, f"Expected {expected} in {all_dirs}"


# ---------------------------------------------------------------------------
# TestPackageSkillReadOnly — skill_manager_tool guard
# ---------------------------------------------------------------------------

class TestPackageSkillReadOnly:
    """Package skills are protected from agent write actions."""

    def test_is_package_skill_path_true_for_owned_skill(
        self, tmp_path: Path, hermetic_home: Path
    ) -> None:
        from tools.skill_manager_tool import _is_package_skill_path

        src = _make_manifest(tmp_path)
        (src / "skills").mkdir()
        (src / "skills" / "mypkg_skill.py").write_text("")
        install_package(src, hermetic_home)

        pkgs_dir = get_role_packages_dir(hermetic_home)
        skill_path = pkgs_dir / "test-pkg" / "skills" / "mypkg_skill.py"
        assert _is_package_skill_path(skill_path) is True

    def test_is_package_skill_path_false_for_builtin(self, tmp_path: Path) -> None:
        from tools.skill_manager_tool import _is_package_skill_path

        builtin = tmp_path / "some_skill.py"
        builtin.write_text("")
        assert _is_package_skill_path(builtin) is False
