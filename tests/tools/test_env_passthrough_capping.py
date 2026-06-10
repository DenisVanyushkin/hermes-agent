"""Tests for cap_env_passthrough_for_skill() — the three-gate security invariant.

Security invariant:
  effective = skill_required_env ∩ manifest.env_requires ∩ accepted_consents
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from hermes_cli.role_packages import (
    cap_env_passthrough_for_skill,
    install_package,
    get_role_packages_dir,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_pkg(
    tmp_path: Path,
    hermetic_home: Path,
    name: str = "test-pkg",
    env_requires: list | None = None,
    accept_env: list[str] | None = None,
) -> Path:
    data = {
        "schema_version": 1,
        "package": {"name": name, "version": "0.1.0"},
        "role": {
            "id": f"{name}-id",
            "canonical_id": f"{name}-id",
            "display_name": name,
        },
    }
    if env_requires:
        data["env_requires"] = env_requires
    src = tmp_path / name
    src.mkdir(exist_ok=True)
    skills_dir = src / "skills"
    skills_dir.mkdir()
    (skills_dir / "skill.py").write_text("")
    (src / "role-package.yaml").write_text(
        yaml.safe_dump(data, sort_keys=False), encoding="utf-8"
    )
    install_package(src, hermetic_home, accept_env=accept_env or [])
    pkgs_dir = get_role_packages_dir(hermetic_home)
    return pkgs_dir / name / "skills" / "skill.py"


@pytest.fixture()
def hermetic_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "hermes_home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    return home


# ---------------------------------------------------------------------------
# TestEnvPassthroughCapping — matrix of three gates
# ---------------------------------------------------------------------------

class TestEnvPassthroughCapping:
    """Seven-case matrix for the three-gate intersection."""

    def test_all_three_gates_pass(self, tmp_path: Path, hermetic_home: Path) -> None:
        """skill_required ∩ manifest ∩ accepted → full allowed set."""
        skill_path = _make_pkg(
            tmp_path, hermetic_home,
            env_requires=[{"name": "FOO"}, {"name": "BAR"}],
            accept_env=["FOO", "BAR"],
        )
        result = cap_env_passthrough_for_skill(
            skill_path=skill_path,
            skill_env_names={"FOO", "BAR"},
            hermes_home=hermetic_home,
        )
        assert sorted(result) == ["BAR", "FOO"]

    def test_manifest_gate_blocks_undeclared(
        self, tmp_path: Path, hermetic_home: Path
    ) -> None:
        """skill_required has EXTRA not in manifest → EXTRA stripped."""
        skill_path = _make_pkg(
            tmp_path, hermetic_home,
            env_requires=[{"name": "FOO"}],
            accept_env=["FOO"],
        )
        result = cap_env_passthrough_for_skill(
            skill_path=skill_path,
            skill_env_names={"FOO", "EXTRA"},
            hermes_home=hermetic_home,
        )
        assert result == ["FOO"]

    def test_consent_gate_blocks_unapproved(
        self, tmp_path: Path, hermetic_home: Path
    ) -> None:
        """Declared in manifest but not in accepted_env → stripped."""
        skill_path = _make_pkg(
            tmp_path, hermetic_home,
            env_requires=[{"name": "FOO"}, {"name": "BAR"}],
            accept_env=["FOO"],  # BAR not accepted
        )
        result = cap_env_passthrough_for_skill(
            skill_path=skill_path,
            skill_env_names={"FOO", "BAR"},
            hermes_home=hermetic_home,
        )
        assert result == ["FOO"]

    def test_skill_gate_blocks_not_requested(
        self, tmp_path: Path, hermetic_home: Path
    ) -> None:
        """Declared + accepted but skill doesn't request it → stripped."""
        skill_path = _make_pkg(
            tmp_path, hermetic_home,
            env_requires=[{"name": "FOO"}, {"name": "BAR"}],
            accept_env=["FOO", "BAR"],
        )
        result = cap_env_passthrough_for_skill(
            skill_path=skill_path,
            skill_env_names={"FOO"},  # BAR not requested by skill
            hermes_home=hermetic_home,
        )
        assert result == ["FOO"]

    def test_non_package_skill_returns_none(
        self, tmp_path: Path, hermetic_home: Path
    ) -> None:
        """Skill path outside packages dir returns None (builtin skill)."""
        builtin = tmp_path / "builtin_skill.py"
        builtin.write_text("")
        result = cap_env_passthrough_for_skill(
            skill_path=builtin,
            skill_env_names={"FOO"},
            hermes_home=hermetic_home,
        )
        assert result is None

    def test_empty_skill_env_returns_empty_list(
        self, tmp_path: Path, hermetic_home: Path
    ) -> None:
        """Skill requests no env vars → empty list (not None)."""
        skill_path = _make_pkg(
            tmp_path, hermetic_home,
            env_requires=[{"name": "FOO"}],
            accept_env=["FOO"],
        )
        result = cap_env_passthrough_for_skill(
            skill_path=skill_path,
            skill_env_names=set(),
            hermes_home=hermetic_home,
        )
        assert result == []

    def test_no_env_requires_in_manifest_returns_empty(
        self, tmp_path: Path, hermetic_home: Path
    ) -> None:
        """Package with no env_requires → empty list even if skill requests vars."""
        skill_path = _make_pkg(
            tmp_path, hermetic_home,
            # no env_requires
        )
        result = cap_env_passthrough_for_skill(
            skill_path=skill_path,
            skill_env_names={"FOO", "BAR"},
            hermes_home=hermetic_home,
        )
        assert result == []


# ---------------------------------------------------------------------------
# TestSkillViewCapIntegration — cap_env_passthrough_for_skill round-trip
# ---------------------------------------------------------------------------

class TestSkillViewCapIntegration:
    """Verify the three-gate cap is correctly applied end-to-end."""

    def test_cap_returns_sorted_intersection(
        self, tmp_path: Path, hermetic_home: Path
    ) -> None:
        """cap_env_passthrough_for_skill returns sorted intersection."""
        skill_path = _make_pkg(
            tmp_path, hermetic_home,
            env_requires=[{"name": "A"}, {"name": "B"}, {"name": "C"}],
            accept_env=["A", "C"],
        )
        result = cap_env_passthrough_for_skill(
            skill_path=skill_path,
            skill_env_names={"A", "B", "C"},
            hermes_home=hermetic_home,
        )
        # B not accepted; result is sorted
        assert result == ["A", "C"]

    def test_cap_result_is_deterministic(
        self, tmp_path: Path, hermetic_home: Path
    ) -> None:
        """Multiple calls return identical sorted lists."""
        skill_path = _make_pkg(
            tmp_path, hermetic_home,
            env_requires=[{"name": "Z"}, {"name": "A"}],
            accept_env=["Z", "A"],
        )
        r1 = cap_env_passthrough_for_skill(
            skill_path=skill_path,
            skill_env_names={"Z", "A"},
            hermes_home=hermetic_home,
        )
        r2 = cap_env_passthrough_for_skill(
            skill_path=skill_path,
            skill_env_names={"Z", "A"},
            hermes_home=hermetic_home,
        )
        assert r1 == r2 == ["A", "Z"]
